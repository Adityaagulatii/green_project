"""Tonight's Sky (SPEC §10.3): a fully pre-scripted, non-interactive light
sequence for the facade. No camera, no microphone, no live sensing -- every
star position and every reveal is a hard-coded constant below, and the
sequence just loops.

A fixed field of "stars" glows at low ambient brightness at all times. On
top of it, the animation cycles through named "constellations": different
subsets of that same fixed field, each with its own connecting lines and a
short invented myth, the way different cultures drew different pictures over
the same sky. Ours are original and MIT/hack-themed, with one real exception:
the Pleiades, included as a genuine star cluster (a dense clump, drawn as a
simultaneous soft brighten rather than stick-figure lines).

Tune the shapes by editing STAR_FIELD (fixed grid coordinates) and
CONSTELLATIONS (which indices belong to each shape, and the order lines are
drawn in) below. Frame-length constants are grouped just after them.
"""

from tetris_engine.tables import COLS, ROWS

# --------------------------------------------------------------- star field
# 27 fixed (row, col) coordinates, 0 <= row < 17 (0 = top), 0 <= col < 9.
# Designed once and never moved. Indices 0-6 are a deliberately dense clump
# (Pleiades); the rest are spread out so the invented shapes don't collide.
STAR_FIELD = [
    (0, 0), (0, 1), (1, 0), (1, 1), (1, 2), (2, 0), (2, 1),   # 0-6  Pleiades
    (3, 6), (3, 7), (4, 6), (4, 7),                           # 7-10 Square
    (6, 4), (7, 2), (7, 6), (9, 2), (9, 6), (10, 4),          # 11-16 Brass Rat
    (12, 1), (12, 5), (13, 3), (12, 3),                       # 17-20 Eye (+16)
    (13, 7), (14, 7), (15, 7), (15, 6), (15, 8), (16, 7),     # 21-26 Bot
]


class _C:
    """One constellation: a name, a subset of STAR_FIELD indices, connecting
    lines drawn in reveal order (empty for a cluster), and a short myth."""

    def __init__(self, name, stars, connections, myth):
        self.name = name
        self.stars = stars
        self.connections = connections
        self.myth = myth


# Cycle order matters: Brass Rat -> Eye share index 16, so that star persists
# across their transition (a free continuity effect, SPEC step 5) because
# they're placed back to back below.
CONSTELLATIONS = [
    _C("The Square", [7, 8, 9, 10],
       [(7, 8), (8, 10), (10, 9), (9, 7)],
       "A tetromino, adrift and unresolved, waiting for a line that never "
       "comes."),
    _C("The Brass Rat", [11, 12, 13, 14, 15, 16],
       [(11, 12), (12, 14), (14, 16), (16, 15), (15, 13), (13, 11)],
       "The ring every graduate wears home, cast here in six stars instead "
       "of brass."),
    _C("The Watchful Eye", [16, 17, 18, 19, 20],
       [(16, 17), (17, 19), (19, 18), (18, 16), (16, 20)],
       "It opens where the Rat's ring closes, and keeps a pupil trained on "
       "the tower below."),
    _C("The Wandering Bot", [21, 22, 23, 24, 25, 26],
       [(21, 22), (22, 23), (23, 24), (23, 25), (23, 26)],
       "A small builder, forever mid-stride, arms out for balance."),
    _C("The Pleiades", [0, 1, 2, 3, 4, 5, 6], [],
       "The one true constellation here: a huddle of sisters, too close "
       "together to draw apart."),
]

# --------------------------------------------------------------- timing
FADE_FRAMES = 75      # ~2.5s @ 30fps: ambient -> full
HOLD_FRAMES = 150     # ~5s: full brightness
# The engine's line-clear flash (tables.CLEAR_FLASH_FRAMES, 5 frames of solid
# white) is a property of core.State's padded piece/board and isn't a frame
# producer any other Animation can call into -- there's nothing to import.
# The closest fit for a plain RGB Animation is a clean mirror of the fade-in,
# which is what SPEC's fade-out/clear step asks for anyway.
FADEOUT_FRAMES = 75

STAR_COLOR = (255, 244, 214)   # warm starlight white
AMBIENT_FRAC = 0.20            # always-on background brightness
FULL_FRAC = 1.0
# A real facade has only 153 windows, floors and bays apart -- 1-3 lit
# in-between cells don't fuse into a perceived "line" the way screen pixels
# do, they just read as more scattered spots. Making a "set" line cell as
# bright as the stars it joins is the best a sparse grid can do to keep the
# path legible: same brightness, so the eye at least groups them by chance
# rather than a dim line looking like an afterthought next to full stars.
LINE_SET_FRAC = FULL_FRAC

# tetris_sim.html's encode() builds one shared palette across an entire
# recording (max 36 distinct colours total, not per frame) -- fine for the
# game's ~10 piece colours, but a continuously-eased brightness would mint a
# new colour almost every frame over a multi-minute loop. Quantizing to a
# fixed number of levels keeps the whole loop's palette bounded regardless of
# length, at the cost of faint banding during fades.
BRIGHT_LEVELS = 20

FADE_IN, HOLD, FADE_OUT = "fade_in", "hold", "fade_out"


def _ease(f):
    f = max(0.0, min(1.0, f))
    return f * f * (3 - 2 * f)


def _line_cells(a, b):
    """Grid cells strictly between star indices a and b (Bresenham),
    excluding both endpoints."""
    r0, c0 = STAR_FIELD[a]
    r1, c1 = STAR_FIELD[b]
    pts = []
    dr, dc = abs(r1 - r0), abs(c1 - c0)
    sr = 1 if r0 < r1 else -1
    sc = 1 if c0 < c1 else -1
    err = dr - dc
    r, c = r0, c0
    while True:
        pts.append((r, c))
        if r == r1 and c == c1:
            break
        e2 = 2 * err
        if e2 > -dc:
            err -= dc
            r += sr
        if e2 < dr:
            err += dr
            c += sc
    return pts[1:-1]


def _star_frac(cur, prev, phase, timer, fade_frames, fadeout_frames):
    """{field_index: brightness fraction} and {(r, c): fraction} for the
    transient cells of any lines currently drawn, for one render call."""
    stars = {i: AMBIENT_FRAC for i in range(len(STAR_FIELD))}
    lines = {}

    if phase == FADE_IN:
        shared_prev = set(cur.stars) & set(prev.stars)
        for i in shared_prev:
            stars[i] = FULL_FRAC  # persisted through the last fade-out

        if not cur.connections:  # a cluster: brighten together, softly
            k = AMBIENT_FRAC + (FULL_FRAC - AMBIENT_FRAC) * _ease(timer / fade_frames)
            for i in cur.stars:
                stars[i] = max(stars[i], k)
            return stars, lines

        steps = len(cur.connections)
        step_len = fade_frames / steps
        step_idx = min(steps - 1, int(timer // step_len))
        step_f = min(1.0, (timer - step_idx * step_len) / step_len)

        stars[cur.connections[0][0]] = FULL_FRAC
        for a, b in cur.connections[:step_idx]:
            stars[a] = FULL_FRAC
            stars[b] = FULL_FRAC
            for cell in _line_cells(a, b):
                lines[cell] = LINE_SET_FRAC

        a, b = cur.connections[step_idx]
        stars[a] = FULL_FRAC
        cells = _line_cells(a, b)
        if not cells:
            stars[b] = AMBIENT_FRAC + (FULL_FRAC - AMBIENT_FRAC) * _ease(step_f)
        else:
            head = min(len(cells) - 1, int(step_f * len(cells)))
            for i, cell in enumerate(cells):
                if i < head:
                    lines[cell] = LINE_SET_FRAC
                elif i == head:
                    glow = LINE_SET_FRAC + (FULL_FRAC - LINE_SET_FRAC) * _ease(
                        (step_f * len(cells)) % 1.0)
                    lines[cell] = max(lines.get(cell, 0.0), glow)
            stars[b] = FULL_FRAC if step_f >= 0.999 else \
                AMBIENT_FRAC + (FULL_FRAC - AMBIENT_FRAC) * _ease(step_f)
        return stars, lines

    if phase == HOLD:
        for i in cur.stars:
            stars[i] = FULL_FRAC
        for a, b in cur.connections:
            for cell in _line_cells(a, b):
                lines[cell] = LINE_SET_FRAC
        return stars, lines

    # FADE_OUT: mirror of the hold, but a star shared with the *next*
    # constellation just persists at full instead of fading (SPEC step 5).
    nxt = prev  # caller passes CONSTELLATIONS[idx + 1] as "prev" here
    shared_next = set(cur.stars) & set(nxt.stars)
    k = 1.0 - _ease(timer / fadeout_frames)
    for i in cur.stars:
        stars[i] = FULL_FRAC if i in shared_next else \
            AMBIENT_FRAC + (FULL_FRAC - AMBIENT_FRAC) * k
    for a, b in cur.connections:
        for cell in _line_cells(a, b):
            lines[cell] = LINE_SET_FRAC * k
    return stars, lines


class TonightsSky:
    """Pre-scripted, looping constellation sequence over a fixed star field.
    Pure Animation: state is (constellation index, phase, frames-in-phase).

    The three duration args default to SPEC's suggested ~2-3s fade / ~5s
    hold; pass smaller ones to shrink one full loop (e.g. for a host that
    caps how many frames a pre-rendered clip may hold)."""

    def __init__(self, fade_frames=FADE_FRAMES, hold_frames=HOLD_FRAMES,
                 fadeout_frames=FADEOUT_FRAMES):
        self.fade_frames = fade_frames
        self.hold_frames = hold_frames
        self.fadeout_frames = fadeout_frames

    def init(self):
        return (0, FADE_IN, 0)

    def tick(self, state, events=()):
        idx, phase, timer = state
        timer += 1
        if phase == FADE_IN and timer >= self.fade_frames:
            return (idx, HOLD, 0)
        if phase == HOLD and timer >= self.hold_frames:
            return (idx, FADE_OUT, 0)
        if phase == FADE_OUT and timer >= self.fadeout_frames:
            return ((idx + 1) % len(CONSTELLATIONS), FADE_IN, 0)
        return (idx, phase, timer)

    def render(self, state):
        idx, phase, timer = state
        n = len(CONSTELLATIONS)
        cur = CONSTELLATIONS[idx]
        adjacent = CONSTELLATIONS[(idx - 1) % n] if phase == FADE_IN \
            else CONSTELLATIONS[(idx + 1) % n]
        star_frac, line_frac = _star_frac(cur, adjacent, phase, timer,
                                           self.fade_frames, self.fadeout_frames)

        grid = [[0.0] * COLS for _ in range(ROWS)]
        for i, frac in star_frac.items():
            r, c = STAR_FIELD[i]
            grid[r][c] = max(grid[r][c], frac)
        for (r, c), frac in line_frac.items():
            grid[r][c] = max(grid[r][c], frac)

        def color(k):
            q = round(max(0.0, min(1.0, k)) * BRIGHT_LEVELS) / BRIGHT_LEVELS
            return tuple(int(round(ch * q)) for ch in STAR_COLOR)

        return tuple(tuple(color(grid[r][c]) for c in range(COLS))
                     for r in range(ROWS))
