package display

import (
	"testing"
	"time"
)

func TestPalettesLoaded(t *testing.T) {
	if len(Palettes["cga"]) != 16 {
		t.Fatalf("cga palette has %d entries, want 16", len(Palettes["cga"]))
	}
	if got := Palettes["cga"][15]; got != (Color{0xFF, 0xFF, 0xFF}) {
		t.Errorf("cga[15]=%+v want white", got)
	}
	if got := Palettes["cga"][0]; got != (Color{0, 0, 0}) {
		t.Errorf("cga[0]=%+v want black", got)
	}
}

func TestNearest(t *testing.T) {
	cga := Palettes["cga"]
	// Pure red (255,0,0): #AA0000 (index 4) is nearer than #FF5555 (index 12),
	// because 85^2 < 85^2+85^2.
	if idx := Nearest(cga, Color{255, 0, 0}); idx != 4 {
		t.Errorf("nearest red = %d want 4 (#AA0000)", idx)
	}
	// Exact black is index 0.
	if idx := Nearest(cga, Color{0, 0, 0}); idx != 0 {
		t.Errorf("nearest black = %d want 0", idx)
	}
	// Exact white is index 15.
	if idx := Nearest(cga, Color{255, 255, 255}); idx != 15 {
		t.Errorf("nearest white = %d want 15", idx)
	}
}

func TestFrameHexRoundTrip(t *testing.T) {
	f := NewFrame(9, 17)
	f.Set(0, 0, 1)
	f.Set(8, 16, 15)
	f.Set(4, 8, 10)
	hex := f.Hex()
	g, err := ParseHex(hex, 9, 17)
	if err != nil {
		t.Fatal(err)
	}
	for i := range f.Cells {
		if f.Cells[i] != g.Cells[i] {
			t.Fatalf("round-trip differs at cell %d: %d vs %d", i, f.Cells[i], g.Cells[i])
		}
	}
	// A green-building frame is 17 lines of 9 digits.
	if got := len(hex); got != 17*(9+1) {
		t.Errorf("hex length %d want %d", got, 17*10)
	}
}

func TestPal16SeqPrefix(t *testing.T) {
	f := NewFrame(9, 17)
	if got := len(f.Pal16(-1)); got != 153 {
		t.Errorf("pal16 no-prefix length %d want 153", got)
	}
	b := f.Pal16(0x0102)
	if len(b) != 155 || b[0] != 0x01 || b[1] != 0x02 {
		t.Errorf("pal16 seq prefix wrong: len=%d head=%#v", len(b), b[:2])
	}
}

// fakeSink records the last frame shown.
type fakeSink struct{ last *Frame }

func (s *fakeSink) Show(f *Frame) { s.last = f }

func TestRelayLease(t *testing.T) {
	sink := &fakeSink{}
	r := NewRelay(Profiles["green-building"], sink)
	// Controllable clock.
	base := time.Unix(1_000_000, 0)
	clk := base
	r.now = func() time.Time { return clk }

	if _, err := r.Reserve("alice", 60); err != nil {
		t.Fatalf("alice reserve: %v", err)
	}
	if r.Holder() != "alice" {
		t.Fatalf("holder=%q want alice", r.Holder())
	}
	// A second holder is busy while the lease is live.
	if _, err := r.Reserve("bob", 60); err == nil {
		t.Fatal("bob should be refused (busy)")
	}
	// Alice can send a correctly-sized frame; wrong size is rejected.
	if err := r.Send("alice", NewFrame(9, 17)); err != nil {
		t.Fatalf("alice send: %v", err)
	}
	if sink.last == nil {
		t.Fatal("sink did not receive the frame")
	}
	if err := r.Send("alice", NewFrame(10, 20)); err == nil {
		t.Fatal("wrong-size frame should be rejected")
	}
	// bob (non-holder) cannot send.
	if err := r.Send("bob", NewFrame(9, 17)); err == nil {
		t.Fatal("bob send should be refused")
	}
	// After release, the lease is free and bob can take it.
	if err := r.Release("alice"); err != nil {
		t.Fatalf("alice release: %v", err)
	}
	if _, err := r.Reserve("bob", 30); err != nil {
		t.Fatalf("bob reserve after release: %v", err)
	}
	// Lease lapses after TTL: advance the clock past bob's 30s.
	clk = base.Add(31 * time.Second)
	if r.Holder() != "" {
		t.Errorf("holder=%q want empty after TTL lapse", r.Holder())
	}
}
