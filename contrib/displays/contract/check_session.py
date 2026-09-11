"""Check a recorded relay session against the display contract, v0.2.1.

    cd contrib/displays
    python -m contract.check_session SESSION.jsonl [--seq-rule literal|serial]
    python contract/check_session.py SESSION.jsonl          # also works as a script

A session is JSONL, one record per message the relay received ("in") or sent
("out"), as schemas/session-record.json describes; the demo relay writes one
with --record.  A first "meta" record, if present, is the relay's
capabilities.json (its displays and default); without it the pinned
presets and --default are used.

Findings, one per line; exit status 1 if there are any, 0 if none:

  records   every record matches session-record.json; every control message
            the relay sent matches schemas/<op>.json
  replies   a malformed control message is answered bad-format, an unknown op
            unknown-op, renew/release/frames from a non-holder not-holder, a
            bad frame bad-frame-length or bad-format, exactly as the
            reference decodes it
  holders   one holder per display: no granted while another connection's
            lease is live; busy names the holder
  fan-out   every frame sent to a viewer is a frame the display's holder had
            accepted (never a non-holder's or a dropped one), of the length
            caps.format gives (pal16 w*h, hex h*(w+1) [+1])
  expiry    a lease ended by the relay (not released) is not ended early, and
            every viewer gets lease holder null and then an all-zero frame
  rate      a frame with a sequence lower than the last accepted is dropped
            with rate (--seq-rule); accepted frames are never closer than
            half a period, a lease never exceeds its fps over time, and a frame
            1.5 periods after the last accepted is not dropped for rate
  viewers   no display has more than 32 viewers; a refused viewer was the 33rd

--lease-keys reads a session of a relay in the dlk1 experiment extension (not
v0.2.1): an unauthorized refusal is expected, the holder is the key's sub, and a
lease ends no later than the key's exp.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from dataclasses import dataclass, field

try:
    from . import display_contract as dc
    from . import dlk1
except ImportError:  # run as a script
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from contract import display_contract as dc
    from contract import dlk1

SLACK = 0.25   # seconds: the recorder's clock against the relay's timers


@dataclass
class Display:
    name: str
    w: int
    h: int
    fps: int
    fanout: str
    palette16: list
    holder: str | None = None
    hname: str | None = None
    fmt: str = "pal16"
    ttl_lo: float = 0.0          # the lease's ttl lies in (ttl_lo, ttl_hi]
    ttl_hi: float = 0.0
    renewed: float = 0.0
    accepted: list = field(default_factory=list)
    last_seq: int | None = None
    ok_cells: collections.deque = field(default_factory=lambda: collections.deque(maxlen=1024))
    viewers: set = field(default_factory=set)
    expiring: bool = False
    black_due: set = field(default_factory=set)
    cap: float | None = None     # a dlk1 key's exp (--lease-keys)


@dataclass
class Conn:
    held: str | None = None
    viewing: set = field(default_factory=set)
    view_lease: set = field(default_factory=set)   # displays whose view reply lease is due


def is_control(payload):
    return isinstance(payload, str) and payload.startswith("{")


class Checker:
    def __init__(self, records, *, seq_rule="literal", default=None,
                 max_viewers=dc.MAX_VIEWERS, udp_ttl=None, lease_keys=False):
        self.records, self.seq_rule, self.max_viewers = records, seq_rule, max_viewers
        self.lease_keys = lease_keys
        self.findings: list[str] = []
        meta = next((r.get("payload") for r in records if r.get("kind") == "meta"), None)
        caps = meta if isinstance(meta, dict) else dc.capabilities()
        mock = caps.get("mock", {})
        self.default = default or caps.get("default") or dc.SPEC_DEFAULT
        self.udp_ttl = udp_ttl or mock.get("udp_ttl") or dc.UDP_TTL
        self.displays = {n: Display(n, p["w"], p["h"], p["fps"], p.get("format", "pal16"),
                                    dc.palette16(dc.palette(p["palette"])))
                         for n, p in caps["displays"].items()}
        self.order = [n for n in dc.preset_order() if n in self.displays] + [
            n for n in self.displays if n not in dc.preset_order()]
        self.conns: dict[str, Conn] = collections.defaultdict(Conn)
        self.t0 = records[0]["t"] if records else 0.0

    # ------------------------------------------------------------ reporting

    def find(self, t, conn, text):
        self.findings.append(f"t=+{t - self.t0:.3f} {conn}: {text}")

    def expect(self, t, conn, got, want, what):
        errs = [m["reason"] for m in got if isinstance(m, dict) and m.get("op") == "error"]
        first = errs[0] if errs else None
        if first != want:
            self.find(t, conn, f"{what}: want {'error ' + want if want else 'no error'}, "
                               f"got {'error ' + first if first else 'none'}")

    # ------------------------------------------------------------ the run

    def replies(self):
        """For each in-record: the control messages (or "close") sent to the same
        connection before its next in-record."""
        out, latest = {}, {}
        for i, r in enumerate(self.records):
            c = r.get("conn")
            if r.get("dir") == "in":
                latest[c], out[i] = i, []
            elif c in latest:
                if r.get("kind") == "close":
                    out[latest[c]].append("close")
                elif r.get("kind") == "text" and is_control(r.get("payload")):
                    try:
                        out[latest[c]].append(json.loads(r["payload"]))
                    except ValueError:
                        pass
        return out

    def run(self):
        rec_schema = dc.schema("session-record")
        valid = []
        for i, r in enumerate(self.records):
            errs = dc.validate(r, rec_schema)
            if errs:
                self.findings.append(f"record {i}: {errs[0]}")
            else:
                valid.append(r)
        self.records = valid
        rep = self.replies()
        for i, r in enumerate(self.records):
            t, c, kind, p = r["t"], r["conn"], r["kind"], r.get("payload")
            try:
                if r["dir"] == "in":
                    if kind == "close":
                        self.close(c, t)
                    elif kind == "udp":
                        self.udp(c, t, bytes.fromhex(p))
                    elif kind == "text" and is_control(p):
                        self.control(c, t, p, rep[i])
                    elif kind in ("text", "binary"):
                        self.frame(c, t, p if kind == "text" else bytes.fromhex(p), rep[i])
                    elif kind == "open":
                        self.conns[c]
                elif kind == "text" and is_control(p):
                    self.sent_control(c, t, p)
                elif kind in ("text", "binary"):
                    self.sent_frame(c, t, p if kind == "text" else bytes.fromhex(p))
            except (ValueError, TypeError, KeyError) as e:
                self.find(t, c, f"record {i} could not be checked: {e!r}")
        for d in self.displays.values():
            self.check_pace(d, self.records[-1]["t"] if self.records else 0.0)
        return self.findings

    # ------------------------------------------------------------ leases

    def deadline(self, d, lo=True):
        """When D's lease ends at the earliest (LO) or the latest: ttl after the
        last renewal, and never past a dlk1 key's exp (--lease-keys)."""
        end = d.renewed + (d.ttl_lo if lo else d.ttl_hi)
        return end if d.cap is None else min(end, d.cap)

    def lease_state(self, d, t):
        """live, ambiguous (the ttl window, where the relay may have expired it
        unseen) or gone."""
        if d.holder is None:
            return "gone"
        if t < self.deadline(d) - SLACK:
            return "live"
        if t > self.deadline(d, lo=False) + SLACK:
            return "gone"
        return "ambiguous"

    def held(self, c, t):
        name = self.conns[c].held
        if name is None:
            return None, "gone"
        d = self.displays[name]
        st = self.lease_state(d, t)
        if st == "gone":
            self.end(d, t, "expired unseen")
            return None, "gone"
        return name, st

    def grant(self, d, c, hname, fmt, t, expires):
        d.holder, d.hname, d.fmt = c, hname, fmt
        d.ttl_lo, d.ttl_hi = expires - t - 1, expires - t
        d.renewed, d.accepted, d.last_seq, d.expiring = t, [], None, False
        d.ok_cells.clear()
        self.conns[c].held = d.name

    def end(self, d, t, why):
        self.check_pace(d, t)
        if d.holder is not None and self.conns[d.holder].held == d.name:
            self.conns[d.holder].held = None
        d.holder = d.hname = d.cap = None
        d.accepted, d.expiring = [], why == "expired"

    def check_pace(self, d, t):
        a = d.accepted
        if len(a) >= 3 and len(a) - 1 > (a[-1] - a[0]) * d.fps * 1.02 + 1:
            self.find(t, d.name, f"{len(a)} frames accepted in {a[-1] - a[0]:.3f} s, "
                                 f"over {d.fps} fps")

    def close(self, c, t):
        for d in self.displays.values():
            d.viewers.discard(c)
            d.black_due.discard(c)
        name = self.conns[c].held
        if name is not None and self.displays[name].holder == c:
            self.end(self.displays[name], t, "closed")

    # ------------------------------------------------------------ what clients send

    def control(self, c, t, text, got):
        try:
            m = json.loads(text)
        except ValueError:
            return self.expect(t, c, got, "bad-format", "unparseable control")
        if not isinstance(m, dict) or not isinstance(m.get("op"), str):
            return self.expect(t, c, got, "bad-format", "control with no op")
        op = m["op"]
        if op not in dc.TO_RELAY:
            return self.expect(t, c, got, "unknown-op", f"op {op!r}")
        if dc.check_message(m):
            return self.expect(t, c, got, "bad-format", f"malformed {op}")
        if op == "reserve" and self.lease_keys and any(
                isinstance(x, dict) and x.get("reason") == "unauthorized" for x in got):
            return                                  # refused by the dlk1 extension
        if op in ("view", "reserve"):
            d = self.displays.get(m.get("display") or self.default)
            if d is None:
                return self.expect(t, c, got, "bad-format", f"{op} of an unknown display")
            return self.view(c, t, d, got) if op == "view" else self.reserve(c, t, d, m, got)
        name, st = self.held(c, t)
        if name is None:
            return self.expect(t, c, got, "not-holder", f"{op} from a non-holder")
        d = self.displays[name]
        if st == "ambiguous" and any(isinstance(x, dict) and x.get("reason") == "not-holder"
                                     for x in got):
            return self.end(d, t, "expired")
        self.expect(t, c, got, None, op)
        if op == "renew":
            d.renewed = t
        else:
            self.end(d, t, "released")

    def view(self, c, t, d, got):
        caps = next((x for x in got if isinstance(x, dict) and x.get("op") == "caps"), None)
        admitted = c in d.viewers
        if caps is None:
            if "close" in got and len(d.viewers) < self.max_viewers:
                self.find(t, c, f"view refused with {len(d.viewers)} viewers on {d.name}")
            elif "close" not in got:
                self.expect(t, c, got, None, f"view {d.name}")
            return
        if not admitted and len(d.viewers) >= self.max_viewers:
            self.find(t, c, f"viewer {len(d.viewers) + 1} admitted on {d.name}")
        want = {"display": d.name, "w": d.w, "h": d.h, "fps": d.fps, "format": d.fanout,
                "palette": d.palette16}
        for k, v in want.items():
            if caps.get(k) != v:
                self.find(t, c, f"caps.{k} {caps.get(k)!r}, want {v!r}")
        d.viewers.add(c)
        self.conns[c].viewing.add(d.name)
        self.conns[c].view_lease.add(d.name)

    def reserve(self, c, t, d, m, got):
        reply = next((x for x in got if isinstance(x, dict)
                      and x.get("op") in ("granted", "busy", "error")), None)
        if reply is None:
            return self.find(t, c, f"reserve {d.name}: no reply")
        st = self.lease_state(d, t)
        if st == "gone" and d.holder is not None:
            self.end(d, t, "expired unseen")
        if reply["op"] == "error":
            return self.find(t, c, f"reserve {d.name}: error {reply.get('reason')}")
        if reply["op"] == "busy":
            if d.holder is None or d.holder == c:
                self.find(t, c, f"busy for {d.name}, which {'it holds' if d.holder else 'is free'}")
            elif reply.get("holder") != d.hname:
                self.find(t, c, f"busy names {reply.get('holder')!r}, holder is {d.hname!r}")
            return
        if d.holder not in (None, c) and st == "live":
            self.find(t, c, f"granted {d.name} while {d.holder} ({d.hname}) holds it")
        elif d.holder not in (None, c):
            self.end(d, t, "expired unseen")
        fmt = m.get("format", "pal16")
        want = {"w": d.w, "h": d.h, "fps": d.fps, "format": fmt, "palette": d.palette16}
        for k, v in want.items():
            if reply.get(k) != v:
                self.find(t, c, f"granted.{k} {reply.get(k)!r}, want {v!r}")
        claims = dlk1.peek(m.get("key")) if self.lease_keys else None
        ttl = min(int(m.get("ttl", dc.MAX_TTL)), dc.MAX_TTL)
        if claims:
            ttl = min(ttl, claims["exp"] - t)       # granted.expires = min(now + ttl, exp)
        left = reply["expires"] - t
        if left > ttl + 1 + SLACK or ("ttl" in m and left < ttl - SLACK):
            self.find(t, c, f"granted.expires is {left:.1f} s away for a ttl of {ttl}")
        held = self.conns[c].held
        if held is not None and held != d.name:
            self.end(self.displays[held], t, "released")
        self.grant(d, c, claims["sub"] if claims else m["name"], fmt, t, reply["expires"])
        d.cap = claims["exp"] if claims else None

    def frame(self, c, t, data, got):
        name, st = self.held(c, t)
        if name is None:
            return self.expect(t, c, got, "not-holder", "frame from a non-holder")
        d = self.displays[name]
        err = next((x.get("reason") for x in got if isinstance(x, dict)
                    and x.get("op") == "error"), None)
        if st == "ambiguous" and err == "not-holder":
            return self.end(d, t, "expired")
        try:
            cells, seq = dc.decode_source_frame(data, d.w, d.h, d.fmt, d.palette16)
        except dc.FrameError as e:
            return self.expect(t, c, got, e.reason, f"frame to {d.name}")
        if not dc.seq_accepts(d.last_seq, seq, self.seq_rule):
            return self.expect(t, c, got, "rate", f"sequence {seq} after {d.last_seq}")
        since = t - d.accepted[-1] if d.accepted else None
        if err == "rate":
            if since is not None and since >= 1.5 / d.fps:
                self.find(t, c, f"rate drop {since * 1000:.1f} ms after the last accepted frame "
                                f"({d.fps} fps)")
            return
        if err is not None:
            return self.find(t, c, f"frame to {d.name}: unexpected error {err}")
        if since is not None and since < 0.5 / d.fps:
            self.find(t, c, f"frame accepted {since * 1000:.1f} ms after the last ({d.fps} fps)")
        d.accepted.append(t)
        d.last_seq = seq if seq is not None else d.last_seq
        d.renewed = t
        d.ok_cells.append(cells)

    def udp(self, c, t, data):
        try:
            p = dc.parse_interop(data)
        except dc.FrameError:
            return
        name = next((n for n in self.order if (self.displays[n].w, self.displays[n].h)
                     == (p["w"], p["h"])), None)
        if name is None:
            return
        d = self.displays[name]
        if d.holder is not None and d.holder != c and self.lease_state(d, t) == "gone":
            self.end(d, t, "expired unseen")
        if d.holder is None:
            self.grant(d, c, c, "pal16", t, t + self.udp_ttl)
            d.ttl_lo = self.udp_ttl - 1
        elif d.holder != c:
            return
        d.renewed = t
        d.ok_cells.append(dc.interop_cells(p, d.palette16))

    # ------------------------------------------------------------ what the relay sends

    def sent_control(self, v, t, text):
        m = json.loads(text)
        errs = dc.check_message(m)
        if (errs and self.lease_keys and m.get("op") == "error"
                and m.get("reason") == "unauthorized" and isinstance(m.get("detail"), str)):
            errs = []                               # the dlk1 extension's refusal
        if errs or m.get("op") not in dc.FROM_RELAY:
            return self.find(t, v, f"relay sent an invalid message: {(errs or [m.get('op')])[0]}")
        if m["op"] != "lease":
            return
        name = m.get("display")
        if name is None:   # the Rules' short form {"op":"lease","holder":null}
            viewing = sorted(self.conns[v].viewing)
            if len(viewing) != 1:
                return self.find(t, v, f"lease without display to a connection viewing "
                                       f"{len(viewing)} displays")
            name = viewing[0]
        d = self.displays.get(name)
        if d is None:
            return self.find(t, v, f"lease for an unknown display {name!r}")
        # the lease that answers v's own view says how the display is now; it
        # ends nothing, and owes v no black frame (v came after any cut)
        reply = name in self.conns[v].view_lease
        self.conns[v].view_lease.discard(name)
        if m["holder"] is None:
            if d.holder is not None and reply:
                if self.lease_state(d, t) == "live":
                    self.find(t, v, f"view of {d.name} says it is free; {d.hname} holds it")
                self.end(d, t, "expired unseen")
            elif d.holder is not None:
                if t < self.deadline(d) - SLACK:
                    self.find(t, v, f"{d.name} expired {self.deadline(d) - t:.2f} s early")
                self.end(d, t, "expired")
            if d.expiring and v in d.viewers and not reply:
                d.black_due.add(v)       # present at the cut: its black frame is due
        elif m["holder"] != d.hname:
            self.find(t, v, f"lease names {m['holder']!r}, holder is {d.hname!r}")

    def sent_frame(self, v, t, data):
        cands = []
        for name in sorted(self.conns[v].viewing):
            d = self.displays[name]
            if d.fanout == "pal16" and isinstance(data, bytes) and len(data) == d.w * d.h or (
                    d.fanout == "hex" and isinstance(data, str)
                    and len(data) in dc.hex_lengths(d.w, d.h)):
                cands.append(d)
        if not cands:
            return self.find(t, v, f"frame of {len(data)} {'bytes' if isinstance(data, bytes) else 'characters'}"
                                   f" fits no display it views ({sorted(self.conns[v].viewing)})")
        for d in cands:
            try:
                cells = (dc.decode_pal16(data, d.w, d.h)[0] if d.fanout == "pal16"
                         else dc.decode_hex(data, d.w, d.h))
            except dc.FrameError as e:
                return self.find(t, v, f"fan-out frame on {d.name} is {e.reason}")
            if v in d.black_due:
                d.black_due.discard(v)
                if any(cells):
                    self.find(t, v, f"the frame after {d.name}'s expiry is not black")
                return
            if cells in d.ok_cells:
                return
        self.find(t, v, f"fan-out frame on {cands[0].name} is no frame its holder had accepted")


def load(path):
    records, bad = [], []
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        if line.strip():
            try:
                records.append(json.loads(line))
            except ValueError:
                bad.append(f"line {n}: not JSON")
    return records, bad


def check(records, **kw):
    return Checker(records, **kw).run()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("session", help="a JSONL session, as the demo relay's --record writes it")
    ap.add_argument("--seq-rule", choices=dc.SEQ_RULES, default="literal")
    ap.add_argument("--default", help="the relay's default display, if no meta record")
    ap.add_argument("--max-viewers", type=int, default=dc.MAX_VIEWERS)
    ap.add_argument("--lease-keys", action="store_true",
                    help="a relay in the dlk1 extension: unauthorized, holder = sub, cap = exp")
    args = ap.parse_args(argv)
    records, bad = load(args.session)
    findings = bad + check(records, seq_rule=args.seq_rule, default=args.default,
                           max_viewers=args.max_viewers, lease_keys=args.lease_keys)
    for f in findings:
        print(f)
    ins = sum(r.get("dir") == "in" for r in records)
    print(f"{len(records)} records ({ins} in, {len(records) - ins} out), "
          f"{len({r.get('conn') for r in records})} connections: {len(findings)} findings",
          file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
