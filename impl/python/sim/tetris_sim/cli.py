"""Command line: run the engine (or another Animation) headlessly and
render the result.

    python -m tetris_sim --seed 42 --bot --frames 900 --html out.html
    python -m tetris_sim --seed 42 --bot --frames 300 --ansi
    python -m tetris_sim --trace spec/conformance/traces/hold.json --ansi-final
"""

import argparse
import json
import sys

from tetris_engine.animation import TetrisAnimation
from tetris_engine.conformance import group_events, make_trace, observe

from . import ansi
from .animations import ANIMATIONS
from .bot import Bot
from .html import write_html
from .recorder import record


def _replay_controller(events):
    by_frame = group_events(events)
    counter = {"k": 0}

    def controller(_state):
        k = counter["k"]
        counter["k"] += 1
        return by_frame.get(k, [])
    return controller


def build_parser():
    p = argparse.ArgumentParser(prog="tetris_sim", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--frames", type=int, default=900)
    p.add_argument("--bot", action="store_true", help="demo auto-player")
    p.add_argument("--bot-fast", action="store_true",
                   help="auto-player that acts every frame")
    p.add_argument("--trace", help="replay seed/events from a conformance trace")
    p.add_argument("--animation", choices=sorted(ANIMATIONS),
                   help="run a non-game Animation instead of the game")
    p.add_argument("--html", help="write a self-contained HTML replay")
    p.add_argument("--ansi", action="store_true", help="animate in the terminal")
    p.add_argument("--ansi-final", action="store_true", help="print the last frame")
    p.add_argument("--speed", type=float, default=1.0, help="--ansi playback speed")
    p.add_argument("--trace-out", help="write the run as a conformance trace")
    p.add_argument("--viewer", action="store_true",
                   help="desktop window via legacy DummyDisplay (needs pygame)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    seed, frames, controller = args.seed, args.frames, None
    if args.trace:
        with open(args.trace) as fh:
            trace = json.load(fh)
        seed = trace["seed"]
        frames = args.frames if "--frames" in (argv or sys.argv) else trace["frames"]
        controller = _replay_controller(trace["events"])
    elif args.bot_fast:
        controller = Bot()
    elif args.bot:
        controller = Bot(pace=3, think=6, batch_shifts=False)

    if args.viewer:
        from .viewer import run_viewer
        run_viewer(seed=seed, frames=frames, bot=controller)
        return 0

    if args.animation:
        animation = ANIMATIONS[args.animation]()
        title = f"animation: {args.animation}"
    else:
        animation = TetrisAnimation(seed)
        title = "17x9 Tetris"
    rec, state = record(animation, frames, controller)

    if args.html:
        write_html(args.html, rec.frames, rec.info,
                   meta={"title": title, "seed": None if args.animation else seed,
                         "bot": bool(args.bot or args.bot_fast)})
        print(f"wrote {args.html} ({len(rec)} frames)", file=sys.stderr)
    if args.ansi:
        def caption(i):
            h = rec.info[i]
            return (f"frame {i}  score {h['score']}  level {h['level']}  "
                    f"{h['phase']}" if h else f"frame {i}")
        ansi.play(rec.frames, speed=args.speed, caption=caption)
    if args.ansi_final:
        print(ansi.frame_to_ansi(rec.frames[-1]))
    if args.trace_out and not args.animation:
        trace = make_trace("sim-run", "recorded by tetris_sim", seed, frames,
                           rec.events)
        with open(args.trace_out, "w") as fh:
            json.dump(trace, fh, indent=1)
            fh.write("\n")
    if not args.animation:
        summary = observe(state)
        summary.pop("frame_hex")
        print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
