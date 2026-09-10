# Conformance traces

The traces in `traces/*.json` are the **oracle** for every implementation. [`SPEC.md` §12](../../SPEC.md#12-conformance-traces) defines their schema normatively; this page explains how to use them. If this page and SPEC.md disagree, SPEC.md wins.

- **Generated, never edited.** The sealing implementation writes the traces:
  ```
  impl/python/conformance/generate_traces.py
  ```
  Implementations read them in place: they never copy, clean up or re-serialise them. A revision that changes behaviour regenerates the whole set and records its hash in [`../SEALS.md`](../SEALS.md).
- **A trace in one line.** It holds a seed and a list of `[frame, action, down]` input events, plus what the reference produced from them: SHA-256 digests of the 17×9 RGB frames (every `digest_every` frames, and the last frame) and a final observation (phase, score, level, lines, high score, active piece, hold, and the final frame's bytes as hex).
- **Frame 0.** Frame 0 is the first countdown frame. Play starts at frame 91 (SPEC §9.1).

## Driver protocol

Each implementation provides a **driver** command:

```
<driver> TRACE.json [TRACE.json ...]
```

- **Arguments and output.** The driver reads each trace given on the command line. For each one, in argument order, it prints one JSON object on its own line (JSON Lines):
  ```
  {"name": "<trace name>", "digests": ["<hex>", ...], "final": {...}}
  ```
- **What it computes.** `digests` and `final` are computed exactly as SPEC §12 describes, from the trace's `seed`, `frames`, `digest_every` and `events` only. The driver MUST NOT read the trace's own `digests` or `final`.
- **Exit status.** The driver exits 0. A crash counts as a failure.

## Runner and gate

- **Runner.** `spec/conformance/run.py` is language-neutral and uses only the Python standard library. It first validates every trace's structure, then runs the driver once over all traces and compares the results. It prints `PASS` with the trace-set hash, or `FAIL` followed by one `FINDING` line per mismatch:
  ```
  python3 spec/conformance/run.py --impl "env PYTHONPATH=impl/python/engine python3 -m tetris_engine.conformance" --verbose
  ```
- **Gate.** `bin/verify.sh` is the single gate. It verifies the verifier first, then runs every implementation's driver. The gate PASSes only with zero findings:
  - a corrupted trace must be rejected;
  - deliberately wrong engines (mutants) must be rejected;
  - the reference must pass.
- **Adding an implementation.** Add a `run <name> "<driver>"` line to `bin/verify.sh`.

## Trace-set hash

`sha256(traces)` in `SEALS.md` is `run.py`'s `trace_set_digest`: SHA-256 over the trace files sorted by file name. Each file contributes its base name, a NUL byte, its raw bytes, and another NUL byte. `run.py` prints it on PASS.

## The v1 set

| Trace | What it exercises |
|---|---|
| `01-idle-gravity` | boot countdown; level-0 gravity (25-frame cadence); gravity locks |
| `02-move-left-right` | shifts; walls block; releases are no-ops |
| `03-rotate-cw-ccw` | rotations; latches; DCD gating (QUIRK-2/3/15) |
| `04-rotate-kicks` | wall kicks for JLSTZ and I, including a failed rotation |
| `05-rotate-180` | 180° kick arithmetic (QUIRK-1) for T, I and O |
| `06-soft-drop` | soft drop to the floor; immediate lock (QUIRK-14) |
| `07-hard-drop` | hard drop; latch; release resets DCD (QUIRK-4) |
| `08-hold` | hold from empty; blocked re-hold; swap keeps rotation (QUIRK-6) |
| `09-line-clear-single` | single clear; 5-frame flash; scoring |
| `10-line-clear-multi` | multi-line clear; per-row scoring (QUIRK-5) |
| `11-level-up` | level target (QUIRK-7); gravity speed-up |
| `12-game-over-reset` | top-out by hard drop; fill, wait and fall; countdown; reset; QUIRK-10/11/13 |
| `13-suspended-input` | input queued during a flash, discarded during game over and countdown (QUIRK-12) |
| `14-bot-marathon` | a long bot game with random input noise |

Each trace's `covers` field lists what it exercises.
