"""The relay's lease-secret mode (dlk1, experiment 002): every reserve needs a
signed key, the holder is the key's sub, and the lease ends at exp.

    cd contrib/displays && python -m pytest demo/test_lease_keys.py
"""
import asyncio
import json
import socket
import time

from contract import check_session, dlk1
from contract import display_contract as dc
from websockets.asyncio.client import connect

from demo.relay import Relay

SECRETS = {dlk1.TEST_KID: dlk1.TEST_SECRET}
GB, N = "green-building", 9 * 17


def key(display=GB, *, nbf=None, exp=None, fmt=("pal16", "hex"), sub="alice@lab",
        secret=dlk1.TEST_SECRET, kid="test"):
    now = int(time.time())
    return dlk1.sign({"v": 1, "kid": kid, "iss": "dres", "sub": sub, "rid": "r-7",
                      "display": display, "nbf": now - 10 if nbf is None else nbf,
                      "exp": now + 600 if exp is None else exp, "fmt": list(fmt),
                      "jti": f"j-{time.monotonic_ns()}"}, secret)


def run(coro, timeout=30):
    return asyncio.run(asyncio.wait_for(coro, timeout))


async def text(ws, op=None, where=None, timeout=8):
    while True:
        m = await asyncio.wait_for(ws.recv(), timeout)
        if isinstance(m, str) and m.startswith("{"):
            msg = json.loads(m)
            if (op is None or msg.get("op") == op) and (where is None or where(msg)):
                return msg


async def frame(ws, timeout=8):
    while True:
        m = await asyncio.wait_for(ws.recv(), timeout)
        if not (isinstance(m, str) and m.startswith("{")):
            return m


async def reserve(url, k, display=GB, **kw):
    ws = await connect(url)
    msg = {"op": "reserve", "name": "ignored", "display": display, "ttl": 300} | kw
    if k is not None:
        msg["key"] = k
    await ws.send(json.dumps(msg))
    return ws, await text(ws)


def test_every_reserve_needs_a_valid_key():
    async def go():
        relay = Relay(lease_secrets=SECRETS)
        async with relay.serve() as url:
            now = int(time.time())
            v = await connect(url)                       # viewers need no key
            await v.send(json.dumps({"op": "view", "display": GB}))
            assert (await text(v, "caps"))["display"] == GB
            refusals = [
                (None, {}, "missing"), ("dlk1.x", {}, "malformed"),
                (key(kid="other"), {}, "unknown-kid"),
                (key(secret=bytes(32)), {}, "bad-signature"),
                (key(display="tetris"), {}, "wrong-display"),
                (key(nbf=now + 100, exp=now + 200), {}, "not-yet"),
                (key(nbf=now - 100, exp=now - 1), {}, "expired"),
                (key(), {"format": "rgb24"}, "format-not-allowed"),
            ]
            for k, extra, detail in refusals:
                ws, reply = await reserve(url, k, **extra)
                assert reply == {"op": "error", "reason": "unauthorized", "detail": detail}
                await ws.close()
            assert relay.displays[GB].holder is None
            assert relay.stats["unauthorized:missing"] == 1
            await v.close()
    run(go())


def test_the_holder_is_the_keys_sub_and_expires_is_capped():
    async def go():
        async with Relay(lease_secrets=SECRETS).serve() as url:
            v = await connect(url)
            await v.send(json.dumps({"op": "view", "display": GB}))
            await text(v, "lease")
            s, g = await reserve(url, key(sub="bob@lab"))
            assert g["op"] == "granted" and 298 <= g["expires"] - time.time() <= 301
            assert (await text(v, "lease", lambda m: m["holder"]))["holder"] == "bob@lab"
            await s.close()
            await text(v, "lease", lambda m: m["holder"] is None)
            exp = int(time.time()) + 100
            s, g = await reserve(url, key(exp=exp, fmt=["hex"]), format="hex")
            assert g["op"] == "granted" and g["expires"] == exp and g["format"] == "hex"
            await s.close()
            await v.close()
    run(go())


def test_the_lease_ends_at_exp_whatever_renews_it():
    async def go():
        async with Relay(lease_secrets=SECRETS).serve() as url:
            v = await connect(url)
            await v.send(json.dumps({"op": "view", "display": GB}))
            await text(v, "lease")
            exp = int(time.time()) + 2
            k = key(exp=exp)
            s, g = await reserve(url, k, ttl=60)
            assert g["expires"] == exp
            for n in range(3):                           # frames and renew: no extension
                await s.send(bytes([n + 1]) * N)
                await frame(v)
                await s.send(json.dumps({"op": "renew"}))
                await asyncio.sleep(0.3)
            await text(v, "lease", lambda m: m["holder"] is None)
            assert exp - 0.3 <= time.time() <= exp + 1.5
            assert await frame(v) == bytes(N)            # the black frame
            await s.send(bytes([5]) * N)
            assert await text(s, "error") == {"op": "error", "reason": "not-holder"}
            await s.send(json.dumps({"op": "renew"}))
            assert await text(s, "error") == {"op": "error", "reason": "not-holder"}
            again, reply = await reserve(url, k)
            assert reply == {"op": "error", "reason": "unauthorized", "detail": "expired"}
            for ws in (again, s, v):
                await ws.close()
    run(go())


def test_without_secrets_nothing_changes():
    async def go():
        async with Relay().serve() as url:
            s, g = await reserve(url, "dlk1.not.checked")
            assert g["op"] == "granted" and g["expires"] - time.time() > 290
            await s.close()
    run(go())


def test_udp_is_refused_in_secret_mode():
    async def go():
        relay = Relay(lease_secrets=SECRETS)
        async with relay.serve(udp_port=0) as url:
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            u.sendto(dc.encode_blp(18, 8, [1] * 144), relay.udp_addr)
            await asyncio.sleep(0.2)
            u.close()
            assert relay.displays["blinkenlights"].holder is None
            assert relay.stats["udp:unauthorized"] == 1 and url
    run(go())


def test_check_session_knows_lease_keys(tmp_path):
    rec = tmp_path / "keys.jsonl"

    async def go():
        async with Relay(lease_secrets=SECRETS, record=rec).serve() as url:
            v = await connect(url)
            await v.send(json.dumps({"op": "view", "display": GB}))
            await text(v, "lease")
            bad, _ = await reserve(url, None)
            exp = int(time.time()) + 2
            s, g = await reserve(url, key(exp=exp, sub="carol@lab"), ttl=60)
            await s.send(bytes([2]) * N)
            await frame(v)
            await text(v, "lease", lambda m: m["holder"] is None)
            await frame(v)
            for ws in (bad, s, v):
                await ws.close()
            await asyncio.sleep(0.1)
    run(go())
    records, _ = check_session.load(rec)
    assert check_session.check(records, lease_keys=True) == []
    plain = check_session.check(records)
    assert plain and any("unauthorized" in f for f in plain)
