// Package display is a dependency-free, in-process model of the
// wal.sh/tools/display protocol (spec v0.2.1): named device profiles, 16-entry
// palettes, pal16/hex frames, and a per-display lease. It lets a terminal TUI
// act as a display *source* against a *simulated* sink, without ever touching
// the live relay (docs/PROTOCOL.md §5.4 forbids connecting to
// wss://wal.sh/tools/display/ws from here).
//
// Palettes and profiles are transcribed from the pinned capabilities.json at
// contrib/displays/contract/wal-sh-display-0.2.1/capabilities.json.
package display

import "fmt"

// Color is a 24-bit RGB palette entry.
type Color struct{ R, G, B uint8 }

// Palettes are the named 16-(or fewer)-entry palettes from capabilities.json.
// A cell's pal16 index selects an entry of the profile's palette; the sink
// never quantizes, it just shows palette[index].
var Palettes = map[string][]Color{
	"cga":   mustHex("#000000", "#0000AA", "#00AA00", "#00AAAA", "#AA0000", "#AA00AA", "#AA5500", "#AAAAAA", "#555555", "#5555FF", "#55FF55", "#55FFFF", "#FF5555", "#FF55FF", "#FFFF55", "#FFFFFF"),
	"c64":   mustHex("#000000", "#FFFFFF", "#880000", "#AAFFEE", "#CC44CC", "#00CC55", "#0000AA", "#EEEE77", "#DD8855", "#664400", "#FF7777", "#333333", "#777777", "#AAFF66", "#0088FF", "#BBBBBB"),
	"gb":    mustHex("#0F380F", "#306230", "#8BAC0F", "#9BBC0F"),
	"mono":  mustHex("#000000", "#FFFFFF"),
	"grey8": mustHex("#000000", "#242424", "#494949", "#6D6D6D", "#929292", "#B6B6B6", "#DBDBDB", "#FFFFFF"),
	"pico8": mustHex("#000000", "#1D2B53", "#7E2553", "#008751", "#AB5236", "#5F574F", "#C2C3C7", "#FFF1E8", "#FF004D", "#FFA300", "#FFEC27", "#00E436", "#29ADFF", "#83769C", "#FF77A8", "#FFCCAA"),
}

func mustHex(hexes ...string) []Color {
	cs := make([]Color, len(hexes))
	for i, h := range hexes {
		c, err := parseHexColor(h)
		if err != nil {
			panic(err)
		}
		cs[i] = c
	}
	return cs
}

// parseHexColor parses "#RRGGBB".
func parseHexColor(s string) (Color, error) {
	if len(s) != 7 || s[0] != '#' {
		return Color{}, fmt.Errorf("bad hex color %q", s)
	}
	var r, g, b uint8
	if _, err := fmt.Sscanf(s[1:], "%02x%02x%02x", &r, &g, &b); err != nil {
		return Color{}, fmt.Errorf("bad hex color %q: %w", s, err)
	}
	return Color{r, g, b}, nil
}

// Nearest returns the index of the palette entry closest to c by squared
// Euclidean RGB distance. This is the source-side quantization
// (capabilities.json NR-QUANT: quantization happens at the source, never the
// sink).
func Nearest(pal []Color, c Color) byte {
	best, bestD := 0, 1<<62
	for i, p := range pal {
		dr := int(p.R) - int(c.R)
		dg := int(p.G) - int(c.G)
		db := int(p.B) - int(c.B)
		d := dr*dr + dg*dg + db*db
		if d < bestD {
			best, bestD = i, d
		}
	}
	return byte(best)
}
