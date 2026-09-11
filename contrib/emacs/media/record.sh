#!/bin/sh
# Re-record contrib/emacs/media/green-building-9x17.{cast,gif}: the stock
# Emacs overlay display (tetris-mit-display.el, 9 wide x 17 tall) in
# "emacs -nw" with truecolor, playing a SPEC-engine game. Recordings stay
# in the repo: never upload them.
#
#   contrib/emacs/media/record.sh [trace|cast|gif|all]      (default: all)
#
#   trace  rewrite green-building-game.json with green_building_game.py
#   cast   record the cast with asciinema (headless, asciicast v2)
#   gif    convert the cast with agg
#
# Needs emacs (28.1+), asciinema 3.x and agg (FreeBSD: pkg install asciinema
# asciinema-agg), and a python that can import the engine: $PY, then
# $TETRIS_MIT_PYTHON, then python3. The same settings as docs/media.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
PY=${PY:-${TETRIS_MIT_PYTHON:-python3}}
EMACS=${EMACS:-emacs}
FONT=${FONT:-DejaVu Sans Mono}
SIZE=${SIZE:-80x24}
NAME=green-building-9x17
cd "$ROOT"
export TERM=xterm-256color COLORTERM=truecolor TETRIS_MIT_PYTHON="$PY"

trace() {
	PYTHONPATH=impl/python/engine:impl/python/sim "$PY" "$HERE/green_building_game.py"
}

# emacs -nw is "emacs -Q" plus one init file, in a throwaway init
# directory. It runs before the terminal is set up: it turns off xterm.el's
# capability queries, which a headless recorder never answers (each would
# cost its 2 s timeout at startup), the menu bar and the startup screen.
cast() {
	work=$(mktemp -d)
	trap 'rm -rf "$work"' EXIT
	cat > "$work/init.el" <<-'INIT'
	;;; init.el --- throwaway init for record.sh  -*- lexical-binding: t -*-
	(setq xterm-query-timeout nil
	      inhibit-startup-screen t)
	(menu-bar-mode -1)
	INIT
	summary="$work/summary.txt"
	asciinema rec --overwrite --headless --quiet -f asciicast-v2 \
		--window-size "$SIZE" \
		--title "17x9 Tetris on the Emacs display at MIT Green Building geometry (9 x 17)" \
		-c "TETRIS_MIT_RECORD_SUMMARY='$summary' $EMACS -nw --init-directory '$work' \
--no-site-file --no-site-lisp --no-splash --no-x-resources -L contrib/emacs \
-l contrib/emacs/media/record-game.el; \
echo 'MIT Green Building: 9 x 17 windows, tetris-mit-display.el in emacs -nw'; \
cat '$summary'" \
		"$work/raw.cast"
	cat "$summary"
	grep -q -- '-- PASS$' "$summary"
	trim "$work/raw.cast" "$HERE/$NAME.cast"
}

# Emacs's startup and the server's launch take a few seconds that vary
# with host load, and Emacs draws *scratch* before it loads
# record-game.el. record-game.el writes an invisible marker (an OSC 2
# terminal title) when the game starts. Everything the terminal received
# before it is folded into one event at t = 0, byte for byte, so the cast
# opens on the empty display and the rest keeps its recorded timing.
trim() {  # trim RAW OUT
	"$PY" - "$1" "$2" <<-'PY'
	import json, sys
	src, dst = sys.argv[1:]
	with open(src, encoding="utf-8") as fh:
	    lines = fh.read().splitlines()
	events = [json.loads(line) for line in lines[1:]]
	cut = next(i for i, e in enumerate(events) if "tetris-mit-record: play" in e[2])
	t0 = events[cut][0]
	lead = "".join(e[2] for e in events[:cut + 1] if e[1] == "o")
	out = [lines[0], json.dumps([0.0, "o", lead])]
	out += [json.dumps([round(e[0] - t0, 6), e[1], e[2]]) for e in events[cut + 1:]]
	with open(dst, "w", encoding="utf-8") as fh:
	    fh.write("\n".join(out) + "\n")
	print(f"folded {t0:.2f} s of startup ({cut + 1} events) into t = 0")
	PY
}

# --idle-time-limit 6 keeps the game's real timing: its longest still
# stretch is the 150-frame (5 s) white wait after game over.
gif() {
	agg --quiet --theme asciinema --font-family "$FONT" --font-size 16 \
		--line-height 1.2 --fps-cap 15 --idle-time-limit 6 \
		--last-frame-duration 4 "$HERE/$NAME.cast" "$HERE/$NAME.gif"
}

case "${1:-all}" in
trace) trace ;;
cast) cast ;;
gif) gif ;;
all) trace; cast; gif ;;
*) echo "usage: $0 [trace|cast|gif|all]" >&2; exit 2 ;;
esac
ls -l "$HERE"
