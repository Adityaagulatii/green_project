"""Frame composition and digests (SPEC §7, §8, §9.4)."""

import hashlib

from . import tables as T
from .core import CLEARING, COUNTDOWN, GAMEOVER, collides, drop_row

_BLACK_CODES = ("." * T.COLS,) * T.ROWS
_WHITE_CODES = ("W" * T.COLS,) * T.ROWS
_CODE_BYTES = {code: bytes(rgb) for code, rgb in T.PALETTE.items()}


def crop(board):
    """Padded board -> 17 display rows of 9 cell codes."""
    return tuple(row[1:T.COLS + 1] for row in board[1:T.ROWS + 1])


def compose(board, piece, ghost=True):
    """Background, then the gray ghost, then the active piece; then crop."""
    rows = [list(r) for r in board]
    if ghost:
        g = drop_row(board, piece)
        for i, j in T.CELLS[piece.shape][piece.rot]:
            rows[g + i][piece.col + j] = T.GHOST
    for i, j in T.CELLS[piece.shape][piece.rot]:
        rows[piece.row + i][piece.col + j] = piece.shape
    return crop(tuple("".join(r) for r in rows))


def _gameover_codes(board, t):
    if t < T.FILL_FRAMES:
        k = t // T.FILL_FRAMES_PER_ROW  # rows 17-k .. 17 are white
        first_white = T.ROWS - k
        return tuple("W" * T.COLS if r >= first_white else board[r][1:T.COLS + 1]
                     for r in range(1, T.ROWS + 1))
    t -= T.FILL_FRAMES
    if t < T.GAMEOVER_WAIT_FRAMES:
        return _WHITE_CODES
    t -= T.GAMEOVER_WAIT_FRAMES
    k = t // T.FALL_FRAMES_PER_ROW  # rows 1 .. 1+k are black
    return tuple("." * T.COLS if r <= 1 + k else "W" * T.COLS
                 for r in range(1, T.ROWS + 1))


def render_codes(s):
    """17 strings of 9 cell codes for the frame shown at state ``s``."""
    if s.phase == COUNTDOWN:
        t = s.timer
        if t < 0 or t >= T.COUNTDOWN_FRAMES:
            return _BLACK_CODES
        return T.COUNTDOWN["321"[t // T.COUNTDOWN_FRAMES_PER_DIGIT]]
    if s.phase == CLEARING:
        return crop(s.flash)
    if s.phase == GAMEOVER:
        return _gameover_codes(s.gameover_board, s.timer)
    return compose(s.board, s.active)


def codes_to_rgb(codes):
    return tuple(tuple(T.PALETTE[ch] for ch in row) for row in codes)


def render(s):
    """The display Frame: 17 rows x 9 columns of (r, g, b) ints."""
    return codes_to_rgb(render_codes(s))


def frame_bytes(frame):
    """Canonical row-major R,G,B bytes of a 17x9 RGB frame (459 bytes)."""
    return bytes(v for row in frame for rgb in row for v in rgb)


def codes_bytes(codes):
    return b"".join(_CODE_BYTES[ch] for row in codes for ch in row)


def frame_digest(frame):
    return hashlib.sha256(frame_bytes(frame)).hexdigest()


def codes_digest(codes):
    return hashlib.sha256(codes_bytes(codes)).hexdigest()


__all__ = ["render", "render_codes", "compose", "crop", "codes_to_rgb",
           "frame_bytes", "codes_bytes", "frame_digest", "codes_digest",
           "collides"]
