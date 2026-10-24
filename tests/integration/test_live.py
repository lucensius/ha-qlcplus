"""Live test against a real QLC+ with the test project loaded.

Skipped unless QLC_LIVE_HOST is set, e.g.:

    QLC_LIVE_HOST=192.168.8.76 pytest tests/integration/test_live.py -s

Safety: never switches on the laser (channels 39-47) or the fog machine
(channel 48), never touches the Grand Master. Every channel and function it
changes is restored; a before/after snapshot of all patched channels must match.
"""

from __future__ import annotations

import asyncio
import os

import pytest
import pytest_socket
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, CONF_PORT, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.qlcplus.const import ALL_GROUPS, CONF_GROUPS, CONF_STORAGE_ID, DOMAIN
from custom_components.qlcplus.qlc.client import QLCClient

from .conftest import write_entry_files

HOST = os.environ.get("QLC_LIVE_HOST")
pytestmark = pytest.mark.skipif(not HOST, reason="QLC_LIVE_HOST not set")

LASER = range(39, 48)
FOG = 48
BAR_AUS = 0  # sets dimmers 9/18/27/36 and macro 37 to 0
STROBE_HEAD1 = 8  # "Offen (kein Strobe)" is 0-10


async def snapshot(client: QLCClient) -> tuple[list[tuple[int, int, bool]], set[int]]:
    values = await client.get_channels(1, 1, 48)
    functions = await client.get_functions()
    states = await asyncio.gather(*(client.get_function_status(f) for f in functions))
    running = {f for f, s in zip(functions, states, strict=True) if s}
    return [(v.address, v.value, v.overridden) for v in values], running


async def wait_for(hass, predicate, timeout=10.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met")
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()


async def test_live(hass: HomeAssistant, library_cache) -> None:
    pytest_socket.socket_allow_hosts(["127.0.0.1", HOST])
    probe = QLCClient(HOST)
    await probe.start()
    assert await probe.wait_connected(5)
    before = await snapshot(probe)
    assert all(v == 0 for a, v, _ in before[0] if a in LASER or a == FOG), "laser/fog not off"
    dimmers = {a: v for a, v, _ in before[0] if a in (9, 18, 27, 36, 37)}
    if any(dimmers.values()) or before[0][STROBE_HEAD1 - 1][1:] != (0, False) or BAR_AUS in before[1]:
        await probe.close()
        pytest.skip(f"rig not in the expected idle state: {dimmers}")

    entry = MockConfigEntry(
        domain=DOMAIN, title="QLC+ Live", unique_id=f"{HOST}:9999",
        data={CONF_HOST: HOST, CONF_PORT: 9999, CONF_STORAGE_ID: "live"},
        options={CONF_GROUPS: ALL_GROUPS, "poll_interval": 1})
    write_entry_files(hass, "live")
    entry.add_to_hass(hass)
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data.coordinator
        assert coordinator.data.version == "4.14.4"
        assert entry.runtime_data.loaded.errors == {}
        print("\nentities:", len(hass.states.async_entity_ids()),
              "lights:", len(hass.states.async_entity_ids("light")))
        assert hass.states.get("binary_sensor.qlc_live_connection").state == STATE_ON

        # 1. harmless function: on/off via the switch, state comes from pushes
        switch = "switch.qlc_live_bar_aus"
        await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: switch}, blocking=True)
        await wait_for(hass, lambda: hass.states.get(switch).state == STATE_ON)
        await hass.services.async_call("switch", "turn_off", {ATTR_ENTITY_ID: switch}, blocking=True)
        await wait_for(hass, lambda: hass.states.get(switch).state == STATE_OFF)

        # 2. Simple Desk value inside the "open" range, then release it
        strobe = "number.tmh_bar_s120_kopf_1_strobe"
        await hass.services.async_call("number", "set_value",
                                       {ATTR_ENTITY_ID: strobe, "value": 5}, blocking=True)
        [ch] = await probe.get_channels(1, STROBE_HEAD1)
        assert (ch.value, ch.overridden) == (5, True)
        await wait_for(hass, lambda: hass.states.get(strobe).state == "5")
        await hass.services.async_call("qlcplus", "release_channel",
                                       {"address": STROBE_HEAD1}, blocking=True)
        [ch] = await probe.get_channels(1, STROBE_HEAD1)
        assert not ch.overridden

        # 3. laser lock: nothing may be sent
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                "select", "select_option",
                {ATTR_ENTITY_ID: "select.laser_el_400rgb_style", "option": "Sound mode"},
                blocking=True)
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call("qlcplus", "run_function",
                                           {"function": "Laser Musik"}, blocking=True)
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call("qlcplus", "set_channel",
                                           {"address": 39, "value": 75}, blocking=True)
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        await asyncio.sleep(1)
        # clean up even if an assertion failed half way
        was_overridden = {a for a, _, o in before[0] if o}
        current = await probe.get_channels(1, 1, 48)
        for v in current:
            if v.overridden and v.address not in was_overridden:
                await probe.release_channel(v.address)
        if BAR_AUS not in before[1]:
            await probe.set_function(BAR_AUS, False)
        await asyncio.sleep(1)
        after = await snapshot(probe)
        await probe.close()
    changed = [(b, a) for b, a in zip(before[0], after[0], strict=True) if b != a]
    assert changed == [], f"channels changed: {changed}"
    assert after[1] == before[1]
