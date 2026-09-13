package mitris

import "math"

// Green Building display geometry: 9 windows wide, 17 tall (153 pixels), driven
// at 15 fps in 24-bit color in the original hack.
const (
	GBWidth  = 9
	GBHeight = 17
)

// LevelColor ports Piece.getColor(level, time): the base color at level 0, a
// progressive desaturation for levels 1–4, and an animated hue rotation for
// levels 5+. This affects only how filled cells are tinted for display; it does
// not touch game logic.
//
// The HSB<->RGB conversions match java.awt.Color's algorithm (HSB == HSV).
// Saturation and brightness are clamped to [0,1], a small, display-only
// deviation from the original's unclamped float arithmetic.
func (p *Piece) LevelColor(level int, time float64) RGB {
	level %= 10
	if level == 0 {
		return p.color
	}
	h, s, b := rgbToHSB(p.color)
	if level < 5 {
		s -= 0.2 * float64(level)
		return hsbToRGB(h, clamp01(s), b)
	}
	level -= 2
	delta := math.Mod(time*float64(level-1)/7, 1)
	if level%2 == 0 {
		h += delta
	} else {
		h += 1 - delta
	}
	h = math.Mod(h, 1)
	return hsbToRGB(h, clamp01(s), b)
}

func clamp01(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

// rgbToHSB mirrors java.awt.Color.RGBtoHSB.
func rgbToHSB(c RGB) (h, s, b float64) {
	r, g, bl := int(c.R), int(c.G), int(c.B)
	cmax := r
	if g > cmax {
		cmax = g
	}
	if bl > cmax {
		cmax = bl
	}
	cmin := r
	if g < cmin {
		cmin = g
	}
	if bl < cmin {
		cmin = bl
	}
	b = float64(cmax) / 255
	if cmax != 0 {
		s = float64(cmax-cmin) / float64(cmax)
	}
	if s != 0 {
		rc := float64(cmax-r) / float64(cmax-cmin)
		gc := float64(cmax-g) / float64(cmax-cmin)
		bc := float64(cmax-bl) / float64(cmax-cmin)
		switch cmax {
		case r:
			h = bc - gc
		case g:
			h = 2 + rc - bc
		default:
			h = 4 + gc - rc
		}
		h /= 6
		if h < 0 {
			h++
		}
	}
	return h, s, b
}

// hsbToRGB mirrors java.awt.Color.HSBtoRGB.
func hsbToRGB(h, s, v float64) RGB {
	if s == 0 {
		n := uint8(v*255 + 0.5)
		return RGB{n, n, n}
	}
	hh := (h - math.Floor(h)) * 6
	f := hh - math.Floor(hh)
	p := v * (1 - s)
	q := v * (1 - s*f)
	t := v * (1 - s*(1-f))
	var r, g, b float64
	switch int(hh) {
	case 0:
		r, g, b = v, t, p
	case 1:
		r, g, b = q, v, p
	case 2:
		r, g, b = p, v, t
	case 3:
		r, g, b = p, q, v
	case 4:
		r, g, b = t, p, v
	default:
		r, g, b = v, p, q
	}
	return RGB{uint8(r*255 + 0.5), uint8(g*255 + 0.5), uint8(b*255 + 0.5)}
}
