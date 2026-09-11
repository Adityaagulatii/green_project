"""dlk1, the display lease key: parse, sign and verify offline (experiment 002).

The format is the steward's proposal,
/scratch/work/tetris-parallel/inputs/dlk1-display-lease-key.md.  A key is

    dlk1.<P>.<S>
    P = base64url (no padding) of the canonical JSON of the claims
    S = base64url (no padding) of HMAC-SHA256(secret[kid], ASCII("dlk1." + P))

The reservation system signs keys, and a display relay started with lease
secrets verifies them on every reserve, never calling the issuer.  This is an
extension of wal.sh/tools/display v0.2.1, not part of it: it lifts NR-AUTH
only for a relay given secrets.  Standard library only (hmac, hashlib,
base64, json).

verify() checks in the proposal's order and returns the first failure's
detail code (None when the key is good) with the claims, once they are
signed:

    1 shape       missing | malformed
    2 decoding    malformed          (base64url, JSON object)
    3 kid         unknown-kid
    4 signature   bad-signature      (constant time, before any claim is trusted)
    5 claims      bad-claims         (exactly the table, types, canonical bytes)
    6 display     wrong-display
    7 time        not-yet | expired  (now >= nbf - 5 and now < exp)
    8 format      format-not-allowed
"""
import base64
import hashlib
import hmac
import json
import re

PREFIX = "dlk1"
DETAILS = ("missing", "malformed", "unknown-kid", "bad-signature", "bad-claims",
           "wrong-display", "not-yet", "expired", "format-not-allowed")
CLAIMS = frozenset(("v", "kid", "iss", "sub", "rid", "display", "nbf", "exp", "fmt", "jti"))
FORMATS = ("pal16", "hex", "rgb24")
ISSUER = "dres"
SKEW = 5          # seconds of clock skew allowed, at the start of the slot only
MAX_SLOT = 86400  # exp - nbf at most one day
KID = re.compile(r"[a-z0-9-]{1,32}")
TEST_KID, TEST_SECRET = "test", bytes(range(32))   # the published, test-only secret
_B64U = re.compile(r"[A-Za-z0-9_-]*")


def b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_decode(text: str) -> bytes:
    """Strict base64url: the URL-safe alphabet, no padding, and canonical (the
    bytes re-encode to the same text, so unused trailing bits are zero)."""
    if not isinstance(text, str) or not _B64U.fullmatch(text) or len(text) % 4 == 1:
        raise ValueError("not base64url")
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    if b64u_encode(data) != text:
        raise ValueError("non-canonical base64url")
    return data


def canonical(claims: dict) -> bytes:
    """UTF-8 JSON with sorted keys and no whitespace."""
    return json.dumps(claims, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def mac(secret: bytes, payload: str) -> bytes:
    return hmac.new(secret, (PREFIX + "." + payload).encode("ascii"), hashlib.sha256).digest()


def sign(claims: dict, secret: bytes) -> str:
    """A key for CLAIMS (not checked: an issuer's or a test's business)."""
    p = b64u_encode(canonical(claims))
    return f"{PREFIX}.{p}.{b64u_encode(mac(secret, p))}"


def parse_secrets(text: str) -> dict:
    """`kid hex64` lines (blank lines and # comments ignored) -> {kid: 32 bytes}."""
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or not KID.fullmatch(parts[0]) or not re.fullmatch(
                r"[0-9a-fA-F]{64}", parts[1]):
            raise ValueError(f"line {n}: want `kid hex64` with kid [a-z0-9-]{{1,32}}")
        if parts[0] in out:
            raise ValueError(f"line {n}: kid {parts[0]!r} twice")
        out[parts[0]] = bytes.fromhex(parts[1])
    if not out:
        raise ValueError("no secrets")
    return out


def load_secrets(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return parse_secrets(f.read())


def _reject_constant(name):
    raise ValueError(f"{name} is not JSON")


def _decode(key):
    """(parts, payload bytes, claims) of a key of the right shape, or a detail."""
    if key is None or key == "":
        return "missing"
    if not isinstance(key, str):
        return "malformed"
    parts = key.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        return "malformed"
    try:
        raw, sig = b64u_decode(parts[1]), b64u_decode(parts[2])
        claims = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError):
        return "malformed"
    if not isinstance(claims, dict):
        return "malformed"
    return parts, raw, sig, claims


def peek(key):
    """The claims of a key, NOT verified (a client reading its own key's fmt
    or exp); None if it does not decode."""
    d = _decode(key)
    return None if isinstance(d, str) else d[3]


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def claims_ok(c: dict) -> bool:
    """Exactly the proposal's claims, with their types and bounds."""
    if set(c) != CLAIMS:
        return False
    strings = all(isinstance(c[k], str) for k in ("kid", "iss", "sub", "rid", "display", "jti"))
    return (strings and _int(c["v"]) and c["v"] == 1 and bool(KID.fullmatch(c["kid"]))
            and c["iss"] == ISSUER and 1 <= len(c["sub"]) <= 64
            and _int(c["nbf"]) and _int(c["exp"]) and 0 <= c["nbf"] < c["exp"]
            and c["exp"] - c["nbf"] <= MAX_SLOT
            and isinstance(c["fmt"], list) and c["fmt"]
            and all(isinstance(f, str) and f in FORMATS for f in c["fmt"])
            and len(set(c["fmt"])) == len(c["fmt"]))


def verify(key, secrets: dict, *, display: str, fmt: str = "pal16", now: float):
    """(detail, claims): detail is None for a good key, else the first failing
    check's code (DETAILS); claims are returned only once the signature holds."""
    d = _decode(key)
    if isinstance(d, str):
        return d, None
    parts, raw, sig, claims = d
    kid = claims.get("kid")
    if not isinstance(kid, str) or kid not in secrets:
        return "unknown-kid", None
    if not hmac.compare_digest(mac(secrets[kid], parts[1]), sig):
        return "bad-signature", None
    if not claims_ok(claims) or canonical(claims) != raw:
        return "bad-claims", None
    if claims["display"] != display:
        return "wrong-display", claims
    if now < claims["nbf"] - SKEW:
        return "not-yet", claims
    if now >= claims["exp"]:
        return "expired", claims
    if fmt not in claims["fmt"]:
        return "format-not-allowed", claims
    return None, claims
