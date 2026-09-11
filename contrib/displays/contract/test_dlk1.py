"""The dlk1 verifier against the proposal's text: every detail code, the check
order, the time boundaries nbf-5, nbf-6, exp-1 and exp, and the published
vectors when the reservation workstream has written them.

    cd contrib/displays && python -m pytest contract/test_dlk1.py
"""
import hmac
import json
import os

import pytest

from contract import dlk1

SECRETS = {dlk1.TEST_KID: dlk1.TEST_SECRET}
NBF, EXP = 1_789_000_000, 1_789_000_600
VECTORS = "/scratch/work/tetris-parallel/inputs/dlk1-vectors.json"


def claims(**kw):
    return {"v": 1, "kid": "test", "iss": "dres", "sub": "alice@lab", "rid": "r-1",
            "display": "green-building", "nbf": NBF, "exp": EXP, "fmt": ["pal16", "hex"],
            "jti": "j-1"} | kw


def key(**kw):
    return dlk1.sign(claims(**kw), dlk1.TEST_SECRET)


def check(k, display="green-building", fmt="pal16", now=NBF + 10, secrets=SECRETS):
    return dlk1.verify(k, secrets, display=display, fmt=fmt, now=now)[0]


def raw_key(payload: bytes, secret=dlk1.TEST_SECRET):
    """A key over an arbitrary payload (the canonical-bytes check comes after
    the signature)."""
    p = dlk1.b64u_encode(payload)
    return f"dlk1.{p}.{dlk1.b64u_encode(dlk1.mac(secret, p))}"


def test_a_good_key():
    k = key()
    assert k.startswith("dlk1.") and k.count(".") == 2 and "=" not in k
    detail, got = dlk1.verify(k, SECRETS, display="green-building", fmt="hex", now=NBF)
    assert detail is None and got == claims()
    assert dlk1.peek(k) == claims()


@pytest.mark.parametrize("k", [None, ""])
def test_missing(k):
    assert check(k) == "missing"


@pytest.mark.parametrize("k", [
    "dlk1", "dlk1.a", "dlk1.a.b.c", "dlk2." + key().split(".", 1)[1], 42,
    "dlk1.@@@@." + key().split(".")[2],                              # not base64url
    "dlk1." + key().split(".")[1] + "=." + key().split(".")[2],      # padding
    "dlk1." + key().split(".")[1][:-1] + "_." + key().split(".")[2], # non-canonical bits
    raw_key(b"not json"), raw_key(b"[1,2]"), raw_key(b'{"v":NaN}'), raw_key(b"\xff\xfe"),
])
def test_malformed(k):
    assert check(k) == "malformed"


def test_unknown_kid():
    assert check(key(kid="other")) == "unknown-kid"
    assert check(key(kid="TEST")) == "unknown-kid"          # checked before the claims
    assert check(raw_key(b'{"kid":7}')) == "unknown-kid"
    assert check(key(), secrets={"new": bytes(32)}) == "unknown-kid"


def test_bad_signature():
    k = key()
    p, s = k.split(".")[1:]
    flipped = s[:5] + ("A" if s[5] != "A" else "B") + s[6:]
    assert check(f"dlk1.{p}.{flipped}") == "bad-signature"
    assert check(dlk1.sign(claims(), bytes(32))) == "bad-signature"
    assert check(f"dlk1.{p}.") == "bad-signature"           # an empty signature
    # the signature is checked before any other claim is trusted
    assert check(dlk1.sign(claims(v=2, exp=NBF), bytes(32))) == "bad-signature"


@pytest.mark.parametrize("bad", [
    {"v": 2}, {"v": True}, {"v": 1.0}, {"iss": "other"}, {"sub": ""}, {"sub": "x" * 65},
    {"rid": 5}, {"display": None}, {"jti": ["j"]}, {"nbf": float(NBF)}, {"exp": NBF},
    {"exp": NBF + 86401}, {"nbf": -1}, {"fmt": []}, {"fmt": ["pal16", "pal16"]},
    {"fmt": ["rgb"]}, {"fmt": "pal16"}, {"kid": "test", "extra": 1},
])
def test_bad_claims(bad):
    assert check(key(**bad)) == "bad-claims"


def test_bad_claims_missing_and_not_canonical():
    c = claims()
    del c["jti"]
    assert check(dlk1.sign(c, dlk1.TEST_SECRET)) == "bad-claims"
    spaced = json.dumps(claims(), sort_keys=True).encode()      # whitespace
    unsorted = json.dumps(claims(), separators=(",", ":")).encode()
    dup = dlk1.canonical(claims())[:-1] + b',"jti":"j-1"}'
    for payload in (spaced, unsorted, dup):
        assert check(raw_key(payload)) == "bad-claims"
    assert check(raw_key(dlk1.canonical(claims()))) is None   # the same bytes, canonical
    assert check(key(sub="x" * 64)) is None and check(key(exp=NBF + 86400)) is None


def test_wrong_display():
    assert check(key(), display="tetris") == "wrong-display"


@pytest.mark.parametrize("now,detail", [
    (NBF - 6, "not-yet"), (NBF - 5, None), (NBF, None), (EXP - 1, None), (EXP, "expired"),
    (EXP + 1, "expired"), (NBF - 5.5, "not-yet"), (EXP - 0.001, None)])
def test_time_boundaries(now, detail):
    assert check(key(), now=now) == detail


def test_format_not_allowed():
    assert check(key(), fmt="rgb24") == "format-not-allowed"
    assert check(key(fmt=["rgb24"]), fmt="rgb24") is None


def test_the_order_decides_the_detail():
    both = key(display="tetris", exp=NBF + 20, fmt=["hex"])
    assert check(both, display="green-building", now=NBF + 30, fmt="rgb24") == "wrong-display"
    assert check(both, display="tetris", now=NBF + 30, fmt="rgb24") == "expired"
    assert check(both, display="tetris", now=NBF - 30, fmt="rgb24") == "not-yet"
    assert check(both, display="tetris", now=NBF, fmt="rgb24") == "format-not-allowed"


def test_the_signature_is_compared_in_constant_time(monkeypatch):
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(dlk1.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    assert check(key()) is None and calls == [1]


def test_secrets_file():
    text = "# kid hex64\n\ntest " + dlk1.TEST_SECRET.hex() + "\nnew-2 " + "ab" * 32 + "\n"
    assert dlk1.parse_secrets(text) == {"test": dlk1.TEST_SECRET, "new-2": bytes([0xAB]) * 32}
    for bad in ("Test " + "00" * 32, "test " + "00" * 31, "test", "x" * 33 + " " + "00" * 32,
                "test " + "00" * 32 + "\ntest " + "11" * 32, "# only a comment"):
        with pytest.raises(ValueError):
            dlk1.parse_secrets(bad)


@pytest.mark.skipif(not os.path.exists(VECTORS), reason=f"{VECTORS} not published yet")
def test_published_vectors():
    doc = json.load(open(VECTORS, encoding="utf-8"))
    secrets = {k: bytes.fromhex(v) for k, v in doc.get("secrets", {"test": dlk1.TEST_SECRET.hex()}
                                                     ).items()}
    cases = doc["cases"] if isinstance(doc, dict) else doc
    wrong = []
    for c in cases:
        want = c.get("detail", c.get("expect"))
        want = None if want in (None, "ok", "valid") else want
        got = dlk1.verify(c.get("key"), secrets, display=c["display"],
                          fmt=c.get("format", "pal16"), now=c["now"])[0]
        if got != want:
            wrong.append((c.get("name"), want, got))
    assert wrong == []
