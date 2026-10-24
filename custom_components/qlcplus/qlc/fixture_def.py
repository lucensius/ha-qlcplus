"""Parser for QLC+ fixture definitions (.qxf)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .presets import CHANNEL_PRESETS, PRIMARY_COLOURS, ChannelGroup
from .xmlutil import QLCParseError, children, parse_xml, text, to_int


@dataclass(slots=True)
class Capability:
    min: int
    max: int
    name: str
    preset: str | None = None
    color1: str | None = None  # "#rrggbb"
    color2: str | None = None
    resource: str | None = None  # e.g. gobo image path or numeric parameter

    @property
    def center(self) -> int:
        return (self.min + self.max) // 2

    def contains(self, value: int) -> bool:
        return self.min <= value <= self.max


@dataclass(slots=True)
class ChannelDef:
    name: str
    group: ChannelGroup
    fine: bool = False  # LSB of a 16-bit pair
    preset: str | None = None
    colour: int | None = None  # primary colour of an intensity channel, 0xRRGGBB
    default: int = 0
    capabilities: list[Capability] = field(default_factory=list)

    def capability_for(self, value: int) -> Capability | None:
        for cap in self.capabilities:
            if cap.contains(value):
                return cap
        return None


@dataclass(slots=True)
class Physical:
    pan_max: int = 0  # degrees
    tilt_max: int = 0
    focus_type: str = ""
    bulb_type: str = ""
    colour_temperature: int = 0
    power: int = 0


@dataclass(slots=True)
class Mode:
    name: str
    channels: list[ChannelDef]
    heads: list[list[int]]  # channel indices per head
    physical: Physical


@dataclass(slots=True)
class FixtureDef:
    manufacturer: str
    model: str
    type: str
    channels: dict[str, ChannelDef]
    modes: dict[str, Mode]
    physical: Physical
    author: str = ""

    def mode(self, name: str) -> Mode | None:
        return self.modes.get(name)


def _parse_capability(el) -> Capability:
    cap = Capability(
        min=to_int(el.get("Min")),
        max=to_int(el.get("Max"), 255),
        name=(el.text or "").strip(),
        preset=el.get("Preset"),
    )
    res = [el.get("Res1"), el.get("Res2")]
    if el.get("Res") is not None:  # pre 4.12 format: single resource
        res[0] = el.get("Res")
    if el.get("Color") is not None:  # pre 4.12 format: colours as attributes
        res = [el.get("Color"), el.get("Color2")]
    for idx, value in enumerate(res):
        if not value:
            continue
        if value.startswith("#") and len(value) in (7, 9):
            # QLC+ may store #AARRGGBB; keep the RGB part
            rgb = "#" + value[-6:].lower()
            if idx == 0:
                cap.color1 = rgb
            else:
                cap.color2 = rgb
        elif idx == 0:
            cap.resource = value
    return cap


def _parse_channel(el) -> ChannelDef:
    preset = el.get("Preset")
    group = ChannelGroup.INTENSITY
    fine = False
    colour: int | None = None
    if preset and preset in CHANNEL_PRESETS:
        group, colour_name, fine = CHANNEL_PRESETS[preset]
        colour = PRIMARY_COLOURS.get(colour_name) if colour_name else None
    elif preset:
        preset = None  # unknown or "Custom"
    group_el = el.find("Group")
    if group_el is not None:
        group = ChannelGroup.from_xml((group_el.text or "").strip())
        fine = to_int(group_el.get("Byte")) == 1
    colour_el = el.find("Colour")
    if colour_el is not None and colour_el.text:
        colour = PRIMARY_COLOURS.get(colour_el.text.strip(), colour)
    caps = [_parse_capability(c) for c in children(el, "Capability")]
    caps.sort(key=lambda c: c.min)
    return ChannelDef(
        name=el.get("Name", "").strip(),
        group=group,
        fine=fine,
        preset=preset,
        colour=colour,
        default=to_int(el.get("Default")),
        capabilities=caps,
    )


def _parse_physical(el, base: Physical | None = None) -> Physical:
    phys = Physical() if base is None else Physical(**{
        f: getattr(base, f) for f in Physical.__slots__
    })
    if el is None:
        return phys
    if (focus := el.find("Focus")) is not None:
        phys.pan_max = to_int(focus.get("PanMax"))
        phys.tilt_max = to_int(focus.get("TiltMax"))
        phys.focus_type = focus.get("Type", "")
    if (bulb := el.find("Bulb")) is not None:
        phys.bulb_type = bulb.get("Type", "")
        phys.colour_temperature = to_int(bulb.get("ColourTemperature"))
    if (tech := el.find("Technical")) is not None:
        phys.power = to_int(tech.get("PowerConsumption"))
    return phys


def parse_fixture_def(data: bytes | str, source: str = "fixture") -> FixtureDef:
    """Parse the contents of a .qxf file."""
    root = parse_xml(data, "FixtureDefinition", source)
    channels: dict[str, ChannelDef] = {}
    for ch_el in children(root, "Channel"):
        ch = _parse_channel(ch_el)
        channels[ch.name] = ch
    physical = _parse_physical(root.find("Physical"))

    modes: dict[str, Mode] = {}
    for mode_el in children(root, "Mode"):
        slots: dict[int, ChannelDef] = {}
        for idx, mch in enumerate(children(mode_el, "Channel")):
            number = to_int(mch.get("Number"), idx)
            ch_name = (mch.text or "").strip()
            if ch_name not in channels:
                raise QLCParseError(
                    f"{source}: mode '{mode_el.get('Name')}' references unknown "
                    f"channel '{ch_name}'"
                )
            slots[number] = channels[ch_name]
        mode_channels = [slots[i] for i in sorted(slots)]
        heads = []
        for head_el in children(mode_el, "Head"):
            head = [to_int(c.text) for c in children(head_el, "Channel")]
            heads.append([i for i in head if 0 <= i < len(mode_channels)])
        modes[mode_el.get("Name", "")] = Mode(
            name=mode_el.get("Name", ""),
            channels=mode_channels,
            heads=[h for h in heads if h],
            physical=_parse_physical(mode_el.find("Physical"), physical),
        )

    creator = root.find("Creator")
    return FixtureDef(
        manufacturer=text(root, "Manufacturer"),
        model=text(root, "Model"),
        type=text(root, "Type", "Other"),
        channels=channels,
        modes=modes,
        physical=physical,
        author=text(creator, "Author") if creator is not None else "",
    )


def generic_dimmer_def(channels: int) -> FixtureDef:
    """Equivalent of Fixture::genericDimmerDef(): one head per channel."""
    chans = [
        ChannelDef(name=f"Dimmer #{i + 1}", group=ChannelGroup.INTENSITY,
                   capabilities=[Capability(0, 255, "Intensity")])
        for i in range(channels)
    ]
    mode = Mode(
        name=f"{channels} Channel",
        channels=chans,
        heads=[[i] for i in range(channels)],
        physical=Physical(),
    )
    return FixtureDef(
        manufacturer="Generic", model="Generic", type="Dimmer",
        channels={c.name: c for c in chans}, modes={mode.name: mode},
        physical=Physical(), author="QLC+",
    )


def generic_rgb_panel_def(channels: int, components: str) -> FixtureDef:
    """Equivalent of Fixture::genericRGBPanelDef(): one head per pixel column.

    ``components`` is the mode name stored in the project, e.g. "RGB",
    "GRB 16bit" or "RGBW".
    """
    parts = components.split()
    order = parts[0] if parts and parts[0] in (
        "RGB", "BGR", "BRG", "GBR", "GRB", "RBG", "RGBW") else "RGB"
    is16 = len(parts) > 1 and parts[1] == "16bit"
    names = {"R": "Red", "G": "Green", "B": "Blue", "W": "White"}
    per_px = len(order) * (2 if is16 else 1)
    pixels = max(1, channels // per_px)
    chans: list[ChannelDef] = []
    heads: list[list[int]] = []
    for px in range(pixels):
        head = []
        for letter in order:
            colour = names[letter]
            head.append(len(chans))
            chans.append(ChannelDef(
                name=f"{colour} {px + 1}", group=ChannelGroup.INTENSITY,
                colour=PRIMARY_COLOURS[colour]))
            if is16:
                head.append(len(chans))
                chans.append(ChannelDef(
                    name=f"{colour} Fine {px + 1}", group=ChannelGroup.INTENSITY,
                    colour=PRIMARY_COLOURS[colour], fine=True))
        heads.append(head)
    mode = Mode(name=components, channels=chans, heads=heads, physical=Physical())
    return FixtureDef(
        manufacturer="Generic", model="RGBPanel", type="LED Bar (Pixels)",
        channels={c.name: c for c in chans}, modes={mode.name: mode},
        physical=Physical(), author="QLC+",
    )
