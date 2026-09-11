"""python -m demo {run,relay,source,view,list} -- see README.md."""
import argparse
import asyncio
import json
import pathlib
import sys

from . import source, view
from .producers import DEMOS
from .relay import DEFAULT_DISPLAY, PROFILES, Relay


async def _run(args):
    relay = Relay(fps=args.fps)
    async with relay.serve(args.host, args.port) as url:
        stats, ready = {}, asyncio.Event()
        viewer = asyncio.create_task(
            view.run(url, args.display, render=not args.quiet, stats=stats, ready=ready))
        await ready.wait()
        frames = round(args.seconds * args.fps) if args.seconds else None
        result = await source.run(url, args.display, args.demo, frames=frames,
                                  seed=args.seed, seq=args.seq, ttl=args.ttl)
        await asyncio.sleep(0.1)
        viewer.cancel()
        print(f"\nrelay {url}\nsource {json.dumps(result)}\n"
              f"viewer {stats.get('frames')} frames; relay {dict(relay.stats)}")
        return 0 if result.get("op") == "done" and not result["errors"] else 1


async def _relay(args):
    relay = Relay(fps=args.fps, page=args.page)
    async with relay.serve(args.host, args.port) as url:
        print(f"mock relay {url}  (capabilities: {url.rsplit('/', 1)[0]}/capabilities.json)",
              flush=True)
        await asyncio.Future()


async def _source(args):
    frames = round(args.seconds * args.fps) if args.seconds else None
    result = await source.run(args.url, args.display, args.demo, frames=frames,
                              seed=args.seed, seq=args.seq, ttl=args.ttl, fps=args.fps)
    print(json.dumps(result))
    return 0 if result.get("op") == "done" else 1


async def _view(args):
    stats = await view.run(args.url, args.display, frames=args.frames)
    print(f"\n{stats['frames']} frames")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m demo", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, url=False):
        sp.add_argument("--display", "-d", default=DEFAULT_DISPLAY, choices=sorted(PROFILES))
        sp.add_argument("--fps", type=int, default=30)
        if url:
            sp.add_argument("--url", default="ws://127.0.0.1:8765/tools/display/ws")

    def producer(sp):
        sp.add_argument("demo", nargs="?", default="tetris", choices=sorted(DEMOS))
        sp.add_argument("--seconds", type=float, default=10, help="0 means forever")
        sp.add_argument("--seed", type=int, default=0)
        sp.add_argument("--ttl", type=int, default=60)
        sp.add_argument("--seq", action="store_true", help="prefix the 2-byte sequence")

    sp = sub.add_parser("run", help="relay + source + terminal viewer, in one process")
    common(sp)
    producer(sp)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=0)
    sp.add_argument("--quiet", action="store_true", help="no rendering, summary only")

    sp = sub.add_parser("relay", help="the mock relay alone")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--fps", type=int, default=30)
    sp.add_argument("--page", type=pathlib.Path, help="an HTML sink page to serve")

    sp = sub.add_parser("source", help="send a demo to a running relay")
    common(sp, url=True)
    producer(sp)

    sp = sub.add_parser("view", help="view a display on a running relay")
    common(sp, url=True)
    sp.add_argument("--frames", type=int)

    sub.add_parser("list", help="display profiles and demos")

    args = p.parse_args(argv)
    if args.cmd == "list":
        for name, (w, h) in PROFILES.items():
            print(f"{name:15} {w}x{h}")
        print("demos:", " ".join(DEMOS))
        return 0
    runner = {"run": _run, "relay": _relay, "source": _source, "view": _view}[args.cmd]
    try:
        return asyncio.run(runner(args)) or 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
