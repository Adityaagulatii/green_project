# 003: gravity accumulator in binary64

- **Hypothesis.** The legacy `fractionalPosition += self._gravity * 2`, with `_gravity = 1/48`, reaches `≥ 1` after **25** frames at level 0, not the 24 that exact arithmetic gives. So an exact-rational spec would not match the original.
- **Success criterion.** Enumerate every gravity level. Compare the number of frames from 0 to the first drop under binary64 and under exact rationals, and pin whichever the legacy uses.
- **What could go wrong.** Another language could accumulate differently: a Guile exact rational `1/48`, a JS number or a Clojure double. SPEC §7.3 therefore mandates binary64 and gives the resulting cadence table. The `gravity-ceil-cadence` mutant in `bin/verify.sh` proves that the traces catch the difference.

## Run

```
/scratch/venvs/tetris-py/bin/python experiments/003-gravity-binary64/run.py
```

## Observations

- **2026-09-10.** binary64 and exact rationals disagree for DEN 48, 38 and 28:

  | DEN | 48 | 43 | 38 | 33 | 28 | 23 | 18 | 13 | 8 | 6 | 5 | 4 | 3 | 2 | 1 |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | binary64 | 25 | 22 | 20 | 17 | 15 | 12 | 9 | 7 | 4 | 3 | 3 | 2 | 2 | 1 | 1 |
  | rational | 24 | 22 | 19 | 17 | 14 | 12 | 9 | 7 | 4 | 3 | 3 | 2 | 2 | 1 | 1 |

  The differential suite (001) confirms that the legacy and the engine accumulators are bit-identical at every logical frame.

## Promotion checklist

- [x] SPEC §7.3 mandates binary64 and gives the table (QUIRK-8).
- [x] `test_gravity_cadence_table` and P7 (`test_p07_gravity_cadence`).
- [x] Mutant `gravity-ceil-cadence` is rejected by the gate.
