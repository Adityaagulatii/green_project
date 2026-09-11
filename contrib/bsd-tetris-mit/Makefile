# Mock-port wrapper around fetch.sh / build.sh. Plain POSIX make syntax:
# works with FreeBSD make (bmake) and GNU make.

all: work/tetris-mit

work/tetris-mit: mit-17x9.patch build.sh fetch.sh
	./build.sh

fetch:
	./fetch.sh >/dev/null

# Scores go to $HOME/.tetris-17x9.scores; keep them inside work/.
run: work/tetris-mit
	mkdir -p work/home
	HOME="$$(pwd)/work/home" ./work/tetris-mit -p

clean:
	rm -rf work

distclean: clean
	rm -rf distfiles

.PHONY: all fetch run clean distclean
