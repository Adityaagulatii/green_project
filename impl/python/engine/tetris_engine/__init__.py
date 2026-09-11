"""Pure Python engine for 17x9 Tetris, SPEC.md v1.

No numpy, pygame, clock or I/O in the core: ``new_game(seed)`` ->
``step(state, events)`` -> ``render(state)``.
"""

from .core import (
                   CLEARING,
                   COUNTDOWN,
                   GAMEOVER,
                   PHASES,
                   PLAYING,
                   Held,
                   Piece,
                   State,
                   apply_action,
                   collides,
                   gravity_increment,
                   level_target,
                   new_game,
                   step,
)
from .frame import codes_digest, frame_digest, render, render_codes
from .tables import ACTIONS, COLS, FPS, PALETTE, ROWS

SPEC_VERSION = 1

__all__ = [
    "ACTIONS", "CLEARING", "COLS", "COUNTDOWN", "FPS", "GAMEOVER", "Held",
    "PALETTE", "PHASES", "PLAYING", "Piece", "ROWS", "SPEC_VERSION", "State",
    "apply_action", "codes_digest", "collides", "frame_digest",
    "gravity_increment", "level_target", "new_game", "render",
    "render_codes", "step",
]
