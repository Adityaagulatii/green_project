"""The reservation workstream's dlk1 vectors, sent to the demo relay.

/scratch/work/tetris-parallel/inputs/dlk1-vectors.json (display-reservation
2970f63, `bb dlk1:vectors`, never edited by hand) says, for each vector: a
relay holding exactly `secrets`, whose default display is `relay_default`,
receives `reserve` at unix time `now`.  expect.ok true: it is granted, the
holder shown is expect.holder and granted.expires is expect.expires.
expect.ok false: it answers expect.error.  Each vector gets a fresh relay
whose clock reads `now`.

    cd contrib/displays && python -m pytest demo/test_dlk1_vectors.py
"""
import asyncio
import json
import os

import pytest
from websockets.asyncio.client import connect

from demo.relay import Relay

VECTORS = "/scratch/work/tetris-parallel/inputs/dlk1-vectors.json"
DOC = json.load(open(VECTORS, encoding="utf-8")) if os.path.exists(VECTORS) else None
pytestmark = pytest.mark.skipif(DOC is None, reason=f"{VECTORS} not published yet")


def reply_to(vector):
    secrets = {s["kid"]: bytes.fromhex(s["hex"]) for s in DOC["secrets"]}

    async def go():
        relay = Relay(lease_secrets=secrets, default=vector["relay_default"],
                      clock=lambda: vector["now"])
        async with relay.serve() as url:
            async with connect(url) as ws:
                await ws.send(json.dumps(vector["reserve"]))
                reply = json.loads(await asyncio.wait_for(ws.recv(), 5))
                name = vector["reserve"].get("display") or vector["relay_default"]
                holder = relay.displays[name].holder_name if name in relay.displays else None
                return reply, holder
    return asyncio.run(asyncio.wait_for(go(), 15))


@pytest.mark.parametrize("vector", (DOC or {}).get("vectors", []), ids=lambda v: v["name"])
def test_the_relay_answers_each_vector(vector):
    reply, holder = reply_to(vector)
    e = vector["expect"]
    if e["ok"]:
        assert reply["op"] == "granted" and reply["expires"] == e["expires"], reply
        assert holder == e["holder"]
    else:
        assert reply == e["error"]
