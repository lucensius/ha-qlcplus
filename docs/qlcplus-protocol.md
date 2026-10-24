# QLC+ Web Access protocol

Derived from the QLC+ sources (`webaccess/src/webaccess.cpp`,
`webaccessbase.cpp`, `qhttpserver/qhttpconnection.cpp`,
`ui/src/simpledesk*.cpp`) at tags `QLC+_4.13.1`, `QLC+_4.14.4` and `master`
(Oct 2026), and verified live against QLC+ **4.14.4** started with `-w`
(`tools/qlc_probe.py`). This file is the reference for
`custom_components/qlcplus/qlc/client.py`.

## Transport

| Item | Value |
|---|---|
| Endpoint | `ws://<host>:9999/qlcplusWS` (port set with `qlcplus -w -wp <port>`) |
| Framing | text frames, UTF-8, fields separated by `\|` |
| Server keep-alive | server sends a WebSocket **ping every 5 s** on every connection |
| Auth | optional HTTP Basic auth (`qlcplus -wa`); the WS upgrade request carries it |
| Version | not exposed over WS; `GET /simpleDesk` contains `<div class="swInfo">Q Light Controller Plus 4.14.4</div>` |

Version differences in the WS layer:

* **4.13.x** uses a hand-written frame parser: it never answers client pings
  (pong is not sent), reads the 16-bit length with signed chars and does not
  reassemble TCP-split frames. Keep client messages short (< 126 bytes) and do
  not rely on client-initiated pings. Server→client frames ≥ 64 KiB are broken.
* **4.14.x / 5.x** use `QWebSocket` and are RFC 6455 compliant.

Client strategy: keep `autoping` on (answers the server pings), do not send our
own heartbeat pings, and treat "nothing received for ~20 s" (four missed server
pings) as a dead connection.

There are **no request IDs**. Replies to `QLC+API` requests are sent only to
the requesting connection, in request order, prefixed with
`QLC+API|<command>|`. Correlate replies FIFO per reply-command name. Several
requests return *nothing* (see table) and must be fire-and-forget.

## API requests (`QLC+API|…`)

Addresses are 1-based in all requests.

| Request | Reply | Notes |
|---|---|---|
| `QLC+API\|isProjectLoaded` | `…\|true` / `false` | `true` only once after a project was loaded via web; not a "has project" flag |
| `QLC+API\|getFunctionsNumber` | `…\|<n>` | |
| `QLC+API\|getFunctionsList` | `…\|<id>\|<name>\|<id>\|<name>…` | names may contain any char except `\|` |
| `QLC+API\|getFunctionType\|<fid>` | `…\|Scene` | type strings are **not** translated: `Scene`, `Chaser`, `EFX`, `Collection`, `Script`, `RGBMatrix`, `Show`, `Sequence`, `Audio`, `Video`, `Undefined` |
| `QLC+API\|getFunctionStatus\|<fid>` | `…\|Running` / `Stopped` / `Undefined` | `Undefined` = no such function |
| `QLC+API\|setFunctionStatus\|<fid>\|<0\|1>` | **none** | starts/stops with the master as parent; a `FUNCTION\|…` push follows |
| `QLC+API\|getWidgetsNumber` | `…\|<n>` | all widgets below the VC root frame (root not counted) |
| `QLC+API\|getWidgetsList` | `…\|<id>\|<caption>…` | |
| `QLC+API\|getWidgetType\|<wid>` | `…\|<wid>\|<type>` | type is **translated to the UI language** (`Knopf`, `Schieberegler`, `Rahmen`, …) → take widget types from the `.qxw` |
| `QLC+API\|getWidgetFunction\|<wid>` | `…\|<wid>\|<fid>\|<type>\|<name>` | 4.14+ only. Bug: a widget linked to function **0** reports `0\|Undefined\|` |
| `QLC+API\|getWidgetStatus\|<wid>` | 4.14+: `…\|<wid>\|<value>`; 4.13: `…\|<value>` | button: `255` on, `127` monitoring, `0` off; slider: 0–255; cue list: `PLAY\|<idx>\|` or `STOP`; unknown widget: `…\|` (empty) |
| `QLC+API\|getWidgetSubIdList\|<wid>` | `…\|<id>\|<name>…` | XY pad presets / animation controls; **no reply** for unknown widget |
| `QLC+API\|getChannelsValues\|<uni>\|<start>[\|<count>]` | `…\|<addr>\|<value>\|<type>\|<override>…` (4 fields per channel) | `uni` 1-based, `start` 1-based inside the universe, `addr` in the reply is 1-based inside the universe. `type` = channel group number (`0.#RRGGBB` for intensity with colour, empty if no fixture), `override` = `1` if a Simple Desk value is active |
| `QLC+API\|sdResetChannel\|<abs>` | `QLC+API\|getChannelsValues\|…` | `abs` = 1-based absolute address. **Replies with a getChannelsValues page of the Simple Desk's current page**, not with its own name |
| `QLC+API\|sdResetUniverse\|<uni>` | `QLC+API\|getChannelsValues\|…` | 4.14+: `uni` 1-based; 4.13 ignores the argument and resets the universe currently selected in the Simple Desk GUI |

Group numbers (`QLCChannel::Group`): 0 Intensity, 1 Colour, 2 Gobo, 3 Speed,
4 Pan, 5 Tilt, 6 Shutter, 7 Prism, 8 Beam, 9 Effect, 10 Maintenance,
11 Nothing.

## Direct commands (no reply)

| Command | Effect |
|---|---|
| `CH\|<abs>\|<value>` | Simple Desk value. `abs` = `universe_index * 512 + address_index + 1` (both 0-based, i.e. 1-based absolute address). |
| `GM_VALUE\|<0-255>` | Grand Master. In 4.13.x the handler falls through to the widget handler and **also sends `<value>` to VC widget ID 0** (if it exists). |
| `<wid>\|<value>` | VC widget: button `255` = press, `0` = release (toggle buttons toggle on press, flash buttons flash while pressed); slider: set value 0–255; audio triggers: enable/disable |
| `<wid>\|PLAY` / `STOP` / `PREV` / `NEXT` / `STEP\|<i>` | cue list |
| `<wid>\|NEXT_PG` / `PREV_PG` / `FRAME_DISABLE\|<0\|1>` | frames |
| `<wid>\|S` / `R` | clock start-pause / reset |
| `<wid>\|MATRIX_SLIDER_CHANGE\|<v>`, `MATRIX_COMBO_CHANGE\|<name>`, `MATRIX_COLOR_CHANGE\|COLOR_n\|<rgb>`, `MATRIX_KNOB\|<id>\|<v>`, `MATRIX_PUSHBUTTON\|<id>` | animation widget |
| `POLL` | ignored (no-op, safe keep-alive) |
| `QLC+CMD\|opMode` | toggles design/operate mode – **never send** |
| `QLC+IO\|…`, `QLC+AUTH\|…`, `QLC+SYS\|…` | I/O patching, users, OS network – not used |

There is **no blackout command** over WS. Options: press a VC button whose
action is `Blackout` (true QLC+ blackout, toggles), or set the Grand Master to 0
(only affects channels the GM is configured for – by default intensity
channels only, so e.g. a laser mode channel is *not* dimmed).

## Push messages (server → all clients)

| Message | When | Always sent? |
|---|---|---|
| `FUNCTION\|<fid>\|Running` / `Stopped` | any function start/stop (master timer) | **yes** |
| `<wid>\|BUTTON\|<255\|127\|0>` | VC button state change | only after the VC page (`GET /`) was rendered once* |
| `<wid>\|SLIDER\|<value>\|<display>` | VC slider value change | only after `GET /`* |
| `<wid>\|BUTTON_DISABLE\|…`, `SLIDER_DISABLE`, `FRAME\|<page>`, `FRAME_DISABLE`, `CUE\|<idx>`, `CUE_…`, `CLOCK\|…`, `AUDIOTRIGGERS\|…`, `MATRIX_…` | widget changes | only after `GET /`* |
| `GM_VALUE\|<value>\|<display>` | grand master change | only after `GET /`* |
| `ALERT\|<text>` | errors in auth commands | – |

\* The Qt signal connections for widget pushes are created while generating
the VC HTML page, and are created **again on every `GET /`** (duplicated
pushes). The integration therefore never requests `/`; widget and channel
states are polled, function states come from `FUNCTION` pushes.

## Simple Desk vs. running functions (verified live)

Test on address 6 (colour wheel of head 1) with scene 21 setting it to 5:

| Step | value | override |
|---|---|---|
| scene running | 5 | 0 |
| `CH\|6\|15` | 15 | 1 |
| scene stopped | 15 | 1 |
| scene restarted | 15 | 1 |
| `sdResetChannel\|6` (scene still running) | 5 | 0 |
| scene stopped, `sdResetChannel\|6` | 0 | 0 |

* A Simple Desk value has the `Override` flag: it **wins over every running
  function** (HTP and LTP) until it is released.
* Release a channel with `QLC+API|sdResetChannel|<abs>`. Afterwards running
  functions take over again immediately.
* Releasing sets the universe value to the fixture channel's **default value**
  if no function writes it; LTP residue from functions that ran before is lost.
* LTP channels keep their last value after a scene stops (seen as value 5 with
  the scene stopped).
* `getChannelsValues` shows the effective output value (pre Grand Master) and
  whether an override is active – this is what entity states use.

## Project / fixture upload over HTTP

* `POST /loadProject` (multipart, field `qlcprj`) **replaces the running
  project** in QLC+. Possible optional feature ("push project to QLC+"), but it
  changes the loaded project, so it is not used automatically. Requires admin
  level if auth is enabled.
* `POST /loadFixture` (multipart `.qxf`/`.d4`) stores a fixture definition in
  QLC+'s user fixture folder.
* There is no endpoint to *download* the loaded project, which is why the user
  uploads the `.qxw` to Home Assistant.

## Consistency checks used by the integration

* Function IDs + names: `getFunctionsList` vs. `<Function ID Name>` in the `.qxw`.
* Widget IDs + captions: `getWidgetsList` vs. VC widgets in the `.qxw`.
* Fixture layout cannot be read over WS; `getChannelsValues` group numbers can
  be compared with the parsed fixture definitions as a sanity check.
