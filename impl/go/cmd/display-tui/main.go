// Command display-tui is a terminal front-end that simulates a
// wal.sh/tools/display sink and lets you send it commands, the way the web page
// at https://wal.sh/tools/display/?d=green-building&demo=tetris does.
//
// It acts as a display *source*: it takes the per-display lease and sends
// pal16/hex frames to an in-process, simulated sink rendered in the terminal.
// It NEVER connects to the live relay (wss://wal.sh/tools/display/ws) — that is
// forbidden from here (docs/PROTOCOL.md §5.4).
//
// The `tetris` demo drives the ported MITris engine (impl/go/mitris) and maps
// its RGB frames onto the display's 16-color palette, the same source-side
// adaptation PROTOCOL.md §5.4 describes for a 9×17 green-building display.
package main

import (
	"bufio"
	"flag"
	"fmt"
	"math"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"mitris/display"
	"mitris/mitris"
)

const sourceName = "tui"

const (
	modeCommand = iota
	modeTetris
	modeLife
)

type termSink struct{ latest *display.Frame }

func (s *termSink) Show(f *display.Frame) { s.latest = f.Clone() }

type app struct {
	prof  display.Profile
	sink  *termSink
	relay *display.Relay

	mode  int
	input string
	log   []string

	game     *mitris.Game
	gameTime float64
	life     *lifeState

	quit bool
}

func main() {
	dName := flag.String("d", "green-building", "display profile (see `profiles`)")
	demo := flag.String("demo", "", "start a demo immediately: tetris | life")
	flag.Parse()

	prof, ok := display.Profiles[*dName]
	if !ok {
		fmt.Fprintf(os.Stderr, "unknown display %q; try one of: %s\n", *dName, strings.Join(display.ProfileNames(), ", "))
		os.Exit(1)
	}

	restore, err := makeRaw()
	if err != nil {
		fmt.Fprintln(os.Stderr, "cannot set raw mode:", err)
		os.Exit(1)
	}
	cleanup := func() { fmt.Print(showCur, reset, "\r\n"); restore() }
	defer cleanup()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	go func() { <-sig; cleanup(); os.Exit(0) }()

	a := &app{}
	a.setProfile(prof)
	a.logf("simulated %s display — type `help`, or `demo tetris`. NOT connected to the live relay.", prof.Name)

	switch *demo {
	case "tetris":
		a.startTetris()
	case "life":
		a.startLife()
	}

	keys := make(chan keyEvent, 32)
	go readKeys(keys)

	fmt.Print(hideCur, clearScreen)
	ticker := time.NewTicker(time.Second / time.Duration(prof.FPS))
	defer ticker.Stop()

	for !a.quit {
		select {
		case k := <-keys:
			a.onKey(k)
		case <-ticker.C:
			a.step()
			a.render()
		}
	}
	cleanup()
}

// step advances the active demo one frame and sends it to the display.
func (a *app) step() {
	switch a.mode {
	case modeTetris:
		a.game.ClockTick()
		a.gameTime = a.game.Time()
		a.sendFrame(a.tetrisFrame())
		if a.game.IsGameOver() {
			a.logf("game over — cleared %d lines in %.1fs", a.game.DisplayBoard().NumCleared(), a.game.Time())
			a.mode = modeCommand
		}
	case modeLife:
		a.life.step()
		a.sendFrame(a.life.frame(a.prof))
	}
}

// onKey routes a key to the active mode.
func (a *app) onKey(k keyEvent) {
	if k.special == keyCtrlC {
		a.quit = true
		return
	}
	if a.mode == modeTetris {
		switch k.special {
		case keyEsc:
			a.mode = modeCommand
			a.logf("stopped demo")
		case keyLeft:
			a.game.MoveLeft()
		case keyRight:
			a.game.MoveRight()
		case keyUp:
			a.game.RotatePiece()
		case keyDown:
			a.game.DropPiece()
		default:
			switch k.r {
			case 'a':
				a.game.MoveLeft()
			case 'd':
				a.game.MoveRight()
			case 'w':
				a.game.RotatePiece()
			case 's', ' ':
				a.game.DropPiece()
			case 'q':
				a.mode = modeCommand
			}
		}
		return
	}
	if a.mode == modeLife {
		if k.special == keyEsc || k.r == 'q' {
			a.mode = modeCommand
			a.logf("stopped demo")
		}
		return
	}
	// Command mode: line editing.
	switch k.special {
	case keyEnter:
		line := strings.TrimSpace(a.input)
		a.input = ""
		if line != "" {
			a.runCommand(line)
		}
	case keyBackspace:
		if n := len(a.input); n > 0 {
			a.input = a.input[:n-1]
		}
	default:
		if k.r != 0 {
			a.input += string(k.r)
		}
	}
}

// runCommand parses and executes one command line.
func (a *app) runCommand(line string) {
	f := strings.Fields(line)
	cmd, args := f[0], f[1:]
	switch cmd {
	case "help", "?":
		a.logf("cmds: d <profile> | profiles | reserve <name> [ttl] | renew | release |")
		a.logf("      fill <0-15> | pixel <x> <y> <0-15> | pattern <bars|checker|ramp|border> |")
		a.logf("      clear | hex | demo <tetris|life> | quit")
	case "profiles":
		a.logf("displays: %s", strings.Join(display.ProfileNames(), ", "))
	case "d":
		if len(args) != 1 {
			a.logf("usage: d <profile>")
			return
		}
		p, ok := display.Profiles[args[0]]
		if !ok {
			a.logf("unknown display %q", args[0])
			return
		}
		a.setProfile(p)
		a.logf("display = %s (%dx%d, %s, %dfps)", p.Name, p.W, p.H, p.Palette, p.FPS)
	case "reserve":
		if len(args) < 1 {
			a.logf("usage: reserve <name> [ttl]")
			return
		}
		ttl := 900
		if len(args) >= 2 {
			ttl = atoiOr(args[1], 900)
		}
		exp, err := a.relay.Reserve(args[0], ttl)
		if err != nil {
			a.logf("reserve: %v", err)
			return
		}
		a.logf("granted to %q, ttl %ds (expires %s)", args[0], ttl, exp.Format("15:04:05"))
	case "renew":
		if _, err := a.relay.Renew(a.relay.Holder(), 900); err != nil {
			a.logf("renew: %v", err)
			return
		}
		a.logf("renewed, ttl 900s")
	case "release":
		h := a.relay.Holder()
		if err := a.relay.Release(h); err != nil {
			a.logf("release: %v", err)
			return
		}
		a.logf("released %q", h)
	case "fill":
		if len(args) != 1 {
			a.logf("usage: fill <0-15>")
			return
		}
		fr := display.NewFrame(a.prof.W, a.prof.H)
		fr.Fill(byte(atoiOr(args[0], 0)))
		a.sendFrame(fr)
	case "clear":
		a.sendFrame(display.NewFrame(a.prof.W, a.prof.H))
	case "pixel":
		if len(args) != 3 {
			a.logf("usage: pixel <x> <y> <0-15>")
			return
		}
		fr := a.currentFrame()
		fr.Set(atoiOr(args[0], 0), atoiOr(args[1], 0), byte(atoiOr(args[2], 0)))
		a.sendFrame(fr)
	case "pattern":
		if len(args) != 1 {
			a.logf("usage: pattern <bars|checker|ramp|border>")
			return
		}
		a.sendFrame(a.pattern(args[0]))
	case "hex":
		a.logf("pal16 frame as hex (%d rows of %d):", a.prof.H, a.prof.W)
		for _, row := range strings.Split(strings.TrimRight(a.currentFrame().Hex(), "\n"), "\n") {
			a.logf("  %s", row)
		}
	case "demo":
		if len(args) != 1 {
			a.logf("usage: demo <tetris|life>")
			return
		}
		switch args[0] {
		case "tetris":
			a.startTetris()
		case "life":
			a.startLife()
		default:
			a.logf("unknown demo %q", args[0])
		}
	case "quit", "q", "exit":
		a.quit = true
	default:
		a.logf("unknown command %q (try `help`)", cmd)
	}
}

// setProfile switches to a new display profile with a fresh sink and relay.
func (a *app) setProfile(p display.Profile) {
	a.prof = p
	a.sink = &termSink{}
	a.relay = display.NewRelay(p, a.sink)
	a.mode = modeCommand
}

// ensureLease auto-reserves the display as the TUI source if it is free, so
// simple commands "just work" while still exercising the lease.
func (a *app) ensureLease() bool {
	if a.relay.Holder() == "" {
		if _, err := a.relay.Reserve(sourceName, 900); err != nil {
			a.logf("cannot send: %v", err)
			return false
		}
		a.logf("auto-reserved as %q", sourceName)
	}
	return true
}

// sendFrame sends a frame through the relay to the sink, as the TUI source.
func (a *app) sendFrame(fr *display.Frame) {
	if !a.ensureLease() {
		return
	}
	if err := a.relay.Send(a.relay.Holder(), fr); err != nil {
		a.logf("send: %v", err)
	}
}

// currentFrame returns the last shown frame, or a fresh blank one.
func (a *app) currentFrame() *display.Frame {
	if a.sink.latest != nil {
		return a.sink.latest.Clone()
	}
	return display.NewFrame(a.prof.W, a.prof.H)
}

func (a *app) startTetris() {
	a.game = mitris.NewGame(a.prof.W, a.prof.H, 1.0/float64(a.prof.FPS))
	a.gameTime = 0
	a.ensureLease()
	a.mode = modeTetris
	a.logf("demo tetris — arrows/WASD play, Esc stops")
}

func (a *app) startLife() {
	a.life = newLife(a.prof.W, a.prof.H)
	a.ensureLease()
	a.mode = modeLife
	a.logf("demo life — Esc stops")
}

// tetrisFrame maps the MITris board onto a pal16 frame: filled cells are
// quantized to the nearest palette entry, empty cells are index 0. The board is
// bottom-origin, so display row r (0 at top) is board y = H-1-r.
func (a *app) tetrisFrame() *display.Frame {
	board := a.game.DisplayBoard()
	pal := a.prof.Colors()
	fr := display.NewFrame(a.prof.W, a.prof.H)
	level := board.Level()
	for r := 0; r < a.prof.H; r++ {
		y := a.prof.H - 1 - r
		for x := 0; x < a.prof.W; x++ {
			p := board.At(x, y)
			if p == nil {
				continue // index 0
			}
			c := p.LevelColor(level, a.gameTime)
			fr.Set(x, r, display.Nearest(pal, display.Color{R: c.R, G: c.G, B: c.B}))
		}
	}
	return fr
}

// pattern builds a named test frame.
func (a *app) pattern(name string) *display.Frame {
	fr := display.NewFrame(a.prof.W, a.prof.H)
	n := len(a.prof.Colors())
	switch name {
	case "bars":
		for x := 0; x < fr.W; x++ {
			idx := byte(1 + x%(n-1))
			for y := 0; y < fr.H; y++ {
				fr.Set(x, y, idx)
			}
		}
	case "checker":
		for y := 0; y < fr.H; y++ {
			for x := 0; x < fr.W; x++ {
				if (x+y)%2 == 0 {
					fr.Set(x, y, byte(n-1))
				}
			}
		}
	case "ramp":
		for y := 0; y < fr.H; y++ {
			for x := 0; x < fr.W; x++ {
				fr.Set(x, y, byte((x+y)%n))
			}
		}
	case "border":
		for x := 0; x < fr.W; x++ {
			fr.Set(x, 0, byte(n-1))
			fr.Set(x, fr.H-1, byte(n-1))
		}
		for y := 0; y < fr.H; y++ {
			fr.Set(0, y, byte(n-1))
			fr.Set(fr.W-1, y, byte(n-1))
		}
	default:
		a.logf("unknown pattern %q", name)
	}
	return fr
}

func (a *app) logf(format string, args ...any) {
	a.log = append(a.log, fmt.Sprintf(format, args...))
	if len(a.log) > 8 {
		a.log = a.log[len(a.log)-8:]
	}
}

// ---- rendering ----

const (
	reset       = "\x1b[0m"
	hideCur     = "\x1b[?25l"
	showCur     = "\x1b[?25h"
	clearScreen = "\x1b[2J"
	home        = "\x1b[H"
	clrEOL      = "\x1b[K"
	clrBelow    = "\x1b[J"
	dim         = "\x1b[2m"
	bold        = "\x1b[1m"
)

// cellW returns how many terminal columns to draw per cell so the on-screen
// aspect approximates the profile's (a terminal char is roughly twice as tall
// as wide, so a cell one row tall needs ~2*aspect columns to look right).
func (a *app) cellW() int {
	w := int(math.Round(2 * a.prof.Aspect))
	if w < 1 {
		w = 1
	}
	return w
}

func (a *app) render() {
	var b strings.Builder
	b.WriteString(home)

	p := a.prof
	holder := a.relay.Holder()
	lease := "free"
	if holder != "" {
		lease = fmt.Sprintf("%s (ttl %ds)", holder, a.relay.TTL())
	}
	writeLine(&b, fmt.Sprintf("%swal.sh display (simulated)%s  d=%s%s%s  %dx%d  %s  %dfps",
		bold, reset, bold, p.Name, reset, p.W, p.H, p.Palette, p.FPS))
	status := fmt.Sprintf("mode=%s  lease=%s  frames=%d", a.modeName(), lease, a.relay.Seq())
	if a.mode == modeTetris {
		bd := a.game.DisplayBoard()
		status += fmt.Sprintf("  lines=%d level=%d", bd.NumCleared(), bd.Level())
	}
	writeLine(&b, dim+status+reset)
	writeLine(&b, "")

	a.renderGrid(&b)

	writeLine(&b, "")
	for _, l := range a.log {
		writeLine(&b, dim+"· "+l+reset)
	}
	writeLine(&b, "")
	if a.mode == modeCommand {
		b.WriteString("> " + a.input + "\x1b[7m \x1b[27m" + clrEOL + "\r\n")
	} else {
		writeLine(&b, dim+"[playing — Esc to stop]"+reset)
	}
	b.WriteString(clrBelow)
	os.Stdout.WriteString(b.String())
}

// renderGrid draws the current sink frame as ANSI truecolor blocks, with a gap
// between columns to evoke the masonry between the building's windows.
func (a *app) renderGrid(b *strings.Builder) {
	fr := a.sink.latest
	if fr == nil {
		fr = display.NewFrame(a.prof.W, a.prof.H)
	}
	pal := a.prof.Colors()
	cw := a.cellW()
	gap := a.prof.Gap > 0.2
	cells := strings.Repeat(" ", cw)
	for y := 0; y < fr.H; y++ {
		b.WriteString("  ") // left margin
		for x := 0; x < fr.W; x++ {
			c := pal[fr.At(x, y)]
			fmt.Fprintf(b, "\x1b[48;2;%d;%d;%dm%s", c.R, c.G, c.B, cells)
			if gap && x < fr.W-1 {
				b.WriteString(reset + " ")
			}
		}
		b.WriteString(reset + clrEOL + "\r\n")
	}
}

func writeLine(b *strings.Builder, s string) {
	b.WriteString(s)
	b.WriteString(clrEOL)
	b.WriteString("\r\n")
}

func (a *app) modeName() string {
	switch a.mode {
	case modeTetris:
		return "tetris"
	case modeLife:
		return "life"
	default:
		return "command"
	}
}

func atoiOr(s string, def int) int {
	if v, err := strconv.Atoi(s); err == nil {
		return v
	}
	return def
}

// ---- Conway's Life demo ----

type lifeState struct {
	w, h int
	cur  []bool
	age  []byte
	rng  uint32
}

func newLife(w, h int) *lifeState {
	l := &lifeState{w: w, h: h, cur: make([]bool, w*h), age: make([]byte, w*h), rng: 0x1234abcd}
	for i := range l.cur {
		l.cur[i] = l.next()%5 == 0
	}
	return l
}

func (l *lifeState) next() uint32 {
	l.rng ^= l.rng << 13
	l.rng ^= l.rng >> 17
	l.rng ^= l.rng << 5
	return l.rng
}

func (l *lifeState) at(x, y int) bool {
	x = (x + l.w) % l.w
	y = (y + l.h) % l.h
	return l.cur[y*l.w+x]
}

func (l *lifeState) step() {
	nxt := make([]bool, l.w*l.h)
	for y := 0; y < l.h; y++ {
		for x := 0; x < l.w; x++ {
			n := 0
			for dy := -1; dy <= 1; dy++ {
				for dx := -1; dx <= 1; dx++ {
					if (dx != 0 || dy != 0) && l.at(x+dx, y+dy) {
						n++
					}
				}
			}
			i := y*l.w + x
			alive := l.cur[i]
			nxt[i] = n == 3 || (alive && n == 2)
			if nxt[i] {
				if alive && l.age[i] < 255 {
					l.age[i]++
				} else if !alive {
					l.age[i] = 1
				}
			} else {
				l.age[i] = 0
			}
		}
	}
	l.cur = nxt
}

// frame colors live cells by age using a few palette indices for a pleasing ramp.
func (l *lifeState) frame(p display.Profile) *display.Frame {
	fr := display.NewFrame(l.w, l.h)
	n := len(p.Colors())
	for i, alive := range l.cur {
		if !alive {
			continue
		}
		idx := byte(9 + int(l.age[i]))
		if int(idx) >= n {
			idx = byte(n - 1)
		}
		fr.Cells[i] = idx
	}
	return fr
}

// ---- terminal input ----

type special int

const (
	keyNone special = iota
	keyUp
	keyDown
	keyLeft
	keyRight
	keyEnter
	keyBackspace
	keyEsc
	keyCtrlC
)

type keyEvent struct {
	special special
	r       rune
}

func readKeys(out chan<- keyEvent) {
	r := bufio.NewReader(os.Stdin)
	for {
		c, err := r.ReadByte()
		if err != nil {
			return
		}
		switch {
		case c == 0x1b:
			// Might be an arrow (ESC [ A/B/C/D). Peek to distinguish a bare Esc.
			if next, err := r.Peek(1); err == nil && next[0] == '[' {
				r.ReadByte() // consume '['
				c3, err := r.ReadByte()
				if err != nil {
					return
				}
				switch c3 {
				case 'A':
					out <- keyEvent{special: keyUp}
				case 'B':
					out <- keyEvent{special: keyDown}
				case 'C':
					out <- keyEvent{special: keyRight}
				case 'D':
					out <- keyEvent{special: keyLeft}
				}
			} else {
				out <- keyEvent{special: keyEsc}
			}
		case c == '\r' || c == '\n':
			out <- keyEvent{special: keyEnter}
		case c == 0x7f || c == 0x08:
			out <- keyEvent{special: keyBackspace}
		case c == 0x03:
			out <- keyEvent{special: keyCtrlC}
		case c >= 0x20 && c < 0x7f:
			out <- keyEvent{r: rune(c)}
		}
	}
}

// makeRaw switches the terminal to raw, no-echo mode via stty.
func makeRaw() (restore func(), err error) {
	saved, err := sttyState()
	if err != nil {
		return nil, err
	}
	if err := stty("-echo", "-icanon", "min", "1", "time", "0"); err != nil {
		return nil, err
	}
	return func() { _ = sttyRestore(saved) }, nil
}

func sttyState() (string, error) {
	cmd := exec.Command("stty", "-g")
	cmd.Stdin = os.Stdin
	out, err := cmd.Output()
	return string(out), err
}

func sttyRestore(state string) error {
	state = strings.TrimRight(state, "\r\n")
	return stty(state)
}

func stty(args ...string) error {
	cmd := exec.Command("stty", args...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}
