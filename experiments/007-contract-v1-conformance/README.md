# Experiment 007: contract v1 conformance over the wire

- **Owner:** the contract/spec steward, on branch `spec/contract`.
- **Status:** the checker, the transcripts and the negative tests are done. The promotion waits on the Python server and on a client speaking v1 natively (see the checklist).

## Hypothesis

The contract v1 transcripts are derived from the 14 SPEC traces, plus 7 error transcripts. A server that folds a session log with a SPEC-conformant engine reproduces them exactly over the wire, over TCP and over WebSocket:
- every checkpoint digest is the trace's;
- every `state` is the reference view;
- every error is PROTOCOL §7's.

The checker, `spec/protocol/check.py`, rejects every way of getting that wrong. If both hold, server conformance is engine conformance over the wire.

## Success criterion

1. A server speaking the v1 wire passes 21/21 transcripts over TCP, and 21/21 through a WebSocket front, with the same contract-set digest.
2. The checker rejects:
   - the v0 server;
   - a corrupted transcript;
   - each of the 7 mutant servers of `proxy.py`, each by the gate it targets (schema, state or oracle).
3. The self-test (`check.py --selftest`) rejects every known-bad message and accepts every known-good one.

## What could go wrong

- **Wrong derivation.** The transcripts might pin the reference's accidents rather than the contract, for example message order or state timing that PROTOCOL.md does not state. Mitigation: PROTOCOL §4.3 now states the delivery order normatively, and the transcripts only pin what it states.
- **Validator gaps.** The stdlib validator (`schema.py`) might ignore a keyword. Mitigation: it rejects any keyword it does not implement, and the self-test covers each constraint.
- **Proxy artefacts.** The WebSocket front might pass because the proxy normalises something. Mitigation: the proxy forwards message text verbatim unless mutating, and a transparent proxy is a requirement of §9.3 anyway.
- **The shim is not the server.** `v1_shim.py` monkeypatches the v0 Python server, so passing it is evidence that the checker works, not that the Python server conforms. It is never listed in `SERVERS`.

## Run

```
W=/scratch/worktrees/17x9-Tetris-contract; PY=/scratch/venvs/tetris-py/bin/python
SHIM="env PYTHONPATH=$W/impl/python/engine:$W/impl/python/sim $PY $W/experiments/007-contract-v1-conformance/v1_shim.py --mode engine --clock lockstep"
$PY spec/protocol/check.py --selftest
$PY spec/protocol/check.py --launch "$SHIM --port {port}" --server "tcp://127.0.0.1:{port}" --verbose
$PY spec/protocol/check.py --launch "$PY spec/protocol/proxy.py --listen ws://127.0.0.1:{port}/tetris-17x9 --upstream-launch '$SHIM --port {upstream_port}'" \
    --server "ws://127.0.0.1:{port}/tetris-17x9" --verbose
for m in digest-flip repaint phase-alias illegal-edge drop-state stamp-shift no-events; do
  $PY spec/protocol/check.py --launch "$PY spec/protocol/proxy.py --mutate $m --listen tcp://127.0.0.1:{port} --upstream-launch '$SHIM --port {upstream_port}'" \
      --server "tcp://127.0.0.1:{port}"; done          # each MUST fail
```

## Observations

All observed on 2026-09-11, on FreeBSD 15.1 amd64 (the jail maps a 127.0.0.1 bind to its lo0 address), with Python 3.12.14 and websockets 17.1.

- **The v0 server** (`impl/python/sim` at `790c081`) fails with 20 findings over 21 transcripts. Every transcript but one is refused with `version`. `e03-version` fails at the schema gate: the v0 server accepts the v0 hello, and its reply has no `client_role`.
- **The shim over TCP** passes 21/21, contract-set sha256 `e45524fa…7c4d`.
  - The first run took 78 s. The profile showed 92% of the time in `schema.py` `_v`: 4.1 M calls, re-validating the same RGB cells, and 2 M `$ref` resolutions.
  - After memoizing validity by type-tagged value, and memoizing `$ref`, the run takes 18.8 s, and the self-test still passes all 45 cases.
- **The shim through a WebSocket front** (`proxy.py`, ws → tcp) passes 21/21, with the same contract-set digest. The WebSocket binding and a transparent gatekeeper change nothing.
- **Every mutant is rejected** (all 21 transcripts), each by the gate it targets:

  | mutant | first finding | gate |
  |---|---|---|
  | `digest-flip` | "frame 100: digest does not match rows" | schema gate |
  | `phase-alias` | `$.phase: "play" is not one of …` | schema gate |
  | `no-events` | "missing required field 'events'" | schema gate |
  | `illegal-edge` | "illegal phase edge gameover -> playing into frame 91" | state gate |
  | `repaint` | "frame 100: digest expected … got …" | oracle |
  | `drop-state` | "frame 92: type expected state got frame" | oracle |
  | `stamp-shift` | "frame 93: events expected [["left", true]] got []" | oracle |

- **A corrupted transcript** (07-hard-drop, one digest flipped at frame 60) is rejected by the oracle, with 1 finding.
- **A lint bug, found by the first run.** The checker's lint flagged the server hello's `rows: 17` (a count) as a pinned frame. Only frame expectations must not pin `rows`; the lint is fixed.

## Promotion checklist

- [x] Transcripts generated, and `gen_transcripts.py --check` in the gate.
- [x] Checker negative-tested: the self-test, a corrupted transcript, and 7 mutant servers.
- [x] Opt-in protocol leg in `bin/verify.sh` (`SERVERS=…`). With `SERVERS` empty, the output is the engine gate's.
- [ ] The Python server speaks v1 natively (request to the python workstream), and `SERVERS=python bin/verify.sh` passes.
- [ ] A client (Hy or Emacs) passes `check.py --session` on a KAV replay (requests filed).
- [ ] The contract-v1 row in `spec/SEALS.md`, the seal commit and its note. The main session tags.
