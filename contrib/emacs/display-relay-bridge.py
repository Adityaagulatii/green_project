#!/usr/bin/env python3
"""A WebSocket bridge between Emacs and a display relay (wal.sh/tools/display v0.2.1).

    display-relay-bridge.py URL [DISPLAY]

Emacs has no built-in WebSocket client, and websocket.el is not always
installed, so tetris-mit-display-source.el can reach a relay through this
bridge: one line per message on stdin and stdout, payloads in hex.  With
DISPLAY, the bridge also keeps a viewer of it open (viewers need no key).

stdin, Emacs to the source's socket:
    O              open a source connection
    T <hex>        send the UTF-8 text (JSON control, or a hex frame)
    B <hex>        send the bytes (a pal16 frame)
    C              close the source connection
    (end of file)  close everything and exit
stdout, the relay to Emacs:
    S T <hex>      a text message to the source
    V T <hex>      a text message to the viewer
    V B <hex>      a binary message (a frame) to the viewer

Loopback only: the relay on the live site is never reached from here.
Needs the websockets package, as the mock relay in contrib/displays does.
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


async def main(url, display=None):
    host = urlsplit(url).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        sys.exit(f"display-relay-bridge: loopback only, not {host}")
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    tasks, sockets, source = [], [], None
    if display:
        viewer = await connect(url)
        sockets.append(viewer)
        await viewer.send(json.dumps({"op": "view", "display": display}))
        tasks.append(asyncio.create_task(pump(viewer, "V")))
    try:
        while line := await reader.readline():
            kind, _, payload = line.decode().strip().partition(" ")
            if kind == "O":
                source = await connect(url)
                sockets.append(source)
                tasks.append(asyncio.create_task(pump(source, "S")))
            elif kind == "T" and source is not None:
                await source.send(bytes.fromhex(payload).decode())
            elif kind == "B" and source is not None:
                await source.send(bytes.fromhex(payload))
            elif kind == "C" and source is not None:
                await source.close()
    finally:
        for ws in sockets:
            await ws.close()
        for task in tasks:
            task.cancel()


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: display-relay-bridge.py URL [DISPLAY]")
    asyncio.run(main(*sys.argv[1:]))
