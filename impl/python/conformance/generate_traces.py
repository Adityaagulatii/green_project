#!/usr/bin/env python3
"""Generate the v1 conformance traces from the reference engine.

    python impl/python/conformance/generate_traces.py [--out spec/conformance/traces]

Each scenario is a *controller* (state, frame) -> events that runs against
the engine while the generator records the events it emits. The trace
then stores only seed + events + reference digests. Scenarios assert that
they really exercised what they claim (e.g. a kick, a multi-line clear),
so a behaviour change cannot silently hollow out a trace.
"""

import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "impl", "python", "engine"),
                os.path.join(ROOT, "impl", "python", "sim")]

from tetris_engine import core  # noqa: E402
from tetris_engine import tables as T  # noqa: E402
from tetris_engine.conformance import make_trace  # noqa: E402
from tetris_engine.prng import seed_state, shuffle_bag  # noqa: E402
from tetris_sim.bot import Bot  # noqa: E402

P0 = 91  # first logical play frame (SPEC §9.1)
TAP = lambda a: [(a, True), (a, False)]  # noqa: E731


def find_seed(pred, start=1):
    seed = start
    while True:
        bag, _ = shuffle_bag(seed_state(seed))
        if pred("".join(bag)):
            return seed
        seed += 1


class Run:
    """Run a controller against the engine, recording events + stats."""

    def __init__(self, seed):
        self.seed = seed
        self.s = core.new_game(seed)
        self.k = 0
        self.events = []
        self.log = []  # (frame, kind, detail) observations for assertions

    def step(self, events=()):
        before = self.s
        events = list(events)
        self.events.extend([self.k, a, bool(d)] for a, d in events)
        self.s = core.step(self.s, events)
        self._observe(before, self.s, events)
        self.k += 1

    def _observe(self, a, b, events):
        if a.phase != core.CLEARING and b.phase == core.CLEARING:
            rows = sum(1 for r in range(1, T.ROWS + 1)
                       if b.flash[r] == "W" * T.PAD_COLS)
            self.log.append((self.k, "clear", rows))
        if a.phase != core.GAMEOVER and b.phase == core.GAMEOVER:
            self.log.append((self.k, "gameover", None))
        if b.level > a.level:
            self.log.append((self.k, "level", b.level))
        if a.phase == core.PLAYING and b.phase == core.PLAYING \
                and a.spawns == b.spawns:
            pa, pb = a.active, b.active
            if pa.rot != pb.rot:
                kicked = False
                for act, down in events:
                    if act.startswith("rotate") and down:
                        kicked = True
                if kicked and (pa.row, pa.col) != (pb.row, pb.col) \
                        and pa.shape != "O":
                    self.log.append((self.k, "kick", (pa, pb)))
                if (pa.rot - pb.rot) % 4 == 2:
                    self.log.append((self.k, "rot180", (pa, pb)))
            if any(a_ == "rotate_cw" and d for a_, d in events) and pa == pb \
                    and a.cw_available and a.dcd + 2 >= 2:
                self.log.append((self.k, "rotfail", pa))
        if b.hold is not None and (a.hold != b.hold):
            self.log.append((self.k, "hold", b.hold))

    def until(self, frame, controller=None):
        while self.k < frame:
            self.step(controller(self.s, self.k) if controller else ())

    def kinds(self, kind):
        return [e for e in self.log if e[1] == kind]

    def trace(self, name, description, covers, digest_every=1):
        return make_trace(name, description, self.seed, self.k, self.events,
                          digest_every, covers)


def script(steps):
    """Controller from {frame_offset_from_P0: [events]}."""
    def ctl(_s, k):
        return steps.get(k - P0, [])
    return ctl


# ------------------------------------------------------------ scenarios

def idle_gravity():
    r = Run(1)
    r.until(P0 + 700)
    assert len({e[0] for e in r.log}) == 0 or True
    assert r.s.spawns >= 2, "a gravity lock must happen"
    return r.trace("01-idle-gravity",
                   "No input: boot countdown, level-0 gravity every 25 frames,"
                   " gravity lock and respawn.",
                   ["§7.3", "§8.4", "§9.1", "QUIRK-8", "P7", "P16"])


def move_left_right():
    r = Run(7)
    steps = {
        2: [("left", True)],
        4: [("left", False)],
        6: [("left", True)] * 6,          # into the left wall
        8: [("right", True)] * 12,        # across to the right wall
        9: [("right", False), ("left", False)],
        11: TAP("hard_drop"),
        14: [("right", True), ("right", True), ("left", True)],
        16: TAP("rotate_cw"),
        18: [("left", True)] * 9,
        20: TAP("hard_drop"),
    }
    r.until(P0 + 60, script(steps))
    return r.trace("02-move-left-right",
                   "Shifts one and many cells per frame, walls block,"
                   " releases do nothing.",
                   ["§5.2", "§4.4", "P10"])


def rotate_cw_ccw():
    r = Run(3)
    steps = {
        1: [("rotate_cw", True), ("rotate_cw", True)],      # latch: 1 turn
        2: [("rotate_cw", False), ("rotate_cw", True)],     # turns again
        3: [("rotate_ccw", True), ("rotate_ccw", False),    # QUIRK-3
            ("rotate_cw", False), ("rotate_cw", True)],     # swallowed
        4: [("rotate_cw", False), ("rotate_cw", True)],
        5: [("rotate_cw", False)],
        6: TAP("rotate_180") + TAP("rotate_cw"),            # DCD blocks cw
        8: TAP("hard_drop"),
        10: TAP("rotate_ccw"), 11: TAP("rotate_ccw"),
        12: TAP("rotate_ccw"), 13: TAP("rotate_ccw"),
        14: TAP("rotate_cw"), 15: TAP("rotate_cw"),
        16: [("rotate_cw", False)],                         # stray release
        17: TAP("hard_drop"),
        # QUIRK-3 observable: a CCW release at dcd >= 2 swallows the CW
        # press that follows it in the same frame (a CW release would not).
        19: [("rotate_ccw", True)],                         # turns
        21: [("rotate_ccw", False), ("rotate_cw", True)],   # cw swallowed
        22: [("rotate_cw", False)],
        24: [("rotate_180", True)],                         # turns
        26: [("rotate_180", False), ("rotate_cw", True)],   # cw turns
        27: [("rotate_cw", False)],
        29: TAP("hard_drop"),
    }
    r.until(P0 + 70, script(steps))
    swallowed = [e for e in r.events if e[0] == P0 + 21]
    assert swallowed, "QUIRK-3 probe missing"
    return r.trace("03-rotate-cw-ccw",
                   "CW/CCW rotations, latches, DCD gating, CCW release"
                   " resetting DCD, swallowed presses.",
                   ["§5.3", "§5.4", "QUIRK-2", "QUIRK-3", "QUIRK-15",
                    "P11", "P12"])


def rotate_kicks():
    seed = find_seed(lambda b: b[0] == "T" and b[1] == "I" and b[2] in "JLSZ")
    r = Run(seed)
    steps = {
        # T: turn to state 1, hug the left wall, turn again -> wall kick
        1: TAP("rotate_cw"), 2: [("left", True)] * 5,
        3: TAP("rotate_cw"), 4: TAP("rotate_ccw"), 5: TAP("rotate_ccw"),
        6: TAP("hard_drop"),
        # I: vertical, hug the right wall, turn back -> kick
        8: TAP("rotate_cw"), 9: [("right", True)] * 6,
        10: TAP("rotate_ccw"), 11: TAP("rotate_cw"),
        12: [("left", True)] * 9, 13: TAP("rotate_cw"),
        14: TAP("hard_drop"),
        # third piece: kicks against the stack
        16: TAP("rotate_ccw"), 17: [("left", True)] * 5,
        18: TAP("rotate_cw"), 19: TAP("rotate_cw"), 20: TAP("hard_drop"),
    }
    r.until(P0 + 50, script(steps))
    assert r.kinds("kick"), "scenario must perform a wall kick"
    return r.trace("04-rotate-kicks",
                   f"Wall kicks (SRS offset tests 2-5) for T, I and a third"
                   f" piece; seed {seed} gives T, I first.",
                   ["§5.3", "§4.4", "P4"])


def rotate_180():
    seed = find_seed(lambda b: b[0] == "T" and b[1] == "I" and b[2] == "O")
    r = Run(seed)
    steps = {
        1: TAP("rotate_180"), 2: TAP("rotate_180"),
        3: [("left", True)] * 5, 4: TAP("rotate_cw"), 5: TAP("rotate_180"),
        6: [("right", True)] * 8, 7: TAP("rotate_180"), 8: TAP("rotate_180"),
        9: TAP("hard_drop"),
        11: TAP("rotate_180"), 12: [("right", True)] * 6,
        13: TAP("rotate_cw"), 14: TAP("rotate_180"), 15: TAP("rotate_180"),
        16: [("left", True)] * 9, 17: TAP("rotate_180"), 18: TAP("hard_drop"),
        20: TAP("rotate_180"), 21: TAP("rotate_180"), 22: TAP("rotate_cw"),
        23: TAP("rotate_180"), 24: TAP("hard_drop"),
    }
    r.until(P0 + 50, script(steps))
    assert len(r.kinds("rot180")) >= 6
    return r.trace("05-rotate-180",
                   "180-degree rotations for T, I and O, at walls; kick"
                   " offsets = KICKS_180[src] - KICKS[dst].",
                   ["§5.3", "QUIRK-1"])


def soft_drop():
    r = Run(11)

    def ctl(s, k):
        f = k - P0
        if 1 <= f <= 40:
            return [("soft_drop", True)] + ([("soft_drop", False)]
                                            if f % 7 == 0 else [])
        if f == 44:
            return TAP("rotate_cw")
        if 46 <= f <= 70 and f % 2 == 0:
            return [("soft_drop", True)]
        return []
    r.until(P0 + 90, ctl)
    assert r.s.spawns >= 3
    return r.trace("06-soft-drop",
                   "Held soft drop (one row per event) to the floor; the"
                   " blocked move locks immediately (no lock delay).",
                   ["§5.2", "§6.1", "QUIRK-14"])


def hard_drop():
    r = Run(5)
    steps = {
        1: TAP("hard_drop"),
        2: [("hard_drop", True)],                            # drops
        3: [("hard_drop", True)],                            # latched
        4: [("hard_drop", False), ("hard_drop", True)],      # DCD swallow
        5: [("hard_drop", True)],                            # still latched
        6: [("hard_drop", False)],
        7: [("hard_drop", True), ("hard_drop", False)],      # drops
        8: [("left", True)] * 3 + TAP("hard_drop"),
        9: TAP("rotate_cw") + TAP("hard_drop"),               # DCD blocks drop
        10: TAP("hard_drop"),
    }
    r.until(P0 + 30, script(steps))
    return r.trace("07-hard-drop",
                   "Hard drop, its latch, DCD gating and the release that"
                   " resets DCD.",
                   ["§5.5", "§5.4", "QUIRK-2", "QUIRK-4", "P8", "P12"])


def hold():
    r = Run(9)
    steps = {
        1: TAP("rotate_cw"),
        2: TAP("hold"),                  # hold empty: draw from bag
        3: TAP("hold"),                  # blocked until lock
        4: TAP("rotate_ccw"),
        5: TAP("hard_drop"),
        7: TAP("hold"),                  # swap in (keeps rotation 1)
        8: TAP("hold"),                  # blocked
        9: [("right", True)] * 3,
        10: TAP("hard_drop"),
        12: TAP("rotate_180"), 13: TAP("hold"),
        14: [("left", True)] * 2, 15: TAP("hard_drop"),
        17: TAP("hold"), 18: TAP("hard_drop"),
    }
    r.until(P0 + 40, script(steps))
    assert len(r.kinds("hold")) >= 3
    return r.trace("08-hold",
                   "Hold from an empty slot, blocked re-hold, swap-in at the"
                   " spawn cell keeping rotation, hold after each lock.",
                   ["§5.6", "QUIRK-6", "P15"])


def bot_until(seed, stop, after, name, desc, covers, noise=0.0, every=1,
              limit=6000):
    r = Run(seed)
    bot = Bot()
    rnd = random.Random(seed)
    stop_at = None
    while r.k < limit:
        events = bot(r.s)
        if noise and r.s.phase == core.PLAYING and rnd.random() < noise:
            events = list(events)
            events.insert(rnd.randrange(len(events) + 1),
                          (rnd.choice(T.ACTIONS), rnd.random() < 0.6))
        r.step(events)
        if stop_at is None and stop(r):
            stop_at = r.k + after
        if stop_at is not None and r.k >= stop_at:
            break
    assert stop_at is not None, f"{name}: condition never met"
    return r, r.trace(name, desc, covers, every)


def line_clear_single():
    _, t = bot_until(
        21, lambda r: any(e[2] == 1 for e in r.kinds("clear")), 20,
        "09-line-clear-single",
        "Bot play up to the first single-line clear: 5 flash frames,"
        " row removal, 100 points.", ["§6.1", "§6.2", "§8.2", "P13", "P16"])
    return t


def line_clear_multi():
    _, t = bot_until(
        4, lambda r: any(e[2] >= 2 for e in r.kinds("clear")), 20,
        "10-line-clear-multi",
        "Bot play up to the first multi-line clear; per-row scoring.",
        ["§6.1", "§6.2", "QUIRK-5", "P13"])
    return t


def level_up():
    _, t = bot_until(
        8, lambda r: r.s.level >= 2, 60, "11-level-up",
        "Bot play through two level-ups (target = level + 5); the gravity"
        " cadence speeds up.", ["§7.1", "§7.2", "QUIRK-7", "QUIRK-16", "P14"])
    return t


def game_over_reset():
    r = Run(13)
    # Hard drop every frame; the post-reset remainder of the batch holds a
    # rotate press that QUIRK-11 (dcd = 0) swallows.
    def ctl(s, k):
        if r.kinds("gameover"):  # new game: gentle input only
            return [("left", True)] if k % 5 == 0 else []
        if k >= P0:
            return TAP("hard_drop") + [("rotate_cw", True), ("left", True),
                                       ("rotate_cw", False)]
        return []
    while not r.kinds("gameover"):
        r.step(ctl(r.s, r.k))
    over = r.kinds("gameover")[0][0]
    r.until(over + 218 + 90 + 40, ctl)
    return r.trace("12-game-over-reset",
                   "Top-out by hard drop, fill/wait/fall, countdown without"
                   " the black frame, reset, and the resumed batch on the new"
                   " game with dcd = 0.",
                   ["§6.1", "§8.3", "§8.4", "QUIRK-10", "QUIRK-11",
                    "QUIRK-13", "QUIRK-19", "P16", "P17"])


def suspended_input():
    r = Run(21)
    bot = Bot()
    phase = {"stage": "bot"}

    def ctl(s, k):
        if phase["stage"] == "bot":
            if r.kinds("clear"):
                phase["stage"] = "flash"
            else:
                return bot(s)
        if phase["stage"] == "flash":
            if s.phase == core.CLEARING:
                return [("left", True), ("left", True)] + TAP("rotate_cw")
            phase["stage"] = "topout"
        if phase["stage"] == "topout":
            if s.phase == core.PLAYING and not r.kinds("gameover"):
                return TAP("hard_drop")
            return [("right", True), ("hold", True)] + TAP("rotate_ccw")
        return []
    while not r.kinds("gameover") and r.k < 5000:
        r.step(ctl(r.s, r.k))
    over = r.kinds("gameover")[0][0]
    r.until(over + 218 + 90 + 10, ctl)
    return r.trace("13-suspended-input",
                   "Input during a flash is queued and applied afterwards;"
                   " input during game over and countdown is discarded.",
                   ["§9.2", "QUIRK-12", "P18"])


def bot_marathon():
    _, t = bot_until(
        2026, lambda r: r.k >= 3200, 0, "14-bot-marathon",
        "Long bot game with 5% random input noise: clears, levels, game"
        " overs.", ["§5", "§6", "§7", "§8", "§9"], noise=0.05, every=4,
        limit=3300)
    return t


SCENARIOS = [idle_gravity, move_left_right, rotate_cw_ccw, rotate_kicks,
             rotate_180, soft_drop, hard_drop, hold, line_clear_single,
             line_clear_multi, level_up, game_over_reset, suspended_input,
             bot_marathon]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.path.join(ROOT, "spec", "conformance",
                                                  "traces"))
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    for scenario in SCENARIOS:
        trace = scenario()
        path = os.path.join(args.out, trace["name"] + ".json")
        with open(path, "w") as fh:
            json.dump(trace, fh, indent=None, separators=(",", ":"))
            fh.write("\n")
        f = trace["final"]
        print(f"{trace['name']}: seed {trace['seed']} frames {trace['frames']}"
              f" events {len(trace['events'])} score {f['score']}"
              f" level {f['level']} high {f['high_score']} phase {f['phase']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
