"""Protocol tests for the mock relay (spec v0.2.1), the demo source and the viewer.

    cd contrib/displays && python -m pytest demo

The relay-neutral conformance suite is contract/relay_conformance.py; these
tests cover the mock itself: its CLI, capabilities.json, UDP listener, fan-out
options and the choices README.md lists.
"""
import asyncio
import json
import socket
import time
import urllib.request

import pytest
from contract import display_contract as dc
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from demo import producers, source, view
from demo.__main__ import main
from demo.relay import CAPS_PATH, DEFAULT_DISPLAY, MAX_TTL, PROFILES, Relay

GB = 9 * 17


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


async def text(ws, op=None, where=None):
    """The next control message (of type OP, satisfying WHERE), skipping frames."""
    while True:
        m = await asyncio.wait_for(ws.recv(), 3)
        if isinstance(m, str) and m.startswith("{"):
            msg = json.loads(m)
            if (op is None or msg.get("op") == op) and (where is None or where(msg)):
                return msg


async def frame(ws):
    """The next frame: a binary message, or a text message that is not control."""
    while True:
        m = await asyncio.wait_for(ws.recv(), 3)
        if not (isinstance(m, str) and m.startswith("{")):
            return m


def free(m):
    return m["holder"] is None


async def send(ws, msg):
    await ws.send(json.dumps(msg))


async def viewer(url, display=DEFAULT_DISPLAY):
    ws = await connect(url)
    await send(ws, {"op": "view", "display": display})
    return ws


async def holder(url, display, **kw):
    ws = await connect(url)
    await send(ws, {"op": "reserve", "name": kw.pop("name", "s"), "display": display, **kw})
    g = await text(ws)
    assert g["op"] == "granted", g
    return ws, g


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


def test_profiles_are_the_pinned_presets():
    assert list(PROFILES) == ["cga40", "tetris", "green-building", "dc32", "gameboy", "trs80",
                              "c64", "ws2812", "hub75", "blinkenlights", "arcade", "remote"]
    assert PROFILES["green-building"] == (9, 17) and PROFILES["arcade"] == (20, 26)
    assert DEFAULT_DISPLAY == "green-building" and dc.SPEC_DEFAULT == "cga40"


def test_view_gets_caps_and_lease():
    async def go():
        async with Relay().serve() as url:
            ws = await viewer(url, "hub75")
            assert await text(ws) == {"op": "caps", "display": "hub75", "w": 64, "h": 32,
                                      "fps": 60, "format": "pal16",
                                      "palette": dc.palette("cga")}
            assert await text(ws) == {"op": "lease", "display": "hub75", "holder": None,
                                      "expires": None}
            await ws.close()
    run(go())


def test_view_and_reserve_default_to_the_relays_default():
    async def go():
        for default, want in ((None, "green-building"), ("cga40", "cga40")):
            relay = Relay() if default is None else Relay(default=default)
            async with relay.serve() as url:
                ws = await connect(url)
                await send(ws, {"op": "view"})
                assert (await text(ws, "caps"))["display"] == want
                s = await connect(url)
                await send(s, {"op": "reserve", "name": "s"})
                g = await text(s, "granted")
                assert (g["w"], g["h"]) == PROFILES[want]
                assert (await text(ws, "lease", lambda m: m["holder"]))["holder"] == "s"
                await send(ws, {"op": "view", "display": "no-such-display"})
                assert await text(ws, "error") == {"op": "error", "reason": "bad-format"}
                await ws.close()
                await s.close()
    run(go())


def test_short_palettes_are_announced_through_the_level_rule():
    async def go():
        async with Relay().serve() as url:
            for name, pal in (("dc32", "gb"), ("blinkenlights", "mono"), ("arcade", "grey8")):
                ws = await viewer(url, name)
                caps = await text(ws, "caps")
                assert caps["palette"] == dc.palette16(dc.palette(pal))
                assert len(set(caps["palette"])) == len(dc.palette(pal))
                await ws.close()
    run(go())


def test_reserve_busy_and_not_holder():
    async def go():
        async with Relay().serve() as url:
            a, g = await holder(url, "green-building", name="a", ttl=300)
            assert (g["w"], g["h"], g["fps"], g["format"]) == (9, 17, 30, "pal16")
            assert g["palette"] == dc.palette("cga") and g["expires"] - time.time() > 290
            assert dc.check_message(g) == []
            b = await connect(url)
            await send(b, {"op": "reserve", "name": "b", "display": "green-building"})
            assert await text(b) == {"op": "busy", "holder": "a", "expires": g["expires"]}
            await b.send(bytes(GB))
            assert await text(b) == {"op": "error", "reason": "not-holder"}
            await b.send(dc.encode_hex(bytes(GB), 9, 17))
            assert await text(b) == {"op": "error", "reason": "not-holder"}
            for op in ("renew", "release"):
                await send(b, {"op": op})
                assert await text(b) == {"op": "error", "reason": "not-holder"}
            await a.close()
            await b.close()
    run(go())


def test_frames_fan_out_with_the_sequence_stripped():
    async def go():
        async with Relay().serve() as url:
            v = await viewer(url, "ws2812")
            await text(v, "lease")
            s, _ = await holder(url, "ws2812")
            body = bytes(i % 16 for i in range(256))
            await s.send(b"\x12\x34" + body)
            assert await frame(v) == body
            await s.close()
            await v.close()
    run(go())


def test_frame_errors_and_drops_never_reach_a_viewer():
    async def go():
        relay = Relay()
        async with relay.serve() as url:
            v = await viewer(url, "trs80")
            await text(v, "lease")
            s, _ = await holder(url, "trs80")
            n = 10 * 12
            ok = "0123456789\n" * 12
            for bad, reason in ((b"", "bad-frame-length"), (bytes(n - 1), "bad-frame-length"),
                                (bytes(n + 1), "bad-frame-length"),
                                (bytes(n + 3), "bad-frame-length"),
                                (bytes([16]) * n, "bad-format"), (bytes([255]) * n, "bad-format"),
                                ("", "bad-frame-length"), (ok[:-1], "bad-frame-length"),
                                (ok.replace("9", "g", 1), "bad-format"),
                                (ok.replace("\n", "\r\n"), "bad-frame-length")):
                await s.send(bad)
                assert (await text(s, "error"))["reason"] == reason, bad
            good = bytes([15]) * n
            await s.send(b"\x00\x05" + good)
            await s.send(good)  # at once: faster than 30 fps
            assert await text(s, "error") == {"op": "error", "reason": "rate"}
            assert await frame(v) == good
            await asyncio.sleep(0.05)
            await s.send(b"\x00\x04" + bytes(n))  # lower than the last accepted
            assert await text(s, "error") == {"op": "error", "reason": "rate"}
            await asyncio.sleep(0.05)
            await s.send(b"\x00\x05" + bytes([1]) * n)  # equal is not lower
            assert await frame(v) == bytes([1]) * n
            await asyncio.sleep(0.05)
            await s.send(ok.upper())  # upper-case hex digits are accepted here
            assert await frame(v) == bytes(i % 10 for i in range(n))
            assert relay.stats["frames"] == 3
            await s.close()
            await v.close()
    run(go())


def test_hex_and_rgb24_sources_fan_out_as_pal16():
    async def go():
        async with Relay().serve() as url:
            v = await viewer(url, "dc32")
            caps = await text(v, "caps")
            cells = bytes(i % 16 for i in range(180))
            s, g = await holder(url, "dc32", format="hex")
            assert g["format"] == "hex"
            await s.send(dc.encode_hex(cells, 10, 18, blank=True))
            assert await frame(v) == cells
            # the holder may switch format by reserving again
            await send(s, {"op": "reserve", "name": "s", "display": "dc32", "format": "rgb24"})
            assert (await text(s, "granted"))["format"] == "rgb24"
            await s.send(dc.encode_rgb24(cells, g["palette"], seq=1))
            got = await frame(v)
            colours = dc.colour_table(caps["palette"])
            # the relay picks the lowest index of the same colour; it renders the same
            assert [colours[c] for c in got] == [colours[c] for c in cells]
            assert got == bytes(min(j for j in range(16) if colours[j] == colours[c])
                                for c in cells)
            await asyncio.sleep(0.05)
            await s.send(bytes(180))  # w*h bytes is not an rgb24 frame
            assert await text(s, "error") == {"op": "error", "reason": "bad-frame-length"}
            await s.close()
            await v.close()
    run(go())


def test_hex_fan_out_converts_and_blacks_out_in_hex():
    async def go():
        async with Relay(fanout={"tetris": "hex"}).serve() as url:
            v = await viewer(url, "tetris")
            assert (await text(v, "caps"))["format"] == "hex"
            s, _ = await holder(url, "tetris", ttl=1)
            cells = bytes((x + y) % 16 for y in range(20) for x in range(10))
            await s.send(dc.encode_pal16(cells, seq=7))
            assert await frame(v) == dc.encode_hex(cells, 10, 20)
            await text(v, "lease", free)
            assert await frame(v) == "0000000000\n" * 20
            await s.close()
            await v.close()
    run(go())


def test_blp_and_mcuf_over_websocket():
    async def go():
        async with Relay().serve() as url:
            v = await viewer(url, "arcade")
            await text(v, "lease")
            s, g = await holder(url, "arcade")
            values = [i % 8 for i in range(20 * 26)]
            await s.send(dc.encode_mcuf(20, 26, values, maxval=7))
            assert await frame(v) == bytes(dc.scale(x, 7, 15) for x in values)
            await s.send(dc.encode_blp(18, 8, [1] * 144))  # the wrong geometry
            assert await text(s, "error") == {"op": "error", "reason": "bad-frame-length"}
            await s.send(dc.encode_mcuf(20, 26, [8] * 520, maxval=7))
            assert await text(s, "error") == {"op": "error", "reason": "bad-format"}
            await s.close()
            await v.close()
    run(go())


def test_udp_interop_picks_the_display_by_geometry_and_holds_it():
    async def go():
        relay = Relay(udp_ttl=1)
        async with relay.serve(udp_port=0) as url:
            addr = relay.udp_addr
            vs = {}
            for name in ("blinkenlights", "green-building", "tetris", "dc32"):
                vs[name] = await viewer(url, name)
                await text(vs[name], "lease")
            a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            b = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            a.bind(("127.0.0.1", 0))
            bits = [i % 2 for i in range(144)]
            a.sendto(dc.encode_blp(18, 8, bits), addr)
            # the sender's address as the relay sees it: in a FreeBSD jail
            # without its own lo0, 127.0.0.1 arrives as the jail's address
            who = (await text(vs["blinkenlights"], "lease"))["holder"]
            assert who.startswith("udp:") and who.endswith(f":{a.getsockname()[1]}")
            assert await frame(vs["blinkenlights"]) == bytes(15 * x for x in bits)
            s = await connect(url)
            await send(s, {"op": "reserve", "name": "ws", "display": "blinkenlights"})
            busy = await text(s, "busy")
            assert busy["holder"] == who
            b.sendto(dc.encode_blp(18, 8, [1] * 144), addr)  # another sender: dropped
            for w, h, name in ((9, 17, "green-building"), (10, 20, "tetris"), (10, 18, "dc32")):
                a.sendto(dc.encode_mcuf(w, h, [255] * (w * h), maxval=255), addr)
                assert await frame(vs[name]) == bytes([15]) * (w * h)
            a.sendto(dc.encode_mcuf(7, 7, [1] * 49), addr)  # no 7x7 display
            a.sendto(b"\xde\xad\xbe\xef\x00", addr)          # a short header
            await text(vs["blinkenlights"], "lease", free)
            assert await frame(vs["blinkenlights"]) == bytes(144)
            assert relay.stats["udp:busy"] == 1 and relay.stats["udp:no-display"] == 1
            assert relay.stats["udp:bad-frame-length"] == 1
            a.close()
            b.close()
            await s.close()
            for ws in vs.values():
                await ws.close()
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
            s, _ = await holder(url, "dc32", ttl=1)
            await text(v, "lease")
            t = time.monotonic()
            await s.send(bytes([9]) * 180)  # renews: 1 s from now
            await frame(v)
            await text(v, "lease", free)
            assert 0.9 < time.monotonic() - t < 2.5
            assert await frame(v) == bytes(180)
            await s.send(bytes(180))
            assert await text(s) == {"op": "error", "reason": "not-holder"}
            await s.close()
            await v.close()
    run(go())


def test_ttl_bounds():
    async def go():
        async with Relay().serve() as url:
            for ttl in (900, 901, 5000):
                s, g = await holder(url, "c64", ttl=ttl)
                assert MAX_TTL - 1 <= g["expires"] - time.time() <= MAX_TTL + 1
                await s.close()
            s = await connect(url)
            for ttl in (0, -1, 1.5, "10", None, True):
                await send(s, {"op": "reserve", "name": "s", "display": "c64", "ttl": ttl})
                assert await text(s) == {"op": "error", "reason": "bad-format"}, ttl
            await send(s, {"op": "reserve", "name": "s", "display": "c64", "ttl": 1.0})
            assert (await text(s))["op"] == "granted"  # 1.0 is an integer in JSON
            await s.close()
    run(go())


def test_unknown_op_and_malformed_control():
    async def go():
        async with Relay().serve() as url:
            s = await connect(url)
            for msg, reason in (('{"op":"dance"}', "unknown-op"), ('{"op":"caps"}', "unknown-op"),
                                ('{not json', "bad-format"), ('{"op":3}', "bad-format"),
                                ('{"display":"tetris"}', "bad-format"),
                                ('{"op":"reserve"}', "bad-format"),
                                ('{"op":"reserve","name":"s","format":"rgb"}', "bad-format"),
                                ('{"op":"view","display":7}', "bad-format")):
                await s.send(msg)
                assert await text(s) == {"op": "error", "reason": reason}, msg
            await s.close()
    run(go())


def test_viewer_cap_closes_the_extra_viewer():
    async def go():
        async with Relay(max_viewers=2).serve() as url:
            vs = [await viewer(url, "tetris") for _ in range(2)]
            for ws in vs:
                await text(ws, "lease")
            extra = await viewer(url, "tetris")
            with pytest.raises(ConnectionClosed):
                await text(extra)
            assert extra.close_code == 1013
            other = await viewer(url, "c64")  # the cap is per display
            await text(other, "caps")
            for ws in vs + [other]:
                await ws.close()
    run(go())


@pytest.mark.parametrize("rule,after_wrap", [("literal", "rate"), ("serial", None)])
def test_sequence_wrap(rule, after_wrap):
    async def go():
        async with Relay(seq_rule=rule).serve() as url:
            v = await viewer(url, "ws2812")
            await text(v, "lease")
            s, _ = await holder(url, "ws2812")
            for seq, body in ((65535, bytes([3]) * 256), (None, bytes([4]) * 256),
                              (0, bytes([5]) * 256)):
                await s.send(dc.encode_pal16(body, seq))
                if seq == 0 and after_wrap:
                    assert await text(s, "error") == {"op": "error", "reason": after_wrap}
                else:
                    assert await frame(v) == body
                await asyncio.sleep(0.05)
            await s.close()
            await v.close()
    run(go())


def test_capabilities_json():
    async def go():
        relay = Relay(extra=["edge=256x256,fanout=hex"])
        async with relay.serve(udp_port=0) as url:
            http_url = url.replace("ws://", "http://").rsplit("/", 1)[0] + "/capabilities.json"
            assert http_url.endswith(CAPS_PATH)
            body = await asyncio.to_thread(lambda: urllib.request.urlopen(http_url).read())
            caps = json.loads(body)
            pinned = dc.capabilities()
            assert caps["default"] == "green-building" and caps["spec"] == "0.2.1"
            assert list(caps["displays"])[:12] == list(PROFILES)
            assert caps["displays"]["hub75"]["fps"] == 60
            assert caps["displays"]["edge"]["format"] == "hex"
            assert caps["palettes"] == pinned["palettes"] and caps["max"] == pinned["max"]
            assert caps["interop"]["udp_port"] == relay.udp_addr[1]
            assert "wal.sh" not in json.dumps(caps["endpoints"])
            assert caps["endpoints"]["ws"] == url
    run(go())


def test_grid_limits_on_extra_displays():
    Relay(extra=["one=1x1", "max=256x256"])
    for bad in ("zero=0x5", "wide=257x1", "tall=1x257"):
        with pytest.raises(ValueError):
            Relay(extra=[bad])
    with pytest.raises(ValueError):
        Relay(default="nowhere")


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
            assert stats["frames"] == 6 and len(stats["last"]) == w * h
            assert stats["lease"]["holder"] == "demo@jail"
            assert stats["state"]["dropped"] == 0 and stats["state"]["seq"] == 6
            assert relay.stats["released"] == 1  # the source released at the end
    run(go())


@pytest.mark.parametrize("fmt", ["pal16", "hex", "rgb24"])
@pytest.mark.parametrize("display", ["dc32", "arcade", "blinkenlights"])
def test_source_formats_render_alike(fmt, display):
    async def go():
        async with Relay().serve() as url:
            stats, ready = {}, asyncio.Event()
            watcher = asyncio.create_task(
                view.run(url, display, frames=3, render=False, stats=stats, ready=ready))
            await ready.wait()
            result = await source.run(url, display, "bars", frames=3, fps=20, fmt=fmt)
            await asyncio.wait_for(watcher, 3)
            assert result["errors"] == {} and stats["frames"] == 3
            w, h = PROFILES[display]
            colours = dc.colour_table(stats["state"]["palette"])
            want = next(producers.bars(w, h))
            assert [colours[c] for c in stats["state"]["cells"]] == [colours[c] for c in want]
    run(go())


def test_list_shows_every_preset(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    for name in PROFILES:
        assert name in out
    assert "(default here)" in out and "(spec default)" in out


def test_refuses_the_live_relay():
    with pytest.raises(SystemExit):
        source.check_url("wss://wal.sh/tools/display/ws")
    with pytest.raises(SystemExit):
        source.check_url("ws://10.0.0.1:8765/tools/display/ws")
    source.check_url("ws://127.0.0.1:8765/tools/display/ws")
