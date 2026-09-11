"""The conformance suite a wal.sh/tools/display v0.2.1 relay must pass, on loopback.

    cd contrib/displays
    python -m pytest contract/relay_conformance.py                # this repo's demo relay
    python -m pytest contract/relay_conformance.py \\
        --relay-url ws://127.0.0.1:8765/tools/display/ws [--relay-udp 127.0.0.1:2323]
    python -m pytest contract/relay_conformance.py --relay-cmd "CMD"   # prints its ws:// URL
    # a relay behind a gatekeeper: sources through the gatekeeper, viewers direct
    python -m pytest contract/relay_conformance.py \\
        --source-url ws://127.0.0.1:9000/tools/display/ws \\
        --viewer-url ws://127.0.0.1:8765/tools/display/ws

With no options it starts `python -m demo relay` with UDP, a hex fan-out on
tetris and the boundary displays 1x1, 256x256, 256x1 and 1x256, and stops
it with SIGTERM at the end.  Waits are WAIT (8 s) for anything expected,
so a stalled host slows a failure rather than causing one; the 33rd viewer
may be refused with any close code.

Displays are read from the relay's capabilities.json (served next to the
WebSocket) when it has one, else from the pinned presets; tests needing a
display the relay does not advertise (a 1x1 or 256x256 grid, a hex fan-out)
or a UDP listener skip.  Every control message received is checked against
schemas/.  Marks: `choice` tests this kit's readings where the spec is silent
(--skip-choices skips them); `same_url` needs view and reserve on one URL.
The frame, sequence and interop tests replay contract/fixtures/.
"""
import asyncio
import json
import os
import pathlib
import queue
import re
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from contract import display_contract as dc

HERE = pathlib.Path(__file__).resolve().parent
LOOPBACK = ("127.0.0.1", "::1", "localhost")
WAIT = 8.0   # a loaded shared host can stall a relay for seconds
DEMO_CMD = (f"{shlex.quote(sys.executable)} -m demo relay --port 0 --udp-port 0 "
            "--fanout tetris=hex --extra-display edge-1x1=1x1 --extra-display edge-256=256x256 "
            "--extra-display edge-256x1=256x1 --extra-display edge-1x256=1x256")


def fixture_cases(name):
    return json.loads((dc.FIXTURES / name).read_text("utf-8"))["cases"]


FRAMES, SEQUENCES, INTEROP = (fixture_cases(n) for n in
                              ("frames.json", "sequence.json", "interop.json"))


# ------------------------------------------------------------------ the relay under test

@dataclass
class Target:
    source: str
    viewer: str
    caps: dict
    served: bool
    udp: tuple | None
    seq_rule: str
    default: str | None

    def displays(self):
        return self.caps["displays"]

    def by_grid(self, w, h):
        return next((n for n, p in self.displays().items() if (p["w"], p["h"]) == (w, h)), None)


def _loopback(url):
    if urlsplit(url).scheme != "ws" or urlsplit(url).hostname not in LOOPBACK:
        raise pytest.UsageError(f"refusing {url}: conformance runs on loopback only")
    return url


def _launch(cmd):
    proc = subprocess.Popen(cmd, shell=True, cwd=HERE.parent, stdout=subprocess.PIPE, text=True,
                            start_new_session=True)
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(x) for x in proc.stdout], daemon=True).start()
    url = udp = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and (url is None or udp is None):
        try:
            line = lines.get(timeout=0.5 if url else max(0.1, deadline - time.monotonic()))
        except queue.Empty:
            if url:
                break
            continue
        url = url or (m.group(0) if (m := re.search(r"ws://\S+", line)) else None)
        if m := re.search(r"udp (\S+):(\d+)", line):
            udp = (m.group(1), int(m.group(2)))
    if url is None:
        os.killpg(proc.pid, signal.SIGKILL)
        raise RuntimeError(f"{cmd!r} printed no ws:// URL")
    return proc, url, udp


def _served_caps(url):
    base = url.replace("ws://", "http://", 1).split("?")[0].rsplit("/", 1)[0]
    try:
        with urllib.request.urlopen(base + "/capabilities.json", timeout=3) as r:
            doc = json.loads(r.read())
        return doc if isinstance(doc.get("displays"), dict) else None
    except (OSError, ValueError):
        return None


@pytest.fixture(scope="session")
def target(request):
    opt = request.config.getoption
    url, proc, udp = opt("--relay-url"), None, None
    if not url and not (opt("--source-url") and opt("--viewer-url")):
        proc, url, udp = _launch(opt("--relay-cmd") or DEMO_CMD)
    source, viewer = _loopback(opt("--source-url") or url), _loopback(opt("--viewer-url") or url)
    served = _served_caps(viewer)
    caps = served or {"displays": {n: {**p} for n, p in dc.presets().items()}}
    if opt("--relay-udp"):
        host, _, port = opt("--relay-udp").rpartition(":")
        udp = (host, int(port))
    elif udp is None and served and served.get("interop", {}).get("udp_port"):
        udp = (urlsplit(viewer).hostname, served["interop"]["udp_port"])
    default = opt("--relay-default") or (served or {}).get("default")
    try:
        yield Target(source, viewer, caps, served is not None, udp, opt("--seq-rule"), default)
    finally:
        if proc is not None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(5)


# ------------------------------------------------------------------ peers

def run(coro, timeout=60):
    """Run a test's coroutine; then close every peer it opened, even when it
    failed, so a held lease never leaks into the next test."""
    async def main():
        try:
            return await asyncio.wait_for(coro, timeout)
        finally:
            for p in Peer.opened:
                try:
                    await asyncio.wait_for(p.ws.close(), 2)
                except (TimeoutError, OSError, ConnectionClosed):
                    pass
            Peer.opened.clear()
    return asyncio.run(main())


def is_control(m):
    return isinstance(m, str) and m.startswith("{")


class Peer:
    opened: list = []

    def __init__(self, ws):
        self.ws = ws
        Peer.opened.append(self)

    @classmethod
    async def open(cls, url):
        return cls(await connect(url, max_size=1 << 20))

    async def send(self, m):
        await self.ws.send(json.dumps(m) if isinstance(m, dict) else m)

    def _parse(self, m):
        msg = json.loads(m)
        assert dc.check_message(msg) == [], f"relay sent {msg}: {dc.check_message(msg)}"
        return msg

    async def control(self, op=None, where=None, timeout=WAIT):
        """The next control message (of type OP, satisfying WHERE)."""
        while True:
            m = await asyncio.wait_for(self.ws.recv(), timeout)
            if is_control(m):
                msg = self._parse(m)
                if (op is None or msg["op"] == op) and (where is None or where(msg)):
                    return msg

    async def frame(self, timeout=WAIT):
        while True:
            m = await asyncio.wait_for(self.ws.recv(), timeout)
            if not is_control(m):
                return m
            self._parse(m)

    async def drain(self, seconds):
        """Every message within SECONDS: control as dicts, frames as they came."""
        out, end = [], time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            try:
                m = await asyncio.wait_for(self.ws.recv(), left)
            except (TimeoutError, ConnectionClosed):
                break
            out.append(self._parse(m) if is_control(m) else m)
        return out

    async def close(self):
        await self.ws.close()


def frames_in(msgs):
    return [m for m in msgs if not isinstance(m, dict)]


def errors_in(msgs):
    return [m["reason"] for m in msgs if isinstance(m, dict) and m["op"] == "error"]


def free(m):
    return m["holder"] is None


async def viewer(t, display):
    v = await Peer.open(t.viewer)
    await v.send({"op": "view", "display": display} if display else {"op": "view"})
    caps = await v.control("caps")
    await v.control("lease")
    return v, caps


async def holder(t, display, *, name=None, ttl=60, fmt=None, retry=3.0):
    """A source holding DISPLAY; a busy (a previous test's lease still being
    released) is retried for RETRY seconds."""
    s = await Peer.open(t.source)
    msg = {"op": "reserve", "name": name or f"conformance@{display}", "ttl": ttl}
    msg |= ({"display": display} if display else {}) | ({"format": fmt} if fmt else {})
    end = time.monotonic() + retry
    while True:
        await s.send(msg)
        r = await s.control(where=lambda m: m["op"] in ("granted", "busy", "error"))
        if r["op"] == "granted":
            return s, r
        if r["op"] != "busy" or time.monotonic() > end:
            raise AssertionError(f"reserve {display}: {r}")
        await asyncio.sleep(0.1)


def fan(cells, caps):
    return dc.fanout_frame(cells, caps["w"], caps["h"], caps["format"])


async def pace(caps, periods=1.5):
    await asyncio.sleep(periods / caps["fps"])


# ------------------------------------------------------------------ discovery, caps, limits

@pytest.mark.parametrize("name", dc.preset_order())
def test_caps_for_every_preset(target, name):
    """Presets, Viewer: caps carries display, w, h, fps, format and 16 colours (a
    short palette through the level rule); then lease."""
    async def go():
        v = await Peer.open(target.viewer)
        await v.send({"op": "view", "display": name})
        caps, lease = await v.control(), await v.control()
        p = dc.preset(name)
        assert (caps["op"], caps["display"], caps["w"], caps["h"]) == ("caps", name, p["w"], p["h"])
        assert caps["fps"] <= p["fps"] and caps["format"] in dc.FANOUT_FORMATS
        assert caps["palette"] == dc.palette16(dc.palette(p["palette"]))
        assert lease["op"] == "lease" and lease["display"] == name
        await v.close()
    run(go())


def test_hub75_runs_at_60_fps(target):
    async def go():
        v, caps = await viewer(target, "hub75")
        assert caps["fps"] == 60
        await v.close()
    run(go())


def test_omitted_display_is_the_advertised_default(target):
    """Viewer: "display optional"; it resolves to the relay's default (cga40 in
    the spec, green-building in this repo's mock)."""
    if target.default is None:
        pytest.skip("the relay serves no capabilities.json; pass --relay-default")
    async def go():
        v, caps = await viewer(target, None)
        assert caps["display"] == target.default
        s, g = await holder(target, None)
        d = target.displays()[target.default]
        assert (g["w"], g["h"]) == (d["w"], d["h"])
        await s.send({"op": "release"})
        await s.close()
        await v.close()
    run(go())


def test_advertised_displays_are_within_the_limits(target):
    """Limits: w, h in 1..256, w*h at most 65,536, fps at most 60."""
    for name, d in target.displays().items():
        assert dc.check_grid(d["w"], d["h"]) is None, name
        assert 1 <= d.get("fps", 30) <= dc.MAX_FPS, name
    if target.served:
        assert set(dc.preset_order()) <= set(target.displays())


# ------------------------------------------------------------------ leases

def test_granted_carries_the_lease_and_the_display(target):
    """Source: granted carries lease, w, h, fps, format (pal16 by default), palette
    and expires, ttl seconds away."""
    async def go():
        s, g = await holder(target, "c64", ttl=300)
        p = dc.preset("c64")
        assert (g["w"], g["h"], g["format"]) == (p["w"], p["h"], "pal16")
        assert g["palette"] == dc.palette16(dc.palette("c64"))
        assert 298 <= g["expires"] - time.time() <= 302
        await s.close()
    run(go())


def test_one_holder_busy_and_not_holder(target):
    """Rules: one holder per display; a reserve while held is busy; a
    non-holder's frames are dropped with not-holder and never drawn."""
    async def go():
        v, caps = await viewer(target, "dc32")
        a, g = await holder(target, "dc32", name="first")
        b = await Peer.open(target.source)
        await b.send({"op": "reserve", "name": "second", "display": "dc32"})
        busy = await b.control("busy")
        assert busy["expires"] >= int(time.time()) and busy["holder"]
        n = caps["w"] * caps["h"]
        for data in (bytes([7]) * n, dc.encode_hex(bytes([7]) * n, caps["w"], caps["h"])):
            await b.send(data)
            assert (await b.control("error"))["reason"] == "not-holder"
        for op in ("renew", "release"):
            await b.send({"op": op})
            assert (await b.control("error"))["reason"] == "not-holder"
        mine = bytes([3]) * n
        await a.send(mine)
        assert await v.frame() == fan(mine, caps)
        assert frames_in(await v.drain(0.3)) == []
        for p in (a, b, v):
            await p.close()
    run(go())


def test_ttl_is_at_most_900(target):
    """Rules: ttl is at most 900 s (901 is clamped to 900 here, or refused)."""
    async def go():
        for ttl in (900, 901):
            s = await Peer.open(target.source)
            await s.send({"op": "reserve", "name": "ttl", "display": "gameboy", "ttl": ttl})
            r = await s.control(where=lambda m: m["op"] in ("granted", "error", "busy"))
            if r["op"] == "granted":
                assert r["expires"] - time.time() <= dc.MAX_TTL + 1
            else:
                assert r == {"op": "error", "reason": "bad-format"}, r
            await s.close()
            await asyncio.sleep(0.1)
    run(go())


@pytest.mark.choice
def test_ttl_outside_the_domain_is_bad_format(target):
    async def go():
        s = await Peer.open(target.source)
        for ttl in (0, -1, 1.5, "10", True):
            await s.send({"op": "reserve", "name": "ttl", "display": "gameboy", "ttl": ttl})
            assert await s.control() == {"op": "error", "reason": "bad-format"}, ttl
        await s.close()
    run(go())


def test_expiry_counts_from_the_last_frame_or_renew(target):
    """Rules: the lease expires ttl s after the last frame or renew; then every
    viewer gets lease holder null and a black frame; the old holder is a
    non-holder; another source may reserve."""
    async def go():
        v, caps = await viewer(target, "trs80")
        s, _ = await holder(target, "trs80", ttl=1)
        n = caps["w"] * caps["h"]
        for k in range(4):                       # 1.6 s of frames on a 1 s lease
            await s.send(bytes([k + 1]) * n)
            await asyncio.sleep(0.4)
        await s.send({"op": "renew"})
        t = time.monotonic()
        await v.control("lease", free, timeout=WAIT)
        assert 0.8 <= time.monotonic() - t <= 2.6
        assert await v.frame() == dc.black_frame(caps["w"], caps["h"], caps["format"])
        await s.send(bytes(n))
        assert (await s.control("error"))["reason"] == "not-holder"
        other, _ = await holder(target, "trs80", retry=0)
        for p in (other, s, v):
            await p.close()
    run(go())


def test_release_and_close_free_the_display(target):
    async def go():
        v, _ = await viewer(target, "ws2812")
        a, _ = await holder(target, "ws2812", name="a")
        await v.control("lease", lambda m: m["holder"])
        await a.send({"op": "release"})
        await v.control("lease", free)
        b, _ = await holder(target, "ws2812", name="b", retry=0)
        await b.close()
        await v.control("lease", free)
        for p in (a, v):
            await p.close()
    run(go())


# ------------------------------------------------------------------ frames

def frame_cases(grid, fmt):
    return [c for c in FRAMES if c["name"].startswith(f"{grid} {fmt}: ")]


async def replay_frames(t, display, grid, fmt, skip_choices):
    """Send the fixture frames of GRID in FMT to DISPLAY: each valid one must
    reach the viewer (in caps.format), each bad one get its error, and no bad
    or dropped one ever be drawn."""
    v, caps = await viewer(t, display)
    s, _ = await holder(t, display, fmt=fmt)
    last = None
    for c in frame_cases(grid, fmt):
        if skip_choices and "choice" in c.get("tags", []):
            continue
        exp = c["expect"]
        await s.send(dc.frame_from_json(c))
        if not exp["ok"]:
            assert (await s.control("error"))["reason"] == exp["reason"], c["name"]
            continue
        if not dc.seq_accepts(last, exp["seq"], t.seq_rule):
            assert (await s.control("error"))["reason"] == "rate", c["name"]
            continue
        last = exp["seq"] if exp["seq"] is not None else last
        assert await v.frame() == fan(dc.state_from_fixture(exp)["cells"], caps), c["name"]
        await pace(caps)
    assert frames_in(await v.drain(0.2)) == []
    await s.close()
    await v.close()


@pytest.mark.parametrize("fmt", dc.SOURCE_FORMATS)
@pytest.mark.parametrize("name", dc.preset_order())
def test_frames_on_every_preset(target, name, fmt, request):
    """Frame formats: one valid frame per format, each wrong length, a pal16 16
    and 255, a hex g, CRLF, a missing LF (fixtures/frames.json)."""
    run(replay_frames(target, name, name, fmt, request.config.getoption("--skip-choices")))


@pytest.mark.parametrize("grid", ["1x1", "256x1", "1x256", "256x256"])
@pytest.mark.parametrize("fmt", dc.FANOUT_FORMATS)
def test_frames_at_the_grid_boundaries(target, grid, fmt, request):
    """Limits: 256 x 256 frames up to 65,538 bytes (pal16) and 65,793 (hex)."""
    w, h = map(int, grid.split("x"))
    display = target.by_grid(w, h)
    if display is None:
        pytest.skip(f"the relay advertises no {grid} display")
    run(replay_frames(target, display, grid, fmt, request.config.getoption("--skip-choices")))


def test_hex_fan_out_converts(target):
    """caps.format is the format the relay fans out: a pal16 source on a hex
    display arrives as hex, and its black frame too."""
    display = next((n for n, d in target.displays().items() if d.get("format") == "hex"), None)
    if display is None:
        pytest.skip("the relay advertises no hex fan-out")
    async def go():
        v, caps = await viewer(target, display)
        assert caps["format"] == "hex"
        s, _ = await holder(target, display, ttl=1)
        cells = bytes(i % 16 for i in range(caps["w"] * caps["h"]))
        await s.send(dc.encode_pal16(cells, seq=9))
        assert await v.frame() == dc.encode_hex(cells, caps["w"], caps["h"])
        await v.control("lease", free, timeout=WAIT)
        assert await v.frame() == dc.black_frame(caps["w"], caps["h"], "hex")
        await s.close()
        await v.close()
    run(go())


def test_rgb24_quantizes_with_ties_low(target):
    """Source recipes: rgb24 at the relay, nearest palette entry, ties to the
    lower index (fixtures/quantize.json, cga)."""
    rows = [c for c in fixture_cases("quantize.json") if c["palette"] == "cga"]
    async def go():
        v, caps = await viewer(target, "ws2812")
        s, g = await holder(target, "ws2812", fmt="rgb24")
        n = caps["w"] * caps["h"]
        picked = (rows * (n // len(rows) + 1))[:n]
        await s.send(b"".join(bytes(c["rgb"]) for c in picked))
        assert await v.frame() == fan(bytes(c["index"] for c in picked), caps)
        await s.close()
        await v.close()
    run(go())


# ------------------------------------------------------------------ rate and sequence

@pytest.mark.parametrize("case", sorted({c["name"] for c in SEQUENCES}))
def test_sequence(target, case):
    """Frame formats: a sequence lower than the last accepted is dropped with
    rate (--seq-rule: literal, the spec's text, or serial)."""
    c = next(x for x in SEQUENCES if x["name"] == case and x["rule"] == target.seq_rule)
    async def go():
        v, caps = await viewer(target, "blinkenlights")
        s, _ = await holder(target, "blinkenlights")
        n = caps["w"] * caps["h"]
        for k, (seq, ok) in enumerate(zip(c["seqs"], c["accepted"], strict=True)):
            body = bytes([k % 2 * 15]) * n
            await s.send(dc.encode_pal16(body, seq))
            if ok:
                assert await v.frame() == fan(body, caps), (seq, k)
            else:
                assert (await s.control("error"))["reason"] == "rate", (seq, k)
            await pace(caps)
        await s.close()
        await v.close()
    run(go())


@pytest.mark.parametrize("display", ["tetris", "hub75"])
def test_back_to_back_frames_are_dropped_not_queued(target, display):
    """Rules: frames faster than fps are dropped, not queued (no backlog)."""
    async def go():
        v, caps = await viewer(target, display)
        s, _ = await holder(target, display, fmt="pal16")
        n = caps["w"] * caps["h"]
        await s.send(bytes([1]) * n)
        await s.send(bytes([2]) * n)            # at once
        assert errors_in(await s.drain(0.3)) == ["rate"]
        assert frames_in(await v.drain(0.3)) == [fan(bytes([1]) * n, caps)]
        await s.close()
        await v.close()
    run(go())


@pytest.mark.parametrize("display", ["tetris", "hub75"])
def test_frames_at_fps_pass(target, display):
    """Frames 1.5 periods apart all pass (hub75: 25 ms, which a 30 fps relay
    would drop).  A loaded host can bunch two frames in the relay's socket,
    which no relay can tell from a burst, so one clean round in three passes;
    the experiment behind this is in README.md."""
    async def go():
        v, caps = await viewer(target, display)
        s, _ = await holder(target, display, fmt="pal16")
        n, rounds = caps["w"] * caps["h"], []
        for _ in range(3):
            for k in range(8):
                await s.send(bytes([k % 16]) * n)
                await pace(caps)
            drops = errors_in(await s.drain(0.2))
            got = frames_in(await v.drain(0.2))
            rounds.append((len(drops), len(got)))
            if len(drops) <= 1 and len(got) >= 7:
                break
            await asyncio.sleep(0.3)
        assert rounds[-1][0] <= 1 and rounds[-1][1] >= 7, f"(drops, drawn) per round: {rounds}"
        await s.close()
        await v.close()
    run(go())


@pytest.mark.parametrize("display", ["tetris", "hub75"])
def test_frames_above_fps_are_dropped(target, display):
    """Frames at 1.5 x fps: some are dropped with rate, and what passes never
    exceeds fps over the run."""
    async def go():
        v, caps = await viewer(target, display)
        s, _ = await holder(target, display, fmt="pal16")
        n, fps = caps["w"] * caps["h"], caps["fps"]
        t0 = time.monotonic()
        for k in range(12):                      # 1.5 x fps
            await s.send(bytes([k % 16]) * n)
            await asyncio.sleep(1 / (1.5 * fps))
        span = time.monotonic() - t0
        errs, got = errors_in(await s.drain(0.3)), frames_in(await v.drain(0.3))
        assert errs.count("rate") >= 1 and len(got) + len(errs) == 12
        assert len(got) <= span * fps + 2
        await s.close()
        await v.close()
    run(go())


# ------------------------------------------------------------------ control and viewers

def test_unknown_op_is_unknown_op(target):
    async def go():
        for url in {target.source, target.viewer}:
            p = await Peer.open(url)
            await p.send({"op": "dance"})
            assert await p.control() == {"op": "error", "reason": "unknown-op"}
            await p.close()
    run(go())


@pytest.mark.choice
def test_malformed_control_is_bad_format(target):
    async def go():
        p = await Peer.open(target.viewer)
        for text in ("{oops", '{"op":7}', '{"display":"tetris"}',
                     '{"op":"view","display":"no-such-display"}'):
            await p.send(text)
            assert await p.control() == {"op": "error", "reason": "bad-format"}, text
        await p.close()
    run(go())


def test_32_viewers_and_no_more(target):
    """Limits: a relay-side cap of 32 viewers per display."""
    async def go():
        vs = [(await viewer(target, "gameboy"))[0] for _ in range(dc.MAX_VIEWERS)]
        extra = await Peer.open(target.viewer)
        await extra.send({"op": "view", "display": "gameboy"})
        assert not any(isinstance(m, dict) and m["op"] == "caps" for m in await extra.drain(1.0))
        s, g = await holder(target, "gameboy")
        await s.send(bytes([5]) * (g["w"] * g["h"]))
        got = [frames_in(await p.drain(0.5)) for p in vs + [extra]]
        assert sum(1 for f in got if f) == dc.MAX_VIEWERS and not got[-1]
        for p in vs + [extra, s]:
            await p.close()
    run(go())


def test_a_late_viewer_sees_the_next_frame_not_history(target):
    """Rules and NR-HISTORY: the relay keeps no frames."""
    async def go():
        v1, caps = await viewer(target, "cga40")
        s, _ = await holder(target, "cga40")
        n = caps["w"] * caps["h"]
        await s.send(bytes([4]) * n)
        await v1.frame()
        v2, _ = await viewer(target, "cga40")
        assert frames_in(await v2.drain(0.3)) == []
        await pace(caps)
        await s.send(bytes([6]) * n)
        assert await v2.frame() == fan(bytes([6]) * n, caps)
        for p in (s, v1, v2):
            await p.close()
    run(go())


@pytest.mark.same_url
def test_a_holder_may_view_its_own_display(target):
    async def go():
        p = await Peer.open(target.source)
        await p.send({"op": "view", "display": "arcade"})
        caps = await p.control("caps")
        await p.send({"op": "reserve", "name": "self", "display": "arcade"})
        await p.control("granted")
        await p.send(bytes([2]) * (caps["w"] * caps["h"]))
        assert await p.frame() == fan(bytes([2]) * (caps["w"] * caps["h"]), caps)
        await p.close()
    run(go())


# ------------------------------------------------------------------ UDP interop (last: 5 s holds)

def test_udp_blp_and_mcuf(target, request):
    """Source recipes and capabilities.interop: BLP and MCUF over UDP, the display
    by width x height, maxval scaled to 0..15, the sender a 5-second holder
    that a WebSocket reserve finds busy; expiry as for any lease."""
    if target.udp is None:
        pytest.skip("no UDP listener (--relay-udp)")
    skip_choices = request.config.getoption("--skip-choices")
    ties = {(9, 17), (10, 18), (10, 20)}
    cases = [c for c in INTEROP if c["expect"]["ok"] and c["expect"]["display"]
             and not (skip_choices and (c["expect"]["w"], c["expect"]["h"]) in ties)]
    async def go():
        vs, sock = {}, socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for c in cases:
            d = c["expect"]["display"]
            if d not in vs:
                vs[d] = await viewer(target, d)
        for c in cases:
            v, caps = vs[c["expect"]["display"]]
            sock.sendto(dc.frame_from_json(c), target.udp)
            assert await v.frame() == fan(dc.state_from_fixture(c["expect"])["cells"], caps), c
            await pace(caps)
        t = time.monotonic()
        v, caps = vs["blinkenlights"]
        s = await Peer.open(target.source)
        await s.send({"op": "reserve", "name": "ws", "display": "blinkenlights"})
        assert (await s.control("busy"))["holder"]
        other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        other.sendto(dc.encode_blp(18, 8, [1] * 144), target.udp)   # held: dropped
        await v.control("lease", free, timeout=8)
        assert dc.UDP_TTL - 1.5 <= time.monotonic() - t <= dc.UDP_TTL + 1.5
        assert await v.frame() == dc.black_frame(caps["w"], caps["h"], caps["format"])
        sock.close()
        other.close()
        for p in [s] + [v for v, _ in vs.values()]:
            await p.close()
    run(go(), timeout=90)
