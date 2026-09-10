"""Optional desktop viewer: the engine drawn by the legacy pygame
``DummyDisplay``. pygame is imported only when ``run_viewer`` is called, so
everything else stays headless.
"""

import sys
from pathlib import Path

LEGACY_DIR = Path(__file__).resolve().parents[2] / "legacy"

# Legacy keyboard bindings (impl/python/legacy/tetris.py KEYBINDS).
KEYMAP = {
    "K_UP": "rotate_cw", "K_c": "rotate_cw", "K_x": "rotate_180",
    "K_LCTRL": "rotate_ccw", "K_z": "rotate_ccw", "K_SPACE": "hard_drop",
    "K_DOWN": "soft_drop", "K_LEFT": "left", "K_RIGHT": "right",
    "K_LSHIFT": "hold",
}


def _import_dummy_display():
    if str(LEGACY_DIR) not in sys.path:
        sys.path.insert(0, str(LEGACY_DIR))
    try:
        import pygame  # noqa: F401
        from utilities.dummy import DummyDisplay
    except ImportError as exc:  # pygame (or numpy) missing: headless host
        raise SystemExit(
            f"desktop viewer needs pygame and numpy ({exc}); use --html or "
            "--ansi instead") from exc
    return pygame, DummyDisplay


def run_viewer(seed=1, frames=30 * 60 * 5, bot=None, scale=50):
    """Open a window and play (``bot`` given) or take keyboard input."""
    pygame, DummyDisplay = _import_dummy_display()
    from tetris_engine.adapter import run
    from tetris_engine.animation import TetrisAnimation

    display = DummyDisplay(scale)
    keys = {getattr(pygame, name): action for name, action in KEYMAP.items()}
    pygame.key.set_repeat(99, 66)  # legacy DAS 3 frames, ARR 2 frames

    def keyboard(_state):
        events = []
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                raise SystemExit(0)
            if ev.type in (pygame.KEYDOWN, pygame.KEYUP) and ev.key in keys:
                events.append((keys[ev.key], ev.type == pygame.KEYDOWN))
        return events

    def controller(state):
        pygame.event.pump()
        return bot(state) if bot else keyboard(state)

    return run(display, TetrisAnimation(seed), frames, controller=controller)
