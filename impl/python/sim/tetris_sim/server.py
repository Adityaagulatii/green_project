"""Remote display/control server (docs/PROTOCOL.md, contract v1), over TCP
(JSON lines, port 1709) and WebSocket (ws://HOST:1710/tetris-17x9).

    python -m tetris_sim.server --mode engine  --seed 42
    python -m tetris_sim.server --mode engine  --port 0 --clock lockstep
    python -m tetris_sim.server --mode engine  --transport ws
    python -m tetris_sim.server --mode engine  --transport both
    python -m tetris_sim.server --mode engine  --unix /tmp/tetris.sock
    python -m tetris_sim.server --mode display --html out.html

engine   runs the SPEC v1 engine at 30 FPS (or in lockstep), applies the
         controller's events and streams frames + state to every client.
display  accepts frames from one producer, validates them and renders them
         (ANSI to stdout, the HTML recorder, or a legacy Display adapter).

Transports: tcp (JSON lines, the default, port 1709), ws (WebSocket, port
1710, path /tetris-17x9, subprotocol tetris-17x9.v1), or both. --unix PATH
serves the chosen binding on a unix socket instead of a port. All listeners
feed one server: one session, one controller/producer slot and one
connection limit, whatever carries each connection. ws needs the websockets
package; tcp is standard library only.

Binds 127.0.0.1 unless --host says otherwise. The protocol has no
authentication: keep it on loopback, or front it with a gatekeeper
(contract §9). In a FreeBSD jail whose lo0 carries the jail's own address
(e.g. 10.0.0.22), the kernel maps a bind to 127.0.0.1 onto that address,
which other jails on the host can reach: the server prints the address it
really got, and --unix avoids the question. A WebSocket handshake that
carries an Origin header (a browser page) is refused with 403 unless
--ws-origin allows that origin (contract §5.3); non-browser clients send no
Origin and are accepted.

One line per listener goes to stderr on startup:
    tetris_sim.server: engine mode listening on 127.0.0.1:1709 (seed 1, clock realtime)
    tetris_sim.server: engine mode listening on ws://127.0.0.1:1710/tetris-17x9 (...)
    tetris_sim.server: engine mode listening on unix:/tmp/tetris.sock (seed 1, ...)
"""

import argparse
import asyncio
import contextlib
import importlib
import ipaddress
import json
import os
import signal
import stat
import sys
from collections import deque
from http import HTTPStatus
from urllib.parse import urlsplit

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
CLOSE_TIMEOUT = 5.0
TRANSPORTS = ("tcp", "ws")
WS_MISSING = ("the ws transport needs the websockets package (FreeBSD: pkg "
              "install py312-websockets; elsewhere: pip install websockets)")

# WebSocket close codes (RFC 6455 §7.4.1) after a fatal error (PROTOCOL §6).
WS_NORMAL, WS_GOING_AWAY, WS_POLICY, WS_TOO_BIG, WS_TRY_LATER = (
    1000, 1001, 1008, 1009, 1013)


def ws_close_code(reason):
    """The close code for a connection the server ends: ``reason`` is None
    (no error), "shutdown", or the §6 code of a fatal error."""
    if reason is None:
        return WS_NORMAL
    if reason == "shutdown":
        return WS_GOING_AWAY
    return {"too_large": WS_TOO_BIG, "busy": WS_TRY_LATER}.get(reason, WS_POLICY)


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
    """One connection, whatever carries it. The server sees only ``send``,
    ``recv``, ``decode``, ``drain``, ``close`` and ``finish``."""

    transport = None

    def __init__(self):
        self.role = None
        self.errors = 0
        self.closed = False

    def error(self, code, message):
        self.send(P.make_error(code, message))
        self.errors += 1
        if code in P.FATAL:
            self.close(code)
        elif self.errors >= MAX_ERRORS:
            self.send(P.make_error("too_many_errors",
                                   f"{MAX_ERRORS} errors on one connection"))
            self.close("too_many_errors")

    def decode(self, data):
        return P.decode(data)

    async def finish(self):
        """Wait (briefly) until what was sent is out and the link is shut."""


class TcpClient(Client):
    """The JSON-lines binding (§1), over TCP or a unix socket."""

    transport = "tcp"

    def __init__(self, reader, writer):
        super().__init__()
        self.reader = reader
        self.writer = writer

    def send(self, msg):
        if self.closed:
            return
        try:
            self.writer.write(P.encode(msg))
        except (ConnectionError, RuntimeError):
            self.close()
            return
        if self.writer.transport.get_write_buffer_size() > MAX_WRITE_BUFFER:
            self.abort()  # a client that stopped reading (§7)

    def abort(self):
        """Drop the connection now. A graceful close would wait to flush a
        buffer the client does not read, and never disconnect it."""
        self.closed = True
        self.writer.transport.abort()

    async def recv(self):
        """The next non-empty line, or None at EOF."""
        while True:
            try:
                line = await self.reader.readline()
            except ValueError:  # the line outgrew MAX_MESSAGE
                raise P.ProtocolError("too_large", f"message over "
                                      f"{P.MAX_MESSAGE} bytes") from None
            except ConnectionError:
                return None
            if not line.endswith(b"\n"):
                return None  # EOF; an unterminated fragment is discarded
            if line.strip():
                return line

    async def drain(self):
        if self.closed:
            return
        try:
            await self.writer.drain()
        except (ConnectionError, RuntimeError):
            self.close()

    def close(self, reason=None):
        if not self.closed:
            self.closed = True
            with contextlib.suppress(RuntimeError):
                self.writer.close()


class WsClient(Client):
    """The WebSocket binding: one message per text frame. Sends go through
    a queue and a writer task, so ``send`` stays synchronous (a broadcast
    from the engine's step) and a fatal error still reaches the client
    before the close frame."""

    transport = "ws"

    def __init__(self, ws):
        super().__init__()
        self.ws = ws
        self.queue = deque()        # (text, bytes)
        self.queued = 0
        self.close_code = WS_NORMAL
        self.close_reason = ""
        self.wakeup = asyncio.Event()
        self.idle = asyncio.Event()
        self.idle.set()
        self.writer = asyncio.ensure_future(self._write())

    def send(self, msg):
        if self.closed:
            return
        data = P.encode(msg)
        self.queue.append((data[:-1].decode("utf-8"), len(data) - 1))
        self.queued += len(data) - 1
        self.idle.clear()
        self.wakeup.set()
        transport = self.ws.transport
        if self.queued + transport.get_write_buffer_size() > MAX_WRITE_BUFFER:
            self.abort()  # §5.3: no close handshake; the peer sees 1006

    async def _write(self):
        from websockets.exceptions import ConnectionClosed
        try:
            while True:
                self.wakeup.clear()
                while self.queue:
                    text, n = self.queue.popleft()
                    self.queued -= n
                    await self.ws.send(text)
                self.idle.set()
                if self.closed:
                    break
                await self.wakeup.wait()
            await self.ws.close(self.close_code, self.close_reason)
        except ConnectionClosed:
            pass
        finally:
            self.closed = True
            self.queue.clear()
            self.idle.set()

    async def recv(self):
        """The next non-blank message (str, or bytes for a binary frame), or
        None once closed. The library enforces max_size (close 1009) and
        UTF-8 (close 1007) before a message gets here."""
        from websockets.exceptions import ConnectionClosed
        while True:
            try:
                data = await self.ws.recv()
            except ConnectionClosed:
                return None
            if isinstance(data, bytes) or data.strip():
                return data

    def decode(self, data):
        if isinstance(data, bytes):
            raise P.ProtocolError("malformed", "binary frame: send each "
                                  "message as JSON in a text frame")
        return P.decode(data)

    async def drain(self):
        await self.idle.wait()

    def close(self, reason=None):
        if not self.closed:
            self.closed = True
            self.close_code = ws_close_code(reason)
            self.close_reason = reason or ""
            self.wakeup.set()

    def abort(self):
        """Drop the connection now: a close frame would only queue behind
        the output that the client is not reading."""
        self.closed = True
        self.writer.cancel()
        self.ws.transport.abort()

    async def finish(self):
        self.close()
        done, _ = await asyncio.wait({self.writer}, timeout=CLOSE_TIMEOUT)
        if not done:
            self.abort()


# ---------------------------------------------------------------- servers

async def _ws_serve(handler, host, port, path, origins):
    """A websockets server for the WS binding (contract §5.3): the contract
    at /tetris-17x9 (404 elsewhere), subprotocol tetris-17x9.v1 required
    (400 without), and an Origin allow-list (403 otherwise): clients that
    send no Origin are always admitted, ``origins`` adds to them, and "*"
    in it admits every origin.

    max_size is 65536, one byte over the 65535-byte text limit, so that a
    message just over the limit reaches the codec and gets the full
    too_large treatment (error, then close 1009 "too_large"). Anything
    bigger is refused by the frame layer with a bare 1009, before it is
    buffered, which §5.3 allows."""
    try:
        from websockets.asyncio.server import serve, unix_serve
    except ImportError as exc:
        raise RuntimeError(WS_MISSING) from exc
    if origins and "*" in origins:
        allowed = None
    else:
        allowed = [None, *(origins or ())]

    def process_request(connection, request):
        if urlsplit(request.path).path != P.WS_PATH:
            return connection.respond(HTTPStatus.NOT_FOUND,
                                      f"the 17x9 protocol is at {P.WS_PATH}\n")
        return None

    options = {"max_size": P.MAX_MESSAGE, "compression": None,
               "origins": allowed, "subprotocols": [P.WS_SUBPROTOCOL],
               "process_request": process_request, "close_timeout": 2}
    if path is not None:
        return await unix_serve(handler, path, **options)
    return await serve(handler, host, port, **options)


class _Server:
    mode = None

    def __init__(self, sinks=(), fps=FPS, ws_origins=None):
        self.sinks = list(sinks)
        self.fps = fps
        self.ws_origins = ws_origins
        self.clients = set()
        self.listeners = []     # (transport, server, address)
        self.address = None     # the first listener's
        self.done = asyncio.Event()

    @property
    def addresses(self):
        return {transport: address for transport, _s, address in self.listeners}

    async def start(self, host=P.DEFAULT_HOST, port=P.DEFAULT_PORT,
                    transport="tcp", path=None):
        """Add a listener; return its address: (host, port), or the unix
        socket path when ``path`` is given."""
        if transport == "tcp" and path is not None:
            server = await asyncio.start_unix_server(self._handle_tcp, path,
                                                     limit=P.MAX_MESSAGE)
        elif transport == "tcp":
            server = await asyncio.start_server(self._handle_tcp, host, port,
                                                limit=P.MAX_MESSAGE)
        elif transport == "ws":
            server = await _ws_serve(self._handle_ws, host, port, path,
                                     self.ws_origins)
        else:
            raise ValueError(f"transport must be one of {TRANSPORTS}")
        name = server.sockets[0].getsockname()
        address = name if path is not None else tuple(name[:2])
        self.listeners.append((transport, server, address))
        if self.address is None:
            self.address = address
        return address

    async def close(self):
        for c in list(self.clients):
            c.close("shutdown")
        for _t, server, _a in self.listeners:
            server.close()
        for _t, server, address in self.listeners:
            await server.wait_closed()
            if isinstance(address, str):
                _unlink_socket(address)
        self._end()
        for sink in self.sinks:
            close = getattr(sink, "close", None)
            if callable(close):
                close()

    def hello(self, client_role):
        return P.make_hello("server", mode=self.mode, client_role=client_role,
                            spec_version=P.SPEC_VERSION, rows=ROWS,
                            cols=COLS, fps=FPS, max_message=P.MAX_MESSAGE)

    def broadcast(self, msg, roles):
        for c in list(self.clients):
            if c.role in roles:
                c.send(msg)

    async def _handle_tcp(self, reader, writer):
        await self._serve(TcpClient(reader, writer))

    async def _handle_ws(self, ws):
        await self._serve(WsClient(ws))

    async def _serve(self, client):
        """One connection's life (§2 Lifecycle), for every transport."""
        if len(self.clients) >= MAX_CLIENTS:
            client.error("busy", f"at most {MAX_CLIENTS} connections")
            await client.finish()
            return
        self.clients.add(client)
        try:
            while not client.closed:
                try:
                    data = await client.recv()
                except P.ProtocolError as exc:
                    client.error(exc.code, exc.message)
                    break
                if data is None:
                    break
                try:
                    msg = client.decode(data)
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
            await client.finish()

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


def _unlink_socket(path):
    with contextlib.suppress(OSError):
        if stat.S_ISSOCK(os.stat(path).st_mode):
            os.unlink(path)


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
                 max_frames=None, ws_origins=None):
        super().__init__(sinks, fps, ws_origins)
        if clock not in P.CLOCKS:
            raise ValueError(f"clock must be one of {P.CLOCKS}")
        self.seed = seed
        self.clock = clock
        self.max_frames = max_frames
        self.controller = None
        self.session = None
        self.sessions = []     # finished sessions, oldest first

    def hello(self, client_role, seed=None):
        msg = super().hello(client_role)
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
        client.send(self.hello(role, seed))
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
        # §3/§4.4: frame k carries E_k, the events passed to this step.
        self.broadcast(P.make_frame(s.k, frame, events=events),
                       ("controller", "viewer"))
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

    def __init__(self, sinks=(), pace=True, fps=FPS, once=False,
                 ws_origins=None):
        super().__init__(sinks, fps, ws_origins)
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
        client.send(self.hello(role))
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
    p.add_argument("--transport", choices=(*TRANSPORTS, "both"), default="tcp",
                   help="tcp: JSON lines (default); ws: WebSocket, one JSON "
                   "message per text message; both: tcp on --port and ws on "
                   "--ws-port")
    p.add_argument("--host", default=P.DEFAULT_HOST,
                   help="bind address (default 127.0.0.1: loopback only)")
    p.add_argument("--port", type=int,
                   help=f"port (default {P.DEFAULT_PORT} for tcp, "
                   f"{P.DEFAULT_WS_PORT} for ws; 0 = any free port)")
    p.add_argument("--ws-port", type=int,
                   help=f"with --transport both: the WebSocket port (default "
                   f"{P.DEFAULT_WS_PORT}, or --port + 1 when --port is given, "
                   f"or 0 when --port is 0)")
    p.add_argument("--unix", metavar="PATH",
                   help="serve the tcp or ws binding on this unix socket "
                   "instead of a port")
    p.add_argument("--ws-origin", action="append", metavar="ORIGIN",
                   help="also admit WebSocket handshakes from this browser "
                   "Origin (repeatable; 'null' for file:// pages, '*' for any). "
                   "Default: only clients that send no Origin (contract §5.3)")
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


def listen_plan(args):
    """[(transport, port, unix path)] for the parsed arguments."""
    port = args.port
    if args.transport == "both":
        if args.unix:
            raise ValueError("--unix serves one binding: use --transport tcp "
                             "or ws")
        if args.ws_port is not None:
            ws_port = args.ws_port
        elif port is None:
            ws_port = P.DEFAULT_WS_PORT
        else:
            ws_port = port + 1 if port else 0
        return [("tcp", P.DEFAULT_PORT if port is None else port, None),
                ("ws", ws_port, None)]
    if args.ws_port is not None:
        raise ValueError("--ws-port needs --transport both")
    if port is None:
        port = P.DEFAULT_WS_PORT if args.transport == "ws" else P.DEFAULT_PORT
    return [(args.transport, port, args.unix)]


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
                            max_frames=args.max_frames,
                            ws_origins=args.ws_origin)
    return DisplayServer(sinks=sinks, pace=not args.no_pace, once=args.once,
                         ws_origins=args.ws_origin)


def _where(transport, address):
    if isinstance(address, str):
        return ("unix:" if transport == "tcp" else "ws+unix:") + address
    host, port = address
    if transport == "tcp":
        return f"{host}:{port}"  # unchanged: the Emacs client parses it
    shown = f"[{host}]" if ":" in host else host
    return f"ws://{shown}:{port}{P.WS_PATH}"


async def serve(args):
    plan = listen_plan(args)
    server = build_server(args)
    extra = (f" (seed {args.seed}, clock {args.clock})"
             if args.mode == "engine" else "")
    try:
        for transport, port, path in plan:
            address = await server.start(args.host, port, transport, path)
            note = ""
            if path is None and _is_loopback(args.host) \
                    and not _is_loopback(address[0]):
                # e.g. a FreeBSD jail whose lo0 carries the jail's own address
                note = f" [the kernel mapped {args.host} to {address[0]}]"
            print(f"tetris_sim.server: {args.mode} mode listening on "
                  f"{_where(transport, address)}{extra}{note}",
                  file=sys.stderr, flush=True)
    except BaseException:
        await server.close()
        raise
    if not args.unix and not _is_loopback(args.host):
        print(f"tetris_sim.server: WARNING: {args.host} is not loopback and "
              "the protocol has no authentication (contract §9.1)",
              file=sys.stderr)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, server.done.set)
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
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        listen_plan(args)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        return asyncio.run(serve(args))
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError) as exc:
        print(f"tetris_sim.server: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
