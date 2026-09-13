# impl/go — MITris in Go

A faithful Go port of **MITris**, the Tetris game the MIT hackers ran on the
windows of the Green Building (Building 54) on April 20, 2012. The original is
the Java `edu.mit.d54.plugins.mitris` package from
[`mitrisdev/d54`](https://github.com/mitrisdev/d54).

## Provenance and license

This is a **derived work of the upstream Java code**, which is BSD 3-Clause
licensed. The license permits source and binary redistribution with attribution;
the required notice and disclaimer are in [`UPSTREAM-LICENSE.txt`](UPSTREAM-LICENSE.txt)
and repeated in the `mitris` package doc comment.

## What this is (and is not)

This port reproduces the **observable behaviour of the original MITris**, which
is a deliberately simple game:

- 9-wide × 17-tall board (the 153 windows of the Green Building's south face);
- the seven tetrominoes with the original component offsets and colors;
- the original four-case rotation transform, with **no wall kicks** — a rotation
  that does not fit simply fails;
- **uniform-random** piece selection (no 7-bag);
- gravity cadence `5/(level+5)` seconds per row; `level = linesCleared/4`;
- **hard drop** on the "down" button (the original `D` button), no soft drop;
- **no hold**, no scoring beyond lines cleared;
- a bottom-origin board: `y=0` is the floor, pieces spawn at the top and fall
  toward `y=0`.

It is therefore **not** the game defined by this repo's [`SPEC.md`](../../SPEC.md)
(that lineage — the Python legacy and its conformance traces — is a modern
Tetris with DAS/ARR, hold, 7-bag, wall kicks, T-spins and scoring). This port is
a separate lineage and is **not wired into `bin/verify.sh`**; it is a faithful
re-telling of the original hack, not a SPEC conformance target.

## Layout

```
impl/go/
  mitris/            pure engine (no I/O): piece.go, board.go, game.go, color.go
  display/           in-process model of the wal.sh/tools/display protocol:
                     palettes, device profiles, pal16/hex frames, the lease relay
  cmd/mitris/        terminal front-end for the game (ANSI truecolor, raw input)
  cmd/display-tui/   TUI that simulates a display sink and sends it commands
```

The `mitris` and `display` packages are pure/I/O-free (the relay is in-process);
all terminal I/O lives under `cmd/`.

## `display-tui` — command a simulated display

A terminal tool that stands in for the web page
`https://wal.sh/tools/display/?d=green-building&demo=tetris`. It acts as a
display **source**: it takes the per-display lease and streams `pal16`/`hex`
frames to an **in-process, simulated sink** rendered in your terminal with the
device's real palette. The **`tetris` demo drives the `mitris` engine above**,
quantizing its RGB frames to the display's 16-color palette exactly as
`docs/PROTOCOL.md` §5.4 describes for a 9×17 green-building display.

It **never connects to the live relay** (`wss://wal.sh/tools/display/ws`);
§5.4 forbids that from this repo. Everything is local.

```sh
go run ./cmd/display-tui                       # green-building, command mode
go run ./cmd/display-tui -d green-building -demo tetris
go run ./cmd/display-tui -d hub75              # any profile from capabilities.json
```

Commands (type at the `>` prompt):

| command | effect |
|---|---|
| `d <profile>` / `profiles` | switch device / list them |
| `reserve <name> [ttl]` · `renew` · `release` | the display lease (auto-reserved as `tui` on first send) |
| `fill <0-15>` · `clear` · `pixel <x> <y> <0-15>` | edit the frame |
| `pattern <bars\|checker\|ramp\|border>` | test patterns |
| `hex` | dump the current frame in the `hex` wire form |
| `demo tetris` · `demo life` | animations; in `tetris`, arrows/WASD play, Esc stops |
| `quit` | exit |

Palettes and profiles are transcribed from the pinned
`contrib/displays/contract/wal-sh-display-0.2.1/capabilities.json` (spec v0.2.1).

## Build, test, run

```sh
go test ./...            # engine tests (faithful-behaviour checks)
go build ./...           # build everything
go run ./cmd/mitris      # play in the terminal
go run ./cmd/mitris -fps 60
```

## Controls

Mapped to the original arcade box's four buttons:

| Action  | Original | Keys                    |
|---------|----------|-------------------------|
| Left    | `L`      | ←, `a`, `h`             |
| Right   | `R`      | →, `d`, `l`             |
| Rotate  | `U`      | ↑, `w`, `k`             |
| Drop    | `D`      | ↓, `s`, `j`, Space      |
| Quit    | —        | `q`, Ctrl-C             |

The terminal is put into raw/no-echo mode via `stty` and restored on exit, so
the module has **no external dependencies**.
