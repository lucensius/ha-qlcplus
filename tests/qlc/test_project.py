"""Tests for the .qxw parser."""

import pytest
from qlc.project import FunctionType, RunOrder, SceneValue, WidgetType, parse_project
from qlc.xmlutil import QLCParseError


@pytest.fixture
def project(data_dir):
    return parse_project((data_dir / "Lichtsteuerung.qxw").read_bytes())


def test_header_and_fixtures(project):
    assert project.creator_version == "4.13.1"
    assert project.universes == {0: "Universe 1"}
    assert set(project.fixtures) == {0, 1, 2}
    laser = project.fixtures[1]
    assert (laser.manufacturer, laser.model, laser.mode) == (
        "Laserworld", "EL-400RGB MK2", "9 Channel")
    assert (laser.universe, laser.address, laser.channels) == (0, 38, 9)
    assert laser.absolute_address == 38
    assert project.fixtures[2].name == "Nebel AF-150"


def test_functions(project):
    assert len(project.functions) == 93
    scene = project.functions[21]
    assert scene.type is FunctionType.SCENE and scene.name == "K1 Farbe Weiß"
    assert scene.values == [SceneValue(0, 5, 5)]
    assert project.functions[13].values == [SceneValue(1, 0, 75)]
    chaser = project.functions[19]
    assert chaser.type is FunctionType.CHASER
    assert chaser.run_order is RunOrder.SINGLE_SHOT and chaser.is_single_shot
    assert chaser.duration == 5000
    assert [(s.function, s.hold) for s in chaser.steps] == [(17, 5000)]


def test_widgets_tree(project):
    w = project.widgets
    assert 144 not in w  # VC contents frame is not a widget for QLC+
    assert len(w) == 143  # matches getWidgetsNumber on the live system
    assert w[22].type is WidgetType.FRAME and w[22].parent is None
    btn = w[9]
    assert btn.type is WidgetType.BUTTON
    assert (btn.function, btn.action) == (0, "Toggle")
    assert btn.parent == 21 and btn.solo_frame == 21
    assert btn.path == ["TMH Bar S120 – alle Köpfe", "Szenen"]
    assert w[138].action == "Flash" and w[138].function == 17
    assert w[138].solo_frame is None
    slider = w[2]
    assert slider.type is WidgetType.SLIDER and slider.slider_mode == "Level"
    assert slider.level_channels == [(0, 8), (0, 17), (0, 26), (0, 35)]
    assert w[1].type is WidgetType.XYPAD
    assert w[1].xy_fixtures == [(0, 0), (0, 1), (0, 2), (0, 3)]
    assert project.blackout_widgets() == []


EDGE = """<?xml version="1.0" encoding="UTF-8"?>
<Workspace xmlns="http://www.qlcplus.org/Workspace">
 <Engine>
  <InputOutputMap><Universe Name="Front" ID="0"/><Universe Name="Back" ID="1"/></InputOutputMap>
  <Fixture><Manufacturer>Generic</Manufacturer><Model>Generic</Model><Mode>Generic</Mode>
   <ID>5</ID><Name>Dimmers</Name><Universe>1</Universe><Address>10</Address><Channels>4</Channels></Fixture>
  <ChannelsGroup ID="0" Name="All" Value="0">5,0,5,1</ChannelsGroup>
  <FixtureGroup ID="3"><Name>Grid</Name><Size X="2" Y="1"/>
   <Head X="0" Y="0" Fixture="5">0</Head><Head X="1" Y="0" Fixture="5">1</Head></FixtureGroup>
  <Function ID="1" Type="Scene" Name="Grp" Hidden="True">
   <ChannelGroupsVal>0,200</ChannelGroupsVal>
   <Value Fixture="5" Channel="3">7</Value>
  </Function>
  <Function ID="2" Type="Sequence" Name="Seq" BoundScene="1">
   <Speed FadeIn="0" FadeOut="0" Duration="0"/><Direction>Backward</Direction><RunOrder>PingPong</RunOrder>
   <Step Number="0" FadeIn="10" Hold="20" FadeOut="30" Values="2">5:0,255,1,128</Step>
  </Function>
  <Function ID="3" Type="Collection" Name="Col"><Step Number="0">1</Step><Step Number="1">2</Step></Function>
  <Function ID="4" Type="Show" Name="Show"><Track ID="0" Name="T"><ShowFunction ID="3" StartTime="0"/></Track></Function>
  <Function ID="5" Type="EFX" Name="Circle"><Fixture><ID>5</ID><Head>1</Head></Fixture></Function>
  <Function ID="6" Type="RGBMatrix" Name="Mtx"><FixtureGroup>3</FixtureGroup></Function>
  <Function ID="7" Type="Script" Name="Scr">
   <Command>setfixture%3A5%20ch%3A2%20val%3A99</Command>
   <Command>startfunction%3A3</Command>
   <Command>wait%3A1s</Command>
  </Function>
  <Function ID="8" Type="Script" Name="Weird"><Command>random%3A1</Command></Function>
  <Function ID="9" Type="Unknownish" Name="?"/>
 </Engine>
 <VirtualConsole>
  <Frame Caption=""><Appearance/>
   <Frame Caption="Outer" ID="10">
    <Frame Caption="" ID="11">
     <Button Caption="BO" ID="12"><Action>Blackout</Action></Button>
     <Button Caption="None" ID="13"><Function ID="4294967295"/><Action>Toggle</Action></Button>
    </Frame>
    <Slider Caption="PB" ID="14" WidgetStyle="Knob"><SliderMode>Playback</SliderMode>
     <Playback><Function>3</Function></Playback></Slider>
    <CueList Caption="Cues" ID="15"><Chaser>2</Chaser></CueList>
    <Label Caption="Hello" ID="16"/>
   </Frame>
  </Frame>
 </VirtualConsole>
</Workspace>"""


def test_edge_cases():
    p = parse_project(EDGE)
    assert p.universes == {0: "Front", 1: "Back"}
    fx = p.fixtures[5]
    assert fx.is_generic and fx.absolute_address == 522
    f = p.functions
    assert f[1].hidden
    assert set(f[1].values) == {SceneValue(5, 0, 200), SceneValue(5, 1, 200), SceneValue(5, 3, 7)}
    seq = f[2]
    assert seq.type is FunctionType.SEQUENCE and seq.run_order is RunOrder.PING_PONG
    assert seq.direction == "Backward" and seq.bound_scene == 1
    assert seq.steps[0].function == 1
    assert seq.steps[0].values == [SceneValue(5, 0, 255), SceneValue(5, 1, 128)]
    assert (seq.steps[0].fade_in, seq.steps[0].hold, seq.steps[0].fade_out) == (10, 20, 30)
    assert f[3].children == [1, 2]
    assert f[4].children == [3]
    assert f[5].efx_fixtures == [(5, 1)]
    assert f[6].fixture_group == 3
    assert p.fixture_groups[3].heads == [(5, 0), (5, 1)]
    assert f[7].script_values == [SceneValue(5, 2, 99)] and f[7].children == [3]
    assert not f[7].script_unparsed
    assert f[8].script_unparsed
    assert f[9].type is FunctionType.UNDEFINED

    w = p.widgets
    assert w[12].path == ["Outer"]  # empty captions are not part of the path
    assert w[12].action == "Blackout"
    assert p.blackout_widgets() == [w[12]]
    assert w[13].function is None  # invalid function id
    assert w[14].widget_style == "Knob" and w[14].playback_function == 3
    assert w[15].type is WidgetType.CUE_LIST and w[15].chaser == 2
    assert w[16].type is WidgetType.LABEL


def test_not_a_workspace():
    with pytest.raises(QLCParseError):
        parse_project("<FixtureDefinition/>")
