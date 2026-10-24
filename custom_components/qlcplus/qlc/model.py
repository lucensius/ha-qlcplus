"""Patched fixture model: project fixtures + definitions, heads and addresses."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .fixture_def import ChannelDef
from .presets import DIMMER_PRESETS, MASTER_DIMMER_PRESETS, ChannelGroup
from .project import Function, FunctionType, Project, Widget, WidgetType
from .resolver import ResolvedFixture

LASER_TYPES = frozenset({"Laser"})
SMOKE_TYPES = frozenset({"Smoke", "Hazer"})


@dataclass(slots=True)
class PatchedChannel:
    fixture_id: int
    index: int  # channel index inside the fixture
    address: int  # 1-based absolute DMX address, as used by CH| and sdResetChannel
    definition: ChannelDef
    head: int | None  # head index, None for channels outside all heads
    fine: PatchedChannel | None = None  # LSB partner of a 16-bit pair
    is_fine: bool = False

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def group(self) -> ChannelGroup:
        return self.definition.group

    @property
    def universe(self) -> int:
        """1-based universe, as used by getChannelsValues."""
        return (self.address - 1) // 512 + 1

    @property
    def universe_address(self) -> int:
        """1-based address inside the universe."""
        return (self.address - 1) % 512 + 1


@dataclass(slots=True)
class Head:
    fixture_id: int
    index: int | None  # None = fixture level (channels outside heads)
    label: str
    channels: list[PatchedChannel]

    def coarse(self) -> list[PatchedChannel]:
        return [c for c in self.channels if not c.is_fine]


@dataclass(slots=True)
class PatchedFixture:
    resolved: ResolvedFixture
    channels: list[PatchedChannel]
    heads: list[Head]
    master: Head | None  # channels not assigned to any head

    @property
    def id(self) -> int:
        return self.resolved.fixture.id

    @property
    def name(self) -> str:
        return self.resolved.fixture.name

    @property
    def type(self) -> str:
        return self.resolved.definition.type

    @property
    def is_laser(self) -> bool:
        return self.type in LASER_TYPES

    @property
    def is_smoke(self) -> bool:
        return self.type in SMOKE_TYPES


def _strip_fine(preset: str | None) -> str | None:
    return preset[:-4] if preset and preset.endswith("Fine") else preset


def _pair_fine_channels(channels: list[PatchedChannel]) -> None:
    """Attach each LSB channel to its MSB partner in the same head."""
    for idx, ch in enumerate(channels):
        d = ch.definition
        if not d.fine:
            continue
        ch.is_fine = True
        candidates = [
            c for c in channels[:idx]
            if not c.definition.fine and c.fine is None and c.head == ch.head
            and c.group == d.group and c.definition.colour == d.colour
        ]
        if d.preset:
            same = [c for c in candidates if c.definition.preset == _strip_fine(d.preset)]
            candidates = same or candidates
        if candidates:
            candidates[-1].fine = ch  # nearest preceding coarse channel


_WORD_RE = re.compile(r"\S+")


def _head_labels(fixture: ResolvedFixture, heads: list[list[int]]) -> list[str]:
    """Find a label per head from a token sequence shared by its channel names.

    Example: "Pan Kopf 1", "Dimmer Kopf 1" -> "Kopf 1". Falls back to "Head N".
    """
    names = [[fixture.mode.channels[i].name for i in head] for head in heads]
    labels: list[str] = []
    for n, head_names in enumerate(names):
        label = ""
        if len(head_names) > 1:
            words = [_WORD_RE.findall(x) for x in head_names]
            for take in range(min(len(w) for w in words), 0, -1):
                suffix = words[0][-take:]
                if all(w[-take:] == suffix for w in words) and any(len(w) > take for w in words):
                    label = " ".join(suffix)
                    break
                prefix = words[0][:take]
                if all(w[:take] == prefix for w in words) and any(len(w) > take for w in words):
                    label = " ".join(prefix)
                    break
        labels.append(label or f"Head {n + 1}")
    if len(set(labels)) != len(labels):
        labels = [f"Head {n + 1}" for n in range(len(heads))]
    return labels


def strip_head_label(name: str, label: str) -> str:
    """Remove the head label from a channel name ("Pan Kopf 1" -> "Pan")."""
    if label and name.endswith(" " + label):
        return name[: -len(label) - 1].strip() or name
    if label and name.startswith(label + " "):
        return name[len(label) + 1:].strip() or name
    return name


def patch_fixture(resolved: ResolvedFixture) -> PatchedFixture:
    fx = resolved.fixture
    mode = resolved.mode
    head_of: dict[int, int] = {}
    for h, head in enumerate(mode.heads):
        for i in head:
            head_of.setdefault(i, h)
    multi_head = len(mode.heads) > 1
    channels = [
        PatchedChannel(
            fixture_id=fx.id,
            index=i,
            address=fx.absolute_address + i + 1,
            definition=ch,
            head=head_of.get(i) if multi_head else (0 if mode.heads and i in head_of else None),
        )
        for i, ch in enumerate(mode.channels)
    ]
    _pair_fine_channels(channels)

    heads: list[Head] = []
    if multi_head:
        labels = _head_labels(resolved, mode.heads)
        for h, label in enumerate(labels):
            heads.append(Head(fx.id, h, label, [c for c in channels if c.head == h]))
    rest = [c for c in channels if not multi_head or c.head is None]
    master = Head(fx.id, None, "", rest) if rest else None
    return PatchedFixture(resolved, channels, heads, master)


def dimmer_channel(head: Head) -> PatchedChannel | None:
    """The main brightness channel of a head, if any."""
    coarse = head.coarse()
    for presets in (DIMMER_PRESETS - MASTER_DIMMER_PRESETS, MASTER_DIMMER_PRESETS):
        for c in coarse:
            if c.definition.preset in presets:
                return c
    for c in coarse:
        if c.group is ChannelGroup.INTENSITY and c.definition.colour is None and not c.definition.preset:
            return c
    return None


# ---------------------------------------------------------------------------
# Laser safety analysis
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SafetyInfo:
    """Which channels and project objects can drive a laser above 0."""

    laser_fixtures: set[int] = field(default_factory=set)
    laser_addresses: set[int] = field(default_factory=set)  # 1-based absolute
    functions: set[int] = field(default_factory=set)  # function IDs that are unsafe
    widgets: set[int] = field(default_factory=set)  # widget IDs that are unsafe

    def address_blocked(self, address: int, value: int) -> bool:
        return value > 0 and address in self.laser_addresses


def analyse_safety(project: Project, fixtures: dict[int, PatchedFixture]) -> SafetyInfo:
    info = SafetyInfo()
    for pf in fixtures.values():
        if pf.is_laser:
            info.laser_fixtures.add(pf.id)
            info.laser_addresses.update(c.address for c in pf.channels)
    if not info.laser_fixtures:
        return info

    lasers = info.laser_fixtures
    memo: dict[int, bool] = {}

    def group_fixtures(group_id: int | None) -> set[int]:
        grp = project.fixture_groups.get(group_id) if group_id is not None else None
        return {f for f, _ in grp.heads} if grp else set()

    def unsafe(fid: int, stack: frozenset[int] = frozenset()) -> bool:
        if fid in memo:
            return memo[fid]
        if fid in stack:
            return False
        fn: Function | None = project.functions.get(fid)
        if fn is None:
            return False
        stack = stack | {fid}
        result = False
        match fn.type:
            case FunctionType.SCENE:
                result = any(v.fixture in lasers and v.value > 0 for v in fn.values)
            case FunctionType.CHASER | FunctionType.SEQUENCE:
                result = any(
                    any(v.fixture in lasers and v.value > 0 for v in step.values)
                    or (not step.values and unsafe(step.function, stack))
                    for step in fn.steps
                )
            case FunctionType.COLLECTION | FunctionType.SHOW:
                result = any(unsafe(c, stack) for c in fn.children)
            case FunctionType.EFX:
                result = any(f in lasers for f, _ in fn.efx_fixtures)
            case FunctionType.RGBMATRIX:
                result = bool(group_fixtures(fn.fixture_group) & lasers)
            case FunctionType.SCRIPT:
                # unknown commands could do anything: treat as unsafe
                result = (
                    fn.script_unparsed
                    or any(v.fixture in lasers and v.value > 0 for v in fn.script_values)
                    or any(unsafe(c, stack) for c in fn.children)
                )
        memo[fid] = result
        return result

    for fid in project.functions:
        if unsafe(fid):
            info.functions.add(fid)

    for w in project.widgets.values():
        if widget_unsafe(w, info, lasers):
            info.widgets.add(w.id)
    return info


def widget_unsafe(w: Widget, info: SafetyInfo, lasers: set[int]) -> bool:
    match w.type:
        case WidgetType.BUTTON:
            return w.function is not None and w.function in info.functions
        case WidgetType.SLIDER:
            if w.slider_mode == "Playback":
                return w.playback_function in info.functions
            return any(f in lasers for f, _ in w.level_channels)
        case WidgetType.XYPAD:
            return any(f in lasers for f, _ in w.xy_fixtures)
        case WidgetType.CUE_LIST:
            return w.chaser in info.functions
    return False
