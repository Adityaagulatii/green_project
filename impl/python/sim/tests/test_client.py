"""The reference client (tetris_sim.client) and its seeded bot, over every
transport: the same seeded session gives the same frame digests over TCP,
WebSocket and unix sockets, and they equal the engine's own for the same
seed and events.
"""

import asyncio
import json
import subprocess
import sys

import pytest
from remote import (
    ENV,
    HAVE_WS,
    TRACES,
    needs_ws,
    run,
    run_client_cli,
    session_digest,
    short_tmpdir,
    start,
    start_cli,
    stop_cli,
    until,
)

from tetris_engine import core
from tetris_engine.animation import TetrisAnimation
from tetris_engine.conformance import run_trace
from tetris_engine.frame import render
from tetris_sim import protocol as P
from tetris_sim.bot import Bot
from tetris_sim.client import (
    ClientError,
    Result,
    _replica,
    bot_policy,
    connect,
    parse_url,
)
from tetris_sim.client import run as play
from tetris_sim.recorder import record
from tetris_sim.server import DisplayServer, EngineServer


def demo_bot():
    """What ``--bot`` plays: the same as ``python -m tetris_sim --bot``."""
    return Bot(pace=3, think=6, batch_shifts=False)


@pytest.mark.parametrize("url,parsed", [
    ("tcp://127.0.0.1:1709", ("tcp", "127.0.0.1", 1709, None, None)),
    ("127.0.0.1:5000", ("tcp", "127.0.0.1", 5000, None, None)),
    ("ws://127.0.0.1:1710", ("ws", "127.0.0.1", 1710, None,
                             "ws://127.0.0.1:1710/tetris-17x9")),
    ("ws://h", ("ws", "h", 1710, None, "ws://h:1710/tetris-17x9")),
    ("ws://localhost:1710/play?x=1",
     ("ws", "localhost", 1710, None, "ws://localhost:1710/play?x=1")),
    ("ws://[::1]:1710", ("ws", "::1", 1710, None, "ws://[::1]:1710/tetris-17x9")),
    ("unix:///tmp/t.sock", ("tcp", None, None, "/tmp/t.sock", None)),
    ("unix:rel.sock", ("tcp", None, None, "rel.sock", None)),
    ("ws+unix:/tmp/t.sock", ("ws", None, None, "/tmp/t.sock", None)),
])
def test_parse_url(url, parsed):
    assert parse_url(url) == parsed


@pytest.mark.parametrize("url", ["http://h:1", "unix:", "tcp://h:0",
                                 "tcp://h:99999", "tcp://h:port"])
def test_parse_url_rejects(url):
    with pytest.raises(ValueError):
        parse_url(url)


class ScriptedServer:
    """Stands in for a server's connection: hands the client scripted
    frames, and keeps what the client sends."""

    transport = "script"

    def __init__(self, seed, msgs):
        self.hello = {"seed": seed, "mode": "engine", "clock": "realtime"}
        self.msgs = list(msgs)
        self.sent = []

    async def recv(self, timeout=None):
        return self.msgs.pop(0) if self.msgs else None

    async def send(self, *msgs):
        self.sent.extend(msgs)


def engine_frames(seed, n, stamps, reported=None):
    """Frames 0..n-1 of the session whose E_k are ``stamps``; ``reported``
    overrides the E_k that the frames claim."""
    reported = stamps if reported is None else reported
    s, out = core.new_game(seed), []
    for k in range(n):
        s = core.step(s, stamps.get(k, []))
        out.append(P.make_frame(k, render(s), events=reported.get(k, [])))
    return out


def test_the_replica_checks_digests_and_the_events_it_sent():
    """Contract §4.4: the client folds E_k from the frames. It catches a
    frame whose events do not explain its digest, and an E_k holding an
    event it never sent."""
    seed, n = 5, 130
    sent = [("left", True), ("left", False), ("hard_drop", True)]
    stamps = {97: sent[:2], 101: sent[2:]}     # they land late, in order

    def replay(frames):
        conn = ScriptedServer(seed, frames)
        res = Result(conn, "controller", n)
        run(_replica(conn, res, n, lambda _s, k: sent if k == 95 else [],
                     None, None))
        return res, conn

    res, conn = replay(engine_frames(seed, n, stamps))
    assert res.ok and res.verified == n
    assert res.events == [[97, "left", True], [97, "left", False],
                          [101, "hard_drop", True]]
    assert [(m["action"], m["down"]) for m in conn.sent] == sent
    hidden, _ = replay(engine_frames(seed, n, stamps, reported={}))
    assert not hidden.ok and hidden.first_mismatch == 97
    extra = {**stamps, 99: [("rotate_cw", True)]}      # never sent
    forged, _ = replay(engine_frames(seed, n, extra))
    assert not forged.ok and forged.first_mismatch == 99


def test_seeded_session_is_identical_over_every_transport():
    """Deliverable: a seeded session over TCP and over WS (and both over
    unix sockets) yields identical frame digests, equal to the engine's own
    trace digests for the same seed and events."""
    seed, frames = 7, 600
    rec, _ = record(TetrisAnimation(seed), frames, demo_bot())
    engine = run_trace({"seed": seed, "frames": frames, "digest_every": 1,
                        "events": rec.events})["digests"]
    assert rec.events and engine == rec.digests()

    async def go():
        server = EngineServer(seed=1, clock="lockstep")
        with short_tmpdir() as tmp:
            try:
                urls = [await start(server, "tcp"),
                        await start(server, "tcp", str(tmp / "l.sock"))]
                if HAVE_WS:
                    urls += [await start(server, "ws"),
                             await start(server, "ws", str(tmp / "w.sock"))]
                results = []
                for i, url in enumerate(urls):
                    results.append(await play(url, seed=seed, frames=frames,
                                              policy=bot_policy()))
                    await until(lambda n=i + 1: len(server.sessions) == n)
                return urls, results, [s.log for s in server.sessions]
            finally:
                await server.close()

    urls, results, logs = run(go())
    assert [r.transport for r in results] == ["tcp", "tcp", "ws", "ws"][:len(urls)]
    for res, log in zip(results, logs, strict=True):
        assert res.ok and res.verified == frames and res.mismatches == 0
        assert res.digests == engine
        assert res.events == rec.events == log
        assert res.session_digest() == session_digest(engine)
        assert res.summary()["clock"] == "lockstep"


def test_realtime_bot_session_verifies_and_matches_the_server_log(transport):
    frames = 400

    async def go():
        server = EngineServer(seed=1, fps=90)
        url = await start(server, transport)
        try:
            res = await play(url, seed=9, frames=frames, policy=bot_policy())
            await until(lambda: server.sessions)
            return res, server.sessions[0]
        finally:
            await server.close()

    res, session = run(go())
    assert res.ok and res.verified == frames and res.mismatches == 0
    assert res.seed == 9 and res.summary()["clock"] == "realtime"
    assert res.events, "the bot played"
    assert res.events == [e for e in session.log if e[0] < frames]
    replay = run_trace({"seed": 9, "frames": frames, "digest_every": 1,
                        "events": res.events})["digests"]
    assert replay == res.digests


def test_a_viewer_client_watches_the_session(transport):
    async def go():
        server = EngineServer(seed=4, clock="lockstep")
        url = await start(server, transport)
        try:
            watching = asyncio.ensure_future(play(url, role="viewer", frames=50))
            await until(lambda: len(server.clients) == 1
                        and next(iter(server.clients)).role == "viewer")
            ctl = await connect(url, "controller")
            await ctl.send({"type": "tick", "frames": 50})
            mine = [(await ctl.expect("frame", 20))["digest"] for _ in range(50)]
            res = await watching
            await ctl.close()
            return res, mine
        finally:
            await server.close()

    res, mine = run(go())
    assert res.ok and res.digests == mine and res.role == "viewer"


def test_the_client_reports_refusals(transport):
    async def go():
        codes = []
        server = EngineServer(seed=1, clock="lockstep")
        url = await start(server, transport)
        try:
            ctl = await connect(url, "controller")
            with pytest.raises(ClientError) as exc:
                await play(url, seed=1, frames=10)
            codes.append(exc.value.code)
            await ctl.close()
        finally:
            await server.close()
        display = DisplayServer(pace=False)
        url = await start(display, transport)
        try:
            with pytest.raises(ClientError) as exc:
                await play(url, frames=10)
            codes.append(exc.value.code)
        finally:
            await display.close()
        return codes

    assert run(go()) == ["busy", "role"]


# -------------------------------------------------------------------- CLI

@needs_ws
def test_cli_both_transports_and_the_bot_client():
    """``gmake server`` + ``gmake client``, in lockstep so the digests are
    checkable against a direct engine run."""
    proc, urls = start_cli("--mode", "engine", "--transport", "both",
                           "--port", "0", "--clock", "lockstep", listeners=2)
    try:
        outs = [run_client_cli(url, "--seed", "5", "--bot", "--frames", "300")
                for url in urls]
    finally:
        code, err = stop_cli(proc)
    rec, _ = record(TetrisAnimation(5), 300, demo_bot())
    assert [u.split("://")[0] for u in urls] == ["tcp", "ws"]
    for rc, summary, _out, cerr in outs:
        assert rc == 0, cerr
        assert summary["verified"] == 300 and summary["mismatches"] == 0
        assert summary["session_digest"] == session_digest(rec.digests())
        assert summary["events"] == len(rec.events)
    assert [o[1]["transport"] for o in outs] == ["tcp", "ws"]
    assert code == 0 and "2 session(s), 600 frames" in err


@needs_ws
def test_cli_realtime_ws_demo():
    proc, (url,) = start_cli("--mode", "engine", "--transport", "ws",
                             "--port", "0", "--seed", "3")
    try:
        rc, summary, _out, err = run_client_cli(url, "--seed", "1", "--bot",
                                                "--frames", "150")
    finally:
        code, serr = stop_cli(proc)
    assert rc == 0, err
    assert (summary["clock"], summary["seed"], summary["verified"]) == \
        ("realtime", 1, 150)
    assert code == 0, serr


def test_cli_unix_socket_trace_replay(tmp_path):
    trace_file = TRACES[0]
    trace = json.loads(trace_file.read_text())
    out_file = tmp_path / "session.json"
    with short_tmpdir() as tmp:
        sock = tmp / "engine.sock"
        proc, (url,) = start_cli("--mode", "engine", "--clock", "lockstep",
                                 "--unix", str(sock))
        try:
            assert url == f"unix:{sock}"
            rc, summary, out, err = run_client_cli(
                url, "--trace", str(trace_file), "--digests",
                "--trace-out", str(out_file))
        finally:
            code, serr = stop_cli(proc)
        assert not sock.exists(), "the server removes its socket"
    assert rc == 0, err
    assert summary["trace_match"] is True and summary["seed"] == trace["seed"]
    lines = out.splitlines()[:-1]
    assert [int(line.split()[0]) for line in lines] == list(range(trace["frames"]))
    written = json.loads(out_file.read_text())
    assert written["events"] == trace["events"]
    assert written["digests"] == [line.split()[1] for line in lines]
    assert code == 0, serr


def test_cli_client_exit_status_when_unreachable():
    with short_tmpdir() as tmp:
        rc, summary, _out, err = run_client_cli(f"unix:{tmp / 'nobody.sock'}",
                                                "--frames", "1", timeout=60)
    assert rc == 3 and summary is None and "tetris_sim.client" in err


def test_cli_client_help():
    result = subprocess.run([sys.executable, "-m", "tetris_sim.client", "--help"],
                            env=ENV, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0
    for word in ("ws://", "unix:", "--bot", "--seed", "session_digest"):
        assert word in result.stdout
