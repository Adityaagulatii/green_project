"""Constant tables from SPEC.md v1 (all data, no behaviour).

Every table here is transcribed from impl/python/legacy/tetris.py and is
checked against the legacy module by tests/test_differential.py.
"""

# Display and padded board geometry (SPEC §2, §3).
ROWS, COLS = 17, 9
PAD_ROWS, PAD_COLS = ROWS + 3, COLS + 2  # 20 x 11
FPS = 30
SPAWN_ROW, SPAWN_COL = 0, 3

# Cell codes. "." is empty; "W" is white (border / flash / game-over fill);
# "G" is the ghost (render only); piece codes are the shape letters.
EMPTY = "."
WHITE = "W"
GHOST = "G"

# Palette (SPEC §4.1). RGB triples, already inside 0..255.
PALETTE = {
    ".": (0, 0, 0),
    "W": (255, 255, 255),
    "I": (0, 255, 255),
    "J": (0, 0, 255),
    "L": (255, 170, 0),
    "O": (255, 255, 0),
    "S": (0, 255, 0),
    "Z": (255, 0, 0),
    "T": (153, 0, 255),
    "G": (42, 42, 42),
}

# Canonical piece order: the bag starts from this order before shuffling.
BAG_ORDER = ("I", "J", "L", "O", "S", "Z", "T")

# Shapes x rotation states, 4x4 boxes, row-major (SPEC §4.2).
SHAPES = {
    "I": (
        ("....", "IIII", "....", "...."),
        ("..I.", "..I.", "..I.", "..I."),
        ("....", "....", "IIII", "...."),
        (".I..", ".I..", ".I..", ".I.."),
    ),
    "J": (
        ("....", ".J..", ".JJJ", "...."),
        ("....", "..JJ", "..J.", "..J."),
        ("....", "....", ".JJJ", "...J"),
        ("....", "..J.", "..J.", ".JJ."),
    ),
    "L": (
        ("....", "...L", ".LLL", "...."),
        ("....", "..L.", "..L.", "..LL"),
        ("....", "....", ".LLL", ".L.."),
        ("....", ".LL.", "..L.", "..L."),
    ),
    "O": (
        ("....", ".OO.", ".OO.", "...."),
        ("....", "....", ".OO.", ".OO."),
        ("....", "....", "OO..", "OO.."),
        ("....", "OO..", "OO..", "...."),
    ),
    "S": (
        ("....", "..SS", ".SS.", "...."),
        ("....", "..S.", "..SS", "...S"),
        ("....", "....", "..SS", ".SS."),
        ("....", ".S..", ".SS.", "..S."),
    ),
    "Z": (
        ("....", ".ZZ.", "..ZZ", "...."),
        ("....", "...Z", "..ZZ", "..Z."),
        ("....", "....", ".ZZ.", "..ZZ"),
        ("....", "..Z.", ".ZZ.", ".Z.."),
    ),
    "T": (
        ("....", "..T.", ".TTT", "...."),
        ("....", "..T.", "..TT", "..T."),
        ("....", "....", ".TTT", "..T."),
        ("....", "..T.", ".TT.", "..T."),
    ),
}

# Occupied (i, j) offsets per shape/rotation, row-major.
CELLS = {
    shape: tuple(
        tuple((i, j) for i in range(4) for j in range(4) if box[i][j] != ".")
        for box in states
    )
    for shape, states in SHAPES.items()
}

_JLSTZ = (
    ((0, 0), (0, 0), (0, 0), (0, 0), (0, 0)),
    ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),
    ((0, 0), (0, 0), (0, 0), (0, 0), (0, 0)),
    ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),
)
_O = (
    ((0, 0),) * 5,
    ((0, -1),) * 5,
    ((-1, -1),) * 5,
    ((-1, 0),) * 5,
)

# SRS offset table, (x, y) with y pointing UP (SPEC §5.3).
KICKS = {
    "J": _JLSTZ, "L": _JLSTZ, "S": _JLSTZ, "T": _JLSTZ, "Z": _JLSTZ,
    "I": (
        ((0, 0), (-1, 0), (2, 0), (-1, 0), (2, 0)),
        ((0, 0), (1, 0), (1, 0), (1, 1), (1, -2)),
        ((0, 0), (2, 0), (-1, 0), (2, -1), (-1, -1)),
        ((0, 0), (0, 0), (0, 0), (0, -2), (0, 1)),
    ),
    "O": _O,
}

_JLSTZ_180 = (
    ((0, 0), (0, 1), (1, 0), (-1, 0), (0, -1)),
    ((0, 0), (1, 0), (0, 1), (0, -1), (-1, 0)),
    ((0, 0),) * 5,
    ((0, 0),) * 5,
)

# 180-degree source table (QUIRK-1: combined with KICKS for the target).
KICKS_180 = {
    "J": _JLSTZ_180, "L": _JLSTZ_180, "S": _JLSTZ_180, "T": _JLSTZ_180,
    "Z": _JLSTZ_180,
    "I": (
        ((0, 0), (0, 0), (-1, 0), (2, 0), (-1, 1)),
        ((0, 0), (0, 0), (0, 1), (0, -2), (1, 1)),
        ((0, 0),) * 5,
        ((0, 0),) * 5,
    ),
    "O": _O,
}

# Gravity denominators per level: GRAVITY[level] = 1 / GRAVITY_DEN[level].
GRAVITY_DEN = (48, 43, 38, 33, 28, 23, 18, 13, 8, 6,
               5, 5, 5, 4, 4, 4, 3, 3, 3, 2,
               2, 2, 2, 2, 2, 2, 2, 2, 2, 1)

# Handling (frames / counters), SPEC §5.
DCD = 2
DCD_PER_FRAME = 2

# Animation lengths in frames (SPEC §8).
CLEAR_FLASH_FRAMES = 5
FILL_FRAMES_PER_ROW = 2
GAMEOVER_WAIT_FRAMES = 150
FALL_FRAMES_PER_ROW = 2
COUNTDOWN_FRAMES_PER_DIGIT = 30
FILL_FRAMES = FILL_FRAMES_PER_ROW * ROWS            # 34
FALL_FRAMES = FALL_FRAMES_PER_ROW * ROWS            # 34
GAMEOVER_FRAMES = FILL_FRAMES + GAMEOVER_WAIT_FRAMES + FALL_FRAMES  # 218
COUNTDOWN_FRAMES = 3 * COUNTDOWN_FRAMES_PER_DIGIT    # 90

# Countdown glyphs: the first 17 rows of the legacy 19-row arrays
# (QUIRK-9: the legacy rows 17 and 18 are black and off-screen).
_GLYPH_TOP = (".........",) * 3
_GLYPH_BOTTOM = (".........",) * 4
COUNTDOWN = {
    "3": _GLYPH_TOP + (
        "..WWWWW..", "..WWWWW..", ".....WW..", ".....WW..",
        "..WWWWW..", "..WWWWW..", ".....WW..", ".....WW..",
        "..WWWWW..", "..WWWWW..",
    ) + _GLYPH_BOTTOM,
    "2": _GLYPH_TOP + (
        "..WWWWW..", "..WWWWW..", ".....WW..", ".....WW..",
        "..WWWWW..", "..WWWWW..", "..WW.....", "..WW.....",
        "..WWWWW..", "..WWWWW..",
    ) + _GLYPH_BOTTOM,
    "1": _GLYPH_TOP + (
        "....WW...",
    ) * 10 + _GLYPH_BOTTOM,
}

ACTIONS = ("left", "right", "soft_drop", "hard_drop",
           "rotate_cw", "rotate_ccw", "rotate_180", "hold")
