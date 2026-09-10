"""P19 known answers (SPEC Appendix A) and the transcribed tables, checked
against the legacy module itself."""

import pytest

from legacy_harness import RGB_TO_CODE, load_legacy
from tetris_engine import core
from tetris_engine import tables as T
from tetris_engine.frame import codes_digest, frame_digest, render
from tetris_engine.prng import seed_state, shuffle_bag, xorshift32

XORSHIFT = {
    1: [270369, 67634689, 2647435461, 307599695, 2398689233],
    42: [11355432, 2836018348, 476557059, 3648046016, 3759983556],
    0: [1359758873, 3761132862, 2075758394, 25405621, 3862129951],
}
BAGS = {
    1: ["SILOZTJ", "LJOZSTI", "ZIOJSLT"],
    42: ["JLOIZTS", "ILJOZST", "OZITJLS"],
    0: ["ZLOJTIS", "ZOTSIJL", "JITOLSZ"],
}
BLACK = "e0ee29ce7978a33861e6e63545deda9e734ea784ee8e4ba6fd6aa56b775f6ca9"
WHITE = "cc1c8c603a0863247abc4b8a117a234714b37d2be10ca27218301e5e617830d8"
# SPEC §7.3: frames between drops from acc = 0, per DEN.
CADENCE = {48: 25, 43: 22, 38: 20, 33: 17, 28: 15, 23: 12, 18: 9, 13: 7,
           8: 4, 6: 3, 5: 3, 4: 2, 3: 2, 2: 1, 1: 1}


@pytest.mark.parametrize("seed", sorted(XORSHIFT))
def test_p19_xorshift_vectors(seed):
    x, out = seed_state(seed), []
    for _ in range(5):
        x = xorshift32(x)
        out.append(x)
    assert out == XORSHIFT[seed]


@pytest.mark.parametrize("seed", sorted(BAGS))
def test_p19_bag_vectors(seed):
    x, bags = seed_state(seed), []
    for _ in range(3):
        bag, x = shuffle_bag(x)
        bags.append("".join(bag))
    assert bags == BAGS[seed]
    assert core.new_game(seed).active.shape == BAGS[seed][0][0]


def test_p19_digest_vectors():
    assert codes_digest(("." * 9,) * 17) == BLACK
    assert codes_digest(("W" * 9,) * 17) == WHITE
    assert frame_digest(render(core.new_game(5))) == BLACK  # pre-boot


def test_gravity_cadence_table():
    for den, frames in CADENCE.items():
        level = T.GRAVITY_DEN.index(den)
        acc, n = 0.0, 0
        while acc < 1.0:
            acc += core.gravity_increment(level)
            n += 1
        assert n == frames, den


def test_level_target_is_level_plus_five():
    for level in range(0, 500):
        assert core.level_target(level) == level + 5


# ---------------------------------------------------------- vs legacy

@pytest.fixture(scope="module")
def legacy():
    return load_legacy()


def _codes(arr):
    return tuple("".join(RGB_TO_CODE[(c.r, c.g, c.b)] for c in row) for row in arr)


def test_shapes_match_legacy(legacy):
    assert list(legacy.SHAPES) == list(T.BAG_ORDER)  # canonical order
    for shape, states in legacy.SHAPES.items():
        assert tuple(_codes(s) for s in states) == tuple(
            tuple(row.replace(shape, RGB_TO_CODE[_rgb(legacy, shape)])
                  for row in box) for box in T.SHAPES[shape])


def _rgb(legacy, shape):
    color = {"I": legacy.C, "J": legacy.B, "L": legacy.O, "O": legacy.Y,
             "S": legacy.G, "Z": legacy.R, "T": legacy.P}[shape]
    return (color.r, color.g, color.b)


def test_palette_matches_legacy(legacy):
    for code, name in {".": "X", "W": "W", "I": "C", "J": "B", "L": "O",
                       "O": "Y", "S": "G", "Z": "R", "T": "P", "G": "A"}.items():
        c = getattr(legacy, name)
        assert (c.r, c.g, c.b) == T.PALETTE[code]


def test_kick_tables_match_legacy(legacy):
    for shape in T.BAG_ORDER:
        assert [[tuple(x) for x in row] for row in legacy.KICK_TABLE[shape]] \
            == [list(row) for row in T.KICKS[shape]]
        assert [[tuple(x) for x in row] for row in legacy.KICK_TABLE_180[shape]] \
            == [list(row) for row in T.KICKS_180[shape]]


def test_gravity_matches_legacy(legacy):
    assert list(legacy.GRAVITY) == [1 / d for d in T.GRAVITY_DEN]
    for level in range(40):
        g = legacy.GRAVITY[min(level, len(legacy.GRAVITY) - 1)]
        assert g * 2 == core.gravity_increment(level)


def test_countdown_glyphs_match_legacy(legacy):
    for digit, arr in legacy.COUNTDOWN.items():
        rows = _codes(arr)
        assert len(rows) == 19  # QUIRK-9
        assert rows[:17] == T.COUNTDOWN[digit]
        assert rows[17:] == (".........",) * 2


def test_spawn_matches_legacy(legacy):
    assert legacy.Tetromino("T").getPosition() == [T.SPAWN_ROW, T.SPAWN_COL]
    assert legacy.FPS == T.FPS
