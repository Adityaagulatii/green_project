// Package mitris is a faithful Go port of the MITris game from the MIT Green
// Building "Display 54" hack (April 20, 2012), originally written in Java.
//
// Upstream: https://github.com/mitrisdev/d54  (BSD 3-Clause)
//
//	Copyright (c) 2012-2014, the author(s). All rights reserved.
//
// This port preserves the observable game logic of the original
// edu.mit.d54.plugins.mitris package: the tetromino set and their component
// offsets, the four-case rotation transform, the 5/(level+5) gravity cadence,
// level = linesCleared/4, and the bottom-origin board (y increases upward,
// pieces spawn at the top and fall toward y=0). It is NOT the modern
// SPEC.md game (no hold, no 7-bag, no DAS/ARR, no wall kicks, no scoring
// beyond lines cleared) — see impl/go/README.md.
package mitris

import "math/rand"

// numComponents is the number of cells in every tetromino (N_COMPONENTS in the
// original).
const numComponents = 4

// RGB is a 24-bit color, matching the display's 24-bit color mode.
type RGB struct{ R, G, B uint8 }

// Piece is one of the seven tetrominoes. It mirrors the Java enum
// edu.mit.d54.plugins.mitris.Piece: each piece carries the (x,y) offsets of its
// four component cells, its number of distinct rotations, and its base color.
type Piece struct {
	Name   byte
	off    [numComponents][2]int // off[i] = {x, y}
	MaxRot int
	color  RGB
}

// X returns the x offset of component ind (Java getX).
func (p *Piece) X(ind int) int { return p.off[ind][0] }

// Y returns the y offset of component ind (Java getY).
func (p *Piece) Y(ind int) int { return p.off[ind][1] }

// NumComponents returns the cell count (Java getNumComponents).
func (p *Piece) NumComponents() int { return numComponents }

// pieces is the canonical L,S,Z,J,I,T,O order and geometry from Piece.java.
// Offsets and colors are copied verbatim from the original enum constructor.
var pieces = [7]Piece{
	{Name: 'L', MaxRot: 4, color: RGB{255, 50, 0},
		off: [4][2]int{{-1, 0}, {0, 0}, {1, 0}, {1, 1}}},
	{Name: 'S', MaxRot: 2, color: RGB{0, 255, 0}, // Color.GREEN
		off: [4][2]int{{0, 0}, {1, 0}, {-1, 1}, {0, 1}}},
	{Name: 'Z', MaxRot: 2, color: RGB{255, 0, 0}, // Color.RED
		off: [4][2]int{{-1, 0}, {0, 0}, {0, 1}, {1, 1}}},
	{Name: 'J', MaxRot: 4, color: RGB{0, 0, 255}, // Color.BLUE
		off: [4][2]int{{-1, 0}, {0, 0}, {1, 0}, {-1, 1}}},
	{Name: 'I', MaxRot: 2, color: RGB{0, 255, 255}, // Color.CYAN
		off: [4][2]int{{-2, 0}, {-1, 0}, {0, 0}, {1, 0}}},
	{Name: 'T', MaxRot: 4, color: RGB{178, 0, 255},
		off: [4][2]int{{-1, 0}, {0, 0}, {1, 0}, {0, 1}}},
	{Name: 'O', MaxRot: 1, color: RGB{200, 255, 0},
		off: [4][2]int{{-1, 0}, {0, 0}, {-1, 1}, {0, 1}}},
}

// Pieces returns the seven tetrominoes in canonical order. The returned
// pointers are stable and shared, matching the Java enum's singleton semantics
// (a board cell stores a reference to the piece that filled it).
func Pieces() []*Piece {
	out := make([]*Piece, len(pieces))
	for i := range pieces {
		out[i] = &pieces[i]
	}
	return out
}

// randSource lets tests inject determinism; the original used java.util.Random.
var randSource = rand.Intn

// GetRandom returns a uniformly random piece (Java Piece.getRandom). The
// original picks uniformly at random with no 7-bag.
func GetRandom() *Piece {
	return &pieces[randSource(len(pieces))]
}
