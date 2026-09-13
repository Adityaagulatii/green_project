// Command mitris plays the MITris game in a terminal, faithful to the original
// MIT Green Building "Display 54" hack. The 17×9 building facade is drawn with
// ANSI truecolor blocks; controls mirror the original arcade box's four buttons
// (L, R, U, D), where U rotates and D is a hard drop.
//
// Faithful port of https://github.com/mitrisdev/d54 (BSD 3-Clause).
package main

import (
	"bufio"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"syscall"
	"time"

	"mitris/mitris"
)

const (
	fps     = 30
	cellW   = 2 // characters per cell horizontally, for square-ish pixels
	emptyBG = "\x1b[48;2;20;20;28m"
	reset   = "\x1b[0m"
	hideCur = "\x1b[?25l"
	showCur = "\x1b[?25h"
	clear   = "\x1b[2J"
	home    = "\x1b[H"
)

func main() {
	frameRate := flag.Int("fps", fps, "frames per second (input resolution; gravity is time-based)")
	flag.Parse()

	// Put the terminal in raw, no-echo mode so single keypresses arrive
	// immediately. Uses stty to avoid any external Go dependency.
	restore, err := makeRaw()
	if err != nil {
		fmt.Fprintln(os.Stderr, "cannot set raw mode:", err)
		os.Exit(1)
	}
	defer restore()

	fmt.Print(hideCur, clear)
	cleanup := func() {
		fmt.Print(showCur, reset, "\r\n")
		restore()
	}

	// Restore the terminal on Ctrl-C / SIGTERM.
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	go func() { <-sig; cleanup(); os.Exit(0) }()

	// Read keys on a background goroutine.
	keys := make(chan byte, 16)
	go readKeys(keys)

	g := mitris.NewGame(mitris.GBWidth, mitris.GBHeight, 1.0/float64(*frameRate))
	ticker := time.NewTicker(time.Second / time.Duration(*frameRate))
	defer ticker.Stop()

	for {
		select {
		case k := <-keys:
			switch k {
			case 'q', 3: // q or Ctrl-C
				cleanup()
				return
			case 'l':
				g.MoveLeft()
			case 'r':
				g.MoveRight()
			case 'u':
				g.RotatePiece()
			case 'd':
				g.DropPiece()
			}
		case <-ticker.C:
			g.ClockTick()
			render(g)
			if g.IsGameOver() {
				drainAnimation(g, keys)
				cleanup()
				return
			}
		}
	}
}

// render draws the current display board to the terminal.
func render(g *mitris.Game) {
	board := g.DisplayBoard()
	var b []byte
	b = append(b, home...)
	level := board.Level()
	t := g.Time()
	// Top of the screen is high y (bottom-origin board flipped for display).
	for y := board.Height() - 1; y >= 0; y-- {
		for x := 0; x < board.Width(); x++ {
			p := board.At(x, y)
			if p == nil {
				b = append(b, emptyBG...)
			} else {
				c := p.LevelColor(level, t)
				b = append(b, fmt.Sprintf("\x1b[48;2;%d;%d;%dm", c.R, c.G, c.B)...)
			}
			for i := 0; i < cellW; i++ {
				b = append(b, ' ')
			}
		}
		b = append(b, reset...)
		b = append(b, "\r\n"...)
	}
	b = append(b, fmt.Sprintf("%s lines %-3d  level %-2d  time %5.1fs   [L]eft [R]ight [U]rotate [D]rop [q]uit\r\n",
		reset, board.NumCleared(), level, t)...)
	os.Stdout.Write(b)
}

// drainAnimation plays the game-over "drain": the stack shifts down one row at a
// time (Java GAME_END_2), then waits briefly for a keypress.
func drainAnimation(g *mitris.Game, keys chan byte) {
	board := g.DisplayBoard()
	fmt.Printf("\r\nGAME OVER — cleared %d lines in %.1fs\r\n", board.NumCleared(), g.Time())
	ticker := time.NewTicker(300 * time.Millisecond)
	defer ticker.Stop()
	for !board.IsBoardEmpty() {
		select {
		case k := <-keys:
			if k == 'q' || k == 3 {
				return
			}
		case <-ticker.C:
			board = board.ShiftBoardDown()
			renderBoard(board, g.Time())
		}
	}
	time.Sleep(600 * time.Millisecond)
}

// renderBoard draws an arbitrary board (used by the drain animation).
func renderBoard(board *mitris.Board, t float64) {
	var b []byte
	b = append(b, home...)
	level := board.Level()
	for y := board.Height() - 1; y >= 0; y-- {
		for x := 0; x < board.Width(); x++ {
			p := board.At(x, y)
			if p == nil {
				b = append(b, emptyBG...)
			} else {
				c := p.LevelColor(level, t)
				b = append(b, fmt.Sprintf("\x1b[48;2;%d;%d;%dm", c.R, c.G, c.B)...)
			}
			for i := 0; i < cellW; i++ {
				b = append(b, ' ')
			}
		}
		b = append(b, reset...)
		b = append(b, "\r\n"...)
	}
	os.Stdout.Write(b)
}

// readKeys reads stdin byte-by-byte and normalizes arrow keys and WASD into the
// original four button letters (l/r/u/d), forwarding q and Ctrl-C as-is.
func readKeys(out chan<- byte) {
	r := bufio.NewReader(os.Stdin)
	for {
		c, err := r.ReadByte()
		if err != nil {
			return
		}
		switch c {
		case 0x1b: // escape: possibly an arrow sequence ESC [ A/B/C/D
			c2, err := r.ReadByte()
			if err != nil {
				return
			}
			if c2 != '[' {
				continue
			}
			c3, err := r.ReadByte()
			if err != nil {
				return
			}
			switch c3 {
			case 'A':
				out <- 'u'
			case 'B':
				out <- 'd'
			case 'C':
				out <- 'r'
			case 'D':
				out <- 'l'
			}
		case 'w', 'W', 'k', 'K':
			out <- 'u'
		case 's', 'S', 'j', 'J', ' ':
			out <- 'd'
		case 'a', 'A', 'h', 'H':
			out <- 'l'
		case 'd', 'D':
			out <- 'r' // note: 'd'/'D' as WASD means right; the U/D/L/R button
			// names come from arrows/space. Space also hard-drops.
		case 'l', 'L':
			out <- 'r'
		case 'q', 'Q', 3:
			out <- c
		}
	}
}

// makeRaw switches the controlling terminal to raw, no-echo mode via stty and
// returns a function that restores the previous settings.
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
	// state ends with a newline from stty -g; trim it.
	for len(state) > 0 && (state[len(state)-1] == '\n' || state[len(state)-1] == '\r') {
		state = state[:len(state)-1]
	}
	return stty(state)
}

func stty(args ...string) error {
	cmd := exec.Command("stty", args...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}
