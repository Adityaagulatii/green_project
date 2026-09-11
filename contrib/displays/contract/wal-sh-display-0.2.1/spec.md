---
title: "Display: a 16-colour grid sink with a leased frame source"
description: >-
  Specification for wal.sh/tools/display, a browser sink that renders w by h cells in 16 colours from frames delivered by a single leased source; formal query parameters, device presets with CGA 40 by 25 as the default, the control and frame protocol with one byte per cell on the wire, numeric limits, the experiments gate, and the conditions that would falsify the design.
date: '2026-09-11'
authors:
  - "Jason Walsh"
keywords:
  - "display"
  - "tool"
  - "websocket"
  - "lease"
  - "frame"
  - "palette"
  - "cga"
  - "tetris"
  - "hub75"
  - "ws2812"
  - "gamegrid"
  - "spec"
  - "protocol"
  - "sink"
  - "pal16"
canonical: "https://wal.sh/tools/display/spec"
---

- [Scope](#orgc4971ac)
- [Rendering contract: 16 colours](#rendering-contract)
- [Query parameters](#query-parameters)
- [Presets](#presets)
- [Discovery](#discovery)
- [Wire protocol](#wire-protocol)
  - [Viewer](#org31fe4c5)
  - [Source](#org1207900)
  - [Rules](#orgb98c7fb)
  - [Frame formats](#frame-format)
  - [Emacs source sketch](#org6e39e13)
- [Source recipes](#source-recipes)
- [Reduction contract (the sink's fold)](#reduction-contract)
- [Semantic DOM](#semantic-dom)
- [Limits](#limits)
- [Experiments](#experiments)
- [Non-requirements](#non-requirements)
- [Conformance fixtures](#conformance-fixtures)
- [Refutation conditions](#refutation-conditions)
- [Open questions](#org3a3c2c3)
- [Changelog](#org2955a42)
  - [v0.2.1 (2026-09-11)](#orgded434e)
  - [v0.2.0 (2026-09-11)](#orgcd44d95)
  - [v0.1.0 (2026-09-11)](#org18e40ab)



<a id="orgc4971ac"></a>

# Scope

The display is a sink. It draws the frames it receives and does nothing else: no engine, no animations, no content of its own. Anything that produces frames (Emacs gamegrid, a Python engine, an ESP32 relaying a sensor, a 16-colour movie player, the building rig) is a source and lives elsewhere. This page specifies what the sink accepts on its URL, what it advertises, what it speaks on the wire, and the numbers it will not exceed.

Three parts:

| Part                | Where                                      | Role                                                                           |
|------------------- |------------------------------------------ |------------------------------------------------------------------------------ |
| `index.org` page    | static, `/tools/display/`                  | browser sink; draws frames it receives                                         |
| `capabilities.json` | static, `/tools/display/capabilities.json` | advertisement: presets, palettes, fps, frame formats, endpoints                |
| `relay`             | one process                                | WebSocket endpoint; holds one lease per display and fans frames out to viewers |

The relay is one possible transport. A named pipe, a UDP port, a serial line, or the building rig can replace it as long as it delivers the same frames; the page and the advertisement stay the same. The page also accepts frames from a source in the same browser through `window.postMessage({frame})` and exposes `window.display = {w, h, makeframe, send}`, the SPEC section 2.3 surface, so a local source needs no socket at all.

Built as a browser tool under `docs/specs/tool-maintenance.org` v1.0.0: `src/wal_sh/tools/display/core.cljc` holds the pure reduction and projection, `browser.cljs` holds the socket, the DOM, and nothing that decides.


<a id="rendering-contract"></a>

# Rendering contract: 16 colours

The rendered surface is `w` by `h` cells. Every cell shows exactly one of 16 palette entries, index 0 to 15. Index 0 is unlit (the background). The palette is a property of the display (chosen by preset, by the `palette` parameter, or announced in `caps`), not of the frame.

A cell on the wire is a palette index. That is the whole rendering contract: the wire carries what the display can show, four bits of information per cell, and nothing the display would discard. Sources that hold colours rather than indices quantize before the wire (see [7](#source-recipes)); the sink never quantizes.

Default palette is `cga`, the IBM PC CGA sixteen:

| idx | name       | hex     | idx | name          | hex     |
|--- |---------- |------- |--- |------------- |------- |
| 0   | black      | #000000 | 8   | dark gray     | #555555 |
| 1   | blue       | #0000AA | 9   | light blue    | #5555FF |
| 2   | green      | #00AA00 | 10  | light green   | #55FF55 |
| 3   | cyan       | #00AAAA | 11  | light cyan    | #55FFFF |
| 4   | red        | #AA0000 | 12  | light red     | #FF5555 |
| 5   | magenta    | #AA00AA | 13  | light magenta | #FF55FF |
| 6   | brown      | #AA5500 | 14  | yellow        | #FFFF55 |
| 7   | light gray | #AAAAAA | 15  | white         | #FFFFFF |

Named palettes in v0.2: `cga` (default), `c64`, `pico8` (16 entries each), `gb` (4), `grey8` (8), `mono` (2). A palette with fewer than 16 entries is a display with fewer **levels**: a lamp behind a window is on or off, a relay-dimmed one has eight steps. A frame is still 16-valued on the wire; the sink reduces each index to the palette's `n` levels by the display-contract rule

    level(idx) = 0            when idx = 0
               = max(1, round(idx * (n - 1) / 15))   otherwise

so index 0 is always unlit and any lit index stays lit. `mono` and `grey8` are **mono** palettes: their entries are brightness, drawn as a grey ramp; the others are colours. `levels` and `mono` are advertised per preset in `capabilities.json`. A relay may announce a 16-entry palette in `caps`; the page adopts it. Custom palettes are not accepted on the URL in v0.2 (see [10](#limits)).


<a id="query-parameters"></a>

# Query parameters

The page takes `d`, `w`, `h`, `px`, `gap`, `aspect`, `src`, `palette`, `fps`. `d` selects a preset; the explicit parameters override the preset's fields one by one. Unknown parameters are ignored. A parameter outside its domain is rejected, not clamped: the page renders nothing and shows an error element naming the parameter (see [9](#semantic-dom)), so a bad link fails visibly rather than drawing the wrong grid.

| name      | type    | domain                                     | default           | overrides preset   | on invalid          |
|--------- |------- |------------------------------------------ |----------------- |------------------ |------------------- |
| `d`       | enum    | a preset name from [4](#presets)           | `cga40`           | n/a                | error `bad-preset`  |
| `w`       | integer | 1 to 256                                   | preset            | yes, and fixes it  | error `bad-w`       |
| `h`       | integer | 1 to 256                                   | preset            | yes, and fixes it  | error `bad-h`       |
| `px`      | integer | 0 to 200, cell height in CSS px, 0 = fit   | 0                 | yes                | error `bad-px`      |
| `gap`     | number  | 0 to 1, fraction of a cell                 | preset            | yes                | error `bad-gap`     |
| `aspect`  | number  | 0.25 to 4, cell width over cell height     | preset            | yes                | error `bad-aspect`  |
| `src`     | URL     | `ws://` or `wss://`, host in [10](#limits) | from capabilities | yes                | error `bad-src`     |
| `palette` | enum    | a named palette                            | preset or `cga`   | yes                | error `bad-palette` |
| `fps`     | integer | 1 to 60                                    | preset            | yes, downward only | error `bad-fps`     |

`w * h` must not exceed 65,536 (256 by 256). `fps` can lower a preset's rate, never raise it: the preset's rate is the hardware's, and the sink honours the tighter of the two.

Geometry authority: an explicit `w` or `h` fixes the grid, and a relay whose `caps` disagree is refused (`bad-src` with the mismatch in the error element). Without explicit `w` and `h`, the relay's `caps` may resize the grid, since the source knows the panel it is driving. `px` is a rendering hint and never changes the grid.

Resolution order: preset fields first, then each explicit parameter in the table's order, then `caps` for `w`, `h`, `fps`, `palette` when not fixed. The effective values are written to the root element's `data-` attributes so a reader (or Bombadil) can check what the page decided against what the URL asked.


<a id="presets"></a>

# Presets

A preset is a named geometry with a default palette and frame rate. The footer lists every preset as a link, so `/tools/display?d=green-building` works and the others are one click away.

| `d`               | grid    | cell aspect | gap  | palette | fps | kind      | stands for                                                                                |
|----------------- |------- |----------- |---- |------- |--- |--------- |----------------------------------------------------------------------------------------- |
| `cga40` (default) | 40 x 25 | 1.2         | 0    | cga     | 30  | text-mode | IBM PC CGA 40-column text mode: 320 x 200 on a 4:3 tube, pixel aspect 6:5                 |
| `tetris`          | 10 x 20 | 1           | 0.12 | cga     | 30  | field     | standard field                                                                            |
| `green-building`  | 9 x 17  | 1.5         | 0.35 | cga     | 30  | facade    | Building 54: wide windows with masonry between them                                       |
| `dc32`            | 10 x 18 | 1           | 0    | gb      | 30  | badge     | DEF CON 32 badge, Game Boy field                                                          |
| `gameboy`         | 10 x 18 | 1           | 0    | gb      | 30  | field     | same geometry, named for the original                                                     |
| `trs80`           | 10 x 12 | 1           | 0.12 | mono    | 30  | text-mode | largest field inside a 32 x 16 text screen                                                |
| `c64`             | 10 x 20 | 1           | 0.12 | c64     | 30  | field     | 40 x 25 screen; the full field fits                                                       |
| `ws2812`          | 16 x 16 | 1           | 0.3  | cga     | 30  | panel     | ESP32 + 16 x 16 LED matrix                                                                |
| `hub75`           | 64 x 32 | 1           | 0.15 | cga     | 60  | panel     | ESP32 + 64 x 32 HUB75 panel                                                               |
| `blinkenlights`   | 18 x 8  | 1.6         | 0.3  | mono    | 30  | facade    | Haus des Lehrers, Berlin, 2001: 8 floors x 18 windows, one lamp and relay each, on or off |
| `arcade`          | 20 x 26 | 1.3         | 0.3  | grey8   | 30  | facade    | Bibliotheque nationale de France, Paris, 2002: 20 x 26 windows, 8 grey levels             |
| `remote`          | 9 x 17  | 1.5         | 0.35 | cga     | 30  | facade    | frame sink; needs `&src=ws://host:port`                                                   |

`kind` is the display-contract vocabulary (`text-mode`, `field`, `facade`, `badge`, `panel`): what the preset stands in for, so a source can pick a demo that suits a building rather than a screen. The two Project Blinkenlights facades are mono displays with 2 and 8 levels; their gap of 0.3 is a placeholder until the window-to-masonry ratio is measured from the catalogue photographs. Toronto City Hall (Stereoscope, 2008, 960 windows, 16 levels), the Cira Centre (Philadelphia, 2013, RGB LED), and the Schonherz dormitory (Budapest) are known but not configured: their grids are not published in the sources consulted, and `capabilities.json` lists them as `unconfigured` rather than guessing.

`aspect` is cell width over cell height throughout; the CGA default's 1.2 is the 6:5 pixel of 320 by 200 on a 4:3 display (issue #93's draft wrote the same cell as 0.8, height over width). It is the largest default that still reads as a screen rather than a poster on a laptop, and its palette is the one the sink ships with.

Presets are data (`capabilities.json` and the same EDN the core reads), not code. Adding one is a data change plus a footer link.


<a id="discovery"></a>

# Discovery

`GET /tools/display/capabilities.json`. Static, co-located with the page. The live lease holder is only on the WebSocket; the advertisement never claims to know it. The contract does not live under `.well-known/`, which this site reserves for cross-cutting agent discovery; it is linked from `.well-known/api-catalog.json` (RFC 9727 linkset) the way `agents.json` is:

```json
{
  "anchor": "https://wal.sh/tools/display/",
  "service-desc": [{ "href": "https://wal.sh/tools/display/capabilities.json", "type": "application/json" }],
  "service-doc":  [{ "href": "https://wal.sh/tools/display/", "type": "text/html" }]
}
```

The advertisement:

```json
{
  "name": "wal.sh display",
  "spec": "0.2.0",
  "endpoints": { "ws": "wss://wal.sh/tools/display/ws", "page": "https://wal.sh/tools/display/?d=<display>" },
  "formats": ["pal16", "hex"],
  "max": { "w": 256, "h": 256, "cells": 65536, "fps": 60, "ttl": 900, "frameBytes": 65538 },
  "default": "cga40",
  "palettes": { "cga": ["#000000", "#0000AA", "..."], "c64": ["..."], "gb": ["..."], "mono": ["..."], "pico8": ["..."] },
  "displays": {
    "cga40":          { "w": 40, "h": 25, "aspect": 1.2, "gap": 0,    "palette": "cga", "fps": 30, "note": "IBM PC CGA 40-column text mode" },
    "tetris":         { "w": 10, "h": 20, "aspect": 1,   "gap": 0.12, "palette": "cga", "fps": 30 },
    "green-building": { "w": 9,  "h": 17, "aspect": 1.5, "gap": 0.35, "palette": "cga", "fps": 30 },
    "hub75":          { "w": 64, "h": 32, "aspect": 1,   "gap": 0.15, "palette": "cga", "fps": 60 }
  },
  "reservation": { "max_ttl": 900, "renew": "any frame or {\"op\":\"renew\"}", "one_holder_per_display": true }
}
```


<a id="wire-protocol"></a>

# Wire protocol

WebSocket at the advertised `endpoints.ws` (`src` overrides it). Text messages are JSON control, except that a `hex` frame is also a text message (see [6.4](#frame-format)); the two are told apart by the first character, `{` for control. Binary messages are `pal16` frames. Every control message carries `op`; unknown `op` values are ignored by the sink and answered with `error reason:unknown-op` by the relay.


<a id="org31fe4c5"></a>

## Viewer

```
->  {"op":"view", "display":"green-building"}        display optional; default cga40
<-  {"op":"caps", "display":"green-building", "w":9, "h":17, "fps":30, "format":"pal16", "palette":[16 hex]}
<-  {"op":"lease", "display":"green-building", "holder":"emacs@minibos" | null, "expires": <unix s> | null}
<-  <frame>  ...                                      one per frame the holder sends
```

A viewer never sends frames. `caps.format` is the format the relay will fan out; the page reads it once and does not renegotiate. `caps.w` and `caps.h` resize the grid unless the URL fixed it (see [3](#query-parameters)).


<a id="org1207900"></a>

## Source

```
->  {"op":"reserve", "name":"emacs@minibos", "display":"green-building", "ttl":300, "format":"pal16"}
<-  {"op":"granted", "lease":"<id>", "w":9, "h":17, "fps":30, "format":"pal16", "palette":[16 hex], "expires": <unix s>}
    or
<-  {"op":"busy", "holder":"...", "expires": <unix s>}
->  <frame>  ...                                      a frame renews the lease
->  {"op":"renew"}                                    optional, when idle
->  {"op":"release"}                                  or just close the socket
<-  {"op":"error", "reason":"not-holder" | "bad-frame-length" | "rate" | "bad-format" | "unknown-op"}
```


<a id="orgb98c7fb"></a>

## Rules

-   One holder per display. A `reserve` while held returns `busy`; the caller waits for `expires` or asks the holder to release.
-   `ttl` is at most 900 s. The lease expires `ttl` seconds after the last frame or `renew`. On expiry the relay sends every viewer `{"op":"lease","holder":null}` and a black frame (all cells index 0). Expiry is written as an event, never left implicit, so a viewer folding the message stream stays a pure function of it.
-   Frames from a non-holder are dropped with `error not-holder`.
-   Frames faster than `fps` are dropped, not queued. The relay keeps no backlog; a viewer that joins late sees the next frame, not history.
-   `format` defaults to `pal16` when omitted in `reserve`.
-   The reservation half is a timed-reservation state machine, `free -> held(holder, expires) -> free`, the same rung as `aygp-dr/state-machine-ladder` issue #1; this is a production instance of it.


<a id="frame-format"></a>

## Frame formats

Row-major, row 0 at the top, column 0 at the left, one palette index per cell. There is no partial or sparse frame; a source that changes one cell sends the whole frame.

| format  | carrier | bytes per frame    | encoding                                                                                      |
|------- |------- |------------------ |--------------------------------------------------------------------------------------------- |
| `pal16` | binary  | `w * h` (+2)       | one byte per cell, value 0 to 15; an optional 2-byte big-endian sequence number may prefix it |
| `hex`   | text    | `h * (w + 1)` (+1) | `h` lines of `w` hex digits `0` to `f`, LF-terminated; an optional blank line ends the frame  |

`pal16` is the native form and the one the relay fans out by default: 153 bytes for the Green Building, 1,000 for CGA, 2,048 for HUB75. The relay strips the sequence prefix before fan-out and uses it only to drop reordered frames (a lower sequence than the last accepted is dropped with `rate`).

`hex` is the same information as text. It exists because a text frame is also a render: `tail -f` the stream and the picture is legible, a few lines of script turn it into ANSI for a terminal, and every source can produce it with a `printf` over a plain pipe, UDP, or serial line with no binary handling. A `hex` frame travels as a WebSocket text message and is told from control by its first character.

A frame of any other length, or a `hex` digit outside `0` to `f`, is dropped with `bad-frame-length` or `bad-format`; the byte value 16 to 255 in `pal16` is `bad-format`.

Why not RGB on the wire: the display shows 16 colours, so an RGB cell carries 24 bits of which 20 are discarded at render; two sources sending the same picture in slightly different RGB render identically. v0.1.0 accepted `rgb24` at the sink and quantized there; v0.2.0 moves that to the edge that has the colours (see [7](#source-recipes)) so the sink stays a pure index renderer.


<a id="org6e39e13"></a>

## Emacs source sketch

Emacs 30 has no built-in WebSocket client; `websocket.el` (MELPA) provides one. The source side is: open the socket, send the `reserve` text, then on each gamegrid change build a `w*h` unibyte string of palette indices and `websocket-send-binary`, or build the `hex` text and `websocket-send-text`. That code belongs in the source repo, not here.


<a id="source-recipes"></a>

# Source recipes

Not normative; the shapes that sources take, so the wire choices above can be judged against real producers.

-   **Video at CGA geometry.** `ffmpeg` scales and quantizes; a shim does the `reserve` and sends frames. The quantization is ffmpeg's, to the display's exact palette, so the sink sees indices:
    
    ```sh
      # palette.png: the sixteen CGA colours as a 16x1 image (make once).
      ffmpeg -hide_banner -re -i rick.mp4 -an \
        -vf "scale=40:25:flags=area,paletteuse=dither=none" -i palette.png \
        -pix_fmt pal8 -f rawvideo - | shim --display cga40 --name ffmpeg@nexus
    ```
    
    `pal8` is one byte per cell already; the shim maps the 256-entry palette index to 0 to 15 (identity when the palette is ours) and prefixes nothing. Without `paletteuse`, `-pix_fmt rgb24 -f rawvideo` gives `w*h*3` bytes and the shim quantizes (nearest sRGB, ties to the lower index). On nexus, ffmpeg 8.1 has `rawvideo` and `rgb24` but no libcaca device, so `-f caca` is not available here; it was never on the wire path.
-   **Gamegrid.** Already palette-indexed; one `pal16` frame per board change, 153 bytes for the Green Building.
-   **Microcontroller.** A HUB75 frame buffer is indexed or RGB; `pal16` costs 2,048 bytes at 60 fps, 123 KB/s, well inside an ESP32 over WiFi.
-   **Blinkenlights family.** A `pal16` frame is an MCUF payload with `channels=1`, `maxval=15` and the header removed; BLP (magic `DEADBEEF`, one byte per pixel, 0 or 1) is the 2-level case. The relay, when it exists, accepts both over UDP 2323 and as WebSocket binary, picks the display by width x height, scales other `maxval` to 0..15, and treats a UDP sender as a 5-second holder. The sink never sees these formats; it sees `pal16`. Reference: <https://wiki.blinkenarea.org/index.php/BlinkenlightsProtocolEnglish>
-   **Same-page source.** `window.display.send(frame)` with a `Uint8Array` of `w*h` indices, or `postMessage({frame})` from another window; no socket, no relay, same reduction.


<a id="reduction-contract"></a>

# Reduction contract (the sink's fold)

`core.cljc` exposes one pure step over the messages above:

```clojure
(reduce-event state event) ;; => state, total over every event kind
```

Canonical state:

```clojure
{:w 40 :h 25 :fixed? false :format :pal16 :palette :cga :fps 30
 :status :connecting            ; :connecting | :live | :idle | :closed | :error
 :holder nil :expires nil
 :seq 0 :cells [...]            ; w*h palette indices
 :dropped 0 :error nil}
```

Events are the control messages (as maps) plus `:frame` (bytes or text), `:open`, `:close`, `:tick`. Reductions:

| event    | effect                                                                                                                                                         |
|-------- |-------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `:open`  | status `:connecting`; the adapter then sends `view`                                                                                                            |
| `caps`   | set fps, format, palette; set w and h unless `:fixed?` (then a mismatch is `:error`); reallocate `:cells` to index 0                                           |
| `lease`  | set holder and expires; holder nil sets status `:idle`                                                                                                         |
| `:frame` | length and digits must match the format; else `:dropped` increments and state is unchanged; a valid frame replaces `:cells`, increments `:seq`, status `:live` |
| `error`  | record `:error`; status unchanged                                                                                                                              |
| `:close` | status `:closed`; cells kept so the last frame stays visible                                                                                                   |
| `:tick`  | if expires has passed, status `:idle` (the relay's black frame will follow)                                                                                    |
| unknown  | state unchanged, `:dropped` increments                                                                                                                         |

Invariants, asserted on every reduced state and tested by `clojure.test.check` properties:

-   `(count cells)` equals `w * h` whenever w and h are set.
-   every cell is an integer in 0 to 15.
-   `:seq` is monotone non-decreasing across `:frame` events.
-   a frame of wrong length or with an out-of-range cell never changes `:cells`.
-   `reduce-event` is total: no event kind throws; unknown input counts as dropped.
-   `decode` is format-symmetric: the `hex` and `pal16` encodings of the same indices decode to the same `:cells`.

Projection (`core.cljc`, pure): `(dirty state prev)` returns the list of `(x y idx)` triples that differ, so the adapter writes only changed cells. The projection is lossy by declaration: it forgets everything except what the DOM needs.


<a id="semantic-dom"></a>

# Semantic DOM

Observable by a reader, by Bombadil, and by the research-audit checks:

-   root `#display` with `data-d data-w data-h data-fixed data-px data-aspect data-gap data-palette data-fps data-format data-src data-status data-holder data-seq data-dropped`, the effective values after preset, parameter, and `caps` resolution.
-   one `<i data-x data-y data-c>` per cell in row-major order; `data-c` is the palette index; colour comes from a CSS custom property per index (`--c0` to `--c15`) set on the root from the palette.
-   `#display-error` present only when a parameter was rejected or `caps` contradicted a fixed grid, with `data-param` naming it.
-   footer `nav.display-presets` listing every preset as a link.

The DOM grid is the v0.2 renderer. A canvas renderer is an experiment (below), gated, and must produce the same `data-` attributes on the root; the per-cell elements are what the canvas path gives up.


<a id="limits"></a>

# Limits

Each limit is a number with a reason and a refutation condition (the observation that would justify changing it).

| limit       | value                                                           | reason                                                                                        | change it when                                                         |
|----------- |--------------------------------------------------------------- |--------------------------------------------------------------------------------------------- |---------------------------------------------------------------------- |
| grid        | w, h in 1 to 256; w\*h at most 65,536                           | 65,536 DOM cells renders in under a frame on a laptop; hub75 is 2,048                         | a preset needs more and the DOM renderer measures over 16 ms per frame |
| frame bytes | at most 65,538 (`pal16` at 256 x 256 with prefix); `hex` 65,793 | one WebSocket message, no fragmentation logic in v0.2                                         | a source needs a bigger grid                                           |
| fps         | at most 60; per preset                                          | the browser cannot paint faster; hardware presets carry their real rate                       | never upward; a preset may lower it                                    |
| ttl         | at most 900 s                                                   | a stalled source frees the display within fifteen minutes without operator action             | an unattended source needs longer and renews anyway                    |
| holders     | 1 per display                                                   | the single-writer rule; see [12](#non-requirements)                                           | a second writer is wanted (this is the CRDT trigger)                   |
| `src` hosts | loopback, RFC 1918, `*.wal.sh`                                  | the page's CSP `connect-src` admits only these; anything else is refused before connecting    | the relay is hosted somewhere else, and the CSP grows with it          |
| palettes    | named, or the 16 announced in `caps`                            | a custom list on the URL is 16 more inputs to validate; presets and `caps` cover the hardware | two URL-only sources ask for the same custom palette                   |
| viewers     | relay-side cap, 32 per display                                  | fan-out is `viewers * fps * frameBytes`; hub75 at 60 fps and 32 viewers is 3.9 MB/s           | the relay leaves one process                                           |

The CSP limit is the one that decides where the relay can live. The site-wide policy is `default-src 'self'`; crowsnest widens `connect-src` for one path to loopback only, via a per-tool `.htaccess`. Display does the same for `/tools/display/` and adds `wss://wal.sh` for the advertised endpoint. Whether the VPS can proxy WebSockets under Apache is an open question (see [11](#experiments)); until it can, `src` points at a relay on the LAN and the advertised endpoint is a plan, not a fact.


<a id="experiments"></a>

# Experiments

The tool ships behind the `tool.display` gate in `data/experiments.edn` (Statsig-shaped, plaintext, evaluated client-side by `wal-sh.experiments.core`). Gated off, the page renders the preset footer and the message the other RubyConf-batch tools use.

Dynamic configs, all read once at load:

| config                 | type    | default           | what it decides                             |
|---------------------- |------- |----------------- |------------------------------------------- |
| `display.relay`        | URL     | from capabilities | the `src` used when the URL gives none      |
| `display.max-cells`    | integer | 65536             | the grid cap, so a rollback needs no deploy |
| `display.max-fps`      | integer | 60                | the rate cap                                |
| `display.renderer`     | enum    | `dom`             | `dom` or `canvas`                           |
| `display.wire-formats` | set     | `#{pal16 hex}`    | which formats the sink negotiates           |

Experiments, each a gate with a percentage and a checkpoint:

-   `display.canvas`: the canvas renderer against the DOM grid, measured by paint time per frame at hub75 geometry. Refuted for canvas if it does not beat the DOM by 2x at 2,048 cells.
-   `display.wire-hex`: sources offered `hex` by default. Refuted if any SPEC section 2.3 source breaks on a text frame, or if the relay's fan-out cost at hub75 rate exceeds `pal16` by more than the byte ratio.
-   `display.relay-host`: the advertised endpoint on wal.sh against a LAN relay. Refuted for wal.sh if Apache on the VPS cannot hold a WebSocket for 900 s.


<a id="non-requirements"></a>

# Non-requirements

Stated, not omitted, each with the condition under which it becomes a requirement.

-   **NR-CRDT**: no conflict-free replicated state. The lease makes the display single-writer, and a full frame is a last-writer-wins register keyed by sequence; there is nothing to merge. Becomes a requirement the day two holders may paint one grid (then each cell is an LWW register and the frame becomes a per-cell map), or the day sparse updates exist and can interleave.
-   **NR-HISTORY**: the relay keeps no frames. A late viewer sees the next frame. Becomes a requirement if a source wants replay.
-   **NR-AUTH**: `reserve` is first-come. A name is a label, not an identity. Becomes a requirement when the relay leaves the LAN or the loopback trust domain (the crowsnest condition).
-   **NR-CONF**: frames are visible to every viewer of that display.
-   **NR-ENGINE**: the sink never generates content. A test card, a demo animation, a Tetris engine are sources; if one is wanted for the no-holder case it is a source that reserves the display like any other.
-   **NR-QUANT**: the sink never quantizes. Colour-to-index is a source or relay concern; the sink's input is indices, and a byte outside 0 to 15 is a bad frame, not a colour.


<a id="conformance-fixtures"></a>

# Conformance fixtures

Land with the core, under `test/wal_sh/tools/display/`:

-   `caps` for every preset, and the resulting `:cells` length; `caps` against a fixed grid, and the resulting `:error`.
-   one valid frame per format per preset; one frame of each wrong length (off by one either way) per format; a `pal16` frame with one byte of 16; a `hex` frame with one `g`.
-   the same indices as `pal16` and as `hex`, decoding to equal `:cells`.
-   a reordered frame (sequence prefix lower than last) and its `rate` drop.
-   the lease expiry sequence: `lease holder:null` followed by the black frame, and the DOM after it.


<a id="refutation-conditions"></a>

# Refutation conditions

This spec is wrong if any of these is observed:

1.  A parameter outside its domain draws a grid instead of the error element.
2.  A frame of the wrong length, or with a cell outside 0 to 15, changes any cell.
3.  Two connections hold the same display at once, or a non-holder's frame is drawn.
4.  The DOM renderer takes more than 16 ms to paint a full 256 by 256 frame on a 2024 laptop; the grid cap is then too generous.
5.  The advertised `wss://wal.sh` endpoint cannot be reached from the page under the site's CSP once the relay is up; the `src` limit and the per-tool `.htaccess` are then out of step.
6.  A `hex` frame and a `pal16` frame of the same indices render differently.
7.  A relay's `caps` resize a grid the URL fixed with `w` and `h`.


<a id="org3a3c2c3"></a>

# Open questions

-   Hosting the relay: a long-lived process on the DreamHost VPS behind Apache, or on a LAN box reached through the `src` parameter. The experiment above decides; until then `remote` is the honest default for anything not on loopback.
-   Whether `gb` and `mono` should reject frames that use indices their palettes alias, rather than aliasing silently. Aliasing keeps every frame renderable; rejecting keeps sources honest.
-   Whether the sequence prefix should be mandatory. Optional matches SPEC section 2.3 sources today.
-   Whether `capabilities.json` needs a publish path of its own: org-publish ships org and its attachments; a co-located JSON file needs the static component or an explicit rsync, the way `.well-known` has one.


<a id="org2955a42"></a>

# Changelog


<a id="orgded434e"></a>

## v0.2.1 (2026-09-11)

Display contract merged from the 17x9-Tetris SPEC v1 section 2: presets `blinkenlights` (18 x 8, mono) and `arcade` (20 x 26, grey8); `kind` per preset; palettes shorter than 16 entries reduce by the level rule instead of aliasing to the last entry (`gb` becomes a ramp); `grey8` palette; `levels` and `mono` advertised per preset; `capabilities.json` carries `interop` (BLP, MCUF), `unconfigured` facades, and the property vocabulary, and is generated from the core's tables by `wal-sh.tools.display.capabilities`. Kept against the contract: `cga40` aspect 1.2 (it wrote 1), `hub75` at 60 fps (it caps 30), and RGB on the wire stays a relay concern (NR-QUANT).


<a id="orgcd44d95"></a>

## v0.2.0 (2026-09-11)

Reconciled with issue #93. `pal16` (one byte per cell) is the native wire format and `hex` text the second; `rgb24` left the sink for the source shim and the relay (NR-QUANT). Preset id `cga40`; `px`; `caps` may resize an unfixed grid; the `postMessage` and `window.display` local-source surface; `capabilities.json` linked from the RFC 9727 api-catalog; the timed-reservation lease machine named; source recipes with the ffmpeg pipeline; conformance fixtures and refutation conditions extended for format symmetry and fixed grids.


<a id="org18e40ab"></a>

## v0.1.0 (2026-09-11)

First draft. Presets with CGA 40 by 25 as the default, formal query parameters with reject-not-clamp, the lease-based wire protocol with `rgb24` and `idx4` frames, numeric limits with refutation conditions, the `tool.display` gate and three experiments, and NR-CRDT with its trigger.
