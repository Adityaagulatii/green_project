#!/usr/bin/env python3
"""Render deterministic snapshots of the seed-42 bot demo.

    python docs/media/render_snapshots.py            # (re)write docs/media/snapshots/
    python docs/media/render_snapshots.py --check    # verify committed files vs the engine

The run is exactly ``python -m tetris_sim --seed 42 --bot``: the pure engine
(``impl/python/engine``) driven by the demo bot ``Bot(pace=3, think=6,
batch_shifts=False)``. Moments (countdown, first spawn, line clear, level
up, game over, ...) are *found* in the frame stream by predicates on the
engine state, not hard-coded, so a spec revision that moves them moves the
snapshots with it.

For every moment it writes four files named ``f<frame>-t<seconds>s-<event>``:

- ``.grid.svg`` / ``.grid.png``: the plain 17x9 grid, 50 px per cell with no
  gaps. These are exactly the pixels the legacy pygame ``DummyDisplay``
  (``impl/python/legacy/utilities/dummy.py``, scalar 50) would draw for
  the frame: ``pygame.Rect(j*50, i*50, 50, 50)`` filled with the cell RGB.
- ``.facade.svg`` / ``.facade.png``: the frame on an illustrative Green
  Building (Building 54) facade: 21 stories, the 17 lit floors (20..4)
  times 9 bays, using the PROVISIONAL row->floor mapping of SPEC §10.4.
  The PNG has no text labels.

plus ``index.json``: frame, time, event, SPEC §9.4 digest (sha256 of the
459 RGB bytes) and file names. ``--check`` re-runs the engine and verifies
the digests, the SVG bytes and the decoded PNG pixels; it exits non-zero
on any finding.

Standard library only: PNGs are written with ``zlib`` (no Pillow, no
cairosvg, no pygame).
"""

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for sub in ("impl/python/engine", "impl/python/sim"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from tetris_engine import core, frame_digest, new_game, render, step  # noqa: E402
from tetris_engine.frame import frame_bytes  # noqa: E402
from tetris_engine.tables import COLS, FPS, ROWS  # noqa: E402
from tetris_sim.bot import Bot  # noqa: E402
from tetris_sim.building import Building  # noqa: E402

SEED = 42
OUT = HERE / "snapshots"
CELL = 50  # DummyDisplay default scalar
MID_GAME_FRAME = 90 * FPS  # "mid-game": first play frame at or after 90 s


# --------------------------------------------------------------- the run

def demo_bot():
    """The controller used by ``python -m tetris_sim --bot``."""
    return Bot(pace=3, think=6, batch_shifts=False)


def run_until(done, limit=20000):
    """Run seed 42 with the demo bot; return per-frame (state, frame) up to
    the first frame where ``done(history)`` holds."""
    bot, s, hist = demo_bot(), new_game(SEED), []
    for _ in range(limit):
        s = step(s, bot(s))
        hist.append((s, render(s)))
        if done(hist):
            return hist
    raise RuntimeError("moment search ran past %d frames" % limit)


def _first(hist, pred, start=0):
    for k in range(start, len(hist)):
        if pred(k, hist[k][0]):
            return k
    return None


def find_moments(hist):
    """[(frame, slug, caption)] in frame order."""
    P, C, G, D = core.PLAYING, core.CLEARING, core.GAMEOVER, core.COUNTDOWN
    ph = lambda k: hist[k][0].phase  # noqa: E731
    first_play = _first(hist, lambda k, s: s.phase == P)
    flash = _first(hist, lambda k, s: s.phase == C)
    after = _first(hist, lambda k, s: s.phase == P, flash)
    lvl1 = _first(hist, lambda k, s: s.level == 1)
    mid = _first(hist, lambda k, s: s.phase == P, MID_GAME_FRAME)
    go0 = _first(hist, lambda k, s: s.phase == G)
    fill_end = _first(hist, lambda k, s: s.phase == G and s.timer == 33, go0)
    wait0 = fill_end + 1
    fall0 = _first(hist, lambda k, s: s.phase == G and s.timer == 184, go0)
    fall_end = _first(hist, lambda k, s: s.phase == G and s.timer == 217, go0)
    idle = _first(hist, lambda k, s: s.phase == P and s.board == core.EMPTY_BOARD,
                  fall_end)
    assert ph(0) == D and ph(30) == D and ph(60) == D and ph(90) == D
    lvl_from = hist[lvl1 - 1][0]
    return [
        (0, "countdown-3", "countdown: 3"),
        (30, "countdown-2", "countdown: 2"),
        (60, "countdown-1", "countdown: 1"),
        (90, "boot-black", "boot: the one black frame (SPEC §8.4)"),
        (first_play, "first-spawn", "first spawn, frame 91 = first logical play frame"),
        (flash, "line-clear-flash", "line-clear flash (1st of 5 white frames)"),
        (after, "after-clear", "the frame after the clear"),
        (lvl1, "level-up",
         "level up %d->%d (lines target reached)" % (lvl_from.level, 1)),
        (mid, "mid-game", "mid-game board"),
        (go0 - 1, "top-out", "last play frame before the top-out"),
        (go0, "gameover-fill-start", "game over: fill-up starts (t=0)"),
        (fill_end, "gameover-fill-end", "game over: fill-up ends (t=33)"),
        (wait0, "gameover-wait", "game over: all white, 150 frames (t=34)"),
        (fall0, "fall-down-start", "fall-down starts, rows darken from the top (t=184)"),
        (fall_end, "fall-down-end", "fall-down ends (t=217); countdown next"),
        (idle, "idle-board", "idle board: empty well, game 2's first piece"),
    ]


# --------------------------------------------------------------- scenes
# A scene is (width, height, background, items); items are
# ("rect", x, y, w, h, rgb), ("ellipse-top", cx, cy, rx, ry, rgb) (upper
# half of an ellipse, the radome) or ("text", x, y, size, rgb, anchor, s).
# The SVG writer emits all of them; the PNG rasterizer skips text.

BG = (10, 14, 26)
BODY = (70, 73, 80)
BAND = (84, 88, 96)
GROUND = (52, 55, 61)
LABEL = (150, 158, 176)


def grid_scene(frame):
    items = [("rect", c * CELL, r * CELL, CELL, CELL, rgb)
             for r, row in enumerate(frame) for c, rgb in enumerate(row)]
    return COLS * CELL, ROWS * CELL, (0, 0, 0), items


def facade_scene(frame, caption, digest, building=Building()):
    win_w, win_h, bay, story = 22, 24, 32, 36
    left, body_x, body_w = 64, 44, COLS * bay + 36
    roof_y = 150                         # top of floor 21
    floors = building.stories            # 21
    base_h = 3 * story + 20              # floors 1-3, taller ground floor
    body_h = (floors - 3) * story + base_h
    width, height = body_x + body_w + 64, roof_y + body_h + 70
    items = []
    # radome and mechanical penthouse on the roof
    cx = body_x + body_w // 2
    items.append(("rect", cx - 46, roof_y - 34, 92, 34, BAND))
    items.append(("ellipse-top", cx, roof_y - 34, 44, 44, (196, 200, 208)))
    # tower body
    items.append(("rect", body_x, roof_y, body_w, body_h, BODY))
    items.append(("rect", body_x, roof_y + body_h - base_h, body_w, base_h, GROUND))
    lit = {building.window(r, c).floor for r in range(ROWS) for c in range(COLS)}
    for f in range(floors, 0, -1):
        y = roof_y + (floors - f) * story
        if f > 3:
            items.append(("rect", body_x, y + story - 4, body_w, 4, BAND))
        if f in lit:
            r = building.top_floor - f
            for c in range(COLS):
                x = left + c * bay
                items.append(("rect", x, y + 6, win_w, win_h, frame[r][c]))
            if f % 2 == 0 or f == 4:
                items.append(("text", body_x - 8, y + 24, 11, LABEL, "end", str(f)))
        elif f > 3:  # unlit floors (21) keep their glass dark
            for c in range(COLS):
                items.append(("rect", left + c * bay, y + 6, win_w, win_h, (22, 24, 30)))
    # ground-floor openings
    gy = roof_y + body_h - base_h
    for c in range(0, COLS, 2):
        items.append(("rect", left + c * bay, gy + 40, win_w + bay, 70, (30, 32, 38)))
    items += [
        ("text", width // 2, 26, 15, (231, 235, 243), "middle", caption),
        ("text", width // 2, 46, 11, LABEL, "middle",
         "MIT Green Building (Bldg 54) - 153 windows = 17 floors x 9 bays"),
        ("text", width // 2, 62, 10, LABEL, "middle",
         "floors 20..4 lit; row->floor mapping PROVISIONAL (SPEC 10.4)"),
        ("text", width // 2, height - 38, 10, LABEL, "middle",
         "sha256(frame RGB) " + digest[:32]),
        ("text", width // 2, height - 24, 10, LABEL, "middle", digest[32:]),
    ]
    return width, height, BG, items


# --------------------------------------------------------------- SVG

def _hex(rgb):
    return "#%02x%02x%02x" % rgb


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_svg(scene, title, desc):
    w, h, bg, items = scene
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
           'viewBox="0 0 %d %d" shape-rendering="crispEdges">' % (w, h, w, h),
           "<title>%s</title>" % _esc(title),
           "<desc>%s</desc>" % _esc(desc),
           '<rect width="%d" height="%d" fill="%s"/>' % (w, h, _hex(bg))]
    for it in items:
        kind = it[0]
        if kind == "rect":
            _, x, y, rw, rh, rgb = it
            out.append('<rect x="%d" y="%d" width="%d" height="%d" fill="%s"/>'
                       % (x, y, rw, rh, _hex(rgb)))
        elif kind == "ellipse-top":
            _, cx, cy, rx, ry, rgb = it
            out.append('<path d="M%d %dA%d %d 0 0 1 %d %dZ" fill="%s" '
                       'shape-rendering="auto"/>'
                       % (cx - rx, cy, rx, ry, cx + rx, cy, _hex(rgb)))
        elif kind == "text":
            _, x, y, size, rgb, anchor, s = it
            out.append('<text x="%d" y="%d" font-family="DejaVu Sans Mono,Menlo,'
                       'Consolas,monospace" font-size="%d" fill="%s" '
                       'text-anchor="%s" shape-rendering="auto">%s</text>'
                       % (x, y, size, _hex(rgb), anchor, _esc(s)))
    out.append("</svg>")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------- PNG

def rasterize(scene):
    """Scene -> (w, h, bytearray RGB). Text is skipped."""
    w, h, bg, items = scene
    buf = bytearray(bytes(bg) * (w * h))
    for it in items:
        if it[0] == "rect":
            _, x, y, rw, rh, rgb = it
            x0, x1 = max(0, x), min(w, x + rw)
            if x1 <= x0:
                continue
            span = bytes(rgb) * (x1 - x0)
            for yy in range(max(0, y), min(h, y + rh)):
                o = (yy * w + x0) * 3
                buf[o:o + len(span)] = span
        elif it[0] == "ellipse-top":
            _, cx, cy, rx, ry, rgb = it
            for yy in range(max(0, cy - ry), min(h, cy)):
                dy = (cy - yy - 0.5) / ry
                half = int(rx * (1 - dy * dy) ** 0.5 + 0.5) if dy < 1 else 0
                x0, x1 = max(0, cx - half), min(w, cx + half)
                if x1 > x0:
                    o = (yy * w + x0) * 3
                    buf[o:o + 3 * (x1 - x0)] = bytes(rgb) * (x1 - x0)
    return w, h, buf


def png_bytes(w, h, rgb):
    raw = b"".join(b"\x00" + bytes(rgb[y * w * 3:(y + 1) * w * 3])
                   for y in range(h))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def png_pixels(data):
    """Decode a PNG written by ``png_bytes`` (8-bit RGB, filter 0)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat = 8, b""
    w = h = None
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = w * 3 + 1
    assert all(raw[y * stride] == 0 for y in range(h)), "unexpected PNG filter"
    return w, h, b"".join(raw[y * stride + 1:(y + 1) * stride] for y in range(h))


def frame_from_grid_png(data):
    """Sample each cell centre of a grid PNG -> 17x9 RGB frame."""
    w, h, px = png_pixels(data)
    assert (w, h) == (COLS * CELL, ROWS * CELL)
    def at(r, c):
        o = ((r * CELL + CELL // 2) * w + c * CELL + CELL // 2) * 3
        return tuple(px[o:o + 3])
    return tuple(tuple(at(r, c) for c in range(COLS)) for r in range(ROWS))


# --------------------------------------------------------------- driver

def stem(frame_no, slug):
    return "f%04d-t%.2fs-%s" % (frame_no, frame_no / FPS, slug)


def build():
    """Run the demo and return [(entry, {filename: bytes})]."""
    hist = run_until(lambda h: len(h) > 1 and h[-1][0].phase == core.PLAYING
                     and h[-1][0].board == core.EMPTY_BOARD
                     and any(s.phase == core.GAMEOVER for s, _ in h[-300:]))
    results = []
    for k, slug, caption in find_moments(hist):
        s, frame = hist[k]
        digest = frame_digest(frame)
        assert frame == render(s)
        name = stem(k, slug)
        title = "f%04d t=%.2fs %s" % (k, k / FPS, caption)
        desc = ("17x9 Tetris, seed %d, demo bot; frame %d (t=%.2f s); phase %s, "
                "score %d, level %d; SPEC 9.4 digest %s"
                % (SEED, k, k / FPS, s.phase, s.score, s.level, digest))
        grid, facade = grid_scene(frame), facade_scene(frame, title, digest)
        files = {
            name + ".grid.svg": to_svg(grid, title, desc).encode(),
            name + ".facade.svg": to_svg(facade, title, desc).encode(),
            name + ".grid.png": png_bytes(*rasterize(grid)),
            name + ".facade.png": png_bytes(*rasterize(facade)),
        }
        entry = {"frame": k, "time_s": round(k / FPS, 2), "event": slug,
                 "caption": caption, "phase": s.phase, "score": s.score,
                 "level": s.level, "digest": digest, "stem": name}
        results.append((entry, files))
    return results


def index_doc(results):
    return {
        "format": "17x9-tetris-snapshots",
        "command": "python -m tetris_sim --seed %d --bot" % SEED,
        "bot": "Bot(pace=3, think=6, batch_shifts=False)",
        "digest": "SPEC.md §9.4: sha256 of the 459 row-major R,G,B bytes",
        "views": {"grid": "DummyDisplay pixels, %d px per cell" % CELL,
                  "facade": "illustrative Building 54 facade, provisional "
                            "row->floor mapping (SPEC §10.4)"},
        "snapshots": [e for e, _ in results],
    }


def write(results):
    OUT.mkdir(parents=True, exist_ok=True)
    keep = {"index.json"}
    for entry, files in results:
        for fname, data in files.items():
            (OUT / fname).write_bytes(data)
            keep.add(fname)
    (OUT / "index.json").write_text(json.dumps(index_doc(results), indent=1) + "\n")
    stale = [p.name for p in OUT.iterdir() if p.name not in keep]
    for name in stale:
        (OUT / name).unlink()
    for entry, _ in results:
        print("%-44s %s" % (entry["stem"], entry["digest"]))
    print("wrote %d snapshots (%d files) to %s" % (
        len(results), 4 * len(results) + 1, OUT.relative_to(ROOT)))


def check(results):
    findings = []
    try:
        committed = json.loads((OUT / "index.json").read_text())
    except OSError as exc:
        return ["index.json: %s" % exc]
    if committed != index_doc(results):
        findings.append("index.json differs from a fresh run of the engine")
    for entry, files in results:
        for fname, fresh in files.items():
            path = OUT / fname
            if not path.exists():
                findings.append("%s: missing" % fname)
                continue
            data = path.read_bytes()
            if fname.endswith(".svg") and data != fresh:
                findings.append("%s: SVG differs from a fresh render" % fname)
            if fname.endswith(".png") and png_pixels(data) != png_pixels(fresh):
                findings.append("%s: PNG pixels differ from a fresh render" % fname)
            if fname.endswith(".grid.png"):
                got = hashlib.sha256(frame_bytes(frame_from_grid_png(data))).hexdigest()
                if got != entry["digest"]:
                    findings.append("%s: pixels hash to %s, engine says %s"
                                    % (fname, got, entry["digest"]))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="verify committed snapshots; write nothing")
    args = ap.parse_args(argv)
    results = build()
    if args.check:
        findings = check(results)
        for f in findings:
            print("FINDING:", f)
        print("%d snapshots checked, %d findings" % (len(results), len(findings)))
        return 1 if findings else 0
    write(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
