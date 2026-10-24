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
