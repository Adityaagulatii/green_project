#!/usr/bin/env python3
"""Reference outer gatekeeper, binding bridge and mutant server (PROTOCOL §9.3, §10).

    proxy.py --listen tcp://127.0.0.1:1800 --upstream tcp://127.0.0.1:1709
    proxy.py --listen ws://127.0.0.1:1710/tetris-17x9 --upstream tcp://127.0.0.1:1709
    proxy.py --listen tcp://127.0.0.1:{port} --upstream-launch "SERVER CMD {upstream_port}"
    proxy.py ... --record session.jsonl      # log every message, for check.py --session
    proxy.py ... --demote                    # admit controllers as viewers
    proxy.py ... --mutate NAME               # a deliberately wrong server (see MUTANTS)

Each downstream connection gets its own upstream connection, and whole
messages are forwarded both ways, in order. With no --mutate and no --demote
the proxy is a *transparent gatekeeper*: PROTOCOL §9.3 requires it to pass
the transcripts exactly as the server does alone, and the gate checks that
over TCP and over a WebSocket front (which also exercises the WebSocket
binding against a TCP server). When the upstream ends a connection after a
fatal error, a WebSocket downstream gets the close code of PROTOCOL §5.3.

--mutate turns it into a mutant server for verifying the verifier: each
mutant corrupts the server-to-client stream in one way that check.py MUST
reject. Standard library, plus the websockets package for ws:// URLs.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

SUBPROTOCOL = "tetris-17x9.v1"
WS_PATH = "/tetris-17x9"
FATAL = {"too_large", "hello_required", "version", "role", "bad_hello", "busy",
         "too_many_errors"}
WS_CLOSE = {"too_large": 1009, "busy": 1013}
LINE_LIMIT = 1 << 20


def _digest(rows):
    return hashlib.sha256(bytes(v for r in rows for c in r for v in c)).hexdigest()


class Mutant:
    """One deliberate corruption of the server-to-client stream. ``apply``
    returns the list of messages to forward in place of ``msg``."""

    def __init__(self, name):
        if name not in MUTANTS:
            raise SystemExit(f"unknown mutant {name!r}; one of {', '.join(MUTANTS)}")
        self.name = name
        self.states = 0
        self.held = None

    def apply(self, msg):
        return MUTANTS[self.name](self, msg)


def _digest_flip(m, msg):
    """A frame whose digest does not match its rows (schema gate)."""
    if msg.get("type") == "frame" and msg.get("frame_no") == 100 and "digest" in msg:
        d = msg["digest"]
        msg = dict(msg, digest=("0" if d[0] != "0" else "1") + d[1:])
    return [msg]


def _repaint(m, msg):
    """A self-consistent but wrong frame: one cell changed, digest recomputed
    (only the oracle can see it, as with a wrong engine)."""
    if msg.get("type") == "frame" and msg.get("frame_no") == 100:
        rows = [list(r) for r in msg["rows"]]
        c = rows[16][8]
        rows[16][8] = [255 - c[0], c[1], c[2]]
        msg = dict(msg, rows=rows, digest=_digest(rows))
    return [msg]


def _phase_alias(m, msg):
    """A phase that is not SPEC's name for it (schema gate)."""
    if msg.get("type") == "state" and msg.get("phase") == "playing":
        msg = dict(msg, phase="play")
    return [msg]


def _illegal_edge(m, msg):
    """Frame 0 reported as gameover: the later gameover -> playing edge is
    illegal (state gate, SPEC Table 9.1)."""
    if msg.get("type") == "state" and msg.get("frame_no") == 0:
        msg = dict(msg, phase="gameover")
    return [msg]


def _drop_state(m, msg):
    """The second state message never arrives (oracle)."""
    if msg.get("type") == "state":
        m.states += 1
        if m.states == 2:
            return []
    return [msg]


def _stamp_shift(m, msg):
    """Events reported one frame late: the log a replica would fold is not
    the log the server folded (oracle)."""
    if msg.get("type") != "frame" or "events" not in msg:
        return [msg]
    carried, m.held = m.held, None
    if msg["events"] and carried is None:
        m.held = msg["events"]
        return [dict(msg, events=[])]
    return [dict(msg, events=carried)] if carried is not None else [msg]


def _no_events(m, msg):
    """A v0-style frame without events (schema gate)."""
    if msg.get("type") == "frame":
        msg = {k: v for k, v in msg.items() if k != "events"}
    return [msg]


MUTANTS = {"digest-flip": _digest_flip, "repaint": _repaint,
           "phase-alias": _phase_alias, "illegal-edge": _illegal_edge,
           "drop-state": _drop_state, "stamp-shift": _stamp_shift,
           "no-events": _no_events}


# ------------------------------------------------------------ endpoints

class TcpSide:
    def __init__(self, reader, writer):
        self.reader, self.writer = reader, writer

    @classmethod
    async def connect(cls, url):
        u = urlsplit(url)
        r, w = await asyncio.open_connection(u.hostname, u.port, limit=LINE_LIMIT)
        return cls(r, w)

    async def recv(self):
        """Text of the next message, or None at the end."""
        try:
            line = await self.reader.readline()
        except (ConnectionError, ValueError):
            return None
        if not line.endswith(b"\n"):
            return None
        return line.rstrip(b"\n").rstrip(b"\r").decode("utf-8", errors="surrogateescape")

    async def send(self, text):
        self.writer.write(text.encode("utf-8", errors="surrogateescape") + b"\n")
        await self.writer.drain()

    async def close(self, code=None, reason=""):
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, RuntimeError):
            pass


class WsSide:
    def __init__(self, ws):
        self.ws = ws

    @classmethod
    async def connect(cls, url):
        from websockets.asyncio.client import connect
        ws = await connect(url, subprotocols=[SUBPROTOCOL], max_size=LINE_LIMIT,
                           compression=None, proxy=None)
        return cls(ws)

    async def recv(self):
        from websockets.exceptions import ConnectionClosed
        try:
            data = await self.ws.recv()
        except ConnectionClosed:
            return None
        # A binary message is malformed (PROTOCOL §5.3): forward a JSON value
        # that is not an object, which every server answers with `malformed`.
        return '"binary message"' if isinstance(data, bytes) else data

    async def send(self, text):
        await self.ws.send(text)

    async def close(self, code=None, reason=""):
        try:
            await self.ws.close(code or 1000, reason)
        except Exception:  # noqa: BLE001 - already gone
            pass


# ---------------------------------------------------------------- proxy

class Proxy:
    def __init__(self, upstream, mutate=None, demote=False, record=None):
        self.upstream = upstream
        self.mutate = mutate
        self.demote = demote
        self.record = open(record, "a", encoding="utf-8") if record else None
        self.next_id = 0

    def log(self, **rec):
        if self.record:
            rec["t"] = round(time.monotonic(), 4)
            self.record.write(json.dumps(rec) + "\n")
            self.record.flush()

    async def serve_conn(self, down):
        self.next_id += 1
        cid = self.next_id
        connector = WsSide if urlsplit(self.upstream).scheme == "ws" else TcpSide
        try:
            up = await connector.connect(self.upstream)
        except OSError:
            await down.close(1011, "upstream unavailable")
            return
        mutant = Mutant(self.mutate) if self.mutate else None
        last = {"code": None}

        async def c2s():
            first = True
            while True:
                text = await down.recv()
                if text is None:
                    self.log(conn=cid, dir="end", by="client")
                    await up.close()
                    return
                if first and self.demote:
                    try:
                        msg = json.loads(text)
                        if msg.get("type") == "hello" and msg.get("role") == "controller":
                            text = json.dumps(dict(msg, role="viewer"), separators=(",", ":"))
                    except ValueError:
                        pass
                first = False
                self.log(conn=cid, dir="c2s", text=text)
                try:
                    await up.send(text)
                except Exception:  # noqa: BLE001 - upstream gone
                    return

        async def s2c():
            while True:
                text = await up.recv()
                if text is None:
                    self.log(conn=cid, dir="end", by="server")
                    code = last["code"]
                    await down.close(WS_CLOSE.get(code, 1008) if code in FATAL else 1000,
                                     code if code in FATAL else "")
                    return
                out = [text]
                try:
                    msg = json.loads(text)
                except ValueError:
                    msg = None
                if isinstance(msg, dict):
                    if msg.get("type") == "error":
                        last["code"] = msg.get("code")
                    if mutant:
                        out = [json.dumps(m, separators=(",", ":")) for m in mutant.apply(msg)]
                for t in out:
                    self.log(conn=cid, dir="s2c", text=t)
                    try:
                        await down.send(t)
                    except Exception:  # noqa: BLE001 - downstream gone
                        await up.close()
                        return

        tasks = [asyncio.ensure_future(c2s()), asyncio.ensure_future(s2c())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        # Let the other direction finish forwarding what is in flight.
        await asyncio.wait(tasks, timeout=5)
        for t in tasks:
            t.cancel()
        await up.close()
        await down.close()

    async def listen(self, url):
        u = urlsplit(url)
        if u.scheme == "tcp":
            async def on_tcp(r, w):
                await self.serve_conn(TcpSide(r, w))
            server = await asyncio.start_server(on_tcp, u.hostname, u.port, limit=LINE_LIMIT)
            addr = server.sockets[0].getsockname()[:2]
        elif u.scheme == "ws":
            from websockets.asyncio.server import serve

            def check_request(conn, request):
                if request.path != WS_PATH:
                    return conn.respond(404, f"the contract path is {WS_PATH}\n")
                offered = ",".join(request.headers.get_all("Sec-WebSocket-Protocol"))
                if SUBPROTOCOL not in [p.strip() for p in offered.split(",")]:
                    return conn.respond(400, f"subprotocol {SUBPROTOCOL} required\n")
                return None

            async def on_ws(ws):
                await self.serve_conn(WsSide(ws))
            server = await serve(on_ws, u.hostname, u.port, subprotocols=[SUBPROTOCOL],
                                 process_request=check_request, max_size=LINE_LIMIT,
                                 max_queue=16, compression=None)
            addr = next(iter(server.sockets)).getsockname()[:2]
        else:
            raise SystemExit(f"unsupported --listen scheme {u.scheme!r}")
        print(f"proxy.py: listening on {u.scheme}://{addr[0]}:{addr[1]}"
              f"{u.path if u.scheme == 'ws' else ''} -> {self.upstream}"
              f"{' mutant ' + self.mutate if self.mutate else ''}"
              f"{' demoting' if self.demote else ''}", file=sys.stderr, flush=True)
        return server


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(host, port, proc, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


async def amain(args):
    upstream, proc = args.upstream, None
    if args.upstream_launch:
        port = _free_port()
        upstream = (args.upstream or "tcp://127.0.0.1:{upstream_port}").replace(
            "{upstream_port}", str(port))
        proc = subprocess.Popen(shlex.split(args.upstream_launch.replace(
            "{upstream_port}", str(port))), stdout=sys.stderr, stderr=sys.stderr)
        u = urlsplit(upstream)
        if not _wait_port(u.hostname, u.port, proc):
            proc.kill()
            raise SystemExit("proxy.py: the upstream server did not start")
    proxy = Proxy(upstream, args.mutate, args.demote, args.record)
    server = await proxy.listen(args.listen)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await stop.wait()
    finally:
        server.close()
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", required=True)
    ap.add_argument("--upstream", help="tcp:// or ws:// URL of the server")
    ap.add_argument("--upstream-launch", help="start this server command; "
                    "{upstream_port} in it (and in --upstream) is a free port")
    ap.add_argument("--mutate", choices=sorted(MUTANTS))
    ap.add_argument("--demote", action="store_true")
    ap.add_argument("--record")
    args = ap.parse_args(argv)
    if not (args.upstream or args.upstream_launch):
        ap.error("--upstream or --upstream-launch is required")
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
