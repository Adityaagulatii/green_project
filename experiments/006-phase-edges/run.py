#!/usr/bin/env python3
"""006: the phase machine's legal edges, checked against the Python reference.

The table LEGAL is derived from SPEC §9.2's pseudocode. This script then
watches the Python engine and collects every edge (phase of S_k, phase of
S_k+1) it takes over:

  1. the 14 conformance traces;
  2. random input and bot games (with noise) for many seeds;
  3. two constructed witnesses for the edges that random play never
     reaches: a whole batch in frame 91 (the frame that ends the boot
     countdown) that clears a row, and one that tops out.

Success: the observed set equals LEGAL, with at least one example of every
legal edge, and no illegal edge observed.

    python3 experiments/006-phase-edges/run.py [seeds] [frames]
"""

import collections
import glob
import json
import os
import random
import sys
from dataclasses import replace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "impl", "python", "engine"),
                os.path.join(ROOT, "impl", "python", "sim")]

from tetris_engine import ACTIONS, core  # noqa: E402
from tetris_engine.conformance import group_events  # noqa: E402
from tetris_sim.bot import Bot  # noqa: E402

C, P, K, G = core.COUNTDOWN, core.PLAYING, core.CLEARING, core.GAMEOVER
LEGAL = {(C, C), (C, P), (C, K), (C, G),
         (P, P), (P, K), (P, G),
         (K, K), (K, P), (K, G),
         (G, G), (G, C)}


def edges_of(seed, per_frame, bot=None):
    s = core.new_game(seed)
    out = collections.Counter()
    for evs in per_frame:
        evs = (list(bot(s)) if bot else []) + list(evs)
        nxt = core.step(s, evs)
        out[(s.phase, nxt.phase)] += 1
        s = nxt
    return out


def boot_state(seed):
    """S_91: after frames 0-90; the next step is frame 91, a fresh logical frame."""
    s = core.new_game(seed)
    for _ in range(91):
        s = core.step(s)
    return s


def top_out_batch():
    """Soft drops only: pieces stack in the spawn columns until a spawn
    collides. Columns 3-6 alone can never fill a row."""
    return [("soft_drop", True)] * 400


def clear_batch(seed, max_pieces=14):
    """Greedy: shifts and soft drops only (ungated), rotation 0, until the
    bottom row is full. Returns the batch, or None."""
    s = boot_state(seed)
    probe = replace(s, phase=P, boot=False, timer=0)
    batch = []
    for _ in range(max_pieces):
        best = None
        for dcol in range(-5, 6):
            p, evs = probe, []
            move = "right" if dcol > 0 else "left"
            for _ in range(abs(dcol)):
                evs.append((move, True))
                p = core.apply_action(p, move, True)
            spawns = p.spawns
            while p.spawns == spawns and len(evs) < 60:
                evs.append(("soft_drop", True))
                p = core.apply_action(p, "soft_drop", True)
            if p.phase == K:
                return batch + evs
            if p.phase != P:
                continue
            bottom = sum(ch != "." for ch in p.board[17][1:10])
            height = min((r for r in range(1, 18) if p.board[r][1:10] != "." * 9),
                         default=18)
            score = (bottom, height)
            if best is None or score > best[0]:
                best = (score, evs, p)
        if best is None:
            return None
        batch += best[1]
        probe = best[2]
    return None


def main(argv):
    seeds = int(argv[1]) if len(argv) > 1 else 40
    frames = int(argv[2]) if len(argv) > 2 else 1500
    seen = collections.Counter()

    for path in sorted(glob.glob(os.path.join(ROOT, "spec", "conformance", "traces", "*.json"))):
        with open(path) as fh:
            t = json.load(fh)
        by = group_events(t["events"])
        seen += edges_of(t["seed"], [by.get(k, ()) for k in range(t["frames"])])
    print("after traces:", len(seen), "edges")

    rng = random.Random(2026)
    for seed in range(seeds):
        noise = [[(rng.choice(ACTIONS), rng.random() < 0.7)
                  for _ in range(rng.randint(1, 3))] if rng.random() < 0.2 else []
                 for _ in range(frames)]
        seen += edges_of(seed, noise)
        seen += edges_of(seed, noise, bot=Bot())
    print("after random + bot:", len(seen), "edges")

    witnesses = {}
    for seed in range(1, 50):
        s = boot_state(seed)
        nxt = core.step(s, top_out_batch())
        if (s.phase, nxt.phase) == (C, G):
            witnesses[(C, G)] = seed
            seen[(C, G)] += 1
            break
    for seed in range(1, 200):
        batch = clear_batch(seed)
        if batch is None:
            continue
        s = boot_state(seed)
        nxt = core.step(s, batch)
        if (s.phase, nxt.phase) == (C, K):
            witnesses[(C, K)] = (seed, len(batch))
            seen[(C, K)] += 1
            break
    print("witnesses:", witnesses)

    for edge in sorted(set(seen) | LEGAL):
        mark = "legal  " if edge in LEGAL else "ILLEGAL"
        print(f"  {mark} {edge[0]:>9} -> {edge[1]:<9} observed {seen.get(edge, 0)}")
    illegal = set(seen) - LEGAL
    missing = LEGAL - set(seen)
    ok = not illegal and not missing
    print("RESULT:", "observed == LEGAL" if ok else f"illegal {illegal} missing {missing}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
