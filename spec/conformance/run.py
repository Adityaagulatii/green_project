#!/usr/bin/env python3
"""Language-neutral conformance runner (stdlib only).

    run.py --impl "<driver command>" [--traces DIR] [--verbose]

The driver command is run once with every trace path appended as
arguments. It must print one JSON object per trace, in argument order
(JSON Lines): {"name": ..., "digests": [...], "final": {...}}. See
spec/conformance/README.md for the full protocol.

Exit status: 0 only if every trace matches exactly (PASS), 1 on any
finding (FAIL), 2 on usage or I/O errors. There is no warning tier.
"""

import argparse
import glob
import hashlib
import json
import os
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIRED = ("format", "spec_version", "name", "seed", "frames",
            "digest_every", "events", "digests", "final")
ACTIONS = {"left", "right", "soft_drop", "hard_drop", "rotate_cw",
           "rotate_ccw", "rotate_180", "hold"}


def trace_set_digest(paths):
    """sha256 over the sorted trace files: name NUL bytes NUL, per file."""
    h = hashlib.sha256()
    for p in sorted(paths, key=os.path.basename):
        h.update(os.path.basename(p).encode() + b"\0")
        with open(p, "rb") as fh:
            h.update(fh.read())
        h.update(b"\0")
    return h.hexdigest()


def validate(trace, path):
    """Structural checks on a trace; returns a list of findings."""
    out = []
    for key in REQUIRED:
        if key not in trace:
            out.append(f"{path}: missing key {key!r}")
    if out:
        return out
    if trace["format"] != "17x9-tetris-trace":
        out.append(f"{path}: bad format {trace['format']!r}")
    frames, every = trace["frames"], trace["digest_every"]
    if not (isinstance(frames, int) and frames > 0 and isinstance(every, int)
            and every > 0):
        out.append(f"{path}: frames/digest_every must be positive ints")
        return out
    expect = len(range(0, frames, every)) + (0 if (frames - 1) % every == 0 else 1)
    if len(trace["digests"]) != expect:
        out.append(f"{path}: {len(trace['digests'])} digests, expected {expect}")
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
    return out


def compare(trace, result):
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
        found = compare(t, result)
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
