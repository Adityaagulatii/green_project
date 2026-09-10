# SPEC: 17×9 Tetris for the MIT Green Building facade

**Spec v1** · sealed by the Python reference (`impl/python/engine`) · seal record: [`spec/SEALS.md`](spec/SEALS.md)

This is the one canonical document for the game, its simulator and its conformance traces. The README, the plan and every implementation defer to it: a derived document that disagrees with this file is the bug. A run that refutes this file amends it, through the protocol in [`docs/POLYGLOT-PLAN.md`](docs/POLYGLOT-PLAN.md).

## 0. Conventions

- The key words MUST, MUST NOT, REQUIRED, SHALL, SHOULD, SHOULD NOT, MAY and OPTIONAL are used as in RFC 2119.
- Sections are normative unless marked *informative*.
- **The legacy** is the original Python in `impl/python/legacy/` (`tetris.py`, `utilities/`), kept byte-for-byte unchanged. Spec v1 is derived from its behaviour. The Python differential suite (`impl/python/tests/test_differential.py`) checks that derivation.
- **`QUIRK-n`** marks a legacy behaviour that is odd, arguably a bug, or an accident of the implementation. Such behaviours are specified *as they are*. A QUIRK is part of the contract until a spec revision changes it. The index is in Appendix B.
- **`LEGACY-NONDET`** marks where the legacy depends on wall-clock time or unseeded randomness, and says how conformance mode replaces it (§13).
- Integers are unbounded unless stated otherwise. `a div b` is floor division and `a mod b` is the non-negative remainder (b > 0).
- Coordinates are `(row, col)`. Row 0 is the top; rows grow downward.
- **Re-tell, not port.** Every implementation MUST reproduce the observable behaviour defined here: frames, digests and the observation of §12. Implementations SHOULD be idiomatic for their language, not transliterations of the Python. State layout, timers, data structures and control flow are free, as long as the observables match. Pseudocode in this document fixes behaviour, not structure.

## 1. Scope and conformance classes

An **engine** is a pure function pair, `init(seed) → S` and `step(S, E) → S'`, with a pure `render(S) → Frame`. There is no I/O, clock or global randomness in the engine core. An engine conforms to Spec v1 if it reproduces every trace in `spec/conformance/traces/` exactly (§12), and its property-based tests cover P1…P19 (§11).

A **simulator** is the host around an engine or another Animation: the display sink, the recorder, renderers and input sources (§10).

## 2. Display contract

- **2.1 Frame.** A `Frame` is a grid of **17 rows × 9 columns** of `Color`, 153 cells: one per lit window of the facade.
- **2.2 Color.** A `Color` is an `(r, g, b)` triple. Each channel is an integer in `0..255`. A constructor or setter given any other value MUST convert it to an integer and clamp it into `0..255`, as legacy `Color._doset` does: `max(0, min(int(v), 255))`. `Color()` is black `(0, 0, 0)`.
- **2.3 Display.** A `Display` has two operations:
  - `makeframe()` returns a new all-black 17×9 Frame.
  - `send(frame)` shows the frame from the next frame start.

  A frame producer MUST NOT call `send` more often than **30 times per second (30 FPS)**. One *frame* is 1/30 s.
- **2.4 Transport.** The Frame's in-memory representation is not normative. The legacy uses a numpy object array of `Color`, while the engines use nested tuples or vectors. Only the cell values, the dimensions and the row-major order are.

## 3. Board

- **3.1 The padded board.** The game keeps a padded board `B` of **20 rows × 11 columns**. That is the display plus 3 rows and 2 columns:

  | Rows / columns | Content |
  |---|---|
  | row 0 | white **top border** |
  | rows 1–17 | the playfield (display rows 0–16) |
  | rows 18–19 | two white **floor** rows |
  | col 0, col 10 | white side borders |
  | cols 1–9 | playfield columns (display columns 0–8) |

- **3.2 Cells.** Each cell is empty or holds a color code (§4.1).
  - The empty board has `W` in every border cell and empty cells everywhere else.
  - Border cells are *solid* for collision.
  - Cells in rows 18–19 and columns 0/10 MUST stay `W` for ever.
  - Row 0 cells stay non-empty. They may, however, take a piece color (QUIRK-18).
- **3.3 Display mapping.** Display cell `(r, c)` is board cell `(r+1, c+1)`, so the display is `B[1..17][1..9]`.

## 4. Pieces

### 4.1 Palette

| Code | Meaning | RGB |
|---|---|---|
| `.` | empty / black | (0, 0, 0) |
| `W` | white: borders, the line-clear flash, the game-over fill, countdown digits | (255, 255, 255) |
| `I` | cyan | (0, 255, 255) |
| `J` | blue | (0, 0, 255) |
| `L` | orange | (255, 170, 0) |
| `O` | yellow | (255, 255, 0) |
| `S` | green | (0, 255, 0) |
| `Z` | red | (255, 0, 0) |
| `T` | purple | (153, 0, 255) |
| `G` | ghost gray (render only, never stored in the board) | (42, 42, 42) |

A piece's cells carry the code of its shape. No other colors appear in any frame.

### 4.2 Shapes and rotation states

Each shape has 4 rotation states `0..3`, each drawn in a 4×4 box. The box rows are listed top to bottom and `#` marks an occupied cell. Rotation `r+1 (mod 4)` is clockwise from `r`. The tables are exactly the legacy `SHAPES`. Note that they are **not** the standard SRS placements: for example, `O` drifts inside its box and `I` sits in rows 1 and 2.

```
I: 0 ....  1 ..#.  2 ....  3 .#..      J: 0 ....  1 ....  2 ....  3 ....
     ####    ..#.    ....    .#..           .#..    ..##    ....    ..#.
     ....    ..#.    ####    .#..           .###    ..#.    .###    ..#.
     ....    ..#.    ....    .#..           ....    ..#.    ...#    .##.

L: 0 ....  1 ....  2 ....  3 ....      O: 0 ....  1 ....  2 ....  3 ....
     ...#    ..#.    ....    .##.           .##.    ....    ....    ##..
     .###    ..#.    .###    ..#.           .##.    .##.    ##..    ##..
     ....    ..##    .#..    ..#.           ....    .##.    ##..    ....

S: 0 ....  1 ....  2 ....  3 ....      Z: 0 ....  1 ....  2 ....  3 ....
     ..##    ..#.    ....    .#..           .##.    ...#    ....    ..#.
     .##.    ..##    ..##    .##.           ..##    ..##    .##.    .##.
     ....    ...#    .##.    ..#.           ....    ..#.    ..##    .#..

T: 0 ....  1 ....  2 ....  3 ....
     ..#.    ..#.    ....    ..#.
     .###    ..##    .###    .##.
     ....    ..#.    ..#.    ..#.
```

### 4.3 Position and spawn

A piece is `(shape, rot, row, col)`. `(row, col)` is the board cell of its box's top-left corner, so occupied box cell `(i, j)` sits at board cell `(row+i, col+j)`. A piece **spawns** in rotation 0 at **`(0, 3)`** in padded coordinates.

### 4.4 Collision

A piece `collides` with `B` if any of its occupied cells is outside `0 ≤ row < 20, 0 ≤ col < 11`, or lands on a non-empty cell of `B`.

> *Note.* The legacy indexes a numpy array, where negative indices wrap around. Experiment [`002-collision-bounds`](experiments/002-collision-bounds/) shows that the "outside collides" rule gives identical results for every candidate the rules can generate.

## 5. Input and moves

### 5.1 Actions

Input is a sequence of **events** `(action, down)`: `down = true` is a press and `false` is a release. The actions are:

`left`, `right`, `soft_drop`, `hard_drop`, `rotate_cw`, `rotate_ccw`, `rotate_180`, `hold`

A front end MAY synthesize auto-repeat by repeating `down` events (§14). The engine itself never auto-repeats.

### 5.2 Shifts and soft drop

- `left` / `right` with `down`: if the active piece shifted by −1 / +1 column does not collide, shift it. A release does nothing. Shifts have no latch and are not gated by DCD.
- `soft_drop` with `down`: **move down** (§6.1). A release does nothing. Soft drop is not gated.
- The legacy soft-drop factor (SDF 6) is unused (QUIRK-14).

### 5.3 Rotation with kicks

A rotation has a *source* state `r`, a *target* state `n` and two 5-entry offset tables `src` and `dst`, each entry an `(x, y)` pair with **y pointing up**:

| Action | n | src | dst |
|---|---|---|---|
| `rotate_cw` | (r+1) mod 4 | `KICKS[shape][r]` | `KICKS[shape][n]` |
| `rotate_ccw` | (r−1) mod 4 | `KICKS[shape][r]` | `KICKS[shape][n]` |
| `rotate_180` | (r+2) mod 4 | **`KICKS_180[shape][r]`** | **`KICKS[shape][n]`** |

The piece is tried at each test `i = 0..4` in order:
- `dx = src[i].x − dst[i].x`, `dy = src[i].y − dst[i].y`;
- the candidate is `(shape, n, row − dy, col + dx)`.

The first candidate that does not collide becomes the active piece, and the DCD counter is set to 0. If all five collide, nothing changes. This is the SRS "offset table" method.

**QUIRK-1.** For 180° rotation the source offsets come from `KICKS_180`, but the target offsets come from the ordinary `KICKS` table. The legacy's 180° table was, in its author's words, guessed.

`KICKS` (J, L, S, T and Z share one table):

| state | J L S T Z | I | O |
|---|---|---|---|
| 0 | (0,0) (0,0) (0,0) (0,0) (0,0) | (0,0) (−1,0) (2,0) (−1,0) (2,0) | (0,0) ×5 |
| 1 | (0,0) (1,0) (1,−1) (0,2) (1,2) | (0,0) (1,0) (1,0) (1,1) (1,−2) | (0,−1) ×5 |
| 2 | (0,0) (0,0) (0,0) (0,0) (0,0) | (0,0) (2,0) (−1,0) (2,−1) (−1,−1) | (−1,−1) ×5 |
| 3 | (0,0) (−1,0) (−1,−1) (0,2) (−1,2) | (0,0) (0,0) (0,0) (0,−2) (0,1) | (−1,0) ×5 |

`KICKS_180`:

| state | J L S T Z | I | O |
|---|---|---|---|
| 0 | (0,0) (0,1) (1,0) (−1,0) (0,−1) | (0,0) (0,0) (−1,0) (2,0) (−1,1) | (0,0) ×5 |
| 1 | (0,0) (1,0) (0,1) (0,−1) (−1,0) | (0,0) (0,0) (0,1) (0,−2) (1,1) | (0,−1) ×5 |
| 2 | (0,0) ×5 | (0,0) ×5 | (−1,−1) ×5 |
| 3 | (0,0) ×5 | (0,0) ×5 | (−1,0) ×5 |

### 5.4 DCD counter and latches

The engine keeps an integer **DCD counter** `dcd`, with `DCD = 2`, and four **latches** that are each *available* or *consumed*: `cw`, `ccw`, `r180` and `hard`.

- At the start of every logical frame, `dcd := dcd + 2` (§9.2). A new game starts with `dcd = 2`, except for QUIRK-11.
- **Press of a rotation, or of `hard_drop`, while its latch is available.** The latch becomes consumed. Then:
  - if `dcd ≥ DCD`, the rotation is attempted (§5.3) or the hard drop is performed (§5.5);
  - otherwise the press is swallowed. The latch stays consumed even when the action is blocked or no kick fits (**QUIRK-2**).
- **Press while the latch is consumed.** Nothing happens.
- **Release while the latch is consumed.** The latch becomes available.
  - For `rotate_ccw` the release also sets `dcd := 0` (**QUIRK-3**), which `rotate_cw` and `rotate_180` releases do not do.
  - For `hard_drop` the release also sets `dcd := 0` (**QUIRK-4**).
- **Release while the latch is available.** Nothing happens.
- A successful rotation and a hard drop set `dcd := 0`.

The counter rises by 2 per frame against a threshold of 2. So in practice, after any action that resets it, **rotations and hard drop are blocked for the rest of that logical frame** (**QUIRK-15**). Because a release resets `dcd`, a release and a new press of `hard_drop` (or `rotate_ccw`) in the same logical frame always swallow the press.

### 5.5 Hard drop

If allowed (§5.4), the piece moves down while the next row does not collide, and then **locks** (§6.1). After the lock, spawn and game-over check, `hold_available := true` and `dcd := 0`.

### 5.6 Hold

`hold` with `down`, while `hold_available` is true:
- **Hold empty.** The active piece `(s, r, …)` goes to hold as `(s, r)`, and the next piece is drawn from the bag (§9.3) at spawn.
- **Hold occupied.** Hold `(hs, hr)` and the active piece swap. The new active piece is `(hs, hr, 0, 3)`, and hold becomes `(s, r)`.

In both cases `hold_available := false`. It becomes true again when a piece locks.

**QUIRK-6.** A held piece keeps its rotation state, and only its position is reset to the spawn cell. Neither a hold-spawn nor a swap-in is collision-checked: the active piece may overlap the board, and even the top border row. It then moves normally, since moves check only their candidate, and it locks where it stands if moving down is blocked.

A `hold` press with `hold_available` false, and any `hold` release, does nothing. Hold has no latch and no DCD gate.

## 6. Locking, line clears and scoring

### 6.1 Move down and lock

**Move down** means: if the piece one row lower does not collide, move it; otherwise **lock** it where it stands. There is **no lock delay** in v1 (**QUIRK-14**): a piece locks at the first blocked downward move, whether that comes from gravity, a soft drop or a hard drop. The legacy lock-delay fields are never used.

**Lock**:

1. Write the piece's cells into `B` with its shape code. This overwrites whatever is there (QUIRK-6, QUIRK-18).
2. **Clear lines.** A row `r ∈ 1..17` is *full* if every cell in columns 1–9 is non-empty.
   - If any row is full, remember the **flash board**: `B` with every full row entirely `W`. The display shows it for 5 frames (§8.2).
   - Then, for each full row in ascending order: delete it, insert an empty playfield row at row 1 (row 0 is untouched), and score it (§6.2).
3. **Spawn.** Draw the next piece (§9.3) at spawn and set `hold_available := true`.
4. **Game over.** If the new piece collides, the game is over. Set `high_score := max(high_score, score)`, remember the board as the game-over board, and start the game-over sequence (§8.3). If the lock came from a hard drop, the new game will start with `dcd = 0`; otherwise it starts with `dcd = 2` (**QUIRK-11**).

The clear and the spawn take effect in the state immediately. The flash and game-over sequences only change what is *displayed*, and when the rest of the logical frame runs (§9.2).

### 6.2 Scoring

The line counter `lines` is the number of lines cleared *on the current level*.

**QUIRK-5.** Each cleared row does `lines := lines + 1; score := score + 100 × (lines div 10 + 1)`, one row at a time, in the order of §6.1. So a lock that clears `n` rows starting from counter `L` scores Σ_{k=1..n} 100·((L+k) div 10 + 1). There is no bonus for multiple lines and no dependence on level.

`high_score` survives game-over resets for the lifetime of the engine.

## 7. Levels and gravity

- **7.1 Level target.** At the level-check stage of each logical frame (§9.2), with `target(L) = min(L + 5, max(100, 5·L − 50))`:
  ```
  if target(level) ≤ lines:
      level := level + 1
      lines := lines − target
  ```
  At most one level is gained per logical frame (QUIRK-16).

  **QUIRK-7.** This is the legacy's "faster level progression for testing" formula. For every `level ≥ 0` it equals `level + 5`.
- **7.2 Gravity table.** `GRAVITY[L] = 1 / DEN[min(L, 29)]`, where `DEN` is:

  | levels | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10–12 | 13–15 | 16–18 | 19–28 | 29+ |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | DEN | 48 | 43 | 38 | 33 | 28 | 23 | 18 | 13 | 8 | 6 | 5 | 4 | 3 | 2 | 1 |
- **7.3 Accumulator.** The gravity accumulator `acc` is an **IEEE 754 binary64** value with round-to-nearest-even, starting at `+0.0`. At each logical frame:
  - `acc := acc + inc(level)`, where `inc(L) = (1.0 / DEN[min(L,29)]) × 2.0`, computed in binary64. This is legacy `self._gravity * 2`: the legacy ran "at half FPS", hence the × 2.
  - At the gravity stage: `if acc ≥ 1.0: acc := 0.0`, then **move down** (§6.1).

  Implementations MUST use binary64 arithmetic here, not exact rationals: for example, Guile MUST use inexact reals. **QUIRK-8.** The accumulator resets to 0 instead of subtracting 1, it moves at most one row per frame, and binary64 rounding makes some cadences one frame slower than the rational value. From `acc = 0` at a constant level, the frames between drops `N(L)` are:

  | DEN | 48 | 43 | 38 | 33 | 28 | 23 | 18 | 13 | 8 | 6 | 5 | 4 | 3 | 2 | 1 |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
  | N | **25** | 22 | **20** | 17 | **15** | 12 | 9 | 7 | 4 | 3 | 3 | 2 | 2 | 1 | 1 |

  The bold entries are one frame more than ⌈DEN/2⌉.
- **7.4 Level changes.** The accumulator keeps its value when the level changes, and later increments use the new level. A new game resets the level to 0 but does not reset `acc` (QUIRK-17). The only time `acc` resets is the drop itself: a game over triggered by gravity therefore leaves `acc = 0`, and one triggered by input leaves it unchanged.

## 8. Frame composition and animations

The frame shown after a step depends on the phase (§9.1).

### 8.1 Playing

Composition is exactly this, in order:

1. Start from `B`.
2. Paint the **ghost** in gray `G`. The ghost is the active piece moved down while the next row does not collide, starting from its current row. So if the piece already collides, the ghost starts where it is.
3. Paint the **active piece** in its code, over the ghost and over the board.
4. **Crop** to `B[1..17][1..9]` (§3.3).

The ghost is always on in v1.

### 8.2 Line-clear flash

For **5 frames**, the frame is the cropped **flash board** (§6.1): full rows white, the locked piece included, no ghost and no active piece. After that, play resumes with the post-clear board (§9.2).

### 8.3 Game-over sequence

This is **218 frames**, followed by the countdown. With `Bg` the game-over board and `t = 0..217` the frame index within the sequence:

| t | Frame (display rows 0–16 = board rows 1–17) |
|---|---|
| 0 ≤ t < 34 | **fill-up**: with `k = t div 2`, board rows `17−k … 17` are all white, and the rows above show `Bg` |
| 34 ≤ t < 184 | **wait**: all white (150 frames = 5 s) |
| 184 ≤ t < 218 | **fall-down**: with `k = (t−184) div 2`, board rows `1 … 1+k` are black and the rows below are white. Rows go dark from the top (QUIRK-19). |

Then the game **resets**:
- `B` is emptied;
- a new bag is drawn from the *continuing* PRNG stream (§9.3), and its first piece spawns;
- hold is emptied and `hold_available := true`;
- `level := 0`, `lines := 0`, `score := 0`;
- all latches become available;
- `dcd` is set by QUIRK-11;
- the input queue is cleared.

`high_score`, `acc` and the PRNG state carry over. The countdown (§8.4) follows without the black frame, and then the suspended logical frame resumes (§9.2).

### 8.4 Countdown

Each digit is shown for **30 frames**: `3`, then `2`, then `1` (90 frames). At boot a **black** frame follows (1 frame). After a game over, the black frame is superseded in the same slot by the resumed play frame (**QUIRK-10**). The glyphs are 17×9, with `#` = `W`:

```
row  "3"        "2"        "1"
 0-2 .........  .........  .........
 3   ..#####..  ..#####..  ....##...
 4   ..#####..  ..#####..  ....##...
 5   .....##..  .....##..  ....##...
 6   .....##..  .....##..  ....##...
 7   ..#####..  ..#####..  ....##...
 8   ..#####..  ..#####..  ....##...
 9   .....##..  ..##.....  ....##...
10   .....##..  ..##.....  ....##...
11   ..#####..  ..#####..  ....##...
12   ..#####..  ..#####..  ....##...
13-16 .........  .........  .........
```

**QUIRK-9.** The legacy glyph arrays have 19 rows and are sent as is. Their rows 17–18 are black and fall outside the 17-row display. The spec frame is their first 17 rows.

## 9. Conformance mode (determinism)

### 9.1 Frames, phases and the initial state

Time is a sequence of **frames** `k = 0, 1, 2, …` at 30 FPS. A run is:

```
S_0     = init(seed)
S_{k+1} = step(S_k, E_k)
frame k = render(S_{k+1})
```

`E_k` is the ordered list of events delivered at frame `k`. The engine is in one of four phases: **countdown**, **playing**, **clearing** (the flash) or **gameover** (fill, wait and fall).

`init(seed)` gives:
- phase countdown, before its first frame (the boot variant, with the black frame);
- an empty board;
- the PRNG seeded (§9.3), the first bag drawn, and its first piece active at spawn;
- hold empty and `hold_available = true`;
- `level = lines = score = high_score = 0`;
- `acc = 0.0`;
- `dcd = 2`;
- all latches available;
- an empty input queue, and no suspended frame.

The boot sequence is therefore frames 0–89 (the countdown) and frame 90 (black). **Frame 91 is the first logical play frame.**

### 9.2 The step

A **logical frame** is one iteration of the legacy play loop. It runs these stages in order:

1. `acc := acc + inc(level)` and `dcd := dcd + 2`.
2. Take the batch = queued events ++ `E_k`, and empty the queue. Apply each event in order (§5).
3. The level check (§7.1).
4. Gravity (§7.3).
5. Render.

**Suspension.** A lock may start a flash (§8.2) or a game-over sequence (§8.3), whether the lock came from an event or from gravity. When it does, the logical frame is **suspended** at that point: this frame shows the first frame of the animation. When the animation ends, the frame **resumes** where it stopped, without repeating stage 1:
- if the lock came from event `i` of the batch, events `i+1…` run, then stages 3 and 4, then the render;
- if it came from gravity, just the render.

A resumed frame can suspend again. After a game over, the resumed events apply to the *new* game (QUIRK-13).

Events delivered while suspended (`E_k` during an animation):
- **during a flash**, and on the frame the flash resumes, they are **queued**. They are applied at the beginning of the next fresh logical frame, before that frame's own events.
- **during a game over or a countdown** they are **discarded** (QUIRK-12). So is anything already queued.

Normative pseudocode:

```
step(S, E):
  case S.phase
  countdown:
    t := timer + 1
    if t < 90 or (boot and t = 90): timer := t; return        -- E discarded
    phase := playing
    if boot: boot := false; LOGICAL(E)
    else:    queue := queue ++ E; RESUME
  clearing:
    queue := queue ++ E; t := timer − 1                       -- timer starts at 5
    if t > 0: timer := t; return
    if gameover pending: phase := gameover; timer := 0; return
    phase := playing; RESUME
  gameover:
    t := timer + 1                                            -- timer starts at 0
    if t < 218: timer := t; return                            -- E discarded
    RESET (§8.3); phase := countdown; timer := 0; boot := false
  playing:
    LOGICAL(E)

LOGICAL(E):  acc := acc + inc(level); dcd := dcd + 2
             batch := queue ++ E; queue := []; RUN(batch, true)
RESUME:      (ev, st) := suspended; suspended := none; RUN(ev, st)
RUN(ev, st): for i in 0..|ev|−1:
               apply ev[i]
               if phase ≠ playing: suspended := (ev[i+1..], st); return
             if not st: return
             level check (§7.1)
             if acc ≥ 1.0:
               acc := 0.0; move down (§6.1)
               if phase ≠ playing: suspended := ([], false)
```

A lock that both clears lines and tops out enters **clearing** with the game over pending: 5 flash frames, then the 218-frame sequence, then the countdown, then the resume.

Event order within a frame is the order in `E_k`. Events for frames earlier than the current one never exist, since traces are sorted by frame.

### 9.3 PRNG and bag

The PRNG is **xorshift32** (Marsaglia; shifts 13, 17, 5) on an unsigned 32-bit state `x`:

```
seed_state(seed) = seed mod 2^32, or 0x9E3779B9 if that is 0
next(x):  x := x XOR ((x << 13) mod 2^32)
          x := x XOR (x >> 17)
          x := x XOR ((x << 5)  mod 2^32)
          return x                  -- the new state is also the output
```

A **bag** is a Fisher–Yates shuffle of the canonical order **`I J L O S Z T`**:

```
bag := [I, J, L, O, S, Z, T]
for i from 6 down to 1:
    x := next(x); j := x mod (i + 1); swap bag[i], bag[j]
```

Each bag consumes exactly 6 PRNG outputs. Pieces are drawn in bag order.
- When the 7 pieces of a bag are used up, the **next draw** shuffles a new bag. The refill is lazy.
- `init` shuffles the first bag and draws its first piece.
- The game-over reset shuffles a fresh bag immediately and draws its first piece, discarding what was left of the old bag.
- A hold with an empty hold slot draws a piece.

The PRNG state is never reseeded. **LEGACY-NONDET:** the legacy uses unseeded `np.random.shuffle`. The differential suite forces the legacy bag onto this procedure.

### 9.4 Frame digest

`digest(frame)` is the lowercase hex **SHA-256** of the frame's **459 bytes**: the cells in row-major order (row 0 left to right, then row 1, and so on), each written as three bytes `R, G, B`.

## 10. Simulator

- **10.1 Scope.** A simulator hosts frame producers and renders their frames. It MUST be runnable **headless**: no window system, no pygame, no audio.
- **10.2 Headless sink and recorder.** A sink implements the Display contract (§2.3). A **recorder** is a sink that keeps every frame it is sent, in order. The timestamp of frame `k` is `k / 30` seconds. Renderers (terminal, HTML, web canvas) and tests read from a recorder, never from the engine directly. A recorder SHOULD also log the input events, as `[frame, action, down]`, so that any run can be saved as a trace (§12).
- **10.3 Animation interface.** An **Animation** is any frame producer:

  ```
  init() → state
  tick(state, events) → state      -- pure; one call per frame
  render(state) → Frame
  ```

  The game engine is one Animation, with `init = init(seed)`, `tick = step` and `render = render`. Embodied behaviours are others: gestures in response to people, mood colors, "world transitions". The line-clear flash, the game over and the countdown are just parts of the game's frame stream. A host that drives a real Display MUST pace `tick` + `send` at 30 FPS.
- **10.4 Building model (TBD).** The facade has **153 windows**: 17 lit floors × 9 bays of MIT's Green Building (Building 54, 21 stories). The mapping from display row to floor and from display column to bay is **TBD**. It is to be confirmed at the hack on 2026-09-13 and then fixed here in a spec revision. Until then, simulators SHOULD use this **provisional** mapping and label it as such: display row `r` → floor `20 − r` (rows 0–16 → floors 20–4), display column `c` → bay `c + 1`, counted left to right as seen by the viewer.

## 11. Properties

Every implementation's property-based tests MUST cover P1…P19, with generated seeds and generated event sequences (with events in random frames, including presses without releases, releases without presses, and all eight actions). "Within a game" means between two resets.

- **P1 Frame contract.** Every rendered frame is 17×9, every channel is an integer in 0..255, and every color is in the palette (§4.1).
- **P2 Purity and determinism.** `step` never mutates its input. The same seed and events always give the same frames and digests.
- **P3 Board frame.** `B` is 20×11. Rows 18–19 and columns 0 and 10 are always `W`, and row 0 has no empty cell.
- **P4 Legal active piece.** While playing, the active piece's cells lie inside the padded board. The active piece does not collide with `B` unless a `hold` placed it there since the last lock (QUIRK-6).
- **P5 No full rows at rest.** After every step, no row 1–17 of `B` is full.
- **P6 7-bag.** Within a game, the sequence of pieces drawn splits into consecutive bags, each a permutation of `I J L O S Z T`. The first piece of every game starts a new bag.
- **P7 Gravity cadence.** With no input at constant level `L` and `acc = 0` at a fresh logical frame, the active piece descends exactly one row every `N(L)` frames (§7.3) until it lands.
- **P8 Hard drop lands on the ghost.** A successful hard drop locks the piece exactly on the ghost cells rendered for the previous frame, before any line clear.
- **P9 Ghost geometry.** In playing frames, the gray cells are exactly the active piece's cells moved down to their drop row, minus the cells the active piece covers.
- **P10 Shift inverse.** If a `left` press moves the active piece, a following `right` press restores its previous position and rotation, and vice versa.
- **P11 One gated action per logical frame.** At most one successful rotation or hard drop happens in any logical frame, including its resumption.
- **P12 Latches.** A second press of a rotation or of `hard_drop` with no release in between has no effect.
- **P13 Scoring.** The score changes only when rows clear, and a clear of `n` rows from counter `L` adds Σ_{k=1..n} 100·((L+k) div 10 + 1). Within a game the score is a multiple of 100 and never decreases.
- **P14 Levels.** Within a game the level never decreases, and it rises by at most 1 per logical frame. `lines ≥ 0` always.
- **P15 Hold.** There is at most one hold per locked piece. Hold keeps shape and rotation, and a swapped-in piece appears at the spawn cell.
- **P16 Animation lengths.** A clear shows exactly 5 flash frames. A game over shows exactly 34 + 150 + 34 frames, then 90 countdown frames. Boot is 90 countdown frames and 1 black frame.
- **P17 Reset.** After the game-over sequence: score, level and lines are 0, hold is empty, the board is empty, `high_score = max(old high_score, final score)`, and the next game's pieces come from a fresh bag.
- **P18 Suspended input.** Events delivered during a flash take effect after it, in order. Events delivered during a game over or a countdown have no effect.
- **P19 Known answers.** The PRNG, bag and digest vectors in Appendix A reproduce exactly.

## 12. Conformance traces

Traces live in `spec/conformance/traces/*.json`. They are the **oracle**, generated by the sealing implementation and never edited by hand (see `spec/conformance/README.md` for the driver protocol and the runner). Each file is one JSON object:

| Field | Type | Meaning |
|---|---|---|
| `format` | `"17x9-tetris-trace"` | fixed |
| `spec_version` | int | the spec version the trace was sealed under |
| `name` | string | equal to the file name without `.json` |
| `description` | string | informative |
| `covers` | [string] | informative: spec sections, QUIRKs and properties exercised |
| `seed` | int | PRNG seed (§9.3), in `0 ≤ seed < 2^32` |
| `frames` | int > 0 | number of steps to run |
| `digest_every` | int > 0 | `d` |
| `events` | [[int, string, bool]] | `[frame, action, down]`, sorted by frame. Order within a frame is significant. |
| `digests` | [string] | digests (§9.4) of frames `0, d, 2d, …` below `frames`, followed by the digest of frame `frames − 1` if it is not already included |
| `final` | object | the observation of `S_frames` |

The **observation** `final` is:

```
{ "phase": "countdown" | "playing" | "clearing" | "gameover",
  "score": int, "level": int, "lines": int, "high_score": int,
  "active": {"shape": "I".."T", "rotation": 0..3, "row": int, "col": int},
  "hold": null | {"shape": ..., "rotation": 0..3},
  "frame_hex": the 459 bytes of the final frame as lowercase hex }
```

An implementation **passes** a trace if its digests and its observation are all equal to the trace's. The gate `bin/verify.sh` runs every implementation against every trace. It passes only with **zero findings**; there is no warning tier.

## 13. Legacy nondeterminism and wall-clock mapping (informative)

| Legacy behaviour | LEGACY-NONDET | Conformance mode |
|---|---|---|
| `np.random.shuffle` with an unseeded global RNG | the piece order | xorshift32 + Fisher–Yates (§9.3) |
| The frame loop busy-waits on `time.perf_counter`, and events are processed in 1 ms subticks (`MODERN_SIMULATION_RATE = 1000`) | which frame an input lands in; batch boundaries | explicit `(frame, action, down)` events, applied at frame boundaries in order (§9.2) |
| `fractionalPosition += gravity * 2` per loop ("half FPS") | none (float, but deterministic) | binary64 accumulator (§7.3) |
| The line-clear flash busy-waits 5/30 s inside the handler; events queue in pygame meanwhile | the batch split | 5 flash frames, suspension and queueing (§8.2, §9.2) |
| Countdown and game-over waits use `time.sleep(1)` and 2/30 s busy-waits; `pygame.event.clear()` runs after the countdown | how long each frame is shown | exact frame counts (§8.3, §8.4); input discarded (QUIRK-12) |
| A `keyboard.is_pressed('q')` poll during the game-over wait quits the game | host keyboard state | not modelled: quitting belongs to the host |
| Auto-repeat comes from `pygame.key.set_repeat(99 ms, 66 ms)` (DAS 3, ARR 2 frames); only the last key held repeats | OS/pygame timing | not in the engine; front ends MAY synthesize repeats (§14) |
| The countdown sends 19-row arrays | none | first 17 rows (QUIRK-9) |
| `self.time` counts down from 300 s and is never read | none | not modelled |
| `GLOBAL_STATE` and `second_screen()` (tkinter) share score, high score and hold with a second window; the thread is commented out | none | not part of v1; a host MAY show them from the §12 observation |
| Collision on the numpy array wraps negative indices | none | "outside collides" (§4.4); equal for every reachable candidate |

## 14. Handling, bindings and auto-repeat (informative)

The legacy `HANDLING` table is ARR 2, DAS 3, DCD 2 and SDF 6, in frames. Only DCD is used by the game logic (§5.4). DAS and ARR reach it only through pygame key repeat, and SDF is unused.

A front end that wants the legacy feel SHOULD repeat a held key's `down` event after 3 frames, then every 2 frames, and stop repeating when another key is pressed.

The legacy keyboard bindings:

| Key | Action |
|---|---|
| ↑, `c` | `rotate_cw` |
| `x` | `rotate_180` |
| Left Ctrl, `z` | `rotate_ccw` |
| Space | `hard_drop` |
| ↓ | `soft_drop` |
| ← / → | `left` / `right` |
| Left Shift | `hold` |

The legacy controller bindings:

| Button | Action |
|---|---|
| D-pad up | `hard_drop` |
| D-pad down | `soft_drop` |
| D-pad left / right | `left` / `right` |
| A | `rotate_cw` |
| B | `rotate_ccw` |
| Shoulders, Y | `hold` |

The joystick axis (±0.5) maps to the same moves. Note that the legacy `InputManager` looks up an axis by `event.axis`, but then dispatches through `event.type`.

## Appendix A. Known-answer vectors (normative)

- **xorshift32.** Outputs of `next` from `seed_state(seed)`:
  - seed `1`: 270369, 67634689, 2647435461, 307599695, 2398689233
  - seed `42`: 11355432, 2836018348, 476557059, 3648046016, 3759983556
  - seed `0` (state `0x9E3779B9`): 1359758873, 3761132862, 2075758394, 25405621, 3862129951
- **Bags.** The first three bags:
  - seed `1`: `SILOZTJ`, `LJOZSTI`, `ZIOJSLT`
  - seed `42`: `JLOIZTS`, `ILJOZST`, `OZITJLS`
  - seed `0`: `ZLOJTIS`, `ZOTSIJL`, `JITOLSZ`
- **Digests.**
  - all-black frame: `e0ee29ce7978a33861e6e63545deda9e734ea784ee8e4ba6fd6aa56b775f6ca9`
  - all-white frame: `cc1c8c603a0863247abc4b8a117a234714b37d2be10ca27218301e5e617830d8`

## Appendix B. QUIRK index

| QUIRK | Summary | § |
|---|---|---|
| 1 | 180° kicks mix `KICKS_180` (source) with `KICKS` (target) | 5.3 |
| 2 | Rotation and hard-drop latches are consumed even if the action is blocked by DCD or no kick fits | 5.4 |
| 3 | A `rotate_ccw` release resets DCD; `rotate_cw` and `rotate_180` releases don't | 5.4 |
| 4 | A `hard_drop` release resets DCD | 5.4 |
| 5 | Score `100 × (lines div 10 + 1)` per row, with the per-level counter, accumulated row by row | 6.2 |
| 6 | Hold keeps rotation; hold-spawn and swap-in aren't collision-checked | 5.6 |
| 7 | Level target `min(L+5, max(100, 5L−50))` (= L+5) | 7.1 |
| 8 | Gravity: binary64 accumulator reset to 0, one row per frame at most, level 0 = 25 frames | 7.3 |
| 9 | Countdown glyphs are 19 rows in legacy; the spec uses the first 17 | 8.4 |
| 10 | The post-game-over countdown shows no black frame; boot shows one | 8.4 |
| 11 | A game over from a hard drop starts the new game with `dcd = 0` | 6.1 |
| 12 | Input during countdown and game over is discarded; input during a flash is queued | 9.2 |
| 13 | A suspended logical frame resumes after the animation, even into a new game | 9.2 |
| 14 | No lock delay; soft drop into the stack locks immediately; SDF unused | 6.1 |
| 15 | DCD +2 per frame vs threshold 2: gated actions blocked only for the rest of the frame | 5.4 |
| 16 | At most one level per logical frame; the level check runs before gravity | 7.1 |
| 17 | `acc` survives a game over triggered by input | 7.4 |
| 18 | A piece locked while overlapping row 0 writes its color into the top border | 3.2 |
| 19 | The game-over "fall down" darkens rows from the top | 8.3 |

## Changelog

- **v1** (2026-09-10, Python). First sealed spec. Derived from the legacy by reading it and by differential testing against it: zero divergences over Hypothesis-generated and bot-driven action sequences (experiment 001). It defines conformance mode, 19 properties and the trace format.
