# Testing the display over WebSocket — wscat / websocat and friends

A hack-day runbook for poking the display protocol (`wal.sh/tools/display`, cited
in [`PROTOCOL.md` §5.4](PROTOCOL.md)) by hand. It covers three targets — the
**local mock relay**, the **hack-day simulator / live `wal.sh` page**, and
**hardware** — and the tools for each. Every recipe below was run against the
local mock relay on loopback.

> **Guardrail (agents).** An automated agent MUST NOT open a socket to the live
> relay `wss://wal.sh/tools/display/ws` from the jail — that publishes to the
> live site ([`PROTOCOL.md` §5.4](PROTOCOL.md), §9.2 jail-loopback caveat). Agents
> test against the local mock relay only. A human at the hack drives the
> simulator / live page themselves. The recipes are written so the same commands
> work against either by swapping `$URL`.

## TL;DR — which tool when

| Tool | Installed as | Best for | Binary `pal16`? | Scriptable? |
|---|---|---|---|---|
| **wscat** | `~/.npm-global/bin/wscat` | interactive REPL: type JSON ops, watch replies | no (text/hex only) | no — **interactive TTY only**, silent when piped |
| **websocat** | `/usr/local/bin/websocat` | one-shot / scripted control-plane + hex; handshake debugging (`-v`) | from a file, if payload has no `0x0A` | yes |
| **python `websockets`** | system `python3` (v16.0) | full source/viewer, binary `pal16`, `rgb24`, precise timing | yes | yes |
| **repo demo** (`python -m demo …`) | `contrib/displays/demo/` | reserve + stream a real demo (`bars/matrix/fishbowl/tetris`), a viewer, the mock relay | yes | yes |
| **Go `display-tui`** | `impl/go/cmd/display-tui` | offline: simulate the sink + drive it, no network at all | n/a (in-process) | n/a |

Rule of thumb: **wscat/websocat for the control plane** (`view`, `reserve`,
`renew`, `release`, error probing, reading `caps`), **python / the demo source
for frames** (especially binary `pal16`), **Go TUI when you have no relay**.

## The wire, in one screen

Endpoint: `.../tools/display/ws`. Client's messages are JSON control ops (text)
or frames; the relay answers with JSON status and fans frames out to viewers.

- **viewer:** `{"op":"view","display":D}` → `caps` (display, w, h, fps, format,
  16 hex colors) then `lease` (holder, expires), then every frame the holder
  sends, **in `caps.format`**.
- **source:** `{"op":"reserve","name":N,"display":D,"ttl":T,"format":"pal16"|"hex"|"rgb24"}`
  → `granted` (lease id, w, h, fps, format, palette, expires) or `busy`.
  A frame or `{"op":"renew"}` renews; `{"op":"release"}` or closing ends it.
- **frames:** `pal16` = `w*h` bytes of indices 0–15 (153 for green-building),
  optional 2-byte big-endian sequence prefix; `hex` = `h` lines of `w` hex
  digits as **one** LF-terminated text message; `rgb24` = `w*h*3` bytes (relay
  quantizes).
- **errors:** `unknown-op`, `not-holder`, `bad-frame-length`, `bad-format`,
  `rate`. One holder per display; `ttl ≤ 900 s`; ≤ 32 viewers.

**Fan-out format is the display's, not the source's.** A viewer of
green-building always receives `pal16` binary (153 bytes), even when the holder
sent `hex` or `rgb24`. Verified: a hex source's frame arrived at the viewer as a
153-byte binary message.

## Setup — the local mock relay (dev / CI target)

```sh
cd contrib/displays
# system python3 has the websockets lib; a venv is not required
python3 -m demo relay --host 127.0.0.1 --port 8765 --record /tmp/relay.jsonl
# -> mock relay ws://127.0.0.1:8765/tools/display/ws  (capabilities: http://127.0.0.1:8765/tools/display/capabilities.json)
```

```sh
export URL=ws://127.0.0.1:8765/tools/display/ws
```

It serves all 12 presets (green-building is this repo's default), one lease each,
on loopback. `--record FILE` logs every message in/out as JSONL (see *Verify a
session* below). Options: `--default D`, `--udp-port 2323` (BLP/MCUF), `--fps N`,
`--page display.html`, `--extra-display NAME=WxH`.

## Recipes (verified against the mock relay)

### websocat — viewer handshake (one-shot)

```sh
printf '{"op":"view","display":"green-building"}\n' | websocat -t "$URL"
```
```json
{"op": "caps", "display": "green-building", "w": 9, "h": 17, "fps": 30, "format": "pal16", "palette": ["#000000", ... , "#FFFFFF"]}
{"op": "lease", "display": "green-building", "holder": null, "expires": null}
```

### websocat — reserve (one-shot)

```sh
printf '{"op":"reserve","name":"me","display":"green-building","ttl":60,"format":"hex"}\n' | websocat -t "$URL"
# -> {"op": "granted", "lease": "3d0de4…", "w": 9, "h": 17, "fps": 30, "format": "hex", "palette": [...], "expires": 1789297114}
```
(The lease is released when this short-lived connection closes. To *hold* it,
keep the connection open — use wscat interactively or python.)

### wscat — interactive REPL (the human tool)

```sh
wscat -c "$URL"
> {"op":"view","display":"green-building"}
< {"op":"caps", ...}
< {"op":"lease", "holder":null, ...}
```
Type each JSON op at the `>` prompt; replies print with `<`. **wscat prints
nothing when its stdin is piped** — it is a TTY REPL, not a scripting tool. For
scripts use websocat or python.

### websocat — handshake / debugging

```sh
websocat -v "$URL"                       # log the HTTP upgrade and frames
```
Note: the python `websockets` client rejects the relay's upgrade with **HTTP 400**
if you pass `subprotocols=[]` (an empty `Sec-WebSocket-Protocol`). Omit the arg
entirely; websocat and wscat send no subprotocol and connect fine.

### python — hold a lease and send a `pal16` frame (binary)

The robust way to send binary frames. Keeps the socket open so the lease lives.

```python
import asyncio, json, websockets
URL = "ws://127.0.0.1:8765/tools/display/ws"
async def main():
    async with websockets.connect(URL) as ws:          # no subprotocols= arg
        await ws.send(json.dumps({"op":"reserve","name":"src",
                                  "display":"green-building","ttl":60,"format":"pal16"}))
        print(await ws.recv())                          # granted
        frame = bytes((i % 16) for i in range(9*17))    # 153 bytes, indices 0..15
        await ws.send(frame)                            # binary message
        await asyncio.sleep(1)
        await ws.send(json.dumps({"op":"renew"}))       # frames also renew
asyncio.run(main())
```

### python — a `hex` frame is one multi-line text message

`hex` frames contain embedded `\n`, so they must be sent as a single message
(websocat's line-splitting would break them):

```python
hexframe = "\n".join("012345678" for _ in range(17)) + "\n"   # 17 rows × 9 digits
await ws.send(hexframe)
```

### Error probing

```sh
printf '{"op":"frobnicate"}\n'                         | websocat -t "$URL"   # {"op":"error","reason":"unknown-op"}
# from a reserved hex source, a wrong-size frame:      -> {"op":"error","reason":"bad-frame-length"}
# a frame before reserving:                            -> {"op":"error","reason":"not-holder"}
```

### Verify a recorded session

The mock relay's `--record FILE` JSONL is checkable against the contract:

```sh
cd contrib/displays
python3 -m contract.check_session /tmp/relay.jsonl
```

## Other options (beyond wscat/websocat)

- **The repo's own demo trio** — the fastest way to see light move:
  ```sh
  cd contrib/displays
  python3 -m demo list                                 # 12 presets + demos
  python3 -m demo run tetris -d green-building --seconds 0   # relay+source+viewer, Ctrl-C to stop
  python3 -m demo source tetris -d green-building --format pal16   # source only, against a running relay
  python3 -m demo view -d green-building                          # a viewer/sink in your terminal
  python3 -m demo source fishbowl -d green-building --format rgb24 # rgb24 path (relay quantizes)
  ```
- **Go `display-tui`** — no relay, no network; simulates the sink *and* drives
  it, and streams the ported MITris engine as the `tetris` demo:
  ```sh
  cd impl/go && go run ./cmd/display-tui -d green-building -demo tetris
  ```
  See [`impl/go/README.md`](../impl/go/README.md). Use this when the relay/simulator
  isn't up yet, or to develop a frame producer offline.
- **Browser page (same-page API).** On the `wal.sh` page,
  `window.display.send(frame)` (a `Uint8Array` of indices) or
  `postMessage({frame})` from an opener drives the sink directly — no socket.
  Handy for a quick front-end prototype.
- **BLP / MCUF over UDP.** With `--udp-port 2323`, the relay accepts
  Blinkenlights-family packets; the display is chosen by `w×h`. For interop with
  existing facade tooling.

## Driving the *game* (17×9 SPEC engine) onto the display

The display protocol and the game protocol are separate ([`PROTOCOL.md` §5.4](PROTOCOL.md)).
A **bridge** is a game *viewer* on one side and a display *source* on the other:
it reads SPEC frames and re-emits them as `pal16`. See
[`contrib/emacs/display-relay-bridge.py`](../contrib/emacs/display-relay-bridge.py)
and the demo `source.py`. An embodied-AI behaviour is just another frame producer
(the event note calls this out): anything that yields a 17×9 frame at the
display's fps and holds the lease can drive the facade.

## Targets: web / simulator / hardware

| Target | `$URL` | Who runs it | Notes |
|---|---|---|---|
| **Local mock relay** | `ws://127.0.0.1:8765/tools/display/ws` | agents, CI, dev | this repo; loopback only |
| **Hack-day simulator** | *confirm on the day* | humans at the hack | the "Demos with simulator" at 20:00; ask for its ws endpoint / how frames are fed |
| **Live `wal.sh` page** | `wss://wal.sh/tools/display/ws`, page `https://wal.sh/tools/display/?d=green-building` | **humans only** | never from an agent/jail |
| **Remote sink → hardware** | preset `remote` + `?d=remote&src=ws://host:port`, or BLP/MCUF UDP `:2323` | humans, on-site | the `remote` preset is green-building geometry pointed at a real sink |

## Hack-day checklist

1. Relay up: `python3 -m demo relay --port 8765 --record session.jsonl`; grab `$URL`.
2. Sanity: `printf '{"op":"view","display":"green-building"}\n' | websocat -t "$URL"` → `caps` + `lease`.
3. Hold a lease and push a test pattern (python `pal16`, or `python -m demo source bars -d green-building`).
4. Watch it: `python3 -m demo view -d green-building`, or the browser page.
5. **Confirm the simulator's real interface** (from the event note's open
   questions): is it a `Display` subclass or this ws protocol? what fps / brightness
   caps for the live install? what sensor feeds (camera / mic / footfall)?
6. Check your recorded session: `python3 -m contract.check_session session.jsonl`.

## References

- Display protocol: [`PROTOCOL.md` §5.4](PROTOCOL.md); pinned spec + capabilities
  in `contrib/displays/contract/wal-sh-display-0.2.1/`.
- Mock relay / source / viewer: [`contrib/displays/demo/README.md`](../contrib/displays/demo/README.md).
- Contract kit (schemas, fixtures, conformance): `contrib/displays/contract/`.
- Event brief: [`docs/events/2026-09-13-sundai-hack-140.md`](events/2026-09-13-sundai-hack-140.md).
