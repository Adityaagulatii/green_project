"""Thin adapter from engine frames to any legacy ``utilities.display.Display``
(the hack-day simulator, the pygame DummyDisplay or the real building).

This is the only engine module that touches a clock, and only in ``run``.
It never imports numpy or the legacy package itself: the Color class is
taken from the module of the Frame that ``display.makeframe()`` returns.
"""

import sys
import time


def _color_class(legacy_frame):
    module = sys.modules[type(legacy_frame).__module__]
    return module.Color


def to_legacy_frame(frame, display):
    """Copy a 17x9 RGB engine frame into a fresh ``display.makeframe()``."""
    out = display.makeframe()
    Color = _color_class(out)
    cache = {}
    for r, row in enumerate(frame):
        for c, rgb in enumerate(row):
            color = cache.get(rgb)
            if color is None:
                color = cache[rgb] = Color(*rgb)
            out[r, c] = color
    return out


class DisplayDriver:
    """Sends engine frames to a legacy Display."""

    def __init__(self, display):
        self.display = display

    def send(self, frame):
        self.display.send(to_legacy_frame(frame, self.display))


def run(display, animation, frames, controller=None, realtime=True,
        clock=time.monotonic, sleep=time.sleep, fps=30):
    """Drive ``display`` with ``animation`` for ``frames`` ticks.

    ``controller(state) -> events`` supplies input (e.g. the demo bot).
    With ``realtime`` the loop is paced at ``fps``; tests pass False.
    Returns the final state.
    """
    driver = DisplayDriver(display)
    state = animation.init()
    period = 1.0 / fps
    next_t = clock()
    for _ in range(frames):
        events = controller(state) if controller else ()
        state = animation.tick(state, events)
        driver.send(animation.render(state))
        if realtime:
            next_t += period
            delay = next_t - clock()
            if delay > 0:
                sleep(delay)
    return state
