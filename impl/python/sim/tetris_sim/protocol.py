"""Wire codec for the remote display/control protocol (docs/PROTOCOL.md,
contract v1). Binding-independent: a message is one JSON object, carried as
an LF-terminated line (TCP) or as one WebSocket text message.

Pure: encode, decode and validate. ``tetris_sim.server`` does the I/O.
Decoded frames are returned in the engine's own representation (17 tuples
of 9 ``(r, g, b)`` tuples), so they compare equal to ``render(state)``.
"""

import json
import re

from tetris_engine.frame import frame_digest
from tetris_engine.tables import ACTIONS, COLS, FPS, ROWS

PROTOCOL = "17x9-tetris-remote"
VERSION = 1                   # contract-v1 speaks version 1 (§2)
SPEC_VERSION = 2              # latest sealed SPEC whose traces the engine passes
MAX_TEXT = 65535              # bytes of JSON text per message (§1.2)
MAX_MESSAGE = 65536           # a TCP line, LF included: the hello's max_message
MAX_TICK = 3600               # frames per tick message (2 minutes)
MAX_PHASE = 32
MAX_ID = 64
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 1709           # TCP, JSON lines (§5.2)
DEFAULT_WS_PORT = 1710        # WebSocket (§5.3)
WS_PATH = "/tetris-17x9"
WS_SUBPROTOCOL = f"tetris-17x9.v{VERSION}"

MODES = ("engine", "display")
CLOCKS = ("realtime", "lockstep")
CLIENT_ROLES = ("controller", "viewer", "producer")
ROLES = CLIENT_ROLES + ("server",)
TYPES = ("hello", "event", "tick", "frame", "state", "ping", "pong", "error")

# Error codes that end the connection (docs/PROTOCOL.md §6).
FATAL = frozenset({"too_large", "hello_required", "version", "role",
                   "bad_hello", "busy", "too_many_errors"})
SEED_LIMIT = 2 ** 32          # SPEC §12: 0 <= seed < 2^32

_HEX64 = re.compile(r"[0-9a-f]{64}")


class ProtocolError(Exception):
    """A message that violates the protocol. ``code`` is a §6 error code."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    @property
    def fatal(self):
        return self.code in FATAL


# --------------------------------------------------------------- encoding

def encode(msg):
    """One message dict -> one line of UTF-8 JSON bytes, LF-terminated."""
    data = json.dumps(msg, separators=(",", ":"), allow_nan=False,
                      ensure_ascii=False).encode("utf-8") + b"\n"
    if len(data) > MAX_MESSAGE:
        raise ProtocolError("too_large", f"{len(data)} bytes > {MAX_MESSAGE}")
    return data


def make_hello(role, **fields):
    return {"type": "hello", "protocol": PROTOCOL, "version": VERSION,
            "role": role, **fields}


def make_event(action, down):
    return {"type": "event", "action": action, "down": bool(down)}


def make_tick(frames=1):
    return {"type": "tick", "frames": frames}


def make_frame(frame_no, rows, digest=None, events=None):
    """``events``: E_k, the (action, down) pairs passed to step k (§4.4);
    an engine server always sends them, a producer need not."""
    rows = validate_rows(rows)
    msg = {"type": "frame", "frame_no": frame_no, "rows": rows,
           "digest": digest or frame_digest(rows)}
    if events is not None:
        msg["events"] = [[a, bool(d)] for a, d in events]
    return msg


def make_state(score, level, lines, **optional):
    msg = {"type": "state", "score": score, "level": level, "lines": lines}
    msg.update((k, v) for k, v in optional.items() if v is not None)
    return msg


def make_ping(ping_id=None):
    return {"type": "ping"} if ping_id is None else {"type": "ping", "id": ping_id}


def make_pong(ping_id=None):
    return {"type": "pong"} if ping_id is None else {"type": "pong", "id": ping_id}


def make_error(code, message):
    return {"type": "error", "code": code, "message": message}


# --------------------------------------------------------------- decoding

def _reject_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _nat(msg, key, code, required=True):
    v = msg.get(key)
    if v is None and not required:
        return None
    if not _is_int(v) or v < 0:
        raise ProtocolError(code, f"{key!r} must be an integer >= 0")
    return v


def validate_rows(rows):
    """Wire rows -> engine frame; ProtocolError('bad_frame') if not 17x9
    cells of three integers in 0..255 (SPEC §2; no clamping on the wire)."""
    if not isinstance(rows, (list, tuple)) or len(rows) != ROWS:
        raise ProtocolError("bad_frame", f"a frame has {ROWS} rows")
    out = []
    for r, row in enumerate(rows):
        if not isinstance(row, (list, tuple)) or len(row) != COLS:
            raise ProtocolError("bad_frame", f"row {r} must have {COLS} cells")
        cells = []
        for c, cell in enumerate(row):
            if (not isinstance(cell, (list, tuple)) or len(cell) != 3
                    or not all(_is_int(v) and 0 <= v <= 255 for v in cell)):
                raise ProtocolError(
                    "bad_frame", f"cell ({r}, {c}) is not an [r, g, b] "
                    "triple of integers in 0..255")
            cells.append((cell[0], cell[1], cell[2]))
        out.append(tuple(cells))
    return tuple(out)


def _hello(msg):
    if msg.get("protocol") != PROTOCOL or msg.get("version") != VERSION \
            or isinstance(msg.get("version"), bool):
        raise ProtocolError("version", f"expected protocol {PROTOCOL!r} "
                            f"version {VERSION}")
    if msg.get("role") not in ROLES:
        raise ProtocolError("role", f"role must be one of {', '.join(ROLES)}")
    out = {"type": "hello", "protocol": PROTOCOL, "version": VERSION,
           "role": msg["role"]}
    for key in ("client",):
        if isinstance(msg.get(key), str):
            out[key] = msg[key]
    if msg.get("mode") in MODES:
        out["mode"] = msg["mode"]
    if msg.get("client_role") in CLIENT_ROLES:
        out["client_role"] = msg["client_role"]
    if msg.get("clock") in CLOCKS:
        out["clock"] = msg["clock"]
    for key in ("spec_version", "rows", "cols", "fps", "max_message"):
        if _is_int(msg.get(key)):
            out[key] = msg[key]
    seed = msg.get("seed")
    if seed is not None:
        if not _is_int(seed) or not 0 <= seed < SEED_LIMIT:
            raise ProtocolError("bad_hello", "'seed' must be an integer in "
                                "0..2^32-1")
        out["seed"] = seed
    return out


def _event(msg):
    action = msg.get("action")
    if action not in ACTIONS:
        raise ProtocolError("bad_event", f"unknown action {action!r}")
    if not isinstance(msg.get("down"), bool):
        raise ProtocolError("bad_event", "'down' must be true or false")
    return make_event(action, msg["down"])


def _tick(msg):
    n = msg.get("frames")
    if not _is_int(n) or not 1 <= n <= MAX_TICK:
        raise ProtocolError("bad_tick", f"'frames' must be an integer in "
                            f"1..{MAX_TICK}")
    return make_tick(n)


def _frame(msg):
    frame_no = _nat(msg, "frame_no", "bad_frame")
    rows = validate_rows(msg.get("rows"))
    digest = msg.get("digest")
    actual = frame_digest(rows)
    if digest is not None:
        if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
            raise ProtocolError("bad_frame", "'digest' must be 64 lowercase "
                                "hex digits")
        if digest != actual:
            raise ProtocolError("digest", "digest does not match rows "
                                "(SPEC §9.4)")
    out = {"type": "frame", "frame_no": frame_no, "rows": rows,
           "digest": actual}
    if "events" in msg:
        out["events"] = _step_events(msg["events"])
    return out


def _step_events(events):
    if not isinstance(events, list) or not all(
            isinstance(e, list) and len(e) == 2 and isinstance(e[0], str)
            and e[0] in ACTIONS and isinstance(e[1], bool) for e in events):
        raise ProtocolError("bad_frame", "'events' must be a list of "
                            "[action, down] pairs")
    return [[a, d] for a, d in events]


def _state(msg):
    out = {"type": "state"}
    for key in ("score", "level", "lines"):
        out[key] = _nat(msg, key, "bad_state")
    for key in ("high_score", "frame_no"):
        v = _nat(msg, key, "bad_state", required=False)
        if v is not None:
            out[key] = v
    phase = msg.get("phase")
    if phase is not None:
        if not isinstance(phase, str) or len(phase) > MAX_PHASE:
            raise ProtocolError("bad_state", "'phase' must be a short string")
        out["phase"] = phase
    return out


def _ping(msg):
    ping_id = msg.get("id")
    if ping_id is not None and not (
            _is_int(ping_id) or (isinstance(ping_id, str)
                                 and len(ping_id) <= MAX_ID)):
        raise ProtocolError("bad_ping", "'id' must be an integer or a short "
                            "string")
    return {"type": msg["type"]} if ping_id is None else \
        {"type": msg["type"], "id": ping_id}


def _error(msg):
    code, message = msg.get("code"), msg.get("message", "")
    if not isinstance(code, str) or not isinstance(message, str):
        raise ProtocolError("malformed", "error needs string code/message")
    return make_error(code, message)


_VALIDATORS = {"hello": _hello, "event": _event, "tick": _tick,
               "frame": _frame, "state": _state, "ping": _ping,
               "pong": _ping, "error": _error}


def decode(line):
    """One message (bytes or str: a line with or without its LF, or a
    WebSocket text message) -> a validated, normalized message dict.
    Unknown fields are dropped. Raises ProtocolError, and nothing else, for
    any bad input."""
    data = line.encode("utf-8") if isinstance(line, str) else bytes(line)
    size = len(data)
    if data.endswith(b"\n"):      # the line's terminator is not message text
        size -= 2 if data.endswith(b"\r\n") else 1
    if size > MAX_TEXT:
        raise ProtocolError("too_large", f"{size} bytes of JSON > {MAX_TEXT}")
    try:
        text = data.decode("utf-8")
        msg = json.loads(text, parse_constant=_reject_constant,
                         object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolError("malformed", str(exc)[:200]) from None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("malformed", "a message is a JSON object with a "
                            "string 'type'")
    validator = _VALIDATORS.get(msg["type"])
    if validator is None:
        raise ProtocolError("unknown_type", f"unknown type {msg['type']!r}")
    return validator(msg)


__all__ = [
    "ACTIONS", "CLIENT_ROLES", "CLOCKS", "DEFAULT_HOST", "DEFAULT_PORT",
    "DEFAULT_WS_PORT", "FATAL", "FPS", "MAX_MESSAGE", "MAX_TEXT", "MAX_TICK",
    "MODES", "PROTOCOL", "ProtocolError", "ROLES", "SPEC_VERSION", "TYPES",
    "VERSION", "WS_PATH", "WS_SUBPROTOCOL", "decode", "encode", "make_error",
    "make_event", "make_frame", "make_hello", "make_ping", "make_pong",
    "make_state", "make_tick", "validate_rows",
]
