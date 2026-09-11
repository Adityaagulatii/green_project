#!/bin/sh
# Fetch the pinned upstream source: the same distfile, URL and checksum as
# the FreeBSD port games/bsdgames 0.76,2 (ports distinfo). Prints the
# tarball path on stdout; status goes to stderr.
set -eu

HERE=$(cd "$(dirname "$0")" && pwd)
DISTDIR=${DISTDIR:-$HERE/distfiles}
DISTFILE=pianojockl-bsdgames-v0.76_GH0.tar.gz
URL=https://codeload.github.com/pianojockl/bsdgames/tar.gz/v0.76
SHA256=fba58361de9c6c3d4489f60e3d61e6fde1655ef53a6bb12378880af19b2e748f
SIZE=2500755

sha256_of() {
	if command -v sha256 >/dev/null 2>&1; then
		sha256 -q "$1"
	else
		sha256sum "$1" | cut -d' ' -f1
	fi
}

mkdir -p "$DISTDIR"
if [ ! -f "$DISTDIR/$DISTFILE" ]; then
	echo "fetch.sh: downloading $URL" >&2
	if command -v fetch >/dev/null 2>&1; then
		fetch -q -o "$DISTDIR/$DISTFILE.part" "$URL"
	else
		curl -fsSL -o "$DISTDIR/$DISTFILE.part" "$URL"
	fi
	mv "$DISTDIR/$DISTFILE.part" "$DISTDIR/$DISTFILE"
fi

got=$(sha256_of "$DISTDIR/$DISTFILE")
if [ "$got" != "$SHA256" ]; then
	echo "fetch.sh: sha256 mismatch for $DISTFILE" >&2
	echo "  expected $SHA256 ($SIZE bytes)" >&2
	echo "  got      $got" >&2
	rm -f "$DISTDIR/$DISTFILE"
	exit 1
fi
echo "fetch.sh: $DISTFILE sha256 OK" >&2
echo "$DISTDIR/$DISTFILE"
