package display

import "sort"

// Profile is a display device preset from capabilities.json (the `d=` query
// parameter on the wal.sh page). Only the fields the TUI needs are kept.
type Profile struct {
	Name    string
	W, H    int
	Palette string  // key into Palettes
	Aspect  float64 // cell width / cell height as seen by the viewer
	Gap     float64 // gap between cells, as a fraction of a cell
	FPS     int
	Levels  int
	Mono    bool
	Note    string
}

// Colors returns the profile's palette entries.
func (p Profile) Colors() []Color { return Palettes[p.Palette] }

// Profiles are the configured displays from capabilities.json v0.2.1.
var Profiles = map[string]Profile{
	"green-building": {"green-building", 9, 17, "cga", 1.5, 0.35, 30, 16, false, "MIT Green Building (Building 54), 153 windows"},
	"tetris":         {"tetris", 10, 20, "cga", 1.0, 0.12, 30, 16, false, "standard Tetris field"},
	"ws2812":         {"ws2812", 16, 16, "cga", 1.0, 0.30, 30, 16, false, "ESP32 + 16x16 LED matrix"},
	"hub75":          {"hub75", 64, 32, "cga", 1.0, 0.15, 60, 16, false, "ESP32 + 64x32 HUB75 panel"},
	"gameboy":        {"gameboy", 10, 18, "gb", 1.0, 0.0, 30, 4, false, "Game Boy field"},
	"c64":            {"c64", 10, 20, "c64", 1.0, 0.12, 30, 16, false, "C64 40x25 screen field"},
	"blinkenlights":  {"blinkenlights", 18, 8, "mono", 1.6, 0.30, 30, 2, true, "Haus des Lehrers, Berlin, 2001"},
	"arcade":         {"arcade", 20, 26, "grey8", 1.3, 0.30, 30, 8, true, "Bibliotheque nationale de France, 2002"},
	"cga40":          {"cga40", 40, 25, "cga", 1.2, 0.0, 30, 16, false, "IBM PC CGA 40-column text mode"},
	"trs80":          {"trs80", 10, 12, "mono", 1.0, 0.12, 30, 2, true, "TRS-80 text-mode field"},
	"remote":         {"remote", 9, 17, "cga", 1.5, 0.35, 30, 16, false, "frame sink; green-building geometry"},
}

// ProfileNames returns the configured display names, sorted.
func ProfileNames() []string {
	names := make([]string, 0, len(Profiles))
	for n := range Profiles {
		names = append(names, n)
	}
	sort.Strings(names)
	return names
}
