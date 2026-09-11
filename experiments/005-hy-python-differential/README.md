# 005: Hy engine vs Python reference differential

- **Hypothesis.** The Hy re-telling (`impl/hy/tetris_hy`) and the Python reference (`impl/python/engine`) agree, frame by frame, on everything observable and on the rule state behind it, for the same seed and events. They do so although the Hy engine is structured differently:
  - a logical frame is a *program* (queued events, fresh events, `"level-check"`, `"gravity"`), and a suspension keeps the rest of the program;
  - animations are a *script* of segments (`flash` 5, `gameover` 218, `countdown` 90, and the boot's `countdown` 90 + `black` 1), and the phase is read off the current segment;
  - the latches are the set of consumed ones, and the bag is the pieces still to draw.
- **Success criterion.** Zero divergences over:
  - Hypothesis random input from `init` (≤ 300 frames, boot included);
  - the Hy bot plus random noise (≤ 400 frames);
  - long bot games for seeds 1, 42 and 2026 (700 frames, or 3000 with `TETRIS_SLOW=1`);
  - single events on constructed mid-game positions (random stacks, almost-full rows, any latches, DCD and hold);
  - up to 60 frames from such positions;
  - the game-over boundaries: a top-out by hard drops, then 330 frames with input at random frames, across flash → game over → countdown → resume.

  The comparison covers the frame (cell codes), phase, score, level, lines, high score, active and held pieces, hold availability, the padded board, the consumed latches, DCD, the accumulator as `float.hex` (bit for bit), the bag still to draw, the PRNG state, the input queue, and the suspended rest of a frame. A divergence is either a Hy bug, fixed and recorded here, or a spec problem: a **clarification** (ambiguous wording) or a **revision** (behaviour change, reported and not applied here).
- **What could go wrong.**
  - The projection could hide a difference. Internal-only fields (timers, counters) are compared through the frames they produce, and everything else directly.
  - Random input rarely reaches clears, level-ups and game overs. The bot games, the almost-full boards and the boundary test are there for that.
  - A mistake shared by both engines is invisible to a differential. The traces (from the Python reference, which experiment 001 checks against the legacy) and the P1–P19 properties, re-told independently in Hy, cover that.

## Run

```
nice -n 10 lockf -k -t 7200 /scratch/locks/heavy.lock \
  /scratch/venvs/tetris-py/bin/python -m pytest impl/hy/tests/test_hy_differential.hy
# the seal run:
HYPOTHESIS_PROFILE=thorough TETRIS_SLOW=1 \
  /scratch/venvs/tetris-py/bin/python -m pytest impl/hy/tests/test_hy_differential.hy
```

## Observations

- **2026-09-11, traces first.** The Hy driver passed 14/14 traces on its first run, with trace-set sha256 `981baab4…2026`, before any spec change was considered.
- **2026-09-11, differential.** 0 divergences between the engines. The first runs failed only in the harness:
  - The Hy helper `booted` stepped 91 frames. That ends on the black frame 90, still in the countdown; frame 91, the first play frame, is step 92. (The Python tests force the phase instead.) Fixed to 92 steps.
  - A Hy `defn` returns its last form, and Hypothesis's health check rejects a test that returns a value. The two affected tests now end with `None`.
- **2026-09-11, reading notes.** Re-telling the engine found four places where the wording was ambiguous but the behaviour is not; all four are clarifications in Spec v2:
  - §9.2 says in prose that the frame on which a flash ends queues its events. The pseudocode also queues on the frame on which the countdown after a game over ends, but the prose lists the countdown among the phases that discard. Pinned by `test_p18_the_frame_that_resumes_after_a_game_over_queues_input` and by the boundary differential.
  - On the frame on which a flash hands over to a pending game over, whether the events are queued or discarded is unobservable, because the reset empties the queue. The Hy engine discards them (the policy of the segment being entered); the Python engine queues them.
  - §6.1's game-over board is the board *after* the line clear of the same lock.
  - §12's `active` is the state's active piece in every phase.
- **Revisions found:** none.

## Promotion checklist

- [x] Promoted to `impl/hy/tests/test_hy_differential.hy`, run by the root `pytest`.
- [ ] The clarifications are in SPEC.md v2 (changelog).
- [ ] Re-run at the v2 seal with `HYPOTHESIS_PROFILE=thorough TETRIS_SLOW=1`.
