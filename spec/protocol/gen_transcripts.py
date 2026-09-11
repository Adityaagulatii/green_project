#!/usr/bin/env python3
"""Generate the contract's conformance transcripts (docs/PROTOCOL.md §10).

    gen_transcripts.py --check           # the gate: files == what the traces give
    gen_transcripts.py --write           # after a trace or contract revision
    gen_transcripts.py --print NAME      # one transcript to stdout

Trace-derived transcripts. For every SPEC trace spec/conformance/traces/
NN-name.json there is a transcript transcripts/NN-name.jsonl: the exchange of
a lockstep controller replaying the trace (hello with the trace's seed, then
the trace's events and ticks) and every message a conforming server sends
back. Frame digests are copied from the trace at its checkpoint frames, so a
server passing the transcript reproduces the trace's digests over the wire.
The per-frame `events` (E_k) come from the trace's events. The `state` lines
are the view of the reference engine (impl/python/engine); the generator
refuses to write them unless that engine reproduces the trace's digests and
its final observation, so every line is anchored to the oracle.

Error transcripts (eNN-*) pin PROTOCOL §7: which error each bad message
gets, which are fatal, and that rejected messages have no effect.

Ticks follow one canonical batching, the one the Emacs client and the
Python tests use: the events of frame k are sent just before the tick that
runs frame k, and a tick runs up to the next frame with events (at most
3600 frames).
"""

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TRACES = os.path.join(ROOT, "spec", "conformance", "traces")
OUT = os.path.join(HERE, "transcripts")
sys.path.insert(0, os.path.join(ROOT, "impl", "python", "engine"))

from tetris_engine import core  # noqa: E402
from tetris_engine.conformance import observe  # noqa: E402
from tetris_engine.frame import codes_digest, render_codes  # noqa: E402

FORMAT = "17x9-tetris-transcript"
CONTRACT = 1
PROTOCOL = "17x9-tetris-remote"
MAX_TICK = 3600
CLIENT = "17x9-conformance"
BLACK_ROWS = [[[0, 0, 0]] * 9 for _ in range(17)]


class GenError(Exception):
    pass


def dumps(obj):
    return json.dumps(obj, separators=(",", ":"))


def client_hello(seed, role="controller", version=CONTRACT):
    return {"type": "hello", "protocol": PROTOCOL, "version": version,
            "role": role, "client": CLIENT, "seed": seed}


def server_hello(seed):
    return {"type": "hello", "protocol": PROTOCOL, "version": CONTRACT,
            "role": "server", "mode": "engine", "client_role": "controller",
            "rows": 17, "cols": 9, "fps": 30, "max_message": 65536,
            "seed": seed, "clock": "lockstep"}


def group(events):
    by_frame = {}
    for f, action, down in events:
        by_frame.setdefault(f, []).append((action, down))
    return by_frame


def ticks(by_frame, frames):
    """Canonical batching: (first frame, its events, frames in the tick)."""
    marks = sorted(by_frame)
    k = 0
    while k < frames:
        nxt = next((f for f in marks if f > k), frames)
        n = min(max(1, min(nxt, frames) - k), MAX_TICK)
        yield k, by_frame.get(k, []), n
        k += n


def view(s):
    return (s.phase, s.score, s.level, s.lines, s.high_score)


def state_msg(s, k):
    return {"type": "state", "score": s.score, "level": s.level,
            "lines": s.lines, "high_score": s.high_score, "phase": s.phase,
            "frame_no": k}


def checkpoints(trace):
    frames, every = trace["frames"], trace["digest_every"]
    ks = list(range(0, frames, every))
    if (frames - 1) % every:
        ks.append(frames - 1)
    if len(ks) != len(trace["digests"]):
        raise GenError(f"{trace['name']}: {len(trace['digests'])} digests for "
                       f"{len(ks)} checkpoints")
    return dict(zip(ks, trace["digests"]))


def from_trace(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    trace = json.loads(raw)
    name, seed, frames = trace["name"], trace["seed"], trace["frames"]
    by_frame = group(trace["events"])
    marks = checkpoints(trace)
    lines = [{"format": FORMAT, "contract": CONTRACT, "name": name,
              "kind": "trace", "trace": os.path.basename(path),
              "trace_sha256": hashlib.sha256(raw).hexdigest(), "seed": seed,
              "frames": frames, "digest_every": trace["digest_every"],
              "description": "Lockstep replay of KAV-" + name[:2] + " ("
              + trace["description"] + ")"},
             [">", client_hello(seed)], ["<", server_hello(seed)]]
    s, last = core.new_game(seed), None
    for k0, events, n in ticks(by_frame, frames):
        for action, down in events:
            lines.append([">", {"type": "event", "action": action, "down": down}])
        lines.append([">", {"type": "tick", "frames": n}])
        for k in range(k0, k0 + n):
            step_events = by_frame.get(k, []) if k == k0 else []
            if k != k0 and k in by_frame:
                raise GenError(f"{name}: events at frame {k} inside a tick")
            s = core.step(s, step_events)
            frame = {"type": "frame", "frame_no": k,
                     "events": [[a, d] for a, d in step_events]}
            if k in marks:
                mine = codes_digest(render_codes(s))
                if mine != marks[k]:
                    raise GenError(f"{name}: the reference engine gives {mine} "
                                   f"at frame {k}, the trace {marks[k]}")
                frame["digest"] = marks[k]
            lines.append(["<", frame])
            if view(s) != last:
                last = view(s)
                lines.append(["<", state_msg(s, k)])
    if observe(s) != trace["final"]:
        raise GenError(f"{name}: the reference engine's final observation "
                       "differs from the trace's")
    lines += [[">", {"type": "ping", "id": "end"}],
              ["<", {"type": "pong", "id": "end"}]]
    return name, lines


def _err(code):
    return ["<", {"type": "error", "code": code}]


def _header(name, description, seed=1):
    return {"format": FORMAT, "contract": CONTRACT, "name": name,
            "kind": "errors", "seed": seed, "description": description}


def error_transcripts():
    """PROTOCOL §7. Seed 1, whose frame 0 is KAV-01's frame 0."""
    s0 = core.step(core.new_game(1), [])
    frame0 = {"type": "frame", "frame_no": 0, "events": [],
              "digest": codes_digest(render_codes(s0))}
    open_ = [[">", client_hello(1)], ["<", server_hello(1)]]
    out = []
    out.append(("e01-nonfatal-errors", [
        _header("e01-nonfatal-errors", "Non-fatal errors, in the order of the "
                "gates (schema, then state); none has an effect, and frame 0 "
                "follows with no events."),
        *open_,
        [">raw", '{"type": "tick", '], _err("malformed"),
        [">raw", "[1, 2, 3]"], _err("malformed"),
        [">raw", '{"type":"tick","frames":1,"frames":2}'], _err("malformed"),
        [">raw", '{"type":"ping","id":NaN}'], _err("malformed"),
        [">", {"type": "warp"}], _err("unknown_type"),
        [">", {"type": "event", "action": "jump", "down": True}], _err("bad_event"),
        [">", {"type": "event", "action": "left", "down": 1}], _err("bad_event"),
        [">", {"type": "tick", "frames": 0}], _err("bad_tick"),
        [">", {"type": "tick", "frames": 3601}], _err("bad_tick"),
        [">raw", '{"type":"tick","frames":1.0}'], _err("bad_tick"),
        [">", {"type": "frame", "frame_no": 0, "rows": []}], _err("bad_frame"),
        [">", {"type": "frame", "frame_no": 0, "rows": BLACK_ROWS}], _err("forbidden"),
        [">", {"type": "state", "score": 0, "level": 0, "lines": 0}], _err("forbidden"),
        [">", client_hello(1)], _err("forbidden"),
        [">", {"type": "ping", "id": True}], _err("bad_ping"),
        [">", {"type": "ping", "id": 7}], ["<", {"type": "pong", "id": 7}],
        [">", {"type": "ping", "id": "x"}], ["<", {"type": "pong", "id": "x"}],
        [">pad", 65535], ["<", {"type": "pong", "id": "pad"}],
        [">", {"type": "tick", "frames": 1}], ["<", frame0],
        ["<", state_msg(s0, 0)],
        [">", {"type": "ping", "id": "end"}], ["<", {"type": "pong", "id": "end"}],
    ]))
    out.append(("e02-hello-required", [
        _header("e02-hello-required", "The first message must be hello."),
        [">", {"type": "ping", "id": 1}], _err("hello_required"), ["<close"]]))
    out.append(("e03-version", [
        _header("e03-version", "A v0 hello is refused by a v1 server."),
        [">", client_hello(1, version=0)], _err("version"), ["<close"]]))
    out.append(("e04-bad-hello", [
        _header("e04-bad-hello", "A seed outside 0..2^32-1 is refused."),
        [">", client_hello(2 ** 32)], _err("bad_hello"), ["<close"]]))
    out.append(("e05-role", [
        _header("e05-role", "An engine server refuses a producer."),
        [">", client_hello(1, role="producer")], _err("role"), ["<close"]]))
    out.append(("e06-too-large", [
        _header("e06-too-large", "A message of 65536 bytes of JSON text is "
                "too large, and fatal (65535 is accepted: e01)."),
        *open_, [">pad", 65536], _err("too_large"), ["<close"]]))
    out.append(("e07-too-many-errors", [
        _header("e07-too-many-errors", "The 16th error is followed by "
                "too_many_errors and the close."),
        *open_,
        *[x for _ in range(15) for x in
          ([">", {"type": "event", "action": "jump", "down": True}],
           _err("bad_event"))],
        [">", {"type": "event", "action": "jump", "down": True}],
        _err("bad_event"), _err("too_many_errors"), ["<close"]]))
    return out


def generate():
    """{file name: bytes} for every transcript."""
    files = {}
    paths = sorted(p for p in os.listdir(TRACES) if p.endswith(".json"))
    for p in paths:
        name, lines = from_trace(os.path.join(TRACES, p))
        files[name + ".jsonl"] = lines
    for name, lines in error_transcripts():
        files[name + ".jsonl"] = lines
    return {n: "".join(dumps(x) + "\n" for x in lines).encode()
            for n, lines in files.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--write", action="store_true")
    g.add_argument("--print", metavar="NAME")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    try:
        files = generate()
    except GenError as exc:
        print(f"FAIL: {exc}")
        return 1
    if args.print:
        sys.stdout.write(files[args.print + ".jsonl"].decode())
        return 0
    if args.write:
        os.makedirs(args.out, exist_ok=True)
        for old in os.listdir(args.out):
            if old.endswith(".jsonl") and old not in files:
                os.remove(os.path.join(args.out, old))
        for name, data in files.items():
            with open(os.path.join(args.out, name), "wb") as fh:
                fh.write(data)
        print(f"wrote {len(files)} transcripts to {args.out}")
        return 0
    have = set(n for n in os.listdir(args.out) if n.endswith(".jsonl")) \
        if os.path.isdir(args.out) else set()
    bad = sorted(have ^ set(files))
    for name in sorted(have & set(files)):
        with open(os.path.join(args.out, name), "rb") as fh:
            if fh.read() != files[name]:
                bad.append(name)
    if bad:
        print("FAIL: transcripts differ from what the traces give: " + ", ".join(bad))
        return 1
    print(f"ok: {len(files)} transcripts equal what the traces give")
    return 0


if __name__ == "__main__":
    sys.exit(main())
