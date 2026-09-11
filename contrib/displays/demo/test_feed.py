"""demo feed, the game-to-display bridge: its adapter against the cljc
adapter's committed output, and end to end: a lockstep contract-v1 game
server (a stand-in here), the feed, the relay in lease-secret mode, and a
display viewer that must see the expected pal16.

    cd contrib/displays && python -m pytest demo/test_feed.py
"""
import asyncio
import hashlib
import json
import pathlib
import sys
import time

import pytest
from contract import display_contract as dc
from contract import dlk1
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from demo import feed
from demo.relay import Relay

CLJC = json.loads((pathlib.Path(__file__).resolve().parent / "fixtures" / "adapt-cljc.json")
                  .read_text("utf-8"))
FRAMES = CLJC["frames"]
SECRETS = {dlk1.TEST_KID: dlk1.TEST_SECRET}
SPEC_COLOURS = [(0, 0, 0), (255, 255, 255), (0, 255, 255), (0, 0, 255), (255, 170, 0),
                (255, 255, 0), (0, 255, 0), (255, 0, 0), (153, 0, 255), (42, 42, 42)]


def expected(display, i):
    return bytes(next(c["cells"] for c in CLJC["cases"] if c["display"] == display
                      and c["frame"] == i))


def key(display, fmt=("pal16", "hex", "rgb24")):
    now = int(time.time())
    return dlk1.sign({"v": 1, "kid": "test", "iss": "dres", "sub": "player@lab", "rid": "r-9",
                      "display": display, "nbf": now - 10, "exp": now + 600, "fmt": list(fmt),
                      "jti": f"j-{time.monotonic_ns()}"}, dlk1.TEST_SECRET)


# ------------------------------------------------------------------ the adapter

@pytest.mark.parametrize("case", CLJC["cases"], ids=lambda c: f"{c['display']}-f{c['frame']}")
def test_the_adapter_matches_cljc(case):
    p = dc.preset(case["display"])
    a = feed.Adapter(p["w"], p["h"], dc.palette16(dc.palette(p["palette"])))
    assert (a.w, a.h) == (case["w"], case["h"])
    assert a.cells(FRAMES[case["frame"]]) == bytes(case["cells"])


def test_placement_is_the_letterbox():
    assert feed.placement(9, 17) == (list(range(9)), list(range(17)), 1)
    xs, ys, k = feed.placement(10, 12)                 # trs80: the top 5 rows go
    assert k == 1 and ys == list(range(5, 17)) and xs == [0, 1, 2, 3, 4, 5, 6, 7, 8, None]
    xs, ys, k = feed.placement(64, 32)                 # hub75: 1x, centred
    assert k == 1 and xs.count(None) == 55 and ys.count(None) == 15
    xs, ys, k = feed.placement(18, 34)                 # 2x fits exactly
    assert k == 2 and xs[:4] == [0, 0, 1, 1] and None not in ys


@pytest.mark.parametrize("name", dc.preset_order())
def test_a_lit_spec_cell_never_goes_dark(name):
    pal = [dc.hex_rgb(c) for c in dc.palette16(dc.palette(dc.preset(name)["palette"]))]
    for rgb in SPEC_COLOURS:
        assert (feed.color_index(pal, rgb) == 0) == (rgb == (0, 0, 0)), (name, rgb)


# ------------------------------------------------------------------ a lockstep game server stand-in

class Game:
    """A contract-v1 engine server stand-in with the lockstep clock: it takes
    one viewer's hello, answers it, then streams FRAMES (as if ticked) PERIOD
    apart, with a state message first, and closes."""

    def __init__(self, frames, period, end="close", pause_at=None, pause=0.0):
        self.frames, self.period, self.end = frames, period, end
        self.pause_at, self.pause = pause_at, pause
        self.released = asyncio.Event()     # set by a test to free a hung stand-in

    def hello(self):
        return {"type": "hello", "protocol": "17x9-tetris-remote", "version": 1,
                "role": "server", "mode": "engine", "client_role": "viewer", "spec_version": 2,
                "rows": 17, "cols": 9, "fps": 30, "max_message": 65536, "seed": 1,
                "clock": "lockstep"}

    def messages(self):
        yield {"type": "state", "score": 0, "level": 0, "lines": 0, "frame_no": 0}
        for i, rows in enumerate(self.frames):
            flat = bytes(v for row in rows for cell in row for v in cell)
            yield {"type": "frame", "frame_no": i, "rows": rows,
                   "digest": hashlib.sha256(flat).hexdigest(), "events": []}

    async def tcp(self, reader, writer):
        hello = json.loads(await reader.readline())
        assert hello["role"] == "viewer" and hello["version"] == 1
        writer.write((json.dumps(self.hello()) + "\n").encode())

        async def answer():                     # pong every ping, unless hung
            while line := await reader.readline():
                m = json.loads(line)
                if m.get("type") == "ping" and self.end != "hang":
                    writer.write((json.dumps({"type": "pong", "id": m.get("id")}) + "\n")
                                 .encode())
        pongs = asyncio.create_task(answer())
        for i, m in enumerate(self.messages()):
            writer.write((json.dumps(m) + "\n").encode())
            await writer.drain()
            await asyncio.sleep(self.pause if i == self.pause_at else self.period)
        if self.end == "hang":                  # stops sending, keeps the socket open
            writer.write((json.dumps({"type": "error", "code": "shutdown",
                                      "message": "stopping"}) + "\n").encode())
            await self.released.wait()
        pongs.cancel()
        if self.end == "abort":
            writer.transport.abort()
        else:
            writer.close()

    async def ws(self, conn):
        assert conn.request.path == "/tetris-17x9"
        hello = json.loads(await conn.recv())
        assert hello["role"] == "viewer"
        await conn.send(json.dumps(self.hello()))
        for m in self.messages():
            await conn.send(json.dumps(m))
            await asyncio.sleep(self.period)


async def game_url(game, transport, stack):
    if transport == "tcp":
        server = await asyncio.start_server(game.tcp, "127.0.0.1", 0)
        stack.append(server)
        return f"tcp://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    server = await serve(game.ws, "127.0.0.1", 0, subprotocols=["tetris-17x9.v1"])
    stack.append(server)
    return f"ws://127.0.0.1:{next(iter(server.sockets)).getsockname()[1]}/tetris-17x9"


async def watch(url, display, n):
    """A display viewer: the first N frames it sees."""
    got = []
    async with connect(url) as v:
        await v.send(json.dumps({"op": "view", "display": display}))
        while len(got) < n:
            m = await asyncio.wait_for(v.recv(), 10)
            if not (isinstance(m, str) and m.startswith("{")):
                got.append(m)
    return got


@pytest.mark.parametrize("display,transport,fmt", [
    ("dc32", "tcp", "pal16"), ("trs80", "tcp", "rgb24"), ("green-building", "ws", "hex"),
    ("hub75", "ws", "pal16")])
def test_game_to_display_end_to_end(display, transport, fmt):
    async def go():
        stack = []
        try:
            async with Relay(lease_secrets=SECRETS).serve() as url:
                gurl = await game_url(Game(FRAMES, 0.1), transport, stack)
                viewer = asyncio.create_task(watch(url, display, len(FRAMES)))
                await asyncio.sleep(0.2)
                done = await feed.run(gurl, url, display, key=key(display), fmt=fmt, seq=True)
                got = await asyncio.wait_for(viewer, 10)
        finally:
            for s in stack:
                s.close()
        assert done["op"] == "done" and done["errors"] == {} and done["lost"] is None
        assert (done["received"], done["sent"], done["format"]) == (4, 4, fmt)
        assert got == [expected(display, i) for i in range(len(FRAMES))]
    asyncio.run(asyncio.wait_for(go(), 40))


def test_the_feed_paces_to_fps_and_the_latest_frame_wins():
    burst = [FRAMES[i % 3] for i in range(20)]        # the last is FRAMES[1]

    async def go():
        stack = []
        try:
            async with Relay(lease_secrets=SECRETS).serve() as url:
                gurl = await game_url(Game(burst, 0), "tcp", stack)
                done = await feed.run(gurl, url, "dc32", key=key("dc32"))
                return done
        finally:
            for s in stack:
                s.close()
    done = asyncio.run(asyncio.wait_for(go(), 40))
    assert done["received"] == 20 and 1 <= done["sent"] < 20 and "rate" not in done["errors"]
    assert done["skipped"] == 20 - done["sent"]


def test_the_last_frame_of_a_burst_is_shown():
    burst = [FRAMES[i % 3] for i in range(20)]

    async def go():
        stack = []
        try:
            async with Relay(lease_secrets=SECRETS).serve() as url:
                gurl = await game_url(Game(burst, 0), "tcp", stack)
                async with connect(url) as v:
                    await v.send(json.dumps({"op": "view", "display": "dc32"}))
                    done = await feed.run(gurl, url, "dc32", key=key("dc32"))
                    frames = []
                    while True:
                        try:
                            m = await asyncio.wait_for(v.recv(), 0.5)
                        except TimeoutError:
                            break
                        if not (isinstance(m, str) and m.startswith("{")):
                            frames.append(m)
                return done, frames
        finally:
            for s in stack:
                s.close()
    done, frames = asyncio.run(asyncio.wait_for(go(), 40))
    assert len(frames) == done["sent"] and frames[-1] == expected("dc32", 1)


@pytest.mark.parametrize("end", ["close", "abort", "hang"])
def test_the_feed_releases_and_returns_when_the_game_goes_away(end):
    """The harness's case: the game server stops mid-stream (--frames 10, 4
    came).  The feed sends release, returns incomplete, and says why."""
    game = Game(FRAMES, 0.05, end=end)

    async def go():
        stack = []
        try:
            relay = Relay(lease_secrets=SECRETS)
            async with relay.serve() as url:
                gurl = await game_url(game, "tcp", stack)
                async with connect(url) as v:
                    await v.send(json.dumps({"op": "view", "display": "dc32"}))
                    t = time.monotonic()
                    done = await asyncio.wait_for(feed.run(gurl, url, "dc32", key=key("dc32"),
                                                           frames=10, idle=0.5), 10)
                    took = time.monotonic() - t
                    while True:                  # the viewer sees the lease end
                        m = await asyncio.wait_for(v.recv(), 5)
                        if isinstance(m, str) and m.startswith("{"):
                            msg = json.loads(m)
                            if msg["op"] == "lease" and msg["holder"] is None and took:
                                break
                    game.released.set()
                    return done, took, relay.stats["released"]
        finally:
            for s in stack:
                s.close()
    done, took, released = asyncio.run(asyncio.wait_for(go(), 30))
    assert done["op"] == "incomplete" and done["received"] == 4 and released == 1
    assert took < 5, took
    want = {"close": ("closed",), "abort": ("closed", "error"), "hang": ("no reply to ping",)}
    assert done["game_end"].startswith(want[end]), done["game_end"]


def test_a_paused_game_keeps_the_feed():
    game = Game(FRAMES, 0.05, pause_at=2, pause=1.5)    # silent 1.5 s, but answers pings

    async def go():
        stack = []
        try:
            async with Relay(lease_secrets=SECRETS).serve() as url:
                gurl = await game_url(game, "tcp", stack)
                return await feed.run(gurl, url, "dc32", key=key("dc32"), idle=0.5)
        finally:
            for s in stack:
                s.close()
    done = asyncio.run(asyncio.wait_for(go(), 30))
    assert done["op"] == "done" and done["received"] == 4 and done["game_end"] == "closed"
    assert done["errors"] == {}


def test_the_cli_exits_nonzero_when_the_game_goes_early(tmp_path):
    keyfile = tmp_path / "key"
    keyfile.write_text(key("dc32"))

    async def go():
        stack = []
        try:
            async with Relay(lease_secrets=SECRETS).serve() as url:
                gurl = await game_url(Game(FRAMES, 0.05), "tcp", stack)
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "demo", "feed", "--game", gurl, "--url", url, "-d",
                    "dc32", "--key-file", str(keyfile), "--frames", "10", "--game-idle", "0.5",
                    cwd=pathlib.Path(__file__).resolve().parents[1],
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                out, err = await asyncio.wait_for(proc.communicate(), 20)
                return proc.returncode, out.decode(), err.decode()
        finally:
            for s in stack:
                s.close()
    code, out, err = asyncio.run(asyncio.wait_for(go(), 40))
    assert code == 1 and json.loads(out)["op"] == "incomplete"
    assert "went away (closed) after 4 of 10 frames; the display was released" in err


def test_refusals():
    async def go():
        async with Relay(lease_secrets=SECRETS).serve() as url:
            by_key = await feed.run("tcp://127.0.0.1:9", url, "dc32", key=key("dc32", ["hex"]),
                                    fmt="rgb24")
            by_relay = await feed.run("tcp://127.0.0.1:9", url, "dc32")
            return by_key, by_relay
    by_key, by_relay = asyncio.run(asyncio.wait_for(go(), 20))
    assert by_key["op"] == "refused" and "rgb24" in by_key["reason"]
    assert by_relay == {"op": "refused", "reply": {"op": "error", "reason": "unauthorized",
                                                   "detail": "missing"}}
    with pytest.raises(SystemExit):
        feed.check_game_url("tcp://10.0.0.1:1709")
    with pytest.raises(SystemExit):
        feed.check_game_url("wss://wal.sh/tetris-17x9")
