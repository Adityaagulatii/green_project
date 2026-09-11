# Is there a FreeBSD tetris that simulates the MIT board?

**Short answer: not out of the box.** Every tetris in the FreeBSD ports tree plays on a board of 10 × 20 or larger. Only Emacs's `tetris.el` can be set to 9 columns × 17 rows without touching the source; the BSD `tetris(6)` needs a patch.

- **Emacs `tetris.el`.** The board size is a pair of `defcustom`s, so a 9 × 17 board takes two settings and no patch.
- **BSD `tetris(6)`,** the one on the [OpenBSD 4.3 man page](https://man.freebsd.org/cgi/man.cgi?query=tetris&sektion=6&manpath=OpenBSD+4.3), fixes its board size as compile-time constants in `tetris.h`. A five-constant patch gives a real 9 × 17 terminal game. That patch now lives in [`contrib/bsd-tetris-mit/`](../contrib/bsd-tetris-mit/), built and recorded.

Neither is a *simulator* of the MIT installation, though. Both are different games that happen to fit on the same grid. The spec-faithful simulator of the Green Building game is this repo's own engine and `tetris_sim` (see [below](#why-our-simulator-is-the-spec-faithful-one)).

![BSD tetris(6) patched to a 9x17 well](media/casts/bsd-tetris-17x9.gif)

## The comparison

These were checked from source on 2026-09-11 against the FreeBSD-latest package set on FreeBSD 15.1. Each distfile's sha256 matched the port's `distinfo`.

| Port | Version | Board (w × h) | Size configurable? | Fits 9 × 17? | UI / deps | Licence | Rotation / scoring |
|---|---|---|---|---|---|---|---|
| **games/bsdgames** `tetris(6)` | 0.76 | 10 × 20 | **Compile time only.** `tetris.h` sets `#define B_COLS 12` and `B_ROWS 23`, the padded board (plus `D_LAST`, `A_LAST` and `MINROWS`). | **Yes, with a patch.** See [`contrib/bsd-tetris-mit`](../contrib/bsd-tetris-mit/). | terminal, termcap API from ncurses | BSD-3 | Counter-clockwise only (`-c`: clockwise), no kicks. 1 point per piece + 1 per row hard-dropped, row bonuses of 10/30/70/150, × level |
| **Emacs** `tetris.el` (in `editors/emacs`, `emacs-nox`) | Emacs 31.1 | 10 × 20 | **At run time.** `tetris.el` has `(defcustom tetris-width 10 …)` and `(defcustom tetris-height 20 …)`. | **Yes, no patch.** Set both *before* `tetris.el` loads. | Emacs buffer (tty or GUI), `gamegrid.el` | GPL-3+ | Simple rotation, no kicks. Points per placed shape and rotation (`tetris-shape-scores`); rows are counted, not scored |
| games/vitetris | 0.59.1 | 10 × 20 | **No.** `board[20]` in `src/game/tetris.h`, and `x < 10` and `20` appear throughout `tetris.c` and `draw.c`. | Only by patching about 20 literals | ncurses | BSD-2 | NES-style, no kicks. NES 40/100/300/1200 × (level+1) |
| games/bastet | 0.43.2 | 10 × 20 | **Compile time only.** `Block.hpp` has `WellHeight=20; WellWidth=10`. | Yes, with a 2-constant edit | ncurses, boost-libs | GPL-3+ | Cycles through orientations, no kicks, *adversarial* piece choice. 100/300/500/800 |
| games/ltris | 1.2.1 | 10 × 20 | **Compile time only.** `src/ltris.h` has `BOWL_WIDTH = 10, BOWL_HEIGHT = 20`. | Yes, with an edit. The spawn `5 - rx` and the "figures" data files assume 10 wide | **SDL 1.2**, SDL_mixer | GPL-2+ | "Modern" mode shifts up to 2 cells sideways on rotation, which is not SRS. NES scoring |
| games/nbsdgames (`jewels`) | 6.0.2 | 19 × 17 | **Compile time only.** `jewels.c` has `#define LEN 17`, `WID 19`. | Height is already 17; set `WID 9`. **It is not tetris,** though: falling jewel pairs | ncurses | CC0 | Pair rotation; match-and-combo scoring |
| games/patapizza-tetris | 1.0 | 20 × 30 | **Compile time only.** `WALL_WIDTH 400`/`WALL_HEIGHT 600` px ÷ `BLOCK_SIZE 20` | Yes, with a pixel-constant edit. The spawn x then needs a fix | **SDL 1.2** + image/gfx/ttf | GPL-3+ | Per-piece flip tables, no kicks. No points; a level every 10 lines |

**Emacs: tried, not just read.** Running `emacs -Q --batch` with `(setq tetris-width 9 tetris-height 17)` before `(require 'tetris)` starts a game on a 9-wide, 17-tall grid, and shapes land and score. The width must be set before loading, because `tetris-next-x` and the buffer layout are computed from it at load time; `M-x customize` after loading is too late for that variable. A dedicated Emacs client, with local 9 × 17 play plus a remote client for a SPEC-faithful engine server, is **in progress on branch `feat/emacs-client`** under `contrib/emacs/`.

## BSD `tetris(6)`: the man page vs. the source

The [OpenBSD 4.3 man page](https://man.freebsd.org/cgi/man.cgi?query=tetris&sektion=6&manpath=OpenBSD+4.3), from 2007, describes the same program that FreeBSD ships today in `games/bsdgames` 0.76. The source tree reached pianojockl/bsdgames by way of DragonFly BSD; the file headers read `$OpenBSD: tetris.c,v 1.32 2017/08/13` and `$NetBSD: … 1995`.

- **The same in both:**
  - the options `-c`, `-k keys`, `-l level`, `-p` and `-s`;
  - the default keys `jkl pq`;
  - counter-clockwise rotation, or clockwise "classic" rotation with `[]` blocks;
  - fall speed = level;
  - score × level, and a preview penalty (`PRE_PENALTY 0.75`).
- **Different in 0.76:**
  - The high-score file is **`$HOME/.tetris.scores`**, not `/var/games/tetris.scores`, so nothing needs setgid.
  - Randomness is `arc4random_uniform`.
- **Board dimensions.** They sit in `tetris/tetris.h` and nowhere else. `board[]` is one flat array of `B_ROWS * B_COLS` cells, and every shape offset in `shapes.c` is written in terms of `B_COLS` (`TL = -B_COLS-1`, …). That is why the size can be changed at compile time, yet only there: there is no option or environment variable for it.

  ```c
  #define B_COLS  12   /* 10 playfield columns + 2 walls           */
  #define B_ROWS  23   /* boundary row + 20 playfield rows + floor */
  #define D_LAST  22   /* last displayed row + 1                   */
  #define A_LAST  21   /* last active (playable) row + 1           */
  #define MINROWS 23   /* minimum terminal height                  */
  ```

  The patch sets these to 11, 20, 19, 18 and 20. It also centres the spawn column for an odd width, and moves the preview, which upstream draws at fixed screen columns *left* of the well, to the right of the well. The full list is in [`contrib/bsd-tetris-mit/README.md`](../contrib/bsd-tetris-mit/README.md).

## Why our simulator is the spec-faithful one

The patched BSD game has the right *shape*, but it is still BSD tetris. The game on the Green Building is the one in [`SPEC.md`](../SPEC.md), derived from the installation's own Python and pinned by the conformance traces (the KAVs). On nearly every rule that shapes play, the two differ:

| | MIT Green Building game (SPEC v1) | BSD `tetris(6)` |
|---|---|---|
| Rotation | CW, CCW **and 180°**, with SRS-style kick tables (§5.3), including the legacy's odd 180° table (QUIRK-1) | CCW only (or CW with `-c`), no kicks: a rotation that doesn't fit is refused |
| Randomiser | **7-bag**, xorshift32 + Fisher–Yates, seeded, so traces are reproducible (§9.3) | uniform `arc4random`, with a random initial rotation; not reproducible |
| Spawn | rotation 0 at a fixed cell (§4.3) | random rotation, centred |
| Gravity | frame-based binary64 accumulator at 30 FPS; level 0 = 25 frames per row (§7) | `1e9/level` ns, and slightly faster every tick |
| Lock | no lock delay (QUIRK-14) | no lock delay; you can still move until the tick that finds it resting |
| Scoring | 100 × (lines ÷ 10 + 1) per row, row by row (QUIRK-5); level every *L*+5 lines | 1/piece + drop distance + 10/30/70/150 bonus, × level at the end |
| Extras | hold, ghost, 5-frame line-clear flash, 218-frame game-over fill, 3-2-1 countdown | none of these; a two-step blink when rows clear |
| Output | 17 × 9 **RGB** frames at 30 FPS, the display contract (§2) | characters on a terminal |

So use the BSD port for a fun, period-correct terminal game at the right aspect ratio. Use [`tetris_sim`](../impl/python/sim/) for anything meant to predict what the building will show. It drives the same engine that the conformance traces check frame by frame through SHA-256 digests. For pictures of it, see [`docs/media/`](media/README.md), which has snapshots, asciinema casts and a digest-verified ASCII replay of KAV-14.

## Sources

- The man page: [tetris(6), OpenBSD 4.3](https://man.freebsd.org/cgi/man.cgi?query=tetris&sektion=6&manpath=OpenBSD+4.3).
- The FreeBSD ports: `games/bsdgames`, `games/vitetris`, `games/bastet`, `games/ltris`, `games/nbsdgames` and `games/patapizza-tetris`, from their `Makefile`/`distinfo` at [cgit.freebsd.org/ports](https://cgit.freebsd.org/ports/).
- Distfiles and their sha256:

  | Distfile | sha256 |
  |---|---|
  | `pianojockl-bsdgames-v0.76_GH0.tar.gz` | `fba58361de9c6c3d4489f60e3d61e6fde1655ef53a6bb12378880af19b2e748f` |
  | vitetris v0.59.1 | `699443df…fbdf58d` |
  | bastet 0.43.2 | `f219510a…f2c403f` |
  | ltris-1.2.1 | `f868f79d…fdea924` |
  | nbsdgames v6.0.2 | `9545b099…d9f37ca` |
  | patapizza/tetris e6f1a41 | `d0d34614…17d5f4b64b` |
- Emacs `lisp/play/tetris.el`: [emacs-mirror](https://github.com/emacs-mirror/emacs/blob/master/lisp/play/tetris.el), read from the installed `emacs-nox` 31.1.
