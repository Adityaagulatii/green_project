#!/bin/sh
# Re-record the asciinema casts in docs/media/casts/ and convert each to a
# GIF with agg. Recordings stay in the repo: never upload them.
#
#   docs/media/record_casts.sh [sim|repl|bsd|countdown|mock|gif|all]
#
# Needs asciinema 3.x and agg (FreeBSD: pkg install asciinema asciinema-agg)
# and a python3 on PATH that can import the engine (no extra packages).
# "bsd" needs contrib/bsd-tetris-mit/build.sh to have been run first.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
CASTS=$HERE/casts
PY=${PY:-python3}
FONT=${FONT:-DejaVu Sans Mono}
cd "$ROOT"
export PYTHONPATH=impl/python/engine:impl/python/sim
export TERM=xterm-256color

rec() {  # rec NAME COLSxROWS TITLE COMMAND
	asciinema rec --overwrite --headless --quiet -f asciicast-v2 \
		--window-size "$2" --title "$3" -c "$4" "$CASTS/$1.cast"
}

gif() {  # gif NAME FONT_SIZE FPS_CAP
	agg --quiet --theme asciinema --font-family "$FONT" --font-size "$2" \
		--line-height 1.2 --fps-cap "$3" --idle-time-limit 3 \
		--last-frame-duration 3 "$CASTS/$1.cast" "$CASTS/$1.gif"
}

sim() {
	rec sim-seed42-bot 44x20 "17x9 Tetris: seed 42, demo bot, ANSI simulator (30 s)" \
		"$PY -m tetris_sim --seed 42 --bot --frames 900 --ansi"
}

repl() {
	rec repl-engine 92x30 "17x9 Tetris: driving the pure engine from python -i" \
		"$PY docs/media/pty_drive.py repl"
}

bsd() {
	rec bsd-tetris-17x9 80x22 "BSD tetris(6) patched to a 9x17 well, unattended" \
		"$PY docs/media/pty_drive.py bsd-tetris"
}

# SPEC 8.4 countdown, frames 0-90 of KAV-01 (every frame pinned); keep
# this path: SPEC links docs/media/casts/countdown.gif.
countdown() {
	rec countdown 80x26 "KAV-01 frames 0-90: SPEC 8.4 countdown, plain ASCII" \
		"$PY docs/media/ascii_player.py countdown"
}

# 30 s plain-ASCII replay of KAV-14 (its last 900 frames), digests checked.
mock() {
	rec mock-game-30s 80x26 "KAV-14 replay: 900 frames, plain ASCII, digests verified" \
		"$PY docs/media/ascii_player.py game"
}

gifs() {
	gif sim-seed42-bot 14 15
	gif repl-engine 14 10
	gif bsd-tetris-17x9 14 10
	gif countdown 14 30
	gif mock-game-30s 14 15
}

mkdir -p "$CASTS"
case "${1:-all}" in
sim) sim ;;
repl) repl ;;
bsd) bsd ;;
countdown) countdown ;;
mock) mock ;;
gif) gifs ;;
all) sim; repl; bsd; countdown; mock; gifs ;;
*) echo "usage: $0 [sim|repl|bsd|countdown|mock|gif|all]" >&2; exit 2 ;;
esac
ls -l "$CASTS"
