"""Tests for fixture resolution, patching and the laser safety analysis."""

import os
import time

import pytest
from qlc.fixture_def import parse_fixture_def
from qlc.model import analyse_safety, dimmer_channel, patch_fixture, strip_head_label
from qlc.presets import ChannelGroup
from qlc.project import ProjectFixture, parse_project
from qlc.resolver import LIBRARY_BASE, LIBRARY_REF, FixtureResolver, parse_fixtures_map


class FakeLibrary:
    """Serves files from tests/data/library and records requests."""

    def __init__(self, root, fail=False):
        self.root = root
        self.fail = fail
        self.requests = []

    async def __call__(self, url):
        self.requests.append(url)
        if self.fail:
            raise OSError("offline")
        base = LIBRARY_BASE.format(ref=LIBRARY_REF)
        assert url.startswith(base)
        path = self.root / url[len(base):]
        if not path.exists():
            raise FileNotFoundError(url)
        return path.read_bytes()


@pytest.fixture
def project(data_dir):
    return parse_project((data_dir / "Lichtsteuerung.qxw").read_bytes())


@pytest.fixture
def user_defs(data_dir):
    return [parse_fixture_def((data_dir / "Eurolite-LED-TMH-Bar-S120-MH-Spots.qxf").read_bytes())]


async def test_resolve_test_project(project, user_defs, data_dir, tmp_path):
    lib = FakeLibrary(data_dir / "library")
    resolver = FixtureResolver(user_defs, cache_dir=tmp_path, fetcher=lib)
    result = await resolver.resolve(project.fixtures.values())
    assert result.errors == {}
    assert result.warnings == []
    assert result.fixtures[0].source == "user"
    assert result.fixtures[1].source == "library"
    assert result.fixtures[1].definition.type == "Laser"
    assert result.fixtures[2].mode.name == "1ch"
    # only the map and the two needed definitions are downloaded
    assert sorted(u.rsplit("/", 1)[1] for u in lib.requests) == [
        "FixturesMap.xml", "Laserworld-EL-400RGB-MK2.qxf", "Stairville-AF-150.qxf"]
    assert (tmp_path / "Laserworld/Laserworld-EL-400RGB-MK2.qxf").exists()

    # second resolver works from the cache without network
    offline = FakeLibrary(data_dir / "library", fail=True)
    again = await FixtureResolver(user_defs, tmp_path, offline).resolve(project.fixtures.values())
    assert again.errors == {}
    assert again.fixtures[1].source == "cache"
    assert offline.requests == []


async def test_stale_map_is_refreshed_but_used_offline(data_dir, tmp_path):
    (tmp_path / "FixturesMap.xml").write_bytes((data_dir / "library/FixturesMap.xml").read_bytes())
    old = time.time() - 365 * 24 * 3600
    os.utime(tmp_path / "FixturesMap.xml", (old, old))
    offline = FakeLibrary(data_dir / "library", fail=True)
    resolver = FixtureResolver([], tmp_path, offline)
    fx = ProjectFixture(1, "Laser", "Laserworld", "EL-400RGB MK2", "9 Channel", 0, 0, 9)
    with pytest.raises(LookupError, match="could not be downloaded"):
        await resolver.resolve_one(fx)
    assert len(offline.requests) == 2  # map refresh attempt + definition


async def test_clear_errors(user_defs, data_dir, tmp_path):
    resolver = FixtureResolver(user_defs, tmp_path, FakeLibrary(data_dir / "library"))
    missing = ProjectFixture(7, "Spot", "Nobody", "Nothing", "8ch", 0, 0, 8)
    bad_mode = ProjectFixture(8, "Bar", "Eurolite", "LED TMH Bar S120 MH Spots", "12 Kanal", 0, 0, 12)
    result = await resolver.resolve([missing, bad_mode])
    assert "Nobody Nothing" in result.errors[7] and "mode '8ch'" in result.errors[7]
    assert "Mode '12 Kanal' not found for Eurolite LED TMH Bar S120 MH Spots" in result.errors[8]
    assert "38 Kanal" in result.errors[8]  # lists available modes


async def test_no_network_no_cache():
    resolver = FixtureResolver([])
    fx = ProjectFixture(1, "L", "Laserworld", "EL-400RGB MK2", "9 Channel", 0, 0, 9)
    with pytest.raises(LookupError, match="library is not reachable"):
        await resolver.resolve_one(fx)


async def test_generic_fixtures_need_no_definition():
    resolver = FixtureResolver([])
    dim = await resolver.resolve_one(ProjectFixture(1, "D", "Generic", "Generic", "Generic", 0, 0, 6))
    assert dim.source == "generic" and len(dim.mode.channels) == 6
    pnl = await resolver.resolve_one(ProjectFixture(2, "P", "Generic", "RGBPanel", "RGBW", 0, 0, 8))
    assert len(pnl.mode.heads) == 2


def test_fixtures_map_rejects_path_traversal(data_dir):
    fmap = parse_fixtures_map((data_dir / "library/FixturesMap.xml").read_bytes())
    assert fmap[("laserworld", "el-400rgb mk2")] == "Laserworld/Laserworld-EL-400RGB-MK2.qxf"
    assert ("..", "evil") not in fmap and len(fmap) == 2


@pytest.fixture
async def patched(project, user_defs, data_dir, tmp_path):
    resolver = FixtureResolver(user_defs, tmp_path, FakeLibrary(data_dir / "library"))
    result = await resolver.resolve(project.fixtures.values())
    return {fid: patch_fixture(r) for fid, r in result.fixtures.items()}


async def test_patch_moving_head_bar(patched):
    bar = patched[0]
    assert [h.label for h in bar.heads] == ["Kopf 1", "Kopf 2", "Kopf 3", "Kopf 4"]
    head2 = bar.heads[1]
    assert [c.address for c in head2.channels] == list(range(10, 19))
    pan = head2.channels[0]
    assert pan.group is ChannelGroup.PAN and pan.fine is head2.channels[1]
    assert head2.channels[2].fine is head2.channels[3]  # tilt + tilt fine
    assert len(head2.coarse()) == 7
    assert dimmer_channel(head2).name == "Dimmer Kopf 2"
    # macro channels are outside all heads
    assert [c.index for c in bar.master.channels] == [36, 37]
    assert strip_head_label("Pan Fein Kopf 1", "Kopf 1") == "Pan Fein"


async def test_patch_laser_and_fog(patched):
    laser, fog = patched[1], patched[2]
    assert laser.is_laser and laser.heads == []
    assert [c.address for c in laser.master.channels] == list(range(39, 48))
    assert fog.is_smoke and fog.master.channels[0].address == 48
    assert dimmer_channel(fog.master).name == "Pump"
    assert laser.master.channels[2].universe == 1
    assert laser.master.channels[2].universe_address == 41


async def test_laser_safety(project, patched):
    info = analyse_safety(project, patched)
    assert info.laser_fixtures == {1}
    assert info.laser_addresses == set(range(39, 48))
    # "Laser Aus" writes 0 -> safe; Musik/Auto/DMX scenes switch the laser on
    assert info.functions == {13, 14, 15, 16}
    assert {123, 124, 125, 126, 127} & info.widgets == {124, 125, 126, 127}
    # all laser sliders are guarded, fog is not a laser
    assert set(range(129, 137)) <= info.widgets
    assert 138 not in info.widgets and 2 not in info.widgets
    assert info.address_blocked(39, 1) and not info.address_blocked(39, 0)
    assert not info.address_blocked(48, 255)


def test_safety_through_nested_functions():
    xml = """<Workspace><Engine>
     <Fixture><Manufacturer>X</Manufacturer><Model>L</Model><Mode>m</Mode><ID>0</ID>
      <Name>L</Name><Universe>0</Universe><Address>0</Address><Channels>2</Channels></Fixture>
     <Function ID="0" Type="Scene" Name="on"><FixtureVal ID="0">0,10</FixtureVal></Function>
     <Function ID="1" Type="Chaser" Name="c"><Step Number="0">0</Step></Function>
     <Function ID="2" Type="Collection" Name="col"><Step Number="0">1</Step><Step Number="1">2</Step></Function>
     <Function ID="3" Type="Sequence" Name="s" BoundScene="4">
      <Step Number="0" Values="1">0:1,5</Step></Function>
     <Function ID="4" Type="Scene" Name="off"><FixtureVal ID="0">0,0,1,0</FixtureVal></Function>
     <Function ID="5" Type="Script" Name="scr"><Command>startfunction%3A2</Command></Function>
    </Engine></Workspace>"""
    project = parse_project(xml)

    class Stub:
        id = 0
        is_laser = True
        channels = []

    info = analyse_safety(project, {0: Stub()})
    assert info.functions == {0, 1, 2, 3, 5}
