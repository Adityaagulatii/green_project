"""Tests of the contract kit: the pin, the Python reference, the schemas and
every fixture file.

    cd contrib/displays && python -m pytest contract

Hand-checked vectors come from the spec's own text.  The replay_* functions
read each fixture file the way another language would (JSON only) and return
findings; test_replay_catches_a_tampered_fixture checks that they can fail.
The property tests (hypothesis) assert the Reduction contract's invariants;
HYPOTHESIS_PROFILE=thorough runs 20x the examples (use the heavy lock).
"""
import copy
import hashlib
import json
import os

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from contract import display_contract as dc
from contract import gen_fixtures

settings.register_profile("contract", max_examples=150, deadline=None, database=None,
                          suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("thorough", max_examples=3000, deadline=None, database=None,
                          suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "contract"))

GB = "green-building"
COORDINATOR_PIN = "/scratch/work/tetris-parallel/inputs/wal-sh-display-0.2.1/PROVENANCE"


def fixture(name):
    return json.loads((dc.FIXTURES / name).read_text("utf-8"))


# ------------------------------------------------------------------ the pin

def _provenance(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            out[parts[1]] = parts[0]
    return out


def test_pin_matches_provenance():
    listed = _provenance(dc.PIN / "PROVENANCE")
    assert set(listed) == {"spec.md", "capabilities.json"}
    for name, sha in listed.items():
        assert hashlib.sha256((dc.PIN / name).read_bytes()).hexdigest() == sha
    if os.path.exists(COORDINATOR_PIN):
        theirs = _provenance(COORDINATOR_PIN)
        assert {k: theirs[k] for k in listed} == listed


def test_presets_come_from_the_pin_in_the_spec_table_order():
    assert dc.preset_order() == ("cga40", "tetris", "green-building", "dc32", "gameboy",
                                 "trs80", "c64", "ws2812", "hub75", "blinkenlights", "arcade",
                                 "remote")
    assert set(dc.preset_order()) == set(dc.capabilities()["displays"])
    assert dc.SPEC_DEFAULT == "cga40" and dc.preset("hub75")["fps"] == 60
    for p in dc.presets().values():
        assert dc.check_grid(p["w"], p["h"]) is None
        assert p["levels"] == len(dc.palette(p["palette"]))


def test_the_spec_numbers():
    # Frame formats: 153 bytes for the Green Building, 1,000 for CGA, 2,048 for HUB75
    assert [dc.pal16_lengths(p["w"], p["h"])[0] for p in
            (dc.preset(GB), dc.preset("cga40"), dc.preset("hub75"))] == [153, 1000, 2048]
    # Limits: pal16 at 256 x 256 with prefix 65,538; hex 65,793; 65,536 cells
    assert dc.pal16_lengths(256, 256)[1] == dc.MAX_FRAME_BYTES == 65538
    assert dc.hex_lengths(256, 256)[1] == 65793
    assert dc.MAX_CELLS == 256 * 256 and dc.MAX_TTL == 900 and dc.MAX_FPS == 60
    # 65,537 is prime: only a dimension over 256 can make it
    assert all(65537 % k for k in range(2, 257))
    assert dc.check_grid(1, 65537) == "bad-h" and dc.check_grid(65537, 1) == "bad-w"


# ------------------------------------------------------------------ hand vectors

def test_level_rule_by_hand():
    assert [dc.level(i, 4) for i in range(16)] == [0] + [1] * 7 + [2] * 5 + [3] * 3
    assert [dc.level(i, 2) for i in range(16)] == [0] + [1] * 15
    assert [dc.level(i, 8) for i in range(16)] == [0, 1, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7]
    assert [dc.level(i, 16) for i in range(16)] == list(range(16))
    for n in range(2, 17):
        assert (dc.level(0, n), dc.level(1, n), dc.level(15, n)) == (0, 1, n - 1)
        # idx*(n-1)/15 is never a half, so every rounding mode agrees
        assert all((2 * i * (n - 1)) % 30 != 15 for i in range(16))
    for n in (0, 1, 17):
        with pytest.raises(ValueError):
            dc.level(3, n)
    assert dc.palette16(dc.palette("gb"))[5] == "#306230"


def test_quantize_by_hand():
    cga = dc.palette("cga")
    assert dc.quantize((0, 0, 85), cga) == 0          # black and blue tie: the lower
    assert dc.quantize((0, 0, 86), cga) == 1
    assert dc.quantize((255, 255, 255), cga) == 15
    assert dc.quantize((0x55, 0x55, 0x55), cga) == 8
    gb = dc.palette16(dc.palette("gb"))
    assert dc.quantize(dc.hex_rgb("#306230"), gb) == 1  # entries 1..7 share it: the lowest
    mono = dc.palette16(dc.palette("mono"))
    assert dc.quantize((128, 128, 128), mono) == 1 and dc.quantize((127, 127, 127), mono) == 0
    assert dc.quantize((18, 18, 18), dc.palette16(dc.palette("grey8"))) == 0  # a tie


def test_frame_codecs_by_hand():
    assert dc.decode_pal16(b"\x01\x02" + bytes([15]) * 4, 2, 2) == (bytes([15]) * 4, 258)
    assert dc.decode_hex("0f\nFa\n\n", 2, 2) == b"\x00\x0f\x0f\x0a"
    assert dc.encode_hex(b"\x00\x0f\x0f\x0a", 2, 2) == "0f\nfa\n"
    cases = [(b"\x10" * 3, "bad-frame-length"),       # wrong length wins (a choice)
             (b"\x10" * 4, "bad-format"), (b"", "bad-frame-length")]
    for data, reason in cases:
        with pytest.raises(dc.FrameError) as e:
            dc.decode_pal16(data, 2, 2)
        assert e.value.reason == reason
    for text, w, h, reason in (("ab\r\n", 2, 1, "bad-format"), ("ab\r\ncd\r\n", 2, 2,
                                                                 "bad-frame-length"),
                               ("ab\ncd", 2, 2, "bad-frame-length"), ("ab\ncd\n0", 2, 2,
                                                                      "bad-format"),
                               ("a\nbcd\n", 2, 2, "bad-format"), ("", 1, 1, "bad-frame-length")):
        with pytest.raises(dc.FrameError) as e:
            dc.decode_hex(text, w, h)
        assert e.value.reason == reason, text


def test_interop_by_hand():
    blp = dc.encode_blp(18, 8, [1, 0] * 72)
    assert blp[:4] == bytes.fromhex("deadbeef") and len(blp) == 12 + 144
    p = dc.parse_interop(blp)
    assert (p["kind"], p["w"], p["h"]) == ("blp", 18, 8)
    assert dc.interop_cells(p, None)[:2] == b"\x0f\x00"
    m = dc.parse_interop(dc.encode_mcuf(9, 17, [2] * 153, maxval=4))
    assert m["kind"] == "mcuf" and dc.interop_cells(m, None) == bytes([8]) * 153  # 7.5 -> 8
    assert [dc.udp_display(*g) for g in ((10, 20), (10, 18), (9, 17), (7, 7))] == [
        "tetris", "dc32", "green-building", None]


def test_sequence_rules_by_hand():
    assert dc.seq_accepts(None, 0) and dc.seq_accepts(5, None) and dc.seq_accepts(5, 5)
    assert not dc.seq_accepts(5, 4) and not dc.seq_accepts(65535, 0)
    assert dc.seq_accepts(65535, 0, "serial") and not dc.seq_accepts(0, 32768, "serial")
    assert dc.seq_accepts(0, 32767, "serial") and not dc.seq_accepts(1, 0, "serial")


# ------------------------------------------------------------------ the schemas

def test_validator_rejects_what_it_should():
    caps = gen_fixtures.caps_of(GB)
    assert dc.check_message(caps) == []
    for key in ("display", "w", "h", "fps", "format", "palette"):
        assert dc.check_message({k: v for k, v in caps.items() if k != key})
    assert dc.check_message({**caps, "w": True}) and dc.check_message({**caps, "w": 9.5})
    assert dc.check_message({**caps, "w": 9.0}) == []
    with pytest.raises(ValueError):
        dc.validate(1, {"oneOf": []})
    for op in dc.TO_RELAY + dc.FROM_RELAY:
        s = dc.schema(op)
        assert s["properties"]["op"] == {"const": op} and s["additionalProperties"] is True
    assert dc.schema("error")["properties"]["reason"]["enum"] == list(dc.REASONS)
    index = dc.schema("index")
    assert index["reasons"] == list(dc.REASONS)
    assert tuple(index["to_relay"]) == dc.TO_RELAY and tuple(index["from_relay"]) == dc.FROM_RELAY


def test_schemas_agree_with_jsonschema_if_installed():
    jsonschema = pytest.importorskip("jsonschema")
    for c in fixture("messages.json")["cases"]:
        m = c["message"]
        if isinstance(m, dict) and m.get("op") in dc.TO_RELAY + dc.FROM_RELAY:
            ok = jsonschema.Draft202012Validator(dc.schema(m["op"])).is_valid(m)
            assert ok == c["valid"], m


# ------------------------------------------------------------------ the fixtures

def test_fixtures_are_current():
    built = gen_fixtures.build()
    assert sorted(built) == sorted(p.name for p in dc.FIXTURES.glob("*.json"))
    for name, text in built.items():
        assert (dc.FIXTURES / name).read_text("utf-8") == text, (
            f"{name} drifted: python -m contract.gen_fixtures")


def replay_fold(doc):
    findings = []
    for c in doc["cases"]:
        s = dc.state_from_fixture(c["initial"])
        for i, e in enumerate(c["events"]):
            prev, s = s, dc.reduce_event(s, e)
            if dc.state_to_fixture(s) != c["states"][i]:
                findings.append(f"{c['name']}: state after event {i}")
            if dc.state_from_fixture(c["states"][i])["cells"] != s["cells"]:
                findings.append(f"{c['name']}: cells after event {i}")
            findings += [f"{c['name']}: {f}" for f in dc.check_invariants(s)]
            if "dirty" in c and [list(t) for t in dc.dirty(s, prev)] != c["dirty"][i]:
                findings.append(f"{c['name']}: dirty after event {i}")
    return findings


def replay_frames(doc):
    findings = []
    for c in doc["cases"]:
        data = dc.frame_from_json(c)
        got = gen_fixtures.decode(c["format"], data, c["w"], c["h"], c.get("palette"))
        exp = c["expect"]
        if got["ok"] != exp["ok"] or got.get("reason") != exp.get("reason") or got.get(
                "seq") != exp.get("seq"):
            findings.append(c["name"])
        elif exp["ok"] and dc.state_from_fixture(exp)["cells"] != dc.state_from_fixture(got)[
                "cells"]:
            findings.append(c["name"] + ": cells")
    return findings


def replay_equivalence(doc):
    findings = []
    for c in doc["cases"]:
        w, h, cells = c["w"], c["h"], dc.state_from_fixture(c)["cells"]
        a = dc.decode_pal16(dc.frame_from_json(c["pal16"]), w, h)[0]
        b = dc.decode_hex(dc.frame_from_json(c["hex"]), w, h)
        if not a == b == cells:
            findings.append(c["name"])
    return findings


def replay_sequence(doc):
    findings = []
    for c in doc["cases"]:
        last, got = None, []
        for s in c["seqs"]:
            ok = dc.seq_accepts(last, s, c["rule"])
            got.append(ok)
            last = s if ok and s is not None else last
        if got != c["accepted"]:
            findings.append(f"{c['rule']}: {c['name']}")
    return findings


def replay_levels(doc):
    findings = []
    for c in doc["cases"]:
        if "error" in c:
            try:
                dc.level(1, c["n"])
                findings.append(f"n={c['n']} should raise")
            except ValueError:
                pass
            continue
        if [dc.level(i, c["n"]) for i in range(16)] != c["levels"]:
            findings.append(f"levels n={c['n']}")
        if "palette" in c and dc.palette16(dc.palette(c["palette"])) != c["palette16"]:
            findings.append(f"palette16 {c['palette']}")
    return findings


def replay_quantize(doc):
    return [f"{c['palette']} {c['rgb']}" for c in doc["cases"]
            if dc.quantize(c["rgb"], dc.palette16(dc.palette(c["palette"]))) != c["index"]]


def replay_grid(doc):
    findings = [f"{c['w']}x{c['h']}" for c in doc["cases"] if dc.check_grid(c["w"], c["h"])
                != c["error"]]
    for g in doc["lengths"]:
        w, h = g["w"], g["h"]
        if (g["pal16"], g["hex"]) != (list(dc.pal16_lengths(w, h)), list(dc.hex_lengths(w, h))):
            findings.append(g["grid"])
    return findings


def replay_interop(doc):
    findings = []
    for c in doc["cases"]:
        exp = c["expect"]
        try:
            p = dc.parse_interop(dc.frame_from_json(c))
        except dc.FrameError as e:
            if exp != {"ok": False, "reason": e.reason}:
                findings.append(c["name"])
            continue
        d = dc.udp_display(p["w"], p["h"])
        if not exp["ok"] or exp["display"] != d or (p["w"], p["h"]) != (exp["w"], exp["h"]):
            findings.append(c["name"])
        elif d and dc.interop_cells(p, gen_fixtures.pal16_of(d)) != dc.state_from_fixture(exp)[
                "cells"]:
            findings.append(c["name"] + ": cells")
    return findings


def replay_messages(doc):
    findings = [json.dumps(c["message"]) for c in doc["cases"]
                if (not dc.check_message(c["message"])) != c["valid"]]
    return findings + [r["text"] for r in doc["raw"] if r["relay"] not in dc.REASONS]


REPLAY = {"caps.json": replay_fold, "expiry.json": replay_fold, "fold.json": replay_fold,
          "frames.json": replay_frames, "equivalence.json": replay_equivalence,
          "sequence.json": replay_sequence, "levels.json": replay_levels,
          "quantize.json": replay_quantize, "grid.json": replay_grid,
          "interop.json": replay_interop, "messages.json": replay_messages}


@pytest.mark.parametrize("name", sorted(REPLAY))
def test_replay_fixture(name):
    doc = fixture(name)
    assert doc["spec"] == gen_fixtures.SPEC and doc["cases"]
    assert REPLAY[name](doc) == []


def test_every_fixture_file_is_replayed():
    assert sorted(REPLAY) == sorted(p.name for p in dc.FIXTURES.glob("*.json"))


def test_replay_catches_a_tampered_fixture():
    fold = copy.deepcopy(fixture("fold.json"))
    fold["cases"][0]["states"][3]["seq"] += 1
    assert replay_fold(fold)
    frames = copy.deepcopy(fixture("frames.json"))
    bad = next(c for c in frames["cases"] if c["expect"]["ok"])
    bad["expect"] = {"ok": False, "reason": "rate"}
    assert replay_frames(frames)
    q = copy.deepcopy(fixture("quantize.json"))
    q["cases"][0]["index"] ^= 1
    assert replay_quantize(q)
    seq = copy.deepcopy(fixture("sequence.json"))
    seq["cases"][0]["accepted"][1] = True
    assert replay_sequence(seq)


def test_fixtures_cover_the_boundaries():
    frames = fixture("frames.json")["cases"]
    names = {c["name"] for c in frames}
    for grid in ("1x1", "256x256", "256x1", "1x256"):
        assert f"{grid} pal16: valid, sequence prefix 258" in names
    top = [c for c in frames if c["name"].startswith("256x256")]
    lengths = {c["format"]: set() for c in top}
    for c in top:
        lengths[c["format"]].add(len(dc.frame_from_json(c)))
    assert {65536, 65538, 65535, 65537, 65539} <= lengths["pal16"]
    assert {65792, 65793, 65791, 65794} <= lengths["hex"]
    interop = {c["name"] for c in fixture("interop.json")["cases"]}
    assert any("maxval 255" in n for n in interop) and any("maxval 7" in n for n in interop)
    ties = [c for c in fixture("quantize.json")["cases"] if "; tie between" in c["note"]]
    # mono has no exact tie: black and white are equally far from (r, g, b)
    # only when r + g + b = 3 * 255 / 2 = 382.5
    assert all((3 * 255 * 255) % 510 for _ in [0]) and 3 * 255 % 2 == 1
    assert {c["palette"] for c in ties} == set(dc.capabilities()["palettes"]) - {"mono"}


# ------------------------------------------------------------------ properties

grids = st.tuples(st.integers(1, 24), st.integers(1, 24))


@st.composite
def frames_on(draw, w, h):
    return bytes(draw(st.lists(st.integers(0, 15), min_size=w * h, max_size=w * h)))


@given(grids.flatmap(lambda g: st.tuples(st.just(g), frames_on(*g), st.integers(0, 65535),
                                         st.booleans())))
def test_decode_is_format_symmetric(args):
    (w, h), cells, seq, blank = args
    a, s = dc.decode_pal16(dc.encode_pal16(cells, seq), w, h)
    assert a == dc.decode_hex(dc.encode_hex(cells, w, h, blank), w, h) == cells and s == seq
    assert dc.decode_hex(dc.encode_hex(cells, w, h).upper(), w, h) == cells
    assert not dc.is_interop(dc.encode_pal16(cells)) and not dc.is_interop(
        dc.encode_pal16(cells, seq))


json_leaf = (st.none() | st.booleans() | st.integers(-3, 300) | st.floats(allow_nan=False)
             | st.text(max_size=4) | st.sampled_from(["caps", "lease", "error", "frame", "tick",
                                                       "open", "close", GB, "pal16", "hex"]))
json_value = st.recursive(json_leaf, lambda c: st.lists(c, max_size=3)
                          | st.dictionaries(st.sampled_from(["op", "event", "w", "h", "holder",
                                                             "expires", "reason", "now", "bytes",
                                                             "text", "format", "palette", "fps",
                                                             "display"]) | st.text(max_size=3),
                                            c, max_size=5), max_leaves=12)
PAL = dc.palette16(dc.palette("cga"))
good_events = st.one_of(
    st.builds(lambda n, f: gen_fixtures.caps_of(n, f), st.sampled_from(dc.preset_order()),
              st.sampled_from(dc.FANOUT_FORMATS)),
    st.builds(lambda h, e: {"op": "lease", "display": GB, "holder": h, "expires": e},
              st.none() | st.text(min_size=1, max_size=5), st.none() | st.integers(0, 2000)),
    st.builds(lambda r: {"op": "error", "reason": r}, st.sampled_from(dc.REASONS)),
    st.builds(lambda t: {"event": "tick", "now": t}, st.floats(0, 2000)),
    st.sampled_from([{"event": "open"}, {"event": "close"}]),
    st.builds(lambda b: {"event": "frame", "data": b},
              st.binary(max_size=160) | st.sampled_from([bytes(153), bytes(1000), bytes(155)])
              | st.lists(st.integers(0, 17), min_size=153, max_size=153).map(bytes)),
    st.builds(lambda t: {"event": "frame", "data": t}, st.text("0123456789abcdefg\n", max_size=40)
              | st.sampled_from([dc.encode_hex(bytes(153), 9, 17)])),
)
events = st.one_of(good_events, json_value, good_events.flatmap(
    lambda e: st.builds(lambda k, v: {**e, k: v}, st.sampled_from(sorted(e)), json_value)))


@given(st.lists(events, max_size=25))
def test_reduce_event_is_total_and_keeps_the_invariants(evs):
    s = dc.initial_state(GB)
    for e in evs:
        prev = s
        s = dc.reduce_event(s, e)                       # total: never raises
        assert dc.check_invariants(s) == []             # cells == w*h, 0..15, known status
        assert s["seq"] >= prev["seq"]                   # monotone non-decreasing
        if s["seq"] == prev["seq"] and not (isinstance(e, dict) and e.get("op") == "caps"):
            assert s["cells"] == prev["cells"]          # a dropped frame changes no cell
        if s["dropped"] > prev["dropped"]:
            assert {**s, "dropped": prev["dropped"]} == prev  # a drop changes nothing else
        if (s["w"], s["h"]) == (prev["w"], prev["h"]):
            cells = bytearray(prev["cells"])
            for x, y, c in dc.dirty(s, prev):
                cells[y * s["w"] + x] = c
            assert bytes(cells) == s["cells"]


@given(st.integers(2, 16))
def test_level_is_monotone_and_keeps_lit_cells_lit(n):
    ls = [dc.level(i, n) for i in range(16)]
    assert ls[0] == 0 and all(x >= 1 for x in ls[1:]) and ls[15] == n - 1
    assert ls == sorted(ls) and set(ls) == set(range(n))


@given(st.sampled_from(sorted(dc.capabilities()["palettes"])),
       st.tuples(st.integers(0, 255), st.integers(0, 255), st.integers(0, 255)))
def test_quantize_is_the_nearest_with_ties_low(name, rgb):
    pal = [dc.hex_rgb(c) for c in dc.palette16(dc.palette(name))]
    d = [sum((a - b) ** 2 for a, b in zip(rgb, c, strict=True)) for c in pal]
    assert dc.quantize(rgb, pal) == d.index(min(d))


@given(st.sampled_from(dc.preset_order()).flatmap(
    lambda n: st.tuples(st.just(n), frames_on(dc.preset(n)["w"], dc.preset(n)["h"]))))
def test_rgb24_round_trip_renders_alike(args):
    name, cells = args
    p, pal = dc.preset(name), gen_fixtures.pal16_of(name)
    back, _ = dc.decode_rgb24(dc.encode_rgb24(cells, pal), p["w"], p["h"], pal)
    assert [pal[c] for c in back] == [pal[c] for c in cells]
    assert all(pal.index(pal[c]) == c for c in back)     # the lowest index of the colour


@given(st.integers(0, 65535), st.integers(0, 65535))
def test_serial_rule_is_rfc1982(last, seq):
    assert dc.seq_accepts(last, seq, "serial") == ((seq - last) % 65536 < 32768)
    assert dc.seq_accepts(last, seq, "literal") == (seq >= last)
