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
  {"name": "<trace name>", "digests": ["<hex>", ...], "phases": ["countdown", ...], "final": {...}}
  ```
- **What it computes.** `digests` and `final` are computed exactly as SPEC §12 describes, from the trace's `seed`, `frames`, `digest_every` and `events` only. `phases` is the observation's `phase` for every frame `0 … frames − 1`, for the T-legality check (SPEC P20). The driver MUST NOT read the trace's own `digests` or `final`.
- **Exit status.** The driver exits 0. A crash counts as a failure.

## Runner and gate

- **Runner.** `spec/conformance/run.py` is language-neutral and uses only the Python standard library. It runs the driver once over all traces, and checks in the order of the state-machine ladder (SPEC §9.1):
  1. the **schema gate**: the fields and domains of every trace, then of every driver result;
  2. the **state gate**: T-legality of the reported phases (SPEC P20, Table 9.1);
  3. the **oracle**: the digests and the final observation equal the trace's.

  It prints `PASS` with the trace-set hash, or `FAIL` followed by one `FINDING` line per mismatch:
  ```
  python3 spec/conformance/run.py --impl "env PYTHONPATH=impl/python/engine python3 -m tetris_engine.conformance" --verbose
  ```
- **Gate.** `bin/verify.sh` is the single gate. It verifies the verifier first, then runs every implementation's driver. The gate PASSes only with zero findings:
  - a corrupted trace must be rejected, through every driver, so that no driver can pass by echoing the traces' own answers;
  - deliberately wrong engines (mutants) must be rejected;
  - every implementation must pass.
- **Adding an implementation.** Add its name to `IMPLS` in `bin/verify.sh`, and a line for it to `driver()`. The drivers so far:
  ```
  python   env PYTHONPATH=impl/python/engine python3 -m tetris_engine.conformance
  hy       env PYTHONPATH=impl/hy python3 -m hy -m tetris_hy.conformance
  ```

## Known-answer vectors (SPEC Appendix A)

Each trace is the known-answer vector `KAV-NN` of SPEC Appendix A.2, where `NN` is its file number; the ID never changes. The appendix tables summarise the traces and are generated from them, never written by hand:

```
python3 spec/conformance/gen_appendix.py --check   # the gate runs this
python3 spec/conformance/gen_appendix.py --write   # after a revision regenerates the traces
python3 spec/conformance/gen_appendix.py --print
```

The gate's self-test proves that `--check` rejects a SPEC.md whose appendix has a hand-edited digest.

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
