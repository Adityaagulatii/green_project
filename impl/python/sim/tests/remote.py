"""Helpers for the remote-protocol tests: servers on ephemeral ports or unix
sockets, client URLs, and the server and client CLIs under a subprocess.

Nothing here leaves a server running: every start has its close or stop in
a ``finally``. Ports are ephemeral (0). In a FreeBSD jail a bind to
127.0.0.1 lands on the jail's own lo0 address, which is why the tests
connect to the address the server reports, and why unix sockets exist.
"""

import asyncio
import contextlib
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile

import pytest

from tetris_sim import protocol as P

ROOT =pathlib.Path(__file__).resolve().parents[4]
TRACES = sorted((ROOT / "spec" / "conformance" / "traces").glob("*.json"))
ENV = dict(os.environ, PYTHONPATH=os.pathsep.join(
    [str(ROOT / "impl" / "python" / "engine"), str(ROOT / "impl" / "python" / "sim")]))

HAVE_WS = importlib.util.find_spec("websockets") is not None
needs_ws = pytest.mark.skipif(not HAVE_WS, reason="websockets is not installed")
TRANSPORTS = ["tcp", pytest.param("ws", marks=needs_ws)]

LISTENING = re.compile(r"listening on (\S+)")


def run(coro, timeout=120):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def url_for(transport, address):
    """A client URL for a listener's address ((host, port) or a path)."""
    if isinstance(address, str):
        return ("unix:" if transport == "tcp" else "ws+unix:") + address
    host, port = address
    return f"tcp://{host}:{port}" if transport == "tcp" \
        else f"ws://{host}:{port}{P.WS_PATH}"


async def start(server, transport="tcp", path=None):
    """Add a listener on an ephemeral port (or unix socket ``path``) and
    return its client URL."""
    return url_for(transport, await server.start("127.0.0.1", 0, transport, path))


async def until(predicate, timeout=20.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("timed out waiting for the server")
        await asyncio.sleep(0.01)


@contextlib.contextmanager
def short_tmpdir():
    """A short directory: a unix socket path must fit in 104 bytes."""
    path = tempfile.mkdtemp(prefix="t17x9-")
    try:
        yield pathlib.Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def loopback_address():
    """Where a bind to 127.0.0.1 lands on this host. A FreeBSD jail whose
    lo0 carries the jail's own address maps 127.0.0.1 there."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[0]


def session_digest(digests):
    h = hashlib.sha256()
    for d in digests:
        h.update(d.encode("ascii") + b"\n")
    return h.hexdigest()


def start_cli(*args, listeners=1):
    """Start the server CLI; return (process, [client URL per listener])."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "tetris_sim.server", *args],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=ENV, text=True)
    urls = []
    for _ in range(listeners):
        line = proc.stderr.readline()
        match = LISTENING.search(line)
        if not match:
            proc.kill()
            pytest.fail(line + proc.communicate()[1])
        where = match.group(1)
        urls.append(where if where.startswith(("ws://", "unix:", "ws+unix:"))
                    else "tcp://" + where)
    return proc, urls


def stop_cli(proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
    try:
        _out, err = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        _out, err = proc.communicate()
    return proc.returncode, err


def run_client_cli(*args, timeout=120):
    """Run the client CLI; return (exit status, JSON summary, stdout, stderr)."""
    result = subprocess.run([sys.executable, "-m", "tetris_sim.client", *args],
                            env=ENV, capture_output=True, text=True,
                            timeout=timeout)
    lines = result.stdout.strip().splitlines()
    summary = json.loads(lines[-1]) if lines and lines[-1].startswith("{") else None
    return result.returncode, summary, result.stdout, result.stderr
