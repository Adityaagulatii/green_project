#!/bin/sh
# The single gate: every implementation's conformance driver against the
# sealed traces in spec/conformance/traces. PASS means zero findings.
#
# Before believing a PASS, the gate verifies the verifier:
#   1. a corrupted copy of the traces MUST be rejected, through every
#      driver (so no driver can pass by echoing the traces' own answers);
#   2. every mutant (deliberately wrong) engine MUST be rejected;
#   3. a SPEC.md whose Appendix A has a hand-edited digest MUST be rejected.
# Then:
#   4. SPEC.md Appendix A MUST equal the one generated from the traces;
#   5. every implementation MUST pass.
#
#   bin/verify.sh              self-test, then the checks
#   bin/verify.sh --no-selftest
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY=${PYTHON:-/scratch/venvs/tetris-py/bin/python}
[ -x "$PY" ] || PY=$(command -v python3)
RUN="$PY $ROOT/spec/conformance/run.py"
APPENDIX="$PY $ROOT/spec/conformance/gen_appendix.py"
TRACES="$ROOT/spec/conformance/traces"
PY_DRIVER="env PYTHONPATH=$ROOT/impl/python/engine $PY -m tetris_engine.conformance"
HY_DRIVER="env PYTHONPATH=$ROOT/impl/hy $PY -m hy -m tetris_hy.conformance"
MUTANTS="ccw-release-keeps-dcd gravity-ceil-cadence level-target-plus-six standard-180-kicks no-ghost"

# Implementations, in phase order. Later phases add a name here and a
# line to driver().
IMPLS="python hy"
driver() {
    case $1 in
        python) echo "$PY_DRIVER" ;;
        hy) echo "$HY_DRIVER" ;;
        *) echo "unknown implementation $1" >&2; return 1 ;;
    esac
}

status=0
fail() { echo "GATE FINDING: $*"; status=1; }

selftest() {
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/tetris-verify.XXXXXX")
    trap 'rm -rf "$tmp"' EXIT INT TERM

    # 1. corrupted trace: flip one hex digit of one digest
    mkdir "$tmp/traces"
    cp "$TRACES"/*.json "$tmp/traces"/
    victim=$(ls "$tmp/traces"/*.json | head -n 1)
    "$PY" - "$victim" <<'EOF'
import json, sys
p = sys.argv[1]
t = json.load(open(p))
d = t["digests"][len(t["digests"]) // 2]
t["digests"][len(t["digests"]) // 2] = ("0" if d[0] != "0" else "1") + d[1:]
json.dump(t, open(p, "w"), separators=(",", ":"))
EOF
    for impl in $IMPLS; do
        if $RUN --traces "$tmp/traces" --impl "$(driver "$impl")" >/dev/null 2>&1; then
            fail "self-test: corrupted trace was accepted by the $impl driver"
        else
            echo "self-test ok: corrupted trace rejected ($impl driver)"
        fi
    done

    # 2. mutant engines
    for m in $MUTANTS; do
        if $RUN --impl "env PYTHONPATH=$ROOT/impl/python/engine $PY $ROOT/impl/python/conformance/mutants.py $m" >/dev/null 2>&1; then
            fail "self-test: mutant '$m' was accepted"
        else
            echo "self-test ok: mutant '$m' rejected"
        fi
    done

    # 3. a hand-edited digest in a copy of SPEC.md's Appendix A.2b
    cp "$ROOT/SPEC.md" "$tmp/SPEC.md"
    "$PY" - "$tmp/SPEC.md" <<'EOF'
import re, sys
p = sys.argv[1]
text = open(p, encoding="utf-8").read()
start = text.index("**Table A.2b.", text.index("<!-- BEGIN KAV"))
m = re.compile(r"`([0-9a-f]{64})`").search(text, start)
d = m.group(1)
edited = ("0" if d[0] != "0" else "1") + d[1:]
open(p, "w", encoding="utf-8").write(text[:m.start(1)] + edited + text[m.end(1):])
EOF
    if $APPENDIX --check --spec "$tmp/SPEC.md" >/dev/null 2>&1; then
        fail "self-test: a hand-edited Appendix A digest was accepted"
    else
        echo "self-test ok: hand-edited Appendix A digest rejected"
    fi
}

run() {
    name=$1; shift
    echo "== $name"
    if ! $RUN --impl "$*"; then
        fail "implementation '$name' does not conform"
    fi
}

[ "${1:-}" = "--no-selftest" ] || selftest

# 4. SPEC.md Appendix A is generated from the traces, never by hand
echo "== spec appendix"
if ! $APPENDIX --check; then
    fail "SPEC.md Appendix A differs from the traces (spec/conformance/gen_appendix.py --write)"
fi

# 5. implementations
for impl in $IMPLS; do
    run "$impl" "$(driver "$impl")"
done

if [ "$status" -eq 0 ]; then echo "GATE: PASS"; else echo "GATE: FAIL"; fi
exit "$status"
