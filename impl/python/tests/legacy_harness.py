"""Drive the UNCHANGED legacy tetris.py headlessly, frame by frame.

pygame, keyboard and tkinter are replaced by stubs *only while the legacy
module is imported*; ``time`` and the ``np`` name inside the legacy module
are then swapped so that busy-waits return at once and the legacy Bag is
shuffled with the spec PRNG. numpy itself is real (the legacy Frame and
Color classes run unmodified).
"""

import importlib.util
import sys
import types
from pathlib import Path

import numpy

from tetris_engine import core
from tetris_engine.frame import render_codes
from tetris_engine.prng import fisher_yates_inplace, seed_state

LEGACY_DIR = Path(__file__).resolve().parents[1] / "legacy"

HANDLERS = {
    "left": "_moveLeft",
    "right": "_moveRight",
    "soft_drop": "_softDrop",
    "hard_drop": "_hardDrop",
    "rotate_cw": "_rotateCW",
    "rotate_ccw": "_rotateCCW",
    "rotate_180": "_rotate180",
    "hold": "_hold",
}

RGB_TO_CODE = {
    (0, 0, 0): ".", (255, 255, 255): "W", (0, 255, 255): "I",
    (0, 0, 255): "J", (255, 170, 0): "L", (255, 255, 0): "O",
    (0, 255, 0): "S", (255, 0, 0): "Z", (153, 0, 255): "T",
    (42, 42, 42): "G",
}

_PYGAME_NAMES = (
    "K_UP K_x K_LCTRL K_z K_SPACE K_DOWN K_LEFT K_RIGHT K_LSHIFT K_c "
    "CONTROLLER_BUTTON_DPAD_UP CONTROLLER_BUTTON_DPAD_DOWN "
    "CONTROLLER_BUTTON_DPAD_LEFT CONTROLLER_BUTTON_DPAD_RIGHT JOYAXISMOTION "
    "CONTROLLER_BUTTON_A CONTROLLER_BUTTON_B CONTROLLER_BUTTON_LEFTSHOULDER "
    "CONTROLLER_BUTTON_RIGHTSHOULDER CONTROLLER_BUTTON_Y KEYDOWN KEYUP "
    "JOYBUTTONDOWN JOYBUTTONUP JOYDEVICEADDED JOYDEVICEREMOVED"
).split()


def _noop(*_a, **_k):
    return None


def _stub_modules():
    pg = types.ModuleType("pygame")
    for i, name in enumerate(_PYGAME_NAMES):
        setattr(pg, name, 1000 + i)
    pg.get_init = lambda: True
    pg.init = _noop
    pg.quit = _noop
    pg.key = types.SimpleNamespace(set_repeat=_noop)
    pg.event = types.SimpleNamespace(clear=_noop, get=lambda: [])
    pg.joystick = types.SimpleNamespace(init=_noop, get_count=lambda: 0,
                                        Joystick=_noop)
    pg.display = types.SimpleNamespace(quit=_noop, set_mode=_noop, flip=_noop)
    kb = types.ModuleType("keyboard")
    kb.is_pressed = lambda _key: False
    tk = types.ModuleType("tkinter")
    return {"pygame": pg, "keyboard": kb, "tkinter": tk}


def load_legacy():
    """Import impl/python/legacy/tetris.py as module ``legacy_tetris``."""
    if "legacy_tetris" in sys.modules:
        return sys.modules["legacy_tetris"]
    stubs = _stub_modules()
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    if str(LEGACY_DIR) not in sys.path:
        sys.path.insert(0, str(LEGACY_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "legacy_tetris", LEGACY_DIR / "tetris.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["legacy_tetris"] = mod
        spec.loader.exec_module(mod)
    finally:
        for name, old in saved.items():  # don't leak stubs to other tests
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    mod.print = _noop  # silence the legacy game-over prints
    sys.modules["utilities.input_manager"].print = _noop  # "Initialized..."
    return mod


class _FakeTime:
    """perf_counter jumps 1 s per call, so every busy-wait exits at once."""

    def __init__(self):
        self.now = 0.0

    def perf_counter(self):
        self.now += 1.0
        return self.now

    def sleep(self, _seconds):
        return None


class _NpShim:
    def __init__(self, harness):
        self.random = types.SimpleNamespace(shuffle=harness.shuffle)

    def __getattr__(self, name):
        return getattr(numpy, name)


def frame_to_codes(frame):
    """Legacy Frame (17 or 19 rows) -> tuple of code strings, 17 rows."""
    arr = frame.asarray()
    rows = tuple("".join(RGB_TO_CODE[(c.r, c.g, c.b)] for c in arr[r])
                 for r in range(arr.shape[0]))
    if len(rows) == 19:  # countdown glyphs (QUIRK-9)
        assert rows[17:] == ("." * 9,) * 2, "countdown rows 17-18 not black"
        return rows[:17], True
    assert len(rows) == 17
    return rows, False


def board_codes(frame):
    arr = frame.asarray()
    return tuple("".join(RGB_TO_CODE[(c.r, c.g, c.b)] for c in arr[r])
                 for r in range(arr.shape[0]))


class LegacyHarness:
    """One legacy Tetris instance plus the play-loop body, one frame per
    call to ``frame(events)``."""

    def __init__(self, seed):
        self.mod = load_legacy()
        display_mod = sys.modules["utilities.display"]
        self.rng = seed_state(seed)
        self.mod.np = _NpShim(self)
        self.mod.time = _FakeTime()
        harness = self

        class RecordingDisplay(display_mod.Display):
            def send(self, frame):
                harness.sent.append(frame_to_codes(frame))

            def makeframe(self):
                return display_mod.Frame()

        self.sent = []
        self.display = RecordingDisplay()
        self.t = self.mod.Tetris(display=self.display)
        self.acc = 0  # play()'s local ``fractionalPosition``
        self.boot_frames = [codes for codes, _ in self.sent]
        self.sent = []

    def shuffle(self, items):
        self.rng = fisher_yates_inplace(items, self.rng)

    def frame(self, events):
        """Exactly the body of legacy ``Tetris.play``'s while-loop, with the
        input batch given explicitly. Returns the frames sent meanwhile."""
        t = self.t
        start = len(self.sent)
        self.acc += t._gravity * 2
        t._DCDCounter += 2
        for action, down in events:
            getattr(t, HANDLERS[action])(down)
        t._checkLevelUp()
        if self.acc >= 1:
            t._moveDown(True)
            self.acc = 0
        t._updateDisplayFrame()
        t._display.send(t._displayFrame)
        return self.sent[start:]

    def snapshot(self):
        t = self.t
        a = t._activeTetromino
        h = t._holdTetromino
        return {
            "board": board_codes(t._background),
            "active": (a._shape, a._rotation, a._position[0], a._position[1]),
            "hold": None if h is None else (h._shape, h._rotation),
            "hold_available": t._holdAvailable,
            "score": t.score,
            "level": t._level,
            "lines": t._linesCleared,
            "high_score": t._highScore,
            "dcd": t._DCDCounter,
            "latches": (t._CWavailable, t._CCWavailable, t._180available,
                        t._hardDropAvailable),
            "acc": self.acc,
            "bag": (tuple(t._bag._bag), t._bag._index),
        }


class EngineHarness:
    """The engine, advanced one *logical* frame at a time: the frame's step
    plus every suspended frame (flash, game over, countdown) that follows."""

    def __init__(self, seed):
        s = core.new_game(seed)
        boot = []
        while s.phase == core.COUNTDOWN:
            s = core.step(s, ())
            if s.phase != core.COUNTDOWN:
                raise AssertionError("boot step consumed a logical frame")
            boot.append(render_codes(s))
            if s.timer == 90:  # boot black frame; next step plays
                break
        self.s = s
        self.boot_frames = boot

    def frame(self, events):
        s = core.step(self.s, events)
        frames = [render_codes(s)]
        while s.phase != core.PLAYING:
            s = core.step(s, ())
            frames.append(render_codes(s))
        self.s = s
        return frames

    def snapshot(self):
        s = self.s
        a = s.active
        return {
            "board": s.board,
            "active": (a.shape, a.rot, a.row, a.col),
            "hold": None if s.hold is None else (s.hold.shape, s.hold.rot),
            "hold_available": s.hold_available,
            "score": s.score,
            "level": s.level,
            "lines": s.lines,
            "high_score": s.high_score,
            "dcd": s.dcd,
            "latches": (s.cw_available, s.ccw_available, s.r180_available,
                        s.hard_available),
            "acc": s.acc,
            "bag": (s.bag, s.bag_index),
        }


def collapse(frames):
    """Drop consecutive duplicates (a frame held for n slots == one send)."""
    out = []
    for f in frames:
        if not out or out[-1] != f:
            out.append(f)
    return out


BLACK = ("." * 9,) * 17


def legacy_visible(sent):
    """Legacy sends for one logical frame, minus the post-game-over black
    frame that is superseded in the same slot (QUIRK-10)."""
    out = []
    for i, (codes, is_glyph) in enumerate(sent):
        prev_glyph = i > 0 and sent[i - 1][1]
        if codes == BLACK and prev_glyph and i + 1 < len(sent):
            continue
        out.append(codes)
    return out
