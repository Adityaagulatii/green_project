# Remote display and control protocol

**Status: draft, proposed for inclusion at the next spec seal.** Protocol version **0**.

This document defines how a program talks to a 17×9 Tetris engine or display over the network: to play the SPEC engine from another process (for example Emacs), or to feed frames from any producer to a display (a terminal, the HTML recorder, or the building).

It is *derived*: [`SPEC.md`](../SPEC.md) stays the one canonical document. The action names (§5.1), the frame and color contract (§2), the palette (§4.1), conformance mode (§9) and the frame digest (§9.4) are SPEC's. If this page and SPEC.md disagree, SPEC.md wins. At the next seal, this draft is proposed as a new SPEC section, and its version becomes 1.

Implementations:

| Side | File |
|---|---|
| codec (pure) | `impl/python/sim/tetris_sim/protocol.py` |
| server, both modes | `impl/python/sim/tetris_sim/server.py` (`python -m tetris_sim.server`) |
| Emacs client | `contrib/emacs/tetris-mit.el` |

## 1. Transport and framing

- **1.1** TCP. One client per connection.
- **1.2** Each message is one JSON object (RFC 8259), encoded as UTF-8, on **one line** terminated by LF (`0x0A`). A CR before the LF is tolerated. Empty lines are ignored. A final fragment without an LF is discarded when the connection closes.
- **1.3** **Maximum message size: 65536 bytes**, LF included. A receiver MUST NOT buffer more than that while looking for an LF. An oversized message is answered with error `too_large`, and the connection is closed.
- **1.4** Every message has a string field `type`. **Unknown fields are ignored**, so later versions can add optional fields. An unknown `type` is answered with error `unknown_type`, and the connection continues.
- **1.5** Parsing is strict:
  - `NaN`, `Infinity` and duplicate object keys are rejected as `malformed`;
  - booleans are not integers;
  - numbers that must be integers MUST be JSON integers (`1.0` is rejected).

## 2. Handshake and versioning

The client's **first message MUST be `hello`**. Anything else is answered with `hello_required`, and the connection is closed.

Client hello:

```json
{"type":"hello","protocol":"17x9-tetris-remote","version":0,"role":"controller","client":"tetris-mit.el"}
```

- `protocol` MUST be `"17x9-tetris-remote"`, and `version` MUST equal the server's version. Otherwise the server answers `version` and closes.
- `role` is one of:

  | role | mode | may send | receives |
  |---|---|---|---|
  | `controller` | engine | `event`, `tick` (lockstep only), `ping` | `frame`, `state`, `error`, `pong` |
  | `viewer` | engine or display | `ping` | `frame`, `state`, `error`, `pong` |
  | `producer` | display | `frame`, `state`, `ping` | `error`, `pong` |

  A role that the server's mode does not accept is refused with `role`. At most one controller (engine) or one producer (display) is connected at a time; a second one is refused with `busy`. Both errors close the connection.
- `client` is optional free text, and informative only.
- `seed` is optional, and only a controller in engine mode uses it. It is the PRNG seed of the session this hello starts (SPEC §9.3), an integer in `0 ≤ seed < 2^32`, and it defaults to the server's `--seed`. Test drivers use it to replay a trace: `tetris-mit-kav` replays the known-answer vectors, KAV-NN, from `spec/conformance/traces/NN-*.json`. A bad seed is refused with `bad_hello`, and the connection is closed.

The server answers with its own hello:

```json
{"type":"hello","protocol":"17x9-tetris-remote","version":0,"role":"server","mode":"engine",
 "spec_version":1,"rows":17,"cols":9,"fps":30,"max_message":65536,"seed":42,"clock":"realtime"}
```

`seed` and `clock` appear in engine mode only; a controller's `seed` is that of its session. `spec_version` names the SPEC whose actions, palette and digest apply.

**Versioning.** `version` is one integer.
- Adding optional fields or new message types does **not** change it.
- Changing or removing anything a v0 peer relies on **does** change it.
- v0 is this draft. It becomes v1 when it is sealed into SPEC.

**Lifecycle.** This section uses the taxonomy of the state-machine ladder: `aygp-dr/state-machine-ladder`, `spec.org` at tag `spec-v2.4.0`, sha256 `88654eb635fd4d04785aef999046c7182297d091188be9013e5c67e2c9ee51fc`, cited read-only.
- **A connection is a chain.** Setting aside the self-loop of a joined connection handling its messages, its states are entered in order, and none is re-entered. A new session needs a new connection.

  | from | on | to |
  |---|---|---|
  | `open` | a valid `hello`: the role is accepted in this mode and its slot is free | `joined(role)` |
  | `open` | any other message | `closed`, after `hello_required`, `version`, `role`, `bad_hello` or `busy` |
  | `joined(role)` | a message permitted for the role, mode and clock (§3) | `joined(role)` |
  | `joined(role)` | a message not permitted | `joined(role)`, after `forbidden`; the message has no effect |
  | `joined(role)` | a fatal error, EOF, or server shutdown | `closed` |

- **The server's slot is a cycle.** The controller slot of an engine server, and the producer slot of a display server, go `free → taken` on the controller's (or producer's) hello, and `taken → free` when it disconnects. This repeats for ever.
- **The game phases are a cycle, and SPEC defines them.** The phases inside a session are `countdown`, `playing`, `clearing` and `gameover`. Their legal transitions are defined by SPEC (§9.1, §9.2, and the phase-legality table that SPEC v2 adds), not by this protocol, so they are not repeated here.

## 3. Messages

| type | direction | fields |
|---|---|---|
| `hello` | both | see §2 |
| `event` | controller → server | `action`: one of `left`, `right`, `soft_drop`, `hard_drop`, `rotate_cw`, `rotate_ccw`, `rotate_180`, `hold` (SPEC §5.1); `down`: boolean (`true` = press, `false` = release) |
| `tick` | controller → server | `frames`: integer 1…3600. Lockstep clock only (§4.2). |
| `frame` | server → clients; producer → display server | `frame_no`: integer ≥ 0; `rows`: 17 arrays of 9 `[r, g, b]` arrays, row-major, row 0 at the top; `digest`: SPEC §9.4 digest, lowercase hex SHA-256 of the 459 R,G,B bytes |
| `state` | server → clients; producer → display server | `score`, `level`, `lines`: integers ≥ 0 (required). Optional: `high_score` (integer ≥ 0), `phase` (string, ≤ 32 characters), `frame_no` (integer ≥ 0) |
| `ping` / `pong` | either way | optional `id` (integer, or string ≤ 64 characters). `pong` echoes the `id`. A peer can use it as a barrier, since messages are processed in order. |
| `error` | server → client | `code` (§6), `message` (free text) |

**`state` is a view.** A `state` message, `phase` included, is derived from the engine state after a step. It is never authoritative. Clients MUST NOT feed it back as input, and the engine server refuses a `state` from a controller with `forbidden`. Conformance checks the frame digests, not `state`. In engine mode, `phase` is exactly one of SPEC's phase names (§9.1): `countdown`, `playing`, `clearing`, `gameover`. In display mode, a producer's `state` is that producer's own view, and the server uses it only for the HUD.

**Frame validation.** Every receiver MUST check that:
- there are exactly 17 rows of exactly 9 cells;
- each cell is an array of exactly 3 JSON integers in `0..255`.

Out-of-range values are **rejected, not clamped**: SPEC §2.2 clamping belongs to the producer's `Color` constructor, before encoding, so that every frame on the wire has exactly one digest.

A producer MAY omit `digest`, in which case the display server computes it. If `digest` is present, it MUST equal the digest of `rows`, or the frame is rejected with `digest`. The engine server always sends `digest`.

## 4. Engine mode

The server runs the SPEC-faithful engine (`tetris_engine`) and streams its frames.

- **4.1 Sessions are folds over their log.** A controller's hello starts a **session**, which is a fresh `init(seed)`: the seed is the hello's `seed`, or else the server's. The session ends when the controller disconnects.

  A session is a **fold over its log**. The log is the session seed plus the controller's events, in order, each stamped with the frame whose step applied it. The frames are `S_0 = init(seed)`, `S_{k+1} = step(S_k, E_k)` and `frame k = render(S_{k+1})` (SPEC §9.1). **The same log always gives the same frames and digests** (SPEC §9), because the server keeps nothing else that could affect them.
  - Under the lockstep clock, the frame stamps follow from the order of `event` and `tick` messages alone.
  - Under the realtime clock, they depend on arrival, and the server records them (§4.3).

  KAV replay (`tetris-mit-kav`) depends on exactly this property.

  Frame `frame_no = k` is SPEC frame k, that is `render(S_{k+1})` (§9.1), so the boot countdown is frames 0–89. Each frame is sent to the controller and to every viewer. A `state` message (score, level, lines, high score, phase and `frame_no`) is sent with the first frame and whenever one of those values changes.
- **4.2 Clocks.** The server's `--clock` option chooses one of two clocks.
  - **`realtime`** (the default). The server steps once every 1/30 s, which is SPEC §2.3's rate. The events received since the previous step form `E_k` for the next step, in arrival order. Which frame an event lands in depends on the network, as it depended on the OS in the legacy (SPEC §13).
  - **`lockstep`**. The engine advances only on `tick`. The events received since the last tick form `E_k` for the first frame of that tick, and the remaining frames of the tick get no events. This clock is **deterministic**: the same seed and the same message sequence produce exactly the frames, and the digests, of a direct engine run.
- **4.3 Conformance is preserved.** The server logs every applied event as `[frame, action, down]`. A session is therefore a SPEC §12 trace. `--trace-out` writes it, and any conformant engine can replay it to the same digests. The Python test suite checks both clocks against a direct run, and the lockstep clock against the sealed traces. The Emacs client does the same from the other end of the wire: `tetris-mit-kav` sends a trace's seed and events, recomputes every received frame's digest, and prints `KAV-NN: n/n digests match — PASS`.
- **4.4 Key releases.** The engine is driven by presses *and* releases (latches, SPEC §5.4). A client that cannot observe key releases, as in a terminal or Emacs, SHOULD send a release right after each press. Auto-repeat is the client's business (SPEC §14).

## 5. Display mode

The server accepts frames from one producer and renders them to its sinks:
- ANSI truecolor to stdout;
- the self-contained HTML recorder (`--html out.html`, written at shutdown);
- a legacy `Display` adapter, `--display module:attr`: any object with `send(frame)` and `makeframe()` (SPEC §2.3). This includes `utilities.display.Display` subclasses and whatever the building exposes.

Accepted frames are also relayed to viewers. The producer's latest `state`, if any, is used as the sinks' HUD and relayed too.

- **Validation.** §3, plus a strictly increasing `frame_no` per producer. Gaps are allowed, and mean dropped frames. A rejected frame is answered with an error and is not shown. The connection continues.
- **Pacing.** A frame is shown from the next frame start, and the server never shows more than 30 frames per second (SPEC §2.3). It does not read the producer's next message before the next 1/30 s slot, so TCP back-pressure slows a fast producer instead of dropping its frames.

## 6. Errors

`{"type":"error","code":"bad_frame","message":"cell (3, 4) is not ..."}`

**Validation order.** Every message passes two gates, and a message rejected by either one has no effect.
1. **The schema gate** is decoding and field validation. Its errors are `too_large`, `malformed`, `unknown_type`, `version`, `bad_hello`, `role` (an unknown role name), `bad_event`, `bad_tick`, `bad_frame`, `bad_state`, `bad_ping` and `digest`.
2. **The state gate** comes second. It checks that the message is a legal transition for the connection's state (§2, Lifecycle). Its errors are `hello_required`, `role` (a role not accepted in this mode), `busy`, `forbidden`, and `bad_frame` for a `frame_no` that does not increase.

| code | fatal | meaning |
|---|---|---|
| `malformed` | no | not UTF-8 or not JSON; not an object; `type` missing or not a string; NaN/Infinity; duplicate keys |
| `too_large` | **yes** | message over 65536 bytes |
| `unknown_type` | no | `type` not in §3 |
| `hello_required` | **yes** | first message was not `hello` |
| `version` | **yes** | wrong `protocol` or `version` |
| `role` | **yes** | role missing, unknown, or not accepted in this mode |
| `bad_hello` | **yes** | a hello field failed validation (for example `seed` out of range) |
| `busy` | **yes** | controller/producer slot taken, or too many connections |
| `forbidden` | no | message type not allowed for this role, mode or clock |
| `bad_event` / `bad_tick` / `bad_frame` / `bad_state` / `bad_ping` | no | field validation failed |
| `digest` | no | `digest` does not match `rows` |
| `too_many_errors` | **yes** | 16 errors on one connection |

After a non-fatal error the offending message is discarded and has no effect. After a fatal error, the server sends the error message and then closes the connection.

## 7. Security (v0)

- **Loopback by default.** The server binds `127.0.0.1` unless `--host` says otherwise, and warns on stderr when the address is not loopback. Tests never bind anything else.
- **Jails.** A FreeBSD jail whose `lo0` carries the jail's own address, such as `10.0.0.22/32`, maps a bind to `127.0.0.1` onto that address. The server prints the address it actually got, and says when the kernel mapped it. Such an address is still on the loopback interface, but other jails on the same host may be able to reach it.
- **No authentication and no encryption in v0.** Anyone who can reach the port can play the game or paint the display, and on Sep 29 that display is a building. To reach a server from another machine, keep it on loopback and tunnel, for example `ssh -L 1709:127.0.0.1:1709 host`. Adding authentication is an open question for the seal.
- **Bounded input.**
  - Messages are capped at 64 KiB.
  - At most 8 connections are served, and a 9th is refused with `busy`.
  - A connection is closed after 16 errors.
  - A client whose unread output passes 1 MiB is disconnected.
- **Malformed input is rejected, never interpreted.** Parsing is strict JSON, and nothing is evaluated. Every field is type- and range-checked before use. The server's `--display` option imports a module named by the **operator** on the command line, never by a client.

## 8. Example

A lockstep session: `>` is client to server, `<` is server to client, and frames are abbreviated.

```
> {"type":"hello","protocol":"17x9-tetris-remote","version":0,"role":"controller"}
< {"type":"hello","protocol":"17x9-tetris-remote","version":0,"role":"server","mode":"engine","spec_version":1,...,"seed":7,"clock":"lockstep"}
> {"type":"tick","frames":91}
< {"type":"frame","frame_no":0,"rows":[[[0,0,0],...]],"digest":"..."}
< {"type":"state","score":0,"level":0,"lines":0,"high_score":0,"phase":"countdown","frame_no":0}
  ... frames 1-90 ...
> {"type":"event","action":"hard_drop","down":true}
> {"type":"event","action":"hard_drop","down":false}
> {"type":"tick","frames":1}
< {"type":"frame","frame_no":91,...}
> {"type":"event","action":"jump","down":true}
< {"type":"error","code":"bad_event","message":"unknown action 'jump'"}
```

## 9. Open questions for the seal

- Authentication (a shared token in `hello`?) before any non-loopback deployment.
- Several controllers (co-op or versus), and hand-over between them.
- Whether the `state` message should also carry the full SPEC §12 observation (active piece, hold).
- The building's row→floor and column→bay mapping (SPEC §10.4) is still TBD. The protocol carries display coordinates only.
