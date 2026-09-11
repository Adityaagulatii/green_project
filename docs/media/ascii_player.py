#!/usr/bin/env python3
"""Plain-ASCII replay of a pinned conformance trace, with its digests checked.

    python docs/media/ascii_player.py game        # KAV-14, its last 900 frames
    python docs/media/ascii_player.py countdown   # KAV-01, frames 0-90 (SPEC §8.4)
    python docs/media/ascii_player.py game --check        # verify only, no playback
    python docs/media/ascii_player.py game --trace spec/conformance/traces/12-game-over-reset.json

KAV-NN is trace NN in ``spec/conformance/traces/`` (the known-answer vectors
of SPEC Appendix A, in the v2 naming). The player replays the trace from
``init(seed)`` with its recorded ``[frame, action, down]`` events through the
engine (imported read-only from ``impl/python/engine``; nothing there is
modified). It draws a window of frames at 30 FPS, one ASCII character per
window of the facade:

    .  empty (black)    #  white: flash, game over, countdown digits
    +  ghost (gray)     I J L O S Z T  the piece colours

Output is printable ASCII plus cursor positioning (ESC[row;colH); there are
no colour (SGR) sequences.

What is checked, and printed on screen at the end:

- **pinned**: each frame the trace pins (every ``digest_every`` frames, plus
  the last) is hashed from the ASCII actually shown, mapped back through the
  SPEC §4.1 palette to its 459 RGB bytes (SPEC §9.4), and compared with the
  digest stored in the trace file.
- **full trace**: every pinned digest over the whole trace, and the final
  observation (SPEC §12), must match.
- **round trip**: every shown frame's ASCII re-hashes to the engine's digest,
  so the ASCII view is lossless.

Exit status 0 only if everything matches.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT / "impl/python/engine") not in sys.path:
    sys.path.insert(0, str(ROOT / "impl/python/engine"))

from tetris_engine import core, frame_digest, render  # noqa: E402
from tetris_engine.conformance import group_events, observe  # noqa: E402
from tetris_engine.tables import FPS, PALETTE  # noqa: E402

TRACES = ROOT / "spec/conformance/traces"
CHAR = {".": ".", "W": "#", "G": "+", **{p: p for p in "IJLOSZT"}}
TO_ASCII = {PALETTE[code]: ch for code, ch in CHAR.items()}
FROM_ASCII = {ch: rgb for rgb, ch in TO_ASCII.items()}
LEGEND = [
    "legend (one char per window)",
    "  .  empty window (black)",
    "  #  white: flash, game over, digits",
    "  +  ghost (gray)",
    "  I J L O S Z T  piece colours",
]
MODES = {  # mode -> (default trace, window start or None = last N, frames)
    "game": ("14-bot-marathon.json", None, 900),
    "countdown": ("01-idle-gravity.json", 0, 91),
}


def kav(trace):
    return "KAV-" + trace["name"].split("-", 1)[0]


def pinned_frames(trace):
    """{frame: digest} for the frames whose digests the trace pins."""
    n, d = int(trace["frames"]), int(trace["digest_every"])
    ks = list(range(0, n, d))
    if ks[-1] != n - 1:
        ks.append(n - 1)
    if len(ks) != len(trace["digests"]):
        raise ValueError("trace digests do not line up with digest_every")
    return dict(zip(ks, trace["digests"]))


def to_ascii(frame):
    return ["".join(TO_ASCII[rgb] for rgb in row) for row in frame]


def ascii_digest(rows):
    """SPEC §9.4 digest of the frame an ASCII picture stands for."""
    return hashlib.sha256(bytes(v for row in rows for ch in row
                                for v in FROM_ASCII[ch])).hexdigest()


def replay(trace, start, count):
    """Replay the whole trace. Returns (window, report); ``window`` holds
    (frame, state, ascii rows) for frames start..start+count-1."""
    pinned = pinned_frames(trace)
    by_frame = group_events(trace["events"])
    s = core.new_game(trace["seed"])
    window, full_ok, round_ok = [], 0, 0
    for k in range(int(trace["frames"])):
        s = core.step(s, by_frame.get(k, ()))
        frame = render(s)
        digest = frame_digest(frame)
        if k in pinned and pinned[k] == digest:
            full_ok += 1
        if start <= k < start + count:
            rows = to_ascii(frame)
            round_ok += ascii_digest(rows) == digest
            window.append((k, s, rows))
    report = {"pinned": pinned, "full_ok": full_ok, "full_n": len(pinned),
              "final_ok": observe(s) == trace["final"], "round_ok": round_ok}
    return window, report


class Screen:
    """Redraws only the lines that changed. Cursor motion only, no colour."""

    def __init__(self, out, height):
        self.out, self.prev = out, [None] * height
        out.write("\x1b[?25l\x1b[2J")

    def draw(self, lines):
        buf = []
        for i, line in enumerate(lines):
            if line != self.prev[i]:
                buf.append("\x1b[%d;1H%s\x1b[K" % (i + 1, line))
                self.prev[i] = line
        self.out.write("".join(buf))
        self.out.flush()

    def close(self):
        # park the cursor on the spare last row; no newline, so nothing scrolls
        self.out.write("\x1b[%d;1H\x1b[?25h" % (len(self.prev) + 1))
        self.out.flush()


def compose(title, k, s, rows, pinned, seen_ok, seen_n, window, footer):
    board = ["  " + "=" * 21]
    board += ["  | " + " ".join(r) + " |" for r in rows]
    board += ["  " + "=" * 21]
    if s.phase == core.COUNTDOWN:
        t = s.timer
        what = ("digit %s (frames %d-%d of the countdown)"
                % ("321"[t // 30], t // 30 * 30, t // 30 * 30 + 29)
                if 0 <= t < 90 else "black frame (boot, SPEC 8.4)")
    else:
        what = "score %d  level %d  lines %d" % (s.score, s.level, s.lines)
    here = rows and ascii_digest(rows)
    if k in pinned:
        mark = "this frame: %s" % ("ok" if pinned[k] == here else "MISMATCH")
    else:
        mark = "this frame: not pinned"
    panel = [
        "frame %4d of %d-%d   t = %6.2f s" % ((k,) + window + (k / FPS,)),
        "phase %s" % s.phase,
        what,
        "",
        "digest %s..." % here[:24],
        "pinned %d/%d ok (%s)" % (seen_ok, seen_n, mark),
        "",
    ] + LEGEND
    lines = [title, ""]
    for i, b in enumerate(board):
        p = panel[i] if i < len(panel) else ""
        lines.append("%-26s%s" % (b, p))
    lines.append("")
    lines += footer
    return lines


def footer_lines(trace, start, count, report, seen_ok, seen_n):
    tag = kav(trace)
    last = start + count - 1
    ok = (seen_ok == seen_n and report["full_ok"] == report["full_n"]
          and report["final_ok"] and report["round_ok"] == count)
    every = int(trace["digest_every"])
    pinned = ("every frame pinned" if every == 1
              else "pinned: every %dth frame + the last" % every)
    return ok, [
        "%s: %d/%d frame digests match (SPEC Appendix A) -- %s"
        % (tag, seen_ok, seen_n, "PASS" if ok else "FAIL"),
        "  frames %d-%d shown; %s" % (start, last, pinned),
        "  whole trace %d/%d + final observation %s; ASCII->RGB %d/%d"
        % (report["full_ok"], report["full_n"],
           "ok" if report["final_ok"] else "DIFFERS",
           report["round_ok"], count),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("mode", choices=sorted(MODES))
    ap.add_argument("--trace", help="trace JSON (default: per mode)")
    ap.add_argument("--start", type=int, help="first frame shown")
    ap.add_argument("--frames", type=int, help="number of frames shown")
    ap.add_argument("--fps", type=float, default=FPS)
    ap.add_argument("--check", action="store_true",
                    help="verify and print the verdict; no playback")
    args = ap.parse_args(argv)

    default, dstart, dcount = MODES[args.mode]
    path = Path(args.trace) if args.trace else TRACES / default
    trace = json.loads(path.read_text())
    total = int(trace["frames"])
    count = min(args.frames or dcount, total)
    start = args.start if args.start is not None else (
        dstart if dstart is not None else total - count)
    if not (0 <= start and start + count <= total):
        ap.error("window %d+%d outside the trace's %d frames" % (start, count, total))

    window, report = replay(trace, start, count)
    pinned = report["pinned"]
    in_window = [k for k in pinned if start <= k < start + count]
    tag = kav(trace)
    if args.mode == "countdown":
        title = ("%s frames %d-%d: SPEC 8.4 countdown (3, 2, 1, black)"
                 % (tag, start, start + count - 1))
    else:
        title = ("%s replay: %s, seed %d, frames %d-%d of %d"
                 % (tag, trace["name"], trace["seed"], start,
                    start + count - 1, total))

    seen_ok = seen_n = 0
    screen = None if args.check else Screen(sys.stdout, 2 + 19 + 1 + 3)
    shown = (start, start + count - 1)
    period, next_t = 1.0 / args.fps, time.monotonic()
    for k, s, rows in window:
        if k in pinned:
            seen_n += 1
            seen_ok += ascii_digest(rows) == pinned[k]
        if screen:
            screen.draw(compose(title, k, s, rows, pinned, seen_ok,
                                len(in_window), shown, ["", "", ""]))
            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    ok, foot = footer_lines(trace, start, count, report, seen_ok, seen_n)
    if screen:
        k, s, rows = window[-1]
        screen.draw(compose(title, k, s, rows, pinned, seen_ok,
                            len(in_window), shown, foot))
        time.sleep(2.0)
        screen.close()
    else:
        print("\n".join(foot))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
