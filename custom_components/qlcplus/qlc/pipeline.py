"""Load a project end to end: parse, resolve fixtures, patch, analyse, plan."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .fixture_def import FixtureDef, parse_fixture_def
from .model import PatchedFixture, SafetyInfo, analyse_safety, patch_fixture
from .planner import Plan, build_plan
from .project import Project, parse_project
from .resolver import Fetcher, FixtureResolver, ResolveResult


@dataclass(slots=True)
class LoadedProject:
    project: Project
    user_definitions: list[FixtureDef]
    resolve: ResolveResult
    fixtures: dict[int, PatchedFixture]
    safety: SafetyInfo
    plan: Plan

    @property
    def errors(self) -> dict[int, str]:
        return self.resolve.errors


@dataclass(slots=True)
class Mismatch:
    missing_functions: list[int]
    renamed_functions: list[int]
    extra_functions: list[int]
    missing_widgets: list[int]
    renamed_widgets: list[int]
    extra_widgets: list[int]

    @property
    def is_match(self) -> bool:
        return not any((self.missing_functions, self.renamed_functions,
                        self.missing_widgets, self.renamed_widgets,
                        self.extra_functions, self.extra_widgets))

    def summary(self) -> str:
        parts = []
        for label, ids in (
            ("functions missing in QLC+", self.missing_functions),
            ("functions with other names", self.renamed_functions),
            ("functions only in QLC+", self.extra_functions),
            ("widgets missing in QLC+", self.missing_widgets),
            ("widgets with other captions", self.renamed_widgets),
            ("widgets only in QLC+", self.extra_widgets),
        ):
            if ids:
                shown = ", ".join(map(str, ids[:10])) + (" …" if len(ids) > 10 else "")
                parts.append(f"{len(ids)} {label} (IDs {shown})")
        return "; ".join(parts)


def parse_user_fixtures(files: Iterable[tuple[str, bytes]]) -> list[FixtureDef]:
    return [parse_fixture_def(data, name) for name, data in files]


async def load_project(
    project_data: bytes,
    fixture_files: Iterable[tuple[str, bytes]] = (),
    cache_dir: Path | None = None,
    fetcher: Fetcher | None = None,
    source: str = "project",
) -> LoadedProject:
    """Raise QLCParseError for broken files; missing fixtures end up in errors."""
    project = parse_project(project_data, source)
    user_defs = parse_user_fixtures(fixture_files)
    resolver = FixtureResolver(user_defs, cache_dir, fetcher)
    resolved = await resolver.resolve(project.fixtures.values())
    fixtures = {fid: patch_fixture(r) for fid, r in resolved.fixtures.items()}
    # Without a definition we cannot tell whether a fixture is a laser, so
    # unresolved fixtures are guarded like lasers.
    unknown = [project.fixtures[fid] for fid in resolved.errors]
    safety = analyse_safety(project, fixtures, unknown)
    plan = build_plan(project, fixtures, safety)
    return LoadedProject(project, user_defs, resolved, fixtures, safety, plan)


def compare_with_live(project: Project, functions: dict[int, str],
                      widgets: dict[int, str]) -> Mismatch:
    def diff(local: dict[int, str], live: dict[int, str]):
        missing = sorted(set(local) - set(live))
        renamed = sorted(i for i in set(local) & set(live) if local[i] != live[i])
        extra = sorted(set(live) - set(local))
        return missing, renamed, extra

    fm, fr, fe = diff({f.id: f.name for f in project.functions.values()}, functions)
    wm, wr, we = diff({w.id: w.caption for w in project.widgets.values()}, widgets)
    return Mismatch(fm, fr, fe, wm, wr, we)
