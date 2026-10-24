"""Entity creation, state updates, safety and services."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.qlcplus.const import DOMAIN
from custom_components.qlcplus.diagnostics import async_get_config_entry_diagnostics


async def wait_for(hass, predicate, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met")
        await asyncio.sleep(0.05)
        await hass.async_block_till_done()


async def test_devices_and_entities(hass: HomeAssistant, loaded_entry, caplog) -> None:
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert "via_device" not in caplog.text
    devices = dr.async_get(hass)
    hub = devices.async_get_device({(DOMAIN, loaded_entry.entry_id)})
    assert hub.sw_version is None or hub.name == "QLC+ Lichtsteuerung"
    head = devices.async_get_device({(DOMAIN, f"{loaded_entry.entry_id}_fx0_h2")})
    bar = devices.async_get_device({(DOMAIN, f"{loaded_entry.entry_id}_fx0")})
    assert head.name == "TMH Bar S120 Kopf 3" and head.via_device_id == bar.id
    assert bar.via_device_id == hub.id

    states = hass.states
    assert states.get("light.tmh_bar_s120_kopf_1") is not None
    assert states.get("select.tmh_bar_s120_kopf_1_farbrad") is not None
    assert states.get("number.tmh_bar_s120_kopf_1_pan").attributes["unit_of_measurement"] == "°"
    assert states.get("select.laser_el_400rgb_style").attributes["laser"] is True
    assert states.get("number.nebel_af_150_pump") is not None
    assert states.get("switch.qlc_lichtsteuerung_bar_rot") is not None
    assert states.get("button.qlc_lichtsteuerung_nebelstoss_5_s") is not None
    assert states.get("switch.qlc_lichtsteuerung_blackout") is not None
    assert states.get("switch.qlc_lichtsteuerung_laser_release").state == STATE_OFF
    assert states.get("binary_sensor.qlc_lichtsteuerung_connection").state == STATE_ON
    # VC toggles and faders are disabled by default, flash buttons are not
    reg = er.async_get(hass)
    vc_toggle = reg.async_get_entity_id("switch", DOMAIN, f"{loaded_entry.entry_id}_vc9")
    assert reg.async_get(vc_toggle).disabled_by is not None
    vc_flash = reg.async_get_entity_id("button", DOMAIN, f"{loaded_entry.entry_id}_vc138")
    assert reg.async_get(vc_flash).disabled_by is None


async def test_function_switch_and_push(hass: HomeAssistant, loaded_entry, qlc_server) -> None:
    entity = "switch.qlc_lichtsteuerung_bar_rot"
    assert hass.states.get(entity).state == STATE_OFF
    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: entity}, blocking=True)
    await wait_for(hass, lambda: hass.states.get(entity).state == STATE_ON)
    assert 3 in qlc_server.running
    # change made in QLC+ itself arrives as a push
    await qlc_server.set_running(3, False)
    await wait_for(hass, lambda: hass.states.get(entity).state == STATE_OFF)


async def test_light_and_polling(hass: HomeAssistant, loaded_entry, qlc_server) -> None:
    light = "light.tmh_bar_s120_kopf_2"
    await wait_for(hass, lambda: hass.states.get(light).state == STATE_OFF)
    await hass.services.async_call(
        "light", "turn_on",
        {ATTR_ENTITY_ID: light, "brightness": 128, "hs_color": (0, 100), "effect": "Gobo 3"},
        blocking=True)
    await wait_for(hass, lambda: qlc_server.overrides.get(18) == 128)
    await wait_for(hass, lambda: qlc_server.overrides.get(15) == 14)
    await wait_for(hass, lambda: qlc_server.overrides.get(16) == 34)
    state = hass.states.get(light)
    assert state.state == STATE_ON and state.attributes["brightness"] == 128
    assert state.attributes["effect"] == "Gobo 3"
    assert state.attributes["hs_color"] == (0.0, 100.0)

    # a value changed elsewhere is picked up by polling
    qlc_server.overrides[18] = 0
    await wait_for(hass, lambda: hass.states.get(light).state == STATE_OFF)

    await hass.services.async_call("qlcplus", "release_channel", {"address": [18, 15, 16]},
                                   blocking=True)
    await wait_for(hass, lambda: not {15, 16, 18} & qlc_server.overrides.keys())


async def test_number_16bit_and_select(hass: HomeAssistant, loaded_entry, qlc_server) -> None:
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: "number.tmh_bar_s120_kopf_1_pan", "value": 270},
        blocking=True)
    await wait_for(hass, lambda: (qlc_server.overrides.get(1), qlc_server.overrides.get(2))
                   == (128, 0))  # 270/540 of 65535 = 32767.5 -> 32768
    await hass.services.async_call(
        "select", "select_option",
        {ATTR_ENTITY_ID: "select.tmh_bar_s120_kopf_1_farbrad", "option": "Lila + Orange"},
        blocking=True)
    await wait_for(hass, lambda: qlc_server.overrides.get(6) == 84)
    assert hass.states.get("select.tmh_bar_s120_kopf_1_farbrad").state == "Lila + Orange"


async def test_laser_lock(hass: HomeAssistant, loaded_entry, qlc_server) -> None:
    style = "select.laser_el_400rgb_style"
    with pytest.raises(ServiceValidationError, match="laser"):
        await hass.services.async_call(
            "select", "select_option", {ATTR_ENTITY_ID: style, "option": "Sound mode"},
            blocking=True)
    # "Laser off" is allowed: lasers use the lowest value of the range
    await hass.services.async_call(
        "select", "select_option", {ATTR_ENTITY_ID: style, "option": "Laser off"}, blocking=True)
    await wait_for(hass, lambda: qlc_server.overrides.get(39) == 0)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "switch", "turn_on", {ATTR_ENTITY_ID: "switch.qlc_lichtsteuerung_laser_musik"},
            blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("qlcplus", "set_channel", {"address": 41, "value": 10},
                                       blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("qlcplus", "run_function", {"function": "Laser Auto"},
                                       blocking=True)
    assert 13 not in qlc_server.running and 14 not in qlc_server.running
    # fog is not a laser
    await hass.services.async_call("qlcplus", "set_channel", {"address": 48, "value": 0},
                                   blocking=True)

    # release, use the laser, lock again: HA reverts what it did
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: "switch.qlc_lichtsteuerung_laser_release"},
        blocking=True)
    assert hass.states.get(style).attributes["laser_locked"] is False
    await hass.services.async_call(
        "select", "select_option", {ATTR_ENTITY_ID: style, "option": "Sound mode"}, blocking=True)
    await wait_for(hass, lambda: qlc_server.overrides.get(39) == 50)
    await hass.services.async_call("qlcplus", "run_function", {"function": 14}, blocking=True)
    await wait_for(hass, lambda: 14 in qlc_server.running)
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: "switch.qlc_lichtsteuerung_laser_release"},
        blocking=True)
    await wait_for(hass, lambda: 14 not in qlc_server.running)
    await wait_for(hass, lambda: 39 not in qlc_server.overrides)


async def test_single_shot_button_and_flash(hass: HomeAssistant, loaded_entry, qlc_server) -> None:
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.qlc_lichtsteuerung_nebelstoss_5_s"},
        blocking=True)
    await wait_for(hass, lambda: 19 in qlc_server.running)
    received = len(qlc_server.received)
    entity = next(e for e in hass.states.async_entity_ids("button") if e.endswith("halten"))
    task = hass.async_create_task(hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: entity}, blocking=True))
    await wait_for(hass, lambda: "138|255" in qlc_server.received[received:])
    await task
    await wait_for(hass, lambda: "138|0" in qlc_server.received[received:])
    sent = qlc_server.received[received:]
    assert sent.index("138|255") < sent.index("138|0")


async def test_blackout_via_grand_master(hass: HomeAssistant, loaded_entry, qlc_server) -> None:
    entity = "switch.qlc_lichtsteuerung_blackout"
    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: entity}, blocking=True)
    await wait_for(hass, lambda: qlc_server.grand_master == 0)
    assert hass.states.get(entity).state == STATE_ON
    await hass.services.async_call("qlcplus", "blackout", {"state": False}, blocking=True)
    await wait_for(hass, lambda: qlc_server.grand_master == 255)


async def test_connection_loss(hass: HomeAssistant, loaded_entry, qlc_server) -> None:
    sensor = "binary_sensor.qlc_lichtsteuerung_connection"
    await qlc_server.drop_clients()
    await wait_for(hass, lambda: hass.states.get(sensor).state == STATE_OFF)
    assert hass.states.get("light.tmh_bar_s120_kopf_1").state == STATE_UNAVAILABLE
    await wait_for(hass, lambda: hass.states.get(sensor).state == STATE_ON, timeout=8)


async def test_unknown_function_service(hass: HomeAssistant, loaded_entry) -> None:
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call("qlcplus", "run_function", {"function": "Nope"},
                                       blocking=True)


async def test_diagnostics(hass: HomeAssistant, loaded_entry) -> None:
    diag = await async_get_config_entry_diagnostics(hass, loaded_entry)
    assert diag["qlcplus"]["connected"] is True
    assert diag["project"]["fixtures"][1]["type"] == "Laser"
    assert diag["safety"]["guarded_functions"] == [13, 14, 15, 16]


async def test_setup_retry_when_offline(hass: HomeAssistant, mock_entry, qlc_server) -> None:
    await qlc_server.stop()
    assert not await hass.config_entries.async_setup(mock_entry.entry_id)
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY
