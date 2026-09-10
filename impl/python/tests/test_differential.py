"""Differential suite: the engine against the UNCHANGED legacy tetris.py.

The legacy handler methods (``_moveLeft``, ``_rotateCW``, ``_hardDrop``,
``_hold``, ``_moveDown`` ...) run for real, on real numpy, with pygame,
keyboard and time stubbed and the Bag forced onto the spec PRNG. After
every logical frame we compare the whole padded board, the active and held
pieces, score/level/lines/high score, the DCD counter, the latches, the
gravity accumulator, the bag, and every frame sent to the display.

Divergences found so far: none (experiments/001-legacy-differential).
The only adaptations are documented QUIRKs of the display timeline:
duplicate sends collapse (a frame held n slots == one send) and the
post-game-over black frame is superseded (QUIRK-10).
"""

import os

import pytest
from hypothesis import given
from hypothesis import strategies as st

from legacy_harness import (EngineHarness, LegacyHarness, collapse,
                            legacy_visible, load_legacy)
from tetris_engine import ACTIONS
from tetris_engine import tables as T
from tetris_engine.core import collides
from tetris_sim.bot import Bot

events = st.lists(st.tuples(st.sampled_from(ACTIONS), st.booleans()),
                  max_size=4)
seeds = st.integers(0, 2 ** 32 - 1)


def _diff(a, b):
    return {k: (a[k], b[k]) for k in a if a[k] != b[k]}


def run_both(seed, frames, bot=False):
    L, E = LegacyHarness(seed), EngineHarness(seed)
    assert collapse(L.boot_frames) == collapse(E.boot_frames)
    assert L.snapshot() == E.snapshot(), _diff(L.snapshot(), E.snapshot())
    b = Bot() if bot else None
    stats = {"clears": 0, "gameovers": 0}
    for k, evs in enumerate(frames):
        evs = (list(b(E.s)) if b else []) + list(evs)
        lf = collapse(legacy_visible(L.frame(evs)))
        ef = E.frame(evs)
        stats["clears"] += 1 < len(ef) < 50
        stats["gameovers"] += len(ef) >= 50
        ls, es = L.snapshot(), E.snapshot()
        assert ls == es, (k, evs, _diff(ls, es))
        assert lf == collapse(ef), (k, evs, "display stream differs")
    return stats


@given(seeds, st.lists(events, max_size=160))
def test_random_input_matches_legacy(seed, frames):
    run_both(seed, frames)


@given(seeds, st.lists(st.one_of(st.just([]), events), min_size=50,
                       max_size=260))
def test_bot_plus_noise_matches_legacy(seed, frames):
    run_both(seed, frames, bot=True)


@pytest.mark.parametrize("seed", [1, 42, 2026])
def test_long_bot_game_matches_legacy(seed):
    n = 3000 if os.environ.get("TETRIS_SLOW") else 400
    stats = run_both(seed, [[]] * n, bot=True)
    assert stats["clears"] > 0


# ------------------------------------------------ collision bounds

LEGACY_CODE = {".": "X", "W": "W", "I": "C", "J": "B", "L": "O", "O": "Y",
               "S": "G", "Z": "R", "T": "P"}


def _candidates(piece):
    """Every position the rules can test from ``piece``: shifts, the drop
    and all 5 kick candidates of all three rotations."""
    shape, rot, row, col = piece
    out = [(shape, rot, row, col - 1), (shape, rot, row, col + 1),
           (shape, rot, row + 1, col)]
    for new, src_table in (((rot + 1) % 4, T.KICKS), ((rot - 1) % 4, T.KICKS),
                           ((rot + 2) % 4, T.KICKS_180)):
        src, dst = src_table[shape][rot], T.KICKS[shape][new]
        for i in range(5):
            dx, dy = src[i][0] - dst[i][0], src[i][1] - dst[i][1]
            out.append((shape, new, row - dy, col + dx))
    return out


def _reachable(board):
    """BFS over every piece placement reachable from spawn (and from a
    hold swap-in in any rotation, QUIRK-6)."""
    seen, frontier = set(), [(s, r, 0, 3) for s in T.BAG_ORDER for r in range(4)]
    seen.update(frontier)
    while frontier:
        nxt = []
        for p in frontier:
            for cand in _candidates(p):
                if cand not in seen and not collides(board, *cand):
                    seen.add(cand)
                    nxt.append(cand)
        frontier = nxt
    return seen


def _legacy_board(mod, harness, board):
    bg = harness.t._background
    for r in range(20):
        for c in range(11):
            bg[r][c] = getattr(mod, LEGACY_CODE[board[r][c]])


def _check_board(board):
    mod = load_legacy()
    h = LegacyHarness(1)
    _legacy_board(mod, h, board)
    checked = 0
    for piece in _reachable(board):
        for shape, rot, row, col in _candidates(piece):
            legacy = h.t._checkCollision([row, col], mod.SHAPES[shape][rot])
            assert legacy == collides(board, shape, rot, row, col), \
                (shape, rot, row, col)
            checked += 1
    return checked


def test_collision_rule_equals_numpy_wraparound_on_empty_board():
    from tetris_engine.core import EMPTY_BOARD
    assert _check_board(EMPTY_BOARD) > 10000


@given(st.lists(st.integers(0, 510), min_size=17, max_size=17),
       st.integers(0, 17))
def test_collision_rule_equals_numpy_wraparound_on_random_boards(masks, h):
    rows = ["W" + "".join("J" if (m >> c) & 1 and r >= 17 - h else "."
                          for c in range(9)) + "W"
            for r, m in enumerate(masks)]
    board = ("W" * 11,) + tuple(rows) + ("W" * 11,) * 2
    _check_board(board)
