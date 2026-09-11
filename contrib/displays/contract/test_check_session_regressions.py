"""check_session regressions from experiment 002's harness
(requests/reservation-displays-20260911T1339Z-lease-keys-findings.md).

1. A viewer that joins after a lease has ended (a ttl expiry, or a dlk1 key's
   exp) gets `lease` holder null in answer to its view. It owes no black
   frame, and the next holder's frames are good. The harness's timeline on
   green-building: the cut at +45.394 (lease null and black to c1); c19
   views at +53.116 (caps, lease null); c20 is granted at +53.279; c19 gets
   c20's frames from +54.635, which were flagged "not black".
2. A holder whose connection drops is a close, not an early expiry: the
   relay records the close before the lease-null it causes.

    cd contrib/displays && python -m pytest contract/test_check_session_regressions.py
"""
import asyncio
import json
import time

import pytest
from demo.relay import Relay
from websockets.asyncio.client import connect

from contract import check_session, dlk1

GB, N = "green-building", 9 * 17
SECRETS = {dlk1.TEST_KID: dlk1.TEST_SECRET}


def key(exp, sub):
    now = int(time.time())
    return dlk1.sign({"v": 1, "kid": "test", "iss": "dres", "sub": sub, "rid": "r", "display": GB,
                      "nbf": now - 10, "exp": exp, "fmt": ["pal16"], "jti": sub}, dlk1.TEST_SECRET)


async def control(ws, op, where=None):
    while True:
        m = await asyncio.wait_for(ws.recv(), 8)
        if isinstance(m, str) and m.startswith("{"):
            msg = json.loads(m)
            if msg.get("op") == op and (where is None or where(msg)):
                return msg


async def frame(ws):
    while True:
        m = await asyncio.wait_for(ws.recv(), 8)
        if not (isinstance(m, str) and m.startswith("{")):
            return m


async def view(url):
    ws = await connect(url)
    await ws.send(json.dumps({"op": "view", "display": GB}))
    await control(ws, "caps")
    return ws, await control(ws, "lease")


async def reserve(url, **kw):
    ws = await connect(url)
    await ws.send(json.dumps({"op": "reserve", "name": kw.pop("name", "s"), "display": GB} | kw))
    assert (await control(ws, "granted"))["op"] == "granted"
    return ws


def recorded(tmp_path, keys, scenario):
    rec = tmp_path / "session.jsonl"

    async def go():
        relay = Relay(record=rec, lease_secrets=SECRETS if keys else None)
        async with relay.serve() as url:
            await scenario(url)
        await asyncio.sleep(0)
    asyncio.run(asyncio.wait_for(go(), 30))
    return check_session.load(rec)[0]


@pytest.mark.parametrize("keys", [False, True], ids=["ttl-expiry", "dlk1-exp"])
def test_a_viewer_that_joins_after_the_cut_owes_no_black_frame(tmp_path, keys):
    async def scenario(url):
        c1, _ = await view(url)
        first = await (reserve(url, ttl=60, key=key(int(time.time()) + 2, "first")) if keys
                       else reserve(url, ttl=1))
        await first.send(bytes([3]) * N)
        await frame(c1)
        await control(c1, "lease", lambda m: m["holder"] is None)       # the cut
        assert await frame(c1) == bytes(N)                                 # c1's black frame
        await asyncio.sleep(0.5)
        c19, lease = await view(url)                                       # joins after the cut
        assert lease["holder"] is None
        c20 = await (reserve(url, ttl=60, key=key(int(time.time()) + 60, "alice")) if keys
                     else reserve(url, name="alice", ttl=60))
        assert (await control(c19, "lease", lambda m: m["holder"]))["holder"] == "alice"
        await c20.send(bytes([7]) * N)
        assert await frame(c19) == bytes([7]) * N
        for ws in (c1, first, c19, c20):
            await ws.close()
        await asyncio.sleep(0.1)
    records = recorded(tmp_path, keys, scenario)
    assert check_session.check(records, lease_keys=keys) == []


def test_a_dropped_holder_is_a_close_not_an_early_expiry(tmp_path):
    async def scenario(url):
        v, _ = await view(url)
        s = await reserve(url, ttl=60)
        await s.send(bytes([2]) * N)
        await frame(v)
        s.transport.abort()                                                # 1006, no close frame
        await control(v, "lease", lambda m: m["holder"] is None)
        await v.close()
        await asyncio.sleep(0.1)
    records = recorded(tmp_path, False, scenario)
    closes = [r for r in records if r["kind"] == "close" and r["dir"] == "in"]
    assert closes and check_session.check(records) == []
