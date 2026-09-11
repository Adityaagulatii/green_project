"""A terminal viewer (a sink): shows a display's frames in ANSI truecolor.

Each character is two display cells, one above the other, drawn with an
upper half block, so cells come out roughly square.
"""
import json
import sys

from websockets.asyncio.client import connect

from .source import check_url


def ansi(frame, w, h):
    """A w*h*3 RGB frame as lines of ANSI half blocks."""
    lines = []
    for y in range(0, h, 2):
        cells = []
        for x in range(w):
            i = (y * w + x) * 3
            j = i + w * 3
            top = frame[i:i + 3]
            bottom = frame[j:j + 3] if y + 1 < h else b"\0\0\0"
            cells.append("\x1b[38;2;{};{};{}m\x1b[48;2;{};{};{}m▀".format(*top, *bottom))
        lines.append("".join(cells) + "\x1b[0m")
    return "\n".join(lines)


async def run(url, display, *, frames=None, render=True, out=sys.stdout, stats=None,
              ready=None):
    """View DISPLAY until FRAMES frames arrived (forever if None).  STATS, a
    dict, is updated as messages arrive; READY, an asyncio.Event, is set once
    the relay has answered with caps.  Return STATS."""
    check_url(url)
    stats = {} if stats is None else stats
    async with connect(url) as ws:
        await ws.send(json.dumps({"op": "view", "display": display}))
        caps = json.loads(await ws.recv())
        if caps.get("op") != "caps":
            raise SystemExit(f"view: {caps}")
        w, h = caps["w"], caps["h"]
        stats.update(w=w, h=h, frames=0, lease=None, last=None)
        if ready is not None:
            ready.set()
        if render:
            out.write("\x1b[2J\x1b[?25l")
        try:
            async for message in ws:
                if isinstance(message, str):
                    msg = json.loads(message)
                    if msg.get("op") == "lease":
                        stats["lease"] = msg
                    continue
                stats["frames"] += 1
                stats["last"] = message
                if render:
                    holder = (stats["lease"] or {}).get("holder") or "-"
                    out.write(f"\x1b[H{display} {w}x{h}  holder {holder}  "
                              f"frame {stats['frames']}\x1b[K\n{ansi(message, w, h)}\n")
                    out.flush()
                if frames is not None and stats["frames"] >= frames:
                    break
        finally:
            if render:
                out.write("\x1b[?25h")
                out.flush()
    return stats
