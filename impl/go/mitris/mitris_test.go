package mitris

import "testing"

// pieceByName returns the canonical piece for a letter.
func pieceByName(t *testing.T, name byte) *Piece {
	t.Helper()
	for _, p := range Pieces() {
		if p.Name == name {
			return p
		}
	}
	t.Fatalf("no piece %c", name)
	return nil
}

// TestPieceGeometry checks the offsets and rotation counts match Piece.java.
func TestPieceGeometry(t *testing.T) {
	want := map[byte]struct {
		maxRot int
		off    [4][2]int
	}{
		'L': {4, [4][2]int{{-1, 0}, {0, 0}, {1, 0}, {1, 1}}},
		'S': {2, [4][2]int{{0, 0}, {1, 0}, {-1, 1}, {0, 1}}},
		'Z': {2, [4][2]int{{-1, 0}, {0, 0}, {0, 1}, {1, 1}}},
		'J': {4, [4][2]int{{-1, 0}, {0, 0}, {1, 0}, {-1, 1}}},
		'I': {2, [4][2]int{{-2, 0}, {-1, 0}, {0, 0}, {1, 0}}},
		'T': {4, [4][2]int{{-1, 0}, {0, 0}, {1, 0}, {0, 1}}},
		'O': {1, [4][2]int{{-1, 0}, {0, 0}, {-1, 1}, {0, 1}}},
	}
	for name, w := range want {
		p := pieceByName(t, name)
		if p.MaxRot != w.maxRot {
			t.Errorf("%c MaxRot=%d want %d", name, p.MaxRot, w.maxRot)
		}
		for i := 0; i < 4; i++ {
			if p.X(i) != w.off[i][0] || p.Y(i) != w.off[i][1] {
				t.Errorf("%c comp %d = (%d,%d) want (%d,%d)", name, i, p.X(i), p.Y(i), w.off[i][0], w.off[i][1])
			}
		}
	}
}

// TestORotationInvariant verifies the O piece has a single rotation state:
// rotating it (mod MaxRot==1) never changes the occupied cells.
func TestORotationInvariant(t *testing.T) {
	o := pieceByName(t, 'O')
	b := NewBoard(GBWidth, GBHeight)
	base := b.AddPiece(o, 0, 4, 4)
	for rot := 0; rot < 4; rot++ {
		got := b.AddPiece(o, rot, 4, 4)
		if got == nil {
			t.Fatalf("O rot %d did not place", rot)
		}
		for x := 0; x < GBWidth; x++ {
			for y := 0; y < GBHeight; y++ {
				if (base.At(x, y) != nil) != (got.At(x, y) != nil) {
					t.Fatalf("O rot %d differs at (%d,%d)", rot, x, y)
				}
			}
		}
	}
}

// TestRotationTransform checks the four-case transform on the T piece: its
// (0,1) arm should point up, right, down, left across rotations 0..3.
func TestRotationTransform(t *testing.T) {
	tp := pieceByName(t, 'T')
	b := NewBoard(GBWidth, GBHeight)
	// The 3rd component of T is the arm at offset (0,1). Track where it lands
	// relative to the center (4,8).
	arm := 3
	cx, cy := 4, 8
	// rot: expected (dx,dy) of the arm from the four-case transform.
	cases := []struct{ dx, dy int }{{0, 1}, {1, 0}, {0, -1}, {-1, 0}}
	for rot, c := range cases {
		got := b.AddPiece(tp, rot, cx, cy)
		if got == nil {
			t.Fatalf("T rot %d did not place", rot)
		}
		if got.At(cx+c.dx, cy+c.dy) == nil {
			t.Errorf("T rot %d: arm not at (%d,%d)", rot, cx+c.dx, cy+c.dy)
		}
		_ = arm
	}
}

// TestOutOfBoundsAndOverlap covers the bounds/overlap rules of AddPiece.
func TestOutOfBoundsAndOverlap(t *testing.T) {
	i := pieceByName(t, 'I')
	b := NewBoard(GBWidth, GBHeight)
	// I at x=0 rot 0 has a cell at x-2 = -2 -> out of bounds.
	if b.CheckPiece(i, 0, 0, 5) {
		t.Error("I at x=0 should be out of bounds (x-2<0)")
	}
	// Placing then re-placing on the same cells must overlap-fail.
	locked := b.AddPiece(i, 0, 4, 5)
	if locked == nil {
		t.Fatal("I should place at x=4")
	}
	if locked.CheckPiece(i, 0, 4, 5) {
		t.Error("overlapping placement should fail")
	}
}

// TestProtrudeTop verifies cells above the top row are legal but not stored.
func TestProtrudeTop(t *testing.T) {
	tp := pieceByName(t, 'T')
	b := NewBoard(GBWidth, GBHeight)
	// Center at the top row; the arm at y+1 = height is above the board.
	got := b.AddPiece(tp, 0, 4, GBHeight-1)
	if got == nil {
		t.Fatal("piece protruding the top should be legal")
	}
	// The three base cells at y=height-1 are stored; the arm is dropped.
	filled := 0
	for x := 0; x < GBWidth; x++ {
		for y := 0; y < GBHeight; y++ {
			if got.At(x, y) != nil {
				filled++
			}
		}
	}
	if filled != 3 {
		t.Errorf("expected 3 stored cells (arm above top dropped), got %d", filled)
	}
}

// TestClearRowsCompacts fills the bottom row and checks it clears and level
// tracks numCleared/4.
func TestClearRowsCompacts(t *testing.T) {
	b := NewBoard(GBWidth, GBHeight)
	o := pieceByName(t, 'O')
	// Fill row 0 completely, leaving a marker on row 2 to confirm it drops.
	for x := 0; x < GBWidth; x++ {
		b.data[x][0] = o
	}
	b.data[3][1] = o // sits directly above the full row; compacts to row 0
	cleared := b.ClearRows()
	if cleared.NumCleared() != 1 {
		t.Fatalf("NumCleared=%d want 1", cleared.NumCleared())
	}
	// After clearing the full row 0, the marker (only non-empty cell in row 1)
	// compacts down to row 0 at its column; every other cell is empty.
	for x := 0; x < GBWidth; x++ {
		if x == 3 {
			continue
		}
		if cleared.At(x, 0) != nil {
			t.Errorf("row 0 col %d should be empty after clear", x)
		}
	}
	if cleared.At(3, 0) == nil {
		t.Error("marker should have compacted down to row 0")
	}
}

// TestGravityCadence checks a level-0 piece steps once per 1.0s of logical time
// (getStepTime(0) = 5/5 = 1.0) at a 30 fps tick.
func TestGravityCadence(t *testing.T) {
	if got := getStepTime(0); got != 1.0 {
		t.Fatalf("getStepTime(0)=%v want 1.0", got)
	}
	// Force a known piece so we can track pieceY.
	old := randSource
	randSource = func(int) int { return 5 } // T
	defer func() { randSource = old }()

	g := NewGame(GBWidth, GBHeight, 1.0/30)
	g.ClockTick() // spawns the first piece at pieceY = height-1
	_, _, y0, _ := g.Active()
	if y0 != GBHeight-1 {
		t.Fatalf("spawn y=%d want %d", y0, GBHeight-1)
	}
	// One gravity step is due after 1.0s of logical time. Because summing
	// 1.0/30 thirty times lands just under 1.0 in float64 (as it does in the
	// Java double original), the step fires on the 31st tick. A second step
	// would need another full second (~30 more ticks), so exactly one step has
	// occurred here.
	for i := 0; i < 31; i++ {
		g.ClockTick()
	}
	_, _, y1, _ := g.Active()
	if y1 != y0-1 {
		t.Errorf("after ~1s piece y=%d want %d", y1, y0-1)
	}
}

// TestHardDropLocks verifies DropPiece falls to the floor and the next tick
// spawns a fresh piece.
func TestHardDropLocks(t *testing.T) {
	old := randSource
	randSource = func(int) int { return 6 } // O
	defer func() { randSource = old }()

	g := NewGame(GBWidth, GBHeight, 1.0/30)
	g.ClockTick()
	g.DropPiece()
	// After a hard drop the active piece is locked (nil) until the next tick.
	if p, _, _, _ := g.Active(); p != nil {
		t.Error("active piece should be nil immediately after hard drop")
	}
	// Something is now on the floor.
	if g.LockedBoard().IsBoardEmpty() {
		t.Error("board should not be empty after a hard drop")
	}
	g.ClockTick() // spawns the next piece
	if p, _, _, _ := g.Active(); p == nil {
		t.Error("a new piece should spawn after lock")
	}
}

// TestFullGameTerminates plays a full game with deterministic pseudo-random
// input and pieces, confirming it reaches game over without panicking or
// looping forever.
func TestFullGameTerminates(t *testing.T) {
	old := randSource
	// A tiny LCG so the piece sequence is deterministic without touching the
	// global rand.
	seed := uint32(12345)
	randSource = func(n int) int {
		seed = seed*1664525 + 1013904223
		return int(seed>>16) % n
	}
	defer func() { randSource = old }()

	g := NewGame(GBWidth, GBHeight, 1.0/30)
	const maxTicks = 2_000_000
	inputs := 0
	for i := 0; i < maxTicks && !g.IsGameOver(); i++ {
		g.ClockTick()
		// Occasionally poke the piece so the stack builds unevenly and tops out.
		switch i % 7 {
		case 0:
			g.MoveLeft()
		case 3:
			g.RotatePiece()
		case 5:
			g.DropPiece()
			inputs++
		}
	}
	if !g.IsGameOver() {
		t.Fatalf("game did not end within %d ticks (dropped %d pieces)", maxTicks, inputs)
	}
	if g.DisplayBoard().NumCleared() < 0 {
		t.Fatal("negative lines cleared")
	}
}

// TestLevelColorBaseAtZero confirms level 0 returns the piece's base color.
func TestLevelColorBaseAtZero(t *testing.T) {
	z := pieceByName(t, 'Z')
	if got := z.LevelColor(0, 0); got != (RGB{255, 0, 0}) {
		t.Errorf("Z level 0 color = %+v want red", got)
	}
}
