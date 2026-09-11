# Contract conformance (`spec/protocol/`)

This directory holds the machine-readable half of the network contract, [`docs/PROTOCOL.md`](../../docs/PROTOCOL.md) (contract v1). Where this page and PROTOCOL.md disagree, PROTOCOL.md wins. Where PROTOCOL.md and [`SPEC.md`](../../SPEC.md) disagree about the game, SPEC.md wins.

| file | what it is |
|---|---|
| `schemas/*.schema.json` | JSON Schema (draft 2020-12) for every message: `hello-client`, `hello-server`, `event`, `tick`, `frame` (and `frame-engine`), `state` (and `state-engine`), `ping`, `error`, a dispatcher `message`, and the shared `common` |
| `transcripts/*.jsonl` | golden client–server exchanges. They are **generated, never edited**. |
| `gen_transcripts.py` | derives the transcripts from `spec/conformance/traces`. `--check` is the gate. |
| `check.py` | the conformance checker, for servers (`--server`) and for clients (`--session`) |
| `proxy.py` | the reference outer gatekeeper and binding bridge, a session recorder, and the mutant servers |
| `schema.py` | a small standard-library JSON Schema validator, covering the keywords that the schemas use and rejecting any other |

Everything is standard-library Python 3.12, plus `websockets` (FreeBSD `py312-websockets`) for `ws://`.

## Transcripts

Each transcript is JSON Lines. **Line 1** is a header:

```json
{"format":"17x9-tetris-transcript","contract":1,"name":"07-hard-drop","kind":"trace",
 "trace":"07-hard-drop.json","trace_sha256":"…","seed":5,"frames":121,"digest_every":1,"description":"…"}
```

`kind` is `trace` for a transcript derived from a SPEC trace, or `errors` for one that pins the error handling of PROTOCOL §7. Every **later line** is one step:

| step | meaning |
|---|---|
| `[">", MSG]` | the client sends `MSG`, one message |
| `[">raw", TEXT]` | the client sends `TEXT` verbatim as one message. It is used for input that is not valid JSON. |
| `[">pad", N]` | the client sends a `ping` with `id` `"pad"`, padded to exactly `N` bytes of JSON text. It is used for the size limit. |
| `["<", EXPECT]` | the server's next message MUST contain every field of `EXPECT` with an equal value, where `true` is not `1` and `1.0` is not `1`. Other fields are allowed (PROTOCOL §1.3). A frame's `rows` are never in `EXPECT`: its `digest` pins them. |
| `["<close"]` | the server closes the connection next |

**Derivation.** For trace `NN-name.json`, the transcript `NN-name.jsonl` is built as follows:
- a lockstep controller says hello with the trace's seed;
- then, for each tick, it sends the events of the tick's first frame and a `tick` of `n` frames, using the canonical batching: a tick runs up to the next frame with events, and at most 3600 frames;
- the expected replies are `n` frames, each with its `events` (E_k) and, at the trace's checkpoint frames, **the trace's own digest**;
- a `state` follows a frame whenever the view `(phase, score, level, lines, high_score)` changes (PROTOCOL §4.3);
- a `ping`/`pong` barrier ends the transcript.

The `state` lines are the Python reference engine's view. `gen_transcripts.py` refuses to write them unless that engine reproduces every trace digest and the final observation. So a server that passes a transcript reproduces its trace over the wire: **server conformance is engine conformance.** The error transcripts `e01`…`e07` pin PROTOCOL §7:
- non-fatal errors, in the gates' order, and their lack of effect;
- `hello_required`, `version`, `bad_hello`, `role` and `too_large`, each followed by the close;
- `too_many_errors` after the 16th error;
- the 65535-byte boundary.

After a trace revision, run `gen_transcripts.py --write`. The gate runs `--check`.

## The checker

```
check.py --server tcp://127.0.0.1:1709                      # a running server
check.py --server ws://127.0.0.1:1710/tetris-17x9           # WebSocket (PROTOCOL §5.3)
check.py --server unix:///tmp/t.sock                        # JSON lines over a Unix socket (also ws+unix://)
check.py --launch "python -m tetris_sim.server --mode engine --clock lockstep --port {port}" \
         --server "tcp://127.0.0.1:{port}"                  # start it on a free port
check.py --selftest                                         # the schemas reject known-bad messages
check.py --session rec.jsonl [--trace 07-hard-drop]         # a client (see below)
```

Server mode opens one connection per transcript and judges what comes back in the ladder's order:
1. **The schema gate:** strict JSON (PROTOCOL §1.4), the JSON Schema of the message, and a frame's digest against its rows.
2. **The state gate:** the lifecycle (hello first, nothing after a fatal error), `frame_no` contiguous from 0, and T-legality (SPEC Table 9.1) of the phases in the `state` messages.
3. **The oracle:** every expectation, and the WebSocket close codes.

It prints `PASS: 21/21 transcripts … contract-set sha256 …` or `FINDING` lines, and exits 0, 1 or 2 like `spec/conformance/run.py`. The engine server must run the lockstep clock.

**Clients.** Put `proxy.py --record rec.jsonl` between the client and a conforming server, let the client replay a KAV (the Emacs client's `tetris-mit-kav`, or the Hy client), and then run `check.py --session rec.jsonl`. It checks that:
- every client message validates;
- the role's lifecycle is respected;
- the client caused no server errors;
- its log (seed and stamped events) equals the trace's;
- the frames it received carry the trace's digests.

The client's own digest check (PROTOCOL §4.4) is the other half.

## proxy.py

- **Transparent gatekeeper and bridge.** `proxy.py --listen URL --upstream URL` forwards whole messages both ways. It can bridge `ws://` outside to `tcp://` inside. The gate checks that it is transparent (PROTOCOL §9.3).
- **Demotion.** `--demote` rewrites a controller's hello to `viewer` (PROTOCOL §9.3).
- **Mutant servers.** `--mutate NAME` corrupts the server's stream in one way, for verifying the verifier.

  | mutant | corruption | caught by |
  |---|---|---|
  | `digest-flip` | frame 100's digest does not match its rows | schema gate |
  | `phase-alias` | `playing` renamed `play` | schema gate |
  | `no-events` | engine frames without `events` (a v0 wire) | schema gate |
  | `illegal-edge` | frame 0 reported as `gameover` | state gate (gameover → playing) |
  | `repaint` | frame 100 repainted, digest recomputed | oracle |
  | `drop-state` | the second `state` message is lost | oracle |
  | `stamp-shift` | events reported one frame late | oracle |

- **Launching the server.** `--upstream-launch "CMD {upstream_port}"` starts the server itself, so a mutant is one command for `check.py --launch`.

## The gate

`SERVERS="python" bin/verify.sh` adds the protocol leg after the engine leg. With `SERVERS` empty, the gate's output is byte-for-byte the engine gate's. The leg:
- verifies the verifier: `check.py --selftest`; a corrupted transcript rejected; every mutant server rejected; a transparent gatekeeper over TCP passes, and so does a WebSocket front;
- then runs `gen_transcripts.py --check`;
- then runs every server in `SERVERS` against every transcript.

A server enters `SERVERS` by adding one `server()` line in `bin/verify.sh` (the steward resolves conflicts).

## Conformance matrix

What each implementation passes, and where it was observed. Engine columns come from `bin/verify.sh` (`spec/conformance/run.py`); the cross-driver audit column from `spec/conformance/audit.py`; protocol columns from `check.py`. A dash means the column does not apply.

| implementation | engine traces (A.2: digests and observation) | T-legality (P20, state gate) | primitive KAVs (A.1, own tests) | cross-driver audit (phases and digests identical to Python's) | protocol server (`check.py --server`) | protocol client (`check.py --session`) |
|---|---|---|---|---|---|---|
| Python engine (`impl/python/engine`) | PASS 14/14 (v2 seal) | PASS | pytest (P19) | reference | — | — |
| Hy engine (`impl/hy`) | PASS 14/14 (v2 seal) | PASS | `test_hy_kat.hy` | *pending* | — | — |
| Clojure (`impl/clojure`, not merged) | PASS 14/14 (reported by the Clojure rebuild) | PASS | `kav_test.cljc`, including the v3 high-bit seeds | *pending* | — | — |
| Guile (`impl/guile`, not merged) | *in progress* | | | | — | — |
| Elisp (`impl/elisp`, not merged) | *in progress* | | | | — | — |
| Python server (`impl/python/sim`, v0 wire) | — | — | — | — | **FAIL**: speaks protocol version 0 (no `client_role`, no `events`); v1 is in progress on `impl/python-ws` | — |
| Emacs client (`contrib/emacs`, v0 wire) | — | — | — | — | — | *pending v1* |
| Hy client (`impl/hy-client`) | — | — | — | — | — | *pending v1* |
| *evidence only:* v1 shim over the Python server (experiment 007) | — | — | — | — | PASS 21/21 over TCP, and over a WebSocket front | — |
