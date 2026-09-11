"""A local mock of the wal.sh/tools/display relay, spec v0.2.1.

The contract is the pin in contract/wal-sh-display-0.2.1/ (spec.md and
capabilities.json).  Presets, palettes and limits are read from it, and frames
are decoded by the contract's Python reference (contract/display_contract.py).
Each display has its own lease:

- viewer: {"op":"view"} -> caps (display, w, h, fps, format, 16-colour
  palette), lease, then every frame the holder sends, in caps.format;
- source: {"op":"reserve", name, display, ttl, format} -> granted or busy;
  frames are pal16 (binary, w*h bytes, optional 2-byte big-endian sequence),
  hex (text), rgb24 (binary w*h*3, when reserved so; quantized here) or a BLP
  or MCUF packet (binary); renew; release;
- one holder per display; ttl <= 900 s, counted from the last accepted frame
  or renew; on expiry every viewer gets lease holder null, then a black frame;
- dropped, each with {"op":"error"}: not-holder, bad-frame-length,
  bad-format, rate (faster than fps, or a sequence lower than the last
  accepted one), and unknown-op for an op the relay does not know;
- at most 32 viewers per display;
- optionally, BLP and MCUF packets over UDP on loopback.  Width x height picks
  the display, and the sender is a 5-second holder.

The choices this mock makes where the spec is silent are listed in README.md.
It serves capabilities.json (the pin, with local endpoints) and, with
--page, a sink page.  It binds to loopback only: nothing here ever talks to
wss://wal.sh.
"""
import asyncio
import collections
import contextlib
import copy
import http
import itertools
import json
import math
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from contract import display_contract as dc
from websockets.asyncio.server import broadcast, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Response

PATH = "/tools/display/ws"
CAPS_PATH = "/tools/display/capabilities.json"
PAGE_PATHS = ("/tools/display/", "/tools/display/index.html")
# This repo's default display (the user, 2026-09-11): the Green Building.
# The spec's default is cga40; --default changes the relay's.
REPO_DEFAULT = "green-building"
DEFAULT_DISPLAY = REPO_DEFAULT
DEFAULT_TTL = dc.DEFAULT_TTL
MAX_TTL = dc.MAX_TTL
MAX_VIEWERS = dc.MAX_VIEWERS
UDP_TTL = dc.UDP_TTL
MAX_MESSAGE = 1 << 18      # rgb24 at 256 x 256 with its prefix is 196,610 bytes
VIEWER_CAP_CLOSE = 1013    # WebSocket close code "try again later"
LOOPBACK = ("127.0.0.1", "::1", "localhost")

# d= name -> (w, h), every preset of the pinned capabilities.json in the
# order of the spec's Presets table
PROFILES = {n: (p["w"], p["h"]) for n, p in dc.presets().items()}


class Display:
    def __init__(self, name, w, h, fps, palette, fanout="pal16", extra=False):
        err = dc.check_grid(w, h)
        if err:
            raise ValueError(f"display {name}: {err} ({w}x{h})")
        if not (isinstance(fps, int) and 1 <= fps <= dc.MAX_FPS):
            raise ValueError(f"display {name}: fps {fps} outside 1..{dc.MAX_FPS}")
        if fanout not in dc.FANOUT_FORMATS:
            raise ValueError(f"display {name}: fan-out format {fanout!r}")
        self.name, self.w, self.h, self.fps = name, w, h, fps
        self.palette = palette
        self.palette16 = dc.palette16(dc.palette(palette))
        self.rgb16 = [dc.hex_rgb(c) for c in self.palette16]
        self.fanout, self.extra = fanout, extra
        self.viewers = {}            # _Conn -> None, in the order they joined
        self.holder = None           # a _Conn or a _UdpHolder
        self.holder_name = None
        self.lease = None
        self.ttl = 0
        self.fmt = "pal16"           # the format the holder reserved
        self.expires = None
        self.timer = None
        self.tat = -math.inf         # rate: when the next frame is due (monotonic)
        self.last_seq = None         # the last accepted sequence prefix

    def caps(self):
        return {"op": "caps", "display": self.name, "w": self.w, "h": self.h,
                "fps": self.fps, "format": self.fanout, "palette": list(self.palette16)}

    def lease_msg(self):
        return {"op": "lease", "display": self.name, "holder": self.holder_name,
                "expires": self.expires}

    def granted(self):
        return {"op": "granted", "lease": self.lease, "w": self.w, "h": self.h,
                "fps": self.fps, "format": self.fmt, "palette": list(self.palette16),
                "expires": self.expires}

    def out(self, cells):
        return dc.fanout_frame(cells, self.w, self.h, self.fanout)


class _Conn:
    def __init__(self, ws, cid):
        self.ws, self.id = ws, cid
        self.viewing = set()
        self.held = None


class _UdpHolder:
    def __init__(self, addr, cid):
        self.addr, self.id = addr, cid
        self.held = None


class _Udp(asyncio.DatagramProtocol):
    def __init__(self, relay):
        self.relay = relay

    def datagram_received(self, data, addr):
        self.relay._datagram(data, tuple(addr[:2]))


def parse_extra(text):
    """--extra-display NAME=WxH[,fanout=hex][,palette=cga][,fps=30] -> kwargs."""
    name, _, rest = text.partition("=")
    geometry, *opts = rest.split(",")
    w, _, h = geometry.partition("x")
    kw = {"name": name, "w": int(w), "h": int(h), "fps": 30, "palette": "cga",
          "fanout": "pal16"}
    for o in opts:
        k, _, v = o.partition("=")
        if k not in ("fanout", "palette", "fps"):
            raise ValueError(f"--extra-display option {k!r}")
        kw[k] = int(v) if k == "fps" else v
    return kw


class Relay:
    def __init__(self, fps=None, page=None, *, default=DEFAULT_DISPLAY, rate_tolerance=0.2,
                 seq_rule="literal", max_viewers=MAX_VIEWERS, udp_ttl=UDP_TTL,
                 extra=(), fanout=None, record=None):
        """FPS, if given, lowers every display's rate (never raises it).  EXTRA
        adds mock-only displays (dicts as parse_extra returns, or its strings).
        FANOUT is a format for every display, or {name: format} where "*"
        names every display not listed.  RECORD is a
        path: every message in and out is appended to it as JSONL, the shape
        contract/check_session.py reads.  RATE_TOLERANCE is the jitter
        allowance of the rate limit, as a fraction of a frame period."""
        if seq_rule not in dc.SEQ_RULES:
            raise ValueError(f"seq_rule {seq_rule!r}")
        per = dict(fanout) if isinstance(fanout, dict) else {}
        every = per.pop("*", fanout if isinstance(fanout, str) else "pal16")
        self.displays = {}
        for name, p in dc.presets().items():
            rate = p["fps"] if fps is None else min(p["fps"], fps)
            self.displays[name] = Display(name, p["w"], p["h"], rate, p["palette"],
                                          per.get(name, every))
        for kw in extra:
            kw = parse_extra(kw) if isinstance(kw, str) else dict(kw)
            name = kw.pop("name")
            if name in self.displays:
                raise ValueError(f"display {name} already exists")
            kw.setdefault("fanout", per.get(name, every))
            self.displays[name] = Display(name, extra=True, **kw)
        if default not in self.displays:
            raise ValueError(f"default display {default!r} is not a display")
        self.order = list(self.displays)   # presets in the spec's order, then extras
        self.default, self.page = default, page
        self.rate_tolerance, self.seq_rule = rate_tolerance, seq_rule
        self.max_viewers, self.udp_ttl = max_viewers, udp_ttl
        self.record = record
        self.stats = collections.Counter()
        self.url = self.udp_addr = None
        self._ids = itertools.count(1)
        self._rec_file = None
        self._udp_holders = {}

    # ----------------------------------------------------------- serving

    @contextlib.asynccontextmanager
    async def serve(self, host="127.0.0.1", port=0, udp_port=None):
        """Serve on loopback; yield the WebSocket URL.  With UDP_PORT (0 for any
        free port), also take BLP and MCUF packets on it; self.udp_addr is
        then its (host, port)."""
        if host not in LOOPBACK:
            raise ValueError(f"the mock relay binds to loopback only, not {host}")
        # line-buffered: each record is on disk as it happens, so a live reader
        # sees the session and a SIGTERM or SIGKILL loses at most one line
        self._rec_file = (open(self.record, "a", encoding="utf-8", buffering=1)
                          if self.record else None)
        transport = None
        try:
            async with serve(self.handler, host, port, process_request=self.process_request,
                             max_size=MAX_MESSAGE, close_timeout=2) as server:
                port = next(iter(server.sockets)).getsockname()[1]
                self.hostport = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
                self.url = f"ws://{self.hostport}{PATH}"
                if udp_port is not None:
                    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                        lambda: _Udp(self), local_addr=(host, udp_port))
                    self.udp_addr = tuple(transport.get_extra_info("sockname")[:2])
                # the session's first record: what this relay advertises, so a
                # checker knows its displays, default and choices
                self._rec("relay", "out", "meta", self.capabilities())
                yield self.url
        finally:
            if transport is not None:
                transport.close()
            for d in self.displays.values():
                if d.timer is not None:
                    d.timer.cancel()
            if self._rec_file is not None:
                self._rec_file.close()
                self._rec_file = None

    def capabilities(self):
        """The pinned capabilities.json as this relay advertises itself: local
        endpoints, its own default, its displays (with fan-out format and any
        fps cap), the UDP port if listening; every other field verbatim."""
        c = copy.deepcopy(dc.capabilities())
        url = self.url or f"ws://127.0.0.1{PATH}"
        http_base = url.replace("ws://", "http://", 1).rsplit("/", 1)[0]
        c["default"] = self.default
        c["endpoints"] = {
            "ws": url,
            "view": url + ' then {"op":"view","display":...}',
            "reserve": url + ' then {"op":"reserve","name":...,"display":...,"ttl":...}',
            "page": http_base + "/?d=<display>",
        }
        displays = {}
        for name in self.order:
            d = self.displays[name]
            entry = copy.deepcopy(dc.capabilities()["displays"].get(name)) or {
                "w": d.w, "h": d.h, "palette": d.palette, "kind": "mock",
                "levels": len(dc.palette(d.palette)),
                "note": "mock-only display of contrib/displays/demo/relay.py"}
            entry.update(fps=d.fps, format=d.fanout)
            displays[name] = entry
        c["displays"] = displays
        c["interop"]["udp_port"] = self.udp_addr[1] if self.udp_addr else None
        c["status"] = {"relay": "local mock (contrib/displays/demo/relay.py) on loopback; not wal.sh"}
        c["mock"] = {"spec_default": dc.SPEC_DEFAULT, "seq_rule": self.seq_rule,
                     "rate_tolerance": self.rate_tolerance, "max_viewers": self.max_viewers,
                     "udp_ttl": self.udp_ttl, "default_ttl": DEFAULT_TTL}
        return c

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

    # ----------------------------------------------------------- recording

    def _rec(self, cid, direction, kind, payload=None):
        if self._rec_file is None:
            return
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.hex()
        self._rec_file.write(json.dumps({"t": time.time(), "conn": cid, "dir": direction,
                                         "kind": kind, "payload": payload},
                                        separators=(",", ":")) + "\n")

    async def _send(self, conn, msg):
        text = json.dumps(msg)
        self._rec(conn.id, "out", "text", text)
        await conn.ws.send(text)

    def _fan(self, d, message):
        """Send MESSAGE (a frame, or JSON text) to every viewer of D."""
        if not d.viewers:
            return
        kind = "binary" if isinstance(message, bytes) else "text"
        for c in d.viewers:
            self._rec(c.id, "out", kind, message)
        broadcast([c.ws for c in d.viewers], message)

    async def _error(self, conn, reason):
        self.stats["error:" + reason] += 1
        await self._send(conn, {"op": "error", "reason": reason})

    # ----------------------------------------------------------- WebSocket

    async def handler(self, ws):
        conn = _Conn(ws, f"c{next(self._ids)}")
        self._rec(conn.id, "in", "open", {"path": ws.request.path})
        try:
            query = parse_qs(urlsplit(ws.request.path).query)
            if "view" in query:
                await self._view(conn, query["view"][0])
            async for message in ws:
                if isinstance(message, bytes):
                    self._rec(conn.id, "in", "binary", message)
                    await self._frame(conn, message)
                else:
                    self._rec(conn.id, "in", "text", message)
                    if message.startswith("{"):
                        await self._control(conn, message)
                    else:
                        await self._frame(conn, message)
        except ConnectionClosed:
            pass
        finally:
            for d in conn.viewing:
                d.viewers.pop(conn, None)
            if conn.held is not None:
                self._end(conn.held, expired=False)
            self._rec(conn.id, "in", "close", {"code": ws.close_code})

    async def _control(self, conn, text):
        try:
            m = json.loads(text)
        except ValueError:
            return await self._error(conn, "bad-format")
        if not isinstance(m, dict) or not isinstance(m.get("op"), str):
            return await self._error(conn, "bad-format")
        if m["op"] not in dc.TO_RELAY:
            return await self._error(conn, "unknown-op")
        if dc.check_message(m):
            return await self._error(conn, "bad-format")
        op = m["op"]
        if op == "view":
            await self._view(conn, m.get("display"))
        elif op == "reserve":
            await self._reserve(conn, m)
        elif conn.held is None:
            await self._error(conn, "not-holder")
        elif op == "renew":
            self._touch(conn.held)
        else:
            self._end(conn.held, expired=False)

    async def _view(self, conn, name):
        d = self.displays.get(name or self.default)
        if d is None:
            return await self._error(conn, "bad-format")
        if conn not in d.viewers and len(d.viewers) >= self.max_viewers:
            self.stats["viewer-cap"] += 1
            self._rec(conn.id, "out", "close", {"code": VIEWER_CAP_CLOSE})
            await conn.ws.close(VIEWER_CAP_CLOSE, f"viewer cap {self.max_viewers}")
            return
        d.viewers[conn] = None
        conn.viewing.add(d)
        await self._send(conn, d.caps())
        await self._send(conn, d.lease_msg())

    async def _reserve(self, conn, m):
        d = self.displays.get(m.get("display") or self.default)
        if d is None:
            return await self._error(conn, "bad-format")
        if d.holder is not None and d.holder is not conn:
            return await self._send(conn, {"op": "busy", "holder": d.holder_name,
                                           "expires": d.expires})
        if conn.held is not None and conn.held is not d:
            self._end(conn.held, expired=False)
        self._grant(d, conn, m["name"], min(int(m.get("ttl", DEFAULT_TTL)), MAX_TTL),
                    m.get("format", "pal16"))
        await self._send(conn, d.granted())
        self._fan(d, json.dumps(d.lease_msg()))

    def _grant(self, d, holder, name, ttl, fmt):
        d.holder, d.holder_name, holder.held = holder, name, d
        d.ttl, d.fmt, d.lease = ttl, fmt, secrets.token_hex(8)
        d.tat, d.last_seq = -math.inf, None
        self.stats["granted"] += 1
        self._touch(d, announce=False)

    async def _frame(self, conn, message):
        d = conn.held
        if d is None:
            return await self._error(conn, "not-holder")
        try:
            cells, seq = self._decode(d, message)
        except dc.FrameError as e:
            return await self._error(conn, e.reason)
        if not self._admit(d, seq):
            return await self._error(conn, "rate")
        self._accept(d, cells)

    def _decode(self, d, message):
        return dc.decode_source_frame(message, d.w, d.h, d.fmt, d.rgb16)

    def _admit(self, d, seq):
        """The rate rule, as a GCRA: a frame is dropped if its sequence is lower
        than the last accepted one, or if it arrives more than rate_tolerance
        of a period before it is due; each accepted frame makes the next one
        due a full period (1/fps) later.  So a source may jitter, but never
        exceed fps over time, and two frames are never closer than
        (1 - rate_tolerance)/fps."""
        if not dc.seq_accepts(d.last_seq, seq, self.seq_rule):
            self.stats["drop:seq"] += 1
            return False
        now, period = time.monotonic(), 1.0 / d.fps
        if now < d.tat - self.rate_tolerance * period:
            self.stats["drop:fps"] += 1
            return False
        d.tat = max(d.tat, now) + period
        if seq is not None:
            d.last_seq = seq
        return True

    def _accept(self, d, cells):
        self._touch(d)
        self.stats["frames"] += 1
        self._fan(d, d.out(cells))

    # ----------------------------------------------------------- leases

    def _touch(self, d, announce=True):
        """Renew D's lease: it now expires ttl seconds from now.  Viewers are
        sent a fresh lease whenever the whole-second expires changes, so a
        viewer's tick never marks a live display idle."""
        expires = math.ceil(time.time() + d.ttl)
        changed, d.expires = expires != d.expires, expires
        if d.timer is not None:
            d.timer.cancel()
        d.timer = asyncio.get_running_loop().call_later(d.ttl, self._expire, d, d.lease)
        if announce and changed:
            self._fan(d, json.dumps(d.lease_msg()))

    def _expire(self, d, lease):
        if d.lease == lease:
            self._end(d, expired=True)

    def _end(self, d, expired):
        if d.timer is not None:
            d.timer.cancel()
            d.timer = None
        if d.holder is not None:
            d.holder.held = None
            if isinstance(d.holder, _UdpHolder):
                self._udp_holders.pop((d.holder.addr, d.name), None)
        d.holder = d.holder_name = d.lease = d.expires = None
        d.tat, d.last_seq = -math.inf, None
        self.stats["expired" if expired else "released"] += 1
        self._fan(d, json.dumps(d.lease_msg()))
        if expired:
            self._fan(d, d.out(bytes(d.w * d.h)))

    # ----------------------------------------------------------- UDP interop

    def display_for(self, w, h):
        """The display a w x h packet goes to: the first with that geometry in
        the spec's Presets table order (then the mock's extras).  So 10x20 is
        tetris, not c64; 10x18 is dc32, not gameboy; 9x17 is green-building,
        not remote."""
        return next((self.displays[n] for n in self.order
                     if (self.displays[n].w, self.displays[n].h) == (w, h)), None)

    def _datagram(self, data, addr):
        cid = f"udp:{addr[0]}:{addr[1]}"
        self._rec(cid, "in", "udp", data)
        try:
            p = dc.parse_interop(data)
        except dc.FrameError as e:
            self.stats["udp:" + e.reason] += 1
            return
        d = self.display_for(p["w"], p["h"])
        if d is None:
            self.stats["udp:no-display"] += 1
            return
        if d.holder is None:
            holder = self._udp_holders[(addr, d.name)] = _UdpHolder(addr, cid)
            self._grant(d, holder, cid, self.udp_ttl, "pal16")
            self._fan(d, json.dumps(d.lease_msg()))
        elif not (isinstance(d.holder, _UdpHolder) and d.holder.addr == addr):
            self.stats["udp:busy"] += 1
            return
        if not self._admit(d, None):
            self.stats["udp:rate"] += 1
            return
        self._accept(d, dc.interop_cells(p, d.rgb16))


def _response(status, ctype, body):
    headers = Headers([("Content-Type", ctype), ("Content-Length", str(len(body)))])
    return Response(status, http.HTTPStatus(status).phrase, headers, body)
