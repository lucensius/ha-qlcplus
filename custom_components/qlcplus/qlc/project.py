"""Parser for QLC+ workspaces (.qxw)."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from enum import StrEnum

from .xmlutil import children, parse_xml, text, to_bool, to_int


class FunctionType(StrEnum):
    SCENE = "Scene"
    CHASER = "Chaser"
    SEQUENCE = "Sequence"
    EFX = "EFX"
    COLLECTION = "Collection"
    SCRIPT = "Script"
    RGBMATRIX = "RGBMatrix"
    SHOW = "Show"
    AUDIO = "Audio"
    VIDEO = "Video"
    UNDEFINED = "Undefined"

    @classmethod
    def parse(cls, value: str | None) -> FunctionType:
        try:
            return cls(value or "")
        except ValueError:
            return cls.UNDEFINED


class RunOrder(StrEnum):
    LOOP = "Loop"
    SINGLE_SHOT = "SingleShot"
    PING_PONG = "PingPong"
    RANDOM = "Random"


class WidgetType(StrEnum):
    FRAME = "Frame"
    SOLO_FRAME = "SoloFrame"
    BUTTON = "Button"
    SLIDER = "Slider"
    XYPAD = "XYPad"
    CUE_LIST = "CueList"
    LABEL = "Label"
    SPEED_DIAL = "SpeedDial"
    AUDIO_TRIGGERS = "AudioTriggers"
    CLOCK = "Clock"
    ANIMATION = "Matrix"


WIDGET_TAGS = frozenset(t.value for t in WidgetType)


@dataclass(slots=True)
class ProjectFixture:
    id: int
    name: str
    manufacturer: str
    model: str
    mode: str
    universe: int  # 0-based
    address: int  # 0-based inside the universe
    channels: int

    @property
    def absolute_address(self) -> int:
        """0-based absolute address (universe * 512 + address)."""
        return self.universe * 512 + self.address

    @property
    def is_generic(self) -> bool:
        return self.manufacturer == "Generic" and self.model in ("Generic", "RGBPanel")


@dataclass(slots=True, frozen=True)
class SceneValue:
    fixture: int
    channel: int
    value: int


@dataclass(slots=True)
class ChaserStep:
    function: int
    fade_in: int = 0
    hold: int = 0
    fade_out: int = 0
    values: list[SceneValue] = field(default_factory=list)  # sequences only


@dataclass(slots=True)
class Function:
    id: int
    type: FunctionType
    name: str
    path: str = ""
    hidden: bool = False
    fade_in: int = 0
    fade_out: int = 0
    duration: int = 0  # ms
    # Scene
    values: list[SceneValue] = field(default_factory=list)
    # Chaser / Sequence
    run_order: RunOrder | None = None
    direction: str = ""
    steps: list[ChaserStep] = field(default_factory=list)
    bound_scene: int | None = None
    # Collection / Show: referenced functions
    children: list[int] = field(default_factory=list)
    # EFX: (fixture, head), RGBMatrix: fixture group
    efx_fixtures: list[tuple[int, int]] = field(default_factory=list)
    fixture_group: int | None = None
    # Script: (fixture, channel, value) from setfixture commands
    script_values: list[SceneValue] = field(default_factory=list)
    script_unparsed: bool = False

    @property
    def is_single_shot(self) -> bool:
        return self.run_order is RunOrder.SINGLE_SHOT


@dataclass(slots=True)
class Widget:
    id: int
    type: WidgetType
    caption: str
    parent: int | None
    path: list[str]  # captions of enclosing frames, outermost first
    solo_frame: int | None = None  # nearest enclosing solo frame
    disabled: bool = False
    # Button
    function: int | None = None
    action: str = ""  # Toggle / Flash / Blackout / StopAll
    # Slider
    slider_mode: str = ""  # Level / Playback / Submaster / Adjust
    widget_style: str = ""  # Slider / Knob
    level_low: int = 0
    level_high: int = 255
    level_channels: list[tuple[int, int]] = field(default_factory=list)
    playback_function: int | None = None
    # XY pad: (fixture, head)
    xy_fixtures: list[tuple[int, int]] = field(default_factory=list)
    # Cue list
    chaser: int | None = None


@dataclass(slots=True)
class FixtureGroup:
    id: int
    name: str
    heads: list[tuple[int, int]]  # (fixture, head)


@dataclass(slots=True)
class ChannelsGroup:
    id: int
    name: str
    channels: list[tuple[int, int]]  # (fixture, channel)


@dataclass(slots=True)
class Project:
    fixtures: dict[int, ProjectFixture]
    functions: dict[int, Function]
    widgets: dict[int, Widget]
    universes: dict[int, str]
    fixture_groups: dict[int, FixtureGroup] = field(default_factory=dict)
    channel_groups: dict[int, ChannelsGroup] = field(default_factory=dict)
    creator_version: str = ""
    author: str = ""

    def blackout_widgets(self) -> list[Widget]:
        return [w for w in self.widgets.values()
                if w.type is WidgetType.BUTTON and w.action == "Blackout"]


def _int_or_none(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    v = to_int(value, -1)
    # 4294967295 is Function::invalidId()
    return None if v < 0 or v == 0xFFFFFFFF else v


def _parse_pairs(raw: str) -> list[tuple[int, int]]:
    parts = [p for p in raw.replace(" ", "").split(",") if p != ""]
    return [(to_int(parts[i]), to_int(parts[i + 1])) for i in range(0, len(parts) - 1, 2)]


def _parse_scene(fn: Function, el, channel_groups: dict[int, ChannelsGroup]) -> None:
    for fv in children(el, "FixtureVal"):
        fid = to_int(fv.get("ID"), -1)
        for ch, val in _parse_pairs(fv.text or ""):
            fn.values.append(SceneValue(fid, ch, val))
    for old in children(el, "Value"):  # QLC 3 / early 4 format
        fn.values.append(SceneValue(
            to_int(old.get("Fixture")), to_int(old.get("Channel")), to_int(old.text)))
    cgv = el.find("ChannelGroupsVal")
    if cgv is not None and cgv.text:
        for gid, val in _parse_pairs(cgv.text):
            group = channel_groups.get(gid)
            if group:
                fn.values.extend(SceneValue(f, c, val) for f, c in group.channels)


def _parse_step_values(raw: str) -> list[SceneValue]:
    # "fixtureID:ch,val,ch,val:fixtureID:ch,val"
    out: list[SceneValue] = []
    chunks = raw.strip().split(":")
    for i in range(0, len(chunks) - 1, 2):
        fid = to_int(chunks[i], -1)
        out.extend(SceneValue(fid, c, v) for c, v in _parse_pairs(chunks[i + 1]))
    return out


_SETFIXTURE_RE = re.compile(r"setfixture:(\d+)\s+ch:(\d+)\s+val:(\d+)")


def _parse_function(el, channel_groups: dict[int, ChannelsGroup]) -> Function:
    fn = Function(
        id=to_int(el.get("ID"), -1),
        type=FunctionType.parse(el.get("Type")),
        name=el.get("Name", ""),
        path=el.get("Path", ""),
        hidden=to_bool(el.get("Hidden")),
    )
    speed = el.find("Speed")
    if speed is not None:
        fn.fade_in = to_int(speed.get("FadeIn"))
        fn.fade_out = to_int(speed.get("FadeOut"))
        fn.duration = to_int(speed.get("Duration"))

    match fn.type:
        case FunctionType.SCENE:
            _parse_scene(fn, el, channel_groups)
        case FunctionType.CHASER | FunctionType.SEQUENCE:
            ro = text(el, "RunOrder", "Loop")
            fn.run_order = RunOrder(ro) if ro in RunOrder._value2member_map_ else RunOrder.LOOP
            fn.direction = text(el, "Direction", "Forward")
            fn.bound_scene = _int_or_none(el.get("BoundScene"))
            for step in children(el, "Step"):
                cs = ChaserStep(
                    function=to_int(step.text, -1) if step.get("Values") is None
                    else (fn.bound_scene if fn.bound_scene is not None else -1),
                    fade_in=to_int(step.get("FadeIn")),
                    hold=to_int(step.get("Hold")),
                    fade_out=to_int(step.get("FadeOut")),
                )
                if step.get("Values") is not None:
                    cs.values = _parse_step_values(step.text or "")
                fn.steps.append(cs)
        case FunctionType.COLLECTION:
            fn.children = [to_int(s.text, -1) for s in children(el, "Step")]
        case FunctionType.SHOW:
            for track in children(el, "Track"):
                fn.children.extend(
                    to_int(sf.get("ID"), -1) for sf in children(track, "ShowFunction"))
        case FunctionType.EFX:
            for fx in children(el, "Fixture"):
                fn.efx_fixtures.append((to_int(text(fx, "ID"), -1), to_int(text(fx, "Head"))))
        case FunctionType.RGBMATRIX:
            fn.fixture_group = _int_or_none(text(el, "FixtureGroup") or None)
        case FunctionType.SCRIPT:
            for cmd in children(el, "Command"):
                line = urllib.parse.unquote(cmd.text or "").strip()
                if not line or line.startswith("//"):
                    continue
                if m := _SETFIXTURE_RE.search(line):
                    fn.script_values.append(SceneValue(*(int(g) for g in m.groups())))
                elif line.startswith(("startfunction:", "stopfunction:")):
                    fn.children.append(to_int(line.split(":", 1)[1].split()[0], -1))
                elif not line.startswith(("wait", "label", "jump", "blackout", "systemcommand")):
                    fn.script_unparsed = True
                if line.startswith("blackout"):
                    fn.script_unparsed = True
    return fn


def _parse_widget(el, parent: Widget | None, path: list[str], out: dict[int, Widget]) -> None:
    wtype = WidgetType(el.tag)
    widget = Widget(
        id=to_int(el.get("ID"), -1),
        type=wtype,
        caption=el.get("Caption", ""),
        parent=parent.id if parent else None,
        path=list(path),
        solo_frame=(parent.id if parent and parent.type is WidgetType.SOLO_FRAME
                    else parent.solo_frame if parent else None),
        disabled=to_bool(text(el, "Disabled") or None),
    )
    match wtype:
        case WidgetType.BUTTON:
            f = el.find("Function")
            widget.function = _int_or_none(f.get("ID")) if f is not None else None
            widget.action = text(el, "Action", "Toggle")
        case WidgetType.SLIDER:
            widget.widget_style = el.get("WidgetStyle", "Slider")
            widget.slider_mode = text(el, "SliderMode", "Level")
            if (level := el.find("Level")) is not None:
                widget.level_low = to_int(level.get("LowLimit"))
                widget.level_high = to_int(level.get("HighLimit"), 255)
                widget.level_channels = [
                    (to_int(c.get("Fixture"), -1), to_int(c.text))
                    for c in children(level, "Channel")
                ]
            if (pb := el.find("Playback")) is not None:
                widget.playback_function = _int_or_none(text(pb, "Function") or None)
        case WidgetType.XYPAD:
            widget.xy_fixtures = [
                (to_int(f.get("ID"), -1), to_int(f.get("Head")))
                for f in children(el, "Fixture")
            ]
        case WidgetType.CUE_LIST:
            widget.chaser = _int_or_none(text(el, "Chaser") or None)
    if widget.id >= 0:
        out[widget.id] = widget
    if wtype in (WidgetType.FRAME, WidgetType.SOLO_FRAME):
        sub_path = path + [widget.caption] if widget.caption else path
        for child in el:
            if child.tag in WIDGET_TAGS:
                _parse_widget(child, widget, sub_path, out)


def parse_project(data: bytes | str, source: str = "project") -> Project:
    """Parse the contents of a .qxw workspace."""
    root = parse_xml(data, "Workspace", source)
    engine = root.find("Engine")
    if engine is None:
        engine = root  # tolerate exported fragments
    creator = root.find("Creator")

    universes = {}
    iomap = engine.find("InputOutputMap")
    if iomap is not None:
        for uni in children(iomap, "Universe"):
            universes[to_int(uni.get("ID"))] = uni.get("Name", "")

    fixtures: dict[int, ProjectFixture] = {}
    for fx in children(engine, "Fixture"):
        f = ProjectFixture(
            id=to_int(text(fx, "ID"), -1),
            name=text(fx, "Name"),
            manufacturer=text(fx, "Manufacturer"),
            model=text(fx, "Model"),
            mode=text(fx, "Mode"),
            universe=to_int(text(fx, "Universe")),
            address=to_int(text(fx, "Address")),
            channels=to_int(text(fx, "Channels"), 1),
        )
        fixtures[f.id] = f
        universes.setdefault(f.universe, f"Universe {f.universe + 1}")

    fixture_groups: dict[int, FixtureGroup] = {}
    for grp in children(engine, "FixtureGroup"):
        fg = FixtureGroup(
            id=to_int(grp.get("ID"), -1),
            name=text(grp, "Name"),
            heads=[(to_int(h.get("Fixture"), -1), to_int(h.text)) for h in children(grp, "Head")],
        )
        fixture_groups[fg.id] = fg

    channel_groups: dict[int, ChannelsGroup] = {}
    for grp in children(engine, "ChannelsGroup"):
        cg = ChannelsGroup(
            id=to_int(grp.get("ID"), -1),
            name=grp.get("Name", ""),
            channels=_parse_pairs(grp.text or ""),
        )
        channel_groups[cg.id] = cg

    functions = {}
    for fel in children(engine, "Function"):
        fn = _parse_function(fel, channel_groups)
        functions[fn.id] = fn

    widgets: dict[int, Widget] = {}
    vc = root.find("VirtualConsole")
    if vc is not None:
        for top in vc:
            if top.tag in WIDGET_TAGS:
                # The outermost frame is the VC "contents" frame. QLC+ does not
                # report it over the API, so skip it but keep its children.
                for child in top:
                    if child.tag in WIDGET_TAGS:
                        _parse_widget(child, None, [], widgets)

    return Project(
        fixtures=fixtures,
        functions=functions,
        widgets=widgets,
        universes=dict(sorted(universes.items())),
        fixture_groups=fixture_groups,
        channel_groups=channel_groups,
        creator_version=text(creator, "Version") if creator is not None else "",
        author=text(creator, "Author") if creator is not None else "",
    )
