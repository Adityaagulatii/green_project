"""The Animation interface (SPEC §10.3): anything that yields a 17x9 frame
per 1/30 s tick. The game is one Animation; embodied behaviours (gestures,
mood colours, world transitions) are others.
"""

from typing import Any, Protocol, Sequence, Tuple

from . import core
from .frame import render

Frame = Tuple[Tuple[Tuple[int, int, int], ...], ...]


class Animation(Protocol):
    def init(self) -> Any:
        """Return the initial state."""

    def tick(self, state: Any, events: Sequence[Tuple[str, bool]]) -> Any:
        """Advance one frame; must be pure (no I/O, no clock)."""

    def render(self, state: Any) -> Frame:
        """Return the 17x9 RGB frame for ``state``."""


class TetrisAnimation:
    """The game engine wrapped as an Animation."""

    def __init__(self, seed=1):
        self.seed = seed

    def init(self):
        return core.new_game(self.seed)

    def tick(self, state, events=()):
        return core.step(state, events)

    def render(self, state):
        return render(state)
