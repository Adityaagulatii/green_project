# 17×9 Tetris: polyglot fork

Tetris on MIT's Green Building, whose facade has **153 lit windows**: a 17×9 display running at 30 FPS. This fork turns the original Python game into a **language-neutral spec** and rebuilds it four times, **Python → Hy → Clojure/ClojureScript → Guile 3**, each rebuild held to the same conformance traces. It is also the starting kit for [Sundai Hack 140](docs/events/2026-09-13-sundai-hack-140.md) (Sun Sep 13), where the building becomes a body, and for the live demo on the building (Tue Sep 29).

- **[SPEC.md](SPEC.md).** The canonical, versioned spec. Everything else defers to it.
- **[docs/POLYGLOT-PLAN.md](docs/POLYGLOT-PLAN.md).** The rebuild plan, the seal protocol and the methodology.
- **[docs/events/2026-09-13-sundai-hack-140.md](docs/events/2026-09-13-sundai-hack-140.md).** The hack brief and our notes on it.
- **[spec/conformance/](spec/conformance/README.md).** The trace format, the runner and the driver protocol.
- **[spec/SEALS.md](spec/SEALS.md).** One line per sealed spec version.
- **[experiments/](experiments/).** Investigations, such as the legacy differential, collision bounds and gravity arithmetic.

## Layout

```
SPEC.md                          the spec (v1, sealed by Python)
bin/verify.sh                    the single gate: self-test + every implementation vs the traces
spec/SEALS.md                    seal records
spec/conformance/                README.md, run.py (runner), traces/*.json (the oracle)
impl/python/legacy/              the original tetris.py + utilities/, UNCHANGED
impl/python/engine/tetris_engine pure engine: new_game / step / render (no numpy, pygame, I/O)
impl/python/sim/tetris_sim       recorder, building model, ANSI + HTML renderers, bot, CLI, viewer
impl/python/conformance/         trace generator and mutant engines (for the gate's self-test)
impl/python/tests/               pytest + Hypothesis: P1-P19, legacy differential, sim, conformance
impl/{hy,clojure,guile}/         later phases
experiments/NNN-slug/            one directory per investigation
```

| Phase | Language | Status |
|---|---|---|
| 1 | Python 3.12 | engine, simulator, tests and traces; **seals spec v1** |
| 2 | Hy | next |
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
