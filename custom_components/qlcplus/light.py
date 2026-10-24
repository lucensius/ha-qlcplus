"""Lights for heads and fixtures with an intensity channel."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import color as color_util

from . import QLCConfigEntry
from .const import ATTR_DMX_ADDRESS, GROUP_FIXTURES
from .entity import QLCEntity, fixture_device_info
from .qlc.planner import LightSpec, nearest_colour

PARALLEL_UPDATES = 0


async def async_setup_entry(hass: HomeAssistant, entry: QLCConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    data = entry.runtime_data
    if GROUP_FIXTURES not in data.groups:
        return
    devices = {d.key: d for d in data.loaded.plan.devices}
    async_add_entities(
        QLCLight(data.coordinator, entry.entry_id, spec, devices[spec.device])
        for spec in data.loaded.plan.lights
    )


class QLCLight(QLCEntity, LightEntity):
    _attr_name = None

    def __init__(self, coordinator, entry_id, spec: LightSpec, device) -> None:
        super().__init__(coordinator, entry_id, spec.key, fixture_device_info(entry_id, device))
        self._spec = spec
        if {"r", "g", "b", "w"} <= spec.colours.keys():
            mode = ColorMode.RGBW
        elif spec.colours:
            mode = ColorMode.RGB
        elif spec.wheel is not None:
            mode = ColorMode.HS
        else:
            mode = ColorMode.BRIGHTNESS
        self._attr_color_mode = mode
        self._attr_supported_color_modes = {mode}
        if spec.effects:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = [e.label for e in spec.effects]

    def _value(self, address: int) -> int:
        return self.coordinator.data.channels.get(address, 0)

    def _rgb_values(self) -> tuple[int, ...]:
        order = ("r", "g", "b", "w") if self._attr_color_mode is ColorMode.RGBW else ("r", "g", "b")
        return tuple(self._value(self._spec.colours[k].address) for k in order)

    @property
    def is_on(self) -> bool | None:
        if self._spec.dimmer is not None:
            if self._spec.dimmer.address not in self.coordinator.data.channels:
                return None
            return self._value(self._spec.dimmer.address) > 0
        return any(self._rgb_values())

    @property
    def brightness(self) -> int | None:
        if self._spec.dimmer is not None:
            return self._value(self._spec.dimmer.address)
        return max(self._rgb_values(), default=0)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        if self._attr_color_mode is not ColorMode.RGB:
            return None
        return self._normalised()[:3]

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        if self._attr_color_mode is not ColorMode.RGBW:
            return None
        return self._normalised()

    def _normalised(self) -> tuple[int, ...]:
        values = self._rgb_values()
        if self._spec.dimmer is not None:
            return values
        peak = max(values, default=0)
        return tuple(round(v * 255 / peak) for v in values) if peak else values

    @property
    def hs_color(self) -> tuple[float, float] | None:
        wheel = self._spec.wheel
        if self._attr_color_mode is not ColorMode.HS or wheel is None:
            return None
        option = wheel.option_for(self._value(wheel.channel.address))
        if option is None or option.rgb is None:
            return None
        return color_util.color_RGB_to_hs(*option.rgb)

    @property
    def effect(self) -> str | None:
        if not self._spec.effects:
            return None
        channel = self._spec.effects[0].channel
        value = self._value(channel.address)
        cap = channel.definition.capability_for(value)
        if cap is None:
            return None
        for option in self._spec.effects:
            if option.channel.address == channel.address and cap.min <= option.value <= cap.max:
                return option.label
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {ATTR_DMX_ADDRESS: sorted(c.address for c in self._spec.channels)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        spec = self._spec
        values: dict[int, int] = {}
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None:
            brightness = self.brightness if self.is_on else 255
            brightness = brightness or 255

        if spec.colours:
            if ATTR_RGBW_COLOR in kwargs:
                colour = tuple(kwargs[ATTR_RGBW_COLOR])
            elif ATTR_RGB_COLOR in kwargs:
                colour = tuple(kwargs[ATTR_RGB_COLOR])
            elif ATTR_HS_COLOR in kwargs:
                colour = color_util.color_hs_to_RGB(*kwargs[ATTR_HS_COLOR])
            else:
                colour = self._normalised() if any(self._rgb_values()) else (255,) * 4
            scale = 1.0 if spec.dimmer is not None else brightness / 255
            for key, val in zip(("r", "g", "b", "w"), colour, strict=False):
                if key in spec.colours:
                    values[spec.colours[key].address] = round(val * scale)

        if spec.wheel is not None and (ATTR_HS_COLOR in kwargs or ATTR_RGB_COLOR in kwargs):
            rgb = (kwargs[ATTR_RGB_COLOR] if ATTR_RGB_COLOR in kwargs
                   else color_util.color_hs_to_RGB(*kwargs[ATTR_HS_COLOR]))
            option = nearest_colour(spec.wheel.options, tuple(rgb))
            if option is not None:
                values[spec.wheel.channel.address] = option.value

        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            option = next((e for e in spec.effects if e.label == effect), None)
            if option is not None:
                values[option.channel.address] = option.value

        if spec.dimmer is not None:
            values[spec.dimmer.address] = brightness
        await self.coordinator.async_set_channels(values)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self._spec.dimmer is not None:
            await self.coordinator.async_set_channels({self._spec.dimmer.address: 0})
        else:
            await self.coordinator.async_set_channels(
                {c.address: 0 for c in self._spec.colours.values()})
