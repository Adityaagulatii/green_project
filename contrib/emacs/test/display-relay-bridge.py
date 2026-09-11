#!/usr/bin/env python3
"""A WebSocket bridge for the ERT test of tetris-mit-display-source.el.

    display-relay-bridge.py URL DISPLAY

websocket.el is not installed where the tests run, so Emacs reaches the
display relay (wal.sh/tools/display v0.2.1; contrib/displays' mock) through
this bridge: one line per message on stdin and stdout, payloads in hex.
It keeps one viewer of DISPLAY open, and one source connection at a time.

stdin, Emacs to the source's socket:
    O              open a source connection
    T <hex>        send the UTF-8 text (JSON control, or a hex frame)
    B <hex>        send the bytes (a pal16 frame)
    C              close the source connection
stdout, the relay to Emacs:
    S T <hex>      a text message to the source
    V T <hex>      a text message to the viewer
    V B <hex>      a binary message (a frame) to the viewer

Loopback only.  Needs the websockets package, as the mock relay does.
"""

import asyncio
import json
import sys
from urllib.parse import urlsplit

from websockets.asyncio.client import connect


async def pump(ws, tag):
    async for message in ws:
        if isinstance(message, bytes):
            line = f"{tag} B {message.hex()}"
        else:
            line = f"{tag} T {message.encode().hex()}"
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


async def main(url, display):
    host = urlsplit(url).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        sys.exit(f"display-relay-bridge: loopback only, not {host}")
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    async with connect(url) as viewer:
        await viewer.send(json.dumps({"op": "view", "display": display}))
        tasks = [asyncio.create_task(pump(viewer, "V"))]
        source = None
        while line := await reader.readline():
            kind, _, payload = line.decode().strip().partition(" ")
            if kind == "O":
                source = await connect(url)
                tasks.append(asyncio.create_task(pump(source, "S")))
            elif kind == "T":
                await source.send(bytes.fromhex(payload).decode())
            elif kind == "B":
                await source.send(bytes.fromhex(payload))
            elif kind == "C" and source is not None:
                await source.close()
        for task in tasks:
            task.cancel()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: display-relay-bridge.py URL DISPLAY")
    asyncio.run(main(*sys.argv[1:]))
