"""Switches: functions, VC toggle buttons, blackout and laser release."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import QLCConfigEntry
from .const import (
    ATTR_FUNCTION_ID,
    ATTR_LASER,
    ATTR_LASER_LOCKED,
    ATTR_WIDGET_ID,
    GROUP_FUNCTIONS,
    GROUP_WIDGETS,
)
from .entity import QLCEntity, hub_device_info
from .qlc.planner import FunctionSpec, WidgetSpec

PARALLEL_UPDATES = 0

FUNCTION_ICONS = {
    "Scene": "mdi:palette",
    "Chaser": "mdi:play-box-multiple",
    "Sequence": "mdi:play-box-multiple",
    "EFX": "mdi:vector-curve",
    "Collection": "mdi:folder-play",
    "RGBMatrix": "mdi:grid",
    "Show": "mdi:filmstrip",
    "Script": "mdi:script-text",
    "Audio": "mdi:music",
    "Video": "mdi:video",
}


async def async_setup_entry(hass: HomeAssistant, entry: QLCConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    data = entry.runtime_data
    plan = data.loaded.plan
    coord = data.coordinator
    entities: list[SwitchEntity] = [QLCBlackoutSwitch(coord, entry.entry_id)]
    if plan.has_laser:
        entities.append(QLCLaserReleaseSwitch(coord, entry.entry_id))
    if GROUP_FUNCTIONS in data.groups:
        entities += [QLCFunctionSwitch(coord, entry.entry_id, f)
                     for f in plan.functions if f.kind == "switch"]
    if GROUP_WIDGETS in data.groups:
        entities += [QLCWidgetSwitch(coord, entry.entry_id, w)
                     for w in plan.widgets if w.kind == "switch"]
    async_add_entities(entities)


class QLCFunctionSwitch(QLCEntity, SwitchEntity):
    def __init__(self, coordinator, entry_id, spec: FunctionSpec) -> None:
        super().__init__(coordinator, entry_id, f"fn{spec.function_id}", hub_device_info(entry_id))
        self._spec = spec
        self._attr_name = spec.name
        self._attr_icon = "mdi:laser-pointer" if spec.unsafe else FUNCTION_ICONS.get(spec.function_type)

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.functions.get(self._spec.function_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {ATTR_FUNCTION_ID: self._spec.function_id,
                                 "function_type": self._spec.function_type}
        if self._spec.unsafe:
            attrs[ATTR_LASER] = True
            attrs[ATTR_LASER_LOCKED] = not self.coordinator.laser_released
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_function(self._spec.function_id, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_function(self._spec.function_id, False)


class QLCWidgetSwitch(QLCEntity, SwitchEntity):
    """A VC toggle button; its state is polled while the entity is enabled."""

    def __init__(self, coordinator, entry_id, spec: WidgetSpec) -> None:
        super().__init__(coordinator, entry_id, f"vc{spec.widget_id}", hub_device_info(entry_id))
        self._spec = spec
        self._attr_name = f"VC {spec.name}"
        self._attr_entity_registry_enabled_default = spec.enabled_default
        self._attr_icon = "mdi:gesture-tap-button"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.register_widget_poll(self._spec.widget_id)

    async def async_will_remove_from_hass(self) -> None:
        self.coordinator.unregister_widget_poll(self._spec.widget_id)
        await super().async_will_remove_from_hass()

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.data.widgets.get(self._spec.widget_id)
        return None if value is None else value == "255"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {ATTR_WIDGET_ID: self._spec.widget_id}
        if self._spec.function_id is not None:
            attrs[ATTR_FUNCTION_ID] = self._spec.function_id
        if self._spec.unsafe:
            attrs[ATTR_LASER] = True
        return attrs

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self.is_on:
            await self.coordinator.async_click_widget(self._spec.widget_id, switching_on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.is_on is not False:
            await self.coordinator.async_click_widget(self._spec.widget_id, switching_on=False)


class QLCBlackoutSwitch(QLCEntity, SwitchEntity):
    _attr_translation_key = "blackout"

    def __init__(self, coordinator, entry_id) -> None:
        super().__init__(coordinator, entry_id, "blackout", hub_device_info(entry_id))

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.blackout

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        widget = self.coordinator.loaded.plan.blackout_widget
        return {"method": "vc_button" if widget is not None else "grand_master"}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_blackout(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_blackout(False)


class QLCLaserReleaseSwitch(QLCEntity, SwitchEntity):
    """While off (default, also after restarts), lasers stay at 0."""

    _attr_translation_key = "laser_release"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:laser-pointer"

    def __init__(self, coordinator, entry_id) -> None:
        super().__init__(coordinator, entry_id, "laser_release", hub_device_info(entry_id))

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.laser_released

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_laser_release(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_laser_release(False)
