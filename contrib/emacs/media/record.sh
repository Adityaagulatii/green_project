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
# directory: it runs before the terminal is set up, and turns off
# xterm.el's capability queries, which a headless recorder never answers
# (each would cost its 2 s timeout at startup).
cast() {
	work=$(mktemp -d)
	trap 'rm -rf "$work"' EXIT
	echo '(setq xterm-query-timeout nil)' > "$work/init.el"
	summary="$work/summary.txt"
	asciinema rec --overwrite --headless --quiet -f asciicast-v2 \
		--window-size "$SIZE" \
		--title "17x9 Tetris on the Emacs display at MIT Green Building geometry (9 x 17)" \
		-c "TETRIS_MIT_RECORD_SUMMARY='$summary' $EMACS -nw --init-directory '$work' \
--no-site-file --no-site-lisp --no-splash --no-x-resources -L contrib/emacs \
-l contrib/emacs/media/record-game.el; \
echo 'MIT Green Building: 9 x 17 windows, tetris-mit-display.el in emacs -nw'; \
cat '$summary'" \
		"$HERE/$NAME.cast"
	cat "$summary"
	grep -q -- '-- PASS$' "$summary"
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
