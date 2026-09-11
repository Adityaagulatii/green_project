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
#
# The protocol leg (contract, docs/PROTOCOL.md §10) runs only when SERVERS
# names servers, so with SERVERS empty the output is the engine gate's:
#   SERVERS="python" bin/verify.sh
# It verifies the verifier too (schemas reject bad messages; a corrupted
# transcript and every mutant server are rejected; a transparent gatekeeper
# and a WebSocket front pass), then:
#   6. spec/protocol/transcripts MUST equal what the traces give;
#   7. every server MUST pass every transcript.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY=${PYTHON:-/scratch/venvs/tetris-py/bin/python}
[ -x "$PY" ] || PY=$(command -v python3)
RUN="$PY $ROOT/spec/conformance/run.py"
APPENDIX="$PY $ROOT/spec/conformance/gen_appendix.py"
TRACES="$ROOT/spec/conformance/traces"
PY_DRIVER="env PYTHONPATH=$ROOT/impl/python/engine $PY -m tetris_engine.conformance"
HY_DRIVER="env PYTHONPATH=$ROOT/impl/hy $PY -m hy -m tetris_hy.conformance"
# The last mutant keeps every frame right but reports a phase log with an
# illegal edge (gameover -> playing): only the runner's state gate
# (T-legality, SPEC P20) can reject it.
MUTANTS="ccw-release-keeps-dcd gravity-ceil-cadence level-target-plus-six standard-180-kicks no-ghost illegal-edge-gameover-playing"

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

# Servers for the protocol leg. Each server() line prints "URL COMMAND";
# {port} in both is replaced by a free port that check.py chooses. The
# engine server must be started with the lockstep clock (PROTOCOL §4.2).
SERVERS=${SERVERS-}
CHECK="$PY $ROOT/spec/protocol/check.py"
TRANSCRIPTS="$PY $ROOT/spec/protocol/gen_transcripts.py"
PROXY="$PY $ROOT/spec/protocol/proxy.py"
PROTOCOL_MUTANTS="digest-flip repaint phase-alias illegal-edge drop-state stamp-shift no-events"
server() {
    case $1 in
        python) echo "tcp://127.0.0.1:{port} env PYTHONPATH=$ROOT/impl/python/engine:$ROOT/impl/python/sim $PY -m tetris_sim.server --mode engine --clock lockstep --port {port}" ;;
        # list python before python-ws in SERVERS: protocol_selftest proxies only the first
        python-ws) echo "ws://127.0.0.1:{port}/tetris-17x9 env PYTHONPATH=$ROOT/impl/python/engine:$ROOT/impl/python/sim $PY -m tetris_sim.server --mode engine --clock lockstep --transport ws --port {port}" ;;
        *) echo "unknown server $1" >&2; return 1 ;;
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

# The protocol leg (opt-in, see the top of this file).
protocol_selftest() {
    set -- $SERVERS
    spec=$(server "$1")
    url=${spec%% *}; cmd=${spec#* }
    uurl=$(printf '%s' "$url" | sed 's/{port}/{upstream_port}/g')
    ucmd=$(printf '%s' "$cmd" | sed 's/{port}/{upstream_port}/g')
    via() {  # a proxy.py listening on $1, in front of the first server
        echo "$PROXY $2 --listen $1 --upstream $uurl --upstream-launch '$ucmd'"
    }

    if $CHECK --selftest >/dev/null 2>&1; then
        echo "self-test ok: the schemas reject known-bad messages"
    else
        fail "self-test: spec/protocol/check.py --selftest"
    fi

    ptmp=$(mktemp -d "${TMPDIR:-/tmp}/tetris-protocol.XXXXXX")
    trap 'rm -rf "${tmp:-}" "$ptmp"' EXIT INT TERM
    cp "$ROOT"/spec/protocol/transcripts/*.jsonl "$ptmp"/
    "$PY" - "$ptmp/02-move-left-right.jsonl" <<'EOF'
import json, sys
p = sys.argv[1]
lines = open(p).read().splitlines()
for i, ln in enumerate(lines):
    x = json.loads(ln)
    if isinstance(x, list) and x[0] == "<" and "digest" in x[1] and x[1]["frame_no"] >= 100:
        d = x[1]["digest"]
        x[1]["digest"] = ("0" if d[0] != "0" else "1") + d[1:]
        lines[i] = json.dumps(x, separators=(",", ":"))
        break
open(p, "w").write("\n".join(lines) + "\n")
EOF
    if $CHECK --transcripts "$ptmp" --only 02-move-left-right --server "$url" --launch "$cmd" >/dev/null 2>&1; then
        fail "self-test: a corrupted transcript was accepted ($1 server)"
    else
        echo "self-test ok: corrupted transcript rejected ($1 server)"
    fi

    for m in $PROTOCOL_MUTANTS; do
        if $CHECK --only 02-move-left-right,12-game-over-reset --server "tcp://127.0.0.1:{port}" \
                --launch "$(via "tcp://127.0.0.1:{port}" "--mutate $m")" >/dev/null 2>&1; then
            fail "self-test: mutant server '$m' was accepted"
        else
            echo "self-test ok: mutant server '$m' rejected"
        fi
    done

    if $CHECK --server "tcp://127.0.0.1:{port}" --launch "$(via "tcp://127.0.0.1:{port}" "")" >/dev/null 2>&1; then
        echo "self-test ok: transparent gatekeeper passes ($1 server)"
    else
        fail "self-test: a transparent gatekeeper in front of the $1 server was rejected"
    fi
    ws="ws://127.0.0.1:{port}/tetris-17x9"
    if $CHECK --server "$ws" --launch "$(via "$ws" "")" >/dev/null 2>&1; then
        echo "self-test ok: WebSocket front passes ($1 server)"
    else
        fail "self-test: a WebSocket front for the $1 server was rejected"
    fi
}

if [ -n "$SERVERS" ]; then
    [ "${1:-}" = "--no-selftest" ] || protocol_selftest "$@"

    # 6. the transcripts are generated from the traces, never by hand
    echo "== protocol transcripts"
    if ! $TRANSCRIPTS --check; then
        fail "spec/protocol/transcripts differ from the traces (spec/protocol/gen_transcripts.py --write)"
    fi

    # 7. servers
    for s in $SERVERS; do
        echo "== server $s"
        if ! spec=$(server "$s"); then
            fail "unknown server '$s'"
            continue
        fi
        if ! $CHECK --server "${spec%% *}" --launch "${spec#* }"; then
            fail "server '$s' does not conform to the contract"
        fi
    done
fi

if [ "$status" -eq 0 ]; then echo "GATE: PASS"; else echo "GATE: FAIL"; fi
exit "$status"
