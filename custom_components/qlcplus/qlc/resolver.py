"""Resolve project fixtures to fixture definitions and modes.

Order: user supplied .qxf files, then the QLC+ fixture library (downloaded on
demand via FixturesMap.xml and cached on disk), then generic QLC+ fixtures.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .fixture_def import (
    FixtureDef,
    Mode,
    generic_dimmer_def,
    generic_rgb_panel_def,
    parse_fixture_def,
)
from .project import ProjectFixture
from .xmlutil import QLCParseError, children, parse_xml

_LOGGER = logging.getLogger(__name__)

LIBRARY_REF = "QLC+_4.14.4"
LIBRARY_BASE = "https://raw.githubusercontent.com/mcallegari/qlcplus/{ref}/resources/fixtures/"
MAP_FILE = "FixturesMap.xml"
MAP_MAX_AGE = 30 * 24 * 3600

Fetcher = Callable[[str], Awaitable[bytes]]


class FixtureResolveError(LookupError):
    """A fixture definition or mode could not be found."""


@dataclass(slots=True)
class ResolvedFixture:
    fixture: ProjectFixture
    definition: FixtureDef
    mode: Mode
    source: str  # "user", "library", "cache" or "generic"


@dataclass(slots=True)
class ResolveResult:
    fixtures: dict[int, ResolvedFixture] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _key(manufacturer: str, model: str) -> tuple[str, str]:
    return manufacturer.strip().casefold(), model.strip().casefold()


def _safe_name(name: str) -> bool:
    """Reject names that could escape the cache directory."""
    return bool(name) and "/" not in name and "\\" not in name and ".." not in name


def parse_fixtures_map(data: bytes | str) -> dict[tuple[str, str], str]:
    """Return {(manufacturer, model) (casefolded): "Dir/File.qxf"}."""
    root = parse_xml(data, "FixturesMap", MAP_FILE)
    out: dict[tuple[str, str], str] = {}
    for man in children(root, "M"):
        directory = man.get("n", "")
        if not _safe_name(directory):
            continue
        manufacturer = directory.replace("_", " ")
        for fx in children(man, "F"):
            if fx.get("n") and fx.get("m") and _safe_name(fx.get("n")):
                out.setdefault(_key(manufacturer, fx.get("m")), f"{directory}/{fx.get('n')}.qxf")
    return out


class FixtureResolver:
    def __init__(
        self,
        user_definitions: Iterable[FixtureDef] = (),
        cache_dir: Path | None = None,
        fetcher: Fetcher | None = None,
        library_ref: str = LIBRARY_REF,
    ) -> None:
        self._user: dict[tuple[str, str], FixtureDef] = {}
        for d in user_definitions:
            self._user[_key(d.manufacturer, d.model)] = d
        self._cache_dir = cache_dir
        self._fetcher = fetcher
        self._base = LIBRARY_BASE.format(ref=library_ref)
        self._map: dict[tuple[str, str], str] | None = None
        self._loaded: dict[str, FixtureDef] = {}

    async def resolve(self, fixtures: Iterable[ProjectFixture]) -> ResolveResult:
        result = ResolveResult()
        for fx in fixtures:
            try:
                result.fixtures[fx.id] = await self.resolve_one(fx)
            except FixtureResolveError as err:
                result.errors[fx.id] = str(err)
            res = result.fixtures.get(fx.id)
            if res and len(res.mode.channels) != fx.channels:
                result.warnings.append(
                    f"Fixture '{fx.name}': project says {fx.channels} channels, "
                    f"mode '{res.mode.name}' has {len(res.mode.channels)}"
                )
        return result

    async def resolve_one(self, fx: ProjectFixture) -> ResolvedFixture:
        if fx.manufacturer == "Generic" and fx.model == "Generic":
            d = generic_dimmer_def(fx.channels)
            return ResolvedFixture(fx, d, next(iter(d.modes.values())), "generic")
        if fx.manufacturer == "Generic" and fx.model == "RGBPanel":
            d = generic_rgb_panel_def(fx.channels, fx.mode)
            return ResolvedFixture(fx, d, next(iter(d.modes.values())), "generic")

        key = _key(fx.manufacturer, fx.model)
        source = "user"
        definition = self._user.get(key)
        if definition is None:
            definition, source = await self._library_def(fx)
        mode = definition.mode(fx.mode)
        if mode is None:
            # QLC+ matches modes exactly, but tolerate case/whitespace drift
            wanted = fx.mode.strip().casefold()
            mode = next((m for n, m in definition.modes.items()
                         if n.strip().casefold() == wanted), None)
        if mode is None:
            raise FixtureResolveError(
                f"Mode '{fx.mode}' not found for {fx.manufacturer} {fx.model} "
                f"(fixture '{fx.name}'). Available modes: "
                f"{', '.join(definition.modes) or 'none'}"
            )
        return ResolvedFixture(fx, definition, mode, source)

    async def _library_def(self, fx: ProjectFixture) -> tuple[FixtureDef, str]:
        not_found = FixtureResolveError(
            f"No fixture definition found for {fx.manufacturer} {fx.model} "
            f"(mode '{fx.mode}', fixture '{fx.name}'). Upload the .qxf file "
            f"for this fixture."
        )
        fmap = await self._fixtures_map()
        if fmap is None:
            raise FixtureResolveError(
                f"No uploaded definition for {fx.manufacturer} {fx.model} "
                f"(mode '{fx.mode}', fixture '{fx.name}') and the QLC+ fixture "
                f"library is not reachable. Upload the .qxf file or check "
                f"internet access."
            )
        rel = fmap.get(_key(fx.manufacturer, fx.model))
        if rel is None:
            raise not_found
        if rel in self._loaded:
            return self._loaded[rel], "library"
        data, source = await self._get(rel, max_age=None)
        if data is None:
            raise FixtureResolveError(
                f"Fixture definition for {fx.manufacturer} {fx.model} is in the "
                f"QLC+ library but could not be downloaded or read from cache. "
                f"Upload the .qxf file or check internet access."
            )
        try:
            definition = parse_fixture_def(data, rel)
        except QLCParseError as err:
            raise FixtureResolveError(str(err)) from err
        self._loaded[rel] = definition
        return definition, source

    async def _fixtures_map(self) -> dict[tuple[str, str], str] | None:
        if self._map is None:
            data, _ = await self._get(MAP_FILE, max_age=MAP_MAX_AGE)
            if data is not None:
                try:
                    self._map = parse_fixtures_map(data)
                except QLCParseError as err:
                    _LOGGER.warning("Ignoring broken fixtures map: %s", err)
        return self._map

    async def _get(self, rel: str, max_age: float | None) -> tuple[bytes | None, str]:
        """Return (data, source) from cache or network; stale cache as fallback."""
        cached = self._cache_dir / rel if self._cache_dir else None
        stale: bytes | None = None
        if cached is not None:
            info = await asyncio.to_thread(_read_cached, cached)
            if info is not None:
                data, mtime = info
                if max_age is None or time.time() - mtime < max_age:
                    return data, "cache"
                stale = data
        if self._fetcher is not None:
            try:
                data = await self._fetcher(self._base + rel)
            except Exception as err:  # noqa: BLE001 - any network error falls back
                _LOGGER.debug("Download of %s failed: %s", rel, err)
            else:
                if cached is not None:
                    await asyncio.to_thread(_write_cached, cached, data)
                return data, "library"
        return stale, "cache"


def _read_cached(path: Path) -> tuple[bytes, float] | None:
    try:
        return path.read_bytes(), path.stat().st_mtime
    except OSError:
        return None


def _write_cached(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError as err:
        _LOGGER.warning("Could not cache %s: %s", path, err)
