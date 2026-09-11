"""Remote display/control server (docs/PROTOCOL.md, draft v0).

    python -m tetris_sim.server --mode engine  --port 1709 --seed 42
    python -m tetris_sim.server --mode engine  --port 0 --clock lockstep
    python -m tetris_sim.server --mode display --port 1709 --html out.html

engine   runs the SPEC v1 engine at 30 FPS (or in lockstep), applies the
         controller's events and streams frames + state to every client.
display  accepts frames from one producer, validates them and renders them
         (ANSI to stdout, the HTML recorder, or a legacy Display adapter).

Binds 127.0.0.1 unless --host says otherwise. Standard library only (plus
the engine); no authentication in protocol v0, so keep it on loopback.
"""

import argparse
import asyncio
import importlib
import ipaddress
import json
import signal
import sys

from tetris_engine import core
from tetris_engine.adapter import to_legacy_frame
from tetris_engine.conformance import make_trace
from tetris_engine.frame import render
from tetris_engine.tables import COLS, FPS, ROWS

from . import ansi
from . import protocol as P
from .html import write_html
from .recorder import Recorder, hud

MAX_CLIENTS = 8
MAX_ERRORS = 16
MAX_WRITE_BUFFER = 1 << 20


# ------------------------------------------------------------------ sinks

class AnsiSink:
    """Truecolor frames, redrawn in place on a terminal stream."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.started = False

    def send(self, frame, info=None):
        if not self.started:
            self.out.write("\x1b[2J")
            self.started = True
        caption = (f"score {info['score']}  level {info['level']}  "
                   f"lines {info['lines']}  {info['phase']}" if info else "")
        self.out.write("\x1b[H" + ansi.frame_to_ansi(frame) + "\n" + caption
                       + "\x1b[K\n")
        self.out.flush()

    def close(self):
        pass


class HtmlSink:
    """Records every frame shown; writes the HTML replay on close."""

    def __init__(self, path, meta=None):
        self.path = path
        self.meta = meta or {}
        self.recorder = Recorder()

    def send(self, frame, info=None):
        self.recorder.send(frame, info)

    def close(self):
        if len(self.recorder):
            write_html(self.path, self.recorder.frames, self.recorder.info,
                       meta=self.meta)


class DisplaySink:
    """Any SPEC §2.3 Display: ``send(frame)`` + ``makeframe()``. A legacy
    ``utilities.display.Display`` (whose frames have ``asarray``) gets a
    legacy Frame of Color; anything else gets the 17x9 RGB tuples."""

    def __init__(self, display):
        self.display = display
        self.legacy = hasattr(display.makeframe(), "asarray")

    def send(self, frame, info=None):
        if self.legacy:
            self.display.send(to_legacy_frame(frame, self.display))
        else:
            self.display.send(frame)

    def close(self):
        close = getattr(self.display, "close", None)
        if callable(close):
            close()


def load_display(spec):
    """``module:attr`` -> a Display instance (attr is called if callable)."""
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ValueError("--display wants module:attr")
    obj = getattr(importlib.import_module(module_name), attr)
    return obj() if callable(obj) else obj


# ---------------------------------------------------------------- clients

class Client:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.role = None
        self.errors = 0
        self.closed = False

    def send(self, msg):
        if self.closed:
            return
        try:
            self.writer.write(P.encode(msg))
        except (ConnectionError, RuntimeError):
            self.close()
            return
        if self.writer.transport.get_write_buffer_size() > MAX_WRITE_BUFFER:
            self.close()  # a client that stopped reading

    def error(self, code, message):
        self.send(P.make_error(code, message))
        self.errors += 1
        if code in P.FATAL:
            self.close()
        elif self.errors >= MAX_ERRORS:
            self.send(P.make_error("too_many_errors",
                                   f"{MAX_ERRORS} errors on one connection"))
            self.close()

    async def drain(self):
        if self.closed:
            return
        try:
            await self.writer.drain()
        except (ConnectionError, RuntimeError):
            self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.writer.close()
            except RuntimeError:
                pass


class _Server:
    mode = None

    def __init__(self, sinks=(), fps=FPS):
        self.sinks = list(sinks)
        self.fps = fps
        self.clients = set()
        self.server = None
        self.address = None
        self.done = asyncio.Event()

    async def start(self, host=P.DEFAULT_HOST, port=P.DEFAULT_PORT):
        self.server = await asyncio.start_server(self._handle, host, port,
                                                 limit=P.MAX_MESSAGE)
        self.address = self.server.sockets[0].getsockname()[:2]
        return self.address

    async def close(self):
        for c in list(self.clients):
            c.close()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        self._end()
        for sink in self.sinks:
            close = getattr(sink, "close", None)
            if callable(close):
                close()

    def hello(self):
        return P.make_hello("server", mode=self.mode,
                            spec_version=P.SPEC_VERSION, rows=ROWS,
                            cols=COLS, fps=FPS, max_message=P.MAX_MESSAGE)

    def broadcast(self, msg, roles):
        for c in list(self.clients):
            if c.role in roles:
                c.send(msg)

    async def _handle(self, reader, writer):
        client = Client(reader, writer)
        if len(self.clients) >= MAX_CLIENTS:
            client.error("busy", f"at most {MAX_CLIENTS} connections")
            return
        self.clients.add(client)
        try:
            while not client.closed:
                try:
                    line = await reader.readline()
                except ValueError:  # the line outgrew MAX_MESSAGE
                    client.error("too_large", f"message over {P.MAX_MESSAGE} "
                                 "bytes")
                    break
                except ConnectionError:
                    break
                if not line.endswith(b"\n"):
                    break  # EOF; an unterminated fragment is discarded
                if not line.strip():
                    continue
                try:
                    msg = P.decode(line)
                    if client.role is None:
                        if msg["type"] != "hello":
                            raise P.ProtocolError("hello_required",
                                                  "send hello first")
                        self._hello(client, msg)
                    elif msg["type"] == "ping":
                        client.send(P.make_pong(msg.get("id")))
                    else:
                        await self._dispatch(client, msg)
                except P.ProtocolError as exc:
                    client.error(exc.code, exc.message)
                await client.drain()
        finally:
            self.clients.discard(client)
            self._left(client)
            client.close()

    def _forbidden(self, client, msg):
        return P.ProtocolError("forbidden", f"{msg['type']!r} is not accepted "
                               f"from a {client.role} in {self.mode} mode")

    def _hello(self, client, msg):
        raise NotImplementedError

    async def _dispatch(self, client, msg):
        raise NotImplementedError

    def _left(self, client):
        pass

    def _end(self):
        pass


# ------------------------------------------------------------ engine mode

class Session:
    """One controller's game: a fresh ``init(seed)`` and its event log."""

    def __init__(self, seed):
        self.seed = seed
        self.state = core.new_game(seed)
        self.k = 0
        self.pending = []
        self.log = []          # [frame, action, down]: a SPEC §12 trace
        self.last_state = None
        self.state_frame = 0
        self.task = None

    def trace(self, name="remote-session"):
        return make_trace(name, "recorded by tetris_sim.server", self.seed,
                          self.k, self.log)


class EngineServer(_Server):
    mode = "engine"

    def __init__(self, seed=1, clock="realtime", sinks=(), fps=FPS,
                 max_frames=None):
        super().__init__(sinks, fps)
        if clock not in P.CLOCKS:
            raise ValueError(f"clock must be one of {P.CLOCKS}")
        self.seed = seed
        self.clock = clock
        self.max_frames = max_frames
        self.controller = None
        self.session = None
        self.sessions = []     # finished sessions, oldest first

    def hello(self, seed=None):
        msg = super().hello()
        msg.update(seed=self.seed if seed is None else seed, clock=self.clock)
        return msg

    def _hello(self, client, msg):
        role = msg["role"]
        if role not in ("controller", "viewer"):
            raise P.ProtocolError("role", "engine mode accepts a controller "
                                  "or viewers")
        if role == "controller" and self.controller is not None:
            raise P.ProtocolError("busy", "a controller is already connected")
        client.role = role
        # A controller may choose its session's seed (docs/PROTOCOL.md §2).
        seed = msg.get("seed", self.seed) if role == "controller" else (
            self.session.seed if self.session is not None else self.seed)
        client.send(self.hello(seed))
        if role == "controller":
            self.controller = client
            self.session = Session(seed)
            if self.clock == "realtime":
                self.session.task = asyncio.ensure_future(
                    self._run_realtime(self.session))
        elif self.session is not None and self.session.last_state:
            client.send(self._state_msg(self.session))

    async def _dispatch(self, client, msg):
        kind = msg["type"]
        if client.role != "controller" or kind not in ("event", "tick"):
            raise self._forbidden(client, msg)
        s = self.session
        if kind == "event":
            s.pending.append((msg["action"], msg["down"]))
            return
        if self.clock != "lockstep":
            raise P.ProtocolError("forbidden", "tick needs --clock lockstep")
        for _ in range(msg["frames"]):
            if not self.step(s):
                break
            await client.drain()

    def step(self, s):
        """Advance session ``s`` one frame; False once max_frames is hit."""
        if self.max_frames is not None and s.k >= self.max_frames:
            self.done.set()
            return False
        events, s.pending = s.pending, []
        s.log.extend([s.k, a, d] for a, d in events)
        s.state = core.step(s.state, events)
        frame = render(s.state)
        info = hud(s.state)
        self.broadcast(P.make_frame(s.k, frame), ("controller", "viewer"))
        for sink in self.sinks:
            sink.send(frame, info)
        key = (info["phase"], info["score"], info["level"], info["lines"],
               info["high"])
        if key != s.last_state:
            s.last_state, s.state_frame = key, s.k
            self.broadcast(self._state_msg(s), ("controller", "viewer"))
        s.k += 1
        return True

    @staticmethod
    def _state_msg(s):
        phase, score, level, lines, high = s.last_state
        return P.make_state(score, level, lines, high_score=high, phase=phase,
                            frame_no=s.state_frame)

    async def _run_realtime(self, s):
        loop = asyncio.get_running_loop()
        period = 1.0 / self.fps
        next_t = loop.time()
        while self.session is s and self.step(s):
            next_t += period
            delay = next_t - loop.time()
            if delay < -1.0:
                next_t = loop.time()  # fell far behind: resync, don't burst
            await asyncio.sleep(max(delay, 0.0))

    def _left(self, client):
        if client is self.controller:
            self.controller = None
            self._end()

    def _end(self):
        s, self.session = self.session, None
        if s is None:
            return
        if s.task is not None:
            s.task.cancel()
        self.sessions.append(s)


# ----------------------------------------------------------- display mode

class DisplayServer(_Server):
    mode = "display"

    def __init__(self, sinks=(), pace=True, fps=FPS, once=False):
        super().__init__(sinks, fps)
        self.pace = pace
        self.once = once
        self.producer = None
        self.last_frame_no = None
        self.state = None
        self.shown = 0
        self._next_slot = None

    def _hello(self, client, msg):
        role = msg["role"]
        if role not in ("producer", "viewer"):
            raise P.ProtocolError("role", "display mode accepts a producer "
                                  "or viewers")
        if role == "producer" and self.producer is not None:
            raise P.ProtocolError("busy", "a producer is already connected")
        client.role = role
        client.send(self.hello())
        if role == "producer":
            self.producer = client
            self.last_frame_no = None

    def info(self):
        s = self.state
        if s is None:
            return None
        return {"score": s["score"], "level": s["level"], "lines": s["lines"],
                "high": s.get("high_score", 0), "phase": s.get("phase", "remote")}

    async def _dispatch(self, client, msg):
        kind = msg["type"]
        if client.role != "producer" or kind not in ("frame", "state"):
            raise self._forbidden(client, msg)
        if kind == "state":
            self.state = msg
            self.broadcast(msg, ("viewer",))
            return
        if self.last_frame_no is not None and msg["frame_no"] <= self.last_frame_no:
            raise P.ProtocolError("bad_frame", "frame_no must increase "
                                  f"(last was {self.last_frame_no})")
        self.last_frame_no = msg["frame_no"]
        if self.pace:
            await self._wait_slot()
        for sink in self.sinks:
            sink.send(msg["rows"], self.info())
        self.shown += 1
        self.broadcast(msg, ("viewer",))

    async def _wait_slot(self):
        """Show at most ``fps`` frames per second (SPEC §2.3)."""
        loop = asyncio.get_running_loop()
        now = loop.time()
        if self._next_slot is not None and now < self._next_slot:
            await asyncio.sleep(self._next_slot - now)
            now = self._next_slot
        self._next_slot = now + 1.0 / self.fps

    def _left(self, client):
        if client is self.producer:
            self.producer = None
            if self.once:
                self.done.set()


# -------------------------------------------------------------------- CLI

def _is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def build_parser():
    p = argparse.ArgumentParser(prog="python -m tetris_sim.server",
                                description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=P.MODES, required=True)
    p.add_argument("--host", default=P.DEFAULT_HOST,
                   help="bind address (default 127.0.0.1: loopback only)")
    p.add_argument("--port", type=int, default=P.DEFAULT_PORT,
                   help=f"TCP port (default {P.DEFAULT_PORT}; 0 = any free port)")
    p.add_argument("--seed", type=int, default=1, help="engine PRNG seed")
    p.add_argument("--clock", choices=P.CLOCKS, default="realtime",
                   help="engine clock: 30 FPS, or advance on 'tick' messages")
    p.add_argument("--max-frames", type=int,
                   help="engine: stop after this many frames of a session")
    p.add_argument("--ansi", action="store_true",
                   help="render frames to stdout (default in display mode)")
    p.add_argument("--html", help="write an HTML replay of the frames on exit")
    p.add_argument("--display", metavar="MODULE:ATTR",
                   help="send frames to this Display (e.g. the building's)")
    p.add_argument("--trace-out",
                   help="engine: write the last session as a conformance trace")
    p.add_argument("--once", action="store_true",
                   help="display: exit when the producer disconnects")
    p.add_argument("--no-pace", action="store_true",
                   help="display: do not hold frames to 30 FPS (tests only)")
    return p


def build_server(args):
    sinks = []
    if args.ansi or (args.mode == "display" and not (args.html or args.display)):
        sinks.append(AnsiSink())
    if args.html:
        title = "17x9 Tetris (remote engine)" if args.mode == "engine" \
            else "17x9 Tetris display server"
        sinks.append(HtmlSink(args.html, {"title": title,
                                          "seed": args.seed if args.mode == "engine" else None}))
    if args.display:
        sinks.append(DisplaySink(load_display(args.display)))
    if args.mode == "engine":
        return EngineServer(seed=args.seed, clock=args.clock, sinks=sinks,
                            max_frames=args.max_frames)
    return DisplayServer(sinks=sinks, pace=not args.no_pace, once=args.once)


async def serve(args):
    server = build_server(args)
    host, port = await server.start(args.host, args.port)
    if not _is_loopback(args.host):
        print(f"tetris_sim.server: WARNING: {args.host} is not loopback and "
              "protocol v0 has no authentication", file=sys.stderr)
    extra = (f" (seed {args.seed}, clock {args.clock})"
             if args.mode == "engine" else "")
    if _is_loopback(args.host) and not _is_loopback(host):
        # e.g. a FreeBSD jail whose lo0 carries the jail's own address
        extra += f" [the kernel mapped {args.host} to {host}]"
    print(f"tetris_sim.server: {args.mode} mode listening on {host}:{port}"
          f"{extra}", file=sys.stderr, flush=True)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, server.done.set)
        except (NotImplementedError, RuntimeError):
            pass
    await server.done.wait()
    await server.close()
    if args.mode == "engine":
        if args.trace_out and server.sessions and server.sessions[-1].k:
            with open(args.trace_out, "w") as fh:
                json.dump(server.sessions[-1].trace(), fh, indent=1)
                fh.write("\n")
        frames = sum(s.k for s in server.sessions)
        print(f"tetris_sim.server: {len(server.sessions)} session(s), "
              f"{frames} frames", file=sys.stderr, flush=True)
    else:
        print(f"tetris_sim.server: {server.shown} frames shown",
              file=sys.stderr, flush=True)
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(serve(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
