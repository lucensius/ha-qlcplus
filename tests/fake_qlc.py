"""A small fake QLC+ Web Access server for tests (behaves like 4.14)."""

from __future__ import annotations

import asyncio
import contextlib
import socket

from aiohttp import WSMsgType, web


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeQLC:
    def __init__(self, *, ping_interval: float = 5.0, version: str = "4.14.4") -> None:
        self.functions: dict[int, tuple[str, str]] = {}  # id -> (name, type)
        self.running: set[int] = set()
        self.widgets: dict[int, tuple[str, str]] = {}  # id -> (caption, status)
        self.function_values: dict[int, dict[int, int]] = {}  # fid -> {abs addr: value}
        self.universe = [0] * 1024
        self.overrides: dict[int, int] = {}  # 1-based abs addr -> value
        self.grand_master = 255
        self.received: list[str] = []
        self.silent: set[str] = set()  # API commands that get no reply
        self.ping_interval = ping_interval
        self.version = version
        self.sockets: list[web.WebSocketResponse] = []
        self.connections = 0
        self.port = _free_port()
        self.host = "127.0.0.1"
        self._runner: web.AppRunner | None = None

    # -- state helpers ------------------------------------------------------

    def value(self, address: int) -> int:
        if address in self.overrides:
            return self.overrides[address]
        for fid in self.running:
            if address in self.function_values.get(fid, {}):
                return self.function_values[fid][address]
        return self.universe[address - 1]

    async def push(self, text: str) -> None:
        for ws in list(self.sockets):
            if not ws.closed:
                await ws.send_str(text)

    async def set_running(self, fid: int, running: bool) -> None:
        changed = (fid in self.running) != running
        if running:
            self.running.add(fid)
        else:
            self.running.discard(fid)
            # LTP residue stays in the universe
            for addr, val in self.function_values.get(fid, {}).items():
                self.universe[addr - 1] = val
        if changed:
            await self.push(f"FUNCTION|{fid}|{'Running' if running else 'Stopped'}")

    async def drop_clients(self) -> None:
        for ws in list(self.sockets):
            await ws.close()

    # -- server -------------------------------------------------------------

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/qlcplusWS", self._ws_handler)
        app.router.add_get("/simpleDesk", self._simple_desk)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self.port).start()

    async def stop(self) -> None:
        await self.drop_clients()
        if self._runner:
            await self._runner.cleanup()

    async def _simple_desk(self, request: web.Request) -> web.Response:
        return web.Response(
            text=f'<div class="swInfo">Q Light Controller Plus {self.version}</div>',
            content_type="text/html")

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(autoping=True)
        await ws.prepare(request)
        self.sockets.append(ws)
        self.connections += 1
        pinger = asyncio.create_task(self._pinger(ws))
        try:
            async for msg in ws:
                if msg.type is WSMsgType.TEXT:
                    self.received.append(msg.data)
                    reply = await self._handle(msg.data)
                    if reply is not None:
                        await ws.send_str(reply)
        finally:
            pinger.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pinger
            self.sockets.remove(ws)
        return ws

    async def _pinger(self, ws: web.WebSocketResponse) -> None:
        if self.ping_interval <= 0:
            return
        while not ws.closed:
            await asyncio.sleep(self.ping_interval)
            with contextlib.suppress(Exception):
                await ws.ping()

    def _channels_message(self, universe: int, start: int, count: int) -> str:
        fields = []
        for i in range(start, start + count):
            addr = (universe - 1) * 512 + i
            fields += [str(i), str(self.value(addr)), "1", "1" if addr in self.overrides else "0"]
        return "|".join(fields)

    async def _handle(self, data: str) -> str | None:
        p = data.split("|")
        if p[0] == "QLC+API":
            cmd = p[1]
            if cmd in self.silent:
                return None
            head = f"QLC+API|{cmd}|"
            match cmd:
                case "getFunctionsList":
                    return head + "|".join(f"{i}|{n}" for i, (n, _) in self.functions.items())
                case "getFunctionType":
                    return head + self.functions.get(int(p[2]), ("", "Undefined"))[1]
                case "getFunctionStatus":
                    fid = int(p[2])
                    if fid not in self.functions:
                        return head + "Undefined"
                    return head + ("Running" if fid in self.running else "Stopped")
                case "setFunctionStatus":
                    if int(p[2]) in self.functions:
                        await self.set_running(int(p[2]), p[3] != "0")
                    return None
                case "getWidgetsList":
                    return head + "|".join(f"{i}|{c}" for i, (c, _) in self.widgets.items())
                case "getWidgetStatus":
                    wid = int(p[2])
                    if wid not in self.widgets:
                        return head
                    return head + f"{wid}|{self.widgets[wid][1]}"
                case "getChannelsValues":
                    count = int(p[4]) if len(p) > 4 else 1
                    return head + self._channels_message(int(p[2]), int(p[3]), count)
                case "sdResetChannel":
                    self.overrides.pop(int(p[2]), None)
                    return "QLC+API|getChannelsValues|" + self._channels_message(1, 1, 4)
                case "sdResetUniverse":
                    uni = int(p[2])
                    for a in [a for a in self.overrides if (a - 1) // 512 + 1 == uni]:
                        del self.overrides[a]
                    return "QLC+API|getChannelsValues|" + self._channels_message(1, 1, 4)
            return head
        if p[0] == "CH":
            self.overrides[int(p[1])] = int(p[2])
            return None
        if p[0] == "GM_VALUE":
            self.grand_master = int(p[1])
            return None
        if p[0].isdigit() and len(p) > 1 and int(p[0]) in self.widgets:
            caption, _ = self.widgets[int(p[0])]
            self.widgets[int(p[0])] = (caption, p[1])
        return None
