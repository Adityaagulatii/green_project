"""Headless display sink (SPEC §10.2): records every frame sent, with its
logical timestamp, for renderers and tests to read back.

``Recorder`` follows the Display contract by duck typing (``send``,
``makeframe``). It accepts engine frames (17x9 tuples of RGB) and legacy
``utilities.display.Frame`` objects alike, without importing numpy.
"""

from tetris_engine import core
from tetris_engine.frame import frame_digest
from tetris_engine.tables import COLS, FPS, ROWS

BLACK_FRAME = tuple(((0, 0, 0),) * COLS for _ in range(ROWS))


def _clamp(v):
    return max(0, min(int(v), 255))


def normalize(frame):
    """Any 17x9 frame -> tuple of tuples of clamped (r, g, b) ints."""
    if hasattr(frame, "asarray"):  # legacy Frame of Color
        arr = frame.asarray()
        rows = [[(c.r, c.g, c.b) for c in arr[r]] for r in range(arr.shape[0])]
    else:
        rows = frame
    if len(rows) != ROWS or any(len(r) != COLS for r in rows):
        raise ValueError("a Frame is 17 rows x 9 columns")
    return tuple(tuple((_clamp(p[0]), _clamp(p[1]), _clamp(p[2])) for p in row)
                 for row in rows)


def hud(state):
    """Score/level summary for engine states; None for other animations."""
    if isinstance(state, core.State):
        return {"score": state.score, "level": state.level,
                "lines": state.lines, "high": state.high_score,
                "phase": state.phase}
    return None


class Recorder:
    def __init__(self, fps=FPS):
        self.fps = fps
        self.frames = []
        self.info = []
        self.events = []

    def makeframe(self):
        return BLACK_FRAME

    def send(self, frame, info=None):
        self.frames.append(normalize(frame))
        self.info.append(info)

    def __len__(self):
        return len(self.frames)

    @property
    def times(self):
        return [i / self.fps for i in range(len(self.frames))]

    def digests(self):
        return [frame_digest(f) for f in self.frames]


def record(animation, frames, controller=None, recorder=None):
    """Run ``animation`` headlessly for ``frames`` ticks. Returns
    (recorder, final_state); ``recorder.events`` holds the input log as
    [frame, action, down] triples (conformance trace order)."""
    rec = recorder if recorder is not None else Recorder()
    state = animation.init()
    for k in range(frames):
        events = list(controller(state)) if controller else []
        rec.events.extend([k, a, bool(d)] for a, d in events)
        state = animation.tick(state, events)
        rec.send(animation.render(state), hud(state))
    return rec, state
