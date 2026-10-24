"""Base entity for QLC+."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_DMX_ADDRESS, ATTR_LASER, ATTR_LASER_LOCKED, DOMAIN
from .coordinator import QLCCoordinator
from .qlc.model import PatchedChannel
from .qlc.planner import DeviceSpec


def hub_device_info(entry_id: str) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry_id)})


def fixture_device_info(entry_id: str, device: DeviceSpec) -> DeviceInfo:
    parent = f"{entry_id}_{device.via}" if device.via else entry_id
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{device.key}")},
        name=device.name,
        manufacturer=device.manufacturer,
        model=device.model,
        via_device=(DOMAIN, parent),
    )


class QLCEntity(CoordinatorEntity[QLCCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: QLCCoordinator, entry_id: str, key: str,
                 device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return self.coordinator.data.connected


class QLCChannelEntity(QLCEntity):
    """Entity bound to one fixture channel (with optional 16-bit fine partner)."""

    _laser = False

    def __init__(self, coordinator: QLCCoordinator, entry_id: str, key: str,
                 device_info: DeviceInfo, channel: PatchedChannel, laser: bool) -> None:
        super().__init__(coordinator, entry_id, key, device_info)
        self._channel = channel
        self._laser = laser

    @property
    def raw_value(self) -> int | None:
        """Channel value; 0..65535 for 16-bit pairs, else 0..255."""
        channels = self.coordinator.data.channels
        coarse = channels.get(self._channel.address)
        if coarse is None:
            return None
        if self._channel.fine is not None:
            return coarse * 256 + channels.get(self._channel.fine.address, 0)
        return coarse

    @property
    def raw_max(self) -> int:
        return 65535 if self._channel.fine is not None else 255

    async def async_write_raw(self, value: float) -> None:
        value = max(0, min(self.raw_max, int(round(value))))
        if self._channel.fine is not None:
            await self.coordinator.async_set_channels({
                self._channel.address: value >> 8,
                self._channel.fine.address: value & 0xFF,
            })
        else:
            await self.coordinator.async_set_channels({self._channel.address: value})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {ATTR_DMX_ADDRESS: self._channel.address}
        if self._laser:
            attrs[ATTR_LASER] = True
            attrs[ATTR_LASER_LOCKED] = not self.coordinator.laser_released
        return attrs
