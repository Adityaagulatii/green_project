"""A display source: reserve a display on a relay and send a demo's frames.

The producers yield palette indices, so pal16 is the native format: the frame
is the indices themselves.  hex sends the same indices as text; rgb24 sends
each index as its colour in the granted palette, for the relay to quantize
back (spec v0.2.1: rgb24 is accepted at the relay only)."""
import asyncio
import collections
import json
import time
from urllib.parse import urlsplit

from contract import display_contract as dc
from websockets.asyncio.client import connect

from .producers import DEMOS

LOOPBACK = ("127.0.0.1", "::1", "localhost")
FORMATS = dc.SOURCE_FORMATS


def check_url(url):
    """Refuse anything but a ws:// URL on loopback, such as wss://wal.sh."""
    u = urlsplit(url)
    if u.scheme != "ws" or u.hostname not in LOOPBACK:
        raise SystemExit(f"refusing {url}: the demo talks only to a local mock relay "
                         "(ws://127.0.0.1:PORT/tools/display/ws)")


def encoder(fmt, w, h, palette, seq):
    """frame, n -> the wire message for FMT (with the 2-byte sequence N if SEQ)."""
    if fmt == "hex":
        if seq:
            raise ValueError("a hex frame has no sequence prefix; --seq needs pal16 or rgb24")
        return lambda frame, n: dc.encode_hex(frame, w, h)
    if fmt == "rgb24":
        return lambda frame, n: dc.encode_rgb24(frame, palette, n if seq else None)
    if fmt == "pal16":
        return lambda frame, n: dc.encode_pal16(frame, n if seq else None)
    raise ValueError(f"format {fmt!r}, want one of {FORMATS}")


async def run(url, display, demo, *, name="demo@jail", ttl=60, frames=None, seed=0,
              seq=False, fps=None, fmt="pal16"):
    """Reserve DISPLAY in format FMT and send FRAMES frames of DEMO (forever if
    None), paced at the display's fps (or FPS, if lower), then release.
    Return a summary dict, or the relay's reply if the reserve was not granted."""
    check_url(url)
    async with connect(url) as ws:
        reserve = {"op": "reserve", "name": name, "ttl": ttl, "format": fmt}
        if display is not None:
            reserve["display"] = display
        await ws.send(json.dumps(reserve))
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
        w, h = reply["w"], reply["h"]
        encode = encoder(fmt, w, h, reply["palette"], seq)
        frames_of = DEMOS[demo](w, h, seed)
        period = 1.0 / min(fps or reply["fps"], reply["fps"])
        sent = 0
        try:
            while frames is None or sent < frames:
                # off the event loop: in `run` mode the relay shares it
                frame = await asyncio.to_thread(next, frames_of)
                t = time.monotonic()
                await ws.send(encode(frame, sent))
                sent += 1
                # Pace from this send, never catching up in a burst: the relay
                # drops frames that come faster than fps.
                await asyncio.sleep(max(0.0, t + period - time.monotonic()))
            await ws.send(json.dumps({"op": "release"}))
            await asyncio.sleep(0.05)  # let late errors arrive
        finally:
            reader.cancel()
        return {"op": "done", "display": display, "demo": demo, "format": fmt, "w": w,
                "h": h, "sent": sent, "errors": dict(errors)}
