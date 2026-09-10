# 002: collision at the board edge

- **Hypothesis.** The spec rule "a cell outside the 20×11 padded board collides" (SPEC §4.4) gives the same answer as legacy `_checkCollision` on every candidate the rules can generate. The legacy rule is numpy indexing, where negative indices wrap around to the far side, and an index ≥ 20 would raise.
- **Why it matters.** Wrap-around is a numpy accident. Hy shares it, but Clojure, JS and Guile vectors don't. If some reachable kick candidate wrapped onto an empty cell, "outside collides" would diverge. The spec would then need a QUIRK that reproduces the wrap.
- **Success criterion.** For every piece placement reachable from spawn (including hold swap-ins in any rotation, QUIRK-6), and for each of its candidates (both shifts, the drop, and all 5 kick tests of all 3 rotations), legacy and spec agree. The legacy must also never raise `IndexError`. This must hold on the empty board and on random boards.
- **What could go wrong.** The BFS might miss states that only a sequence of kicks reaches. It closes over the candidate relation, so it doesn't. The random boards might also be too sparse; Hypothesis varies the stack height from 0 to 17.
- **Analysis.** A cell at row −1 or −2 wraps to the white floor rows 19 and 18. A cell at column −1 wraps to the white right border. A cell at row −3 or column −2 would wrap onto the playfield, but no reachable candidate puts its *first-scanned* occupied cell there. Each such candidate has a cell on a white border earlier in the row-major scan.

## Run

```
nice -n 10 lockf -k -t 7200 /scratch/locks/heavy.lock /scratch/venvs/tetris-py/bin/python \
  -m pytest impl/python/tests/test_differential.py -k collision
```

## Observations

- **2026-09-10.** Empty board: agreement on every candidate of every reachable placement. That is over 10,000 checks, with no `IndexError`.
- **2026-09-10.** Random boards: agreement on every Hypothesis example.

## Promotion checklist

- [x] SPEC §4.4 states "outside collides" and cites this experiment.
- [x] Promoted to `test_collision_rule_equals_numpy_wraparound_*` in `impl/python/tests/test_differential.py`.
