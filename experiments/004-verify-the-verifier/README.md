# 004: verify the verifier

- **Hypothesis.** The gate `bin/verify.sh` accepts the reference engine, and rejects both a corrupted trace set and every deliberately wrong engine. So a PASS means something.
- **Success criterion.** The gate prints:
  - `self-test ok` for the corrupted copy of the traces and for each of the 5 mutants in `impl/python/conformance/mutants.py`;
  - then `PASS` for the Python reference;
  - then `GATE: PASS`, with exit status 0.

  Any accepted corruption or mutant is a `GATE FINDING`, and the gate then exits 1.
- **The mutants.** Each one breaks a single rule:

  | Mutant | What it breaks |
  |---|---|
  | `ccw-release-keeps-dcd` | QUIRK-3 |
  | `gravity-ceil-cadence` | QUIRK-8: level 0 drops every 24 frames instead of 25 |
  | `level-target-plus-six` | QUIRK-7 |
  | `standard-180-kicks` | QUIRK-1 |
  | `no-ghost` | §8.1 |
- **What could go wrong.**
  - A mutant that is accidentally equivalent to the reference. The first draft of the gravity mutant was: exact `Fraction(2, den)` increments. But Python's float + Fraction is a float, and `fl(1/48)·2 = fl(2/48)`, so that draft would have been a silent no-op. It was replaced before the first gate run.
  - Traces too weak to kill a mutant. That is the point of this experiment: every mutant must be killed by at least one trace.

## Run

```
nice -n 10 lockf -k -t 7200 /scratch/locks/heavy.lock bin/verify.sh
```

## Observations

- **2026-09-10, run 1: REFUTED.**
  - The corrupted trace was rejected, and 4 of the 5 mutants were rejected, but **`ccw-release-keeps-dcd` was accepted**.
  - The reference passed 14/14, and the gate printed `GATE: FAIL`.
  - Cause: trace `03-rotate-cw-ccw` only ever released CCW in the same frame as the CCW press. The press had already set `dcd = 0`, so the reset on release (QUIRK-3) was unobservable.
  - Fix: trace 03 now presses CCW in one frame, then releases it and presses CW in a later frame. The CW press is swallowed only because of QUIRK-3. A control does the same with 180, whose release doesn't reset DCD, so there the CW press turns the piece.
  - This is the case the self-test exists for: without it, the trace set would have been sealed with QUIRK-3 unpinned.
- **2026-09-10, run 2: PASS.**
  - The corrupted trace was rejected, and all 5 mutants were rejected, `ccw-release-keeps-dcd` included.
  - `PASS: 14/14 traces, trace-set sha256 981baab4279c5c9f93f42eda23f1eb9f2b0889b4c228637e2ac19b9a479c2026`, then `GATE: PASS`, exit 0.

## Promotion checklist

- [x] The self-test is part of `bin/verify.sh` by default (`--no-selftest` to skip).
- [x] The runner's unit tests live in `impl/python/tests/test_conformance.py`.
