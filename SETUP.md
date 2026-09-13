# Tonight's Sky — setup guide

This repo is a working copy of [aygp-dr/17x9-Tetris](https://github.com/aygp-dr/17x9-Tetris)
(the starting kit for Sundai Hack 140) plus our team's addition: **Tonight's
Sky**, a fully pre-scripted, non-interactive light sequence for the 17×9
Green Building facade. A fixed field of ambient "stars" cycles through named
constellations — some invented (MIT/hack-themed), one real (the Pleiades) —
each fading in, holding, and fading out on a loop. No camera, no microphone,
no live sensing: everything is choreographed in advance.

This doc gets a new collaborator from a fresh clone to watching it run, both
locally and live on the hosted building simulator.

## 1. Prerequisites

- Git
- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (recommended — the project is set up
  for it and needs no separate install step). Plain `pip` also works; see
  the fallback notes below.

## 2. Clone and sanity-check the engine

```
git clone https://github.com/Adityaagulatii/green_project.git
cd green_project
uv run python -m tetris_sim --seed 1 --bot --frames 300 --ansi
```

That plays the actual Tetris game headlessly with a demo bot, animated in
your terminal. If that works, the engine and simulator are set up correctly.
(`uv run` installs the project automatically on first use — no `uv sync`
needed first.)

## 3. Run Tonight's Sky locally

The animation lives at
[impl/python/sim/tetris_sim/sky.py](impl/python/sim/tetris_sim/sky.py) and is
registered in the simulator as `--animation sky`.

**Terminal playback** (one full 5-constellation loop is 1500 frames at 30fps,
~50s; `--speed` fast-forwards):

```
uv run python -m tetris_sim --animation sky --frames 1500 --ansi --speed 4
```

**Browser replay** (scrubbable, with a facade/grid view toggle):

```
uv run python -m tetris_sim --animation sky --frames 1500 --html out/sky_demo.html
```

Open `out/sky_demo.html` in a browser.

### Tuning

Everything about the shapes and timing is a constant at the top of
`sky.py`:

- `STAR_FIELD` — the fixed grid coordinates (never move once picked).
- `CONSTELLATIONS` — which `STAR_FIELD` indices belong to each named shape,
  and the order connecting lines are drawn in (empty list = a cluster, like
  the Pleiades).
- `FADE_FRAMES` / `HOLD_FRAMES` / `FADEOUT_FRAMES` — per-constellation timing.
- `STAR_COLOR`, `AMBIENT_FRAC`, `LINE_SET_FRAC`, `FULL_FRAC` — brightness/color.

Edit, then re-run one of the commands above to see the change.

## 4. Go live on the hosted Green Building simulator

Sundai also runs a hosted web simulator
([sundai.willsarg.com](https://sundai.willsarg.com)) that shows your frames
on a live page anyone can watch, mimicking the real building. That client
(MIT-licensed, from
[willsarg/sundai-greenbuilding-sim](https://github.com/willsarg/sundai-greenbuilding-sim))
is vendored into this repo under
[contrib/greenbuilding-live/](contrib/greenbuilding-live/), along with a
script that renders Tonight's Sky and uploads it as a looping clip.

**Steps:**

1. **Get your own instance.** Every collaborator needs their own — instances
   are not shared. Open <https://sundai.willsarg.com>, enter the event
   password, click Create. You land on a viewer page — note the
   adjective-animal name in its URL (e.g. `fuzzy-zebra`). That name is
   yours alone; don't reuse a teammate's, and don't reuse the `fuzzy-zebra`
   example name used elsewhere in this doc — it's someone else's instance.
   If you don't have the event password, ask whoever's running the hack.

2. **Install the client's one dependency** (numpy):
   ```
   pip install -r contrib/greenbuilding-live/requirements.txt
   ```

3. **Render and upload:**
   ```
   python3 contrib/greenbuilding-live/upload_sky_clip.py <your-instance-name>
   ```
   This prints `uploaded 900 frames @ 30fps ... to <your-instance-name>` on
   success.

4. **Watch it:** open `https://sundai.willsarg.com/<your-instance-name>` in
   a browser. Add `?view=street` or `?view=river` for the other camera
   angles.

It loops forever on the server from here — nothing needs to keep running on
your machine. Re-run step 3 any time you change `sky.py` and want to push an
update.

**Why the timing looks a bit brisker live than locally:** the host caps an
uploaded clip at 900 frames, but one full local loop (5 constellations at
the defaults) is 1500 frames. `upload_sky_clip.py` scales the three timing
constants down proportionally (fade/hold/fadeout become 45/90/45 frames
instead of 75/150/75) so the *whole* loop still fits in exactly one 900-frame
clip — no constellation gets cut off mid-reveal, and the loop wraps
seamlessly.

**One instance, one live source at a time.** If two people run something
targeting the same instance simultaneously (this script, `demo.py`, etc.),
the display flickers between them. Coordinate, or just re-run
`upload_sky_clip.py` after the other thing stops — live frames always
pre-empt the clip, and the clip resumes automatically ~2s after the last
live frame.

### Fallback without `uv`

Everywhere above that says `uv run python -m tetris_sim ...`, you can instead
run (from the repo root):

```
PYTHONPATH=impl/python/engine:impl/python/sim python3 -m tetris_sim ...
```

(On Windows PowerShell: `$env:PYTHONPATH = "impl/python/engine;impl/python/sim"`.)

## Project layout (what's relevant to this feature)

```
impl/python/engine/tetris_engine/   the pure game engine + Animation protocol (animation.py)
impl/python/sim/tetris_sim/
  sky.py                           Tonight's Sky — this feature
  animations.py                    registers it as ANIMATIONS["sky"]
  cli.py                           `python -m tetris_sim --animation sky ...`
contrib/greenbuilding-live/
  gbsim/                           vendored hosted-simulator client (MIT, willsarg)
  upload_sky_clip.py               renders + uploads Tonight's Sky as a clip
SETUP.md                           this file
```

## Credits

- Engine, simulator and spec: [aygp-dr/17x9-Tetris](https://github.com/aygp-dr/17x9-Tetris)
  (Sundai Hack 140 starting kit).
- Hosted Green Building simulator + client library:
  [willsarg/sundai-greenbuilding-sim](https://github.com/willsarg/sundai-greenbuilding-sim)
  (MIT license, see [contrib/greenbuilding-live/LICENSE](contrib/greenbuilding-live/LICENSE)).
- Tonight's Sky animation: this repo's addition.
