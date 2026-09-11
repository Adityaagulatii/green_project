"""Write contract/fixtures/*.json: the spec's Conformance fixtures as data.

    cd contrib/displays
    python -m contract.gen_fixtures            # (re)write every file
    python -m contract.gen_fixtures --check    # exit 1 if any file differs

The expected values come from the Python reference (display_contract.py).
contract/test_display_contract.py pins that reference to hand-checked vectors
taken from the spec's own text, and replays every fixture file the way
another language would read it.  README.md describes each file.
"""
import argparse
import json
import random
import sys

from . import display_contract as dc

SPEC = "wal.sh/tools/display v0.2.1"
GB = "green-building"
BOUNDARY_GRIDS = ((1, 1), (256, 1), (1, 256), (256, 256))


def pattern(w, h):
    """A test picture that uses every index 0..15 on grids of 16 cells or more."""
    return bytes((x + 3 * y + (x * y) // 5) % 16 for y in range(h) for x in range(w))


def cells_json(cells):
    j = dc.frame_to_json(bytes(cells))
    return {"cells": j["bytes"]} if "bytes" in j else {"cells_rle": j["bytes_rle"]}


def pal16_of(name):
    return dc.palette16(dc.palette(dc.preset(name)["palette"]))


def caps_of(name, fmt="pal16"):
    p = dc.preset(name)
    return {"op": "caps", "display": name, "w": p["w"], "h": p["h"], "fps": p["fps"],
            "format": fmt, "palette": pal16_of(name)}


def fold_case(name, initial, events, note=None, dirty=False):
    """A fold sequence: the state after each event, and the dirty cells if asked."""
    states, dirt, s = [], [], initial
    for e in events:
        prev, s = s, dc.reduce_event(s, e)
        states.append(dc.state_to_fixture(s))
        dirt.append([list(t) for t in dc.dirty(s, prev)])
    case = {"name": name} | ({"note": note} if note else {})
    case |= {"initial": dc.state_to_fixture(initial),
             "events": [dc.event_to_json(e) for e in events], "states": states}
    return case | ({"dirty": dirt} if dirty else {})


def frame(data):
    return {"event": "frame", "data": data}


# ------------------------------------------------------------------ caps

def caps_fixtures():
    base, fixed = dc.initial_state(), dc.initial_state(fixed=True)
    cases = [fold_case(f"caps {n} on an unfixed grid", base, [caps_of(n)])
             for n in dc.preset_order()]
    cases += [fold_case(f"caps {n} on a grid the URL fixed at 40x25", fixed, [caps_of(n)])
              for n in dc.preset_order()]
    cases.append(fold_case("caps remote (9x17) on a grid fixed at 9x17",
                           dc.initial_state(GB, fixed=True), [caps_of("remote")]))
    cases.append(fold_case("caps announcing hex", base, [caps_of("hub75", "hex")]))
    return {"description": "caps for every preset, and the resulting cells length (the "
                           "state after caps); caps against a fixed grid, and the resulting "
                           "error (status error, error.reason bad-src, nothing else changed).",
            "cases": cases}


# ------------------------------------------------------------------ frames

def decode(fmt, data, w, h, pal=None):
    try:
        if fmt == "pal16":
            cells, seq = dc.decode_pal16(data, w, h)
        elif fmt == "hex":
            cells, seq = dc.decode_hex(data, w, h), None
        else:
            cells, seq = dc.decode_rgb24(data, w, h, pal)
        return {"ok": True, **cells_json(cells), "seq": seq}
    except dc.FrameError as e:
        return {"ok": False, "reason": e.reason}


def pal16_variants(w, h, cells):
    n, fill = w * h, bytes(w * h)
    yield "valid", cells, ()
    yield "valid, sequence prefix 258", b"\x01\x02" + fill, ("boundary",)
    yield "sequence prefix 0", b"\x00\x00" + fill, ("boundary",)
    yield "sequence prefix 65535", b"\xff\xff" + fill, ("boundary",)
    yield "one byte short (w*h - 1)", fill[:-1], ("boundary",)
    yield "one byte long (w*h + 1)", fill + b"\x00", ("boundary",)
    yield "prefix, one byte long (w*h + 3)", b"\x00\x01" + fill + b"\x00", ("boundary",)
    yield "empty", b"", ("boundary",)
    yield "every byte 15", b"\x0f" * n, ("boundary",)
    yield "one byte of 16", fill[:-1] + b"\x10", ("boundary",)
    yield "one byte of 255", fill[:-1] + b"\xff", ("boundary",)
    yield "one byte of 16, one byte short", fill[:-2] + b"\x10", ("boundary", "choice")


def hex_variants(w, h, cells):
    t, z = dc.encode_hex(cells, w, h), dc.encode_hex(bytes(w * h), w, h)
    yield "valid", t, ()
    yield "valid, closing blank line", z + "\n", ("boundary",)
    yield "missing the final LF (one short)", z[:-1], ("boundary",)
    yield "one character long, not LF", z + "0", ("boundary",)
    yield "two characters long", z + "\n\n", ("boundary",)
    yield "empty", "", ("boundary",)
    yield "every digit f", dc.encode_hex(b"\x0f" * (w * h), w, h), ("boundary",)
    yield "one g", "g" + z[1:], ("boundary",)
    yield "one upper-case F", "F" + z[1:], ("boundary", "choice")
    yield "CRLF line ends", z.replace("\n", "\r\n"), ("boundary",)
    if w >= 2 and h >= 2:
        yield "an LF one place early (length right)", z[:w - 1] + "\n0" + z[w + 1:], ("boundary",)


def rgb24_variants(w, h, cells, pal):
    black = dc.encode_rgb24(bytes(w * h), pal)   # the lengths matter, not the picture
    yield "valid", dc.encode_rgb24(cells, pal), ()
    yield "valid, sequence prefix 7", b"\x00\x07" + black, ()
    yield "one byte short", black[:-1], ("boundary",)
    yield "one byte long", black + b"\x00", ("boundary",)
    yield "w*h bytes (a pal16 length)", bytes(w * h), ("boundary",)


def frame_fixtures():
    cases = []

    def add(grid, fmt, variants, w, h, pal=None):
        for name, data, tags in variants:
            c = {"name": f"{grid} {fmt}: {name}", "w": w, "h": h, "format": fmt}
            if pal:
                c["palette"] = pal
            c |= dc.frame_to_json(data) | {"expect": decode(fmt, data, w, h, pal)}
            cases.append(c | ({"tags": list(tags)} if tags else {}))

    for n, p in dc.presets().items():
        w, h = p["w"], p["h"]
        cells = pattern(w, h)
        add(n, "pal16", pal16_variants(w, h, cells), w, h)
        add(n, "hex", hex_variants(w, h, cells), w, h)
        add(n, "rgb24", rgb24_variants(w, h, cells, pal16_of(n)), w, h, pal16_of(n))
    for w, h in BOUNDARY_GRIDS:
        cells = bytes([15]) * (w * h)
        add(f"{w}x{h}", "pal16", pal16_variants(w, h, cells), w, h)
        add(f"{w}x{h}", "hex", hex_variants(w, h, cells), w, h)
    return {"description": "Frames per preset and format: one valid frame, each wrong length "
                           "(off by one either way), a pal16 byte of 16 and of 255, a hex g; "
                           "plus the grid boundaries 1x1, 256x1, 1x256 and 256x256 (pal16 up "
                           "to 65,538 bytes, hex up to 65,793). expect is decode's result: "
                           "cells and seq, or the drop reason. Tag choice marks this kit's "
                           "reading where the spec is silent (README.md).",
            "cases": cases}


def equivalence_fixtures():
    cases = []
    for n, p in dc.presets().items():
        w, h = p["w"], p["h"]
        rnd = random.Random(n)
        for label, cells in (("pattern", pattern(w, h)),
                             ("random", bytes(rnd.randrange(16) for _ in range(w * h)))):
            pal16 = dc.encode_pal16(cells, seq=rnd.randrange(65536))
            hx = dc.encode_hex(cells, w, h, blank=label == "random")
            assert dc.decode_pal16(pal16, w, h)[0] == dc.decode_hex(hx, w, h) == cells
            cases.append({"name": f"{n} {label}", "w": w, "h": h,
                          "pal16": dc.frame_to_json(pal16), "hex": dc.frame_to_json(hx),
                          **cells_json(cells)})
    return {"description": "The same indices as pal16 (with a sequence prefix) and as hex "
                           "(the random ones with the closing blank line): both decode to "
                           "cells, and a sink folding either renders the same (refutation 6).",
            "cases": cases}


# ------------------------------------------------------------------ sequence and expiry

def sequence_fixtures():
    runs = [
        ("reordered: lower than the last accepted", [5, 4, 6]),
        ("equal is not lower", [5, 5]),
        ("from 0", [0, 1, 2]),
        ("from 65535", [65535, 65535]),
        ("wrap 65535 -> 0", [65534, 65535, 0, 1]),
        ("no prefix after prefixed frames", [3, None, 2, None, 4]),
        ("no prefix first", [None, 9, 8]),
        ("half the space ahead", [0, 32767, 0, 32768]),
    ]
    cases = []
    for rule in dc.SEQ_RULES:
        for name, seqs in runs:
            last, got = None, []
            for s in seqs:
                ok = dc.seq_accepts(last, s, rule)
                got.append(ok)
                if ok and s is not None:
                    last = s
            cases.append({"name": name, "rule": rule, "seqs": seqs, "accepted": got,
                          "reasons": [None if ok else "rate" for ok in got]})
    return {"description": "Relay-side: frames with these sequence prefixes (null: none), "
                           "at a legal pace, one lease. A dropped frame gets error rate. "
                           "literal is the spec's text (seq >= last accepted); serial is RFC "
                           "1982 over 16 bits, which survives the wrap. A frame with no "
                           "prefix is never dropped by sequence and leaves last alone.",
            "cases": cases}


def expiry_fixtures():
    cases = []
    for name, fmt in ((GB, "pal16"), ("cga40", "pal16"), ("hub75", "hex")):
        p = dc.preset(name)
        w, h = p["w"], p["h"]
        pic = dc.fanout_frame(pattern(w, h), w, h, fmt)
        events = [{"event": "open"}, caps_of(name, fmt),
                  {"op": "lease", "display": name, "holder": "src@lab", "expires": 1000},
                  frame(pic), {"event": "tick", "now": 999},
                  {"op": "lease", "display": name, "holder": None, "expires": None},
                  frame(dc.black_frame(w, h, fmt))]
        cases.append(fold_case(f"{name} {fmt}: lease expiry", dc.initial_state(name), events,
                               note="the relay's expiry: lease holder null, then a black frame. "
                                    "The last state is what the DOM shows after it: every cell "
                                    "0, holder null; status is live again, because the table "
                                    "sets live on any valid frame (an open question)."))
        early = events[:4] + [{"event": "tick", "now": 1000}] + events[5:]
        cases.append(fold_case(f"{name} {fmt}: a tick at expires, before the relay's lease",
                               dc.initial_state(name), early))
    short = [{"event": "open"}, caps_of(GB),
             {"op": "lease", "display": GB, "holder": "src@lab", "expires": 1000},
             frame(pattern(9, 17)), {"op": "lease", "holder": None},
             frame(dc.black_frame(9, 17, "pal16"))]
    cases.append(fold_case(f"{GB} pal16: the Rules' short expiry message", dc.initial_state(GB),
                           short, note="the Rules write {op: lease, holder: null}; the Viewer "
                                       "section adds display and expires; both fold alike"))
    return {"description": "The lease expiry sequence as a viewer folds it, one state per "
                           "event.", "cases": cases}


def relay_answer(m, errs):
    """The error a relay answers when a client sends M, as JSON text, with no lease.
    Text that does not start with { is not control: by the first-character
    rule it is a hex frame, and from a non-holder that is not-holder."""
    if not json.dumps(m).startswith("{"):
        return "not-holder"
    if not isinstance(m.get("op"), str):
        return "bad-format"
    if m["op"] not in dc.TO_RELAY:
        return "unknown-op"
    return "bad-format" if errs else None


# ------------------------------------------------------------------ the fold

def fold_fixtures():
    gb = caps_of(GB)
    pic = pattern(9, 17)
    s0 = dc.initial_state(GB)
    lease = {"op": "lease", "display": GB, "holder": "src@lab", "expires": 100}
    cases = [
        fold_case("every row of the table, green-building", s0, [
            {"event": "open"}, gb, lease, frame(pic), frame(pic[:-1]),
            frame(pic[:-1] + b"\x10"), frame(b"\x00\x07" + bytes(153)),
            {"op": "error", "reason": "rate"}, {"event": "tick", "now": 99.5},
            {"event": "tick", "now": 100}, {"op": "lease", "display": GB, "holder": None,
                                              "expires": None},
            {"event": "close"}, {"op": "dance"}], dirty=True),
        fold_case("a hex sink: text frames only", s0, [
            caps_of(GB, "hex"), frame(dc.encode_hex(pic, 9, 17)), frame(pic),
            frame(dc.encode_hex(pic, 9, 17).upper()), frame("g" + dc.encode_hex(pic, 9, 17)[1:])],
            dirty=True),
        fold_case("a pal16 sink drops text frames", s0, [gb, frame(dc.encode_hex(pic, 9, 17))]),
        fold_case("caps against a fixed grid, then frames", dc.initial_state(fixed=True), [
            gb, frame(bytes(153)), frame(bytes([1]) * 1000)],
            note="the refused caps leaves w, h and format; a frame of the fixed grid's length "
                 "is still folded (the adapter closes the socket; the fold does not)"),
        fold_case("a short palette in caps", dc.initial_state("dc32"), [caps_of("dc32"),
                                                                        frame(pattern(10, 18))]),
        fold_case("seq counts folded frames and never falls", s0,
                  [gb] + [frame(bytes([k]) * 153) for k in range(4)] + [frame(b"")]
                  + [frame(bytes([9]) * 153)]),
        fold_case("anything else is dropped", s0, [
            gb, {"op": "granted", "lease": "x", "w": 9, "h": 17, "fps": 30, "format": "pal16",
                 "palette": gb["palette"], "expires": 5},
            {"op": "caps", "display": GB, "w": 0, "h": 17, "fps": 30, "format": "pal16",
             "palette": gb["palette"]},
            {"op": "caps", "display": GB, "w": 9, "h": 17, "fps": 30, "format": "rgb24",
             "palette": gb["palette"]},
            {"op": "lease", "display": GB, "holder": 5, "expires": None},
            {"op": "error", "reason": "not holder"}, {"event": "wave"}, {"event": "tick"},
            {"event": "tick", "now": "soon"}, {"event": "frame"},
            {"event": "frame", "bytes": [256]}, {"op": 7}, {}, 42, None, [], "text"]),
    ]
    return {"description": "The sink's fold (spec: Reduction contract): event sequences and "
                           "the state after each. Events are control messages (objects with "
                           "op) or local events {event: open|close|tick|frame}; a tick carries "
                           "now (unix s), a frame bytes/bytes_rle or text/text_rle. dirty "
                           "lists the (x, y, idx) triples that changed, where given.",
            "cases": cases}


# ------------------------------------------------------------------ levels and colour

def level_fixtures():
    cases = [{"n": n, "levels": [dc.level(i, n) for i in range(16)],
              "idx0": dc.level(0, n), "idx1": dc.level(1, n), "idx15": dc.level(15, n)}
             for n in range(2, 17)]
    cases += [{"n": n, "error": "level rule needs 2 to 16 entries"} for n in (0, 1, 17)]
    cases += [{"palette": name, "n": len(c), "levels": [dc.level(i, len(c)) for i in range(16)],
               "palette16": dc.palette16(c)} for name, c in dc.capabilities()["palettes"].items()]
    return {"description": "The level rule: level(idx) = 0 for idx 0, else max(1, round(idx * "
                           "(n - 1) / 15)); for every palette size 2..16 and every named "
                           "palette, with palette16 (the 16 colours a relay announces in caps). "
                           "idx*(n-1)/15 is never a half, so no rounding mode is needed.",
            "cases": cases}


def quantize_case(name, pal, rgb, v, note):
    d = [sum((a - b) ** 2 for a, b in zip(v, c, strict=True)) for c in rgb]
    near = [i for i, x in enumerate(d) if x == min(d)]
    distinct = len({pal[i] for i in near})
    tie = "tie" if distinct > 1 else "same colour" if len(near) > 1 else None
    return {"palette": name, "rgb": list(v), "index": dc.quantize(v, rgb),
            "note": note + (f"; {tie} between {near}" if tie else "")}


def quantize_fixtures():
    cases = []
    for name in dc.capabilities()["palettes"]:
        pal = dc.palette16(dc.palette(name))
        rgb = [dc.hex_rgb(c) for c in pal]

        def add(v, note, name=name, pal=pal, rgb=rgb):
            cases.append(quantize_case(name, pal, rgb, v, note))

        for i, c in enumerate(rgb):
            if pal.index(pal[i]) == i:
                add(c, f"exact entry {i}")
        seen = set()
        for i, a in enumerate(rgb):
            for j, b in enumerate(rgb):
                mid = tuple((x + y) // 2 for x, y in zip(a, b, strict=True))
                if i < j and pal[i] != pal[j] and all((x + y) % 2 == 0 for x, y in
                                                      zip(a, b, strict=True)) and mid not in seen:
                    seen.add(mid)
                    add(mid, f"midpoint of {i} and {j}")
        for v in ((0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255),
                  (128, 128, 128)):
            add(v, "extreme")
        rnd = random.Random(name)
        for _ in range(8):
            add(tuple(rnd.randrange(256) for _ in range(3)), "random")
    return {"description": "rgb24 quantization, relay-side: the nearest of the 16 announced "
                           "colours (palette16 of the named palette) by squared sRGB distance, "
                           "ties to the lower index. A note says when the answer is a tie.",
            "cases": cases}


# ------------------------------------------------------------------ limits, interop, messages

def grid_fixtures():
    grids = [(1, 1), (256, 256), (256, 1), (1, 256), (0, 1), (1, 0), (257, 1), (1, 257),
             (256, 257), (65537, 1), (1, 65537), (-1, 5), (1.5, 2), ("9", 17), (True, 1)]
    cases = [{"w": w, "h": h, "error": dc.check_grid(w, h)} for w, h in grids]
    lengths = []
    for name, (w, h) in [(n, (p["w"], p["h"])) for n, p in dc.presets().items()] + [
            (f"{w}x{h}", (w, h)) for w, h in BOUNDARY_GRIDS]:
        lengths.append({"grid": name, "w": w, "h": h, "pal16": list(dc.pal16_lengths(w, h)),
                        "hex": list(dc.hex_lengths(w, h)), "rgb24": [3 * w * h, 3 * w * h + 2]})
    return {"description": "Grid limits (w, h in 1..256, w*h at most 65,536: the page's bad-w, "
                           "bad-h; the cell cap never binds alone, 65,537 being prime) and the "
                           "legal frame lengths per grid. The spec's numbers: 153 bytes for "
                           "green-building, 1,000 for cga40, 2,048 for hub75, 65,538 for "
                           "pal16 and 65,793 for hex at 256x256.",
            "cases": cases, "lengths": lengths}


def interop_fixtures():
    bits = [(x + y) % 2 for y in range(8) for x in range(18)]
    packets = [
        ("BLP 18x8 checkerboard (maxval 1)", dc.encode_blp(18, 8, bits)),
        ("BLP 18x8 with a 2", dc.encode_blp(18, 8, [2] + bits[1:])),
        ("MCUF 18x8 maxval 1", dc.encode_mcuf(18, 8, bits, maxval=1)),
        ("MCUF 20x26 maxval 7", dc.encode_mcuf(20, 26, [i % 8 for i in range(520)], maxval=7)),
        ("MCUF 16x16 maxval 15 (== pal16)", dc.encode_mcuf(16, 16, [i % 16 for i in range(256)])),
        ("MCUF 64x32 maxval 255", dc.encode_mcuf(64, 32, [i % 256 for i in range(2048)],
                                                 maxval=255)),
        ("MCUF 9x17 maxval 2 (a half: 1 -> 7.5)", dc.encode_mcuf(9, 17, [i % 3 for i in range(153)],
                                                                maxval=2)),
        ("MCUF 9x17 RGB maxval 255", dc.encode_mcuf(9, 17, [(i * 37) % 256 for i in range(459)],
                                                    channels=3, maxval=255)),
        ("MCUF 10x18 RGB maxval 15", dc.encode_mcuf(10, 18, [i % 16 for i in range(540)],
                                                    channels=3, maxval=15)),
        ("MCUF 10x20 maxval 15", dc.encode_mcuf(10, 20, [15] * 200)),
        ("MCUF 7x7: no display has this grid", dc.encode_mcuf(7, 7, [1] * 49)),
        ("MCUF value over maxval", dc.encode_mcuf(20, 26, [8] * 520, maxval=7)),
        ("MCUF channels 2", dc.encode_mcuf(4, 4, [0] * 32, channels=2)),
        ("MCUF maxval 0", dc.encode_mcuf(4, 4, [0] * 16, maxval=0)),
        ("MCUF grid 0x4", dc.encode_mcuf(0, 4, [])),
        ("MCUF grid 257x1", dc.encode_mcuf(257, 1, [0] * 257)),
        ("MCUF payload one byte short", dc.encode_mcuf(16, 16, [0] * 255)),
        ("BLP header only, cut short", dc.encode_blp(18, 8, [])[:10]),
        ("no magic", b"\x00\x01\x02\x03" + bytes(12)),
    ]
    cases = []
    for name, data in packets:
        c = {"name": name, **dc.frame_to_json(data)}
        try:
            p = dc.parse_interop(data)
            d = dc.udp_display(p["w"], p["h"])
            exp = {"ok": True, "kind": p["kind"], "w": p["w"], "h": p["h"],
                   "channels": p["channels"], "maxval": p["maxval"], "display": d}
            if d:
                exp |= cells_json(dc.interop_cells(p, pal16_of(d)))
        except dc.FrameError as e:
            exp = {"ok": False, "reason": e.reason}
        cases.append(c | {"expect": exp})
    return {"description": "BLP and MCUF packets (UDP 2323, or WebSocket binary), relay-side: "
                           "the display by width x height (first in the spec's Presets table "
                           "order: tetris before c64, dc32 before gameboy, green-building "
                           "before remote) and the pal16 cells: one channel scaled to 0..15 "
                           "(round half up), three channels scaled to 0..255 and quantized. "
                           "Header layouts are README.md's; the pin names only the magics.",
            "cases": cases}


def message_fixtures():
    cga = dc.palette("cga")
    ok = {"view": {"op": "view", "display": GB},
          "reserve": {"op": "reserve", "name": "emacs@minibos", "display": GB, "ttl": 300,
                      "format": "pal16"},
          "renew": {"op": "renew"}, "release": {"op": "release"},
          "caps": caps_of(GB),
          "lease": {"op": "lease", "display": GB, "holder": "emacs@minibos", "expires": 1789000000},
          "granted": {"op": "granted", "lease": "a1b2", "w": 9, "h": 17, "fps": 30,
                      "format": "pal16", "palette": cga, "expires": 1789000000},
          "busy": {"op": "busy", "holder": "emacs@minibos", "expires": 1789000000},
          "error": {"op": "error", "reason": "rate"}}
    msgs = list(ok.values()) + [
        {"op": "view"}, {"op": "view", "display": ""}, {"op": "view", "display": 7},
        {"op": "reserve", "name": "s"}, {"op": "reserve"}, {"op": "reserve", "name": ""},
        *({"op": "reserve", "name": "s", "ttl": t} for t in (0, 1, 900, 901, -1, 1.5, 1.0,
                                                             "10", None, True)),
        *({"op": "reserve", "name": "s", "format": f} for f in ("pal16", "hex", "rgb24", "rgb",
                                                                "PAL16")),
        {"op": "renew", "lease": "extra fields are ignored"},
        {**ok["caps"], "format": "hex"}, {**ok["caps"], "format": "rgb24"},
        {**ok["caps"], "w": 256, "h": 256}, {**ok["caps"], "w": 257}, {**ok["caps"], "h": 0},
        {**ok["caps"], "fps": 60}, {**ok["caps"], "fps": 61}, {**ok["caps"], "palette": cga[:15]},
        {**ok["caps"], "palette": cga[:15] + ["#FFF"]}, {**ok["granted"], "format": "rgb24"},
        {"op": "lease", "display": GB, "holder": None, "expires": None},
        {"op": "lease", "display": GB, "holder": None},
        {"op": "busy", "holder": "x"},
        *({"op": "error", "reason": r} for r in dc.REASONS),
        {"op": "error", "reason": "not holder"}, {"op": "error", "reason": "busy"},
        {"op": "dance"}, {"display": GB}, {"op": 3}, [], "view",
    ]
    cases = []
    for m in msgs:
        errs = dc.check_message(m)
        c = {"message": m, "valid": not errs}
        if isinstance(m, dict) and m.get("op") in dc.TO_RELAY + ("dance",) or not isinstance(
                m, dict) or not isinstance(m.get("op"), str):
            c["relay"] = relay_answer(m, errs)
        cases.append(c)
    raw = [("{oops", "bad-format"), ("{}", "bad-format"), ('{"op":"caps"}', "unknown-op"),
           ('{"op":"granted"}', "unknown-op")]
    return {"description": "Control messages against schemas/<op>.json: valid or not; for a "
                           "message to the relay, relay is the error a relay answers (null "
                           "when it is valid). raw are text messages starting with {. A "
                           "reserve ttl over 900 is valid and clamped (granted.expires shows "
                           "it); 1.0 is an integer in JSON.",
            "cases": cases, "raw": [{"text": t, "relay": r} for t, r in raw]}


FILES = {
    "caps.json": caps_fixtures, "frames.json": frame_fixtures,
    "equivalence.json": equivalence_fixtures, "sequence.json": sequence_fixtures,
    "expiry.json": expiry_fixtures, "fold.json": fold_fixtures, "levels.json": level_fixtures,
    "quantize.json": quantize_fixtures, "grid.json": grid_fixtures,
    "interop.json": interop_fixtures, "messages.json": message_fixtures,
}


def render(doc):
    """JSON with the top-level fields first, then one case per line."""
    head = {"spec": SPEC, **{k: v for k, v in doc.items() if not isinstance(v, list)}}
    parts = [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in head.items()]
    for k, v in doc.items():
        if isinstance(v, list):
            rows = ",\n".join("    " + json.dumps(x, ensure_ascii=False, separators=(",", ":"))
                              for x in v)
            parts.append(f"  {json.dumps(k)}: [\n{rows}\n  ]")
    return "{\n" + ",\n".join(parts) + "\n}\n"


def build():
    return {name: render(fn()) for name, fn in FILES.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare, do not write")
    args = ap.parse_args(argv)
    dc.FIXTURES.mkdir(exist_ok=True)
    drift = []
    for name, text in build().items():
        path = dc.FIXTURES / name
        if args.check:
            if not path.exists() or path.read_text("utf-8") != text:
                drift.append(name)
        else:
            path.write_text(text, "utf-8")
    for name in drift:
        print(f"fixture drift: {name}", file=sys.stderr)
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
