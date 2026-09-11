#!/usr/bin/env python3
"""Language-neutral conformance runner (stdlib only).

    run.py --impl "<driver command>" [--traces DIR] [--verbose]

The driver command is run once with every trace path appended as
arguments. It must print one JSON object per trace, in argument order
(JSON Lines): {"name": ..., "digests": [...], "phases": [...], "final": {...}}.
See spec/conformance/README.md for the full protocol.

Every trace goes through three gates, in the order of the state-machine
ladder (SPEC §9.1):
  1. the schema gate: the fields and domains of the trace, then of the
     driver's result;
  2. the state gate: T-legality (SPEC P20) of the phases the driver reports;
  3. the oracle: the digests and the final observation equal the trace's.

Exit status: 0 only if every trace matches exactly (PASS), 1 on any
finding (FAIL), 2 on usage or I/O errors. There is no warning tier.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIRED = ("format", "spec_version", "name", "seed", "frames",
            "digest_every", "events", "digests", "final")
ACTIONS = {"left", "right", "soft_drop", "hard_drop", "rotate_cw",
           "rotate_ccw", "rotate_180", "hold"}
SHAPES = {"I", "J", "L", "O", "S", "Z", "T"}
PHASES = ("countdown", "playing", "clearing", "gameover")
# SPEC §9.1, Table 9.1: the legal edges of the phase machine. S0 is a countdown.
LEGAL = {("countdown", "countdown"), ("countdown", "playing"),
         ("countdown", "clearing"), ("countdown", "gameover"),
         ("playing", "playing"), ("playing", "clearing"), ("playing", "gameover"),
         ("clearing", "clearing"), ("clearing", "playing"), ("clearing", "gameover"),
         ("gameover", "gameover"), ("gameover", "countdown")}
OBSERVATION = {"phase", "score", "level", "lines", "high_score", "active",
               "hold", "frame_hex"}
HEX64 = re.compile(r"[0-9a-f]{64}")
FRAME_HEX = re.compile(r"[0-9a-f]{918}")  # 459 bytes


def trace_set_digest(paths):
    """sha256 over the sorted trace files: name NUL bytes NUL, per file."""
    h = hashlib.sha256()
    for p in sorted(paths, key=os.path.basename):
        h.update(os.path.basename(p).encode() + b"\0")
        with open(p, "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


def _nat(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def check_final(final, where):
    """Fields and domains of a §12 observation."""
    if not isinstance(final, dict):
        return [f"{where} is not an object"]
    if set(final) != OBSERVATION:
        return [f"{where} has fields {sorted(final)}, expected {sorted(OBSERVATION)}"]
    out = []
    if final["phase"] not in PHASES:
        out.append(f"{where}.phase {final['phase']!r} is not a phase")
    for key in ("score", "level", "lines", "high_score"):
        if not _nat(final[key]):
            out.append(f"{where}.{key} must be an integer >= 0")
    a = final["active"]
    if not (isinstance(a, dict) and set(a) == {"shape", "rotation", "row", "col"}
            and a["shape"] in SHAPES and a["rotation"] in (0, 1, 2, 3)
            and isinstance(a["row"], int) and isinstance(a["col"], int)):
        out.append(f"{where}.active is malformed")
    h = final["hold"]
    if h is not None and not (isinstance(h, dict) and set(h) == {"shape", "rotation"}
                              and h["shape"] in SHAPES and h["rotation"] in (0, 1, 2, 3)):
        out.append(f"{where}.hold is malformed")
    if not (isinstance(final["frame_hex"], str) and FRAME_HEX.fullmatch(final["frame_hex"])):
        out.append(f"{where}.frame_hex must be 459 bytes of lowercase hex")
    return out


def validate(trace, path):
    """The schema gate for a trace: fields and domains. Returns findings."""
    out = []
    for key in REQUIRED:
        if key not in trace:
            out.append(f"{path}: missing key {key!r}")
    if out:
        return out
    if trace["format"] != "17x9-tetris-trace":
        out.append(f"{path}: bad format {trace['format']!r}")
    if not (_nat(trace["spec_version"]) and trace["spec_version"] >= 1):
        out.append(f"{path}: spec_version must be an integer >= 1")
    if not (_nat(trace["seed"]) and trace["seed"] < 2 ** 32):
        out.append(f"{path}: seed must be an integer in 0..2^32-1")
    frames, every = trace["frames"], trace["digest_every"]
    if not (isinstance(frames, int) and frames > 0 and isinstance(every, int)
            and every > 0):
        out.append(f"{path}: frames/digest_every must be positive ints")
        return out
    expect = len(range(0, frames, every)) + (0 if (frames - 1) % every == 0 else 1)
    if len(trace["digests"]) != expect:
        out.append(f"{path}: {len(trace['digests'])} digests, expected {expect}")
    if not all(isinstance(d, str) and HEX64.fullmatch(d) for d in trace["digests"]):
        out.append(f"{path}: digests must be SHA-256 lowercase hex")
    last = -1
    for ev in trace["events"]:
        if (not isinstance(ev, list) or len(ev) != 3 or not isinstance(ev[0], int)
                or ev[1] not in ACTIONS or not isinstance(ev[2], bool)):
            out.append(f"{path}: malformed event {ev!r}")
            break
        if ev[0] < last or ev[0] >= frames:
            out.append(f"{path}: event frame out of order/range {ev!r}")
            break
        last = ev[0]
    if trace["name"] != os.path.splitext(os.path.basename(path))[0]:
        out.append(f"{path}: name {trace['name']!r} does not match file name")
    out.extend(check_final(trace["final"], f"{path}: final"))
    return out


def check_result(trace, result):
    """The schema gate for a driver's result: fields and domains."""
    name = trace["name"]
    if not isinstance(result, dict):
        return [f"{name}: driver result is not an object"]
    out = []
    if result.get("name") != name:
        out.append(f"{name}: driver result is named {result.get('name')!r}")
    digests = result.get("digests")
    if not (isinstance(digests, list)
            and all(isinstance(d, str) and HEX64.fullmatch(d) for d in digests)):
        out.append(f"{name}: digests must be a list of SHA-256 lowercase hex")
    phases = result.get("phases")
    if not (isinstance(phases, list) and len(phases) == trace["frames"]
            and all(p in PHASES for p in phases)):
        out.append(f"{name}: phases must give one of {'/'.join(PHASES)} "
                   f"for each of the {trace['frames']} frames")
    out.extend(check_final(result.get("final"), f"{name}: final"))
    return out


def t_legality(trace, phases):
    """The state gate (SPEC P20): S0 is a countdown, and each step from S_k
    to S_k+1 (frame k) is a legal edge."""
    path = ["countdown"] + list(phases)
    for k, edge in enumerate(zip(path, path[1:])):
        if edge not in LEGAL:
            return [f"{trace['name']}: illegal phase edge {edge[0]} -> {edge[1]} "
                    f"into frame {k}"]
    return []


def compare(trace, result):
    """The oracle: digests and final observation against the trace's."""
    out, name = [], trace["name"]
    want, got = trace["digests"], result.get("digests")
    if not isinstance(got, list) or len(got) != len(want):
        return [f"{name}: digest count {len(got) if isinstance(got, list) else got!r}"
                f" != {len(want)}"]
    for i, (w, g) in enumerate(zip(want, got)):
        if w != g:
            k = i * trace["digest_every"] if i < len(want) - 1 else trace["frames"] - 1
            out.append(f"{name}: first digest mismatch at frame {k}")
            break
    fw, fg = trace["final"], result.get("final") or {}
    for key in sorted(set(fw) | set(fg)):
        if fw.get(key) != fg.get(key):
            out.append(f"{name}: final.{key} expected {fw.get(key)!r} got {fg.get(key)!r}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--impl", required=True, help="driver command (shell words)")
    ap.add_argument("--traces", default=os.path.join(HERE, "traces"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.traces, "*.json")))
    if not paths:
        print(f"FAIL: no traces in {args.traces}")
        return 1
    findings, traces = [], []
    for p in paths:
        try:
            with open(p) as fh:
                t = json.load(fh)
        except (OSError, ValueError) as exc:
            findings.append(f"{p}: unreadable ({exc})")
            continue
        findings.extend(validate(t, p))
        traces.append((p, t))
    if findings:
        for f in findings:
            print("FINDING", f)
        print(f"FAIL: {len(findings)} finding(s) in trace validation")
        return 1

    cmd = shlex.split(args.impl) + [p for p, _ in traces]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        print(f"FAIL: driver exited {proc.returncode}")
        return 1
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) != len(traces):
        print(f"FAIL: driver printed {len(lines)} results for {len(traces)} traces")
        return 1
    for (p, t), line in zip(traces, lines):
        try:
            result = json.loads(line)
        except ValueError:
            findings.append(f"{t['name']}: driver output is not JSON")
            continue
        found = check_result(t, result)                 # 1. schema gate
        if not found:
            found = t_legality(t, result["phases"])     # 2. state gate
        if not found:
            found = compare(t, result)                  # 3. the oracle
        findings.extend(found)
        if args.verbose:
            print(("ok   " if not found else "FAIL ") + t["name"])
    for f in findings:
        print("FINDING", f)
    set_digest = trace_set_digest([p for p, _ in traces])
    if findings:
        print(f"FAIL: {len(findings)} finding(s) over {len(traces)} traces")
        return 1
    print(f"PASS: {len(traces)}/{len(traces)} traces, trace-set sha256 {set_digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
