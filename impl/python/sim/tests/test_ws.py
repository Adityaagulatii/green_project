"""The WebSocket binding's own rules (contract v1 §5.3): the path and the
subprotocol, text vs binary messages, the size limit and close codes, the
Origin allow-list (--ws-origin), and one server serving both bindings as
one. Contract: docs/PROTOCOL.md on spec/contract at f3e074d.
"""

import json

import pytest
from remote import run, start, until

from tetris_sim import protocol as P
from tetris_sim.client import connect
from tetris_sim.server import MAX_CLIENTS, MAX_ERRORS, EngineServer

wsclient = pytest.importorskip("websockets.asyncio.client")
wsexc = pytest.importorskip("websockets.exceptions")


def text(msg):
    return P.encode(msg)[:-1].decode("utf-8")


async def raw_ws(url, subprotocols=(P.WS_SUBPROTOCOL,), **options):
    return await wsclient.connect(url, compression=None, proxy=None,
                                  subprotocols=list(subprotocols or ()) or None,
                                  **options)


def test_one_json_message_per_text_message():
    async def go():
        server = EngineServer(seed=1, clock="lockstep")
        url = await start(server, "ws")
        try:
            ws = await raw_ws(url)
            await ws.send(text(P.make_hello("controller", seed=4)))
            hello = await ws.recv()
            await ws.send(text(P.make_event("left", True)))
            await ws.send(text(P.make_tick(2)))
            await ws.send(text(P.make_ping(1)))
            got = [await ws.recv() for _ in range(4)]
            subprotocol = ws.subprotocol
            await ws.close()
            return url, hello, got, subprotocol
        finally:
            await server.close()

    url, hello, got, subprotocol = run(go())
    assert url.endswith(P.WS_PATH) and subprotocol == P.WS_SUBPROTOCOL
    for data in (hello, *got):
        assert isinstance(data, str) and "\n" not in data
    hello = json.loads(hello)
    assert (hello["seed"], hello["client_role"], hello["version"]) == \
        (4, "controller", 1)
    msgs = [json.loads(m) for m in got]
    assert [m["type"] for m in msgs] == ["frame", "state", "frame", "pong"]
    assert msgs[0]["events"] == [["left", True]] and msgs[2]["events"] == []


def test_binary_messages_are_malformed_and_blank_ones_ignored():
    async def go():
        server = EngineServer(seed=1)
        url = await start(server, "ws")
        try:
            ws = await raw_ws(url)
            await ws.send(text(P.make_hello("viewer")))
            await ws.recv()
            await ws.send(text(P.make_ping(1)).encode())          # binary
            await ws.send("   ")
            await ws.send("\n")
            await ws.send(text(P.make_ping(2)))
            got = [json.loads(await ws.recv()) for _ in range(2)]
            await ws.close()
            return got
        finally:
            await server.close()

    error, pong = run(go())
    assert (error["type"], error["code"]) == ("error", "malformed")
    assert pong == {"type": "pong", "id": 2}


def test_size_limit_and_utf8():
    """65535 bytes of JSON text pass. One more is too_large: an error, then
    close 1009 "too_large". Far more is refused by the frame layer, which
    closes 1009 with no error message. Invalid UTF-8 is close 1007."""
    base = '{"type":"ping","id":"x"}'
    exact = base[:-1] + " " * (P.MAX_TEXT - len(base)) + "}"
    assert len(exact.encode()) == P.MAX_TEXT

    async def go():
        server = EngineServer(seed=1)
        url = await start(server, "ws")
        out = {}
        try:
            c = await connect(url, "viewer")
            await c.raw(exact)
            out["exact"] = await c.recv(20)
            await c.raw(exact + " ")
            out["one over"] = (*await c.error_then_closed(20), c.close_code,
                               c.close_reason)
            c = await connect(url, "viewer")
            await c.raw("x" * 70000)
            out["far over"] = (await c.recv(20), c.close_code)
            c = await connect(url, "viewer")
            await c.raw(b'{"type":"ping","id":"\xff"}')
            out["utf8"] = (await c.recv(20), c.close_code)
            await until(lambda: not server.clients)
        finally:
            await server.close()
        return out

    out = run(go())
    assert out["exact"] == {"type": "pong", "id": "x"}
    assert out["one over"] == ("too_large", True, 1009, "too_large")
    assert out["far over"] == (None, 1009)
    assert out["utf8"] == (None, 1007)


def test_fatal_errors_arrive_before_a_close_code():
    async def go():
        server = EngineServer(seed=1, clock="lockstep")
        url = await start(server, "ws")
        out = {}
        try:
            async def case(name, *msgs, raw=()):
                c = await connect(url)
                await c.send(*msgs)
                for data in raw:
                    await c.raw(data)
                code, closed = await c.error_then_closed(20)
                out[name] = (code, closed, c.close_code, c.close_reason)

            await case("hello_required", P.make_ping(1))
            await case("version", raw=[b'{"type":"hello","protocol":'
                                       b'"17x9-tetris-remote","version":0,'
                                       b'"role":"viewer"}'])
            ctl = await connect(url, "controller")
            await case("busy", P.make_hello("controller"))
            v = await connect(url, "viewer")
            for _ in range(MAX_ERRORS):
                await v.raw(b"nope")
            codes = [m["code"] for m in [await v.recv(20)
                                         for _ in range(MAX_ERRORS + 1)]]
            out["too_many_errors"] = (codes[-1], await v.recv(20) is None,
                                      v.close_code, v.close_reason)
            await ctl.close()
        finally:
            await server.close()
        return out

    out = run(go())
    assert out["hello_required"] == ("hello_required", True, 1008,
                                     "hello_required")
    assert out["version"] == ("version", True, 1008, "version")
    assert out["busy"] == ("busy", True, 1013, "busy")
    assert out["too_many_errors"] == ("too_many_errors", True, 1008,
                                      "too_many_errors")


async def _attempt(url, **options):
    """"ok" and the negotiated subprotocol, or the HTTP status of a refusal."""
    try:
        ws = await raw_ws(url, **options)
    except wsexc.InvalidStatus as exc:
        return exc.response.status_code
    try:
        await ws.send(text(P.make_hello("viewer")))
        assert json.loads(await ws.recv())["type"] == "hello"
        return "ok", ws.subprotocol
    finally:
        await ws.close()


def test_the_path_and_the_subprotocol_are_required():
    async def go():
        server = EngineServer()
        url = await start(server, "ws")
        root = url[:-len(P.WS_PATH)]
        try:
            offers = [await _attempt(url, subprotocols=offer) for offer in
                      ([P.WS_SUBPROTOCOL], ["chat", P.WS_SUBPROTOCOL], None,
                       ["chat"], [P.PROTOCOL])]
            paths = [await _attempt(root + path) for path in
                     (P.WS_PATH, P.WS_PATH + "?x=1", "/", "/other")]
            return offers, paths
        finally:
            await server.close()

    ok = ("ok", P.WS_SUBPROTOCOL)
    offers, paths = run(go())
    assert offers == [ok, ok, 400, 400, 400]
    assert paths == [ok, ok, 404, 404]


def test_browser_origins_need_the_allow_list():
    async def go():
        out = {}
        for name, origins in (("default", None),
                              ("allowlist", ["http://ok.example", "null"]),
                              ("any", ["*"])):
            server = EngineServer(ws_origins=origins)
            url = await start(server, "ws")
            try:
                out[name] = [await _attempt(url, origin=origin) for origin in
                             (None, "http://ok.example", "null",
                              "http://evil.example")]
            finally:
                await server.close()
        return out

    ok = ("ok", P.WS_SUBPROTOCOL)
    out = run(go())
    assert out["default"] == [ok, 403, 403, 403]
    assert out["allowlist"] == [ok, ok, ok, 403]
    assert out["any"] == [ok, ok, ok, ok]


def test_both_bindings_share_one_session_slot_and_limit():
    async def go():
        server = EngineServer(seed=2, clock="lockstep")
        tcp, ws = await start(server, "tcp"), await start(server, "ws")
        out = {}
        try:
            ctl = await connect(tcp, "controller", seed=6)
            c = await connect(ws)
            await c.send(P.make_hello("controller"))
            out["busy"] = (*await c.error_then_closed(20), c.close_code)
            watcher = await connect(ws, "viewer")
            assert (watcher.hello["seed"], watcher.hello["client_role"]) == \
                (6, "viewer")
            await ctl.send(P.make_tick(3))
            mine = [m["digest"] for m in [await ctl.expect("frame", 20)
                                          for _ in range(3)]]
            seen = [m["digest"] for m in [await watcher.expect("frame", 20)
                                          for _ in range(3)]]
            out["same stream"] = mine == seen
            await until(lambda: len(server.clients) == 2)
            rest = [await connect(url, "viewer")
                    for url in [tcp, ws, tcp, ws, tcp, ws][:MAX_CLIENTS - 2]]
            for url in (ws, tcp):
                extra = await connect(url)
                out[f"limit {url[:3]}"] = await extra.error_then_closed(20)
            for c in (*rest, watcher, ctl):
                await c.close()
        finally:
            await server.close()
        return out

    out = run(go())
    assert out["busy"] == ("busy", True, 1013)
    assert out["same stream"]
    assert out["limit ws:"] == out["limit tcp"] == ("busy", True)
