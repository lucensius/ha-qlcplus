"""Selects for channels with discrete capabilities (colour, gobo, macros)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import QLCConfigEntry
from .const import GROUP_FIXTURES
from .entity import QLCChannelEntity, fixture_device_info
from .qlc.planner import SelectSpec

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: QLCConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    data = entry.runtime_data
    if GROUP_FIXTURES not in data.groups:
        return
    devices = {d.key: d for d in data.loaded.plan.devices}
    async_add_entities(
        QLCChannelSelect(data.coordinator, entry.entry_id, spec, devices[spec.device])
        for spec in data.loaded.plan.selects
    )


class QLCChannelSelect(QLCChannelEntity, SelectEntity):
    def __init__(self, coordinator, entry_id, spec: SelectSpec, device) -> None:
        super().__init__(coordinator, entry_id, spec.key,
                         fixture_device_info(entry_id, device), spec.channel, spec.laser)
        self._spec = spec
        self._attr_name = spec.name
        self._attr_options = [o.label for o in spec.options]
        self._attr_entity_registry_enabled_default = spec.enabled_default
        self._attr_icon = "mdi:laser-pointer" if spec.laser else None

    @property
    def current_option(self) -> str | None:
        value = self.coordinator.data.channels.get(self._channel.address)
        if value is None:
            return None
        option = self._spec.option_for(value)
        return option.label if option else None

    async def async_select_option(self, option: str) -> None:
        opt = next(o for o in self._spec.options if o.label == option)
        # lasers: use the lowest value of the range ("Laser off" 0-49 -> 0)
        await self.async_write_raw(opt.min if self._laser else opt.value)
