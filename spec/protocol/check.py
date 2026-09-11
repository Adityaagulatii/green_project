#!/usr/bin/env python3
"""Protocol conformance checker for contract v1 (docs/PROTOCOL.md §10).

    check.py --server tcp://127.0.0.1:1709 [--only NAME,...] [--verbose]
    check.py --server ws://127.0.0.1:1710/tetris-17x9
    check.py --launch "CMD ... {port} ..." --server tcp://127.0.0.1:{port}
    check.py --session LOG.jsonl [--trace NAME]      a client's recorded session
    check.py --selftest                              verify the schemas reject bad messages

Server mode replays every transcript in spec/protocol/transcripts against the
server, one connection per transcript, and judges what comes back in the
order of the state-machine ladder:
  1. the schema gate: strict JSON (PROTOCOL §1.4), the message's JSON Schema
     (spec/protocol/schemas), and a frame's digest against its rows;
  2. the state gate: the connection lifecycle, contiguous frame numbers, and
     T-legality (SPEC P20, Table 9.1) of the phases in the state messages;
  3. the oracle: every server message equals the transcript's expectation,
     field by field (rows are pinned by digest), and WebSocket closes carry
     the codes of PROTOCOL §5.3.
With --launch, the checker starts the server itself on a free port.

Session mode checks a client: the log written by `proxy.py --record` while
the client talked to a server. See spec/protocol/README.md.

Standard library, plus the websockets package for ws:// URLs.
Exit status: 0 PASS, 1 FAIL (findings), 2 usage or I/O errors.
"""

import argparse
import asyncio
import glob
import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

from schema import Schemas, _equal  # noqa: E402

PROTOCOL = "17x9-tetris-remote"
SUBPROTOCOL = "tetris-17x9.v1"
WS_PATH = "/tetris-17x9"
MAX_TEXT = 65535
PHASES = ("countdown", "playing", "clearing", "gameover")
# SPEC §9.1, Table 9.1 (the same table as spec/conformance/run.py).
LEGAL = {("countdown", "countdown"), ("countdown", "playing"),
         ("countdown", "clearing"), ("countdown", "gameover"),
         ("playing", "playing"), ("playing", "clearing"), ("playing", "gameover"),
         ("clearing", "clearing"), ("clearing", "playing"), ("clearing", "gameover"),
         ("gameover", "gameover"), ("gameover", "countdown")}
FATAL = {"too_large", "hello_required", "version", "role", "bad_hello", "busy",
         "too_many_errors"}
WS_CLOSE = {"too_large": 1009, "busy": 1013}          # other fatal codes: 1008
OPS = {">": 1, ">raw": 1, ">pad": 1, "<": 1, "<close": 0}
SCHEMAS = Schemas(os.path.join(HERE, "schemas"))


# ------------------------------------------------------------- utilities

class Closed:
    def __init__(self, code=None, reason=""):
        self.code, self.reason = code, reason

    def __repr__(self):
        return "close" if self.code is None else f"close {self.code} {self.reason!r}"


class Binary:
    pass


def _reject_constant(name):
    raise ValueError(f"{name} is not JSON")


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def parse_strict(data):
    """(message, None) or (None, why): PROTOCOL §1.2-§1.4."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            return None, "not UTF-8"
    if len(data.encode("utf-8")) > MAX_TEXT:
        return None, f"over {MAX_TEXT} bytes"
    try:
        msg = json.loads(data, parse_constant=_reject_constant,
                         object_pairs_hook=_no_duplicates)
    except (ValueError, RecursionError) as exc:
        return None, f"not strict JSON ({str(exc)[:60]})"
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        return None, "not an object with a string type"
    return msg, None


def frame_digest(rows):
    return hashlib.sha256(bytes(v for row in rows for rgb in row for v in rgb)).hexdigest()


def dumps(obj):
    return json.dumps(obj, separators=(",", ":"))


def pad_message(n):
    """A ping whose JSON text is exactly n bytes."""
    base = dumps({"type": "ping", "id": "pad", "pad": ""})
    if n < len(base):
        raise ValueError(f"cannot pad a ping to {n} bytes")
    return base[:-2] + "x" * (n - len(base)) + base[-2:]


def set_digest(root, rels):
    """sha256 over files: relative name NUL bytes NUL, in sorted order."""
    h = hashlib.sha256()
    for rel in sorted(rels):
        h.update(rel.encode() + b"\0")
        with open(os.path.join(root, rel), "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


def contract_digest():
    rels = [os.path.relpath(p, HERE) for p in
            glob.glob(os.path.join(HERE, "schemas", "*.schema.json"))
            + glob.glob(os.path.join(HERE, "transcripts", "*.jsonl"))]
    return set_digest(HERE, rels)


def server_profile(msg, mode):
    return {"hello": "hello-server",
            "frame": "frame-engine" if mode == "engine" else "frame",
            "state": "state-engine" if mode == "engine" else "state",
            "ping": "ping", "pong": "ping", "error": "error"}.get(msg["type"])


def client_profile(msg, first):
    if first:
        return "hello-client" if msg["type"] == "hello" else None
    return {"event": "event", "tick": "tick", "ping": "ping", "pong": "ping",
            "frame": "frame", "state": "state", "hello": "hello-client"}.get(msg["type"])


# ------------------------------------------------------------ transports

class TcpConn:
    binding = "tcp"

    @classmethod
    async def open(cls, url):
        u = urlsplit(url)
        self = cls()
        if u.scheme == "unix":
            self.reader, self.writer = await asyncio.open_unix_connection(
                u.path, limit=1 << 20)
        else:
            self.reader, self.writer = await asyncio.open_connection(
                u.hostname, u.port, limit=1 << 20)
        return self

    async def send(self, text):
        self.writer.write(text.encode("utf-8") + b"\n")
        await self.writer.drain()

    async def recv(self, timeout):
        try:
            line = await asyncio.wait_for(self.reader.readline(), timeout)
        except TimeoutError:
            return None
        except (ConnectionError, ValueError):
            return Closed()
        if not line.endswith(b"\n"):
            return Closed()
        return line.rstrip(b"\n").rstrip(b"\r")

    async def close(self):
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, RuntimeError):
            pass


class WsConn:
    binding = "ws"

    @classmethod
    async def open(cls, url):
        from websockets.asyncio.client import connect, unix_connect
        self = cls()
        opts = {"subprotocols": [SUBPROTOCOL], "max_size": 1 << 20,
                "compression": None, "open_timeout": 10}
        u = urlsplit(url)
        if u.scheme == "ws+unix":
            self.ws = await unix_connect(u.path, "ws://localhost" + WS_PATH, **opts)
        else:
            self.ws = await connect(url, proxy=None, **opts)
        if self.ws.subprotocol != SUBPROTOCOL:
            await self.ws.close()
            raise ConnectionError(f"server selected subprotocol "
                                  f"{self.ws.subprotocol!r}, not {SUBPROTOCOL!r}")
        return self

    async def send(self, text):
        await self.ws.send(text)

    async def recv(self, timeout):
        from websockets.exceptions import ConnectionClosed
        try:
            data = await asyncio.wait_for(self.ws.recv(), timeout)
        except TimeoutError:
            return None
        except ConnectionClosed as exc:
            rcvd = exc.rcvd
            return Closed(rcvd.code if rcvd else 1006, rcvd.reason if rcvd else "")
        return Binary() if isinstance(data, bytes) else data

    async def close(self):
        try:
            await self.ws.close()
        except Exception:  # noqa: BLE001 - already gone
            pass


async def connect(url):
    scheme = urlsplit(url).scheme
    if scheme in ("tcp", "unix"):
        return await TcpConn.open(url)
    if scheme in ("ws", "ws+unix"):
        return await WsConn.open(url)
    raise ValueError(f"unsupported URL scheme {scheme!r} "
                     "(tcp://, ws://, unix:// or ws+unix://)")


# ----------------------------------------------------------- transcripts

class Transcript:
    def __init__(self, path, header, steps):
        self.path, self.header, self.steps = path, header, steps
        self.name = header.get("name") if isinstance(header, dict) else None


def load_transcripts(directory, only=None):
    out, findings = [], []
    for path in sorted(glob.glob(os.path.join(directory, "*.jsonl"))):
        base = os.path.basename(path)[: -len(".jsonl")]
        if only and base not in only:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                rows = [json.loads(ln) for ln in fh if ln.strip()]
        except (OSError, ValueError) as exc:
            findings.append(f"{base}: transcript unreadable ({exc})")
            continue
        header, steps = (rows[0], rows[1:]) if rows else ({}, [])
        t = Transcript(path, header, steps)
        findings += lint(t, base)
        out.append(t)
    if only:
        missing = set(only) - {t.name for t in out}
        findings += [f"{m}: no such transcript" for m in sorted(missing)]
    return out, findings


def lint(t, base):
    """The transcript's own schema gate."""
    h = t.header
    if not (isinstance(h, dict) and h.get("format") == "17x9-tetris-transcript"
            and h.get("contract") == 1 and h.get("name") == base):
        return [f"{base}: bad transcript header"]
    for i, step in enumerate(t.steps, 2):
        if not (isinstance(step, list) and step and step[0] in OPS
                and len(step) == 1 + OPS[step[0]]):
            return [f"{base}: line {i}: bad step {str(step)[:60]}"]
        if step[0] in (">", "<") and not (isinstance(step[1], dict)
                                          and isinstance(step[1].get("type"), str)):
            return [f"{base}: line {i}: a message must be an object with a type"]
        if step[0] == "<" and step[1]["type"] == "frame" and "rows" in step[1]:
            return [f"{base}: line {i}: expectations pin rows by digest, not rows"]
    return []


async def exchange(url, t, timeout):
    """Play the client side of ``t``. Returns [(step index, item)], one per
    expectation reached; item is text, Binary, Closed, or None (timeout)."""
    conn = await connect(url)
    got = []
    try:
        for i, step in enumerate(t.steps):
            op = step[0]
            if op.startswith(">"):
                text = (dumps(step[1]) if op == ">" else
                        step[1] if op == ">raw" else pad_message(step[1]))
                try:
                    await conn.send(text)
                except Exception:  # noqa: BLE001 - peer went away
                    got.append((i, Closed()))
                    break
                continue
            item = await conn.recv(timeout)
            got.append((i, item))
            if item is None or (isinstance(item, Closed) and op == "<") \
                    or (op == "<close" and not isinstance(item, Closed)):
                break
    finally:
        await conn.close()
    return got, conn.binding


# ------------------------------------------------------------------ gates

def schema_gate(name, items, who="server"):
    """items: [(step, parsed msg | why str | Binary | Closed | None)]."""
    out, mode = [], "engine"
    for i, x in items:
        if isinstance(x, Binary):
            out.append(f"{name}: schema gate: step {i + 2}: a binary WebSocket message")
        elif isinstance(x, str):
            out.append(f"{name}: schema gate: step {i + 2}: {x}")
        elif isinstance(x, dict):
            if x["type"] == "hello" and x.get("mode") in ("engine", "display"):
                mode = x["mode"]
            prof = server_profile(x, mode)
            if prof is None:
                out.append(f"{name}: schema gate: step {i + 2}: a {who} never "
                           f"sends {x['type']!r}")
                continue
            errs = SCHEMAS.validate(prof, x)
            if errs:
                out.append(f"{name}: schema gate: step {i + 2}: {x['type']} "
                           f"({prof}): {errs[0]}")
            elif x["type"] == "frame" and "digest" in x \
                    and frame_digest(x["rows"]) != x["digest"]:
                out.append(f"{name}: schema gate: step {i + 2}: frame "
                           f"{x['frame_no']}: digest does not match rows (SPEC §9.4)")
        if len(out) >= 5:
            break
    return out


def state_gate(name, msgs):
    """Lifecycle, frame numbering and T-legality over the server's messages
    (in order; a Closed marks the end of the connection)."""
    out = []
    if msgs and isinstance(msgs[0], dict) and msgs[0]["type"] not in ("hello", "error"):
        out.append(f"{name}: state gate: the server's first message is "
                   f"{msgs[0]['type']!r}, not hello")
    frames, states, fatal, last_frame = [], {}, None, None
    for x in msgs:
        if isinstance(x, Closed) or x is None:
            break
        if fatal is not None:
            out.append(f"{name}: state gate: a message after the fatal error {fatal!r}")
            break
        if x["type"] == "error" and x["code"] in FATAL and x["code"] != "too_many_errors":
            fatal = x["code"]
        if x["type"] == "error" and x["code"] == "too_many_errors":
            fatal = x["code"]
        if x["type"] == "frame":
            if x["frame_no"] != len(frames):
                out.append(f"{name}: state gate: frame_no {x['frame_no']} "
                           f"after {len(frames)} frames")
                break
            frames.append(x["frame_no"])
            last_frame = x["frame_no"]
        elif x["type"] == "state":
            if x.get("frame_no") != last_frame or last_frame in states:
                out.append(f"{name}: state gate: state for frame "
                           f"{x.get('frame_no')} does not follow its frame")
                break
            states[last_frame] = x["phase"]
    if out or not frames:
        return out
    if 0 not in states:
        return [f"{name}: state gate: no state after the first frame"]
    phase, prev = None, "countdown"   # S0 is a countdown (SPEC §9.1)
    for k in frames:
        phase = states.get(k, phase)
        if (prev, phase) not in LEGAL:
            return [f"{name}: state gate: illegal phase edge {prev} -> {phase} "
                    f"into frame {k} (SPEC Table 9.1)"]
        prev = phase
    return out


def oracle(t, items, binding):
    """Every expectation against what arrived."""
    out, name, prev = [], t.name, None
    expectations = [(i, s) for i, s in enumerate(t.steps) if s[0].startswith("<")]
    got = dict(items)
    for i, step in expectations:
        if i not in got:
            out.append(f"{name}: oracle: step {i + 2}: nothing received "
                       f"(the exchange stopped earlier)")
            break
        x = got[i]
        if step[0] == "<close":
            if not isinstance(x, Closed):
                out.append(f"{name}: oracle: step {i + 2}: expected the close, "
                           f"got {describe(x)}")
            elif binding == "ws" and prev in FATAL:
                want = WS_CLOSE.get(prev, 1008)
                if x.code != want or x.reason != prev:
                    out.append(f"{name}: oracle: step {i + 2}: WebSocket close "
                               f"{x.code} {x.reason!r}, expected {want} {prev!r}")
            break
        if binding == "ws" and isinstance(x, Closed) and step[1]["type"] == "error" \
                and step[1].get("code") in WS_CLOSE and x.code == WS_CLOSE[step[1]["code"]]:
            # PROTOCOL §5.3: over WebSocket, too_large (1009) may arrive as
            # the close alone, the error message being enforced away by the
            # frame layer. The close must end the transcript.
            rest = [s for j, s in expectations if j > i]
            if rest != [["<close"]]:
                out.append(f"{name}: oracle: step {i + 2}: closed {x.code} early")
            break
        if not isinstance(x, dict):
            out.append(f"{name}: oracle: step {i + 2}: expected "
                       f"{step[1]['type']}, got {describe(x)}")
            break
        for key, want in step[1].items():
            if key not in x or not _equal(x[key], want):
                what = f"{x['type']}" + (f" {x['frame_no']}" if "frame_no" in x else "")
                out.append(f"{name}: oracle: step {i + 2}: {what}: {key} expected "
                           f"{json.dumps(want)[:80]} got {json.dumps(x.get(key))[:80]}")
                break
        if out:
            break
        prev = x.get("code") if x["type"] == "error" else None
    return out


def describe(x):
    if isinstance(x, dict):
        return f"a {x['type']} message" + (f" ({x.get('code')})" if x["type"] == "error" else "")
    if x is None:
        return "nothing (timeout)"
    return repr(x)


def judge(t, got, binding):
    items = []
    for i, raw in got:
        if raw is None or isinstance(raw, (Closed, Binary)):
            items.append((i, raw))
        else:
            msg, why = parse_strict(raw)
            items.append((i, msg if msg is not None else why))
    found = schema_gate(t.name, items)
    if not found:
        found = state_gate(t.name, [x for _, x in items])
    if not found:
        found = oracle(t, items, binding)
    return found


# ----------------------------------------------------------- server mode

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Launched:
    """A server process started for the run, on a free port."""

    def __init__(self, cmd, url):
        self.port = free_port()
        self.url = url.replace("{port}", str(self.port))
        self.log = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(shlex.split(cmd.replace("{port}", str(self.port))),
                                     stdout=self.log, stderr=subprocess.STDOUT)

    def wait_ready(self, seconds=20):
        u = urlsplit(self.url)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                with socket.create_connection((u.hostname, u.port), timeout=0.5):
                    return True
            except OSError:
                time.sleep(0.1)
        return False

    def tail(self):
        self.log.seek(0)
        return self.log.read()[-2000:].decode(errors="replace")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


async def run_server(url, transcripts, timeout, verbose):
    findings = []
    for t in transcripts:
        try:
            got, binding = await exchange(url, t, timeout)
        except Exception as exc:  # noqa: BLE001 - reported as a finding
            found = [f"{t.name}: cannot connect to {url}: {exc}"]
        else:
            found = judge(t, got, binding)
        findings += found
        if verbose:
            print(("ok   " if not found else "FAIL ") + t.name, flush=True)
    return findings


def server_mode(args):
    only = set(args.only.split(",")) if args.only else None
    transcripts, findings = load_transcripts(args.transcripts, only)
    if findings or not transcripts:
        for f in findings or ["no transcripts"]:
            print("FINDING", f)
        print("FAIL: transcript validation")
        return 1
    launched = None
    url = args.server
    if args.launch:
        launched = Launched(args.launch, args.server)
        url = launched.url
        if not launched.wait_ready():
            print(launched.tail())
            print(f"FAIL: the launched server never accepted connections on {url}")
            launched.stop()
            return 1
    try:
        findings = asyncio.run(run_server(url, transcripts, args.timeout, args.verbose))
    finally:
        if launched:
            launched.stop()
    for f in findings:
        print("FINDING", f)
    if findings:
        print(f"FAIL: {len(findings)} finding(s) over {len(transcripts)} transcripts ({url})")
        return 1
    print(f"PASS: {len(transcripts)}/{len(transcripts)} transcripts over "
          f"{urlsplit(url).scheme}, contract-set sha256 {contract_digest()}")
    return 0


# ---------------------------------------------------------- session mode

def load_traces():
    out = []
    for p in sorted(glob.glob(os.path.join(ROOT, "spec", "conformance", "traces", "*.json"))):
        with open(p) as fh:
            out.append(json.load(fh))
    return out


def session_mode(args):
    """A client's recorded session (proxy.py --record): PROTOCOL §10."""
    conns = {}
    with open(args.session, encoding="utf-8") as fh:
        for ln in fh:
            if ln.strip():
                rec = json.loads(ln)
                conns.setdefault(rec["conn"], []).append(rec)
    traces = load_traces()
    findings, checked = [], 0
    for cid, recs in sorted(conns.items()):
        name = f"session {cid}"
        c2s, s2c = [], []
        for r in recs:
            if r["dir"] == "end":
                continue
            data = Binary() if r.get("binary") else r["text"]
            parsed = data if isinstance(data, Binary) else None
            if parsed is None:
                msg, why = parse_strict(data)
                parsed = msg if msg is not None else why
            (c2s if r["dir"] == "c2s" else s2c).append(parsed)
        found = []
        # 1. schema gate: both directions
        for n, x in enumerate(c2s):
            if not isinstance(x, dict):
                found.append(f"{name}: schema gate: client message {n + 1}: "
                             f"{'binary' if isinstance(x, Binary) else x}")
                continue
            prof = client_profile(x, n == 0)
            errs = ([f"a client never sends {x['type']!r} here"] if prof is None
                    else SCHEMAS.validate(prof, x))
            if errs:
                found.append(f"{name}: schema gate: client message {n + 1} "
                             f"({x['type']}): {errs[0]}")
        found += schema_gate(name, list(enumerate(s2c)))
        # 2. state gate: the client's lifecycle, then the server's
        if not found:
            hello = s2c[0] if s2c and isinstance(s2c[0], dict) else {}
            role = hello.get("client_role")
            allowed = {"controller": {"event", "tick", "ping", "pong"},
                       "viewer": {"ping", "pong"},
                       "producer": {"frame", "state", "ping", "pong"}}.get(role, set())
            if hello.get("type") != "hello":
                found.append(f"{name}: state gate: the client was not admitted")
            for x in c2s[1:]:
                if x["type"] not in allowed:
                    found.append(f"{name}: state gate: a {role} sent {x['type']!r}")
                    break
            errors = [x["code"] for x in s2c if isinstance(x, dict) and x["type"] == "error"]
            if errors:
                found.append(f"{name}: state gate: the client caused errors {errors}")
            found += state_gate(name, s2c)
        # 3. oracle: a lockstep controller's log is a KAV's (seed, frames, events)
        if not found and hello.get("client_role") == "controller" \
                and hello.get("clock") == "lockstep":
            seed, k, pending, log = hello.get("seed"), 0, [], []
            for x in c2s[1:]:
                if x["type"] == "event":
                    pending.append([x["action"], x["down"]])
                elif x["type"] == "tick":
                    log += [[k, a, d] for a, d in pending]
                    pending, k = [], k + x["frames"]
            cands = [t for t in traces if (args.trace in (None, t["name"]))
                     and t["seed"] == seed and t["events"] == log and t["frames"] == k]
            if not cands:
                found.append(f"{name}: oracle: the log (seed {seed}, {k} frames, "
                             f"{len(log)} events) is no KAV trace's"
                             + (f" ({args.trace})" if args.trace else ""))
            else:
                t = cands[0]
                frames = {x["frame_no"]: x["digest"] for x in s2c
                          if isinstance(x, dict) and x["type"] == "frame"}
                ks = list(range(0, t["frames"], t["digest_every"]))
                if (t["frames"] - 1) % t["digest_every"]:
                    ks.append(t["frames"] - 1)
                bad = [kk for kk, d in zip(ks, t["digests"], strict=False)
                       if frames.get(kk) != d]
                if bad:
                    found.append(f"{name}: oracle: frame {bad[0]} digest differs "
                                 f"from {t['name']}")
                elif args.verbose:
                    print(f"     {name} replays {t['name']}: seed, events and "
                          f"{len(ks)} digests match")
        elif not found and args.require_kav:
            found.append(f"{name}: oracle: not a lockstep controller's KAV replay")
        findings += found
        checked += 1
        if args.verbose:
            print(("ok   " if not found else "FAIL ") + name)
    for f in findings:
        print("FINDING", f)
    if findings or not checked:
        print(f"FAIL: {len(findings)} finding(s) over {checked} session(s)")
        return 1
    print(f"PASS: {checked}/{checked} session(s)")
    return 0


# ------------------------------------------------------------- self-test

def selftest():
    """The schemas and the checker must reject known-bad messages and accept
    known-good ones (verify the verifier)."""
    black = [[[0, 0, 0]] * 9 for _ in range(17)]
    good_frame = {"type": "frame", "frame_no": 0, "rows": black, "events": [],
                  "digest": frame_digest(black)}
    hello = {"type": "hello", "protocol": PROTOCOL, "version": 1, "role": "server",
             "mode": "engine", "client_role": "controller", "spec_version": 2,
             "rows": 17, "cols": 9, "fps": 30, "max_message": 65536, "seed": 1,
             "clock": "lockstep"}
    state = {"type": "state", "score": 0, "level": 0, "lines": 0, "high_score": 0,
             "phase": "countdown", "frame_no": 0}
    good = [("hello-server", hello), ("frame-engine", good_frame),
            ("state-engine", state), ("ping", {"type": "pong", "id": "x"}),
            ("error", {"type": "error", "code": "busy", "message": ""}),
            ("hello-client", {"type": "hello", "protocol": PROTOCOL, "version": 1,
                              "role": "viewer", "extra": [1]}),
            ("event", {"type": "event", "action": "hold", "down": False}),
            ("tick", {"type": "tick", "frames": 3600}),
            ("frame", {"type": "frame", "frame_no": 5, "rows": black})]

    def edit(msg, **kv):
        m = json.loads(json.dumps(msg))
        for k, v in kv.items():
            if v is KeyError:
                m.pop(k)
            else:
                m[k] = v
        return m

    rows16 = black[:16]
    wide = [row + [[0, 0, 0]] for row in black]
    hot = json.loads(json.dumps(black))
    hot[3][4] = [0, 256, 0]
    boolcell = json.loads(json.dumps(black))
    boolcell[0][0] = [True, 0, 0]
    floatcell = json.loads(json.dumps(black))
    floatcell[0][0] = [1.0, 0, 0]
    bad = [
        ("hello-server", edit(hello, client_role=KeyError), "no client_role"),
        ("hello-server", edit(hello, seed=KeyError), "engine hello without seed"),
        ("hello-server", edit(hello, version=0), "version 0"),
        ("hello-server", edit(hello, version=True), "version true"),
        ("hello-server", edit(hello, fps=60), "fps 60"),
        ("hello-client", {"type": "hello", "protocol": PROTOCOL, "version": 1,
                          "role": "server"}, "client claims role server"),
        ("hello-client", {"type": "hello", "protocol": PROTOCOL, "version": 1,
                          "role": "viewer", "seed": 2 ** 32}, "seed 2^32"),
        ("frame-engine", edit(good_frame, events=KeyError), "engine frame without events"),
        ("frame-engine", edit(good_frame, digest=KeyError), "engine frame without digest"),
        ("frame-engine", edit(good_frame, rows=rows16), "16 rows"),
        ("frame-engine", edit(good_frame, rows=wide), "10 columns"),
        ("frame-engine", edit(good_frame, rows=hot), "channel 256"),
        ("frame-engine", edit(good_frame, rows=boolcell), "boolean channel"),
        ("frame-engine", edit(good_frame, rows=floatcell), "channel 1.0"),
        ("frame-engine", edit(good_frame, digest="A" * 64), "uppercase digest"),
        ("frame-engine", edit(good_frame, events=[["left"]]), "short event"),
        ("frame-engine", edit(good_frame, events=[["left", 1]]), "event down 1"),
        ("frame-engine", edit(good_frame, events=[["jump", True]]), "event action jump"),
        ("frame-engine", edit(good_frame, frame_no=-1), "frame_no -1"),
        ("state-engine", edit(state, phase="game_over"), "phase game_over"),
        ("state-engine", edit(state, phase=KeyError), "engine state without phase"),
        ("state-engine", edit(state, score=-100), "score -100"),
        ("event", {"type": "event", "action": "jump", "down": True}, "action jump"),
        ("tick", {"type": "tick", "frames": 0}, "tick 0"),
        ("tick", {"type": "tick", "frames": 3601}, "tick 3601"),
        ("ping", {"type": "ping", "id": "x" * 65}, "id of 65 characters"),
        ("ping", {"type": "ping", "id": True}, "id true"),
        ("error", {"type": "error", "code": "teapot", "message": ""}, "unknown code"),
    ]
    out = []
    for prof, msg in good:
        errs = SCHEMAS.validate(prof, msg)
        if errs:
            out.append(f"self-test: a good {prof} was rejected: {errs[0]}")
    for prof, msg, why in bad:
        if not SCHEMAS.validate(prof, msg):
            out.append(f"self-test: {prof} with {why} was accepted")
    for text, why in (('{"type":"ping","id":NaN}', "NaN"),
                      ('{"type":"ping","type":"pong"}', "duplicate keys"),
                      ('[1]', "an array"), (b'{"type":"\xff"}', "bad UTF-8"),
                      (pad_message(65536), "65536 bytes")):
        if parse_strict(text)[0] is not None:
            out.append(f"self-test: strict parsing accepted {why}")
    if parse_strict(pad_message(65535))[0] is None:
        out.append("self-test: strict parsing rejected 65535 bytes")
    lying = edit(good_frame, digest="0" * 64)
    if not schema_gate("self-test", [(0, lying)]):
        out.append("self-test: a frame whose digest does not match its rows was accepted")
    illegal = [{"type": "frame", "frame_no": 0}, dict(state, phase="gameover"),
               {"type": "frame", "frame_no": 1}, dict(state, phase="playing", frame_no=1)]
    if not state_gate("self-test", illegal):
        out.append("self-test: the illegal edge gameover -> playing was accepted")
    for line in out:
        print(line)
    n = len(good) + len(bad) + 8
    print(("FAIL" if out else "ok") + f": schema and checker self-test, {n} cases")
    return 1 if out else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", help="tcp://HOST:PORT or ws://HOST:PORT/tetris-17x9")
    ap.add_argument("--launch", help="start this server command first; {port} "
                    "in it and in --server is replaced by a free port")
    ap.add_argument("--session", help="check a client's session log (proxy.py --record)")
    ap.add_argument("--trace", help="session mode: the KAV trace the client replays")
    ap.add_argument("--require-kav", action="store_true",
                    help="session mode: every session must be a KAV replay")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--transcripts", default=os.path.join(HERE, "transcripts"))
    ap.add_argument("--only", help="comma-separated transcript names")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.session:
        return session_mode(args)
    if not args.server:
        ap.error("one of --server, --session or --selftest is required")
    return server_mode(args)


if __name__ == "__main__":
    sys.exit(main())
