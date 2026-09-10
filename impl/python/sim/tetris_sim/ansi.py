"""ANSI truecolor terminal renderer."""

import sys
import time

RESET = "\x1b[0m"


def frame_to_ansi(frame, cell="  "):
    """One frame as 17 lines of 24-bit background-coloured cells."""
    return "\n".join(
        "".join(f"\x1b[48;2;{r};{g};{b}m{cell}" for r, g, b in row) + RESET
        for row in frame)


def play(frames, out=None, fps=30, speed=1.0, sleep=time.sleep, caption=None):
    """Animate frames in place at ``fps * speed``."""
    out = out or sys.stdout
    period = 1.0 / (fps * speed) if speed > 0 else 0.0
    out.write("\x1b[?25l\x1b[2J")
    try:
        prev = None
        for i, frame in enumerate(frames):
            text = caption(i) if caption else f"frame {i}"
            if frame != prev or caption:
                out.write("\x1b[H" + frame_to_ansi(frame) + "\n" + text
                          + "\x1b[K\n")
                out.flush()
            prev = frame
            if period:
                sleep(period)
    finally:
        out.write("\x1b[?25h")
        out.flush()
