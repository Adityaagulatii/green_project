"""A display source: reserve a display on a relay and send a demo's frames."""
import asyncio
import collections
import json
import time
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

from .producers import DEMOS, to_rgb

LOOPBACK = ("127.0.0.1", "::1", "localhost")


def check_url(url):
    """Refuse anything but a ws:// URL on loopback, such as wss://wal.sh."""
    u = urlsplit(url)
    if u.scheme != "ws" or u.hostname not in LOOPBACK:
        raise SystemExit(f"refusing {url}: the demo talks only to a local mock relay "
                         "(ws://127.0.0.1:PORT/tools/display/ws)")


async def run(url, display, demo, *, name="demo@jail", ttl=60, frames=None, seed=0,
              seq=False, fps=None):
    """Reserve DISPLAY and send FRAMES frames of DEMO (forever if None), paced
    at the display's fps (or FPS, if lower), then release.  Return a summary
    dict, or the relay's reply if the reserve was not granted."""
    check_url(url)
    async with connect(url) as ws:
        await ws.send(json.dumps({"op": "reserve", "name": name, "display": display,
                                  "ttl": ttl}))
        reply = json.loads(await ws.recv())
        if reply.get("op") != "granted":
            return reply
        errors = collections.Counter()

        async def read():
            async for message in ws:
                if isinstance(message, str):
                    msg = json.loads(message)
                    if msg.get("op") == "error":
                        errors[msg.get("reason")] += 1

        reader = asyncio.create_task(read())
        frames_of = DEMOS[demo](reply["w"], reply["h"], seed)
        period = 1.0 / min(fps or reply["fps"], reply["fps"])
        sent = 0
        try:
            while frames is None or sent < frames:
                # off the event loop: in `run` mode the relay shares it
                body = to_rgb(await asyncio.to_thread(next, frames_of))
                t = time.monotonic()
                await ws.send((sent & 0xFFFF).to_bytes(2, "big") + body if seq else body)
                sent += 1
                # Pace from this send, never catching up in a burst: the relay
                # drops frames that come faster than fps.
                await asyncio.sleep(max(0.0, t + period - time.monotonic()))
            await ws.send(json.dumps({"op": "release"}))
            await asyncio.sleep(0.05)  # let late errors arrive
        finally:
            reader.cancel()
        return {"op": "done", "display": display, "demo": demo, "w": reply["w"],
                "h": reply["h"], "sent": sent, "errors": dict(errors)}
