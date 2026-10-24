"""Derive entity descriptions from a parsed project (no Home Assistant code).

Everything is derived generically from channel groups, presets and
capabilities, never from fixture or channel names.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field

from .model import (
    Head,
    PatchedChannel,
    PatchedFixture,
    SafetyInfo,
    dimmer_channel,
    strip_head_label,
)
from .presets import PRIMARY_COLOURS, ChannelGroup
from .project import FunctionType, Project, WidgetType

RGB_COLOURS = {
    PRIMARY_COLOURS["Red"]: "r",
    PRIMARY_COLOURS["Green"]: "g",
    PRIMARY_COLOURS["Blue"]: "b",
    PRIMARY_COLOURS["White"]: "w",
}

GROUPS_AS_SELECT = frozenset({
    ChannelGroup.COLOUR, ChannelGroup.GOBO, ChannelGroup.EFFECT,
    ChannelGroup.MAINTENANCE, ChannelGroup.PRISM,
})


@dataclass(slots=True)
class DeviceSpec:
    key: str
    name: str
    manufacturer: str
    model: str
    via: str | None  # key of the parent device, None = hub
    fixture_id: int
    head: int | None
    laser: bool = False
    fixture_type: str = ""


@dataclass(slots=True)
class Option:
    label: str
    value: int  # DMX value sent when the option is chosen
    min: int
    max: int
    rgb: tuple[int, int, int] | None = None
    rgb2: tuple[int, int, int] | None = None


@dataclass(slots=True)
class ChannelEntitySpec:
    key: str
    device: str
    name: str
    channel: PatchedChannel
    laser: bool
    enabled_default: bool = True


@dataclass(slots=True)
class SelectSpec(ChannelEntitySpec):
    options: list[Option] = field(default_factory=list)

    def option_for(self, value: int) -> Option | None:
        return next((o for o in self.options if o.min <= value <= o.max), None)


@dataclass(slots=True)
class NumberSpec(ChannelEntitySpec):
    unit: str | None = None  # "°", "%" or None (raw DMX)
    max_value: float = 255.0  # value shown at DMX max


@dataclass(slots=True)
class EffectOption:
    label: str
    channel: PatchedChannel
    value: int


@dataclass(slots=True)
class LightSpec:
    key: str
    device: str
    name: str | None  # None = use device name
    dimmer: PatchedChannel | None
    colours: dict[str, PatchedChannel]  # "r", "g", "b", "w"
    wheel: SelectSpec | None
    effects: list[EffectOption]
    laser: bool

    @property
    def channels(self) -> list[PatchedChannel]:
        chans = [c for c in (self.dimmer, *self.colours.values()) if c]
        if self.wheel:
            chans.append(self.wheel.channel)
        chans.extend(e.channel for e in self.effects)
        return list({c.address: c for c in chans}.values())


@dataclass(slots=True)
class FunctionSpec:
    function_id: int
    name: str
    kind: str  # "switch" or "button"
    function_type: str
    unsafe: bool


@dataclass(slots=True)
class WidgetSpec:
    widget_id: int
    name: str
    kind: str  # "switch", "button" or "number"
    function_id: int | None
    unsafe: bool
    enabled_default: bool
    low: int = 0
    high: int = 255
    action: str = ""


@dataclass(slots=True)
class Plan:
    devices: list[DeviceSpec] = field(default_factory=list)
    lights: list[LightSpec] = field(default_factory=list)
    selects: list[SelectSpec] = field(default_factory=list)
    numbers: list[NumberSpec] = field(default_factory=list)
    functions: list[FunctionSpec] = field(default_factory=list)
    widgets: list[WidgetSpec] = field(default_factory=list)
    blackout_widget: int | None = None
    has_laser: bool = False

    def channel_addresses(self) -> set[int]:
        out: set[int] = set()
        for spec in (*self.selects, *self.numbers):
            out.add(spec.channel.address)
            if spec.channel.fine:
                out.add(spec.channel.fine.address)
        for light in self.lights:
            out.update(c.address for c in light.channels)
        return out


def hex_to_rgb(value: str | None) -> tuple[int, int, int] | None:
    if not value or len(value) != 7:
        return None
    try:
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    except ValueError:
        return None


def _options(channel: PatchedChannel) -> list[Option]:
    caps = channel.definition.capabilities
    labels = [c.name or f"{c.min}–{c.max}" for c in caps]
    dupes = {lbl for lbl in labels if labels.count(lbl) > 1}
    return [
        Option(
            label=f"{lbl} ({c.min}–{c.max})" if lbl in dupes else lbl,
            value=c.center,
            min=c.min,
            max=c.max,
            rgb=hex_to_rgb(c.color1),
            rgb2=hex_to_rgb(c.color2),
        )
        for lbl, c in zip(labels, caps, strict=True)
    ]


def nearest_colour(options: list[Option], rgb: tuple[int, int, int]) -> Option | None:
    """Closest single-colour wheel slot to ``rgb`` (hue weighted)."""
    singles = [o for o in options if o.rgb and not o.rgb2]
    if not singles:
        return None
    h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in rgb))

    def distance(o: Option) -> float:
        oh, os_, ov = colorsys.rgb_to_hsv(*(x / 255 for x in o.rgb))
        dh = min(abs(h - oh), 1 - abs(h - oh)) * 2  # 0..1
        # hue matters less for unsaturated colours (white)
        return (dh * min(s, os_)) ** 2 * 4 + (s - os_) ** 2 + 0.1 * (v - ov) ** 2

    return min(singles, key=distance)


def _is_discrete(channel: PatchedChannel) -> bool:
    return len(channel.definition.capabilities) >= 2


class _Planner:
    def __init__(self, project: Project, fixtures: dict[int, PatchedFixture],
                 safety: SafetyInfo) -> None:
        self.project = project
        self.fixtures = fixtures
        self.safety = safety
        self.plan = Plan(has_laser=bool(safety.laser_fixtures))

    def run(self) -> Plan:
        for pf in sorted(self.fixtures.values(), key=lambda f: f.id):
            self._fixture(pf)
        self._functions()
        self._widgets()
        return self.plan

    # -- fixtures -----------------------------------------------------------

    def _fixture(self, pf: PatchedFixture) -> None:
        d = pf.resolved.definition
        fx_key = f"fx{pf.id}"
        self.plan.devices.append(DeviceSpec(
            fx_key, pf.name, d.manufacturer, d.model, None, pf.id, None,
            pf.is_laser, d.type))
        for head in pf.heads:
            key = f"{fx_key}_h{head.index}"
            self.plan.devices.append(DeviceSpec(
                key, f"{pf.name} {head.label}", d.manufacturer, d.model, fx_key,
                pf.id, head.index, pf.is_laser, d.type))
            self._unit(pf, head, key)
        if pf.master:
            self._unit(pf, pf.master, fx_key)

    def _unit(self, pf: PatchedFixture, unit: Head, device: str) -> None:
        laser = pf.is_laser
        used: set[int] = set()
        prefix = f"fx{pf.id}" + (f"_h{unit.index}" if unit.index is not None else "")

        def name(ch: PatchedChannel) -> str:
            return strip_head_label(ch.name, unit.label)

        selects: dict[int, SelectSpec] = {}
        for ch in unit.coarse():
            if ch.group in GROUPS_AS_SELECT and _is_discrete(ch):
                selects[ch.index] = SelectSpec(
                    key=f"{prefix}_ch{ch.index}", device=device, name=name(ch),
                    channel=ch, laser=laser, options=_options(ch),
                    enabled_default=ch.group is not ChannelGroup.MAINTENANCE)

        # light
        if not pf.is_smoke and not laser:
            dimmer = dimmer_channel(unit)
            colours: dict[str, PatchedChannel] = {}
            for ch in unit.coarse():
                letter = RGB_COLOURS.get(ch.definition.colour or -1)
                if ch.group is ChannelGroup.INTENSITY and letter and letter not in colours:
                    colours[letter] = ch
            if not {"r", "g", "b"} <= colours.keys():
                colours = {}
            wheel = next((s for s in selects.values() if s.channel.group is ChannelGroup.COLOUR
                          and any(o.rgb for o in s.options)), None)
            effect_selects = [s for s in selects.values()
                              if s.channel.group in (ChannelGroup.GOBO, ChannelGroup.EFFECT)]
            effects = [
                EffectOption(o.label if len(effect_selects) == 1 else f"{s.name}: {o.label}",
                             s.channel, o.value)
                for s in effect_selects for o in s.options
            ]
            if dimmer or colours:
                light = LightSpec(
                    key=f"{prefix}_light", device=device, name=None, dimmer=dimmer,
                    colours=colours, wheel=wheel, effects=effects, laser=False)
                self.plan.lights.append(light)
                used.update(c.index for c in (dimmer, *colours.values()) if c)

        self.plan.selects.extend(selects.values())
        used.update(selects)

        # numbers for everything else
        phys = pf.resolved.mode.physical
        for ch in unit.coarse():
            if ch.index in used or ch.group is ChannelGroup.NOTHING:
                continue
            unit_str: str | None = None
            max_value = 255.0
            if ch.group in (ChannelGroup.PAN, ChannelGroup.TILT):
                limit = phys.pan_max if ch.group is ChannelGroup.PAN else phys.tilt_max
                unit_str, max_value = ("°", float(limit)) if limit > 0 else ("%", 100.0)
            elif ch.group is ChannelGroup.INTENSITY:
                unit_str, max_value = "%", 100.0
            self.plan.numbers.append(NumberSpec(
                key=f"{prefix}_ch{ch.index}", device=device, name=name(ch), channel=ch,
                laser=laser, unit=unit_str, max_value=max_value,
                enabled_default=ch.group is not ChannelGroup.MAINTENANCE))

    # -- functions and widgets ---------------------------------------------

    def _functions(self) -> None:
        for fn in sorted(self.project.functions.values(), key=lambda f: f.id):
            if fn.type is FunctionType.UNDEFINED:
                continue
            kind = "button" if fn.is_single_shot else "switch"
            self.plan.functions.append(FunctionSpec(
                fn.id, fn.name, kind, fn.type.value, fn.id in self.safety.functions))

    def _widgets(self) -> None:
        blackout = self.project.blackout_widgets()
        if blackout:
            self.plan.blackout_widget = blackout[0].id
        for w in sorted(self.project.widgets.values(), key=lambda w: w.id):
            name = " › ".join([*w.path, w.caption or f"#{w.id}"])
            unsafe = w.id in self.safety.widgets
            if w.type is WidgetType.BUTTON:
                if w.action == "Blackout":
                    continue
                if w.action == "Flash" and w.function is not None:
                    self.plan.widgets.append(WidgetSpec(
                        w.id, name, "button", w.function, unsafe, True, action=w.action))
                elif w.action == "Toggle" and w.function is not None:
                    self.plan.widgets.append(WidgetSpec(
                        w.id, name, "switch", w.function, unsafe, False, action=w.action))
                elif w.action == "StopAll":
                    self.plan.widgets.append(WidgetSpec(
                        w.id, name, "button", None, False, False, action=w.action))
            elif w.type is WidgetType.SLIDER:
                self.plan.widgets.append(WidgetSpec(
                    w.id, name, "number", w.playback_function, unsafe, False,
                    low=w.level_low if w.slider_mode == "Level" else 0,
                    high=w.level_high if w.slider_mode == "Level" else 255,
                    action=w.slider_mode))


def build_plan(project: Project, fixtures: dict[int, PatchedFixture], safety: SafetyInfo) -> Plan:
    return _Planner(project, fixtures, safety).run()
