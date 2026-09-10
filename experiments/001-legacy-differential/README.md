# 001: legacy vs engine differential

- **Hypothesis.** The pure engine (`impl/python/engine`) and the unchanged legacy `tetris.py` agree, logical frame by logical frame, on everything observable: the padded board, the active and held pieces, score, level, lines, high score, the DCD counter, the latches, the gravity accumulator, the bag, and the sequence of frames sent to the display. This holds when both are driven by the same input batches and the same bag order.
- **Success criterion.** Zero divergences over:
  - Hypothesis-generated random input;
  - bot-driven games with random noise, which reach line clears, level-ups and game overs.

  Any divergence is either recorded as a `QUIRK-n` in SPEC.md, or recorded here as a legacy bug with a minimal failing example.
- **What could go wrong.**
  - The harness could re-implement the legacy instead of running it. It doesn't: it calls the legacy handler methods, and only the body of `play()`'s loop is restated, because `play()` itself never returns.
  - The stubs could hide behaviour. Only pygame, keyboard, tkinter, `time` and the `np.random.shuffle` name are replaced; numpy, `Frame` and `Color` are real.
  - Random input almost never clears a row on a 9-wide board. That is why the bot is mixed in.
  - The display timelines differ by design: a frame held for n slots is one send. So both streams are compared with consecutive duplicates collapsed.

## Run

```
nice -n 10 lockf -k -t 7200 /scratch/locks/heavy.lock \
  /scratch/venvs/tetris-py/bin/python -m pytest impl/python/tests/test_differential.py
# ad-hoc long run (this experiment's original script):
PYTHONPATH=impl/python/engine:impl/python/sim:impl/python/tests \
  /scratch/venvs/tetris-py/bin/python experiments/001-legacy-differential/run.py 8 1200
```

## Observations

- **2026-09-10, random input.** 120 seeds × 400 frames of random input: 0 divergences, 110 game overs, but 0 line clears. That confirmed that random input can't reach clears.
- **2026-09-10, bot-driven.** 8 bot-driven games × 1200 frames, with 0%, 5% or 20% noise: **0 divergences**.
  - Coverage: 9,600 logical frames, 2,038 frames with a line-clear flash, 19 game overs, maximum level 16.
  - Boot sequence (3, 2, 1, black) identical.
- **2026-09-10, black frame.** The only timeline adaptation needed is the post-game-over black frame. The legacy sends it and then immediately sends the resumed play frame, so it is never visible for a whole slot. This is recorded as QUIRK-10.
- **Legacy bugs found:** none that make the legacy crash or disagree with itself. Everything odd is specified as a QUIRK (1–19).

## Promotion checklist

- [x] Harness at `impl/python/tests/legacy_harness.py` runs the legacy for real.
- [x] Promoted to `impl/python/tests/test_differential.py`: Hypothesis random input, bot + noise, and long bot games (`TETRIS_SLOW=1` for 3000 frames).
- [x] QUIRKs found while reading and running the legacy are in SPEC.md Appendix B.
- [x] Re-run at every seal (POLYGLOT-PLAN "Look back at the original").
