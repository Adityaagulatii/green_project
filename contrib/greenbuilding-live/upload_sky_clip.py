"""Render one full TonightsSky loop and upload it as a clip to a hosted
Green Building simulator instance (https://sundai.willsarg.com). Simulator-
only glue: none of this runs on the real building, so it stays out of
impl/python/sim/tetris_sim/sky.py.

usage: python upload_sky_clip.py <instance-name> [base_url]
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO_ROOT, "impl", "python", "engine"))
sys.path.insert(0, os.path.join(_REPO_ROOT, "impl", "python", "sim"))
sys.path.insert(0, _HERE)  # this folder's vendored gbsim/

from gbsim import Frame, Color, upload_clip  # noqa: E402
from tetris_sim.sky import CONSTELLATIONS, TonightsSky  # noqa: E402

FPS = 30
CLIP_MAX_FRAMES = 900  # the host's cap on a single uploaded clip

# Scale SPEC's ~2.5s/5s/2.5s default (300 frames/constellation, 1500 total
# for all 5) down so one whole loop fits the host's 900-frame cap:
# 45 + 90 + 45 = 180 frames/constellation x 5 = 900, exactly one full cycle
# (so the clip wraps seamlessly instead of cutting a constellation off mid-reveal).
_SCALE = CLIP_MAX_FRAMES / (300 * len(CONSTELLATIONS))
_FADE, _HOLD, _FADEOUT = round(75 * _SCALE), round(150 * _SCALE), round(75 * _SCALE)
_TOTAL = (_FADE + _HOLD + _FADEOUT) * len(CONSTELLATIONS)
assert _TOTAL <= CLIP_MAX_FRAMES, _TOTAL


def render_frames():
    anim = TonightsSky(fade_frames=_FADE, hold_frames=_HOLD, fadeout_frames=_FADEOUT)
    state = anim.init()
    frames = []
    for _ in range(_TOTAL):
        state = anim.tick(state)
        rgb = anim.render(state)
        f = Frame()
        for r in range(f.nrows()):
            for c in range(f.ncols()):
                f[r][c] = Color(*rgb[r][c])
        frames.append(f)
    return frames


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    name = sys.argv[1]
    kwargs = {"base_url": sys.argv[2]} if len(sys.argv) > 2 else {}
    frames = render_frames()
    ok = upload_clip(name, frames, fps=FPS, **kwargs)
    print("uploaded" if ok else "failed", f"{len(frames)} frames @ {FPS}fps "
          f"({_FADE}/{_HOLD}/{_FADEOUT} fade/hold/fadeout per constellation) to", name)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
