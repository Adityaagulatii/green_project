#!/usr/bin/env python3
"""Write the game that contrib/emacs/media/record.sh shows on the Emacs display.

    PYTHONPATH=impl/python/engine:impl/python/sim \\
      python contrib/emacs/media/green_building_game.py          # write the trace
    ... green_building_game.py --search                          # list candidates

The game is a SPEC §12 conformance trace (seed, events, every frame's
digest), made by the reference engine, so the Emacs replay can check all
of its frames.  The inputs come from the demo bot in tetris_sim.bot:

1. The bot, Bot(pace=3, think=6, batch_shifts=False) as in
   ``python -m tetris_sim --bot``, plays frames 0 to SWITCH-1.
2. From frame SWITCH on, each new piece is hard-dropped where it spawns,
   DROP_WAIT frames after it appears, so the stack tops out and the
   game-over animation (fill, white wait, fall-down) runs within about
   half a minute.
3. The trace ends TAIL frames after the next game's first spawn.

Standard library plus the engine; the output is deterministic.
"""

import argparse
import json
import sys
from pathlib import Path

from tetris_engine import core
from tetris_engine.conformance import make_trace
from tetris_sim.bot import Bot

HERE = Path(__file__).resolve().parent
OUT = HERE / "green-building-game.json"

# Chosen with --search: see contrib/emacs/README.md, "Recording".
SEED, SWITCH, DROP_WAIT, TAIL = 42, 540, 6, 30


class Controller:
    """The demo bot before frame SWITCH, then a hard drop per piece."""

    def __init__(self, switch, drop_wait):
        self.bot = Bot(pace=3, think=6, batch_shifts=False)
        self.switch, self.drop_wait = switch, drop_wait
        self.k, self.spawn, self.wait, self.over = -1, None, 0, False

    def __call__(self, s):
        self.k += 1
        self.over = self.over or s.phase == core.GAMEOVER
        if s.phase != core.PLAYING or self.over:  # no input after game over
            return []
        if self.k < self.switch:
            return self.bot(s)
        if s.spawns != self.spawn:
            self.spawn, self.wait = s.spawns, self.drop_wait
        if self.wait > 0:
            self.wait -= 1
            return [("hard_drop", True), ("hard_drop", False)] if self.wait == 0 else []
        return []


def play(seed, switch, drop_wait, tail, limit=3000):
    """Run the game; return (events, frames, summary) or None if too long.
    The summary lists each line clear as [frame, rows] (the frame where
    the lines counter rises) and each level-up frame."""
    ctl = Controller(switch, drop_wait)
    s = core.new_game(seed)
    events, clears, levels, gameover, spawn2 = [], [], [], None, None
    for k in range(limit):
        batch = ctl(s)
        events.extend([k, a, d] for a, d in batch)
        prev = s
        s = core.step(s, batch)
        if s.lines > prev.lines:
            clears.append([k, s.lines - prev.lines])
        if s.level > prev.level:
            levels.append(k)
        if gameover is None and s.phase == core.GAMEOVER:
            gameover = k
        if gameover is not None and spawn2 is None and prev.phase == core.COUNTDOWN \
                and s.phase == core.PLAYING:
            spawn2 = k
        if spawn2 is not None and k >= spawn2 + tail:
            return events, k + 1, {"seed": seed, "switch": switch, "clears": clears,
                                   "level_ups": levels, "gameover": gameover,
                                   "spawn2": spawn2, "frames": k + 1,
                                   "score": prev.high_score}
    return None


def search(seeds=range(1, 121), switches=(360, 450, 540, 630)):
    for seed in seeds:
        for switch in switches:
            r = play(seed, switch, DROP_WAIT, TAIL)
            if r is None:
                continue
            info = r[2]
            multi = sum(1 for _, n in info["clears"] if n >= 2)
            if info["frames"] <= 1200 and multi:
                print(json.dumps(info), flush=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--search", action="store_true")
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args(argv)
    if args.search:
        search()
        return 0
    events, frames, info = play(SEED, SWITCH, DROP_WAIT, TAIL)
    desc = (f"Green Building demo for the Emacs display: seed {SEED}; the demo bot "
            f"plays frames 0-{SWITCH - 1}, then each piece is hard-dropped "
            f"{DROP_WAIT} frames after it spawns until the stack tops out; the "
            f"trace ends {TAIL} frames after game 2's first spawn. Line clears "
            f"[frame, rows]: {info['clears']}; level-ups at {info['level_ups']}; "
            f"game over at frame {info['gameover']}; game 2's first spawn at "
            f"frame {info['spawn2']}.")
    trace = make_trace("green-building-emacs", desc, SEED, frames, events,
                       digest_every=1)
    with open(args.out, "w") as fh:
        json.dump(trace, fh, indent=None, separators=(",", ":"))
        fh.write("\n")
    print(json.dumps(info), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
