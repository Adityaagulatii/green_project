# 17×9 Tetris: polyglot fork

> **New here for the "Tonight's Sky" facade animation?** See
> **[SETUP.md](SETUP.md)** for the full setup guide, including how to run it
> locally and push it live to your own hosted Green Building instance.

Tetris on MIT's Green Building, whose facade has **153 lit windows**: a 17×9 display running at 30 FPS. This fork turns the original Python game into a **language-neutral spec** and rebuilds it in turn, **Python → Hy → Clojure/ClojureScript → Elisp → Guile 3**, each rebuild held to the same conformance traces. It is also the starting kit for [Sundai Hack 140](docs/events/2026-09-13-sundai-hack-140.md) (Sun Sep 13), where the building becomes a body, and for the live demo on the building (Tue Sep 29).

- **[SPEC.md](SPEC.md).** The canonical, versioned spec. Everything else defers to it.
- **[docs/POLYGLOT-PLAN.md](docs/POLYGLOT-PLAN.md).** The rebuild plan, the seal protocol and the methodology.
- **[docs/PROCESS.md](docs/PROCESS.md).** How the project was done, stage by stage, with the commits, experiments and media behind each step.
- **[docs/events/2026-09-13-sundai-hack-140.md](docs/events/2026-09-13-sundai-hack-140.md).** The hack brief and our notes on it.
- **[spec/conformance/](spec/conformance/README.md).** The trace format, the runner and the driver protocol.
- **[spec/SEALS.md](spec/SEALS.md).** One line per sealed spec version.
- **[docs/PROTOCOL.md](docs/PROTOCOL.md).** The game-server protocol, for remote play and for displays.
- **[docs/media/](docs/media/README.md).** Snapshots, asciinema casts, the §8.4 countdown GIF and a known-answer replay.
- **[docs/FREEBSD-TETRIS.md](docs/FREEBSD-TETRIS.md).** BSD `tetris(6)` and Emacs `tetris.el` on the 9×17 board.
- **[contrib/emacs/](contrib/emacs/).** Play or drive the game from Emacs.
- **`gmake help`.** Lists the run, demo, test, lint and verify targets.
- **[experiments/](experiments/).** Investigations, such as the legacy differential, collision bounds and gravity arithmetic.

## Layout

```
SPEC.md                          the spec (v2, sealed by Hy; v1 by Python)
bin/verify.sh                    the single gate: self-test, spec appendix, every implementation vs the traces
spec/SEALS.md                    seal records
spec/conformance/                README.md, run.py (runner), gen_appendix.py (SPEC Appendix A), traces/*.json (the oracle)
impl/python/legacy/              the original tetris.py + utilities/, UNCHANGED
impl/python/engine/tetris_engine pure engine: new_game / step / render (no numpy, pygame, I/O)
impl/python/sim/tetris_sim       recorder, building model, ANSI + HTML renderers, bot, CLI, viewer
impl/python/conformance/         trace generator and mutant engines (for the gate's self-test)
impl/python/tests/               pytest + Hypothesis: P1-P19, legacy differential, sim, conformance
impl/hy/tetris_hy                pure Hy engine: init / step / render, and the conformance driver
impl/hy/tetris_hy/sim            Hy simulator: recorder, building model, ANSI renderer, bot, CLI
impl/hy/tests                    Hypothesis from Hy: P1-P19, Hy-vs-Python differential, sim, traces
impl/{clojure,guile}/            later phases
experiments/NNN-slug/            one directory per investigation
```

| Phase | Language | Status |
|---|---|---|
| 1 | Python 3.12 | engine, simulator, tests and traces; **seals spec v1** |
| 2 | Hy 1.3 | engine, simulator, tests, Hy-vs-Python differential; **seals spec v2** |
| 3 | Clojure (`.cljc`) + ClojureScript web simulator | before Sep 29 |
| 4 | Guile 3 | after |

## Quick start (Python, headless)

The only requirements are Python 3.12 and, for the tests, pytest, Hypothesis and numpy (numpy is needed only by the legacy code).

```sh
PY=/scratch/venvs/tetris-py/bin/python      # or any python3 with pytest + hypothesis + numpy
export PYTHONPATH=impl/python/engine:impl/python/sim
```

**A self-contained HTML replay** of the auto-player, drawn as the building facade. It is one file with inline canvas JS and needs no network:

```sh
$PY -m tetris_sim --seed 42 --bot --frames 900 --html out/demo.html
```

**In the terminal** (ANSI truecolor):

```sh
$PY -m tetris_sim --seed 42 --bot --frames 300 --ansi
$PY -m tetris_sim --trace spec/conformance/traces/08-hold.json --ansi-final
```

**Other options:**
- `--bot-fast`: a bot that acts every frame.
- `--animation pulse|wave`: a non-game Animation.
- `--trace-out run.json`: save any run as a trace.
- `--viewer`: the legacy pygame window. It needs pygame, which is not available in the headless jail.

**Tests and the gate:**

```sh
$PY -m pytest                    # P1-P19, legacy differential, simulator, traces
bin/verify.sh                    # verify the verifier, then every implementation vs the traces
HYPOTHESIS_PROFILE=thorough TETRIS_SLOW=1 $PY -m pytest   # the pre-seal run
```

**Traces are regenerated only for a spec revision** (see the seal protocol in the plan):

```sh
$PY impl/python/conformance/generate_traces.py
```

## Using the engine

```python
from tetris_engine import new_game, step, render

s = new_game(seed=42)                 # frames 0-90: countdown 3, 2, 1, black
for k in range(200):
    events = [("left", True)] if k == 100 else []   # (action, down) pairs
    s = step(s, events)               # one frame = 1/30 s; pure, s is immutable
    frame = render(s)                 # 17 rows x 9 cols of (r, g, b)
```

**Driving a display.** To drive any `Display` from the original code (the hack-day simulator, the pygame `DummyDisplay` or the real building), use `tetris_engine.adapter.run(display, TetrisAnimation(seed), frames, controller)`.

**Other behaviours.** Anything with `init()`, `tick(state, events)` and `render(state)` is an **Animation** (SPEC §10.3) and can run on the facade in place of the game. See `tetris_sim/animations.py`.

## Quick start (Hy, headless)

Phase 2 re-tells the engine and the simulator in [Hy](https://hylang.org) 1.3, a Lisp on the Python runtime. The engine and simulator need only Hy; the tests also need pytest and Hypothesis.

```sh
PY=/scratch/venvs/tetris-py/bin/python      # python 3.12 with hy 1.3.1, pytest and hypothesis
export PYTHONPATH=impl/hy
```

**The terminal demo** (ANSI truecolor), drawn as the facade, with each row labelled with its provisional floor:

```sh
hy -m tetris_hy.sim --seed 42 --bot --frames 300 --ansi      # or: $PY -m hy -m tetris_hy.sim ...
hy -m tetris_hy.sim --trace spec/conformance/traces/08-hold.json --ansi-final
hy -m tetris_hy.sim --building                               # the window -> floor/bay mapping
```

The other options are `--bot-fast` (a bot that acts every frame), `--plain` (a bare grid), `--speed 0` (no delay) and `--trace-out run.json` (save the run as a trace).

**Using the engine:**

```hy
(import tetris_hy [init step render])
(setv s (init 42))                         ; frames 0-90: countdown 3, 2, 1, black
(for [k (range 200)]
  (setv s (step s (if (= k 100) [#("left" True)] []))))   ; pure: s is never mutated
(render s)                                 ; 17 rows x 9 columns of #(r g b)
```

**Tests and the gate:**

```sh
$PY -m pytest impl/hy/tests       # P1-P19, the Hy-vs-Python differential, simulator, traces
$PY -m hy -m tetris_hy.conformance spec/conformance/traces/*.json    # the driver, JSON Lines
bin/verify.sh                     # runs both the Python and the Hy driver
```

### How Hy re-tells the engine

The Hy engine is not a transliteration of the Python. SPEC.md fixes the behaviour, and the Hy code is organised around Lisp data:

- **State is an immutable map.** It is a plain dict that is never mutated: `(put s :score 100)` returns a new map, and `(:score s)` reads one. Pieces are tuples `#(shape rot row col)`, and the consumed latches are a set.
- **A logical frame is a program.** The program is the queued and fresh events, then `"level-check"` and `"gravity"`, performed op by op. A lock that starts an animation stops the run and keeps the rest of the program. That is the hidden pause of §9.2: the rest runs when the animation ends.
- **Animations are a script.** A clear or a top-out sets segments such as `#(#("flash" 5) #("gameover" 218) #("countdown" 90))`. The phase is read off the current segment, and entering the countdown after a game over resets the game.
- **Rules are tables.** The gated actions are one table of latch, "release resets DCD" and effect, so QUIRK-3 and QUIRK-4 are a column. The shapes and glyphs are drawn as ASCII art, the way SPEC §4.2 and §8.4 draw them.
- **One macro, `->`,** threads a state through a pipeline. Hy 1.x has none built in, and hyrule isn't installed.
- **Gravity is binary64** because Hy floats are Python floats (§7.3).

The Hy-vs-Python differential ([experiment 005](experiments/005-hy-python-differential/)) compares the two engines frame by frame. It has found no divergence.

## Original

The text below is the upstream README by Nevin Thinagar ([Nevin-Thinagar/17x9-Tetris](https://github.com/Nevin-Thinagar/17x9-Tetris)), kept verbatim apart from heading levels. Paths such as `tetris.py` and `utilities/` now live in `impl/python/legacy/`.

### 17 x 9 Tetris
Tetris! But on a 17 x 9 grid, what an odd choice...

This game was put together in a relatively short amount of time to hit a deadline for this project. It works decently and is still fun to play, but I think there is still room for improvement both aesthetically and in terms of gameplay. Anything in the `tetris.py` and `input_manager.py` file can be changed without impacting the overall functionality of the system. Things in the other utilities can be changed as well, but this will require restructuring other parts of the system and is not the prefered method of improving the game. However, if there is a significant improvement by restructuring those systems, go for it and make a pull request!

I'm hoping that by making this public, people can play around with it, see how the system works, and overall improve the whole system! Maybe you'll even be inspired to make your own game for this kind of display! (Please feel free to do this as well! You can just reuse the display and dummy classes)

#### Things that need to be fixed
- Game loop is not performant
- Better line clear and game over animations
- Auto-repeat inputs are cancelled when a different key is pressed
- More to be added as I think of them...

#### Things that need to be added
- Lock delay
- Color change on level up
- Second window with next pieces, hold piece, score, level, and timer
- More accurate scoring
- High score tracking (partially implemented, needs visual on second window)
- Pausing the game
- Change handling and keybinds while in-game
- Scroll score after game over
- More to be added as I think of them...

#### Other notes
- If you forked this repo before the most recent commit to fix the bug with the `_playing` variable not being initialized, you will have to pull again
- The `Display.send()` function should be called *at most* at 30 FPS (i.e. Once every 0.033 seconds). The send command might get smarter at some point to handle faster send commands, but currently it is up to the game to limit the frame rate

Feel free to add any other features that you think would be useful even if they're not listed here! Just make a PR :)

Disclaimer: *This is a personal educational project and is not affiliated with, sponsored by, or endorsed by The Tetris Company*
