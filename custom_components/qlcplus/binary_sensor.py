"""Connection status of the QLC+ instance."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import QLCConfigEntry
from .entity import QLCEntity, hub_device_info

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: QLCConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([QLCConnectionSensor(entry.runtime_data.coordinator, entry.entry_id)])


class QLCConnectionSensor(QLCEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connection"

    def __init__(self, coordinator, entry_id) -> None:
        super().__init__(coordinator, entry_id, "connection", hub_device_info(entry_id))

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"version": self.coordinator.data.version}
