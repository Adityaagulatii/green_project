"""Non-game Animations (SPEC §10.3): examples of embodied behaviours that
drive the same facade. Each is pure: state is the frame counter."""

import math

from tetris_engine.tables import COLS, ROWS


class Pulse:
    """A slow "breathing" mood colour, brightest at the centre floors."""

    def __init__(self, color=(80, 160, 255), period_frames=90):
        self.color = color
        self.period = period_frames

    def init(self):
        return 0

    def tick(self, state, events=()):
        return state + 1

    def render(self, t):
        phase = 0.5 - 0.5 * math.cos(2 * math.pi * (t % self.period) / self.period)
        rows = []
        for r in range(ROWS):
            falloff = 1.0 - abs(r - (ROWS - 1) / 2) / ROWS
            k = phase * falloff
            rows.append(tuple(tuple(int(c * k) for c in self.color)
                              for _ in range(COLS)))
        return tuple(rows)


class Wave:
    """A "world transition": a band of colour sweeping up the tower."""

    def __init__(self, color=(255, 170, 0), speed=3):
        self.color = color
        self.speed = speed

    def init(self):
        return 0

    def tick(self, state, events=()):
        return state + 1

    def render(self, t):
        head = ROWS - 1 - (t // self.speed) % (ROWS + 6)
        rows = []
        for r in range(ROWS):
            d = r - head
            k = 1.0 if d == 0 else (max(0.0, 1.0 - d / 5.0) if d > 0 else 0.0)
            rows.append(tuple(tuple(int(c * k) for c in self.color)
                              for _ in range(COLS)))
        return tuple(rows)


ANIMATIONS = {"pulse": Pulse, "wave": Wave}
