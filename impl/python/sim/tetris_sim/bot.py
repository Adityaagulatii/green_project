"""A small heuristic auto-player for demos and test coverage.

For each new piece it tries every reachable (rotation, column), drops it on
a copy of the board and scores the result with the classic four-feature
heuristic (aggregate height, completed lines, holes, bumpiness). It then
emits input events frame by frame, exactly as a player would, so its games
are ordinary input traces. Fully deterministic: no randomness.
"""

from dataclasses import replace
from itertools import pairwise

from tetris_engine import core
from tetris_engine import tables as T

WEIGHTS = {"height": -0.51, "lines": 0.76, "holes": -0.36, "bump": -0.18,
           "top": -2.0}

_ROTATIONS = (None, "rotate_cw", "rotate_180", "rotate_ccw")
_ROT_FOR_DELTA = {1: "rotate_cw", 2: "rotate_180", 3: "rotate_ccw"}


def _landed_grid(board, piece):
    """Place ``piece`` at its drop row; return (17x9 grid rows, lines)."""
    row = core.drop_row(board, piece)
    rows = [list(r[1:T.COLS + 1]) for r in board[1:T.ROWS + 1]]
    for i, j in T.CELLS[piece.shape][piece.rot]:
        r, c = row + i - 1, piece.col + j - 1
        if 0 <= r < T.ROWS and 0 <= c < T.COLS:
            rows[r][c] = piece.shape
    kept = [r for r in rows if "." in r]
    lines = T.ROWS - len(kept)
    return [["."] * T.COLS for _ in range(lines)] + kept, lines


def evaluate(grid, lines, weights=WEIGHTS):
    heights, holes = [], 0
    for c in range(T.COLS):
        top = next((r for r in range(T.ROWS) if grid[r][c] != "."), T.ROWS)
        heights.append(T.ROWS - top)
        holes += sum(1 for r in range(top, T.ROWS) if grid[r][c] == ".")
    bump = sum(abs(a - b) for a, b in pairwise(heights))
    top_penalty = max(0, max(heights) - 11)
    return (weights["height"] * sum(heights) + weights["lines"] * lines
            + weights["holes"] * holes + weights["bump"] * bump
            + weights["top"] * top_penalty)


def plan(state, weights=WEIGHTS):
    """Best (rotation, column) for the active piece, or None."""
    probe = replace(state, dcd=T.DCD, cw_available=True, ccw_available=True,
                    r180_available=True)
    best, best_score = None, None
    seen = set()
    for rot_action in _ROTATIONS:
        p = probe
        if rot_action is not None:
            p = core.apply_action(probe, rot_action, True)
            if p.active.rot == probe.active.rot:
                continue  # rotation blocked
        start = p.active
        for dcol in range(-T.COLS, T.COLS + 1):
            a = start
            stepc = 1 if dcol > 0 else -1
            for _ in range(abs(dcol)):
                if core.collides(p.board, a.shape, a.rot, a.row, a.col + stepc):
                    break
                a = a._replace(col=a.col + stepc)
            key = (a.rot, a.col)
            if key in seen:
                continue
            seen.add(key)
            grid, lines = _landed_grid(p.board, a)
            score = evaluate(grid, lines, weights)
            if best_score is None or score > best_score:
                best, best_score = key, score
    return best


class Bot:
    """Controller: ``bot(state) -> [(action, down), ...]`` for this frame.

    ``pace``: act at most every ``pace`` frames (1 = every frame).
    ``think``: idle frames after a new piece appears.
    ``batch_shifts``: do all sideways moves and the drop in one frame.
    """

    def __init__(self, pace=1, think=0, batch_shifts=True, weights=WEIGHTS):
        self.pace = max(1, pace)
        self.think = think
        self.batch_shifts = batch_shifts
        self.weights = weights
        self._spawn = None
        self._target = None
        self._wait = 0

    def __call__(self, state):
        if state.phase != core.PLAYING:
            return []
        if state.spawns != self._spawn:
            self._spawn = state.spawns
            self._target = plan(state, self.weights)
            self._wait = self.think
        if self._wait > 0:
            self._wait -= 1
            return []
        self._wait = self.pace - 1
        if self._target is None:
            return [("hard_drop", True), ("hard_drop", False)]
        rot, col = self._target
        a = state.active
        if a.rot != rot:
            action = _ROT_FOR_DELTA[(rot - a.rot) % 4]
            return [(action, True), (action, False)]
        d = col - a.col
        move = "right" if d > 0 else "left"
        if d and not self.batch_shifts:
            return [(move, True), (move, False)]
        return [(move, True)] * abs(d) + [("hard_drop", True),
                                          ("hard_drop", False)]
