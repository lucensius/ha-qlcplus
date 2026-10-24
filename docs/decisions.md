# Design decisions

Decisions taken where a reasonable default existed. Newest at the bottom.

## Phase 0 – protocol

1. **Supported QLC+ versions:** 4.13.x and 4.14.x (desktop app with `-w`).
   The test rig runs 4.14.4. QLC+ 5 (`webaccess-qml`) uses the same command
   set but is untested.
2. **No `GET /` from the integration.** Rendering the VC page connects widget
   push signals in QLC+ and duplicates them on every request. Widget states are
   polled instead; only `FUNCTION|…` pushes are relied upon.
3. **Keep-alive:** answer server pings (aiohttp `autoping`), never send own
   heartbeat pings (4.13 does not answer them), reconnect after 20 s of
   silence. QLC+ pings every 5 s.
4. **Reply correlation:** FIFO per reply-command name, because QLC+ has no
   request IDs. `sdResetChannel`/`sdResetUniverse` register as waiters for a
   `getChannelsValues` reply so they do not steal answers meant for real
   `getChannelsValues` requests.
5. **Widget types come from the `.qxw`**, not from `getWidgetType` (translated).
6. **"Release channel" = `sdResetChannel`.** Simple Desk values override all
   functions until released.
7. **Blackout:** if the project has a VC button with action `Blackout`, the
   blackout switch uses it (real QLC+ blackout). Otherwise it sets the Grand
   Master to 0 and restores the previous value; the entity documents that GM
   only affects GM-controlled (intensity) channels.
8. **Project upload to QLC+ (`POST /loadProject`) is not used.** It would
   replace the running project; noted as a possible future option.

## Phase 1 – parsers

9. **The library lives in `custom_components/qlcplus/qlc/`** and uses only
   relative imports, so its tests import it as top-level `qlc` without Home
   Assistant.
10. **Fixture library source:** raw files from the QLC+ GitHub repo pinned to
    tag `QLC+_4.14.4` (matches the tested QLC+ version). `FixturesMap.xml` is
    cached for 30 days. If it can't be refreshed, the stale copy is used.
    Fixture files are cached forever, since one tag never changes.
    Manufacturer = map directory with `_` → space, like QLC+ does it.
11. **Matching** is exact first, then case-insensitive, for manufacturer, model
    and mode. Generic dimmers and generic RGB panels are created in code, like
    QLC+ does it.
12. **Heads:** a mode with more than one head becomes heads; channels outside
    all heads (and every channel of single-head fixtures) form the
    fixture-level channel set. Head labels come from a word sequence shared
    by all channel names of a head ("… Kopf 1"), otherwise "Head N".
13. **Laser safety analysis (conservative):** every channel of a fixture with
    type `Laser` is a laser channel. A function is unsafe if it writes a
    value > 0 to a laser channel, directly or through chasers, sequences,
    collections, shows or scripts. EFX or RGB matrices on a laser, and scripts
    with commands that can't be analysed, also count as unsafe. Widgets are
    unsafe if they start an unsafe function or control laser channels.

## Phase 2 – client

14. **Liveness:** `autoping=False`, the client answers the server's pings
    itself and treats 20 s without any frame as a dead connection.
15. **A request timeout closes the connection.** Without request IDs, a lost
    reply would shift every later reply. Reconnecting resets the queues.
16. **Polling lives in the client** (`configure_polling`): nearby patched
    addresses are merged into a few `getChannelsValues` blocks, and VC widget
    statuses are polled only for enabled widget entities. Default interval:
    5 s. `request_poll()` triggers an immediate poll after writes.
17. **Version detection** uses `GET /simpleDesk` (side-effect free), never `/`.

## Phase 3 – Home Assistant integration

18. **Entity planning is HA-free** (`qlc/planner.py`), so the mapping
    channel → entity is unit-tested without HA. Rules:
    * light per head (or fixture level) with a dimmer preset/intensity
      channel or R+G+B intensity channels. Colour wheel → HS colour mode with
      the nearest single-colour slot (hue-weighted distance on `Res1`),
      gobo/effect capabilities → `effect_list`. Smoke/hazer and laser
      fixtures get no light.
    * select: Colour/Gobo/Effect/Maintenance/Prism channels with ≥ 2
      capabilities. Maintenance entities are disabled by default.
    * number: everything else. Pan/tilt are shown in ° when the mode's physical
      data gives `PanMax`/`TiltMax`, otherwise in %. Intensity numbers are in %,
      all other channels raw DMX 0–255, because capabilities are defined in
      DMX values. 16-bit pairs are combined.
    * functions: switch, single-shot chasers/sequences: button.
    * VC: flash buttons → button (enabled), toggle buttons → switch and
      sliders → number (both disabled by default, polled only when enabled).
      XY pads are not mapped (pan/tilt numbers cover them).
19. **Fixture control goes through the Simple Desk** (`CH|`), so HA values
    override running functions. `qlcplus.release_channel` hands control back.
20. **unique_id** = `<entry_id>_<key>` with keys `fx<id>[_h<head>]_ch<index>`,
    `fx<id>[_h<head>]_light`, `fn<id>`, `vc<id>`. They stay stable when the
    project is uploaded again. Entities/devices that disappear are removed
    from the registries on setup.
21. **Files** live in `<config>/qlcplus/<storage_id>/` (random ID chosen in
    the config flow), the shared library cache in
    `<config>/qlcplus/library_cache/`.
22. **Multiple `.qxf` uploads:** HA's file selector accepts one file, so the
    flow asks for more files only while definitions are missing (loop step).
23. **Laser release** is a config switch that is *off after every start* (not
    restored). Turning it off again releases the Simple Desk values HA set
    on laser channels and stops unsafe functions HA started. For laser
    selects, an option sends the lowest value of its range ("Laser off"
    0–49 → 0). Unresolved fixtures are guarded like lasers.
24. **Flash buttons** are held for a configurable time (default 1 s).
25. **Setup waits** for the connection (10 s, otherwise `ConfigEntryNotReady`)
    and for the first function status sync, so states are known immediately.
