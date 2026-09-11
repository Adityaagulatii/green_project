"""A terminal viewer (a sink): shows a display's frames in ANSI truecolor.

It is the spec's sink fold: every message goes through the contract's
reduce_event, and cells are drawn in the palette caps announced, through
the level rule.  Each character is two display cells, one above the other,
drawn with an upper half block, so cells come out roughly square.
"""
import json
import sys

from contract import display_contract as dc
from websockets.asyncio.client import connect

from .source import check_url


def ansi(cells, w, h, colours):
    """w*h palette indices as lines of ANSI half blocks; index i is drawn in
    colours[i] (16 hex colours, as dc.colour_table gives them)."""
    rgb = [dc.hex_rgb(c) for c in colours]
    lines = []
    for y in range(0, h, 2):
        row = []
        for x in range(w):
            top = rgb[cells[y * w + x]]
            bottom = rgb[cells[(y + 1) * w + x]] if y + 1 < h else (0, 0, 0)
            row.append("\x1b[38;2;{};{};{}m\x1b[48;2;{};{};{}m▀".format(*top, *bottom))
        lines.append("".join(row) + "\x1b[0m")
    return "\n".join(lines)


def is_control(message):
    return isinstance(message, str) and message.startswith("{")


async def run(url, display, *, frames=None, render=True, out=sys.stdout, stats=None,
              ready=None):
    """View DISPLAY (the relay's default if None) until FRAMES frames arrived
    (forever if None).  STATS, a dict, is updated as messages arrive; READY,
    an asyncio.Event, is set once the relay has answered with caps.  Return
    STATS, whose "state" is the folded sink state."""
    check_url(url)
    stats = {} if stats is None else stats
    known = display in dc.capabilities()["displays"]
    state = dc.reduce_event(dc.initial_state(display if known else None), {"event": "open"})
    async with connect(url) as ws:
        await ws.send(json.dumps({"op": "view"} if display is None
                                 else {"op": "view", "display": display}))
        first = await ws.recv()
        caps = json.loads(first) if is_control(first) else None
        if not caps or caps.get("op") != "caps":
            raise SystemExit(f"view: {first!r}")
        state = dc.reduce_event(state, caps)
        name = caps["display"]
        stats.update(display=name, w=state["w"], h=state["h"], format=state["format"],
                     frames=0, dropped=0, lease=None, last=None, state=state)
        if ready is not None:
            ready.set()
        if render:
            out.write("\x1b[2J\x1b[?25l")
        try:
            async for message in ws:
                if is_control(message):
                    msg = json.loads(message)
                    state = dc.reduce_event(state, msg)
                    if msg.get("op") == "lease":
                        stats["lease"] = msg
                    stats["state"] = state
                    continue
                seq = state["seq"]
                state = dc.reduce_event(state, {"event": "frame", "data": message})
                stats["state"], stats["dropped"] = state, state["dropped"]
                if state["seq"] == seq:
                    continue   # dropped by the fold: wrong length or format
                stats["frames"] += 1
                stats["last"] = message
                if render:
                    holder = state["holder"] or "-"
                    out.write(f"\x1b[H{name} {state['w']}x{state['h']} {state['format']}  "
                              f"holder {holder}  frame {stats['frames']}\x1b[K\n"
                              f"{ansi(state['cells'], state['w'], state['h'], dc.colour_table(state['palette']))}\n")
                    out.flush()
                if frames is not None and stats["frames"] >= frames:
                    break
        finally:
            if render:
                out.write("\x1b[?25h")
                out.flush()
    stats["state"] = dc.reduce_event(state, {"event": "close"})
    return stats
