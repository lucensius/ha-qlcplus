#!/usr/bin/env python3
"""Probe a running QLC+ web interface over its WebSocket API.

Lists functions and Virtual Console widgets, optionally toggles one scene
on and off, optionally runs a Simple Desk override test on one channel,
and logs every incoming message.

Examples:
    python tools/qlc_probe.py 192.168.8.76
    python tools/qlc_probe.py 192.168.8.76 --scene 21 --channel 6 --deny 39-48

Safety: nothing is started or written unless --scene / --channel is given.
Channels in --deny are never written, and scenes are not started when the
optional project file shows that they touch a denied channel.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import time
import xml.etree.ElementTree as ET

import aiohttp

NS = "{http://www.qlcplus.org/Workspace}"


class Probe:
    def __init__(self, host: str, port: int, log_all: bool) -> None:
        self.url = f"ws://{host}:{port}/qlcplusWS"
        self.log_all = log_all
        self.t0 = time.monotonic()
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.pending: dict[str, list[asyncio.Future[list[str]]]] = {}
        self.pushes: list[str] = []

    def log(self, direction: str, text: str) -> None:
        print(f"[{time.monotonic() - self.t0:7.3f}] {direction} {text}")

    async def reader(self) -> None:
        assert self.ws is not None
        async for msg in self.ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                parts = msg.data.split("|")
                if parts[0] == "QLC+API" and len(parts) > 1:
                    waiters = self.pending.get(parts[1])
                    if waiters:
                        fut = waiters.pop(0)
                        if not fut.done():
                            fut.set_result(parts[2:])
                        if self.log_all:
                            self.log("<<", msg.data[:300])
                        continue
                self.pushes.append(msg.data)
                self.log("<< PUSH", msg.data)
            elif msg.type in (aiohttp.WSMsgType.PING, aiohttp.WSMsgType.PONG):
                self.log("<<", msg.type.name)
            else:
                self.log("<<", f"{msg.type.name} {msg.data!r}")

    async def send(self, text: str) -> None:
        assert self.ws is not None
        self.log(">>", text)
        await self.ws.send_str(text)

    async def api(self, cmd: str, *args: object, reply: str | None = None,
                  timeout: float = 3.0) -> list[str] | None:
        fut: asyncio.Future[list[str]] = asyncio.get_running_loop().create_future()
        self.pending.setdefault(reply or cmd, []).append(fut)
        await self.send("|".join(["QLC+API", cmd, *map(str, args)]))
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            self.log("!!", f"timeout waiting for {cmd}")
            return None

    async def channel(self, address: int) -> tuple[int, bool]:
        """Return (value, overridden) for a 1-based absolute address."""
        uni = (address - 1) // 512 + 1
        local = (address - 1) % 512 + 1
        res = await self.api("getChannelsValues", uni, local, 1)
        if not res or len(res) < 4:
            raise RuntimeError(f"bad getChannelsValues reply: {res}")
        return int(res[1]), res[3] == "1"


def pairs(fields: list[str]) -> list[tuple[str, str]]:
    return [(fields[i], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]


def parse_ranges(spec: str) -> set[int]:
    out: set[int] = set()
    for part in filter(None, spec.split(",")):
        lo, _, hi = part.partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


def scene_addresses(qxw: str, function_id: int) -> set[int]:
    """1-based absolute addresses a scene writes, read from the project."""
    root = ET.parse(qxw).getroot()
    fixtures = {}
    for fx in root.iter(NS + "Fixture"):
        fid = fx.findtext(NS + "ID")
        if fid is not None and fx.findtext(NS + "Address") is not None:
            fixtures[fid] = int(fx.findtext(NS + "Universe")) * 512 + int(
                fx.findtext(NS + "Address"))
    for fn in root.iter(NS + "Function"):
        if fn.get("ID") == str(function_id):
            addrs = set()
            for fv in fn.iter(NS + "FixtureVal"):
                vals = (fv.text or "").split(",")
                base = fixtures[fv.get("ID")]
                addrs.update(base + int(ch) + 1 for ch in vals[0::2] if ch)
            return addrs
    raise SystemExit(f"function {function_id} not found in {qxw}")


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("host")
    p.add_argument("--port", type=int, default=9999)
    p.add_argument("--scene", type=int, help="function ID to toggle on and off")
    p.add_argument("--channel", type=int,
                   help="1-based absolute address for the Simple Desk override test")
    p.add_argument("--deny", default="", help="addresses never written, e.g. 39-48")
    p.add_argument("--project", help=".qxw used to check what --scene touches")
    p.add_argument("--details", action="store_true",
                   help="query type of every function and widget")
    p.add_argument("--listen", type=float, default=6.0,
                   help="seconds to keep listening at the end")
    p.add_argument("-v", "--verbose", action="store_true", help="log API replies too")
    args = p.parse_args()
    deny = parse_ranges(args.deny)

    if args.channel and args.channel in deny:
        raise SystemExit(f"channel {args.channel} is in the deny list")
    if args.scene is not None and args.project:
        touched = scene_addresses(args.project, args.scene)
        if touched & deny:
            raise SystemExit(f"scene {args.scene} writes denied channels "
                             f"{sorted(touched & deny)}")

    probe = Probe(args.host, args.port, args.verbose)
    async with aiohttp.ClientSession() as session:
        # autoping stays on: aiohttp answers the server's 5 s pings itself
        async with session.ws_connect(probe.url, autoping=True) as ws:
            probe.ws = ws
            reader = asyncio.create_task(probe.reader())

            print("\n== Functions ==")
            funcs = pairs(await probe.api("getFunctionsList") or [])
            running_before = set()
            for fid, name in funcs:
                line = f"  {fid:>4}  {name}"
                if args.details:
                    ftype = await probe.api("getFunctionType", fid)
                    status = await probe.api("getFunctionStatus", fid)
                    line += f"  [{ftype and ftype[0]}] {status and status[0]}"
                    if status and status[0] == "Running":
                        running_before.add(fid)
                print(line)

            print("\n== Widgets ==")
            widgets = pairs(await probe.api("getWidgetsList") or [])
            for wid, caption in widgets:
                line = f"  {wid:>4}  {caption!r}"
                if args.details:
                    wtype = await probe.api("getWidgetType", wid)
                    wstat = await probe.api("getWidgetStatus", wid)
                    line += f"  {wtype}  status={wstat}"
                print(line)

            print("\n== Misc ==")
            print("  isProjectLoaded:", await probe.api("isProjectLoaded"))
            print("  getFunctionsNumber:", await probe.api("getFunctionsNumber"))
            print("  getWidgetsNumber:", await probe.api("getWidgetsNumber"))
            print("  getWidgetFunction 9:", await probe.api("getWidgetFunction", 9))
            print("  getFunctionStatus 99999:", await probe.api("getFunctionStatus", 99999))
            print("  getWidgetStatus 99999:", await probe.api("getWidgetStatus", 99999))

            if args.scene is not None:
                await scene_test(probe, args.scene)
            if args.channel:
                await channel_test(probe, args.channel, args.scene)

            print(f"\n== Listening {args.listen:.0f} s for pushes ==")
            await asyncio.sleep(args.listen)
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader


async def scene_test(probe: Probe, fid: int) -> None:
    print(f"\n== Scene test: function {fid} ==")
    status = await probe.api("getFunctionStatus", fid)
    print("  status before:", status)
    if status and status[0] == "Running":
        print("  already running, leaving it alone")
        return
    await probe.send(f"QLC+API|setFunctionStatus|{fid}|1")
    await asyncio.sleep(1.0)
    print("  status while on:", await probe.api("getFunctionStatus", fid))
    await probe.send(f"QLC+API|setFunctionStatus|{fid}|0")
    await asyncio.sleep(1.0)
    print("  status after:", await probe.api("getFunctionStatus", fid))


async def channel_test(probe: Probe, address: int, fid: int | None) -> None:
    """Show how a Simple Desk override interacts with a running function."""
    print(f"\n== Simple Desk test: address {address} ==")
    value0, override0 = await probe.channel(address)
    print(f"  initial: value={value0} override={override0}")
    test_value = 15 if value0 != 15 else 25
    try:
        if fid is not None:
            await probe.send(f"QLC+API|setFunctionStatus|{fid}|1")
            await asyncio.sleep(0.5)
            print("  with function running:", await probe.channel(address))
        await probe.send(f"CH|{address}|{test_value}")
        await asyncio.sleep(0.5)
        print(f"  after CH={test_value}:", await probe.channel(address))
        if fid is not None:
            await probe.send(f"QLC+API|setFunctionStatus|{fid}|0")
            await asyncio.sleep(0.5)
            print("  after function stop (override kept?):", await probe.channel(address))
            await probe.send(f"QLC+API|setFunctionStatus|{fid}|1")
            await asyncio.sleep(0.5)
            print("  function restarted under override:", await probe.channel(address))
        # sdResetChannel answers with a getChannelsValues page of the desk
        await probe.api("sdResetChannel", address, reply="getChannelsValues")
        await asyncio.sleep(0.5)
        print("  after sdResetChannel:", await probe.channel(address))
    finally:
        if fid is not None:
            await probe.send(f"QLC+API|setFunctionStatus|{fid}|0")
        if override0:
            await probe.send(f"CH|{address}|{value0}")
        else:
            await probe.api("sdResetChannel", address, reply="getChannelsValues")
        await asyncio.sleep(0.5)
        print("  restored:", await probe.channel(address))


if __name__ == "__main__":
    asyncio.run(main())
