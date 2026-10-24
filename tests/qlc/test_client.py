"""Tests for the WebSocket client against the fake QLC+ server."""

import asyncio

import pytest
from fake_qlc import FakeQLC
from qlc.client import (
    ChannelsEvent,
    FunctionEvent,
    QLCClient,
    QLCConnectionError,
    WidgetEvent,
    channel_blocks,
    parse_push,
)


@pytest.fixture
async def server():
    srv = FakeQLC(ping_interval=0.2)
    srv.functions = {0: ("Bar Aus", "Scene"), 21: ("K1 Farbe Weiß", "Scene"),
                     19: ("Nebelstoß 5 s", "Chaser")}
    srv.function_values = {21: {6: 5}}
    srv.widgets = {9: ("Aus", "0"), 2: ("Dimmer", "0")}
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
async def client(server):
    c = QLCClient(server.host, server.port, request_timeout=1.0, idle_timeout=1.0,
                  backoff_max=0.2)
    await c.start()
    assert await c.wait_connected(3)
    yield c
    await c.close()


async def wait_for(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def test_parse_push():
    assert parse_push("FUNCTION|21|Running") == FunctionEvent(21, True)
    assert parse_push("FUNCTION|21|Stopped") == FunctionEvent(21, False)
    w = parse_push("5|SLIDER|128|50%")
    assert isinstance(w, WidgetEvent) and (w.widget_id, w.kind, w.value, w.extra) == (
        5, "SLIDER", "128", ("50%",))
    assert parse_push("GM_VALUE|200|78%").value == 200
    assert parse_push("ALERT|oops").text == "ALERT|oops"


def test_channel_blocks():
    assert channel_blocks([6, 1, 2, 3, 48, 40]) == [(1, 1, 6), (1, 40, 9)]
    assert channel_blocks([512, 513]) == [(1, 512, 1), (2, 1, 1)]
    assert channel_blocks([]) == []


async def test_lists_and_status(client):
    assert await client.get_functions() == {0: "Bar Aus", 21: "K1 Farbe Weiß", 19: "Nebelstoß 5 s"}
    assert await client.get_widgets() == {9: "Aus", 2: "Dimmer"}
    assert await client.get_function_type(19) == "Chaser"
    assert await client.get_function_status(21) is False
    assert await client.get_function_status(999) is None
    assert await client.get_widget_status(9) == ["0"]
    assert await client.get_widget_status(999) is None
    assert await client.get_version() == "4.14.4"


async def test_concurrent_requests_are_matched_in_order(client):
    results = await asyncio.gather(*(client.get_function_status(f) for f in (0, 21, 999, 19)))
    assert results == [False, False, None, False]


async def test_function_push_reaches_subscribers(client, server):
    events = []
    unsub = client.subscribe(events.append)
    await client.set_function(21, True)
    await wait_for(lambda: FunctionEvent(21, True) in events)
    assert await client.get_function_status(21) is True
    await client.set_function(21, False)
    await wait_for(lambda: FunctionEvent(21, False) in events)
    unsub()
    await client.set_function(21, True)
    await asyncio.sleep(0.1)
    assert events.count(FunctionEvent(21, True)) == 1


async def test_channels_override_and_release(client, server):
    server.universe[5] = 3
    [ch] = await client.get_channels(1, 6)
    assert (ch.address, ch.value, ch.overridden) == (6, 3, False)
    await client.set_channel(6, 15)
    # sdResetChannel's reply is a getChannelsValues page: it must not be taken
    # as the answer to a concurrent getChannelsValues request
    read, _ = await asyncio.gather(client.get_channels(1, 6), client.release_channel(6))
    assert read[0].value == 15 and read[0].overridden
    [after] = await client.get_channels(1, 6)
    assert after.value == 3 and not after.overridden
    assert "CH|6|15" in server.received
    assert "QLC+API|sdResetChannel|6" in server.received


async def test_set_channel_clamps_and_validates(client, server):
    await client.set_channel(10, 300)
    await client.get_functions()  # round trip so the server has processed CH
    assert server.overrides[10] == 255
    with pytest.raises(ValueError):
        await client.set_channel(0, 1)


async def test_widget_and_grand_master(client, server):
    await client.set_widget(9, 255)
    await client.set_grand_master(0)
    await client.get_functions()
    assert server.widgets[9] == ("Aus", "255")
    assert server.grand_master == 0


async def test_release_universe(client, server):
    await client.set_channel(3, 1)
    await client.set_channel(600, 1)
    await client.release_universe(1)
    assert 3 not in server.overrides and 600 in server.overrides


async def test_reconnect_after_server_drop(client, server):
    states = []
    client.subscribe_connection(states.append)
    await server.drop_clients()
    await wait_for(lambda: states[:1] == [False])
    assert await client.wait_connected(3)
    assert states == [False, True]
    assert server.connections == 2
    assert await client.get_function_status(0) is False


async def test_requests_fail_fast_when_disconnected(server):
    c = QLCClient(server.host, server.port)
    with pytest.raises(QLCConnectionError):
        await c.get_functions()
    await c.close()


async def test_lost_reply_forces_reconnect(client, server):
    server.silent.add("getFunctionsList")
    with pytest.raises(QLCConnectionError, match="Timeout"):
        await client.get_functions()
    server.silent.clear()
    assert await client.wait_connected(3)
    assert await client.get_functions()  # queues are in sync again
    assert server.connections == 2


async def test_idle_connection_is_dropped(server):
    server.ping_interval = 0  # silent server
    c = QLCClient(server.host, server.port, idle_timeout=0.3, backoff_max=0.1)
    states = []
    c.subscribe_connection(states.append)
    await c.start()
    await wait_for(lambda: states.count(False) >= 1, timeout=3)
    await c.close()


async def test_server_pings_keep_connection_alive(server):
    c = QLCClient(server.host, server.port, idle_timeout=0.5)
    await c.start()
    assert await c.wait_connected(3)
    await asyncio.sleep(1.2)  # > idle timeout, but pings every 0.2 s
    assert c.connected and server.connections == 1
    await c.close()


async def test_connect_failure():
    c = QLCClient("127.0.0.1", 1, request_timeout=0.5)
    with pytest.raises(QLCConnectionError):
        await c.connect()
    await c.close()


async def test_polling_emits_events(client, server):
    server.universe[47] = 0
    server.widgets[2] = ("Dimmer", "77")
    events = []
    client.subscribe(events.append)
    client.configure_polling(0.1, channel_addresses=[6, 48], widget_ids=[2])
    await wait_for(lambda: any(isinstance(e, ChannelsEvent) for e in events))
    channels = next(e for e in events if isinstance(e, ChannelsEvent))
    assert [v.address for v in channels.values] == [6, 48]
    await wait_for(lambda: WidgetEvent(2, "STATUS", "77") in events)
    reads = sum(m.startswith("QLC+API|getChannelsValues") for m in server.received)
    client.configure_polling(0)
    await asyncio.sleep(0.3)
    assert sum(m.startswith("QLC+API|getChannelsValues") for m in server.received) <= reads + 1
