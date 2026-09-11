"""python -m demo feed: the game-to-display bridge (docs/PROTOCOL.md section 5.4).

On one side it is a contract-v1 game *viewer*: tcp:// JSON lines, or ws:// on
/tetris-17x9 with the subprotocol tetris-17x9.v1.  On the other it is a
wal.sh/tools/display *source*.  The two protocols never share a connection.

The feed reserves the display, with a dlk1 lease key when the relay wants
one, reads `granted` (w, h, fps, format, palette), and honours those
capabilities, as the client must (dlk1 proposal: "Capabilities are the
client's job"):

- **geometry:** the SPEC 17x9 frame goes onto w x h by the loss policy of the
  cljc adapter's default, :letterbox.  That is the largest integer scale k,
  at least 1, centred with unlit bars; a grid too short for the field crops
  the top rows, so the stack and the floor stay.  `placement` and `axis`
  mirror tetris.displays.adapt/placement and adapt/axis (contrib/displays/src,
  branch contrib/displays-cljc, d0cf331);
- **colour:** the adapter's default rule, :lit :keep, mirrors
  tetris.displays.adapt/color-index.  Black goes to 0 and any other colour to
  the nearest of indices 1..15 (ties to the lower), so a lit SPEC cell never
  becomes index 0;
- **pace:** at most fps frames a second.  When the game outruns the display,
  the latest frame wins: nothing is queued, and skipped frames are counted;
- **format:** the one reserved, which a key must allow (checked here from the
  key's own claims before the relay is asked).

When the game goes away the feed sends `release` and returns.  That is: the
connection closes; a transport error; or, after IDLE seconds of silence, a
contract `ping` that gets no reply.  A paused game that answers the ping
keeps the feed, and the feed renews the display lease meanwhile.  The result
is op "done" (or "incomplete" if FRAMES were asked for and fewer came, or
"lost" if the relay ended the lease), with `game_end` saying why.

demo/test_feed.py holds these to the cljc adapter's committed output
(demo/fixtures/adapt-cljc.json).
"""
import asyncio
import json
import math
import time
from urllib.parse import urlsplit

from contract import display_contract as dc
from contract import dlk1
from websockets.asyncio.client import connect

from .source import LOOPBACK, check_url

SPEC_W, SPEC_H = 9, 17
BLACK = (0, 0, 0)
PROTOCOL, VERSION, SUBPROTOCOL = "17x9-tetris-remote", 1, "tetris-17x9.v1"
MAX_LINE = 1 << 16


# ------------------------------------------------------------------ adapt (mirrors adapt.cljc)

def axis(mode, d, n, k):
    """adapt/axis for :center and :end: for each of D device cells, the source
    cell it shows (None = padding), N source cells at integer scale K."""
    shown = n * k
    off = d - shown if mode == "end" else (d - shown) // 2   # adapt's floor-half
    return [(x - off) // k if 0 <= x - off < shown else None for x in range(d)]


def placement(w, h):
    """adapt/placement :letterbox -> (xs, ys, k)."""
    k = max(1, min(w // SPEC_W, h // SPEC_H))
    return (axis("center", w, SPEC_W, k),
            axis("center" if SPEC_H * k <= h else "end", h, SPEC_H, k), k)


def color_index(pal_rgb, rgb):
    """adapt/color-index with :lit :keep: black -> 0, else the nearest of 1..15."""
    return 0 if tuple(rgb) == BLACK else 1 + dc.quantize(tuple(rgb), pal_rgb[1:])


class Adapter:
    """SPEC frames (17 rows of 9 [r, g, b]) -> the w*h indices of a display
    that announced PALETTE (16 hex colours)."""

    def __init__(self, w, h, palette):
        self.w, self.h = w, h
        self.pal = [dc.hex_rgb(c) for c in palette]
        self.xs, self.ys, self.k = placement(w, h)
        self.memo = {}

    def cells(self, rows):
        out, i = bytearray(self.w * self.h), 0
        for y in self.ys:
            for x in self.xs:
                if x is not None and y is not None:
                    rgb = tuple(rows[y][x])
                    v = self.memo.get(rgb)
                    if v is None:
                        v = self.memo[rgb] = color_index(self.pal, rgb)
                    out[i] = v
                i += 1
        return bytes(out)


def valid_rows(rows):
    """PROTOCOL section 3's frame validation: 17 rows of 9 cells of 3 integers 0..255."""
    return (isinstance(rows, list) and len(rows) == SPEC_H and all(
        isinstance(r, list) and len(r) == SPEC_W and all(
            isinstance(c, list) and len(c) == 3 and all(
                isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in c)
            for c in r) for r in rows))


def encode(fmt, cells, w, h, palette, seq=None):
    if fmt == "hex":
        return dc.encode_hex(cells, w, h)
    if fmt == "rgb24":
        return dc.encode_rgb24(cells, palette, seq)
    return dc.encode_pal16(cells, seq)


# ------------------------------------------------------------------ the game side

def check_game_url(url):
    u = urlsplit(url)
    if u.scheme not in ("tcp", "ws") or u.hostname not in LOOPBACK or not u.port:
        raise SystemExit(f"refusing {url}: the feed views a game server on loopback only "
                         "(tcp://127.0.0.1:1709 or ws://127.0.0.1:1710/tetris-17x9)")
    return u


class GameViewer:
    """A contract-v1 viewer: hello, then the frames and states of the session."""

    def __init__(self, url):
        self.u = check_game_url(url)
        self.url, self.ws, self.reader, self.writer = url, None, None, None

    async def open(self, client="contrib/displays demo feed"):
        if self.u.scheme == "tcp":
            self.reader, self.writer = await asyncio.open_connection(
                self.u.hostname, self.u.port, limit=MAX_LINE + 1)
        else:
            self.ws = await connect(self.url, subprotocols=[SUBPROTOCOL])
            if self.ws.subprotocol != SUBPROTOCOL:
                raise ConnectionError(f"the game server did not select {SUBPROTOCOL}")
        await self.send({"type": "hello", "protocol": PROTOCOL, "version": VERSION,
                         "role": "viewer", "client": client})
        hello = await self.recv()
        if hello is None or hello.get("type") != "hello" or hello.get("client_role") != "viewer":
            raise ConnectionError(f"the game server refused the viewer: {hello}")
        self.hello = hello
        return self

    async def send(self, msg):
        text = json.dumps(msg)
        if self.ws is not None:
            await self.ws.send(text)
        else:
            self.writer.write(text.encode() + b"\n")
            await self.writer.drain()

    async def recv(self):
        """The next message (a dict), or None when the server closed."""
        while True:
            if self.ws is not None:
                try:
                    text = await self.ws.recv()
                except Exception:
                    return None
                if isinstance(text, bytes):
                    continue
            else:
                line = await self.reader.readline()
                if not line:
                    return None
                text = line.decode("utf-8", "replace")
            if text.strip():
                try:
                    msg = json.loads(text)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    return msg

    async def close(self):
        if self.ws is not None:
            await self.ws.close()
        elif self.writer is not None:
            self.writer.close()


# ------------------------------------------------------------------ the feed

async def run(game_url, relay_url, display, *, key=None, fmt="pal16", name="feed@jail",
              ttl=60, frames=None, seq=False, idle=5.0):
    """Feed the game at GAME_URL to DISPLAY on the relay at RELAY_URL until the
    game ends, FRAMES frames have arrived, or the lease is lost.  Returns a
    summary; {"op": "refused", ...} if the relay did not grant the display."""
    check_url(relay_url)
    check_game_url(game_url)
    if key is not None:
        claims = dlk1.peek(key)
        if claims and fmt not in claims.get("fmt", []):
            return {"op": "refused", "reason": f"the key allows {claims.get('fmt')}, not {fmt}"}
    async with connect(relay_url) as relay:
        reserve = {"op": "reserve", "name": name, "ttl": ttl, "format": fmt}
        reserve |= ({"display": display} if display else {}) | ({"key": key} if key else {})
        await relay.send(json.dumps(reserve))
        g = json.loads(await relay.recv())
        if g.get("op") != "granted":
            return {"op": "refused", "reply": g}
        adapter, period = Adapter(g["w"], g["h"], g["palette"]), 1.0 / g["fps"]
        st = {"received": 0, "sent": 0, "invalid": 0, "errors": {}, "lost": None,
              "pending": None, "finished": False, "game_end": None, "game_error": None}
        wake = asyncio.Event()

        async def relay_errors():
            async for m in relay:
                if isinstance(m, str) and m.startswith("{"):
                    msg = json.loads(m)
                    if msg.get("op") == "error":
                        st["errors"][msg["reason"]] = st["errors"].get(msg["reason"], 0) + 1
                        if msg["reason"] == "not-holder":
                            st["lost"] = "not-holder"
                            wake.set()

        async def pump(game):
            pings = 0
            try:
                while frames is None or st["received"] < frames:
                    try:
                        m = await asyncio.wait_for(game.recv(), idle)
                    except TimeoutError:
                        if pings:                       # the last ping went unanswered
                            st["game_end"] = f"no reply to ping in {idle:g} s"
                            break
                        pings += 1
                        await game.send({"type": "ping", "id": pings})
                        await relay.send(json.dumps({"op": "renew"}))   # a paused game
                        continue
                    pings = 0
                    if m is None:
                        err = st["game_error"]
                        st["game_end"] = "closed" + (f" after error {err}" if err else "")
                        break
                    if m.get("type") == "frame":
                        if valid_rows(m.get("rows")):
                            st["received"] += 1
                            st["pending"] = m
                            wake.set()
                        else:
                            st["invalid"] += 1
                    elif m.get("type") == "error":
                        st["game_error"] = m.get("code")
                else:
                    st["game_end"] = "frames"
            except Exception as e:                      # a transport error: the game is gone
                st["game_end"] = f"error: {e!r}"
            finally:
                st["finished"] = True
                wake.set()

        game = await GameViewer(game_url).open()
        tasks = [asyncio.create_task(relay_errors()), asyncio.create_task(pump(game))]
        last = -math.inf
        try:
            while st["lost"] is None:
                # the state first, after every send and every wake-up: a frame
                # to send, or the game over (D3: the frame that reaches --frames
                # and the pump's end can come in one wake-up while this loop idles)
                if st["pending"] is None:
                    if st["finished"]:
                        break
                    await wake.wait()
                    wake.clear()
                    continue
                wait = last + period - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                m, st["pending"] = st["pending"], None     # the latest by now
                number = m.get("frame_no", 0) % dc.SEQ_MOD if seq else None
                await relay.send(encode(g["format"], adapter.cells(m["rows"]), g["w"], g["h"],
                                        g["palette"], number))
                last = time.monotonic()
                st["sent"] += 1
                if st["pending"] is not None:
                    wake.set()
            if st["lost"] is None:
                await relay.send(json.dumps({"op": "release"}))
                await asyncio.sleep(0.05)
        finally:
            for t in tasks:
                t.cancel()
            await game.close()
    op = ("lost" if st["lost"] else "incomplete" if frames and st["received"] < frames
          else "done")
    return {"op": op, "display": display, "format": g["format"], "w": g["w"], "h": g["h"],
            "fps": g["fps"], "received": st["received"], "sent": st["sent"],
            "skipped": st["received"] - st["sent"], "invalid": st["invalid"],
            "errors": st["errors"], "lost": st["lost"], "game_end": st["game_end"]}
