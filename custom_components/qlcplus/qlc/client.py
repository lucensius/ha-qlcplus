"""Asynchronous client for the QLC+ Web Access WebSocket API.

See docs/qlcplus-protocol.md for the protocol this implements.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 9999
WS_PATH = "/qlcplusWS"

_VERSION_RE = re.compile(r'class="swInfo">\s*Q Light Controller Plus\s+([^<\s]+)')


class QLCError(Exception):
    """Base error."""


class QLCConnectionError(QLCError):
    """Not connected, connection lost or request timed out."""


class QLCAuthError(QLCConnectionError):
    """QLC+ rejected the credentials."""


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class FunctionEvent:
    function_id: int
    running: bool


@dataclass(slots=True, frozen=True)
class WidgetEvent:
    """Push or polled state of a VC widget.

    ``kind`` is the QLC+ keyword (BUTTON, SLIDER, CUE, ...) or STATUS for
    polled values. ``value`` is the first value field.
    """

    widget_id: int
    kind: str
    value: str
    extra: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class GrandMasterEvent:
    value: int


@dataclass(slots=True, frozen=True)
class ChannelValue:
    address: int  # 1-based absolute
    value: int
    group: str  # QLC+ group number as string, may be "" or "0.#RRGGBB"
    overridden: bool


@dataclass(slots=True, frozen=True)
class ChannelsEvent:
    values: tuple[ChannelValue, ...]


@dataclass(slots=True, frozen=True)
class RawEvent:
    text: str


Event = FunctionEvent | WidgetEvent | GrandMasterEvent | ChannelsEvent | RawEvent


def parse_push(text: str) -> Event:
    """Turn an unsolicited message into an event."""
    parts = text.split("|")
    if parts[0] == "FUNCTION" and len(parts) >= 3 and parts[1].isdigit():
        return FunctionEvent(int(parts[1]), parts[2] == "Running")
    if parts[0] == "GM_VALUE" and len(parts) >= 2 and parts[1].isdigit():
        return GrandMasterEvent(int(parts[1]))
    if len(parts) >= 3 and parts[0].isdigit():
        return WidgetEvent(int(parts[0]), parts[1], parts[2], tuple(parts[3:]))
    return RawEvent(text)


def parse_channel_values(universe: int, fields: list[str]) -> list[ChannelValue]:
    """Parse the fields of a getChannelsValues reply (4 fields per channel)."""
    out = []
    for i in range(0, len(fields) - 3, 4):
        try:
            addr = int(fields[i])
            value = int(fields[i + 1])
        except ValueError:
            continue
        out.append(ChannelValue(
            address=(universe - 1) * 512 + addr,
            value=value,
            group=fields[i + 2],
            overridden=fields[i + 3] == "1",
        ))
    return out


def channel_blocks(addresses: Iterable[int], max_gap: int = 8) -> list[tuple[int, int, int]]:
    """Group 1-based absolute addresses into (universe, start, count) blocks.

    Close addresses are merged, so a few requests cover all patched channels.
    """
    blocks: list[tuple[int, int, int]] = []
    for addr in sorted(set(addresses)):
        uni, local = (addr - 1) // 512 + 1, (addr - 1) % 512 + 1
        if blocks:
            b_uni, b_start, b_count = blocks[-1]
            if b_uni == uni and local - (b_start + b_count - 1) <= max_gap:
                blocks[-1] = (uni, b_start, local - b_start + 1)
                continue
        blocks.append((uni, local, 1))
    return blocks


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

EventCallback = Callable[[Event], None]
ConnectionCallback = Callable[[bool], None]


class QLCClient:
    """Persistent connection to one QLC+ instance."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        session: aiohttp.ClientSession | None = None,
        *,
        username: str | None = None,
        password: str | None = None,
        request_timeout: float = 5.0,
        idle_timeout: float = 20.0,
        backoff_max: float = 60.0,
    ) -> None:
        self.host = host
        self.port = port
        self._session = session
        self._own_session = session is None
        self._auth = aiohttp.BasicAuth(username, password or "") if username else None
        self._request_timeout = request_timeout
        self._idle_timeout = idle_timeout
        self._backoff_max = backoff_max

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._pending: dict[str, deque[asyncio.Future[list[str]]]] = {}
        self._send_lock = asyncio.Lock()
        self._listeners: list[EventCallback] = []
        self._conn_listeners: list[ConnectionCallback] = []
        self._runner: asyncio.Task | None = None
        self._connected_event = asyncio.Event()
        self._closing = False

        self._poll_interval = 0.0
        self._poll_blocks: list[tuple[int, int, int]] = []
        self._poll_widgets: list[int] = []
        self._poller: asyncio.Task | None = None
        self._poll_wakeup = asyncio.Event()

    # -- properties ---------------------------------------------------------

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}{WS_PATH}"

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    # -- subscriptions ------------------------------------------------------

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None

    def subscribe_connection(self, callback: ConnectionCallback) -> Callable[[], None]:
        self._conn_listeners.append(callback)
        return (lambda: self._conn_listeners.remove(callback)
                if callback in self._conn_listeners else None)

    def _emit(self, event: Event) -> None:
        for cb in list(self._listeners):
            try:
                cb(event)
            except Exception:  # noqa: BLE001 - never let a listener kill the reader
                _LOGGER.exception("Error in QLC+ event listener")

    def _emit_connection(self, state: bool) -> None:
        for cb in list(self._conn_listeners):
            try:
                cb(state)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error in QLC+ connection listener")

    # -- lifecycle ----------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _open(self) -> aiohttp.ClientWebSocketResponse:
        try:
            # autoping=False: we see the server's pings and use them as liveness
            return await self._get_session().ws_connect(
                self.url,
                autoping=False,
                heartbeat=None,
                auth=self._auth,
                timeout=aiohttp.ClientWSTimeout(ws_close=5.0),
                max_msg_size=16 * 1024 * 1024,
            )
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise QLCAuthError("Authentication failed") from err
            raise QLCConnectionError(f"WebSocket handshake failed: {err}") from err
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            raise QLCConnectionError(f"Cannot connect to {self.url}: {err}") from err

    async def connect(self) -> None:
        """Connect once and start the reader; raise QLCConnectionError on failure."""
        self._closing = False
        ws = await asyncio.wait_for(self._open(), self._request_timeout + 5)
        self._attach(ws)
        self._reader_task = asyncio.create_task(self._read_loop(ws))

    async def start(self) -> None:
        """Run forever in the background with reconnect and backoff."""
        self._closing = False
        if self._runner is None or self._runner.done():
            self._runner = asyncio.create_task(self._run())

    async def wait_connected(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout)
        except TimeoutError:
            return False
        return True

    async def close(self) -> None:
        self._closing = True
        for task in (self._runner, self._poller, getattr(self, "_reader_task", None)):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._runner = self._poller = None
        if self._ws is not None:
            await self._ws.close()
        self._detach("closed")
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def _run(self) -> None:
        delay = 1.0
        while not self._closing:
            try:
                ws = await self._open()
            except QLCConnectionError as err:
                _LOGGER.debug("Connect to %s failed: %s (retry in %.0f s)", self.url, err, delay)
            else:
                delay = 1.0
                self._attach(ws)
                await self._read_loop(ws)
            if self._closing:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._backoff_max)

    def _attach(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws
        self._connected_event.set()
        _LOGGER.debug("Connected to %s", self.url)
        self._emit_connection(True)

    def _detach(self, reason: str) -> None:
        was_connected = self._ws is not None
        self._ws = None
        self._connected_event.clear()
        for queue in self._pending.values():
            while queue:
                fut = queue.popleft()
                if not fut.done():
                    fut.set_exception(QLCConnectionError(f"Connection lost ({reason})"))
        self._pending.clear()
        if was_connected:
            self._emit_connection(False)

    async def _read_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        reason = "closed by server"
        try:
            while True:
                msg = await ws.receive(timeout=self._idle_timeout)
                if msg.type is aiohttp.WSMsgType.TEXT:
                    self._handle_text(msg.data)
                elif msg.type is aiohttp.WSMsgType.PING:
                    await ws.pong(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                                  aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except TimeoutError:
            reason = f"no data for {self._idle_timeout:.0f} s"
            _LOGGER.debug("QLC+ connection idle, reconnecting")
        except (aiohttp.ClientError, OSError) as err:
            reason = str(err)
        finally:
            if self._ws is ws:
                self._detach(reason)
            if not ws.closed:
                with contextlib.suppress(Exception):
                    await ws.close()

    def _handle_text(self, text: str) -> None:
        if text.startswith("QLC+API|"):
            parts = text.split("|")
            queue = self._pending.get(parts[1]) if len(parts) > 1 else None
            if queue:
                fut = queue.popleft()
                if not fut.done():
                    fut.set_result(parts[2:])
            else:
                _LOGGER.debug("Unexpected API reply: %s", text[:200])
            return
        self._emit(parse_push(text))

    # -- low level ----------------------------------------------------------

    async def send(self, text: str) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            raise QLCConnectionError("Not connected to QLC+")
        async with self._send_lock:
            try:
                await ws.send_str(text)
            except (aiohttp.ClientError, ConnectionResetError, RuntimeError) as err:
                raise QLCConnectionError(f"Send failed: {err}") from err

    async def request(self, command: str, *args: object, reply: str | None = None) -> list[str]:
        """Send QLC+API|command|args and wait for the reply fields."""
        if self._ws is None:
            raise QLCConnectionError("Not connected to QLC+")
        fut: asyncio.Future[list[str]] = asyncio.get_running_loop().create_future()
        queue = self._pending.setdefault(reply or command, deque())
        queue.append(fut)
        try:
            await self.send("|".join(["QLC+API", command, *map(str, args)]))
        except QLCConnectionError:
            with contextlib.suppress(ValueError):
                queue.remove(fut)
            raise
        try:
            return await asyncio.wait_for(asyncio.shield(fut), self._request_timeout)
        except TimeoutError:
            # Replies carry no IDs: a lost reply would shift all later ones.
            # Drop the connection so the queues start fresh.
            _LOGGER.warning("QLC+ did not answer %s, reconnecting", command)
            if self._ws is not None:
                await self._ws.close()
            raise QLCConnectionError(f"Timeout waiting for {command}") from None

    # -- API ----------------------------------------------------------------

    async def get_version(self) -> str | None:
        """Read the QLC+ version from the Simple Desk page (never from '/')."""
        try:
            async with self._get_session().get(
                f"http://{self.host}:{self.port}/simpleDesk",
                auth=self._auth,
                timeout=aiohttp.ClientTimeout(total=self._request_timeout),
            ) as resp:
                if resp.status == 401:
                    raise QLCAuthError("Authentication failed")
                body = await resp.text(errors="replace")
        except (aiohttp.ClientError, OSError, TimeoutError) as err:
            raise QLCConnectionError(f"HTTP request failed: {err}") from err
        m = _VERSION_RE.search(body)
        return m.group(1) if m else None

    async def get_functions(self) -> dict[int, str]:
        return _pairs(await self.request("getFunctionsList"))

    async def get_function_type(self, function_id: int) -> str:
        return (await self.request("getFunctionType", function_id) or [""])[0]

    async def get_function_status(self, function_id: int) -> bool | None:
        """True = running, False = stopped, None = no such function."""
        status = (await self.request("getFunctionStatus", function_id) or [""])[0]
        if status == "Running":
            return True
        if status == "Stopped":
            return False
        return None

    async def set_function(self, function_id: int, running: bool) -> None:
        await self.send(f"QLC+API|setFunctionStatus|{function_id}|{1 if running else 0}")

    async def get_widgets(self) -> dict[int, str]:
        return _pairs(await self.request("getWidgetsList"))

    async def get_widget_status(self, widget_id: int) -> list[str] | None:
        """Value fields of a widget status, or None if QLC+ doesn't know it."""
        fields = await self.request("getWidgetStatus", widget_id)
        if fields and fields[0] == str(widget_id) and len(fields) > 1:
            fields = fields[1:]  # 4.14+ prefixes the widget ID
        if not fields or fields == [""]:
            return None
        return fields

    async def set_widget(self, widget_id: int, value: int | str) -> None:
        await self.send(f"{widget_id}|{value}")

    async def set_channel(self, address: int, value: int) -> None:
        """Set a Simple Desk value (1-based absolute address)."""
        if not 1 <= address <= 512 * 256:
            raise ValueError(f"Invalid DMX address {address}")
        await self.send(f"CH|{address}|{max(0, min(255, int(value)))}")

    async def release_channel(self, address: int) -> None:
        """Remove the Simple Desk override from a channel."""
        await self.request("sdResetChannel", address, reply="getChannelsValues")

    async def release_universe(self, universe: int) -> None:
        """Remove all Simple Desk overrides of a universe (1-based)."""
        await self.request("sdResetUniverse", universe, reply="getChannelsValues")

    async def get_channels(self, universe: int, start: int, count: int = 1) -> list[ChannelValue]:
        """Read channel values; universe and start are 1-based."""
        fields = await self.request("getChannelsValues", universe, start, count)
        values = parse_channel_values(universe, fields)
        if values and values[0].address != (universe - 1) * 512 + start:
            raise QLCConnectionError("getChannelsValues reply does not match request")
        return values

    async def set_grand_master(self, value: int) -> None:
        await self.send(f"GM_VALUE|{max(0, min(255, int(value)))}")

    # -- polling ------------------------------------------------------------

    def configure_polling(
        self,
        interval: float,
        channel_addresses: Iterable[int] = (),
        widget_ids: Iterable[int] = (),
    ) -> None:
        """Poll channel values and widget states; results arrive as events."""
        self._poll_interval = interval
        self._poll_blocks = channel_blocks(channel_addresses)
        self._poll_widgets = sorted(set(widget_ids))
        if interval > 0 and (self._poll_blocks or self._poll_widgets):
            if self._poller is None or self._poller.done():
                self._poller = asyncio.create_task(self._poll_loop())
        elif self._poller is not None:
            self._poller.cancel()
            self._poller = None

    def request_poll(self) -> None:
        """Poll as soon as possible, e.g. right after a write."""
        self._poll_wakeup.set()

    async def poll_once(self) -> None:
        values: list[ChannelValue] = []
        for uni, start, count in self._poll_blocks:
            values.extend(await self.get_channels(uni, start, count))
        if values:
            self._emit(ChannelsEvent(tuple(values)))
        for wid in self._poll_widgets:
            fields = await self.get_widget_status(wid)
            if fields is not None:
                self._emit(WidgetEvent(wid, "STATUS", fields[0], tuple(fields[1:])))

    async def _poll_loop(self) -> None:
        while True:
            if not self.connected:
                await self._connected_event.wait()
            try:
                await self.poll_once()
            except QLCConnectionError as err:
                _LOGGER.debug("Polling failed: %s", err)
            self._poll_wakeup.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._poll_wakeup.wait(), self._poll_interval)


def _pairs(fields: list[str]) -> dict[int, str]:
    out: dict[int, str] = {}
    for i in range(0, len(fields) - 1, 2):
        with contextlib.suppress(ValueError):
            out[int(fields[i])] = fields[i + 1]
    return out
