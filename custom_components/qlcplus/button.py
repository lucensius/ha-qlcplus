"""Buttons: single-shot functions, VC flash and stop-all buttons."""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import QLCConfigEntry
from .const import (
    ATTR_FUNCTION_ID,
    ATTR_LASER,
    ATTR_WIDGET_ID,
    CONF_FLASH_DURATION,
    DEFAULT_FLASH_DURATION,
    GROUP_FUNCTIONS,
    GROUP_WIDGETS,
)
from .entity import QLCEntity, hub_device_info
from .qlc.planner import FunctionSpec, WidgetSpec

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: QLCConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    data = entry.runtime_data
    plan = data.loaded.plan
    flash = entry.options.get(CONF_FLASH_DURATION, DEFAULT_FLASH_DURATION)
    entities: list[ButtonEntity] = []
    if GROUP_FUNCTIONS in data.groups:
        entities += [QLCFunctionButton(data.coordinator, entry.entry_id, f)
                     for f in plan.functions if f.kind == "button"]
    if GROUP_WIDGETS in data.groups:
        entities += [QLCWidgetButton(data.coordinator, entry.entry_id, w, flash)
                     for w in plan.widgets if w.kind == "button"]
    async_add_entities(entities)


class QLCFunctionButton(QLCEntity, ButtonEntity):
    """Starts a single-shot chaser or sequence."""

    _attr_icon = "mdi:play-circle"

    def __init__(self, coordinator, entry_id, spec: FunctionSpec) -> None:
        super().__init__(coordinator, entry_id, f"fn{spec.function_id}", hub_device_info(entry_id))
        self._spec = spec
        self._attr_name = spec.name
        attrs: dict[str, Any] = {ATTR_FUNCTION_ID: spec.function_id,
                                 "function_type": spec.function_type}
        if spec.unsafe:
            attrs[ATTR_LASER] = True
            self._attr_icon = "mdi:laser-pointer"
        self._attr_extra_state_attributes = attrs

    async def async_press(self) -> None:
        await self.coordinator.async_set_function(self._spec.function_id, True)


class QLCWidgetButton(QLCEntity, ButtonEntity):
    """A VC flash button (held for the flash duration) or stop-all button."""

    def __init__(self, coordinator, entry_id, spec: WidgetSpec, flash_duration: float) -> None:
        super().__init__(coordinator, entry_id, f"vc{spec.widget_id}", hub_device_info(entry_id))
        self._spec = spec
        self._flash = flash_duration
        self._attr_name = f"VC {spec.name}"
        self._attr_entity_registry_enabled_default = spec.enabled_default
        self._attr_icon = "mdi:flash" if spec.action == "Flash" else "mdi:stop-circle"
        attrs: dict[str, Any] = {ATTR_WIDGET_ID: spec.widget_id, "action": spec.action}
        if spec.function_id is not None:
            attrs[ATTR_FUNCTION_ID] = spec.function_id
        if spec.unsafe:
            attrs[ATTR_LASER] = True
        self._attr_extra_state_attributes = attrs

    async def async_press(self) -> None:
        if self._spec.action == "Flash":
            await self.coordinator.async_flash_widget(self._spec.widget_id, self._flash)
        else:
            await self.coordinator.async_click_widget(self._spec.widget_id, switching_on=True)
