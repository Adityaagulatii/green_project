# Remote display and control protocol: contract v1

**Contract v1** · protocol version **1** · normative · seal record: [`spec/SEALS.md`](../spec/SEALS.md) (contract seals). This contract supersedes draft v0 (commit `790c081`, sha256 of this file `87004bfc59b21e0a3b424d36f66dde85f79717be55e530b1e26159c801868503`). §12 lists the changes.

This document is the **contract**: how a program talks to a 17×9 Tetris engine or display over the network. A client can play the SPEC engine from another process (Emacs, a Hy bot, a browser), or a producer can feed frames to a display (a terminal, the HTML recorder, or the building).

**Authority.**

- [`SPEC.md`](../SPEC.md) stays the one canonical document for the game. The actions (§5.1), the frame and color contract (§2), the palette (§4.1), conformance mode and the phases (§9), and the frame digest (§9.4) are SPEC's. This contract never restates engine behaviour. Where the two disagree about the game, SPEC wins.
- This contract is versioned separately from SPEC, as `contract-vN`, and governs only the wire.
- Its machine-readable parts are in [`spec/protocol/`](../spec/protocol/):
  - `schemas/*.schema.json`: JSON Schema (draft 2020-12) for every message (§3);
  - `transcripts/*.jsonl`: golden client–server exchanges derived from the SPEC traces (§10);
  - `check.py`: the conformance checker;
  - `proxy.py`: a reference gatekeeper and binding bridge (§9.3, §10).

  Those files are derived from this page and from the SPEC traces. Where one disagrees with this page, the derived file is the bug.
- The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are used as in RFC 2119.

| Side | File |
|---|---|
| codec (pure) | `impl/python/sim/tetris_sim/protocol.py` |
| server, both modes | `impl/python/sim/tetris_sim/server.py` (`python -m tetris_sim.server`) |
| Emacs client | `contrib/emacs/tetris-mit.el` |
| Hy client | `impl/hy/tetris_hy/client.hy` |
| conformance | `spec/protocol/check.py`, `spec/protocol/proxy.py`, `spec/protocol/gen_transcripts.py` |

## 1. Messages

- **1.1** A message is one JSON object (RFC 8259), encoded as UTF-8. How messages are delimited on a connection is the binding's business (§5). Everything else in this contract is binding-independent, and a message means the same thing in every binding.
- **1.2 Size.** A message's JSON text is at most **65535 bytes**. The TCP binding's LF terminator makes a line at most 65536 bytes, the `max_message` of the server hello, as in v0. A receiver MUST NOT buffer more than 65536 bytes of one message while it waits for the message to end. A larger message is answered with the fatal error `too_large` (§7).
- **1.3** Every message has a string field `type`.
  - **Unknown fields are ignored**, so that later versions can add optional fields.
  - An unknown `type` is answered with the error `unknown_type`, and the connection continues.
- **1.4 Strict parsing.**
  - `NaN`, `Infinity` and duplicate object keys are rejected as `malformed`.
  - Booleans are not integers.
  - A field that must be an integer MUST be a JSON number without a fraction or an exponent. `1.0` is rejected, with the error code of the message's type (for example `bad_tick`).
- **1.5 Schemas.** Every message type has a JSON Schema in `spec/protocol/schemas/` (§3). JSON Schema's `integer` admits `1.0`, but §1.4 is stricter, and it wins. The checker enforces §1.4.

## 2. Handshake, roles and versions

The client's **first message MUST be `hello`**. Anything else is answered with `hello_required`, and the connection is closed.

```json
{"type":"hello","protocol":"17x9-tetris-remote","version":1,"role":"controller","client":"tetris-mit.el","seed":13}
```

- `protocol` MUST be `"17x9-tetris-remote"`, and `version` MUST equal the server's version. Otherwise the server answers `version` and closes the connection. A v1 server answers a v0 hello with `version`: there is no compatibility mode.
- `role` is one of these:

  | role | mode | may send | receives |
  |---|---|---|---|
  | `controller` | engine | `event`, `tick` (lockstep only), `ping` | `frame`, `state`, `error`, `pong` |
  | `viewer` | engine or display | `ping` | `frame`, `state`, `error`, `pong` |
  | `producer` | display | `frame`, `state`, `ping` | `error`, `pong` |

  - A role that the server's mode does not accept is refused with `role`.
  - At most one controller (in engine mode) or one producer (in display mode) is connected at a time. A second one is refused with `busy`.
  - Both errors close the connection.
- `client` is optional free text, and informative only.
- `seed` is optional, and only a controller in engine mode uses it.
  - It is the PRNG seed of the session that this hello starts (SPEC §9.3): an integer with `0 ≤ seed < 2^32`. It defaults to the server's own seed.
  - A bad seed is refused with `bad_hello`, and the connection is closed.

The server answers an accepted hello with its own:

```json
{"type":"hello","protocol":"17x9-tetris-remote","version":1,"role":"server","mode":"engine",
 "client_role":"controller","spec_version":2,"rows":17,"cols":9,"fps":30,"max_message":65536,
 "seed":13,"clock":"lockstep"}
```

- **`client_role`** is the role that the server accepted for this connection. It is new in v1. A client MUST act on `client_role`, not on the role it asked for, because a gatekeeper may have demoted it (§9.3).
- `seed` and `clock` appear in engine mode only. `seed` is the seed of the current session: the controller's own for a controller, and the running session's (or the server's default) for a viewer.
- **`spec_version`** is the latest sealed SPEC version whose traces the server's engine passes. It is informative.
  - A client MUST NOT reject a hello because of its `spec_version`.
  - SPEC versions that share a trace set are digest-equivalent. [`spec/SEALS.md`](../spec/SEALS.md) records `sha256(traces)` for each version, and v1 and v2 share the set `981baab4…`. A client that checks the frame digests against its own engine SHOULD therefore compare trace sets, not version numbers.

**Versioning.**

- `version` is one integer, the contract's major version: contract-vN speaks `version` N.
- Adding optional fields, message types or error codes does **not** change it. A v1 peer ignores unknown fields, and treats an unknown error code as non-fatal unless the connection then closes.
- Changing or removing anything that a v1 peer relies on **does** change it, and needs a new contract seal.
- Each seal records `sha256(docs/PROTOCOL.md)` and the digest of `spec/protocol/` that `check.py` prints (§10).

## 3. Message types

| type | direction | fields | schema |
|---|---|---|---|
| `hello` | client → server | §2 | `hello-client` |
| `hello` | server → client | §2 | `hello-server` |
| `event` | controller → server | `action`: one of `left`, `right`, `soft_drop`, `hard_drop`, `rotate_cw`, `rotate_ccw`, `rotate_180`, `hold` (SPEC §5.1); `down`: boolean (`true` = press, `false` = release) | `event` |
| `tick` | controller → server | `frames`: an integer in 1…3600. Lockstep clock only (§4.2). | `tick` |
| `frame` | server → clients; producer → display server | `frame_no`: an integer ≥ 0; `rows`: 17 arrays of 9 `[r, g, b]` arrays, row-major, row 0 at the top; `digest`: the SPEC §9.4 digest, lowercase hex SHA-256 of the 459 R,G,B bytes; `events`: E_k (engine mode, §4.1) | `frame`; from an engine server, `frame-engine` |
| `state` | server → clients; producer → display server | `score`, `level`, `lines`: integers ≥ 0 (required). `high_score` (integer ≥ 0), `phase` (string of at most 32 characters) and `frame_no` (integer ≥ 0) are optional, except from an engine server. | `state`; from an engine server, `state-engine` |
| `ping` / `pong` | either way | an optional `id` (an integer, or a string of at most 64 characters). `pong` echoes the `id`. | `ping` |
| `error` | server → client | `code` (§7), `message` (free text) | `error` |

- **`events` (new in v1).** In engine mode, every `frame` carries `events`. They are E_k: the `[action, down]` pairs that the server passed to step `k`, in order, possibly none. They are the events *delivered* to that step. The engine may then apply, queue or discard them (SPEC §9.2); `events` reports its input, not what it did with it. With `events`, every client sees the session's log (§4.4).
- **`state` is a view.** A `state` message, `phase` included, is derived from the engine state after a step. It is never authoritative.
  - Clients MUST NOT feed it back as input. The engine server refuses a `state` from a controller with `forbidden`.
  - Conformance checks the frame digests, and checks `state` against the transcripts derived from the reference.
  - In engine mode, `phase` is exactly one of SPEC's phase names (§9.1): `countdown`, `playing`, `clearing` or `gameover`. In display mode, a producer's `state` is that producer's own view, and the server uses it only for the HUD.
- **Frame validation.** Every receiver MUST check that:
  - there are exactly 17 rows of exactly 9 cells;
  - each cell is an array of exactly 3 JSON integers in `0..255`.

  Out-of-range values are **rejected, not clamped**. SPEC §2.2 clamping belongs to the producer's `Color` constructor, before encoding, so that every frame on the wire has exactly one digest.
- **Digest.** A producer MAY omit `digest`; the display server then computes it. If `digest` is present, it MUST equal the digest of `rows`, or the frame is rejected with `digest`. An engine server always sends `digest`.

## 4. Engine mode: a session is a fold over its log

The server runs a SPEC-conformant engine and streams its frames.

- **4.1 The log, the fold and the view.** A controller's accepted hello starts a **session**. The session ends when the controller's connection ends. In the terms of the state-machine ladder (§8):
  - **The log is the source of truth.** It is the session seed plus the sequence `E_0, E_1, …`, where `E_k` is the ordered list of events that the server passed to step `k`.
  - **The fold is the derivation.** `S_0 = init(seed)` and `S_{k+1} = step(S_k, E_k)`, as in SPEC §9.1. It is a pure function of the log.
  - **The frames and `state` messages are the view.** Frame `frame_no = k` is `render(S_{k+1})`, SPEC frame `k`, so the boot countdown is frames 0–89. A `state` is a projection of `S_{k+1}`.

  The view is derived and never authoritative. A server MUST NOT keep anything outside the log that could affect the frames, so **the same log always gives the same frames and digests.** `phase` is a view too: its successive values follow SPEC's legal phase edges (SPEC Table 9.1, P20). A server whose `state` messages take an illegal edge does not conform.
- **4.2 Clocks.** A clock decides how events are stamped with the step that receives them. The server's `--clock` option chooses one of two.
  - **`realtime`** (the default). The server steps once every 1/30 s, SPEC §2.3's rate. The events received since the previous step form `E_k`, in arrival order. The server's clock is the sequencer: event time is assigned at processing time, as it was by the OS in the legacy (SPEC §13), and `events` publishes the stamps.
  - **`lockstep`**. The engine advances only on `tick`. The events received since the previous tick form `E_k` for the first frame of the tick, and the tick's other frames get none. This clock is **deterministic**: the same seed and the same message sequence give exactly the frames, and the digests, of a direct engine run.

  A conforming engine server MUST offer the lockstep clock as an operator option, because the conformance transcripts run under it (§10). A `tick` under the realtime clock is refused with `forbidden`.
- **4.3 Delivery order.** For each step `k`, the server sends frame `k` to the controller and to every viewer. If the view `(phase, score, level, lines, high_score)` differs from the last one sent in this session, or if `k` is the session's first frame, it then sends a `state` with `frame_no` `k`. Both are sent before frame `k+1`.
  - A `tick` of `n` frames therefore yields exactly `n` frame messages, in `frame_no` order, each followed by at most one `state`.
  - A server handles each connection's messages in order. Everything caused by a message (frames, states, an `error` or a `pong`) is sent before anything caused by a later message from the same connection. So `ping` is a barrier: its `pong` comes after every frame of the ticks sent before it.
- **4.4 Replicas.** Since every frame carries `E_k` and the hello carries the seed, any client can fold the log with its own conforming engine and reproduce every digest. It is then a *replica* (§8), and digest equality is the replica-agreement check. This works under both clocks. The Emacs client's KAV mode and the Hy client do exactly this.
- **4.5 Traces.** A session's log is a SPEC §12 trace: each event `[k, action, down]`. A server MAY write it out (the Python server's `--trace-out`), and any conformant engine then replays it to the same digests.
- **4.6 Key releases.** The engine is driven by presses *and* releases (latches, SPEC §5.4). A client that cannot observe key releases, as in a terminal or Emacs, SHOULD send a release right after each press. Auto-repeat is the client's business (SPEC §14).
- **4.7 Viewers.** A viewer's hello never starts or changes a session. The viewer receives the server hello, then the last `state` of the running session, if there is one, then the stream from the next frame on.

## 5. Bindings

- **5.1 Common rules.** Every binding carries the same messages, with the same semantics and the same errors. A binding only:
  - delimits messages;
  - carries the opening handshake;
  - maps the end of a connection.

  One protocol connection is one transport connection. A server MAY offer several bindings at once; they then share one engine, one controller slot and one connection limit, and a session driven over one binding streams to viewers on the others.

  Either binding MAY run over a Unix-domain stream socket instead of TCP, with the same framing and semantics. The URLs are `unix:///path` (JSON lines) and `ws+unix:///path` (WebSocket; the request path is still `/tetris-17x9`). A Unix socket is not subject to a jail's loopback remapping (§9.2).
- **5.2 TCP, JSON lines** (as in v0). The default port is **1709**.
  - Each message is one line terminated by LF (`0x0A`). A CR before the LF is tolerated, and empty lines are ignored.
  - A final fragment without an LF is discarded when the connection closes.
  - A line is at most 65536 bytes, LF included (§1.2).
  - After a fatal error, the server sends the `error` line and then closes the connection.
- **5.3 WebSocket** (RFC 6455, new in v1). The default port is **1710**.
  - **URL.** The URL is `ws://HOST:PORT/tetris-17x9`, or `wss://…` when a front end terminates TLS (§9). A server MUST accept the contract at the path `/tetris-17x9`. Other paths are outside the contract: a server MAY serve something else there, such as a web client page, and otherwise answers 404.
  - **Subprotocol.** The client MUST offer the subprotocol **`tetris-17x9.v1`** in `Sec-WebSocket-Protocol`, and the server MUST select it.
    - A server refuses a handshake that does not offer it with HTTP 400.
    - A client MUST fail the connection if the server does not select it.
    - The subprotocol names the protocol version. A later contract-vN uses `tetris-17x9.vN`, a server that speaks several versions offers them all, and the hello's `version` MUST match the negotiated subprotocol.
  - **Framing.** Each message is exactly one WebSocket **text** message. Fragmentation into frames is the WebSocket layer's business. The payload is the JSON text: no LF is needed, and whitespace around it is allowed, as JSON allows.
    - A **binary** message is treated as `malformed` (non-fatal), just as non-UTF-8 bytes are in the TCP binding.
    - A text message that holds only whitespace is ignored, like an empty TCP line.
    - A payload over 65535 bytes is `too_large`: the server closes with 1009 and the reason `too_large`. RFC 6455 endpoints enforce message limits in the frame layer, before the message is delivered, so the `error` message MAY be absent. A client MUST treat close 1009 as `too_large`.
    - A text message that is not valid UTF-8 fails the connection with 1007, as RFC 6455 §8.1 requires. This is the one place where the binding is stricter than TCP, where invalid UTF-8 is a non-fatal `malformed`. It is forced by the transport, not by the protocol.
  - **Closing.** After a fatal error (§7), the server sends the `error` message and then a close frame. The close reason is the error code.

    | close code | when |
    |---|---|
    | 1000 | normal closure: the client is done, or the server ends a session normally |
    | 1001 | the server is shutting down |
    | 1007 | a text message that is not UTF-8 (RFC 6455 §8.1) |
    | 1008 | a fatal protocol error: `hello_required`, `version`, `role`, `bad_hello`, `too_many_errors` |
    | 1009 | `too_large` (the `error` message may be absent) |
    | 1011 | an internal server error, or a keepalive timeout |
    | 1013 | `busy` |
    | 4000–4999 | reserved for outer gatekeepers (§9.3). The core never sends them. |

  - **Keepalive.** WebSocket control frames (ping/pong, RFC 6455 §5.5.2) are the binding's liveness mechanism, and endpoints MUST answer a control ping. A server SHOULD send a control ping after 20 s of silence, and MAY close with 1011 if no pong comes within 20 s. The protocol's `ping`/`pong` *messages* are application messages, carried as text like any other, and are never mapped to control frames: they are ordered barriers (§4.3), which control frames are not.
  - **Back-pressure.** Bindings change *when* messages arrive, never *what* they are.
    - Output: a server MUST bound each connection's unsent output at 1 MiB, counting both what it has queued and what the transport has buffered, as in the TCP binding. It aborts a connection that exceeds that limit, without a close handshake, because a close frame would only queue behind the unsent output. The peer sees the connection drop (WebSocket 1006).
    - Input: a server MUST bound each connection's receive queue (16 messages is RECOMMENDED), and stop reading while it cannot process, so that transport flow control reaches the peer. A fast producer is slowed down, never dropped.
  - **Pacing.** Pacing does not depend on the binding.
    - A realtime engine sends at most 30 frames per second, one per step (SPEC §2.3).
    - A lockstep engine sends a frame only when the controller has ticked it: the controller owns time.
    - A display server shows at most 30 frames per second, and slows a producer down by back-pressure (§6).
    - No binding coalesces or skips frames, because a skipped frame would leave a gap in the view of the log. A consumer that cannot keep up is disconnected, never silently thinned out.
  - **Origin.** A web page in the user's browser can open a WebSocket to a loopback server (cross-site WebSocket hijacking, §9.2).
    - A server SHOULD refuse, with HTTP 403, a handshake whose `Origin` header is not on an operator allow-list. The Python server's option is `--ws-origin ORIGIN`, where `null` means `file://` pages and `*` means any origin.
    - A handshake without `Origin` comes from a non-browser client, such as the checker, Emacs or a bot, and is accepted.

    This admits or refuses browsers at the handshake. It is not authentication, and it changes nothing in the protocol.
  - **Extensions.** `permessage-deflate` MAY be negotiated; it does not change the semantics.
- **5.4 Displays: the external display protocol** (cited, not a binding of this contract). Physical and remote displays speak their own protocol, which is **normative for displays and external to this contract**:
  - it is the user's `wal.sh/tools/display` protocol, dated 2026-09-11;
  - the verbatim copy is `/scratch/work/tetris-parallel/inputs/wal-sh-display-protocol.md`, sha256 `d8a3b49de70c88d60f59093481392059c07159b409ba9d9e758598e4fd301aac`;
  - it has three parts: a sink page, a static `capabilities.json`, and a relay with one lease per display. Text messages are JSON control and binary messages are frames.

  This contract cites that protocol and does not restate or fork it. Where this section and it disagree, it wins.
  - **Roles.** A display is a sink that implements SPEC §2.3 and nothing else. The relay holds one lease per display, and fans the lease holder's frames out to the display's viewers.
    - Our game servers, and the feeds built on them (`contrib/displays`), act only as **sources** toward a display: they reserve, send frames, and renew or release, exactly as that protocol says.
    - Sinks and browser pages are that protocol's viewers.
  - **Separation from the game protocol.** The game protocol (this contract: JSON messages, and the roles controller, viewer and producer) and the display protocol never share a connection or an endpoint, and neither carries the other's messages.
    - A game `viewer` of this contract is not a display viewer, and a display `source` is not a game `producer`.
    - A feed that bridges the two is a game viewer on one side and a display source on the other.
    - The JSON binding stays at `/tetris-17x9` (§5.3). The display endpoint is the display protocol's own (`/tools/display/ws` in the reference deployment).
  - **Adaptation is outside the core.** A source maps the SPEC 17×9 frame onto the display's `w`×`h`, as announced by `capabilities.json` and by `caps`/`granted`. The mapping may scale, crop, pad, rotate or reduce the palette. It happens in the source, outside the engine and outside the core frame semantics.
    - For a display with `w` 9 and `h` 17, the Green Building's own shape, the SPEC frame's 459 row-major RGB bytes already are a display frame, and their SHA-256 is the frame's SPEC §9.4 digest.
    - For any other profile, the display frame is not a SPEC frame. The digest, the frame validation of §3 and the transcripts of §10 apply to this contract's JSON binding only.
  - **Pacing and sequence numbers.** The relay drops frames that come faster than the display's `fps`, so a source SHOULD send at most `fps` frames per second (30 for the facade). A source that uses the optional 2-byte sequence prefix SHOULD send the source `frame_no` modulo 65536.
  - **Leases belong to the display.** The display's lease (`reserve`, `granted`/`busy`, `renew`, `release`, `ttl`) is part of the display protocol and is outside the game core.
    - It adds no field to this contract, and it does not conflict with §9.3. A lease decides who paints a *display*; admission to a *game server* stays with the outer gatekeeper.
    - The richer reservation system is a separate component in its own repository.
  - **Testing.** Sources are tested against a local mock relay and its conformance tests in `contrib/displays`. Those tests are not part of `spec/protocol/`. No agent connects to the live relay (`wss://wal.sh/tools/display/ws`) from the jail, because that would publish to the live site.

## 6. Display mode

The server accepts frames from one producer and renders them to its sinks:
- ANSI truecolor on stdout;
- the self-contained HTML recorder (`--html out.html`, written at shutdown);
- a legacy `Display` adapter, `--display module:attr`: any object with `send(frame)` and `makeframe()` (SPEC §2.3). This includes `utilities.display.Display` subclasses and whatever the building exposes.

Accepted frames are also relayed to viewers. The producer's latest `state`, if any, is used as the sinks' HUD and relayed too. A producer's frames need no `events`.

- **Validation.** §3 applies, plus a strictly increasing `frame_no` for each producer. Gaps are allowed, and mean dropped frames. A rejected frame is answered with an error and is not shown. The connection continues.
- **Pacing.** A frame is shown from the next frame start, and the server never shows more than 30 frames per second (SPEC §2.3). It does not read the producer's next message before the next 1/30 s slot, so back-pressure (§5.3) slows a fast producer down instead of dropping its frames.

## 7. Errors

`{"type":"error","code":"bad_frame","message":"cell (3, 4) is not ..."}`

**Validation order.** Every message passes two gates, in the order of the state-machine ladder's "schema gate before state gate" (§8). A message rejected by either gate has no effect.
1. **The schema gate** is decoding and field validation. Its errors are `too_large`, `malformed`, `unknown_type`, `version`, `bad_hello`, `role` (for an unknown role name), `bad_event`, `bad_tick`, `bad_frame`, `bad_state`, `bad_ping` and `digest`.
2. **The state gate** checks that the message is a legal transition in the connection's current state (§8). Its errors are `hello_required`, `role` (for a role not accepted in this mode), `busy`, `forbidden`, and `bad_frame` for a `frame_no` that does not increase.

| code | fatal | meaning | WebSocket close |
|---|---|---|---|
| `malformed` | no | not UTF-8, or not JSON; not an object; `type` missing or not a string; NaN or Infinity; duplicate keys; a binary WebSocket message | — (a non-UTF-8 text message: 1007, §5.3) |
| `too_large` | **yes** | a message over 65535 bytes of JSON text (§1.2) | 1009, and the `error` message may be absent |
| `unknown_type` | no | a `type` not in §3 | — |
| `hello_required` | **yes** | the first message was not `hello` | 1008 |
| `version` | **yes** | the wrong `protocol` or `version` | 1008 |
| `role` | **yes** | a role that is missing, unknown, or not accepted in this mode | 1008 |
| `bad_hello` | **yes** | a hello field failed validation, for example `seed` out of range | 1008 |
| `busy` | **yes** | the controller or producer slot is taken, or there are too many connections | 1013 |
| `forbidden` | no | a message type that is not allowed for this role, mode or clock; a second `hello` | — |
| `bad_event` / `bad_tick` / `bad_frame` / `bad_state` / `bad_ping` | no | field validation failed | — |
| `digest` | no | `digest` does not match `rows` | — |
| `too_many_errors` | **yes** | the 16th error on one connection. The server sends that error, then `too_many_errors`, then closes. | 1008 |

After a non-fatal error, the offending message is discarded and has no effect. After a fatal error, the server sends the error message and then closes the connection.

## 8. Lifecycles, classified by the state-machine ladder

The terms below are those of the state-machine ladder: `aygp-dr/state-machine-ladder`, `spec.org` at tag `spec-v2.4.0`, sha256 `88654eb635fd4d04785aef999046c7182297d091188be9013e5c67e2c9ee51fc`. It is cited, not restated. The classification was checked against a sha256-verified copy of that file.

| machine | ladder class | states and edges | right projection | checked by |
|---|---|---|---|---|
| session | the core pattern (§1): log → pure fold → view | seed and `E_0, E_1, …` → `step` → frames and `state` | the log is the truth; frames and `state` are derived | the transcripts' oracle (§10); replicas (§4.4) |
| game phase | CYCLE (§2.2) | SPEC Table 9.1, twelve legal edges | the current phase plus T-legality (SPEC P20) | the checker's state gate, on the phases of the `state` messages |
| connection | branching lifecycle, a tree (DAG, §2.4) | `open → joined(role) → closed` and `open → refused` | the current state; the flag view is sound but not complete | the server's state gate (§7); the checker's lifecycle check |
| controller or producer slot | CYCLE (§2.2) | `free → taken → free` | the current state plus legality: taking a taken slot is `busy` | the server |
| clients that fold the log | replicated state machine (§2.7) | the server orders the log (§4.2); clients fold it | replica agreement: equal digests (E10) | clients (§4.4) |

- **The connection is not a chain**, contrary to draft v0.
  - `refused` is reached from `open` without passing through `joined`: the first message was not an acceptable hello, the server sent `hello_required`, `version`, `role`, `bad_hello`, `busy` or `too_large`, or the client left before its hello.
  - The visited set `{open, refused}` is therefore not a down-set of `open < joined < closed`. The lifecycle is acyclic with two leaves, and ladder §2.4's projection applies: a current-state column restores completeness.
  - While `joined`, the handling of messages is not a lifecycle transition: those messages belong to the session's log (events and ticks) or to its view.
  - The role is fixed for the life of a connection. Nothing in v1 hands a role over.
- **The phases** are SPEC's cycle (SPEC §9.1). This contract does not repeat their edges.
- **Validation and reporting** (ladder E7). The server is the aggregate: it validates, and rejects an illegal message with an error, never repairing it. Clients, and `check.py`, are projectors: they report anomalies and never repair a stream.
- **Back-pressure** (ladder E9) is a consumer-side concern. It changes when frames arrive, never which frames the log gives.

## 9. Security

- **9.1 No authentication, no encryption.** Neither v0 nor v1 has any in the core. Anyone who can reach the port can play the game or paint the display, and on Sep 29 that display is a building. A deployment that is reachable by anything other than trusted local processes MUST front the server, either with a tunnel to a loopback-bound server (for example `ssh -L 1709:127.0.0.1:1709 host`) or with an outer gatekeeper (§9.3).
- **9.2 Binding, reporting and limits.**
  - **Loopback by default.** A server binds `127.0.0.1` unless told otherwise, and warns on stderr when the address is not loopback.
  - **Report the real address.** A server MUST report the address and port that it actually bound, not the one it asked for. In a FreeBSD jail whose `lo0` carries the jail's own address (such as `10.0.0.22/32`), a bind to `127.0.0.1` lands on that address, and **other jails on the same host can reach it**. There, loopback-by-default is not isolation: treat any bound server, test servers included, as reachable by co-tenants.
  - **Bounded input.**
    - Messages are capped at 64 KiB (§1.2).
    - At most 8 connections are served, and a 9th is refused with `busy`.
    - A connection is closed after 16 errors.
    - A connection whose unsent output passes 1 MiB is closed (§5.3).
  - **Malformed input is rejected, never interpreted.** Parsing is strict JSON, and nothing is evaluated. Every field is type- and range-checked before use. The server's `--display` option imports a module named by the **operator** on the command line, never by a client.
  - **Browsers.** Any web page that a user opens can connect to a loopback WebSocket server, because WebSocket has no same-origin restriction. So a WebSocket server SHOULD refuse any `Origin` that is not on its operator's allow-list (§5.3), or else be fronted by a gatekeeper that does.
  - **Unix sockets.** A Unix-domain socket (§5.1) is reachable only through the file system, so it avoids the jail remapping described above.
- **9.3 The outer gatekeeper (extension point).** A deployment MAY put an **outer gatekeeper** in front of a server: a proxy that speaks this protocol on both sides. The core protocol is the same with or without one.
  - **It MAY:**
    - bridge bindings, for example WebSocket outside and TCP inside, and terminate TLS;
    - **refuse** a connection before forwarding anything, by answering `busy` and closing (in WebSocket with 1013 or a 4000–4999 code), or by refusing the WebSocket handshake with an HTTP status;
    - **demote** a connection at its hello, by rewriting `role` from `controller` to `viewer` before forwarding it. The server's hello then reports `client_role: "viewer"` (§2);
    - **end** a connection at any time, by closing it. The session then ends exactly as if the client had disconnected.
  - **It MUST NOT:**
    - alter, reorder, drop or synthesize any other message, in either direction;
    - add fields.

    The log that the server folds is exactly what the client sent, apart from a demotion, which is visible to the client as `client_role`.
  - **Transparency is tested.** The reference `spec/protocol/proxy.py` is a transparent gatekeeper and binding bridge. The gate runs the transcripts through it, and it MUST pass them exactly as the server does alone (§10).
  - **Admission is not in the contract.** How a gatekeeper decides whom to admit (identities, credentials, queues, time slots) is outside this contract. The protocol has no fields for tokens, leases, queues or epochs, and adding such fields is not an extension point: admission lives entirely in the gatekeeper.

## 10. Conformance

- **Transcripts.** `spec/protocol/transcripts/` holds golden exchanges, generated by `spec/protocol/gen_transcripts.py` and never edited by hand.
  - **Derived from the traces.** For each SPEC trace `NN-name.json` there is a transcript `NN-name.jsonl`: a lockstep controller's hello with the trace's seed, its events and ticks, and every frame and `state` that a conforming server sends back.
    - Each checkpoint frame's `digest` is the trace's own digest, so **server conformance is engine conformance over the wire.**
    - The `state` lines are the reference engine's view, and the generator refuses to write them unless that engine reproduces the trace's digests and final observation.
  - **Error handling.** Transcripts `eNN-*.jsonl` pin the error handling of §7.
  - **The gate checks the derivation.** `gen_transcripts.py --check` fails if the files differ from what the traces give, and `bin/verify.sh` runs it.
  - [`spec/protocol/README.md`](../spec/protocol/README.md) defines the file format.
- **A server conforms** if `check.py --server URL` passes every transcript, over each binding that the server offers. For every transcript, the checker applies these gates in the ladder's order:
  1. **The schema gate:** strict JSON, then the message's JSON Schema, then the digest of its rows.
  2. **The state gate:** the connection lifecycle, contiguous `frame_no`s, and T-legality of the phases in the `state` messages.
  3. **The oracle:** each server message equals the transcript's expectation, field by field (`rows` is pinned by `digest`), and WebSocket closes carry the codes of §5.3.
- **A client conforms** if a session it drives through `proxy.py --record` passes `check.py --session`. That means:
  - every message it sends validates, and it follows its role's lifecycle;
  - it causes no errors;
  - for a KAV replay, the log it produces (seed and stamped events) equals the trace's;
  - and it reports that the digests match (§4.4).
- **The gate.** `SERVERS="python" bin/verify.sh` runs this protocol leg after the engine leg. The leg verifies the verifier first. A corrupted transcript, and each mutant server (wrong digests, repainted frames, renamed phases, dropped or restamped messages, an illegal phase edge), MUST be rejected. A transparent gatekeeper over TCP, and a WebSocket bridge, MUST pass.

## 11. Example

A lockstep session: `>` is client to server, `<` is server to client, and frames are abbreviated.

```
> {"type":"hello","protocol":"17x9-tetris-remote","version":1,"role":"controller","seed":7}
< {"type":"hello","protocol":"17x9-tetris-remote","version":1,"role":"server","mode":"engine","client_role":"controller","spec_version":2,...,"seed":7,"clock":"lockstep"}
> {"type":"tick","frames":91}
< {"type":"frame","frame_no":0,"rows":[[[0,0,0],...]],"digest":"...","events":[]}
< {"type":"state","score":0,"level":0,"lines":0,"high_score":0,"phase":"countdown","frame_no":0}
  ... frames 1-90 ...
> {"type":"event","action":"hard_drop","down":true}
> {"type":"event","action":"hard_drop","down":false}
> {"type":"tick","frames":1}
< {"type":"frame","frame_no":91,...,"events":[["hard_drop",true],["hard_drop",false]]}
> {"type":"event","action":"jump","down":true}
< {"type":"error","code":"bad_event","message":"unknown action 'jump'"}
> {"type":"ping","id":"end"}
< {"type":"pong","id":"end"}
```

Over WebSocket, each line above is one text message on `ws://127.0.0.1:1710/tetris-17x9` with the subprotocol `tetris-17x9.v1`.

## 12. Changes from draft v0, and open questions

**Changes in v1:**
- `version` is 1.
- The server hello carries `client_role` (§2).
- Engine frames carry `events` (§3, §4.4), so that realtime sessions can be checked too.
- The delivery order is normative (§4.3).
- The size limit is stated as 65535 bytes of JSON text, which is binding-independent. For TCP it is unchanged.
- The WebSocket binding (§5.3), and Unix-domain sockets as carriers (§5.1).
- Displays (§5.4): the user's `wal.sh/tools/display` protocol is cited as the normative, external display protocol. Our servers and feeds are only its sources. It never shares an endpoint with the game protocol, the adaptation to device profiles lives outside the core, and its per-display lease belongs to the display.
- `spec_version` semantics (§2).
- The ladder classification is corrected: the connection is a branching lifecycle, not a chain (§8).
- Security (§9):
  - fronting is required;
  - the real bound address is reported;
  - the jail loopback caveat;
  - the outer gatekeeper.
- Conformance artifacts (§10).

**Open questions:**
- Several controllers (co-op or versus), and hand-over. In v1 a new controller means a new session.
- Whether `state` should also carry the full SPEC §12 observation (active piece and hold).
- The building's row→floor and column→bay mapping (SPEC §10.4) is still TBD. The protocol carries display coordinates only.
- Authentication stays out of the core. It belongs to the gatekeeper (§9.3).
