"""Protocol tests for the mock relay, the demo source and the viewer.

    python -m pytest contrib/displays/demo
"""
import asyncio
import json
import time
import urllib.request

import pytest
from websockets.asyncio.client import connect

from demo import producers, source, view
from demo.relay import CAPS_PATH, MAX_TTL, PROFILES, Relay


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


async def text(ws, op=None):
    """The next JSON text message (of type OP, if given), skipping frames."""
    while True:
        m = await asyncio.wait_for(ws.recv(), 3)
        if isinstance(m, str):
            msg = json.loads(m)
            if op is None or msg.get("op") == op:
                return msg


async def binary(ws):
    """The next binary message, skipping text."""
    while True:
        m = await asyncio.wait_for(ws.recv(), 3)
        if isinstance(m, bytes):
            return m


async def send(ws, msg):
    await ws.send(json.dumps(msg))


async def viewer(url, display="green-building"):
    ws = await connect(url)
    await send(ws, {"op": "view", "display": display})
    return ws


@pytest.mark.parametrize("demo", sorted(producers.DEMOS))
def test_producers_yield_palette_frames_on_every_profile(demo):
    for w, h in set(PROFILES.values()):
        a = producers.DEMOS[demo](w, h, seed=7)
        b = producers.DEMOS[demo](w, h, seed=7)
        for _ in range(300):
            f = next(a)
            assert len(f) == w * h and max(f) < 16
            assert f == next(b)  # deterministic per seed
        assert len(producers.to_rgb(f)) == w * h * 3


def test_tetris_clears_rows_and_restarts():
    frames = producers.tetris(10, 12, seed=1)
    blank = bytes(120)
    seen_blank_after_play = False
    for k in range(20000):
        f = next(frames)
        if k > 100 and f == blank:
            seen_blank_after_play = True
            break
    assert seen_blank_after_play  # it topped out and started over


def test_view_gets_caps_and_lease():
    async def go():
        async with Relay().serve() as url:
            ws = await viewer(url, "hub75")
            assert await text(ws) == {"op": "caps", "display": "hub75", "w": 64, "h": 32,
                                      "fps": 30}
            assert await text(ws) == {"op": "lease", "display": "hub75", "holder": None,
                                      "expires": None}
            await ws.close()
    run(go())


def test_reserve_busy_and_not_holder():
    async def go():
        async with Relay().serve() as url:
            a, b = await connect(url), await connect(url)
            await send(a, {"op": "reserve", "name": "a", "display": "green-building",
                           "ttl": 300})
            g = await text(a)
            assert (g["op"], g["w"], g["h"], g["fps"]) == ("granted", 9, 17, 30)
            assert g["expires"] - time.time() > 290
            await send(b, {"op": "reserve", "name": "b", "display": "green-building"})
            busy = await text(b)
            assert busy == {"op": "busy", "holder": "a", "expires": g["expires"]}
            await b.send(bytes(9 * 17 * 3))
            assert await text(b) == {"op": "error", "reason": "not holder"}
            await a.close()
            await b.close()
    run(go())


def test_frames_fan_out_with_the_sequence_stripped():
    async def go():
        async with Relay().serve() as url:
            v = await viewer(url, "ws2812")
            await text(v, "lease")
            s = await connect(url)
            await send(s, {"op": "reserve", "name": "s", "display": "ws2812"})
            await text(s, "granted")
            assert (await text(v, "lease"))["holder"] == "s"
            body = bytes(range(256)) * 3
            await s.send(b"\x12\x34" + body)
            assert await binary(v) == body
            await s.close()
            await v.close()
    run(go())


def test_bad_frame_length_and_rate():
    async def go():
        relay = Relay()
        async with relay.serve() as url:
            v = await viewer(url, "trs80")
            s = await connect(url)
            await send(s, {"op": "reserve", "name": "s", "display": "trs80"})
            await text(s, "granted")
            await s.send(bytes(10))
            assert await text(s) == {"op": "error", "reason": "bad frame length"}
            frame = bytes([255]) * (10 * 12 * 3)
            await s.send(frame)
            await s.send(frame)  # at once: faster than 30 fps
            assert await text(s) == {"op": "error", "reason": "rate"}
            assert await binary(v) == frame
            assert relay.stats["frames"] == 1
            await s.close()
            await v.close()
    run(go())


def test_release_and_close_free_the_display():
    async def go():
        async with Relay().serve() as url:
            v = await viewer(url)
            await text(v, "lease")
            a = await connect(url)
            await send(a, {"op": "reserve", "name": "a"})
            await text(a, "granted")
            await text(v, "lease")
            await send(a, {"op": "release"})
            assert (await text(v, "lease"))["holder"] is None
            b = await connect(url)
            await send(b, {"op": "reserve", "name": "b"})
            await text(b, "granted")
            assert (await text(v, "lease"))["holder"] == "b"
            await b.close()  # closing the socket releases too
            assert (await text(v, "lease"))["holder"] is None
            await a.close()
            await v.close()
    run(go())


def test_expiry_sends_lease_null_and_a_black_frame():
    async def go():
        async with Relay().serve() as url:
            v = await viewer(url, "dc32")
            await text(v, "lease")
            s = await connect(url)
            await send(s, {"op": "reserve", "name": "s", "display": "dc32", "ttl": 1})
            await text(s, "granted")
            await text(v, "lease")
            t = time.monotonic()
            await s.send(bytes([200]) * (10 * 18 * 3))  # renews: 1 s from now
            await binary(v)
            assert (await text(v, "lease"))["holder"] is None
            assert 0.9 < time.monotonic() - t < 2.5
            assert await binary(v) == bytes(10 * 18 * 3)
            await s.send(bytes(10 * 18 * 3))
            assert await text(s) == {"op": "error", "reason": "not holder"}
            await s.close()
            await v.close()
    run(go())


def test_ttl_is_capped():
    async def go():
        async with Relay().serve() as url:
            s = await connect(url)
            await send(s, {"op": "reserve", "name": "s", "ttl": 5000})
            g = await text(s, "granted")
            assert g["expires"] - time.time() <= MAX_TTL + 1
            await s.close()
    run(go())


def test_capabilities_json():
    async def go():
        async with Relay().serve() as url:
            http_url = url.replace("ws://", "http://").rsplit("/", 1)[0] + "/capabilities.json"
            assert http_url.endswith(CAPS_PATH)
            body = await asyncio.to_thread(lambda: urllib.request.urlopen(http_url).read())
            caps = json.loads(body)
            assert caps["displays"]["hub75"] == {"w": 64, "h": 32, "fps": 30}
            assert len(caps["palette"]) == 16 and caps["max_ttl"] == MAX_TTL
    run(go())


@pytest.mark.parametrize("demo", sorted(producers.DEMOS))
@pytest.mark.parametrize("display", ["green-building", "hub75"])
def test_source_to_viewer_end_to_end(demo, display):
    async def go():
        relay = Relay()
        async with relay.serve() as url:
            stats, ready = {}, asyncio.Event()
            watcher = asyncio.create_task(
                view.run(url, display, frames=6, render=False, stats=stats, ready=ready))
            await ready.wait()
            # 20 fps against the relay's 30: scheduling jitter on a loaded
            # host must not turn into "rate" drops here
            result = await source.run(url, display, demo, frames=6, seq=True, fps=20)
            await asyncio.wait_for(watcher, 3)
            assert result["sent"] == 6 and result["errors"] == {}
            w, h = PROFILES[display]
            assert stats["frames"] == 6 and len(stats["last"]) == w * h * 3
            assert stats["lease"]["holder"] == "demo@jail"
            assert relay.stats["released"] == 1  # the source released at the end
    run(go())


def test_refuses_the_live_relay():
    with pytest.raises(SystemExit):
        source.check_url("wss://wal.sh/tools/display/ws")
    with pytest.raises(SystemExit):
        source.check_url("ws://10.0.0.1:8765/tools/display/ws")
    source.check_url("ws://127.0.0.1:8765/tools/display/ws")
