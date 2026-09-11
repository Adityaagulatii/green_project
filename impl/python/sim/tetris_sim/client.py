"""Reference client for the remote protocol (docs/PROTOCOL.md, contract v1),
and a seeded bot that plays through it, over TCP or WebSocket.

    python -m tetris_sim.client tcp://127.0.0.1:1709 --seed 1 --bot
    python -m tetris_sim.client ws://127.0.0.1:1710/tetris-17x9 --seed 1 --bot --ansi
    python -m tetris_sim.client ws://127.0.0.1:1710 --role viewer --frames 300
    python -m tetris_sim.client unix:///tmp/t.sock --trace spec/conformance/traces/08-hold.json

URLs: tcp://HOST:PORT and unix:///PATH carry JSON lines; ws://HOST:PORT[/PATH]
(default path /tetris-17x9) and ws+unix:///PATH carry one JSON message per
WebSocket text message, with the subprotocol tetris-17x9.v1.

A controller is a replica (contract §4.4): it folds the session's log (the
seed from the hello, and E_k from each frame's `events`) with its own engine
and checks every frame's digest, under either clock. It also checks that
E_k holds exactly the events it sent, in order. Under the lockstep clock it
drives time itself: it runs the fold ahead, sends each frame's events and
the ticks, and checks the frames as they stream back.

Output: with --digests, "FRAME_NO DIGEST" per frame; then one JSON summary
line on stdout. Its session_digest is the SHA-256 of the frame digests, each
followed by LF, so a session gives the same value over every transport.
Exit status: 0 every frame verified; 1 a mismatch or a short session;
2 usage; 3 refused, closed with an error, or unreachable.
"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import sys
from collections import deque

from tetris_engine import core
from tetris_engine.conformance import group_events, make_trace
from tetris_engine.frame import frame_digest, render

from . import protocol as P
from .bot import Bot
from .server import WS_MISSING, AnsiSink

DEFAULT_FRAMES = 900
TIMEOUT = 10.0


class ClientError(Exception):
    """The session could not go on. ``code`` is a contract §7 code when the
    server sent one, else closed / handshake / bad_message / usage."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def parse_url(url):
    """URL -> (transport, host, port, path, uri). transport is "tcp" (the
    JSON-lines binding, also over a unix socket) or "ws"; path is a unix
    socket path or None; uri is the WebSocket URI or None."""
    for scheme, transport in (("ws+unix", "ws"), ("unix", "tcp")):
        if url.startswith(scheme + ":"):
            path = url[len(scheme) + 1:]
            path = path[2:] if path.startswith("//") else path
            if not path:
                raise ValueError(f"{url}: a unix URL names a socket path")
            return transport, None, None, path, None
    if "://" not in url:
        url = "tcp://" + url
    scheme, _, rest = url.partition("://")
    if scheme not in ("tcp", "ws", "wss"):
        raise ValueError(f"{url}: use tcp://, ws://, wss://, unix: or ws+unix:")
    netloc, slash, tail = rest.partition("/")
    host = P.DEFAULT_HOST
    port = P.DEFAULT_PORT if scheme == "tcp" else P.DEFAULT_WS_PORT
    if netloc.startswith("["):                      # [v6]:port
        host, _, after = netloc[1:].partition("]")
        if after.startswith(":"):
            port = int(after[1:])
    elif ":" in netloc:
        host, _, p = netloc.rpartition(":")
        port = int(p)
    elif netloc:
        host = netloc
    if not 0 < port < 65536:
        raise ValueError(f"{url}: bad port {port}")
    if scheme == "tcp":
        return "tcp", host, port, None, None
    shown = f"[{host}]" if ":" in host else host
    target = f"{slash}{tail}" if slash else P.WS_PATH
    return "ws", host, port, None, f"{scheme}://{shown}:{port}{target}"


# ------------------------------------------------------------ connections

class Connection:
    """One connection to a server: send and receive protocol messages."""

    transport = None

    def __init__(self):
        self.hello = None
        self.close_code = None      # WebSocket close code, once closed
        self.close_reason = None

    async def send(self, *msgs):
        for msg in msgs:
            await self.raw(P.encode(msg))

    async def recv(self, timeout=None):
        """The next message, validated; None once the server has closed."""
        data = await asyncio.wait_for(self.recv_raw(), timeout)
        if data is None:
            return None
        try:
            return P.decode(data)
        except P.ProtocolError as exc:
            raise ClientError("bad_message", f"the server sent {exc}") from None

    async def expect(self, kind, timeout=None):
        while True:
            msg = await self.recv(timeout)
            if msg is None:
                raise ClientError("closed", f"closed while waiting for {kind!r}")
            if msg["type"] == kind:
                return msg

    async def until_pong(self, tag="barrier", timeout=None):
        """The messages received before the pong that answers this ping: a
        barrier, since the server handles a connection's messages in order."""
        await self.send(P.make_ping(tag))
        seen = []
        while True:
            msg = await self.recv(timeout)
            if msg is None:
                raise ClientError("closed", "closed before the pong")
            if msg["type"] == "pong" and msg.get("id") == tag:
                return seen
            seen.append(msg)

    async def error_then_closed(self, timeout=None):
        """The first error's code, and whether the server closed right after.
        (A viewer that joins mid-session first gets the current state.)"""
        msg = await self.recv(timeout)
        while msg is not None and msg["type"] != "error":
            msg = await self.recv(timeout)
        if msg is None:
            raise ClientError("closed", "closed without an error")
        return msg["code"], await self.recv(timeout) is None

    async def handshake(self, role, timeout=None, **fields):
        """Send our hello; return the server's, or raise its error."""
        await self.send(P.make_hello(role, **{k: v for k, v in fields.items()
                                              if v is not None}))
        msg = await self.recv(timeout)
        if msg is None:
            raise ClientError("closed", "the server closed during the handshake")
        if msg["type"] == "error":
            raise ClientError(msg["code"], msg["message"])
        if msg["type"] != "hello":
            raise ClientError("bad_message", f"expected hello, got {msg['type']!r}")
        self.hello = msg
        return msg


class TcpConnection(Connection):
    """The JSON-lines binding, over TCP or a unix socket."""

    transport = "tcp"

    def __init__(self, reader, writer):
        super().__init__()
        self.reader = reader
        self.writer = writer

    @classmethod
    async def open(cls, host=None, port=None, path=None):
        if path is not None:
            reader, writer = await asyncio.open_unix_connection(
                path, limit=P.MAX_MESSAGE)
        else:
            reader, writer = await asyncio.open_connection(
                host, port, limit=P.MAX_MESSAGE)
        return cls(reader, writer)

    async def raw(self, data):
        """Write bytes as they are (tests use this to send bad input). A
        closed connection is not an error here: recv() reports it."""
        with contextlib.suppress(ConnectionError, RuntimeError):
            self.writer.write(data)
            await self.writer.drain()

    async def recv_raw(self):
        try:
            line = await self.reader.readline()
        except (ConnectionError, ValueError):
            return None
        return line if line.endswith(b"\n") else None

    async def close(self):
        self.writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await self.writer.wait_closed()


class WsConnection(Connection):
    """The WebSocket binding (contract §5.3): one message per text message,
    subprotocol tetris-17x9.v1."""

    transport = "ws"

    def __init__(self, ws):
        super().__init__()
        self.ws = ws

    @classmethod
    async def open(cls, uri=None, path=None, timeout=TIMEOUT):
        try:
            from websockets.asyncio.client import connect, unix_connect
            from websockets.exceptions import InvalidHandshake
        except ImportError as exc:
            raise ClientError("unsupported", WS_MISSING) from exc
        options = {"max_size": P.MAX_MESSAGE, "compression": None,
                   "subprotocols": [P.WS_SUBPROTOCOL], "open_timeout": timeout}
        try:
            if path is not None:
                ws = await unix_connect(path, uri or f"ws://localhost{P.WS_PATH}",
                                        **options)
            else:
                ws = await connect(uri, proxy=None, **options)
        except InvalidHandshake as exc:
            raise ClientError("handshake", str(exc)) from None
        if ws.subprotocol != P.WS_SUBPROTOCOL:     # §5.3: MUST fail
            await ws.close()
            raise ClientError("handshake", f"the server did not select "
                              f"{P.WS_SUBPROTOCOL}")
        return cls(ws)

    async def raw(self, data, binary=False):
        """Send one message. Bytes go as text (dropping the LF that
        ``P.encode`` adds) unless ``binary``; they need not be UTF-8, so
        tests can send bad input."""
        from websockets.exceptions import ConnectionClosed
        if not binary and isinstance(data, bytes) and data.endswith(b"\n"):
            data = data[:-1]
        with contextlib.suppress(ConnectionClosed):
            if binary:
                await self.ws.send(bytes(data))
            elif isinstance(data, str):
                await self.ws.send(data)
            else:
                await self.ws.send(data, text=True)

    async def recv_raw(self):
        from websockets.exceptions import ConnectionClosed
        try:
            return await self.ws.recv()
        except ConnectionClosed:
            self.close_code = self.ws.close_code
            self.close_reason = self.ws.close_reason
            return None

    async def close(self):
        await self.ws.close()


async def connect(url, role=None, timeout=TIMEOUT, **hello):
    """Open ``url``; with ``role``, also exchange hellos (``conn.hello``)."""
    transport, host, port, path, uri = parse_url(url)
    if transport == "tcp":
        conn = await asyncio.wait_for(TcpConnection.open(host, port, path),
                                      timeout)
    else:
        conn = await WsConnection.open(uri, path, timeout)
    if role is not None:
        try:
            await conn.handshake(role, timeout, **hello)
        except BaseException:
            await conn.close()
            raise
    return conn


# ------------------------------------------------------------------ driver

class Result:
    def __init__(self, conn, role, wanted):
        self.transport = conn.transport
        self.role = role
        self.hello = conn.hello
        self.seed = conn.hello.get("seed")
        self.wanted = wanted
        self.frames = []        # (frame_no, digest), as received
        self.events = []        # [frame, action, down]: the session's log
        self.verified = 0
        self.mismatches = 0
        self.first_mismatch = None
        self.state = None
        self.errors = []
        self.trace_match = None

    @property
    def digests(self):
        return [d for _k, d in self.frames]

    def record(self, frame_no, digest, ok):
        self.frames.append((frame_no, digest))
        if ok:
            self.verified += 1
        else:
            self.mismatches += 1
            if self.first_mismatch is None:
                self.first_mismatch = frame_no

    def take(self, msg):
        if msg["type"] == "state":
            self.state = msg
        elif msg["type"] == "error":
            self.errors.append(msg["code"])
            print(f"tetris_sim.client: server error {msg['code']}: "
                  f"{msg['message']}", file=sys.stderr)

    def session_digest(self):
        h = hashlib.sha256()
        for d in self.digests:
            h.update(d.encode("ascii") + b"\n")
        return h.hexdigest()

    @property
    def fatal(self):
        return any(code in P.FATAL for code in self.errors)

    @property
    def ok(self):
        return (not self.mismatches and not self.fatal
                and (self.wanted is None or len(self.frames) >= self.wanted)
                and self.trace_match is not False)

    def summary(self):
        s = self.state or {}
        out = {"transport": self.transport, "role": self.role,
               "mode": self.hello.get("mode"), "clock": self.hello.get("clock"),
               "seed": self.seed, "frames": len(self.frames),
               "events": len(self.events), "verified": self.verified,
               "mismatches": self.mismatches,
               "first_mismatch": self.first_mismatch, "errors": self.errors,
               "session_digest": self.session_digest(),
               "score": s.get("score"), "level": s.get("level"),
               "lines": s.get("lines"), "phase": s.get("phase")}
        if self.trace_match is not None:
            out["trace_match"] = self.trace_match
        return out


def bot_policy(fast=False):
    """The demo bot as a policy: ``policy(state, frame) -> events``."""
    bot = Bot() if fast else Bot(pace=3, think=6, batch_shifts=False)
    return lambda state, _k: bot(state)


class TracePolicy:
    """A conformance trace's events, frame by frame (lockstep only)."""

    lockstep_only = True

    def __init__(self, trace):
        self.by_frame = group_events(trace["events"])

    def __call__(self, _state, k):
        return self.by_frame.get(k, ())


def _events(msg):
    return [(a, d) for a, d in msg["events"]] if "events" in msg else None


async def _lockstep(conn, res, frames, policy, on_frame, timeout):
    """Run the fold ahead, send each frame's events and the ticks, and check
    every frame (digest and E_k) that the server streams back."""
    expected = []               # (digest, E_k) per frame

    async def sender():
        s, run = core.new_game(res.seed), 0
        for k in range(frames):
            events = [(a, bool(d)) for a, d in policy(s, k)] if policy else []
            if events:
                if run:
                    await conn.send(P.make_tick(run))
                    run = 0
                await conn.send(*(P.make_event(a, d) for a, d in events))
            s = core.step(s, events)
            expected.append((frame_digest(render(s)), events))
            run += 1
            if run == P.MAX_TICK:
                await conn.send(P.make_tick(run))
                run = 0
        if run:
            await conn.send(P.make_tick(run))

    task = asyncio.ensure_future(sender())
    try:
        while len(res.frames) < frames:
            msg = await conn.recv(timeout)
            if msg is None:
                break
            if msg["type"] != "frame":
                res.take(msg)
                continue
            k, digest, events = msg["frame_no"], msg["digest"], _events(msg)
            res.events.extend([k, a, d] for a, d in events or ())
            res.record(k, digest, k == len(res.frames) and k < len(expected)
                       and expected[k] == (digest, events))
            if on_frame:
                on_frame(msg, res)
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _replica(conn, res, frames, policy, on_frame, timeout):
    """Fold the log that the frames publish (E_k rides on frame k) and check
    every digest, under either clock. With a policy, play: act only once
    every event sent so far has come back in some frame's E_k."""
    s = core.new_game(res.seed)
    unacked = deque()           # events sent, not yet seen in an E_k
    while frames is None or len(res.frames) < frames:
        msg = await conn.recv(timeout)
        if msg is None:
            break
        if msg["type"] != "frame":
            res.take(msg)
            continue
        k, digest, events = msg["frame_no"], msg["digest"], _events(msg)
        ok = events is not None and k == len(res.frames)
        for e in events or ():
            if unacked and unacked[0] == e:
                unacked.popleft()
            else:
                ok = False      # not an event we sent, or out of order
        s = core.step(s, events or ())
        ok = ok and frame_digest(render(s)) == digest
        res.events.extend([k, a, d] for a, d in events or ())
        res.record(k, digest, ok)
        if on_frame:
            on_frame(msg, res)
        if policy and not unacked and (frames is None or len(res.frames) < frames):
            new = [(a, bool(d)) for a, d in policy(s, k + 1)]
            if new:
                unacked.extend(new)
                await conn.send(*(P.make_event(a, d) for a, d in new))


async def _watch(conn, res, frames, on_frame):
    """A viewer counts the frames (decode already checked each digest). It
    cannot fold: no message tells a viewer the session's seed."""
    while frames is None or len(res.frames) < frames:
        msg = await conn.recv()
        if msg is None:
            break
        if msg["type"] != "frame":
            res.take(msg)
            continue
        res.events.extend([msg["frame_no"], a, d] for a, d in _events(msg) or ())
        res.record(msg["frame_no"], msg["digest"], True)
        if on_frame:
            on_frame(msg, res)


async def run(url, role="controller", seed=None, frames=DEFAULT_FRAMES,
              policy=None, on_frame=None, timeout=TIMEOUT):
    """Play (or watch) one session; return its Result."""
    hello = {"client": "tetris_sim.client"}
    if role == "controller":
        hello["seed"] = seed
    conn = await connect(url, role, timeout=timeout, **hello)
    try:
        # §2: act on the role the server accepted (a gatekeeper may demote).
        role = conn.hello.get("client_role", role)
        res = Result(conn, role, frames)
        if role == "viewer":
            await _watch(conn, res, frames, on_frame)
        elif conn.hello.get("clock") == "lockstep":
            await _lockstep(conn, res, frames or DEFAULT_FRAMES, policy,
                            on_frame, timeout)
        elif getattr(policy, "lockstep_only", False):
            raise ClientError("usage", "replaying a trace needs a server "
                              "started with --clock lockstep")
        else:
            await _replica(conn, res, frames, policy, on_frame, timeout)
    finally:
        await conn.close()
    return res


# -------------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(prog="python -m tetris_sim.client",
                                description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url", nargs="?",
                   default=f"tcp://{P.DEFAULT_HOST}:{P.DEFAULT_PORT}",
                   help="tcp://HOST:PORT, ws://HOST:PORT[/PATH], unix:PATH or "
                   "ws+unix:PATH (default %(default)s)")
    p.add_argument("--role", choices=("controller", "viewer"),
                   default="controller")
    p.add_argument("--seed", type=int,
                   help="the session seed sent in the hello (default: the "
                   "server's --seed)")
    p.add_argument("--frames", type=int,
                   help=f"stop after this many frames (controller default "
                   f"{DEFAULT_FRAMES}; a viewer watches until the server closes)")
    p.add_argument("--bot", action="store_true",
                   help="play with the demo bot (acts every 3rd frame)")
    p.add_argument("--bot-fast", action="store_true",
                   help="play with the bot that acts every frame")
    p.add_argument("--trace", help="replay a conformance trace's seed and "
                   "events (lockstep server) and check its digests")
    p.add_argument("--digests", action="store_true",
                   help="print 'FRAME_NO DIGEST' for every frame")
    p.add_argument("--ansi", action="store_true",
                   help="draw the frames in the terminal")
    p.add_argument("--trace-out",
                   help="controller: write the session as a conformance trace")
    p.add_argument("--timeout", type=float, default=TIMEOUT,
                   help="seconds to wait for the handshake and for each "
                   "message (default %(default)s)")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    seed, frames, policy, trace = args.seed, args.frames, None, None
    if args.trace:
        with open(args.trace) as fh:
            trace = json.load(fh)
        seed, frames = trace["seed"], frames or trace["frames"]
        policy = TracePolicy(trace)
    elif args.bot or args.bot_fast:
        policy = bot_policy(fast=args.bot_fast)
    if args.role == "viewer" and policy is not None:
        parser.error("--bot and --trace need --role controller")
    if args.role == "controller" and frames is None:
        frames = DEFAULT_FRAMES
    try:
        parse_url(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    sink = AnsiSink() if args.ansi else None

    def on_frame(msg, res):
        if args.digests:
            print(f"{msg['frame_no']} {msg['digest']}", flush=True)
        if sink is not None:
            s = res.state
            sink.send(msg["rows"], s and {"score": s["score"], "level": s["level"],
                                          "lines": s["lines"],
                                          "phase": s.get("phase", "")})

    try:
        res = asyncio.run(run(args.url, args.role, seed, frames, policy,
                              on_frame, args.timeout))
    except ClientError as exc:
        print(f"tetris_sim.client: {exc}", file=sys.stderr)
        return {"bad_message": 1, "usage": 2}.get(exc.code, 3)
    except (OSError, TimeoutError) as exc:
        print(f"tetris_sim.client: {args.url}: {exc or 'timed out'}",
              file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130
    if trace is not None:
        n, every = trace["frames"], trace.get("digest_every", 1)
        res.trace_match = [d for k, d in res.frames
                           if k % every == 0 or k == n - 1] == trace["digests"]
    if args.trace_out and res.role == "controller" and res.frames:
        with open(args.trace_out, "w") as fh:
            json.dump(make_trace("remote-client", "recorded by tetris_sim.client",
                                 res.seed, len(res.frames), res.events), fh,
                      indent=1)
            fh.write("\n")
    print(json.dumps(res.summary(), sort_keys=True), flush=True)
    if res.ok:
        return 0
    return 3 if res.fatal else 1


if __name__ == "__main__":
    sys.exit(main())
