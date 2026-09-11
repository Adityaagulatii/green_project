"""Frame producers for wal.sh/tools/display. Pure functions of (w, h, t) plus a
small mutable state; each yields a bytes object of w*h palette indices (0-15).
Palette indices follow the CGA order published in capabilities.json."""
import random

BLACK, BLUE, GREEN, CYAN, RED, MAGENTA, BROWN, LGREY, \
DGREY, LBLUE, LGREEN, LCYAN, LRED, LMAGENTA, YELLOW, WHITE = range(16)


def bars(w, h, seed=0):
    """16 vertical color bars; a static test pattern."""
    while True:
        yield bytes((x * 16) // w for y in range(h) for x in range(w))


def matrix(w, h, seed=0):
    """Falling columns: bright head, green trail, random restart."""
    rnd = random.Random(seed)
    heads = [rnd.randrange(-h, 0) for _ in range(w)]
    trail = max(3, h // 3)
    while True:
        f = bytearray(w * h)
        for x in range(w):
            y = heads[x]
            for k in range(trail):
                yy = y - k
                if 0 <= yy < h:
                    f[yy * w + x] = WHITE if k == 0 else LGREEN if k < trail // 2 else GREEN
            heads[x] = y + 1 if y - trail < h else rnd.randrange(-h, 0)
        yield bytes(f)


def fishbowl(w, h, seed=0):
    """Water, sand, one fish that turns at the walls, rising bubbles."""
    rnd = random.Random(seed)
    sand = max(1, h // 8)
    fx, fy, dx = w // 2, h // 2, 1
    bubbles = []
    t = 0
    while True:
        f = bytearray([BLUE]) * (w * h)
        for y in range(h - sand, h):
            for x in range(w):
                f[y * w + x] = BROWN if (x + y) % 3 else YELLOW
        if t % 3 == 0:
            fx += dx
            if fx <= 1 or fx >= w - 2:
                dx = -dx
            if rnd.random() < 0.2:
                fy = min(h - sand - 2, max(1, fy + rnd.choice((-1, 0, 1))))
        for cx, c in ((fx, YELLOW), (fx - dx, YELLOW), (fx - 2 * dx, LRED)):
            if 0 <= cx < w:
                f[fy * w + cx] = c
        if t % 4 == 0:
            bubbles.append([rnd.randrange(w), h - sand - 1])
        bubbles = [[x, y - 1] for x, y in bubbles if y > 0]
        for x, y in bubbles:
            f[y * w + x] = LCYAN
        t += 1
        yield bytes(f)


_TETROMINOES = (  # cells (x, y) and color: I O T S Z J L
    (((0, 0), (1, 0), (2, 0), (3, 0)), LCYAN),
    (((0, 0), (1, 0), (0, 1), (1, 1)), YELLOW),
    (((1, 0), (0, 1), (1, 1), (2, 1)), LMAGENTA),
    (((1, 0), (2, 0), (0, 1), (1, 1)), LGREEN),
    (((0, 0), (1, 0), (1, 1), (2, 1)), LRED),
    (((0, 0), (0, 1), (1, 1), (2, 1)), LBLUE),
    (((2, 0), (0, 1), (1, 1), (2, 1)), BROWN),
)


def _rotations(cells):
    """The distinct rotations of CELLS, each shifted so min x = min y = 0."""
    out = []
    for _ in range(4):
        mx, my = min(x for x, _ in cells), min(y for _, y in cells)
        norm = tuple(sorted((x - mx, y - my) for x, y in cells))
        if norm not in out:
            out.append(norm)
        cells = [(-y, x) for x, y in cells]
    return out


def _best_drop(board, rotations, w, h, rnd):
    """The (cells, px, py) of the hard drop that completes the most rows, then
    leaves the fewest holes, then lands lowest; None if every drop tops out."""
    top = [next((r for r in range(h) if board[r][x]), h) for x in range(w)]
    best = None
    for cells in rotations:
        bottom = {}
        for x, y in cells:
            bottom[x] = max(bottom.get(x, 0), y)
        for px in range(w - max(bottom)):
            py = min(top[px + x] - 1 - y for x, y in bottom.items())
            if py < 0:
                continue
            holes = sum(top[px + x] - 1 - (py + y) for x, y in bottom.items())
            filled = sum(1 for r in {py + y for _, y in cells}
                         if all(board[r][c] or (c - px, r - py) in cells for c in range(w)))
            key = (-filled, holes, -py, rnd.random())
            if best is None or key < best[0]:
                best = (key, cells, px, py)
    return best and best[1:]


def tetris(w, h, seed=0):
    """A self-playing Tetris on the display's own w x h field (a demo, not the
    17x9 SPEC game). Each piece falls one row every two frames to the drop
    that completes the most rows and leaves the fewest holes; full rows go,
    and a field that tops out blanks for a frame and starts over."""
    rnd = random.Random(seed)
    pieces = [(_rotations(cells), color) for cells, color in _TETROMINOES]
    board = [bytearray(w) for _ in range(h)]
    while True:
        rotations, color = rnd.choice(pieces)
        drop = _best_drop(board, rotations, w, h, rnd)
        if drop is None:
            board = [bytearray(w) for _ in range(h)]
            yield bytes(w * h)
            continue
        cells, px, land = drop
        for py in range(-max(y for _, y in cells), land + 1):
            f = bytearray().join(board)
            for x, y in cells:
                if py + y >= 0:
                    f[(py + y) * w + px + x] = color
            yield bytes(f)
            yield bytes(f)
        for x, y in cells:
            board[land + y][px + x] = color
        kept = [row for row in board if not all(row)]
        board = [bytearray(w) for _ in range(h - len(kept))] + kept


DEMOS = {"bars": bars, "matrix": matrix, "fishbowl": fishbowl, "tetris": tetris}

# The CGA colors in index order, as RGB; the mock capabilities.json lists them.
CGA = ((0x00, 0x00, 0x00), (0x00, 0x00, 0xAA), (0x00, 0xAA, 0x00), (0x00, 0xAA, 0xAA),
       (0xAA, 0x00, 0x00), (0xAA, 0x00, 0xAA), (0xAA, 0x55, 0x00), (0xAA, 0xAA, 0xAA),
       (0x55, 0x55, 0x55), (0x55, 0x55, 0xFF), (0x55, 0xFF, 0x55), (0x55, 0xFF, 0xFF),
       (0xFF, 0x55, 0x55), (0xFF, 0x55, 0xFF), (0xFF, 0xFF, 0x55), (0xFF, 0xFF, 0xFF))
_RGB = [bytes(c) for c in CGA]


def to_rgb(frame):
    """w*h palette indices -> the wire frame: w*h*3 bytes, row-major RGB."""
    return b"".join(_RGB[i] for i in frame)
