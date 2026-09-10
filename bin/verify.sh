#!/bin/sh
# The single gate: every implementation's conformance driver against the
# sealed traces in spec/conformance/traces. PASS means zero findings.
#
# Before believing a PASS, the gate verifies the verifier:
#   1. a corrupted copy of the traces MUST be rejected;
#   2. every mutant (deliberately wrong) engine MUST be rejected;
#   3. the reference MUST pass.
#
#   bin/verify.sh              self-test, then all implementations
#   bin/verify.sh --no-selftest
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY=${PYTHON:-/scratch/venvs/tetris-py/bin/python}
[ -x "$PY" ] || PY=$(command -v python3)
RUN="$PY $ROOT/spec/conformance/run.py"
TRACES="$ROOT/spec/conformance/traces"
PY_DRIVER="env PYTHONPATH=$ROOT/impl/python/engine $PY -m tetris_engine.conformance"
MUTANTS="ccw-release-keeps-dcd gravity-ceil-cadence level-target-plus-six standard-180-kicks no-ghost"

status=0
fail() { echo "GATE FINDING: $*"; status=1; }

selftest() {
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/tetris-verify.XXXXXX")
    trap 'rm -rf "$tmp"' EXIT INT TERM

    # 1. corrupted trace: flip one hex digit of one digest
    cp "$TRACES"/*.json "$tmp"/
    victim=$(ls "$tmp"/*.json | head -n 1)
    "$PY" - "$victim" <<'EOF'
import json, sys
p = sys.argv[1]
t = json.load(open(p))
d = t["digests"][len(t["digests"]) // 2]
t["digests"][len(t["digests"]) // 2] = ("0" if d[0] != "0" else "1") + d[1:]
json.dump(t, open(p, "w"), separators=(",", ":"))
EOF
    if $RUN --traces "$tmp" --impl "$PY_DRIVER" >/dev/null 2>&1; then
        fail "self-test: corrupted trace was accepted"
    else
        echo "self-test ok: corrupted trace rejected"
    fi

    # 2. mutant engines
    for m in $MUTANTS; do
        if $RUN --impl "env PYTHONPATH=$ROOT/impl/python/engine $PY $ROOT/impl/python/conformance/mutants.py $m" >/dev/null 2>&1; then
            fail "self-test: mutant '$m' was accepted"
        else
            echo "self-test ok: mutant '$m' rejected"
        fi
    done
}

run() {
    name=$1; shift
    echo "== $name"
    if ! $RUN --impl "$*"; then
        fail "implementation '$name' does not conform"
    fi
}

[ "${1:-}" = "--no-selftest" ] || selftest

# 3. implementations (later phases append their drivers here)
run python "$PY_DRIVER"

if [ "$status" -eq 0 ]; then echo "GATE: PASS"; else echo "GATE: FAIL"; fi
exit "$status"
