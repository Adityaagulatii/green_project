"""Pure game engine for SPEC.md v1 conformance mode.

State is an immutable dataclass. ``step(state, events)`` advances exactly
one display frame (1/30 s) and returns a new state; ``frame.render(state)``
turns a state into a 17x9 frame. Nothing here does I/O, reads a clock or
uses global randomness.
"""

from dataclasses import dataclass, replace
from typing import NamedTuple

from . import tables as T
from .prng import seed_state, shuffle_bag

COUNTDOWN = "countdown"
PLAYING = "playing"
CLEARING = "clearing"
GAMEOVER = "gameover"
PHASES = (COUNTDOWN, PLAYING, CLEARING, GAMEOVER)


class Piece(NamedTuple):
    shape: str
    rot: int
    row: int  # padded-board row of the 4x4 box's top-left cell
    col: int  # padded-board column of the 4x4 box's top-left cell


class Held(NamedTuple):
    shape: str
    rot: int


class Resume(NamedTuple):
    """The unfinished remainder of a logical frame that was suspended by a
    line-clear flash or a game-over sequence (SPEC §9.2)."""
    events: tuple  # remaining (action, down) events of the batch
    stages: bool  # whether the level check and gravity stages still run


_EMPTY_ROW = T.WHITE + T.EMPTY * T.COLS + T.WHITE
_WHITE_ROW = T.WHITE * T.PAD_COLS
EMPTY_BOARD = (_WHITE_ROW,) + (_EMPTY_ROW,) * T.ROWS + (_WHITE_ROW,) * 2


@dataclass(frozen=True)
class State:
    phase: str
    timer: int
    boot: bool
    board: tuple  # PAD_ROWS strings of PAD_COLS cell codes
    active: Piece
    hold: Held | None
    hold_available: bool
    rng: int
    bag: tuple
    bag_index: int
    level: int
    lines: int
    score: int
    high_score: int
    acc: float
    dcd: int
    cw_available: bool
    ccw_available: bool
    r180_available: bool
    hard_available: bool
    inbox: tuple
    resume: Resume | None
    flash: tuple | None
    pending_gameover: bool
    gameover_board: tuple | None
    gameover_dcd: int
    frame: int
    spawns: int


# ---------------------------------------------------------------- helpers

def collides(board, shape, rot, row, col):
    """True if any cell of the piece is outside the padded board or on a
    non-empty board cell (SPEC §4.4)."""
    for i, j in T.CELLS[shape][rot]:
        r, c = row + i, col + j
        if r < 0 or r >= T.PAD_ROWS or c < 0 or c >= T.PAD_COLS:
            return True
        if board[r][c] != T.EMPTY:
            return True
    return False


def gravity_increment(level):
    """Per-frame accumulator increment, IEEE-754 binary64: (1/den) * 2."""
    return (1.0 / T.GRAVITY_DEN[min(level, len(T.GRAVITY_DEN) - 1)]) * 2.0


def level_target(level):
    """Lines needed to leave ``level`` (QUIRK-7: the legacy "testing" formula,
    which equals level + 5 for every level >= 0)."""
    return min(level + 5, max(100, level * 5 - 50))


def drop_row(board, piece):
    """Lowest row the piece reaches by moving straight down."""
    r = piece.row
    while not collides(board, piece.shape, piece.rot, r + 1, piece.col):
        r += 1
    return r


def _draw(s):
    """Draw the next piece from the bag, refilling it lazily. Returns
    (piece, rng, bag, bag_index)."""
    rng, bag, idx = s.rng, s.bag, s.bag_index
    if idx >= len(bag):
        bag, rng = shuffle_bag(rng)
        idx = 0
    piece = Piece(bag[idx], 0, T.SPAWN_ROW, T.SPAWN_COL)
    return piece, rng, bag, idx + 1


def _fresh_game_fields(rng):
    bag, rng = shuffle_bag(rng)
    return dict(
        board=EMPTY_BOARD,
        active=Piece(bag[0], 0, T.SPAWN_ROW, T.SPAWN_COL),
        hold=None,
        hold_available=True,
        rng=rng,
        bag=bag,
        bag_index=1,
        level=0,
        lines=0,
        score=0,
        cw_available=True,
        ccw_available=True,
        r180_available=True,
        hard_available=True,
        inbox=(),
        flash=None,
        pending_gameover=False,
        gameover_board=None,
    )


# ---------------------------------------------------------------- public API

def new_game(seed):
    """Initial state S0 (before frame 0). Frame k is render(S_{k+1})."""
    fields = _fresh_game_fields(seed_state(seed))
    return State(
        phase=COUNTDOWN, timer=-1, boot=True,
        high_score=0, acc=0.0, dcd=T.DCD, resume=None, gameover_dcd=T.DCD,
        frame=0, spawns=1, **fields)


def normalize_events(events):
    out = []
    for ev in events:
        action, down = ev[0], ev[1]
        if action not in T.ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        out.append((action, bool(down)))
    return tuple(out)


def step(s, events=()):
    """Advance one display frame. ``events`` is a sequence of
    (action, down) pairs delivered at this frame, in order."""
    events = normalize_events(events)
    s = replace(s, frame=s.frame + 1)
    phase = s.phase

    if phase == COUNTDOWN:
        t = s.timer + 1
        if t < T.COUNTDOWN_FRAMES or (s.boot and t == T.COUNTDOWN_FRAMES):
            return replace(s, timer=t)  # input is discarded (QUIRK-12)
        if s.boot:
            s = replace(s, phase=PLAYING, timer=0, boot=False)
            return _logical_frame(s, events)
        # After a game over: finish the suspended logical frame.
        s = replace(s, phase=PLAYING, timer=0, inbox=s.inbox + events)
        return _resume(s)

    if phase == CLEARING:
        s = replace(s, inbox=s.inbox + events)  # queued, not lost
        t = s.timer - 1
        if t > 0:
            return replace(s, timer=t)
        s = replace(s, flash=None)
        if s.pending_gameover:
            return replace(s, pending_gameover=False, phase=GAMEOVER, timer=0)
        return _resume(replace(s, phase=PLAYING, timer=0))

    if phase == GAMEOVER:
        t = s.timer + 1
        if t < T.GAMEOVER_FRAMES:
            return replace(s, timer=t)  # input is discarded
        return _reset(s)

    return _logical_frame(s, events)


# ---------------------------------------------------------------- frame logic

def _logical_frame(s, events):
    s = replace(s, acc=s.acc + gravity_increment(s.level),
                dcd=s.dcd + T.DCD_PER_FRAME)
    batch = s.inbox + events
    return _run(replace(s, inbox=()), batch, True)


def _resume(s):
    r = s.resume
    s = replace(s, resume=None)
    if r is None:
        return s
    return _run(s, r.events, r.stages)


def _run(s, events, stages):
    for k, ev in enumerate(events):
        s = apply_action(s, ev[0], ev[1])
        if s.phase != PLAYING:
            return replace(s, resume=Resume(events[k + 1:], stages))
    if not stages:
        return s
    s = _level_check(s)
    if s.acc >= 1.0:
        s = replace(s, acc=0.0)
        s = _move_down(s)
        if s.phase != PLAYING:
            return replace(s, resume=Resume((), False))
    return s


def _level_check(s):
    target = level_target(s.level)
    if target <= s.lines:
        return replace(s, level=s.level + 1, lines=s.lines - target)
    return s


def _reset(s):
    fields = _fresh_game_fields(s.rng)
    return replace(s, phase=COUNTDOWN, timer=0, boot=False,
                   dcd=s.gameover_dcd, spawns=s.spawns + 1, **fields)


# ---------------------------------------------------------------- actions

def apply_action(s, action, down):
    """Apply one input event to a PLAYING state (SPEC §5)."""
    if action == "left":
        return _shift(s, -1) if down else s
    if action == "right":
        return _shift(s, 1) if down else s
    if action == "soft_drop":
        return _move_down(s) if down else s
    if action == "hard_drop":
        return _hard_drop(s, down)
    if action == "rotate_cw":
        return _rotate(s, "cw", down)
    if action == "rotate_ccw":
        return _rotate(s, "ccw", down)
    if action == "rotate_180":
        return _rotate(s, "180", down)
    if action == "hold":
        return _hold(s, down)
    raise ValueError(f"unknown action {action!r}")


def _shift(s, dc):
    a = s.active
    if collides(s.board, a.shape, a.rot, a.row, a.col + dc):
        return s
    return replace(s, active=a._replace(col=a.col + dc))


def _move_down(s):
    a = s.active
    if not collides(s.board, a.shape, a.rot, a.row + 1, a.col):
        return replace(s, active=a._replace(row=a.row + 1))
    return _lock(s, a, hard=False)


def _hard_drop(s, down):
    if down and s.hard_available:
        s = replace(s, hard_available=False)
        if s.dcd >= T.DCD:
            a = s.active
            s = _lock(s, a._replace(row=drop_row(s.board, a)), hard=True)
            s = replace(s, hold_available=True, dcd=0)
        return s
    if not down and not s.hard_available:
        return replace(s, hard_available=True, dcd=0)  # QUIRK-4
    return s


_LATCH = {"cw": "cw_available", "ccw": "ccw_available", "180": "r180_available"}


def _rotate(s, kind, down):
    latch = _LATCH[kind]
    available = getattr(s, latch)
    if down and available:
        s = replace(s, **{latch: False})
        if s.dcd < T.DCD:
            return s
        a = s.active
        if kind == "cw":
            new = (a.rot + 1) % 4
            src = T.KICKS[a.shape][a.rot]
        elif kind == "ccw":
            new = (a.rot - 1) % 4
            src = T.KICKS[a.shape][a.rot]
        else:
            new = (a.rot + 2) % 4
            src = T.KICKS_180[a.shape][a.rot]  # QUIRK-1
        dst = T.KICKS[a.shape][new]
        for i in range(5):
            dx = src[i][0] - dst[i][0]
            dy = src[i][1] - dst[i][1]
            row, col = a.row - dy, a.col + dx
            if not collides(s.board, a.shape, new, row, col):
                return replace(s, active=Piece(a.shape, new, row, col), dcd=0)
        return s
    if not down and not available:
        if kind == "ccw":
            return replace(s, **{latch: True}, dcd=0)  # QUIRK-3
        return replace(s, **{latch: True})
    return s


def _hold(s, down):
    if not s.hold_available or not down:
        return s
    a = s.active
    if s.hold is None:
        piece, rng, bag, idx = _draw(s)
        s = replace(s, active=piece, rng=rng, bag=bag, bag_index=idx,
                    spawns=s.spawns + 1)
    else:  # QUIRK-6: rotation kept, no collision check
        s = replace(s, active=Piece(s.hold.shape, s.hold.rot,
                                    T.SPAWN_ROW, T.SPAWN_COL))
    return replace(s, hold=Held(a.shape, a.rot), hold_available=False)


def _lock(s, piece, hard):
    """Write the piece into the board, clear lines, spawn the next piece and
    detect game over (SPEC §6, §8)."""
    rows = [list(r) for r in s.board]
    for i, j in T.CELLS[piece.shape][piece.rot]:
        rows[piece.row + i][piece.col + j] = piece.shape
    board = tuple("".join(r) for r in rows)

    full = [r for r in range(1, T.ROWS + 1)
            if all(board[r][c] != T.EMPTY for c in range(1, T.COLS + 1))]
    flash = None
    lines, score = s.lines, s.score
    if full:
        flash = tuple(_WHITE_ROW if r in full else board[r]
                      for r in range(T.PAD_ROWS))
        kept = list(board)
        for r in full:  # ascending: delete row r, push an empty row at 1
            del kept[r]
            kept.insert(1, _EMPTY_ROW)
            lines += 1
            score += 100 * (lines // 10 + 1)  # QUIRK-5
        board = tuple(kept)

    nxt, rng, bag, idx = _draw(s)
    s = replace(s, board=board, lines=lines, score=score, active=nxt, rng=rng,
                bag=bag, bag_index=idx, hold_available=True,
                spawns=s.spawns + 1)
    over = collides(board, nxt.shape, nxt.rot, nxt.row, nxt.col)
    if over:
        s = replace(s, high_score=max(s.high_score, s.score),
                    gameover_board=board,
                    gameover_dcd=0 if hard else T.DCD)  # QUIRK-11
    if flash is not None:
        return replace(s, phase=CLEARING, timer=T.CLEAR_FLASH_FRAMES,
                       flash=flash, pending_gameover=over)
    if over:
        return replace(s, phase=GAMEOVER, timer=0)
    return s
