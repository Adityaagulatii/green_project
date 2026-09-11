# 006: the legal edges of the phase machine

- **Context.** Spec v2 adds the state-machine-ladder semantics to §9 (user request). The phase machine is a CYCLE, and the new property P20 (T-legality) requires every step to be a legal edge. The table proposed with the request had 10 edges:
  - countdown → countdown, playing;
  - playing → playing, clearing, gameover;
  - clearing → clearing, playing, gameover;
  - gameover → gameover, countdown.

  It came with the instruction to verify it against the Python engine.
- **Hypothesis.** Derived from §9.2's pseudocode, there are **12** legal edges, not 10. The frame that ends a countdown runs events: at boot it is a fresh logical frame, and after a game over it runs the resumed rest of a frame. A lock among those events can start a flash or a game over, so **countdown → clearing** and **countdown → gameover** are legal too. The illegal edges are playing → countdown, clearing → countdown, gameover → playing and gameover → clearing.
- **Success criterion.** On the Python reference, the observed edges are exactly the 12: every legal edge is observed at least once, and no illegal edge ever is.
- **What could go wrong.**
  - Ordinary play never reaches the two countdown edges, so a table built from observation alone would be too tight: it would make a conforming engine fail. Constructed witnesses settle whether those edges exist.
  - The witnesses must be ordinary input. They use only shifts and soft drops, which are ungated, in rotation 0, all delivered in frame 91, the first logical frame.

## Run

```
nice -n 10 lockf -k -t 7200 /scratch/locks/heavy.lock \
  /scratch/venvs/tetris-py/bin/python experiments/006-phase-edges/run.py 40 1500
```

## Observations

- **2026-09-11.** Edges taken by the Python engine:
  - the 14 traces reach 9 edges;
  - adding 40 seeds × 1500 frames of random input, plus the same with the bot, reaches 10. These are exactly the proposed table.
  - Witness for countdown → gameover: seed 1, 400 `soft_drop` presses in frame 91. The pieces stack in the spawn columns, which can never fill a row, until a spawn collides.
  - Witness for countdown → clearing: seed 1, a 168-event batch in frame 91 (shifts and soft drops) that fills the bottom row.
  - **RESULT: observed == LEGAL, 12 edges; no illegal edge observed.**

  | edge | observed |
  |---|---|
  | countdown → countdown | 19190 |
  | countdown → playing | 211 |
  | countdown → clearing | 1 (witness) |
  | countdown → gameover | 1 (witness) |
  | playing → playing | 61285 |
  | playing → clearing | 2877 |
  | playing → gameover | 137 |
  | clearing → clearing | 11482 |
  | clearing → playing | 2863 |
  | clearing → gameover | 2 |
  | gameover → gameover | 28360 |
  | gameover → countdown | 121 |

- The Hy engine: `impl/hy/tests/test_hy_phase_machine.hy` constructs the same two witnesses with its own search, and the Hy-vs-Python differential (experiment 005) compares the phase at every frame.
- **Conclusion.** Table 9.1 has 12 edges. The proposal's 10 are exactly the edges that ordinary play reaches. This is a clarification, not a revision: the table is read off the existing pseudocode, and no behaviour changes.

## Promotion checklist

- [x] SPEC §9.1 Table 9.1 (12 edges) and P20 (§11).
- [x] `spec/conformance/run.py` state gate, after the schema gate.
- [x] Mutant `illegal-edge-gameover-playing` in the gate's self-test.
- [x] Hy P20 tests, with both witnesses.
