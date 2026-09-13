package mitris

// Board is the locked playfield: a width×height grid of piece references
// (nil = empty). It ports edu.mit.d54.plugins.mitris.MITrisBoard.
//
// Coordinates are bottom-origin, exactly as in the original: data[x][y] with
// x in [0,width) left-to-right and y in [0,height) where y=0 is the floor and
// y increases upward. The renderer flips y so the top of the screen is high y.
//
// Board is treated as an immutable value: AddPiece and ClearRows return a new
// Board rather than mutating the receiver, mirroring the Java copy-constructor
// style so that CheckPiece can probe placements without side effects.
type Board struct {
	width, height int
	data          [][]*Piece
	numCleared    int
}

// NewBoard allocates an empty width×height board.
func NewBoard(width, height int) *Board {
	data := make([][]*Piece, width)
	for x := range data {
		data[x] = make([]*Piece, height)
	}
	return &Board{width: width, height: height, data: data}
}

// clone deep-copies the board (Java copy constructor MITrisBoard(base)).
func (b *Board) clone() *Board {
	n := NewBoard(b.width, b.height)
	for x := range b.data {
		copy(n.data[x], b.data[x])
	}
	n.numCleared = b.numCleared
	return n
}

// Width returns the board width.
func (b *Board) Width() int { return b.width }

// Height returns the board height.
func (b *Board) Height() int { return b.height }

// At returns the piece occupying (x,y), or nil (Java getPosition).
func (b *Board) At(x, y int) *Piece { return b.data[x][y] }

// CheckPiece reports whether piece can occupy (x,y) at the given rotation
// (Java checkPiece): true iff AddPiece would succeed.
func (b *Board) CheckPiece(piece *Piece, rot, x, y int) bool {
	if piece == nil {
		return false
	}
	return b.AddPiece(piece, rot, x, y) != nil
}

// AddPiece returns a new board with piece stamped at (x,y)/rot, or nil if the
// placement is out of bounds or overlaps a locked cell. It ports MITrisBoard's
// addPiece, including its four-case rotation transform and its rule that cells
// above the top of the board (py>=height) are legal but simply not stored — so
// a piece may protrude past the top row.
func (b *Board) AddPiece(piece *Piece, rot, x, y int) *Board {
	if piece == nil {
		return b
	}
	ret := b.clone()
	rot %= piece.MaxRot
	for i := 0; i < piece.NumComponents(); i++ {
		px, py := x, y
		switch rot {
		case 0:
			px += piece.X(i)
			py += piece.Y(i)
		case 1:
			px += piece.Y(i)
			py -= piece.X(i)
		case 2:
			px -= piece.X(i)
			py -= piece.Y(i)
		case 3:
			px -= piece.Y(i)
			py += piece.X(i)
		default:
			panic("invalid rotation")
		}
		switch {
		case py < 0 || px < 0 || px >= b.width: // out of bounds
			return nil
		case py < b.height && ret.data[px][py] != nil: // overlap
			return nil
		default:
			if py < b.height {
				ret.data[px][py] = piece
			}
		}
	}
	return ret
}

// ClearRows returns a new board with full rows removed and the remaining rows
// compacted toward the floor (Java clearRows). numCleared accumulates across
// the whole game and drives the level.
func (b *Board) ClearRows() *Board {
	ret := b.clone()
	outY := 0
	for y := 0; y < b.height; y++ {
		rowClear := true
		for x := 0; x < b.width; x++ {
			if b.At(x, y) == nil {
				rowClear = false
			}
		}
		if !rowClear {
			for x := 0; x < b.width; x++ {
				ret.data[x][outY] = b.data[x][y]
			}
			outY++
		} else {
			ret.numCleared++
		}
	}
	for y := outY; y < b.height; y++ {
		for x := 0; x < b.width; x++ {
			ret.data[x][y] = nil
		}
	}
	return ret
}

// ShiftBoardDown returns a new board shifted down one row, top row cleared
// (Java shiftBoardDown); used by the game-over "drain" animation.
func (b *Board) ShiftBoardDown() *Board {
	ret := b.clone()
	for y := 0; y < b.height-1; y++ {
		for x := 0; x < b.width; x++ {
			ret.data[x][y] = b.data[x][y+1]
		}
	}
	for x := 0; x < b.width; x++ {
		ret.data[x][b.height-1] = nil
	}
	return ret
}

// IsBoardEmpty reports whether no cell is filled (Java isBoardEmpty).
func (b *Board) IsBoardEmpty() bool {
	for y := 0; y < b.height; y++ {
		for x := 0; x < b.width; x++ {
			if b.data[x][y] != nil {
				return false
			}
		}
	}
	return true
}

// Level returns numCleared/4 (Java getLevel).
func (b *Board) Level() int { return b.numCleared / 4 }

// NumCleared returns the cumulative lines cleared (Java getNumCleared).
func (b *Board) NumCleared() int { return b.numCleared }
