"""Numbers for continuous channels and VC sliders."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import DEGREE, PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import QLCConfigEntry
from .const import ATTR_WIDGET_ID, GROUP_FIXTURES, GROUP_WIDGETS
from .entity import QLCChannelEntity, QLCEntity, fixture_device_info, hub_device_info
from .qlc.planner import NumberSpec, WidgetSpec
from .qlc.presets import ChannelGroup

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: QLCConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    data = entry.runtime_data
    plan = data.loaded.plan
    devices = {d.key: d for d in plan.devices}
    entities: list[NumberEntity] = []
    if GROUP_FIXTURES in data.groups:
        entities += [QLCChannelNumber(data.coordinator, entry.entry_id, spec, devices[spec.device])
                     for spec in plan.numbers]
    if GROUP_WIDGETS in data.groups:
        entities += [QLCWidgetNumber(data.coordinator, entry.entry_id, spec)
                     for spec in plan.widgets if spec.kind == "number"]
    async_add_entities(entities)


class QLCChannelNumber(QLCChannelEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0

    def __init__(self, coordinator, entry_id, spec: NumberSpec, device) -> None:
        super().__init__(coordinator, entry_id, spec.key,
                         fixture_device_info(entry_id, device), spec.channel, spec.laser)
        self._spec = spec
        self._attr_name = spec.name
        self._attr_native_max_value = spec.max_value
        self._attr_entity_registry_enabled_default = spec.enabled_default
        if spec.unit == "°":
            self._attr_native_unit_of_measurement = DEGREE
        elif spec.unit == "%":
            self._attr_native_unit_of_measurement = PERCENTAGE
        if spec.unit is None:
            self._attr_native_step = 1
        else:
            self._attr_native_step = 0.1 if spec.channel.fine is not None else 1
        if spec.channel.group is ChannelGroup.MAINTENANCE:
            self._attr_entity_category = EntityCategory.CONFIG
        if spec.laser:
            self._attr_icon = "mdi:laser-pointer"

    @property
    def native_value(self) -> float | None:
        raw = self.raw_value
        if raw is None:
            return None
        if self._spec.unit is None:
            return raw
        return round(raw * self._spec.max_value / self.raw_max, 1)

    async def async_set_native_value(self, value: float) -> None:
        raw = value if self._spec.unit is None else value * self.raw_max / self._spec.max_value
        await self.async_write_raw(raw)


class QLCWidgetNumber(QLCEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1

    def __init__(self, coordinator, entry_id, spec: WidgetSpec) -> None:
        super().__init__(coordinator, entry_id, f"vc{spec.widget_id}", hub_device_info(entry_id))
        self._spec = spec
        self._attr_name = f"VC {spec.name}"
        self._attr_native_min_value = spec.low
        self._attr_native_max_value = spec.high
        self._attr_entity_registry_enabled_default = spec.enabled_default
        self._attr_extra_state_attributes = {ATTR_WIDGET_ID: spec.widget_id}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.register_widget_poll(self._spec.widget_id)

    async def async_will_remove_from_hass(self) -> None:
        self.coordinator.unregister_widget_poll(self._spec.widget_id)
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> float | None:
        value = self.coordinator.data.widgets.get(self._spec.widget_id)
        try:
            return float(value) if value is not None else None
        except ValueError:
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_widget(self._spec.widget_id, int(value))
