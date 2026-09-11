"""python -m demo {run,relay,source,view,list} -- see README.md."""
import argparse
import asyncio
import json
import pathlib
import signal
import sys

from contract import display_contract as dc

from . import source, view
from .producers import DEMOS
from .relay import DEFAULT_DISPLAY, PROFILES, Relay


async def _run(args):
    relay = Relay()
    async with relay.serve(args.host, args.port) as url:
        stats, ready = {}, asyncio.Event()
        viewer = asyncio.create_task(
            view.run(url, args.display, render=not args.quiet, stats=stats, ready=ready))
        await ready.wait()
        frames = round(args.seconds * args.fps) if args.seconds else None
        result = await source.run(url, args.display, args.demo, frames=frames,
                                  seed=args.seed, seq=args.seq, ttl=args.ttl, fps=args.fps,
                                  fmt=args.format)
        await asyncio.sleep(0.1)
        viewer.cancel()
        print(f"\nrelay {url}\nsource {json.dumps(result)}\n"
              f"viewer {stats.get('frames')} frames; relay {dict(relay.stats)}")
        return 0 if result.get("op") == "done" and not result["errors"] else 1


def _fanout(items):
    """--fanout FORMAT (every display) and NAME=FORMAT, as Relay takes them."""
    per = {}
    for item in items:
        name, _, fmt = item.rpartition("=")
        per[name or "*"] = fmt
    return per or None


async def _relay(args):
    relay = Relay(fps=args.fps, page=args.page, default=args.default, seq_rule=args.seq_rule,
                  extra=args.extra_display, fanout=_fanout(args.fanout), record=args.record)
    async with relay.serve(args.host, args.port, udp_port=args.udp_port) as url:
        http_base = url.replace("ws://", "http://", 1).rsplit("/", 1)[0]
        print(f"mock relay {url}  (capabilities: {http_base}/capabilities.json)", flush=True)
        if relay.udp_addr:
            print(f"udp {relay.udp_addr[0]}:{relay.udp_addr[1]}  (BLP, MCUF)", flush=True)
        # SIGTERM (Process.destroy, kill) ends the relay as cleanly as Ctrl-C
        stop = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().add_signal_handler(
            signal.SIGTERM, lambda: stop.done() or stop.set_result(None))
        await stop


async def _source(args):
    frames = round(args.seconds * args.fps) if args.seconds else None
    result = await source.run(args.url, args.display, args.demo, frames=frames, name=args.name,
                              seed=args.seed, seq=args.seq, ttl=args.ttl, fps=args.fps,
                              fmt=args.format)
    print(json.dumps(result))
    return 0 if result.get("op") == "done" else 1


async def _view(args):
    stats = await view.run(args.url, args.display, frames=args.frames)
    print(f"\n{stats['frames']} frames")


def _list():
    print(f"{'display':15} {'grid':6} {'fps':>3}  {'palette':7} {'levels':>6}  "
          f"{'kind':9} {'aspect':>6} {'gap':>5}")
    for name, p in dc.presets().items():
        mark = ("  (default here)" if name == DEFAULT_DISPLAY else "") + (
            "  (spec default)" if name == dc.SPEC_DEFAULT else "")
        print(f"{name:15} {p['w']:>2}x{p['h']:<3} {p['fps']:>3}  {p['palette']:7} "
              f"{p['levels']:>6}  {p['kind']:9} {p['aspect']:>6} {p['gap']:>5}{mark}")
    print("demos:", " ".join(DEMOS))


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m demo", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, url=False):
        if url:
            sp.add_argument("--display", "-d", default=DEFAULT_DISPLAY,
                            help=f"a display on the relay (default {DEFAULT_DISPLAY})")
            sp.add_argument("--url", default="ws://127.0.0.1:8765/tools/display/ws")
        else:
            sp.add_argument("--display", "-d", default=DEFAULT_DISPLAY, choices=list(PROFILES))
        sp.add_argument("--fps", type=int, default=30)

    def producer(sp):
        sp.add_argument("demo", nargs="?", default="tetris", choices=sorted(DEMOS))
        sp.add_argument("--seconds", type=float, default=10, help="0 means forever")
        sp.add_argument("--seed", type=int, default=0)
        sp.add_argument("--ttl", type=int, default=60)
        sp.add_argument("--seq", action="store_true", help="prefix the 2-byte sequence")
        sp.add_argument("--format", default="pal16", choices=source.FORMATS,
                        help="the wire format the source sends (default pal16)")

    sp = sub.add_parser("run", help="relay + source + terminal viewer, in one process")
    common(sp)
    producer(sp)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=0)
    sp.add_argument("--quiet", action="store_true", help="no rendering, summary only")

    sp = sub.add_parser("relay", help="the mock relay alone")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--fps", type=int, help="lower every display's fps to this")
    sp.add_argument("--page", type=pathlib.Path, help="an HTML sink page to serve")
    sp.add_argument("--default", default=DEFAULT_DISPLAY,
                    help=f"the display a view or reserve without one gets "
                         f"(default {DEFAULT_DISPLAY}; the spec's is {dc.SPEC_DEFAULT})")
    sp.add_argument("--udp-port", type=int,
                    help="also take BLP/MCUF packets on this loopback UDP port "
                         f"(0: any free port; the spec's is {dc.UDP_PORT}); off if not given")
    sp.add_argument("--record", type=pathlib.Path,
                    help="append every message in and out to this JSONL file")
    sp.add_argument("--extra-display", action="append", default=[],
                    metavar="NAME=WxH[,fanout=hex][,palette=P][,fps=N]",
                    help="add a mock-only display, e.g. for the grid limits")
    sp.add_argument("--fanout", action="append", default=[], metavar="FORMAT|NAME=FORMAT",
                    help="the format to fan out (caps.format): pal16 (default) or hex")
    sp.add_argument("--seq-rule", choices=dc.SEQ_RULES, default="literal",
                    help="how a sequence prefix orders frames (default literal, the spec's text)")

    sp = sub.add_parser("source", help="send a demo to a running relay")
    common(sp, url=True)
    producer(sp)
    sp.add_argument("--name", default="demo@jail")

    sp = sub.add_parser("view", help="view a display on a running relay")
    common(sp, url=True)
    sp.add_argument("--frames", type=int)

    sub.add_parser("list", help="display presets and demos")

    sp = sub.add_parser("sim", help="simulate a display (or all 12): ANSI, PNG, GIF; "
                                    "locally, or as a viewer of a relay (--url)")
    sp.add_argument("demo", nargs="?", default="tetris", choices=sorted(DEMOS))
    sp.add_argument("--display", "-d", default=DEFAULT_DISPLAY,
                    help=f"a preset, or all (default {DEFAULT_DISPLAY})")
    sp.add_argument("--frames", type=int, default=90, help="frames to play (default 90)")
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--png", type=pathlib.Path, metavar="DIR",
                    help="write the last frame as DIR/<display>.png (<display>-<demo>.png "
                         "with --gif)")
    sp.add_argument("--gif", type=pathlib.Path, metavar="DIR",
                    help="write every frame as DIR/<display>-<demo>.gif")
    sp.add_argument("--cell", type=int, help="cell height in pixels for PNG and GIF "
                                             "(default: the image fits 256 x 256)")
    sp.add_argument("--no-ansi", action="store_true", help="no terminal drawing")
    sp.add_argument("--fast", action="store_true", help="do not pace the terminal at fps")
    sp.add_argument("--url", help="view this relay's display instead of playing a demo: "
                                  "a remote sink (loopback only)")
    sp.add_argument("--window", action="store_true",
                    help="also draw in a pygame window, if pygame and a display exist")

    args = p.parse_args(argv)
    if args.cmd == "list":
        _list()
        return 0
    if args.cmd == "sim":
        from . import sim
        try:
            return sim.cli(args)
        except KeyboardInterrupt:
            return 130
    runner = {"run": _run, "relay": _relay, "source": _source, "view": _view}[args.cmd]
    try:
        return asyncio.run(runner(args)) or 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
