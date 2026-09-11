"""check_session.py on a session the demo relay records, and on tampered
copies of it, which the checker must fail (verify the verifier).

    cd contrib/displays && python -m pytest contract/test_check_session.py
"""
import asyncio
import copy
import json
import socket

import pytest
from demo.relay import Relay
from websockets.asyncio.client import connect

from contract import check_session
from contract import display_contract as dc

GB, N = "green-building", 9 * 17


async def next_control(ws, op, where=None):
    while True:
        m = await asyncio.wait_for(ws.recv(), 4)
        if isinstance(m, str) and m.startswith("{"):
            msg = json.loads(m)
            if msg.get("op") == op and (where is None or where(msg)):
                return msg


async def next_frame(ws):
    while True:
        m = await asyncio.wait_for(ws.recv(), 4)
        if not (isinstance(m, str) and m.startswith("{")):
            return m


async def scenario(url, relay):
    """Every rule once: views, busy, not-holder, bad frames, rate, sequence,
    hex fan-out, rgb24, expiry, UDP, unknown and malformed control."""
    async def conn(msg=None):
        ws = await connect(url)
        if msg:
            await ws.send(json.dumps(msg))
        return ws

    v = await conn({"op": "view", "display": GB})
    vt = await conn({"op": "view", "display": "tetris"})
    vb = await conn({"op": "view", "display": "blinkenlights"})
    for ws in (v, vt, vb):
        await next_control(ws, "lease")
    a = await conn({"op": "reserve", "name": "a", "display": GB, "ttl": 1})
    await next_control(a, "granted")
    b = await conn({"op": "reserve", "name": "b", "display": GB})
    await next_control(b, "busy")
    await b.send(bytes([7]) * N)
    await next_control(b, "error")
    picture = bytes(i % 16 for i in range(N))
    await a.send(b"\x00\x05" + picture)
    await a.send(bytes([1]) * N)                       # too soon: rate
    await next_control(a, "error")
    for bad in (bytes(N - 1), bytes([16]) * N, "g" + dc.encode_hex(bytes(N), 9, 17)[1:]):
        await a.send(bad)
        await next_control(a, "error")
    await asyncio.sleep(0.05)
    await a.send(b"\x00\x04" + bytes([2]) * N)         # lower sequence: rate
    await next_control(a, "error")
    await asyncio.sleep(0.05)
    await a.send(dc.encode_hex(bytes([3]) * N, 9, 17))
    await a.send(json.dumps({"op": "reserve", "name": "a", "display": GB, "ttl": 1,
                             "format": "rgb24"}))
    g = await next_control(a, "granted")
    await asyncio.sleep(0.05)
    await a.send(dc.encode_rgb24(picture, g["palette"]))
    await next_control(v, "lease", lambda m: m["holder"] is None)   # expiry, then black
    await next_frame(v)
    t = await conn({"op": "reserve", "name": "t", "display": "tetris", "format": "hex"})
    await next_control(t, "granted")
    for k in range(3):
        await t.send(dc.encode_hex(bytes([k]) * 200, 10, 20))
        await asyncio.sleep(0.05)
    await t.send(json.dumps({"op": "release"}))
    for text in ('{"op":"dance"}', "{oops", '{"op":"view","display":"nowhere"}',
                 '{"op":"renew"}'):
        await b.send(text)
        await next_control(b, "error")
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.sendto(dc.encode_blp(18, 8, [1, 0] * 72), relay.udp_addr)
    await next_frame(vb)
    await next_control(vb, "lease", lambda m: m["holder"] is None)
    await next_frame(vb)
    u.close()
    for ws in (a, b, t, v, vt, vb):
        await ws.close()
    await asyncio.sleep(0.1)


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    path = tmp_path_factory.mktemp("session") / "session.jsonl"

    async def go():
        relay = Relay(fanout={"tetris": "hex"}, udp_ttl=1, record=path)
        async with relay.serve(udp_port=0) as url:
            await scenario(url, relay)
    asyncio.run(asyncio.wait_for(go(), 40))
    records, bad = check_session.load(path)
    assert bad == [] and records[0]["kind"] == "meta"
    return records


def test_a_recorded_session_passes(session):
    assert check_session.check(copy.deepcopy(session)) == []
    kinds = {r["kind"] for r in session}
    assert {"meta", "open", "close", "text", "binary", "udp"} <= kinds


def test_the_script_runs(session, tmp_path, capsys):
    path = tmp_path / "s.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in session))
    assert check_session.main([str(path)]) == 0
    assert "0 findings" in capsys.readouterr().err


def payload(r):
    p = r.get("payload")
    return json.loads(p) if r["kind"] == "text" and isinstance(p, str) and p.startswith("{") else p


def first(records, pred):
    return next(i for i, r in enumerate(records) if pred(r))


def viewer_of(records, display):
    return next(r["conn"] for r in records if r["dir"] == "in" and r["kind"] == "text"
                and payload(r) == {"op": "view", "display": display})


def is_op(r, op, direction="out", **kv):
    p = payload(r)
    return (r["dir"] == direction and isinstance(p, dict) and p.get("op") == op
            and all(p.get(k) == v for k, v in kv.items()))


def tamper(session, how):
    rs = copy.deepcopy(session)
    v = viewer_of(rs, GB)
    if how == "a non-holder's frame fanned out":
        i = first(rs, lambda r: r["conn"] == v and r["kind"] == "binary")
        rs.insert(i, {**rs[i], "payload": (bytes([7]) * N).hex()})
    elif how == "the frame after expiry not black":
        i = first(rs, lambda r: r["conn"] == v and r["kind"] == "binary"
                  and set(bytes.fromhex(r["payload"])) == {0})
        rs[i]["payload"] = (bytes([1]) + bytes(N - 1)).hex()
    elif how == "an old error reason":
        i = first(rs, lambda r: is_op(r, "error", reason="not-holder"))
        rs[i]["payload"] = json.dumps({"op": "error", "reason": "not holder"})
    elif how == "a rate drop left out":
        del rs[first(rs, lambda r: is_op(r, "error", reason="rate"))]
    elif how == "a second holder":
        i = first(rs, lambda r: is_op(r, "busy"))
        rs[i]["payload"] = json.dumps({"op": "granted", "lease": "x", "w": 9, "h": 17, "fps": 30,
                                       "format": "pal16", "palette": dc.palette("cga"),
                                       "expires": int(rs[i]["t"]) + 300})
    elif how == "a fan-out frame one byte short":
        i = first(rs, lambda r: r["conn"] == v and r["kind"] == "binary")
        rs[i]["payload"] = rs[i]["payload"][:-2]
    elif how == "a not-holder reply left out":
        del rs[first(rs, lambda r: is_op(r, "error", reason="not-holder"))]
    elif how == "caps with the wrong palette":
        i = first(rs, lambda r: is_op(r, "caps", display=GB))
        rs[i]["payload"] = json.dumps({**payload(rs[i]), "palette": dc.palette("c64")})
    elif how == "an unknown op answered bad-format":
        i = first(rs, lambda r: is_op(r, "error", reason="unknown-op"))
        rs[i]["payload"] = json.dumps({"op": "error", "reason": "bad-format"})
    elif how == "a lower sequence accepted":
        i = first(rs, lambda r: r["dir"] == "in" and r["kind"] == "binary"
                  and r["payload"].startswith("0004"))
        del rs[i + 1]                                  # its rate error
    return rs


@pytest.mark.parametrize("how", [
    "a non-holder's frame fanned out", "the frame after expiry not black", "an old error reason",
    "a rate drop left out", "a second holder", "a fan-out frame one byte short",
    "a not-holder reply left out", "caps with the wrong palette",
    "an unknown op answered bad-format", "a lower sequence accepted"])
def test_the_checker_fails_a_tampered_session(session, how):
    findings = check_session.check(tamper(session, how))
    assert findings, how
