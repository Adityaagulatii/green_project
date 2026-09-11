"""Remote protocol (docs/PROTOCOL.md, draft v0): codec and server, over both
bindings (TCP JSON lines and WebSocket).

The key property is that the server preserves conformance. Frames streamed
in engine mode carry exactly the digests of a direct engine run of the same
seed and events, whatever transport carries them. That holds always for the
lockstep clock, and for the realtime clock for the events as they were
logged. The lockstep clock also reproduces every sealed trace.

    PYTHONPATH=impl/python/engine:impl/python/sim \\
        python -m pytest impl/python/sim/tests
"""

import asyncio
import importlib.util
import json
import socket
import subprocess
import sys
import time

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from remote import (
    ENV,
    ROOT,
    TRACES,
    loopback_address,
    run,
    start,
    start_cli,
    stop_cli,
    until,
)

from tetris_engine import core, render
from tetris_engine.animation import TetrisAnimation
from tetris_engine.conformance import group_events, observe, run_trace
from tetris_engine.frame import frame_digest
from tetris_engine.tables import ACTIONS
from tetris_sim import protocol as P
from tetris_sim import server as server_module
from tetris_sim.bot import Bot
from tetris_sim.client import connect, parse_url
from tetris_sim.recorder import Recorder, record
from tetris_sim.server import (
    MAX_CLIENTS,
    MAX_ERRORS,
    DisplayServer,
    DisplaySink,
    EngineServer,
    HtmlSink,
    _is_loopback,
    load_display,
)

BLACK = tuple(((0, 0, 0),) * 9 for _ in range(17))
WHITE = tuple(((255, 255, 255),) * 9 for _ in range(17))
BLACK_DIGEST = "e0ee29ce7978a33861e6e63545deda9e734ea784ee8e4ba6fd6aa56b775f6ca9"
WHITE_DIGEST = "cc1c8c603a0863247abc4b8a117a234714b37d2be10ca27218301e5e617830d8"
CODES = {"malformed", "too_large", "unknown_type", "hello_required", "version",
         "role", "bad_hello", "busy", "forbidden", "bad_event", "bad_tick",
         "bad_frame", "bad_state", "bad_ping", "digest", "too_many_errors"}
PROPERTY = settings(max_examples=100, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow,
                                           HealthCheck.data_too_large])


def sample_frame(seed=1, frames=120):
    s = core.new_game(seed)
    for _ in range(frames):
        s = core.step(s)
    return render(s)


async def lockstep_run(seed, frames, events, transport="tcp", viewer=None):
    """Drive a lockstep engine server with ``events`` ([frame, action, down])
    from a controller on ``transport``, and optionally watch it from a
    viewer on the ``viewer`` transport. The seed travels in the controller's
    hello; the server's own is wrong on purpose. Returns (frame msgs,
    state msgs, viewer frame msgs)."""
    server = EngineServer(seed=424242, clock="lockstep")
    try:
        urls = {t: await start(server, t)
                for t in dict.fromkeys((transport, viewer)) if t}
        watcher = await connect(urls[viewer], "viewer") if viewer else None
        ctl = await connect(urls[transport], "controller", seed=seed)
        assert ctl.hello["seed"] == seed and ctl.hello["clock"] == "lockstep"
        by_frame = group_events(events)
        marks = sorted(by_frame)
        k = 0
        while k < frames:
            await ctl.send(*(P.make_event(action, down)
                             for action, down in by_frame.get(k, ())))
            nxt = next((f for f in marks if f > k), frames)
            n = min(max(1, min(nxt, frames) - k), P.MAX_TICK)
            await ctl.send(P.make_tick(n))
            k += n
        got, states = [], []
        while len(got) < frames:
            msg = await ctl.recv(20)
            assert msg is not None and msg["type"] != "error", msg
            (got if msg["type"] == "frame" else states).append(msg)
        # §4.4: frame k carries E_k, exactly the events sent before its tick.
        assert [m["events"] for m in got] == \
            [[list(e) for e in by_frame.get(k, ())] for k in range(frames)]
        seen = []
        while watcher and len(seen) < frames:
            msg = await watcher.recv(20)
            assert msg is not None
            if msg["type"] == "frame":
                seen.append(msg)
        return got, states, seen
    finally:
        await server.close()


# ------------------------------------------------------------------ codec

def test_known_answer_digests():
    assert P.make_frame(0, BLACK)["digest"] == BLACK_DIGEST
    assert P.make_frame(1, WHITE)["digest"] == WHITE_DIGEST


def test_encode_is_one_compact_line():
    line = P.encode(P.make_frame(7, sample_frame()))
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert b" " not in line and len(line) < 4096


EXAMPLES = [
    P.make_hello("controller", client="pytest", seed=7),
    P.make_hello("server", mode="engine", client_role="controller",
                 spec_version=2, rows=17, cols=9, fps=30,
                 max_message=P.MAX_MESSAGE, seed=1, clock="lockstep"),
    P.make_event("rotate_180", True),
    P.make_event("hold", False),
    P.make_tick(P.MAX_TICK),
    P.make_frame(3, sample_frame()),
    P.make_frame(4, sample_frame(), events=[("left", True), ("hold", False)]),
    P.make_frame(5, sample_frame(), events=[]),
    P.make_state(1200, 3, 4, high_score=9000, phase="playing", frame_no=91),
    P.make_state(0, 0, 0),
    P.make_ping(5),
    P.make_pong("sync"),
    P.make_error("bad_event", "unknown action 'jump'"),
]


@pytest.mark.parametrize("msg", EXAMPLES, ids=lambda m: m["type"])
def test_roundtrip_examples(msg):
    assert P.decode(P.encode(msg)) == msg


def test_decode_normalizes():
    frame = sample_frame()
    wire = {"type": "frame", "frame_no": 2, "extra": {"ignored": True},
            "rows": [[list(rgb) for rgb in row] for row in frame]}
    msg = P.decode(json.dumps(wire).encode() + b"\r\n")
    assert msg == {"type": "frame", "frame_no": 2, "rows": frame,
                   "digest": frame_digest(frame)}
    assert msg["rows"] == frame  # the engine's own representation


colors = st.tuples(st.integers(0, 255), st.integers(0, 255), st.integers(0, 255))
frames = st.lists(st.lists(colors, min_size=9, max_size=9).map(tuple),
                  min_size=17, max_size=17).map(tuple)
nat = st.integers(0, 2 ** 53)
ids = st.none() | st.integers(-2 ** 31, 2 ** 31) | st.text(max_size=64)
messages = st.one_of(
    st.builds(P.make_hello, st.sampled_from(P.ROLES),
              client=st.text(max_size=20), seed=st.integers(0, 2 ** 32 - 1)),
    st.builds(P.make_event, st.sampled_from(ACTIONS), st.booleans()),
    st.builds(P.make_tick, st.integers(1, P.MAX_TICK)),
    st.builds(P.make_frame, nat, frames, events=st.none() | st.lists(
        st.tuples(st.sampled_from(ACTIONS), st.booleans()), max_size=4)),
    st.builds(P.make_state, nat, nat, nat, high_score=st.none() | nat,
              phase=st.none() | st.text(max_size=P.MAX_PHASE),
              frame_no=st.none() | nat),
    st.builds(P.make_ping, ids),
    st.builds(P.make_pong, ids),
    st.builds(P.make_error, st.text(max_size=20), st.text(max_size=100)),
)


@PROPERTY
@given(messages)
def test_encode_decode_roundtrip_property(msg):
    line = P.encode(msg)
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert P.decode(line) == msg
    # The WebSocket binding carries the same text without the LF.
    assert P.decode(line[:-1].decode("utf-8")) == msg


json_values = st.recursive(
    st.none() | st.booleans() | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False) | st.text(max_size=8),
    lambda inner: st.lists(inner, max_size=5)
    | st.dictionaries(st.text(max_size=5), inner, max_size=5),
    max_leaves=20)
fields = st.sampled_from(["action", "down", "frames", "frame_no", "rows",
                          "digest", "score", "level", "lines", "phase", "id",
                          "code", "message", "role", "protocol", "version",
                          "high_score", "seed"])
objects = st.builds(lambda kind, rest: {**rest, "type": kind},
                    st.sampled_from(P.TYPES + ("bogus",)),
                    st.dictionaries(fields, json_values, max_size=6))


@PROPERTY
@given(objects)
def test_decode_is_total_on_json_objects(obj):
    """Any object decodes to a valid message or fails with a §6 code, and
    decoding is idempotent on its own output."""
    try:
        msg = P.decode(json.dumps(obj))
    except P.ProtocolError as exc:
        assert exc.code in CODES
    else:
        assert msg["type"] in P.TYPES
        assert P.decode(P.encode(msg)) == msg


@PROPERTY
@given(st.binary(max_size=400))
def test_decode_is_total_on_bytes(data):
    try:
        P.decode(data)
    except P.ProtocolError as exc:
        assert exc.code in CODES


def frame_line(**over):
    msg = {"type": "frame", "frame_no": 0, "rows": [[[0, 0, 0]] * 9] * 17}
    msg.update(over)
    return json.dumps(msg).encode()


HELLO = b'{"type":"hello","protocol":"17x9-tetris-remote","version":1,'
MALFORMED = [
    (b"not json", "malformed"),
    (b"\xff\xfe{}", "malformed"),
    (b"[1, 2]", "malformed"),
    (b'"hello"', "malformed"),
    (b"{}", "malformed"),
    (b'{"type": 5}', "malformed"),
    (b'{"type": "tick", "type": "tick", "frames": 1}', "malformed"),
    (b'{"type": "state", "score": NaN, "level": 0, "lines": 0}', "malformed"),
    (b'{"type": "tick", "frames": Infinity}', "malformed"),
    (b"[" * 5000 + b"]" * 5000, "malformed"),
    (b'{"type": "teleport"}', "unknown_type"),
    (b'{"type":"hello","protocol":"17x9-tetris-remote","version":0,"role":"viewer"}',
     "version"),                                    # v1 has no v0 mode (§2)
    (b'{"type":"hello","protocol":"tetris","version":1,"role":"viewer"}', "version"),
    (HELLO + b'"role":"viewer","extra":{"ignored":1}}', None),  # valid
    (b'{"type":"hello","protocol":"17x9-tetris-remote","version":false,"role":"viewer"}',
     "version"),
    (HELLO + b'"role":"admin"}', "role"),
    (HELLO + b'"role":"controller","seed":-1}', "bad_hello"),
    (HELLO + b'"role":"controller","seed":4294967296}', "bad_hello"),
    (HELLO + b'"role":"controller","seed":"7"}', "bad_hello"),
    (b'{"type": "event", "action": "jump", "down": true}', "bad_event"),
    (b'{"type": "event", "action": "left", "down": 1}', "bad_event"),
    (b'{"type": "event", "action": "left"}', "bad_event"),
    (b'{"type": "tick", "frames": 0}', "bad_tick"),
    (b'{"type": "tick", "frames": 3601}', "bad_tick"),
    (b'{"type": "tick", "frames": 1.0}', "bad_tick"),
    (b'{"type": "tick", "frames": true}', "bad_tick"),
    (b'{"type": "state", "score": -1, "level": 0, "lines": 0}', "bad_state"),
    (b'{"type": "state", "score": 0, "level": 0}', "bad_state"),
    (b'{"type": "state", "score": 0, "level": 0, "lines": 0, "phase": 7}',
     "bad_state"),
    (b'{"type": "ping", "id": [1]}', "bad_ping"),
    (b'{"type": "error", "code": 3}', "malformed"),
    (frame_line(frame_no=-1), "bad_frame"),
    (frame_line(frame_no=True), "bad_frame"),
    (frame_line(digest="0" * 64), "digest"),
    (frame_line(digest="XYZ"), "bad_frame"),
    (frame_line(events=[["left", True]]), None),     # valid
    (frame_line(events=[["jump", True]]), "bad_frame"),
    (frame_line(events=[["left", 1]]), "bad_frame"),
    (frame_line(events=[["left", True, 0]]), "bad_frame"),
    (frame_line(events=[[["left"], True]]), "bad_frame"),
    (frame_line(events=None), "bad_frame"),
    (frame_line(events="left"), "bad_frame"),
]


@pytest.mark.parametrize("line,code", MALFORMED)
def test_malformed_input_is_rejected(line, code):
    if code is None:
        assert P.decode(line)["type"] in ("hello", "frame")
        return
    with pytest.raises(P.ProtocolError) as exc:
        P.decode(line)
    assert exc.value.code == code


def test_size_limit_boundary():
    base = b'{"type":"ping","id":"x"}'
    line = base[:-1] + b" " * (P.MAX_MESSAGE - len(base) - 1) + b"}\n"
    assert len(line) == P.MAX_MESSAGE == P.MAX_TEXT + 1
    assert P.decode(line) == {"type": "ping", "id": "x"}
    assert P.decode(line[:-1]) == {"type": "ping", "id": "x"}   # WS: no LF
    for over in (b" " + line, b" " + line[:-1]):
        with pytest.raises(P.ProtocolError) as exc:
            P.decode(over)
        assert exc.value.code == "too_large" and exc.value.fatal
    with pytest.raises(P.ProtocolError) as exc:
        P.encode(P.make_error("x", "y" * P.MAX_MESSAGE))
    assert exc.value.code == "too_large"


def _grid(cell=(0, 0, 0), rows=17, cols=9):
    return [[list(cell) for _ in range(cols)] for _ in range(rows)]


def _poke(value):
    g = _grid()
    g[5][4] = value
    return g


BAD_ROWS = {
    "16 rows": _grid(rows=16), "18 rows": _grid(rows=18),
    "8 cols": _grid(cols=8), "10 cols": _grid(cols=10),
    "pair": _poke([0, 0]), "quad": _poke([0, 0, 0, 0]),
    "256": _poke([256, 0, 0]), "negative": _poke([0, -1, 0]),
    "float": _poke([0, 0, 1.5]), "bool": _poke([True, 0, 0]),
    "string": _poke(["0", 0, 0]), "null cell": _poke(None),
    "null rows": None, "object rows": {"0": 1}, "string rows": "x" * 17,
}


@pytest.mark.parametrize("name", sorted(BAD_ROWS))
def test_frame_validation(name):
    rows = BAD_ROWS[name]
    with pytest.raises(P.ProtocolError) as exc:
        P.validate_rows(rows)
    assert exc.value.code == "bad_frame"
    with pytest.raises(P.ProtocolError) as exc:
        P.decode(json.dumps({"type": "frame", "frame_no": 0, "rows": rows}))
    assert exc.value.code == "bad_frame"


def test_loopback_detection():
    assert _is_loopback("127.0.0.1") and _is_loopback("::1")
    assert _is_loopback("localhost")
    assert not _is_loopback("0.0.0.0") and not _is_loopback("192.0.2.7")


# ------------------------------------------------------------ engine mode

def test_server_binds_loopback_by_default(transport):
    async def go():
        server = EngineServer()
        try:
            host, _port = await server.start(port=0, transport=transport)
        finally:
            await server.close()
        return host
    assert P.DEFAULT_HOST == "127.0.0.1"
    assert run(go()) == loopback_address()


def test_lockstep_matches_direct_engine_run(transport):
    rec, final = record(TetrisAnimation(7), 900,
                        Bot(pace=3, think=6, batch_shifts=False))
    assert rec.events, "the bot played"
    got, states, _ = run(lockstep_run(7, 900, rec.events, transport))
    assert [m["frame_no"] for m in got] == list(range(900))
    digests = [m["digest"] for m in got]
    assert digests == rec.digests()
    assert digests == run_trace({"seed": 7, "frames": 900, "digest_every": 1,
                                 "events": rec.events})["digests"]
    assert all(frame_digest(m["rows"]) == m["digest"] for m in got)
    assert got[-1]["rows"] == rec.frames[-1]
    obs, last = observe(final), states[-1]
    assert [last[k] for k in ("score", "level", "lines", "high_score", "phase")] \
        == [obs[k] for k in ("score", "level", "lines", "high_score", "phase")]
    assert states[0]["frame_no"] == 0 and states[0]["phase"] == "countdown"


@pytest.mark.parametrize("path", TRACES, ids=lambda p: p.stem)
def test_lockstep_reproduces_sealed_trace(path, transport):
    trace = json.loads(path.read_text())
    n, every = trace["frames"], trace["digest_every"]
    got, _, _ = run(lockstep_run(trace["seed"], n, trace["events"], transport))
    picked = [m["digest"] for m in got
              if m["frame_no"] % every == 0 or m["frame_no"] == n - 1]
    assert picked == trace["digests"]
    last = bytes(v for row in got[-1]["rows"] for rgb in row for v in rgb)
    assert last.hex() == trace["final"]["frame_hex"]


@pytest.mark.parametrize("ctl,viewer", [("tcp", "tcp"), ("ws", "ws"),
                                        ("tcp", "ws"), ("ws", "tcp")])
def test_viewer_receives_the_controller_stream(ctl, viewer):
    if "ws" in (ctl, viewer) and importlib.util.find_spec("websockets") is None:
        pytest.skip("websockets is not installed")
    rec, _ = record(TetrisAnimation(11), 200, Bot())
    got, _, seen = run(lockstep_run(11, 200, rec.events, ctl, viewer))
    assert [m["digest"] for m in seen] == [m["digest"] for m in got] \
        == rec.digests()


def test_realtime_session_is_a_replayable_trace(transport):
    script = {95: [("left", True), ("left", False)],
              110: [("rotate_cw", True), ("rotate_cw", False)],
              130: [("hold", True), ("hold", False)],
              150: [("hard_drop", True), ("hard_drop", False)],
              170: [("rotate_180", True)],
              175: [("rotate_180", False), ("soft_drop", True)]}

    async def go():
        server = EngineServer(seed=3, clock="realtime", fps=600)
        url = await start(server, transport)
        try:
            ctl = await connect(url, "controller")
            got = []
            while len(got) < 240:
                msg = await ctl.recv(20)
                if msg["type"] == "frame":
                    got.append(msg)
                    await ctl.send(*(P.make_event(action, down) for action, down
                                     in script.pop(msg["frame_no"], ())))
            await ctl.close()
            await until(lambda: server.sessions)
            return got, server.sessions[0]
        finally:
            await server.close()

    got, session = run(go())
    assert {e[1] for e in session.log} >= {"left", "rotate_cw", "hold",
                                           "hard_drop"}
    # The frames publish the log (§4.2: the realtime stamps ride on E_k).
    assert [[m["frame_no"], a, d] for m in got for a, d in m["events"]] == \
        [e for e in session.log if e[0] < len(got)]
    replay = run_trace({"seed": 3, "frames": session.k, "digest_every": 1,
                        "events": session.log})["digests"]
    assert [m["digest"] for m in got] == replay[:len(got)]
    trace = session.trace()
    assert trace["format"] == "17x9-tetris-trace" and trace["digests"] == replay


def test_realtime_paces_at_30_fps(transport):
    async def go():
        server = EngineServer(seed=1)
        url = await start(server, transport)
        try:
            ctl = await connect(url, "controller")
            t0, n = time.monotonic(), 0
            while n < 16:
                if (await ctl.recv(20))["type"] == "frame":
                    n += 1
            return time.monotonic() - t0
        finally:
            await server.close()
    assert run(go()) >= 15 / 30 - 0.05


def test_engine_protocol_errors(transport):
    async def go():
        server = EngineServer(seed=1, clock="lockstep")
        url = await start(server, transport)
        out = {}
        try:
            c = await connect(url)
            await c.send(P.make_event("left", True))
            out["no hello"] = await c.error_then_closed(20)
            c = await connect(url)
            await c.raw(b'{"type":"hello","protocol":"17x9-tetris-remote",'
                        b'"version":9,"role":"controller"}\n')
            out["version"] = await c.error_then_closed(20)
            c = await connect(url)
            await c.send(P.make_hello("producer"))
            out["role"] = await c.error_then_closed(20)
            c = await connect(url)
            await c.raw(HELLO + b'"role":"controller","seed":-5}\n')
            out["seed"] = await c.error_then_closed(20)
            ctl = await connect(url, "controller")
            c = await connect(url)
            await c.send(P.make_hello("controller"))
            out["busy"] = await c.error_then_closed(20)
            viewer = await connect(url, "viewer")
            await viewer.send(P.make_event("left", True), P.make_tick(1))
            out["viewer"] = [m["code"] for m in await viewer.until_pong(timeout=20)
                             if m["type"] == "error"]
            await ctl.raw(b"garbage\n")
            await ctl.raw(b'{"type":"teleport"}\n')
            await ctl.raw(b'{"type":"event","action":"jump","down":true}\n')
            await ctl.send(P.make_frame(0, BLACK))
            out["controller"] = [m["code"] for m in await ctl.until_pong(timeout=20)
                                 if m["type"] == "error"]
            await ctl.send(P.make_tick(1))
            out["still playing"] = (await ctl.expect("frame", 20))["frame_no"]
            big = await connect(url, "viewer")
            await big.raw(b"x" * (P.MAX_MESSAGE + 100))
            if transport == "tcp":
                out["too large"] = await big.error_then_closed(20)
            else:  # the WebSocket library enforces max_size: close 1009
                # (joining mid-session, the viewer first gets the state, §4.7)
                seen = []
                while (msg := await big.recv(20)) is not None:
                    seen.append(msg["type"])
                out["too large"] = (seen, big.close_code)
            many = await connect(url, "viewer")
            for _ in range(MAX_ERRORS):
                await many.raw(b"nope\n")
            codes = []
            while (msg := await many.recv(20)) is not None:
                if msg["type"] == "error":
                    codes.append(msg["code"])
            out["many"] = codes
        finally:
            await server.close()
        return out

    out = run(go())
    assert out["no hello"] == ("hello_required", True)
    assert out["version"] == ("version", True)
    assert out["role"] == ("role", True)
    assert out["seed"] == ("bad_hello", True)
    assert out["busy"] == ("busy", True)
    assert out["viewer"] == ["forbidden", "forbidden"]
    assert out["controller"] == ["malformed", "unknown_type", "bad_event",
                                 "forbidden"]
    assert out["still playing"] == 0
    assert out["too large"] == (("too_large", True) if transport == "tcp"
                                else (["state"], 1009))
    assert out["many"] == ["malformed"] * MAX_ERRORS + ["too_many_errors"]


def test_tick_needs_the_lockstep_clock(transport):
    async def go():
        server = EngineServer(seed=1)
        url = await start(server, transport)
        try:
            ctl = await connect(url, "controller")
            await ctl.send(P.make_tick(1))
            return [m for m in await ctl.until_pong(timeout=20)
                    if m["type"] == "error"]
        finally:
            await server.close()
    errors = run(go())
    assert [e["code"] for e in errors] == ["forbidden"]
    assert "lockstep" in errors[0]["message"]


def test_connection_limit(transport):
    async def go():
        server = DisplayServer(pace=False)
        url = await start(server, transport)
        try:
            conns = [await connect(url, "viewer") for _ in range(MAX_CLIENTS)]
            extra = await connect(url)
            result = await extra.error_then_closed(20)
            for c in conns:
                await c.close()
            return result
        finally:
            await server.close()
    assert run(go()) == ("busy", True)


async def _lazy_viewer(url):
    """A viewer that says hello and then never reads, on a socket with a
    small receive buffer so the server's output backs up quickly."""
    transport, host, port, _path, uri = parse_url(url)
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.setblocking(False)
    await asyncio.get_running_loop().sock_connect(sock, (host, port))
    hello = P.encode(P.make_hello("viewer"))
    if transport == "tcp":
        _reader, writer = await asyncio.open_connection(sock=sock)
        writer.write(hello)
        return writer
    from websockets.asyncio.client import connect as ws_connect
    ws = await ws_connect(uri, sock=sock, compression=None, max_queue=1,
                          subprotocols=[P.WS_SUBPROTOCOL])
    await ws.send(hello[:-1].decode())
    return ws


def test_a_viewer_that_stops_reading_is_disconnected(transport, monkeypatch):
    """§7: a client whose unread output passes the limit is dropped, and
    stops counting against the connection limit; the session goes on."""
    monkeypatch.setattr(server_module, "MAX_WRITE_BUFFER", 1 << 16)

    async def go():
        server = EngineServer(seed=1, fps=300)
        url = await start(server, transport)
        try:
            ctl = await connect(url, "controller")

            async def read_all():
                while await ctl.recv(20) is not None:
                    pass

            reader = asyncio.ensure_future(read_all())
            lazy = await _lazy_viewer(url)
            await until(lambda: len(server.clients) == 2)
            await until(lambda: len(server.clients) == 1, timeout=60)
            k = server.session.k
            await until(lambda: server.session.k > k + 30)
            reader.cancel()
            # Drop it: a close handshake would wait on a reader that never
            # reads (StreamWriter and websocket connection alike).
            lazy.transport.abort()
            return server.controller is not None
        finally:
            await server.close()
    assert run(go())


# ----------------------------------------------------------- display mode

def test_display_mode_validates_relays_and_renders(transport):
    f0, f1, f2, f3 = sample_frame(1), WHITE, sample_frame(2), BLACK

    async def go():
        sink = Recorder()
        server = DisplayServer(sinks=[sink], pace=False)
        url = await start(server, transport)
        try:
            viewer = await connect(url, "viewer")
            prod = await connect(url, "producer")
            assert prod.hello["mode"] == "display"
            await prod.send(P.make_state(300, 2, 5, phase="playing"))
            await prod.send(P.make_frame(0, f0))
            wire = {"type": "frame", "frame_no": 1,
                    "rows": [[list(c) for c in row] for row in f1]}
            await prod.raw(json.dumps(wire).encode() + b"\n")    # no digest
            await prod.raw(frame_line(frame_no=2, rows=_grid(rows=16)) + b"\n")
            await prod.raw(frame_line(frame_no=2, rows=_poke([300, 0, 0])) + b"\n")
            await prod.send(dict(P.make_frame(2, f2), digest=BLACK_DIGEST))
            await prod.send(P.make_frame(1, f2))                # not increasing
            await prod.send(P.make_frame(5, f3))                # gap: fine
            await prod.send(P.make_event("left", True))
            errors = [m["code"] for m in await prod.until_pong(timeout=20)
                      if m["type"] == "error"]
            relayed = []
            while sum(m["type"] == "frame" for m in relayed) < 3:
                relayed.append(await viewer.recv(20))
            second = await connect(url)
            await second.send(P.make_hello("producer"))
            busy = await second.error_then_closed(20)
            ctl = await connect(url)
            await ctl.send(P.make_hello("controller"))
            role = await ctl.error_then_closed(20)
            return sink, errors, relayed, busy, role
        finally:
            await server.close()

    sink, errors, relayed, busy, role = run(go())
    assert errors == ["bad_frame", "bad_frame", "digest", "bad_frame",
                      "forbidden"]
    assert sink.frames == [f0, f1, f3]
    assert sink.info[0] == {"score": 300, "level": 2, "lines": 5, "high": 0,
                            "phase": "playing"}
    assert relayed[0]["type"] == "state" and relayed[0]["score"] == 300
    assert [m["digest"] for m in relayed if m["type"] == "frame"] == \
        [frame_digest(f) for f in (f0, f1, f3)]
    assert busy == ("busy", True) and role == ("role", True)


def test_display_paces_at_30_fps(transport):
    async def go():
        sink = Recorder()
        server = DisplayServer(sinks=[sink])
        url = await start(server, transport)
        try:
            prod = await connect(url, "producer")
            t0 = time.monotonic()
            await prod.send(*(P.make_frame(k, sample_frame(k)) for k in range(10)))
            await prod.until_pong(timeout=20)
            return time.monotonic() - t0, len(sink), server.shown
        finally:
            await server.close()
    elapsed, recorded, shown = run(go())
    assert recorded == shown == 10
    assert elapsed >= 9 / 30 - 0.03


def test_html_sink_writes_a_replay(tmp_path):
    path = tmp_path / "wall.html"
    sink = HtmlSink(str(path), {"title": "display server"})
    for frame in (BLACK, sample_frame()):
        sink.send(frame, None)
    sink.close()
    text = path.read_text()
    assert "canvas" in text and "display server" in text


def test_display_sink_drives_a_legacy_display():
    name = "legacy_display_for_server_test"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "impl" / "python" / "legacy" / "utilities" / "display.py")
    legacy = importlib.util.module_from_spec(spec)
    sys.modules[name] = legacy
    spec.loader.exec_module(legacy)

    class Wall(legacy.Display):
        def __init__(self):
            self.sent = []

        def send(self, frame):
            self.sent.append(frame)

        def makeframe(self):
            return legacy.Frame()

    wall, frame = Wall(), sample_frame()
    sink = DisplaySink(wall)
    assert sink.legacy
    sink.send(frame)
    got = wall.sent[0]
    assert all((got[r, c].r, got[r, c].g, got[r, c].b) == frame[r][c]
               for r in range(17) for c in range(9))

    duck = load_display("tetris_sim.recorder:Recorder")
    plain = DisplaySink(duck)
    assert not plain.legacy
    plain.send(frame)
    assert duck.frames == [frame]
    with pytest.raises(ValueError):
        load_display("no-colon")


# -------------------------------------------------------------------- CLI

def test_cli_engine_lockstep_and_trace_out(tmp_path):
    trace_path = tmp_path / "session.json"
    proc, (url,) = start_cli("--mode", "engine", "--port", "0", "--clock",
                             "lockstep", "--seed", "5", "--trace-out",
                             str(trace_path))

    async def go():
        ctl = await connect(url, "controller")
        assert (ctl.hello["mode"], ctl.hello["seed"], ctl.hello["clock"]) == \
            ("engine", 5, "lockstep")
        await ctl.send(P.make_tick(95), P.make_event("right", True),
                       P.make_event("right", False),
                       P.make_event("hard_drop", True), P.make_tick(30))
        got = []
        while len(got) < 125:
            msg = await ctl.recv(20)
            if msg["type"] == "frame":
                got.append(msg)
        await ctl.close()
        return got

    try:
        assert url == f"tcp://{loopback_address()}:{url.rsplit(':', 1)[1]}"
        got = run(go())
    finally:
        code, err = stop_cli(proc)
    assert code == 0, err
    trace = json.loads(trace_path.read_text())
    assert (trace["seed"], trace["frames"]) == (5, 125)
    assert trace["events"] == [[95, "right", True], [95, "right", False],
                               [95, "hard_drop", True]]
    assert trace["digests"] == [m["digest"] for m in got]
    assert "1 session(s), 125 frames" in err


def test_cli_display_once_writes_html(tmp_path, transport):
    out = tmp_path / "wall.html"
    proc, (url,) = start_cli("--mode", "display", "--transport", transport,
                             "--port", "0", "--html", str(out), "--once")

    async def go():
        prod = await connect(url, "producer")
        await prod.send(*(P.make_frame(k, f) for k, f in
                          enumerate([BLACK, WHITE, sample_frame()])))
        await prod.until_pong(timeout=20)
        await prod.close()

    try:
        assert url.startswith(f"{transport}://")
        run(go())
        proc.wait(timeout=60)
    finally:
        code, err = stop_cli(proc)
    assert code == 0, err
    assert "3 frames shown" in err and out.exists()


def test_cli_help_mentions_loopback_and_transports():
    result = subprocess.run([sys.executable, "-m", "tetris_sim.server", "--help"],
                            env=ENV, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0 and "127.0.0.1" in result.stdout
    for word in ("--transport", "--ws-port", "--unix", "--ws-origin", "jail"):
        assert word in result.stdout


@pytest.mark.parametrize("args", [["--transport", "tcp", "--ws-port", "5"],
                                  ["--transport", "both", "--unix", "/x.sock"]])
def test_cli_rejects_inconsistent_listeners(args):
    result = subprocess.run([sys.executable, "-m", "tetris_sim.server",
                             "--mode", "engine", *args], env=ENV,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 2 and "error:" in result.stderr
