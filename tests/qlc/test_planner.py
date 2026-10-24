"""Tests for entity planning from the test project."""

import pytest
from qlc.fixture_def import parse_fixture_def
from qlc.model import analyse_safety, patch_fixture
from qlc.planner import build_plan, nearest_colour
from qlc.project import ProjectFixture, parse_project
from qlc.resolver import FixtureResolver
from test_resolver_model import FakeLibrary


@pytest.fixture
async def plan(data_dir, tmp_path):
    project = parse_project((data_dir / "Lichtsteuerung.qxw").read_bytes())
    user = [parse_fixture_def((data_dir / "Eurolite-LED-TMH-Bar-S120-MH-Spots.qxf").read_bytes())]
    result = await FixtureResolver(user, tmp_path, FakeLibrary(data_dir / "library")).resolve(
        project.fixtures.values())
    fixtures = {fid: patch_fixture(r) for fid, r in result.fixtures.items()}
    return build_plan(project, fixtures, analyse_safety(project, fixtures))


async def test_devices(plan):
    names = [(d.key, d.name, d.via) for d in plan.devices]
    assert names[:3] == [
        ("fx0", "TMH Bar S120", None),
        ("fx0_h0", "TMH Bar S120 Kopf 1", "fx0"),
        ("fx0_h1", "TMH Bar S120 Kopf 2", "fx0"),
    ]
    assert ("fx1", "Laser EL-400RGB", None) in names
    assert next(d for d in plan.devices if d.key == "fx1").laser
    assert plan.has_laser


async def test_head_light_with_colour_wheel_and_gobos(plan):
    lights = {lt.key: lt for lt in plan.lights}
    assert set(lights) == {"fx0_h0_light", "fx0_h1_light", "fx0_h2_light", "fx0_h3_light"}
    light = lights["fx0_h1_light"]
    assert light.dimmer.address == 18
    assert light.colours == {}
    assert light.wheel.channel.address == 15
    labels = [e.label for e in light.effects]
    assert labels[:3] == ["Offen", "Gobo 1", "Gobo 2"]
    assert light.effects[1].value == 14 and light.effects[1].channel.address == 16


async def test_selects_and_numbers(plan):
    selects = {s.key: s for s in plan.selects}
    wheel = selects["fx0_h0_ch5"]
    assert wheel.name == "Farbrad" and wheel.device == "fx0_h0"
    assert [o.label for o in wheel.options][:2] == ["Weiß", "Rot"]
    assert wheel.option_for(15).label == "Rot"
    assert selects["fx0_h0_ch6"].name == "Goborad"
    macro = selects["fx0_ch36"]
    assert macro.device == "fx0" and macro.name == "Chaser / Bewegungsmakros"
    assert not selects["fx0_ch37"].enabled_default  # maintenance
    style = selects["fx1_ch0"]
    assert style.laser and style.options[0].label == "Laser off"

    numbers = {n.key: n for n in plan.numbers}
    pan = numbers["fx0_h0_ch0"]
    assert pan.name == "Pan" and pan.unit == "°" and pan.max_value == 540
    assert pan.channel.fine.address == 2
    assert numbers["fx0_h0_ch2"].max_value == 180
    assert numbers["fx0_h0_ch7"].name == "Strobe" and numbers["fx0_h0_ch7"].unit is None
    assert numbers["fx0_h0_ch4"].name == "Pan/Tilt Speed"
    laser_color = numbers["fx1_ch7"]
    assert laser_color.laser and laser_color.unit is None
    assert numbers["fx1_ch2"].unit == "°" and numbers["fx1_ch2"].max_value == 30
    fog = numbers["fx2_ch0"]
    assert fog.name == "Pump" and fog.unit == "%" and not fog.laser
    # fine channels never get their own entity
    assert "fx0_h0_ch1" not in numbers and "fx0_h0_ch3" not in numbers


async def test_functions_and_widgets(plan):
    functions = {f.function_id: f for f in plan.functions}
    assert functions[19].kind == "button" and functions[20].kind == "button"
    assert functions[21].kind == "switch"
    assert functions[13].unsafe and not functions[12].unsafe
    widgets = {w.widget_id: w for w in plan.widgets}
    assert widgets[138].kind == "button" and widgets[138].enabled_default
    assert widgets[9].kind == "switch" and not widgets[9].enabled_default
    assert widgets[9].name == "TMH Bar S120 – alle Köpfe › Szenen › Aus"
    assert widgets[2].kind == "number"
    assert widgets[124].unsafe and widgets[129].unsafe
    assert 1 not in widgets  # XY pads are not mapped
    assert plan.blackout_widget is None


async def test_channel_addresses_cover_all_entities(plan):
    addrs = plan.channel_addresses()
    assert set(range(1, 49)) == addrs


def test_nearest_colour():
    from qlc.planner import Option

    opts = [
        Option("Weiß", 5, 0, 9, (255, 255, 255)),
        Option("Rot", 15, 10, 19, (255, 0, 0)),
        Option("Orange", 65, 60, 69, (255, 128, 0)),
        Option("Lila", 75, 70, 79, (128, 0, 255)),
        Option("Lila + Orange", 85, 80, 89, (128, 0, 255), (255, 128, 0)),
    ]
    assert nearest_colour(opts, (250, 10, 10)).label == "Rot"
    assert nearest_colour(opts, (255, 160, 20)).label == "Orange"
    assert nearest_colour(opts, (240, 240, 230)).label == "Weiß"
    assert nearest_colour(opts, (150, 0, 200)).label == "Lila"
    assert nearest_colour([], (1, 2, 3)) is None


async def test_rgb_fixture_light(tmp_path):
    resolver = FixtureResolver([])
    res = await resolver.resolve_one(ProjectFixture(4, "Panel", "Generic", "RGBPanel", "RGBW", 0, 0, 4))
    fixtures = {4: patch_fixture(res)}
    project = parse_project("<Workspace><Engine/></Workspace>")
    plan = build_plan(project, fixtures, analyse_safety(project, fixtures))
    [light] = plan.lights
    assert set(light.colours) == {"r", "g", "b", "w"} and light.dimmer is None
    assert plan.numbers == []
