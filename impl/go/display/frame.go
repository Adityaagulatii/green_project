package display

import (
	"fmt"
	"strings"
)

// Frame is a pal16 frame: w*h palette indices, row-major, row 0 at the top
// (capabilities.json `frame`). Each cell holds a value 0..15 selecting an entry
// of the display's palette.
type Frame struct {
	W, H  int
	Cells []byte
}

// NewFrame returns an all-zero (palette index 0) frame (the sink's makeframe).
func NewFrame(w, h int) *Frame {
	return &Frame{W: w, H: h, Cells: make([]byte, w*h)}
}

// At returns the palette index at (x,y), with (0,0) at the top-left.
func (f *Frame) At(x, y int) byte { return f.Cells[y*f.W+x] }

// Set writes a palette index at (x,y). Out-of-range coordinates are ignored.
func (f *Frame) Set(x, y int, idx byte) {
	if x < 0 || y < 0 || x >= f.W || y >= f.H {
		return
	}
	f.Cells[y*f.W+x] = idx
}

// Fill sets every cell to idx.
func (f *Frame) Fill(idx byte) {
	for i := range f.Cells {
		f.Cells[i] = idx
	}
}

// Clone returns a deep copy.
func (f *Frame) Clone() *Frame {
	n := NewFrame(f.W, f.H)
	copy(n.Cells, f.Cells)
	return n
}

// Hex encodes the frame as h lines of w hex digits, LF-terminated — the `hex`
// text-message form from capabilities.json. Indices are 0..15, one digit each.
func (f *Frame) Hex() string {
	var b strings.Builder
	const digits = "0123456789abcdef"
	for y := 0; y < f.H; y++ {
		for x := 0; x < f.W; x++ {
			b.WriteByte(digits[f.At(x, y)&0x0f])
		}
		b.WriteByte('\n')
	}
	return b.String()
}

// ParseHex decodes the hex form into a w*h frame.
func ParseHex(s string, w, h int) (*Frame, error) {
	lines := strings.Split(strings.TrimRight(s, "\n"), "\n")
	if len(lines) != h {
		return nil, fmt.Errorf("hex frame: got %d rows, want %d", len(lines), h)
	}
	f := NewFrame(w, h)
	for y, line := range lines {
		if len(line) != w {
			return nil, fmt.Errorf("hex frame row %d: got %d digits, want %d", y, len(line), w)
		}
		for x := 0; x < w; x++ {
			v, err := hexDigit(line[x])
			if err != nil {
				return nil, fmt.Errorf("hex frame (%d,%d): %w", x, y, err)
			}
			f.Set(x, y, v)
		}
	}
	return f, nil
}

// Pal16 returns the binary pal16 payload: one byte per cell, row-major. If seq
// is non-negative, a 2-byte big-endian sequence prefix (seq mod 65536) is
// prepended, as the spec allows.
func (f *Frame) Pal16(seq int) []byte {
	if seq < 0 {
		return append([]byte(nil), f.Cells...)
	}
	s := uint16(seq % 65536)
	out := make([]byte, 0, len(f.Cells)+2)
	out = append(out, byte(s>>8), byte(s))
	return append(out, f.Cells...)
}

func hexDigit(c byte) (byte, error) {
	switch {
	case c >= '0' && c <= '9':
		return c - '0', nil
	case c >= 'a' && c <= 'f':
		return c - 'a' + 10, nil
	case c >= 'A' && c <= 'F':
		return c - 'A' + 10, nil
	}
	return 0, fmt.Errorf("not a hex digit: %q", string(c))
}
