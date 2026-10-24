"""Push based coordinator holding the live state of one QLC+ instance."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .qlc.client import (
    ChannelsEvent,
    Event,
    FunctionEvent,
    QLCClient,
    QLCConnectionError,
    WidgetEvent,
)

if TYPE_CHECKING:
    from .qlc.pipeline import LoadedProject

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class QLCState:
    connected: bool = False
    version: str | None = None
    functions: dict[int, bool] = field(default_factory=dict)  # running
    channels: dict[int, int] = field(default_factory=dict)  # address -> value
    overridden: set[int] = field(default_factory=set)
    widgets: dict[int, str] = field(default_factory=dict)
    blackout: bool = False
    laser_released: bool = False


class QLCCoordinator(DataUpdateCoordinator[QLCState]):
    """Owns the client; all writes go through here so safety is enforced once."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: QLCClient,
        loaded: LoadedProject,
        *,
        poll_interval: float,
        poll_channels: bool,
        name: str,
    ) -> None:
        super().__init__(hass, _LOGGER, name=name, update_interval=None)
        self.client = client
        self.loaded = loaded
        self.poll_interval = poll_interval
        self.poll_channels = poll_channels
        self.data = QLCState()
        self._polled_widgets: set[int] = set()
        self._ha_overrides: set[int] = set()  # channels HA has set via Simple Desk
        self._ha_started: set[int] = set()  # functions HA has started
        self._unsubs: list = []
        self._sync_task: asyncio.Task | None = None
        self._gm_before_blackout = 255
        self._synced = asyncio.Event()

    # -- lifecycle ----------------------------------------------------------

    async def async_start(self) -> None:
        self._unsubs.append(self.client.subscribe(self._on_event))
        self._unsubs.append(self.client.subscribe_connection(self._on_connection))
        await self.client.start()

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._sync_task:
            self._sync_task.cancel()
        await self.client.close()
        await super().async_shutdown()

    async def async_wait_synced(self, timeout: float) -> bool:
        """Wait until function states were read after connecting."""
        try:
            await asyncio.wait_for(self._synced.wait(), timeout)
        except TimeoutError:
            return False
        return True

    @callback
    def _on_connection(self, connected: bool) -> None:
        if connected:
            self._sync_task = self.hass.async_create_background_task(
                self._initial_sync(), f"{DOMAIN} initial sync")
        else:
            _LOGGER.info("Connection to QLC+ at %s lost", self.client.host)
        self.data.connected = connected
        self.async_set_updated_data(self.data)

    async def _initial_sync(self) -> None:
        if not self.data.version:
            with contextlib.suppress(QLCConnectionError):
                self.data.version = await self.client.get_version()
        try:
            ids = [f.function_id for f in self.loaded.plan.functions]
            states = await asyncio.gather(*(self.client.get_function_status(i) for i in ids))
        except QLCConnectionError as err:
            _LOGGER.debug("Initial function sync failed: %s", err)
            return
        self.data.functions = {i: bool(s) for i, s in zip(ids, states, strict=True) if s is not None}
        self._apply_polling()
        self.client.request_poll()
        _LOGGER.info("Connected to QLC+ %s at %s", self.data.version or "", self.client.host)
        self._synced.set()
        self.async_set_updated_data(self.data)

    def _apply_polling(self) -> None:
        addresses = self.loaded.plan.channel_addresses() if self.poll_channels else set()
        widgets = set(self._polled_widgets)
        if self.loaded.plan.blackout_widget is not None:
            widgets.add(self.loaded.plan.blackout_widget)
        self.client.configure_polling(self.poll_interval, addresses, widgets)

    @callback
    def register_widget_poll(self, widget_id: int) -> None:
        self._polled_widgets.add(widget_id)
        if self.client.connected:
            self._apply_polling()

    @callback
    def unregister_widget_poll(self, widget_id: int) -> None:
        self._polled_widgets.discard(widget_id)
        if self.client.connected:
            self._apply_polling()

    # -- events -------------------------------------------------------------

    @callback
    def _on_event(self, event: Event) -> None:
        data = self.data
        match event:
            case FunctionEvent(function_id=fid, running=running):
                data.functions[fid] = running
                if not running:
                    self._ha_started.discard(fid)
            case ChannelsEvent(values=values):
                for v in values:
                    data.channels[v.address] = v.value
                    if v.overridden:
                        data.overridden.add(v.address)
                    else:
                        data.overridden.discard(v.address)
                        self._ha_overrides.discard(v.address)
            case WidgetEvent(widget_id=wid, kind=kind, value=value) if kind in (
                    "STATUS", "BUTTON", "SLIDER"):
                data.widgets[wid] = value
                if wid == self.loaded.plan.blackout_widget:
                    data.blackout = value == "255"
            case _:
                return
        self.async_set_updated_data(data)

    # -- safety -------------------------------------------------------------

    @property
    def laser_released(self) -> bool:
        return self.data.laser_released

    def _check_address(self, address: int, value: int) -> None:
        if not self.data.laser_released and self.loaded.safety.address_blocked(address, value):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="laser_locked_channel",
                translation_placeholders={"address": str(address)},
            )

    def _check_function(self, function_id: int) -> None:
        if not self.data.laser_released and function_id in self.loaded.safety.functions:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="laser_locked_function",
                translation_placeholders={"function": str(function_id)},
            )

    def _check_widget(self, widget_id: int, value: int) -> None:
        if (value > 0 and not self.data.laser_released
                and widget_id in self.loaded.safety.widgets):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="laser_locked_widget",
                translation_placeholders={"widget": str(widget_id)},
            )

    async def async_set_laser_release(self, released: bool) -> None:
        self.data.laser_released = released
        if not released:
            await self._revert_laser_actions()
        self.async_set_updated_data(self.data)

    async def _revert_laser_actions(self) -> None:
        """Undo what HA did to lasers while they were released."""
        safety = self.loaded.safety
        for address in sorted(self._ha_overrides & safety.laser_addresses):
            await self._guarded(self.client.release_channel(address))
            self._ha_overrides.discard(address)
        for fid in sorted(self._ha_started & safety.functions):
            if self.data.functions.get(fid):
                await self._guarded(self.client.set_function(fid, False))
        self.client.request_poll()

    # -- writes -------------------------------------------------------------

    async def _guarded(self, coro):
        try:
            return await coro
        except QLCConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_connected",
                translation_placeholders={"error": str(err)},
            ) from err

    async def async_set_channels(self, values: dict[int, int]) -> None:
        """Set several 1-based absolute addresses via Simple Desk."""
        for address, value in values.items():
            self._check_address(address, value)
        for address, value in values.items():
            await self._guarded(self.client.set_channel(address, value))
            self.data.channels[address] = value
            self.data.overridden.add(address)
            self._ha_overrides.add(address)
        self.async_set_updated_data(self.data)
        self.client.request_poll()

    async def async_release_channels(self, addresses: list[int]) -> None:
        for address in addresses:
            await self._guarded(self.client.release_channel(address))
            self._ha_overrides.discard(address)
            self.data.overridden.discard(address)
        self.client.request_poll()

    async def async_release_universes(self, universes: list[int]) -> None:
        """Remove all Simple Desk overrides of the given 1-based universes."""
        for universe in universes:
            await self._guarded(self.client.release_universe(universe))
        self._ha_overrides = {a for a in self._ha_overrides if (a - 1) // 512 + 1 not in universes}
        self.client.request_poll()

    async def async_set_function(self, function_id: int, running: bool) -> None:
        if running:
            self._check_function(function_id)
        await self._guarded(self.client.set_function(function_id, running))
        if running:
            self._ha_started.add(function_id)

    async def async_set_widget(self, widget_id: int, value: int) -> None:
        self._check_widget(widget_id, value)
        await self._guarded(self.client.set_widget(widget_id, value))
        self.data.widgets[widget_id] = str(value)
        self.async_set_updated_data(self.data)
        if widget_id in self._polled_widgets:
            self.client.request_poll()

    async def async_click_widget(self, widget_id: int, *, switching_on: bool) -> None:
        """Press and release a VC button (toggles its function)."""
        if switching_on:
            self._check_widget(widget_id, 255)
        await self._guarded(self.client.set_widget(widget_id, 255))
        await self._guarded(self.client.set_widget(widget_id, 0))
        self.client.request_poll()

    async def async_flash_widget(self, widget_id: int, duration: float) -> None:
        self._check_widget(widget_id, 255)
        await self._guarded(self.client.set_widget(widget_id, 255))
        try:
            await asyncio.sleep(duration)
        finally:
            await self._guarded(self.client.set_widget(widget_id, 0))

    async def async_set_blackout(self, on: bool) -> None:
        widget = self.loaded.plan.blackout_widget
        if widget is not None:
            if on != self.data.blackout:
                # the VC blackout button toggles on press
                await self._guarded(self.client.set_widget(widget, 255))
                await self._guarded(self.client.set_widget(widget, 0))
        else:
            await self._guarded(self.client.set_grand_master(0 if on else self._gm_before_blackout))
        self.data.blackout = on
        self.async_set_updated_data(self.data)
        self.client.request_poll()
