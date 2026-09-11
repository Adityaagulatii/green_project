"""python -m demo sim: a sink simulator for every preset.

A display is simulated the way the spec's sink works.  Each frame is folded
through the contract's reduce_event: caps, then lease, then frames.  The
state is drawn with the preset's own grid, cell aspect, gap (the masonry
between windows) and palette, through the level rule.  So gb shows 4 levels,
mono 2 (blinkenlights' lamps are on or off) and grey8 8 (arcade).

The frames come from one of two places:
- locally, from a demo producer;
- a relay, when --url is given: the simulator is then a viewer of the mock
  relay, which is a remote sink.

Outputs:
- ANSI half blocks in the terminal (the default);
- the last frame per display as a PNG (--png DIR);
- an animated GIF (--gif DIR);
- a pygame window (--window), only when pygame and a display both exist.
  Neither does in the jail, whose venv has no pygame and which has no X.
"""
import json
import os
import shutil
import sys
import time

from contract import display_contract as dc
from websockets.asyncio.client import connect

from . import render
from .producers import DEMOS
from .source import check_url
from .view import is_control

PNG_BOX = 256   # PNG and GIF images fit in 256 x 256 pixels unless --cell is given


def looks(name):
    """(aspect, gap) of a display: its preset's, else square cells and no gap."""
    p = dc.capabilities()["displays"].get(name, {})
    return p.get("aspect", 1), p.get("gap", 0)


def caps_for(name):
    """The caps a relay announces for preset NAME (pal16, a 16-colour palette)."""
    p = dc.preset(name)
    return {"op": "caps", "display": name, "w": p["w"], "h": p["h"], "fps": p["fps"],
            "format": "pal16", "palette": dc.palette16(dc.palette(p["palette"]))}


def local_states(name, demo, frames, seed=0):
    """The sink's fold of DEMO's first FRAMES frames on preset NAME, one state
    per frame."""
    s = dc.reduce_event(dc.initial_state(name), {"event": "open"})
    s = dc.reduce_event(s, caps_for(name))
    s = dc.reduce_event(s, {"op": "lease", "display": name, "holder": f"sim:{demo}",
                            "expires": None})
    produce = DEMOS[demo](s["w"], s["h"], seed)
    for _ in range(frames):
        s = dc.reduce_event(s, {"event": "frame", "data": next(produce)})
        yield s


def pygame_window():
    """pygame, initialised with a window, when pygame and a display exist; else None.
    Untested here: the jail has neither."""
    if os.environ.get("SDL_VIDEODRIVER") == "dummy" or not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return None
    try:
        import pygame
    except ImportError:
        return None
    pygame.display.init()
    return pygame


class Sim:
    """One simulated display: feed it states, then finish() writes the files."""

    def __init__(self, name, *, ansi=True, png=None, gif=None, out=sys.stdout, pace=True,
                 cell=None, label="", window=None):
        self.name, self.aspect, self.gap = name, *looks(name)
        self.ansi, self.png, self.gif, self.out = ansi, png, gif, out
        self.pace, self.cell, self.label, self.window = pace, cell, label, window
        self.frames, self.state, self.g, self.screen = [], None, None, None
        self.count = 0

    def geometry(self, s):
        if self.cell:
            return render.geometry(s["w"], s["h"], self.aspect, self.gap, self.cell)
        return render.fit(s["w"], s["h"], self.aspect, self.gap, PNG_BOX, PNG_BOX)

    def feed(self, s):
        if self.g is None or (self.g.w, self.g.h) != (s["w"], s["h"]):
            self.g = self.geometry(s)
            cols, lines = shutil.get_terminal_size((100, 40))
            self.ga = render.fit(s["w"], s["h"], self.aspect, self.gap, cols, 2 * (lines - 2),
                                 largest=8)
        table = render.colour_table(s["palette"])
        t = time.monotonic()
        if self.ansi:
            self.out.write(f"\x1b[H{self.name} {s['w']}x{s['h']} aspect {self.aspect} gap "
                           f"{self.gap} {self.label} frame {s['seq']}\x1b[K\n"
                           + render.ansi(render.index_image(s["cells"], self.ga), self.ga, table)
                           + "\n")
            self.out.flush()
        if self.gif is not None:
            self.frames.append(render.index_image(s["cells"], self.g))
        if self.window is not None:
            self.draw_window(s, table)
        self.state, self.count = s, self.count + 1
        if self.pace and (self.ansi or self.window):
            time.sleep(max(0.0, t + 1.0 / s["fps"] - time.monotonic()))

    def draw_window(self, s, table):
        pg = self.window
        w, h = self.g.size
        if self.screen is None:
            self.screen = pg.display.set_mode((w, h))
            pg.display.set_caption(f"{self.name} {s['w']}x{s['h']}")
        rgb = render.rgb_image(render.index_image(s["cells"], self.g), table)
        self.screen.blit(pg.image.frombuffer(rgb, (w, h), "RGB"), (0, 0))
        pg.display.flip()
        pg.event.pump()

    def finish(self, stem):
        """Write the PNG and GIF (if asked) and return a summary."""
        s, g = self.state, self.g
        done = {"display": self.name, "frames": self.count}
        if s is None:
            return done
        table = render.colour_table(s["palette"])
        done.update(w=s["w"], h=s["h"], image=list(g.size), levels=len(set(table[:16])))
        if self.png is not None:
            self.png.mkdir(parents=True, exist_ok=True)
            path = self.png / f"{stem}.png"
            path.write_bytes(render.png_bytes(*g.size, render.rgb_image(
                render.index_image(s["cells"], g), table)))
            done["png"] = str(path)
        if self.gif is not None and self.frames:
            self.gif.mkdir(parents=True, exist_ok=True)
            path = self.gif / f"{stem}.gif"
            path.write_bytes(render.gif_bytes(*g.size, self.frames, table,
                                              delay_cs=max(2, round(100 / s["fps"]))))
            done["gif"] = str(path)
        return done


def simulate_local(name, demo, frames, *, seed=0, **kw):
    sim = Sim(name, label=demo, **kw)
    if sim.ansi:
        sim.out.write("\x1b[2J")
    for s in local_states(name, demo, frames, seed):
        sim.feed(s)
    return sim.finish(name if kw.get("png") and not kw.get("gif") else f"{name}-{demo}")


async def simulate_remote(url, display, frames, **kw):
    """View DISPLAY (the relay's default if None) on the relay at URL and draw
    each frame the fold accepts, until FRAMES frames (forever if None)."""
    check_url(url)
    async with connect(url) as ws:
        await ws.send(json.dumps({"op": "view"} | ({"display": display} if display else {})))
        s = dc.reduce_event(dc.initial_state(), {"event": "open"})
        sim = None
        async for m in ws:
            if is_control(m):
                msg = json.loads(m)
                s = dc.reduce_event(s, msg)
                if msg.get("op") == "caps" and sim is None:
                    sim = Sim(msg["display"], label="(remote)", **kw)
                continue
            seq = s["seq"]
            s = dc.reduce_event(s, {"event": "frame", "data": m})
            if sim is not None and s["seq"] != seq:
                sim.feed(s)
                if frames and sim.count >= frames:
                    break
    return sim.finish(f"{sim.name}-remote") if sim else {"display": display, "frames": 0}


def cli(args):
    """python -m demo sim: play a demo on a preset (or all 12), or view a relay."""
    import asyncio
    window = pygame_window() if args.window else None
    if args.window and window is None:
        print("sim: no window (no pygame, or no display): ANSI, PNG and GIF only",
              file=sys.stderr)
    kw = {"ansi": not args.no_ansi, "png": args.png, "gif": args.gif, "pace": not args.fast,
          "cell": args.cell, "window": window}
    if args.url:
        display = None if args.display == "all" else args.display
        done = [asyncio.run(simulate_remote(args.url, display, args.frames, **kw))]
    else:
        names = list(dc.preset_order()) if args.display == "all" else [args.display]
        unknown = [n for n in names if n not in dc.capabilities()["displays"]]
        if unknown:
            print(f"sim: no preset {unknown[0]!r}; one of {', '.join(dc.preset_order())}, all",
                  file=sys.stderr)
            return 2
        done = [simulate_local(n, args.demo, args.frames, seed=args.seed, **kw) for n in names]
    for d in done:
        print(json.dumps(d))
    return 0
