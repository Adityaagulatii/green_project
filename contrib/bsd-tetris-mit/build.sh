#!/bin/sh
# Build BSD tetris(6) with a 9-wide x 17-tall well (the MIT Green Building
# layout): fetch the pinned bsdgames 0.76 tarball, extract only tetris/,
# apply mit-17x9.patch and compile with the base system cc and the termcap
# API of the base ncurses. Output: work/tetris-mit. FreeBSD only (it uses
# arc4random_uniform, strtonum, ppoll and timespecsub from libc).
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$HERE/work
CC=${CC:-cc}
CFLAGS=${CFLAGS:--O2 -pipe}

TARBALL=$("$HERE/fetch.sh")
rm -rf "$WORK/src"
mkdir -p "$WORK/src" "$WORK/home"
tar -xzf "$TARBALL" -C "$WORK/src" bsdgames-0.76/tetris
patch -s -p1 -d "$WORK/src/bsdgames-0.76" < "$HERE/mit-17x9.patch"

cd "$WORK/src/bsdgames-0.76/tetris"
# -fcommon as in the FreeBSD port (screen.c's termcap globals)
$CC $CFLAGS -fcommon -Wall -o "$WORK/tetris-mit" \
	input.c screen.c shapes.c scores.c tetris.c -lncurses
echo "build.sh: built $WORK/tetris-mit (9x17 well)" >&2
echo "build.sh: play with  HOME=$WORK/home $WORK/tetris-mit -p" >&2
