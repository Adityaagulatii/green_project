"""Conformance driver for the Python engine (spec/conformance/README.md).

    python -m tetris_engine.conformance TRACE.json [TRACE.json ...]

prints one JSON object per trace (JSON Lines) with the digests and final
observation this implementation produces for the trace's seed and events.
"""

import json
import sys

from . import core
from .frame import codes_bytes, codes_digest, render_codes


def observe(s):
    """The observable final state recorded in traces (SPEC §12)."""
    a = s.active
    return {
        "phase": s.phase,
        "score": s.score,
        "level": s.level,
        "lines": s.lines,
        "high_score": s.high_score,
        "active": {"shape": a.shape, "rotation": a.rot, "row": a.row,
                   "col": a.col},
        "hold": (None if s.hold is None
                 else {"shape": s.hold.shape, "rotation": s.hold.rot}),
        "frame_hex": codes_bytes(render_codes(s)).hex(),
    }


def group_events(events):
    by_frame = {}
    for f, action, down in events:
        by_frame.setdefault(int(f), []).append((action, bool(down)))
    return by_frame


def run_trace(trace):
    """Replay a trace's inputs; return {"name", "digests", "final"}."""
    frames = int(trace["frames"])
    every = int(trace.get("digest_every", 1))
    by_frame = group_events(trace["events"])
    s = core.new_game(trace["seed"])
    digests = []
    for k in range(frames):
        s = core.step(s, by_frame.get(k, ()))
        if k % every == 0 or k == frames - 1:
            digests.append(codes_digest(render_codes(s)))
    return {"name": trace.get("name"), "digests": digests, "final": observe(s)}


def make_trace(name, description, seed, frames, events, digest_every=1,
               covers=()):
    """Build a conformance trace (spec/conformance/README.md) by running the
    reference engine on ``events`` ([frame, action, down] triples)."""
    events = sorted(([int(f), a, bool(d)] for f, a, d in events),
                    key=lambda e: e[0])  # stable: keeps in-frame order
    trace = {
        "format": "17x9-tetris-trace",
        "spec_version": 1,
        "name": name,
        "description": description,
        "covers": list(covers),
        "seed": int(seed),
        "frames": int(frames),
        "digest_every": int(digest_every),
        "events": events,
    }
    result = run_trace(trace)
    trace["digests"] = result["digests"]
    trace["final"] = result["final"]
    return trace


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    for path in argv:
        with open(path) as fh:
            trace = json.load(fh)
        print(json.dumps(run_trace(trace), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
