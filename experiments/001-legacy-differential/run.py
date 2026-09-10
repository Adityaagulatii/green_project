#!/usr/bin/env python3
"""Long bot-driven differential run: run.py [TRIALS] [FRAMES]."""

import random
import sys
import time

from legacy_harness import EngineHarness, LegacyHarness, collapse, legacy_visible
from tetris_engine import ACTIONS
from tetris_sim.bot import Bot


def main(trials=8, frames=1200):
    bad, t0 = 0, time.time()
    stats = {"clear_frames": 0, "gameovers": 0, "maxlevel": 0, "frames": 0}
    for trial in range(trials):
        seed = random.Random(trial).randrange(1, 2 ** 32)
        rnd = random.Random(500 + trial)
        L, E, bot = LegacyHarness(seed), EngineHarness(seed), Bot()
        noise = [0.0, 0.05, 0.2][trial % 3]
        for f in range(frames):
            evs = list(bot(E.s))
            if rnd.random() < noise:
                evs.insert(rnd.randrange(len(evs) + 1),
                           (rnd.choice(ACTIONS), rnd.random() < 0.6))
            lv = collapse(legacy_visible(L.frame(evs)))
            es = E.frame(evs)
            stats["frames"] += 1
            stats["clear_frames"] += 1 < len(es) < 50
            stats["gameovers"] += len(es) >= 50
            stats["maxlevel"] = max(stats["maxlevel"], E.s.level)
            a, b = L.snapshot(), E.snapshot()
            if a != b or lv != collapse(es):
                print("DIFF seed", seed, "frame", f, "events", evs)
                for k in a:
                    if a[k] != b[k]:
                        print("  ", k, "legacy", a[k], "engine", b[k])
                bad += 1
                break
        print(f"trial {trial} seed {seed} {time.time() - t0:.1f}s {stats}",
              flush=True)
    print("divergences", bad, stats)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(*map(int, sys.argv[1:3])))
