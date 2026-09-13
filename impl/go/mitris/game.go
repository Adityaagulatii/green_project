package mitris

// Game is the MITris state machine, porting edu.mit.d54.plugins.mitris.MITrisGame.
//
// Time is logical: ClockTick advances the clock by tickTime (= 1/framerate)
// every frame, so gravity is framerate-independent — exactly as in the Java
// original, where a higher preview framerate only smooths input, not the fall
// speed. Gravity steps the active piece down by one whenever the elapsed time
// since the last step reaches getStepTime(level) = 5/(level+5) seconds.
type Game struct {
	tickTime      float64
	time          float64
	width, height int

	lockBoard *Board
	active    *Piece
	pieceX    int
	pieceY    int
	rotation  int
	lastStep  float64

	gameOver bool
}

// NewGame builds a width×height game whose clock advances by tickTime seconds
// per ClockTick (Java constructor).
func NewGame(width, height int, tickTime float64) *Game {
	return &Game{
		width:     width,
		height:    height,
		tickTime:  tickTime,
		lockBoard: NewBoard(width, height),
	}
}

// getStepTime returns the gravity interval in seconds for a level
// (Java getStepTime): 5/(level+5). Level 0 is one second per row.
func getStepTime(level int) float64 {
	return 5.0 / (float64(level) + 5.0)
}

// ClockTick advances the game by one frame (Java clockTick): it applies gravity
// when due (stepping the piece down or locking it), clears full rows, and spawns
// a new piece when there is none, ending the game if the spawn does not fit.
func (g *Game) ClockTick() {
	if g.gameOver {
		return
	}
	g.time += g.tickTime
	if g.time-g.lastStep >= getStepTime(g.lockBoard.Level()) {
		g.lastStep = g.time
		if g.lockBoard.CheckPiece(g.active, g.rotation, g.pieceX, g.pieceY-1) {
			g.pieceY--
		} else {
			g.finalizePiece()
		}
	}
	g.lockBoard = g.lockBoard.ClearRows()
	if g.active == nil {
		if !g.addNewPiece() {
			g.gameOver = true
		}
	}
}

// addNewPiece spawns a random piece at the top (Java addNewPiece). It returns
// false when the spawn position is already blocked (top-out).
func (g *Game) addNewPiece() bool {
	g.lastStep = g.time
	g.active = GetRandom()
	g.pieceX = 4
	g.pieceY = g.height - 1
	g.rotation = 0
	return g.lockBoard.CheckPiece(g.active, g.rotation, g.pieceX, g.pieceY)
}

// DisplayBoard returns the board to render: the locked board with the active
// piece overlaid, or just the locked board once the game is over
// (Java getDisplayBoard).
func (g *Game) DisplayBoard() *Board {
	if g.gameOver {
		return g.lockBoard
	}
	return g.lockBoard.AddPiece(g.active, g.rotation, g.pieceX, g.pieceY)
}

// LockedBoard returns the locked board without the active piece.
func (g *Game) LockedBoard() *Board { return g.lockBoard }

// MoveLeft shifts the active piece one column left if it fits (Java moveLeft).
func (g *Game) MoveLeft() bool {
	if g.lockBoard.CheckPiece(g.active, g.rotation, g.pieceX-1, g.pieceY) {
		g.pieceX--
		return true
	}
	return false
}

// MoveRight shifts the active piece one column right if it fits (Java moveRight).
func (g *Game) MoveRight() bool {
	if g.lockBoard.CheckPiece(g.active, g.rotation, g.pieceX+1, g.pieceY) {
		g.pieceX++
		return true
	}
	return false
}

// DropPiece hard-drops the active piece to the floor and locks it
// (Java dropPiece). In the original, the 'D' button is a hard drop.
func (g *Game) DropPiece() {
	if g.active == nil {
		return
	}
	for g.lockBoard.CheckPiece(g.active, g.rotation, g.pieceX, g.pieceY-1) {
		g.pieceY--
	}
	g.finalizePiece()
}

// finalizePiece locks the active piece into the board (Java finalizePiece).
func (g *Game) finalizePiece() {
	if g.active == nil {
		return
	}
	g.lockBoard = g.lockBoard.AddPiece(g.active, g.rotation, g.pieceX, g.pieceY)
	g.active = nil
}

// RotatePiece rotates the active piece clockwise if it fits (Java rotatePiece).
// There are no wall kicks: a rotation that does not fit simply fails.
func (g *Game) RotatePiece() bool {
	if g.lockBoard.CheckPiece(g.active, g.rotation+1, g.pieceX, g.pieceY) {
		g.rotation++
		return true
	}
	return false
}

// IsGameOver reports whether the game has ended (Java isGameOver).
func (g *Game) IsGameOver() bool { return g.gameOver }

// Time returns the elapsed logical time in seconds (Java getTime).
func (g *Game) Time() float64 { return g.time }

// Active returns the current active piece (nil between lock and next spawn),
// with its position and rotation. It has no Java counterpart; the renderer uses
// it to tint the falling piece.
func (g *Game) Active() (p *Piece, x, y, rot int) {
	return g.active, g.pieceX, g.pieceY, g.rotation
}
