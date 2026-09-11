"""Reference implementation of the display contract, wal.sh/tools/display v0.2.1.

The oracle is the pin next to this file: wal-sh-display-0.2.1/spec.md and
capabilities.json, with their sha256 in its PROVENANCE.  Everything here is
pure and stdlib-only.  The fixtures (gen_fixtures.py), the session checker
(check_session.py), the relay conformance suite (relay_conformance.py) and the
demo relay are all built on it.

Sections, each citing the spec section it re-tells:

  capabilities  presets, palettes, limits, preset order   (Presets, Discovery, Limits)
  levels        level(idx, n), palette16, colour_table     (Rendering contract)
  frames        pal16, hex and rgb24 codecs; quantize      (Frame formats, Source recipes)
  interop       BLP and MCUF packets                       (Source recipes; capabilities.interop)
  sequence      seq_accepts                                (Frame formats)
  messages      a small JSON Schema validator; check_message  (Wire protocol)
  fold          initial_state, reduce_event, dirty         (Reduction contract)

The implementer choices, where the spec is silent, are marked CHOICE and are
listed in README.md with the open questions.
"""
from __future__ import annotations

import functools
import json
import math
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
PIN = HERE / "wal-sh-display-0.2.1"
SCHEMAS = HERE / "schemas"
FIXTURES = HERE / "fixtures"

REASONS = ("not-holder", "bad-frame-length", "rate", "bad-format", "unknown-op")
FANOUT_FORMATS = ("pal16", "hex")            # what a sink reads; caps.format
SOURCE_FORMATS = ("pal16", "hex", "rgb24")   # what a relay takes; reserve.format
TO_RELAY = ("view", "reserve", "renew", "release")
FROM_RELAY = ("caps", "lease", "granted", "busy", "error")
STATUSES = ("connecting", "live", "idle", "closed", "error")

# Numbers the spec states in prose rather than in capabilities.json.
MAX_VIEWERS = 32          # Limits: "relay-side cap, 32 per display"
UDP_TTL = 5               # Source recipes: "treats a UDP sender as a 5-second holder"
SEQ_MOD = 1 << 16         # Frame formats: "an optional 2-byte big-endian sequence number"
BLP_MAGIC = 0xDEADBEEF    # capabilities.interop.accepts
MCUF_MAGIC = 0x23542666
INTEROP_HEADER = 12       # CHOICE: header layouts from the blinkenarea wiki, not the pin
DEFAULT_TTL = 300         # CHOICE: the spec's own example value; the spec gives no default


# --------------------------------------------------------------- capabilities

@functools.cache
def capabilities() -> dict:
    """The pinned capabilities.json, parsed."""
    return json.loads((PIN / "capabilities.json").read_text("utf-8"))


_MAX = capabilities()["max"]
MAX_W, MAX_H, MAX_CELLS = _MAX["w"], _MAX["h"], _MAX["cells"]
MAX_FPS, MAX_TTL, MAX_FRAME_BYTES = _MAX["fps"], _MAX["ttl"], _MAX["frameBytes"]
UDP_PORT = capabilities()["interop"]["udp_port"]
SPEC_DEFAULT = capabilities()["default"]     # cga40


@functools.cache
def preset_order() -> tuple[str, ...]:
    """The preset names in the order of the spec's Presets table (cga40 first).

    capabilities.json is a JSON object, whose key order means nothing; the
    table's order is the one the spec writes down, and it is what breaks the
    width x height ties when a UDP packet picks its display (tetris before c64,
    green-building before remote, dc32 before gameboy)."""
    text = (PIN / "spec.md").read_text("utf-8")
    section = text.split('<a id="presets"></a>', 1)[1].split("<a id=", 1)[0]
    known = capabilities()["displays"]
    return tuple(n for n in re.findall(r"^\| `([a-z0-9-]+)`", section, re.M) if n in known)


def preset(name: str) -> dict:
    return capabilities()["displays"][name]


def presets() -> dict[str, dict]:
    """Every preset, in the spec's table order."""
    return {n: preset(n) for n in preset_order()}


def palette(name: str) -> list[str]:
    return capabilities()["palettes"][name]


def _is_int(v) -> bool:
    return (isinstance(v, int) and not isinstance(v, bool)) or (
        isinstance(v, float) and v.is_integer())


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_grid(w, h) -> str | None:
    """None if w x h is a legal grid, else the page's error name.

    Limits: w and h in 1 to 256 and w*h at most 65,536.  The cell cap can
    never bind on its own: 256*256 is 65,536, and 65,537 is prime, so the
    only grids with 65,537 cells are 1 x 65,537 and 65,537 x 1."""
    if not _is_int(w) or not 1 <= w <= MAX_W:
        return "bad-w"
    if not _is_int(h) or not 1 <= h <= MAX_H:
        return "bad-h"
    if w * h > MAX_CELLS:
        return "bad-cells"
    return None


# --------------------------------------------------------------- levels

def _div_round(num: int, den: int) -> int:
    """num/den rounded half up, for num >= 0 and den > 0, in exact arithmetic."""
    return (2 * num + den) // (2 * den)


def level(idx: int, n: int) -> int:
    """The Rendering contract's level rule: a wire index on a palette of n entries.

        level(idx) = 0                                  when idx = 0
                   = max(1, round(idx * (n - 1) / 15))  otherwise

    The quotient idx*(n-1)/15 never lies halfway between two integers (15 is
    odd), so the rounding mode does not matter.  n must be 2 to 16: with n = 1
    a lit index would map to level 1, which does not exist."""
    if not (_is_int(n) and 2 <= n <= 16):
        raise ValueError(f"level rule needs 2 to 16 palette entries, not {n!r}")
    if not (_is_int(idx) and 0 <= idx <= 15):
        raise ValueError(f"palette index out of range: {idx!r}")
    return 0 if idx == 0 else max(1, _div_round(int(idx) * (int(n) - 1), 15))


def palette16(colours: list[str]) -> list[str]:
    """The 16-entry palette that renders a short palette through the level rule:
    entry i is colours[level(i, n)].  A 16-entry palette comes back unchanged.

    CHOICE: this is what the relay announces in caps and granted, which the
    spec writes as "palette":[16 hex]; a sink that adopts it renders exactly
    what a sink applying the level rule to the named palette renders."""
    return [colours[level(i, len(colours))] for i in range(16)]


def colour_table(pal) -> list[str]:
    """The 16 colours a sink draws for indices 0..15, given a state's palette:
    a palette name, or a list of 2 to 16 hex colours (the level rule applies)."""
    colours = palette(pal) if isinstance(pal, str) else list(pal)
    return palette16(colours)


def hex_rgb(colour: str) -> tuple[int, int, int]:
    c = colour.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def rgb_hex(rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


# --------------------------------------------------------------- frames

class FrameError(ValueError):
    """A frame the contract drops; reason is one of REASONS."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def pal16_lengths(w: int, h: int) -> tuple[int, int]:
    """The two legal pal16 lengths: w*h, and w*h + 2 with the sequence prefix."""
    return w * h, w * h + 2


def hex_lengths(w: int, h: int) -> tuple[int, int]:
    """The two legal hex lengths: h*(w+1), and one more for the closing blank line."""
    return h * (w + 1), h * (w + 1) + 1


def decode_pal16(data: bytes, w: int, h: int) -> tuple[bytes, int | None]:
    """A pal16 frame -> (cells, seq or None).

    The length is checked before the values: a frame that is both the wrong
    length and holds a byte over 15 is bad-frame-length (CHOICE).  The two
    prefix bytes are a sequence number and may hold any value."""
    n = w * h
    if len(data) == n:
        seq, cells = None, bytes(data)
    elif len(data) == n + 2:
        seq, cells = int.from_bytes(data[:2], "big"), bytes(data[2:])
    else:
        raise FrameError("bad-frame-length", f"{len(data)} bytes, want {n} or {n + 2}")
    if cells and max(cells) > 15:
        raise FrameError("bad-format", f"byte {max(cells)} is not a palette index")
    return cells, seq


def encode_pal16(cells, seq: int | None = None) -> bytes:
    body = bytes(cells)
    if body and max(body) > 15:
        raise ValueError("pal16 cells are 0 to 15")
    return body if seq is None else (seq % SEQ_MOD).to_bytes(2, "big") + body


_HEXVAL = {c: int(c, 16) for c in "0123456789abcdefABCDEF"}   # CHOICE: A-F accepted
_HEXDIGITS = "0123456789abcdef"


def decode_hex(text: str, w: int, h: int) -> bytes:
    """A hex frame -> cells.  Positional: with the length right, every digit
    position must hold a hex digit and every line end an LF (else
    bad-format); with the length wrong it is bad-frame-length.  So CRLF lines
    are bad-frame-length when h > 1 and bad-format when h = 1, and a missing
    final LF is bad-frame-length.  Upper-case A-F are accepted (CHOICE; the
    spec writes "0 to f")."""
    n = h * (w + 1)
    if len(text) not in (n, n + 1):
        raise FrameError("bad-frame-length", f"{len(text)} characters, want {n} or {n + 1}")
    if len(text) == n + 1 and text[-1] != "\n":
        raise FrameError("bad-format", "the character after the last line is not LF")
    out = bytearray(w * h)
    for y in range(h):
        base = y * (w + 1)
        if text[base + w] != "\n":
            raise FrameError("bad-format", f"line {y} is not {w} digits and LF")
        for x in range(w):
            v = _HEXVAL.get(text[base + x])
            if v is None:
                raise FrameError("bad-format", f"{text[base + x]!r} is not a hex digit")
            out[y * w + x] = v
    return bytes(out)


def encode_hex(cells, w: int, h: int, blank: bool = False) -> str:
    """cells -> h lines of w lower-case hex digits, each LF-terminated, plus an
    empty line if BLANK."""
    cells = bytes(cells)
    lines = ["".join(_HEXDIGITS[c] for c in cells[y * w:(y + 1) * w]) + "\n" for y in range(h)]
    return "".join(lines) + ("\n" if blank else "")


def quantize(rgb, pal16) -> int:
    """The index of the nearest entry of a 16-entry palette to RGB: squared
    Euclidean distance in sRGB, ties to the lower index (Source recipes).
    PAL16 holds hex strings or RGB triples."""
    best_d, best_i = None, 0
    for i, c in enumerate(pal16):
        r, g, b = hex_rgb(c) if isinstance(c, str) else c
        d = (rgb[0] - r) ** 2 + (rgb[1] - g) ** 2 + (rgb[2] - b) ** 2
        if best_d is None or d < best_d:
            best_d, best_i = d, i
    return best_i


def decode_rgb24(data: bytes, w: int, h: int, pal16) -> tuple[bytes, int | None]:
    """An rgb24 frame (w*h*3 bytes, optional 2-byte sequence prefix) -> (cells,
    seq), each cell quantized against PAL16.  Relay-side only (NR-QUANT)."""
    n = w * h
    if len(data) == 3 * n:
        seq, body = None, data
    elif len(data) == 3 * n + 2:
        seq, body = int.from_bytes(data[:2], "big"), data[2:]
    else:
        raise FrameError("bad-frame-length", f"{len(data)} bytes, want {3 * n} or {3 * n + 2}")
    table = [hex_rgb(c) if isinstance(c, str) else tuple(c) for c in pal16]
    memo: dict[bytes, int] = {}
    out = bytearray(n)
    for i in range(n):
        px = bytes(body[3 * i:3 * i + 3])
        v = memo.get(px)
        if v is None:
            v = memo[px] = quantize(px, table)
        out[i] = v
    return bytes(out), seq


def encode_rgb24(cells, pal16, seq: int | None = None) -> bytes:
    table = [bytes(hex_rgb(c)) for c in pal16]
    body = b"".join(table[c] for c in bytes(cells))
    return body if seq is None else (seq % SEQ_MOD).to_bytes(2, "big") + body


def fanout_frame(cells, w: int, h: int, fmt: str):
    """cells as the relay fans them out in FMT (caps.format): bytes or text."""
    return bytes(cells) if fmt == "pal16" else encode_hex(cells, w, h)


def black_frame(w: int, h: int, fmt: str):
    """The frame sent after expiry: every cell index 0."""
    return fanout_frame(bytes(w * h), w, h, fmt)


# --------------------------------------------------------------- interop

def is_interop(data: bytes) -> bool:
    """Does a binary message start with the BLP or MCUF magic?  A pal16 frame
    never does: bytes 3 and 4 of both magics are over 15, and they are cells
    even when a sequence prefix is present."""
    return len(data) >= 4 and int.from_bytes(data[:4], "big") in (BLP_MAGIC, MCUF_MAGIC)


def scale(v: int, maxval: int, top: int) -> int:
    """v in 0..maxval -> 0..top, rounded half up (CHOICE: the spec says only
    "scaled to 0..15"; ties occur for even maxval, e.g. 1 of 2 -> 7.5 -> 8)."""
    return _div_round(v * top, maxval)


def parse_interop(data: bytes) -> dict:
    """A BLP or MCUF packet -> {kind, w, h, channels, maxval, payload}.

    Header layouts (big-endian, 12 bytes), from the Blinkenlights wiki as the
    implementer knows it; the pin gives only the magics and the payload rules:
      BLP   magic u32 = DEADBEEF, frame count u32, width u16, height u16;
            then w*h bytes, each 0 or 1
      MCUF  magic u32 = 23542666, height u16, width u16, channels u16, maxval u16;
            then h*w*channels bytes, each 0..maxval
    """
    if len(data) < 4:
        raise FrameError("bad-frame-length", "shorter than a magic")
    magic = int.from_bytes(data[:4], "big")
    if magic not in (BLP_MAGIC, MCUF_MAGIC):
        raise FrameError("bad-format", "no BLP or MCUF magic")
    if len(data) < INTEROP_HEADER:
        raise FrameError("bad-frame-length", "shorter than the 12-byte header")

    def u16(at):
        return int.from_bytes(data[at:at + 2], "big")

    if magic == BLP_MAGIC:
        kind, w, h, channels, maxval = "blp", u16(8), u16(10), 1, 1
    else:
        kind, h, w, channels, maxval = "mcuf", u16(4), u16(6), u16(8), u16(10)
    if check_grid(w, h):
        raise FrameError("bad-format", f"{kind} grid {w}x{h} outside the limits")
    if channels not in (1, 3):
        raise FrameError("bad-format", f"mcuf channels {channels}, want 1 or 3")
    if maxval < 1:
        raise FrameError("bad-format", "mcuf maxval 0")
    payload = bytes(data[INTEROP_HEADER:])
    if len(payload) != w * h * channels:
        raise FrameError("bad-frame-length",
                         f"{kind} payload {len(payload)} bytes, want {w * h * channels}")
    if payload and max(payload) > maxval:
        raise FrameError("bad-format", f"{kind} value {max(payload)} over maxval {maxval}")
    return {"kind": kind, "w": w, "h": h, "channels": channels, "maxval": maxval,
            "payload": payload}


def interop_cells(packet: dict, pal16) -> bytes:
    """The pal16 cells of a parsed packet.  One channel scales to 0..15, so BLP
    0 and 1 become 0 and 15.  Three channels scale to 0..255 and quantize
    against PAL16, as rgb24 does (CHOICE)."""
    mv, p = packet["maxval"], packet["payload"]
    if packet["channels"] == 1:
        return bytes(scale(v, mv, 15) for v in p)
    rgb = bytes(scale(v, mv, 255) for v in p)
    return decode_rgb24(rgb, packet["w"], packet["h"], pal16)[0]


def encode_blp(w: int, h: int, bits, frame_count: int = 0) -> bytes:
    return (BLP_MAGIC.to_bytes(4, "big") + frame_count.to_bytes(4, "big")
            + w.to_bytes(2, "big") + h.to_bytes(2, "big") + bytes(bits))


def encode_mcuf(w: int, h: int, values, channels: int = 1, maxval: int = 15) -> bytes:
    return (MCUF_MAGIC.to_bytes(4, "big") + h.to_bytes(2, "big") + w.to_bytes(2, "big")
            + channels.to_bytes(2, "big") + maxval.to_bytes(2, "big") + bytes(values))


# --------------------------------------------------------------- sequence

SEQ_RULES = ("literal", "serial")


def seq_accepts(last: int | None, seq: int | None, rule: str = "literal") -> bool:
    """Is a frame with sequence SEQ accepted after the last accepted LAST?

    literal: the spec's text, "a lower sequence than the last accepted is
      dropped": seq >= last.  After 65535 every prefixed frame is dropped
      until the lease ends (an open question for the user).
    serial: RFC 1982 serial-number arithmetic over 16 bits: seq is lower
      when (seq - last) mod 65536 is 32768 or more; 0 follows 65535.
    A frame with no prefix, or the first prefixed frame of a lease, is never
    dropped by sequence, and a frame with no prefix leaves LAST alone
    (CHOICE)."""
    if last is None or seq is None:
        return True
    if rule == "literal":
        return seq >= last
    if rule == "serial":
        return (seq - last) % SEQ_MOD < SEQ_MOD // 2
    raise ValueError(f"unknown sequence rule {rule!r}")


# --------------------------------------------------------------- messages

_ANNOTATIONS = {"$schema", "$id", "title", "description", "$comment", "$defs",
                "examples", "default"}
_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "number": _is_number,
    "integer": lambda v: _is_number(v) and _is_int(v),
}


def _json_eq(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_number(a) and _is_number(b):
        return a == b
    return type(a) is type(b) and a == b


def _resolve(root: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"only local $ref is supported, not {ref!r}")
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def validate(inst, schema: dict, root: dict | None = None, path: str = "$") -> list[str]:
    """Validate INST against a JSON Schema (draft 2020-12), for the subset of
    keywords the schemas in schemas/ use.  Returns a list of findings, empty
    when valid.  Any other keyword raises, so a schema cannot silently use
    something this validator ignores."""
    root = schema if root is None else root
    errs: list[str] = []
    for k, v in schema.items():
        if k in _ANNOTATIONS:
            continue
        if k == "$ref":
            errs += validate(inst, _resolve(root, v), root, path)
        elif k == "type":
            if not any(_TYPES[t](inst) for t in ([v] if isinstance(v, str) else v)):
                errs.append(f"{path}: {inst!r} is not of type {v}")
        elif k == "const":
            if not _json_eq(inst, v):
                errs.append(f"{path}: {inst!r} is not {v!r}")
        elif k == "enum":
            if not any(_json_eq(inst, e) for e in v):
                errs.append(f"{path}: {inst!r} is not one of {v}")
        elif k == "required":
            if isinstance(inst, dict):
                errs += [f"{path}: missing {r!r}" for r in v if r not in inst]
        elif k == "properties":
            if isinstance(inst, dict):
                for name, sub in v.items():
                    if name in inst:
                        errs += validate(inst[name], sub, root, f"{path}.{name}")
        elif k == "additionalProperties":
            if isinstance(inst, dict):
                extra = [x for x in inst if x not in schema.get("properties", {})]
                if v is False:
                    errs += [f"{path}: unexpected {x!r}" for x in extra]
                elif isinstance(v, dict):
                    for x in extra:
                        errs += validate(inst[x], v, root, f"{path}.{x}")
        elif k in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if _is_number(inst):
                bad = {"minimum": inst < v, "maximum": inst > v,
                       "exclusiveMinimum": inst <= v, "exclusiveMaximum": inst >= v}[k]
                if bad:
                    errs.append(f"{path}: {inst!r} violates {k} {v}")
        elif k in ("minLength", "maxLength", "pattern"):
            if isinstance(inst, str):
                if k == "minLength" and len(inst) < v or k == "maxLength" and len(inst) > v:
                    errs.append(f"{path}: length {len(inst)} violates {k} {v}")
                if k == "pattern" and not re.search(v, inst):
                    errs.append(f"{path}: {inst!r} does not match {v}")
        elif k in ("items", "minItems", "maxItems"):
            if isinstance(inst, list):
                if k == "items":
                    for i, x in enumerate(inst):
                        errs += validate(x, v, root, f"{path}[{i}]")
                elif k == "minItems" and len(inst) < v or k == "maxItems" and len(inst) > v:
                    errs.append(f"{path}: {len(inst)} items violates {k} {v}")
        else:
            raise ValueError(f"unsupported JSON Schema keyword {k!r} at {path}")
    return errs


@functools.cache
def schema(name: str) -> dict:
    """schemas/<name>.json: one per control message op, plus session-record."""
    return json.loads((SCHEMAS / f"{name}.json").read_text("utf-8"))


def check_message(msg) -> list[str]:
    """Findings for a control message (a parsed JSON value); empty when valid."""
    if not isinstance(msg, dict):
        return [f"$: a control message is a JSON object, not {type(msg).__name__}"]
    op = msg.get("op")
    if not isinstance(op, str):
        return ["$: missing op"]
    if op not in TO_RELAY + FROM_RELAY:
        return [f"$.op: unknown op {op!r}"]
    errs = validate(msg, schema(op))
    if not errs and op in ("caps", "granted") and msg["w"] * msg["h"] > MAX_CELLS:
        errs.append(f"$: {msg['w']}x{msg['h']} is over {MAX_CELLS} cells")
    return errs


# --------------------------------------------------------------- fold

def initial_state(name: str | None = None, *, w: int | None = None, h: int | None = None,
                  fixed: bool = False) -> dict:
    """The canonical state of the Reduction contract for preset NAME (default:
    the spec's, cga40), with W and H overriding it; FIXED as the URL fixed them.
    Cells are bytes here; state_to_json turns them into a list."""
    p = preset(name or SPEC_DEFAULT)
    w = p["w"] if w is None else w
    h = p["h"] if h is None else h
    return {"w": w, "h": h, "fixed": fixed, "format": "pal16", "palette": p["palette"],
            "fps": p["fps"], "status": "connecting", "holder": None, "expires": None,
            "seq": 0, "cells": bytes(w * h), "dropped": 0, "error": None}


def _drop(s: dict) -> dict:
    return {**s, "dropped": s["dropped"] + 1}


def _frame_data(event: dict):
    """The payload of a frame event: bytes, str, or None if malformed.
    Python callers pass data=bytes|str; JSON fixtures pass bytes=[ints] or text."""
    if "data" in event:
        d = event["data"]
        return bytes(d) if isinstance(d, (bytes, bytearray)) else d if isinstance(d, str) else None
    if "bytes" in event:
        b = event["bytes"]
        if isinstance(b, list) and all(_is_int(x) and 0 <= x <= 255 for x in b):
            return bytes(int(x) for x in b)
        return None
    if "text" in event:
        return event["text"] if isinstance(event["text"], str) else None
    return None


def reduce_event(state: dict, event) -> dict:
    """One step of the sink's fold (spec: Reduction contract).  Total: any
    value is an event, and anything the table does not name, or a named event
    that is malformed, increments dropped and changes nothing else.

    Local events are {"event": "open"|"close"|"tick"|"frame", ...}: a tick
    carries "now" (unix s), a frame "data" (bytes or str) or, in JSON,
    "bytes" (a list of ints) or "text".  Control messages are the parsed JSON
    objects, told apart by "op".  Returns a new state; STATE is not mutated."""
    if not isinstance(event, dict):
        return _drop(state)
    if "event" in event:
        kind = event["event"]
        if kind == "open":
            return {**state, "status": "connecting"}
        if kind == "close":
            return {**state, "status": "closed"}
        if kind == "tick":
            now = event.get("now")
            if not _is_number(now):
                return _drop(state)
            # CHOICE: "passed" is now >= expires; expires is a whole second
            # rounded up, so at that second the relay has already expired it.
            if state["expires"] is not None and now >= state["expires"]:
                return {**state, "status": "idle"}
            return state
        if kind == "frame":
            return _reduce_frame(state, event)
        return _drop(state)
    op = event.get("op")
    if op not in ("caps", "lease", "error") or check_message(event):
        return _drop(state)
    if op == "caps":
        w, h = int(event["w"]), int(event["h"])
        if state["fixed"] and (w, h) != (state["w"], state["h"]):
            # CHOICE: a refused caps changes nothing but status and error
            return {**state, "status": "error",
                    "error": {"reason": "bad-src", "caps": {"w": w, "h": h},
                              "fixed": {"w": state["w"], "h": state["h"]}}}
        return {**state, "w": w, "h": h, "fps": int(event["fps"]), "format": event["format"],
                "palette": list(event["palette"]), "cells": bytes(w * h)}
    if op == "lease":
        holder, expires = event["holder"], event["expires"]
        return {**state, "holder": holder, "expires": None if expires is None else int(expires),
                "status": "idle" if holder is None else state["status"]}
    return {**state, "error": {"reason": event["reason"]}}


def _reduce_frame(state: dict, event: dict) -> dict:
    data, w, h = _frame_data(event), state["w"], state["h"]
    try:
        if state["format"] == "pal16" and isinstance(data, bytes):
            cells = decode_pal16(data, w, h)[0]   # a sink ignores the prefix's value
        elif state["format"] == "hex" and isinstance(data, str):
            cells = decode_hex(data, w, h)
        else:
            return _drop(state)
    except FrameError:
        return _drop(state)
    return {**state, "cells": cells, "seq": state["seq"] + 1, "status": "live"}


def dirty(state: dict, prev: dict | None) -> list[tuple[int, int, int]]:
    """The (x, y, idx) triples of STATE that differ from PREV, row-major.  Every
    cell when there is no PREV or the grid changed size."""
    w, cells = state["w"], state["cells"]
    if prev is None or (prev["w"], prev["h"]) != (w, state["h"]) or len(prev["cells"]) != len(cells):
        return [(i % w, i // w, c) for i, c in enumerate(cells)]
    return [(i % w, i // w, c)
            for i, (c, p) in enumerate(zip(cells, prev["cells"], strict=True)) if c != p]


def check_invariants(state: dict) -> list[str]:
    """The Reduction contract's invariants that hold of a single state."""
    errs = []
    if len(state["cells"]) != state["w"] * state["h"]:
        errs.append(f"{len(state['cells'])} cells for {state['w']}x{state['h']}")
    if any(not 0 <= c <= 15 for c in state["cells"]):
        errs.append("a cell outside 0 to 15")
    if state["status"] not in STATUSES:
        errs.append(f"status {state['status']!r}")
    if state["seq"] < 0 or state["dropped"] < 0:
        errs.append("negative seq or dropped")
    return errs


def state_to_json(s: dict) -> dict:
    return {**s, "cells": list(s["cells"])}


def state_from_json(j: dict) -> dict:
    return {**j, "cells": bytes(j["cells"])}


def event_to_json(e):
    """A Python event as JSON: frame data becomes bytes=[ints] or text."""
    if isinstance(e, dict) and e.get("event") == "frame" and "data" in e:
        d = e["data"]
        rest = {k: v for k, v in e.items() if k != "data"}
        return {**rest, "bytes": list(d)} if isinstance(d, (bytes, bytearray)) else {**rest, "text": d}
    return e
