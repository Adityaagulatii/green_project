"""The Python engine against the sealed traces, and the runner's own checks
(the verifier is verified by bin/verify.sh; these are its unit tests)."""

import copy
import glob
import importlib.util
import json
import os

import pytest

from tetris_engine.conformance import make_trace, run_trace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TRACES = sorted(glob.glob(os.path.join(ROOT, "spec", "conformance", "traces",
                                       "*.json")))
_spec = importlib.util.spec_from_file_location(
    "conformance_run", os.path.join(ROOT, "spec", "conformance", "run.py"))
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def test_there_are_at_least_ten_traces():
    assert len(TRACES) >= 10


@pytest.mark.parametrize("path", TRACES, ids=os.path.basename)
def test_engine_passes_trace(path):
    trace = _load(path)
    assert runner.validate(trace, path) == []
    result = run_trace(trace)
    assert runner.compare(trace, result) == []


def test_runner_rejects_corruptions():
    trace = _load(TRACES[0])
    good = run_trace(trace)
    bad = copy.deepcopy(good)
    bad["digests"][3] = "0" * 64
    assert runner.compare(trace, bad)
    bad = copy.deepcopy(good)
    bad["final"]["score"] += 100
    assert runner.compare(trace, bad)
    bad = copy.deepcopy(good)
    bad["digests"].pop()
    assert runner.compare(trace, bad)


def test_runner_validates_structure():
    trace = _load(TRACES[1])
    path = TRACES[1]
    for mutate in (lambda t: t.pop("seed"),
                   lambda t: t.__setitem__("name", "other"),
                   lambda t: t["digests"].pop(),
                   lambda t: t["events"].insert(0, [10 ** 6, "left", True]),
                   lambda t: t["events"].append([0, "jump", True])):
        t = copy.deepcopy(trace)
        mutate(t)
        assert runner.validate(t, path), mutate


def test_digest_every_and_final_frame_rule():
    t = make_trace("x", "", 3, 25, [[0, "left", True]], digest_every=10)
    assert len(t["digests"]) == 4  # frames 0, 10, 20 and the last (24)
    t = make_trace("x", "", 3, 21, [], digest_every=10)
    assert len(t["digests"]) == 3  # frame 20 is already the last


def test_trace_set_digest_is_order_independent():
    assert runner.trace_set_digest(TRACES) == \
        runner.trace_set_digest(list(reversed(TRACES)))
