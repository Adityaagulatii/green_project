"""A local mock of the wal.sh/tools/display relay, to test the protocol.

It implements the user's protocol text (2026-09-11) for every display in
PROFILES, each with its own lease:

- viewer: {"op":"view"} -> caps, lease, then every frame the holder sends;
- source: {"op":"reserve"} -> granted or busy; binary frames of w*h*3 bytes,
  with an optional 2-byte big-endian sequence that is stripped; renew; release;
- one holder per display; ttl <= 900 s, counted from the last frame or renew;
  on expiry every viewer gets {"op":"lease","holder":null} and a black frame;
- frames from a non-holder, of the wrong length, or faster than fps are
  dropped, each with {"op":"error"}.

Mock choices the protocol leaves open: `?view=NAME` on the WebSocket URL
subscribes on connect (so display.html's ?src= can point here); a frame
counts as too fast when it arrives within 0.8/fps of the last accepted one;
unknown ops and displays get errors of their own.  It serves
capabilities.json and, with --page, a sink page.  It binds to loopback only:
nothing here ever talks to wss://wal.sh.
"""
import asyncio
import collections
import contextlib
import http
import json
import math
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import broadcast, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

from .producers import CGA

PATH = "/tools/display/ws"
CAPS_PATH = "/tools/display/capabilities.json"
PAGE_PATHS = ("/tools/display/", "/tools/display/index.html")
DEFAULT_DISPLAY = "green-building"
DEFAULT_TTL = 300
MAX_TTL = 900
LOOPBACK = ("127.0.0.1", "::1", "localhost")

# d= name -> (w, h), from the user's display-profiles table (2026-09-11)
PROFILES = {
    "tetris": (10, 20), "green-building": (9, 17), "dc32": (10, 18),
    "gameboy": (10, 18), "trs80": (10, 12), "c64": (10, 20),
    "ws2812": (16, 16), "hub75": (64, 32), "remote": (9, 17),
}


class Display:
    def __init__(self, name, w, h, fps):
        self.name, self.w, self.h, self.fps = name, w, h, fps
        self.viewers = set()
        self.holder = None           # the holder's _Conn
        self.holder_name = None
        self.lease = None
        self.ttl = 0
        self.expires = None
        self.timer = None
        self.last_frame = -math.inf  # monotonic time of the last accepted frame

    def caps(self):
        return {"op": "caps", "display": self.name, "w": self.w, "h": self.h, "fps": self.fps}

    def lease_msg(self):
        return {"op": "lease", "display": self.name, "holder": self.holder_name,
                "expires": self.expires}


class _Conn:
    def __init__(self, ws):
        self.ws = ws
        self.viewing = set()
        self.held = None


class Relay:
    def __init__(self, fps=30, profiles=PROFILES, page=None, rate_slack=0.8):
        self.displays = {n: Display(n, w, h, fps) for n, (w, h) in profiles.items()}
        self.page = page
        self.rate_slack = rate_slack
        self.stats = collections.Counter()

    def capabilities(self):
        return {
            "mock": "local mock of wal.sh/tools/display (contrib/displays/demo)",
            "default": DEFAULT_DISPLAY,
            "ws": PATH,
            "frame": {"length": "w*h*3", "format": "RGB, row-major, row 0 at the top",
                      "sequence": "optional 2-byte big-endian prefix"},
            "max_ttl": MAX_TTL,
            "palette": ["#{:02x}{:02x}{:02x}".format(*c) for c in CGA],
            "displays": {d.name: {"w": d.w, "h": d.h, "fps": d.fps}
                         for d in self.displays.values()},
        }

    @contextlib.asynccontextmanager
    async def serve(self, host="127.0.0.1", port=0):
        """Serve on loopback; yield the WebSocket URL."""
        if host not in LOOPBACK:
            raise ValueError(f"the mock relay binds to loopback only, not {host}")
        async with serve(self.handler, host, port,
                         process_request=self.process_request) as server:
            port = next(iter(server.sockets)).getsockname()[1]
            yield f"ws://{host}:{port}{PATH}"

    def process_request(self, connection, request):
        path = urlsplit(request.path).path
        if path == CAPS_PATH:
            body = json.dumps(self.capabilities(), indent=2).encode()
            return _response(200, "application/json", body)
        if path in PAGE_PATHS and self.page:
            return _response(200, "text/html; charset=utf-8", self.page.read_bytes())
        if path != PATH:
            return _response(404, "text/plain", b"not found\n")
        return None

    async def handler(self, ws):
        conn = _Conn(ws)
        try:
            query = parse_qs(urlsplit(ws.request.path).query)
            if "view" in query:
                await self._view(conn, query["view"][0])
            async for message in ws:
                if isinstance(message, bytes):
                    await self._frame(conn, message)
                else:
                    await self._control(conn, message)
        except ConnectionClosed:
            pass
        finally:
            for d in conn.viewing:
                d.viewers.discard(ws)
            if conn.held is not None:
                self._end(conn.held, expired=False)

    async def _send(self, conn, msg):
        await conn.ws.send(json.dumps(msg))

    async def _error(self, conn, reason):
        self.stats["error:" + reason] += 1
        await self._send(conn, {"op": "error", "reason": reason})

    async def _view(self, conn, name):
        d = self.displays.get(name or DEFAULT_DISPLAY)
        if d is None:
            return await self._error(conn, "no such display")
        d.viewers.add(conn.ws)
        conn.viewing.add(d)
        await self._send(conn, d.caps())
        await self._send(conn, d.lease_msg())

    async def _control(self, conn, text):
        try:
            m = json.loads(text)
            op = m["op"]
        except (ValueError, TypeError, KeyError):
            return await self._error(conn, "bad message")
        if op == "view":
            await self._view(conn, m.get("display"))
        elif op == "reserve":
            await self._reserve(conn, m)
        elif op == "renew":
            if conn.held is None:
                return await self._error(conn, "not holder")
            self._touch(conn.held)
        elif op == "release":
            if conn.held is not None:
                self._end(conn.held, expired=False)
        else:
            await self._error(conn, "bad op")

    async def _reserve(self, conn, m):
        d = self.displays.get(m.get("display") or DEFAULT_DISPLAY)
        if d is None:
            return await self._error(conn, "no such display")
        if d.holder is not None and d.holder is not conn:
            return await self._send(conn, {"op": "busy", "holder": d.holder_name,
                                           "expires": d.expires})
        try:
            ttl = int(m.get("ttl", DEFAULT_TTL))
        except (TypeError, ValueError):
            return await self._error(conn, "bad ttl")
        if conn.held is not None and conn.held is not d:
            self._end(conn.held, expired=False)
        d.holder, d.holder_name, conn.held = conn, str(m.get("name") or "anonymous"), d
        d.ttl = max(1, min(ttl, MAX_TTL))
        d.lease = secrets.token_hex(8)
        d.last_frame = -math.inf
        self._touch(d)
        await self._send(conn, {"op": "granted", "lease": d.lease, "w": d.w, "h": d.h,
                                "fps": d.fps, "expires": d.expires})
        broadcast(d.viewers, json.dumps(d.lease_msg()))

    async def _frame(self, conn, data):
        d = conn.held
        if d is None:
            return await self._error(conn, "not holder")
        n = d.w * d.h * 3
        if len(data) == n + 2:
            data = data[2:]
        elif len(data) != n:
            return await self._error(conn, "bad frame length")
        now = time.monotonic()
        if now - d.last_frame < self.rate_slack / d.fps:
            return await self._error(conn, "rate")
        d.last_frame = now
        self._touch(d)
        self.stats["frames"] += 1
        broadcast(d.viewers, data)

    def _touch(self, d):
        """Renew D's lease: it now expires ttl seconds from now."""
        d.expires = math.ceil(time.time() + d.ttl)
        if d.timer is not None:
            d.timer.cancel()
        d.timer = asyncio.get_running_loop().call_later(d.ttl, self._expire, d, d.lease)

    def _expire(self, d, lease):
        if d.lease == lease:
            self._end(d, expired=True)

    def _end(self, d, expired):
        if d.timer is not None:
            d.timer.cancel()
            d.timer = None
        if d.holder is not None:
            d.holder.held = None
        d.holder = d.holder_name = d.lease = d.expires = None
        self.stats["expired" if expired else "released"] += 1
        broadcast(d.viewers, json.dumps(d.lease_msg()))
        if expired:
            broadcast(d.viewers, bytes(d.w * d.h * 3))


def _response(status, ctype, body):
    headers = Headers([("Content-Type", ctype), ("Content-Length", str(len(body)))])
    return Response(status, http.HTTPStatus(status).phrase, headers, body)
