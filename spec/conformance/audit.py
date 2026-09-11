#!/usr/bin/env python3
"""Consistency audit: every driver reports identical phases and digests.

    audit.py --impl python="CMD ..." --impl hy="CMD ..." [--traces DIR]

run.py checks each driver against the oracle: its digests and final
observation against the trace's, and its phases for T-legality (SPEC P20).
The traces do not pin the phase of every frame, though, so two drivers can
both be T-legal and still disagree about a phase. This audit runs every
driver over every trace (with the driver protocol of README.md) and
compares, trace by trace, each driver's digests, per-frame phases and final
observation with the first driver's. It prints a matrix and exits 0 only if
every driver agrees with the first on everything.

Standard library only, like run.py.
"""

import argparse
import glob
import json
import os
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run_driver(cmd, paths):
    proc = subprocess.run(shlex.split(cmd) + paths, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"driver exited {proc.returncode}: {proc.stderr[-400:]}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) != len(paths):
        raise RuntimeError(f"driver printed {len(lines)} results for {len(paths)} traces")
    return [json.loads(ln) for ln in lines]


def first_difference(ref, got):
    """None if equal, else a short description of the first difference."""
    for i, (a, b) in enumerate(zip(ref["digests"], got["digests"], strict=False)):
        if a != b:
            return f"digest #{i}"
    if len(ref["digests"]) != len(got["digests"]):
        return "digest count"
    for k, (a, b) in enumerate(zip(ref["phases"], got["phases"], strict=False)):
        if a != b:
            return f"phase@{k} {b}!={a}"
    if len(ref["phases"]) != len(got["phases"]):
        return "phase count"
    for key in sorted(set(ref["final"]) | set(got["final"])):
        if ref["final"].get(key) != got["final"].get(key):
            return f"final.{key}"
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--impl", action="append", required=True, metavar="NAME=CMD")
    ap.add_argument("--traces", default=os.path.join(HERE, "traces"))
    args = ap.parse_args(argv)
    impls = []
    for spec in args.impl:
        name, sep, cmd = spec.partition("=")
        if not sep:
            ap.error(f"--impl wants NAME=CMD, got {spec!r}")
        impls.append((name, cmd))
    paths = sorted(glob.glob(os.path.join(args.traces, "*.json")))
    names = [os.path.basename(p)[: -len(".json")] for p in paths]
    results, broken = {}, []
    for name, cmd in impls:
        try:
            results[name] = run_driver(cmd, paths)
        except (RuntimeError, ValueError) as exc:
            broken.append(f"{name}: {exc}")
    ref_name = impls[0][0]
    if ref_name not in results:
        for b in broken:
            print("FINDING", b)
        print("FAIL: the reference driver did not run")
        return 1
    cols = [n for n, _ in impls if n in results]
    width = max(len(n) for n in names)
    print("trace".ljust(width) + "  " + "  ".join(c.ljust(12) for c in cols))
    findings = list(broken)
    for i, tname in enumerate(names):
        row = []
        for c in cols:
            diff = None if c == ref_name else first_difference(results[ref_name][i], results[c][i])
            row.append(("ref" if c == ref_name else "=" if diff is None else diff).ljust(12))
            if diff:
                findings.append(f"{tname}: {c} differs from {ref_name}: {diff}")
        print(tname.ljust(width) + "  " + "  ".join(row))
    frames = sum(len(r["phases"]) for r in results[ref_name])
    for f in findings:
        print("FINDING", f)
    if findings:
        print(f"FAIL: {len(findings)} disagreement(s)")
        return 1
    print(f"PASS: {len(cols)} drivers agree on every digest, every phase of "
          f"{frames} frames and every final observation over {len(names)} traces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
