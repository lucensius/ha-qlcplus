"""Tests for the .qxf parser."""

import pytest
from qlc.fixture_def import generic_dimmer_def, generic_rgb_panel_def, parse_fixture_def
from qlc.presets import ChannelGroup
from qlc.xmlutil import QLCParseError


@pytest.fixture
def tmh(data_dir):
    return parse_fixture_def((data_dir / "Eurolite-LED-TMH-Bar-S120-MH-Spots.qxf").read_bytes())


def test_tmh_header(tmh):
    assert tmh.manufacturer == "Eurolite"
    assert tmh.model == "LED TMH Bar S120 MH Spots"
    assert tmh.type == "Moving Head"
    assert tmh.author == "Lucas"


def test_tmh_mode_and_heads(tmh):
    mode = tmh.mode("38 Kanal")
    assert mode is not None
    assert len(mode.channels) == 38
    assert len(mode.heads) == 4
    assert mode.heads[1] == list(range(9, 18))
    assert mode.channels[36].name == "Chaser / Bewegungsmakros"
    assert mode.physical.pan_max == 540
    assert mode.physical.tilt_max == 180


def test_tmh_channel_presets(tmh):
    pan = tmh.channels["Pan Kopf 1"]
    assert pan.group is ChannelGroup.PAN and not pan.fine
    pan_fine = tmh.channels["Pan Fein Kopf 1"]
    assert pan_fine.group is ChannelGroup.PAN and pan_fine.fine
    assert tmh.channels["Dimmer Kopf 1"].preset == "IntensityDimmer"
    assert tmh.channels["Pan/Tilt Speed Kopf 1"].group is ChannelGroup.SPEED


def test_tmh_capabilities(tmh):
    wheel = tmh.channels["Farbrad Kopf 1"]
    assert wheel.group is ChannelGroup.COLOUR
    assert len(wheel.capabilities) == 16
    red = wheel.capabilities[1]
    assert (red.min, red.max, red.name, red.preset, red.color1) == (
        10, 19, "Rot", "ColorMacro", "#ff0000")
    double = wheel.capability_for(85)
    assert double.name == "Lila + Orange"
    assert (double.color1, double.color2) == ("#8000ff", "#ff8000")
    assert wheel.capability_for(200).preset is None
    strobe = tmh.channels["Strobe Kopf 1"]
    assert strobe.group is ChannelGroup.SHUTTER
    assert strobe.capabilities[0].preset == "ShutterOpen"
    reset = tmh.channels["Makro-Speed / Mikrofon / Reset"]
    assert reset.group is ChannelGroup.MAINTENANCE
    assert reset.capability_for(240).preset == "ResetAll"


def test_library_laser(data_dir):
    d = parse_fixture_def(
        (data_dir / "library/Laserworld/Laserworld-EL-400RGB-MK2.qxf").read_bytes())
    assert d.type == "Laser"
    mode = d.mode("9 Channel")
    assert [c.name for c in mode.channels][:2] == ["Style", "Pattern selection"]
    assert mode.channels[0].group is ChannelGroup.EFFECT
    assert mode.channels[2].group is ChannelGroup.PAN  # PositionXAxis
    assert mode.channels[6].group is ChannelGroup.BEAM
    assert mode.channels[7].group is ChannelGroup.COLOUR
    assert mode.heads == []


def test_old_format_and_edge_cases():
    xml = """<?xml version="1.0"?>
    <FixtureDefinition xmlns="http://www.qlcplus.org/FixtureDefinition">
     <Manufacturer>Acme</Manufacturer><Model>Old</Model><Type>Color Changer</Type>
     <Channel Name="Red" Default="12"><Group Byte="0">Intensity</Group><Colour>Red</Colour></Channel>
     <Channel Name="Red fine"><Group Byte="1">Intensity</Group><Colour>Red</Colour></Channel>
     <Channel Name="Wheel"><Group Byte="0">Colour</Group>
      <Capability Min="20" Max="39" Color="#ff00ff00">Green</Capability>
      <Capability Min="0" Max="19" Res="gobo.svg">Open</Capability>
     </Channel>
     <Channel Name="Odd" Preset="NotAPreset"><Group Byte="0">Bogus</Group></Channel>
     <Mode Name="A"><Channel Number="1">Wheel</Channel><Channel Number="0">Red</Channel></Mode>
    </FixtureDefinition>"""
    d = parse_fixture_def(xml)
    red = d.channels["Red"]
    assert red.colour == 0xFF0000 and red.default == 12
    assert d.channels["Red fine"].fine
    wheel = d.channels["Wheel"]
    assert [c.name for c in wheel.capabilities] == ["Open", "Green"]  # sorted
    assert wheel.capabilities[1].color1 == "#00ff00"  # ARGB -> RGB
    assert wheel.capabilities[0].resource == "gobo.svg"
    assert d.channels["Odd"].group is ChannelGroup.NOTHING
    assert d.channels["Odd"].preset is None
    assert [c.name for c in d.mode("A").channels] == ["Red", "Wheel"]  # by Number


def test_invalid_files():
    with pytest.raises(QLCParseError, match="invalid XML"):
        parse_fixture_def("<FixtureDefinition>")
    with pytest.raises(QLCParseError, match="expected <FixtureDefinition>"):
        parse_fixture_def("<Workspace/>")
    with pytest.raises(QLCParseError, match="unknown channel 'Nope'"):
        parse_fixture_def(
            "<FixtureDefinition><Mode Name='m'><Channel Number='0'>Nope</Channel>"
            "</Mode></FixtureDefinition>")


def test_generic_definitions():
    d = generic_dimmer_def(3)
    mode = d.mode("3 Channel")
    assert len(mode.channels) == 3 and mode.heads == [[0], [1], [2]]
    p = generic_rgb_panel_def(12, "GRB 16bit")
    mode = p.mode("GRB 16bit")
    assert len(mode.channels) == 12 and len(mode.heads) == 2
    assert [c.name for c in mode.channels[:3]] == ["Green 1", "Green Fine 1", "Red 1"]
    assert mode.channels[1].fine
