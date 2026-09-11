# Display contract kit: wal.sh/tools/display v0.2.1

The user's published display spec, pinned, turned into a kit that any implementation can check itself against. The Clojure core, the Emacs source and sink, and the reservation gatekeeper all consume the same kit, and so does this repo's demo relay.

| path | what |
|---|---|
| `wal-sh-display-0.2.1/` | **The oracle:** `spec.md` and `capabilities.json` byte for byte, with `PROVENANCE` (URL, date, sha256). Nothing here is edited. |
| `schemas/*.json` | One JSON Schema (draft 2020-12) per control message: `view`, `reserve`, `renew`, `release` to the relay; `caps`, `lease`, `granted`, `busy`, `error` from it. There is also `session-record.json`, and `index.json` lists the ops, reasons and formats. |
| `fixtures/*.json` | The spec's "Conformance fixtures" section as data (see below). |
| `display_contract.py` | The Python reference. It is pure and uses only the standard library. |
| `gen_fixtures.py` | Writes `fixtures/` from the reference. `--check` reports drift. |
| `check_session.py` | Checks a recorded relay session (JSONL). |
| `relay_conformance.py` | A pytest suite that any relay must pass on loopback. |
| `test_*.py`, `conftest.py` | Tests of all of the above, and the conformance options. |

Nothing in the kit connects to wal.sh. Everything runs on loopback.

## Running it

```sh
cd contrib/displays
PY=/scratch/venvs/tetris-py/bin/python
$PY -m pytest -q -p no:cacheprovider contract               # the kit, plus conformance against the demo relay
$PY -m contract.gen_fixtures --check                         # the fixtures match the reference
$PY -m contract.check_session session.jsonl [--seq-rule serial]
HYPOTHESIS_PROFILE=thorough nice -n 10 lockf -k -t 7200 /scratch/locks/heavy.lock \
  timeout 3600 $PY -m pytest -q -p no:cacheprovider contract/test_display_contract.py
```

## Using it from another language

- **Schemas.** Validate every control message with any draft 2020-12 validator.
  - Every schema has `additionalProperties: true`, so unknown fields are ignored. The gatekeeper's `busy` adds `by` and `reason`, and that stays valid.
  - The five error reasons and the formats are enums.
  - `w*h <= 65536` cannot be expressed in JSON Schema. It can never bind on its own anyway (see the Limits notes below).
- **Fixtures.** Each file is `{"spec", "description", ..., "cases": [...]}`, with one case per line.
  - **Frames** are written as `{"bytes": [ints]}` or `{"text": "..."}`. A frame over 256 long, when run-length encoding it is at least four times shorter, is written as `{"bytes_rle": [[byte, count], ...]}` or `{"text_rle": [[char, count], ...]}` instead.
  - **States** carry `cells` (a list) or `cells_rle`.
  - **Events** come in two forms:
    - control messages, which are objects with `op`;
    - local events, `{"event": "open"|"close"|"tick"|"frame"}`. A `tick` carries `now` (unix seconds). A `frame` carries one of the frame keys above.

| file | cases |
|---|---|
| `caps.json` | `caps` for every preset on an unfixed grid (the state after it, including the cells length); `caps` of every preset against a grid the URL fixed at 40x25 (status `error`, `error.reason` `bad-src`); `remote` on a fixed 9x17 grid; a `hex` caps. |
| `frames.json` | Frames by preset and format (pal16, hex, rgb24), and at the grid boundaries (1x1, 256x1, 1x256, 256x256). See the breakdown below this table. |
| `equivalence.json` | The same indices as pal16 (with a prefix) and as hex (some with the blank line), decoding to equal cells. |
| `sequence.json` | Relay-side prefixes and whether each is accepted, for the `literal` and `serial` rules: a reordered prefix, an equal one, 0, 65535, the wrap, no prefix after prefixed frames, and half the sequence space ahead. |
| `expiry.json` | The viewer's fold through `lease holder:null` and the black frame, and the state (the DOM) after it; also a `tick` at `expires` before the relay's lease arrives. |
| `fold.json` | The Reduction contract table, row by row. It has event sequences with the state after each event and `dirty` after each, a hex sink, a fixed-grid refusal, a short palette, `seq` monotonicity, and garbage events. |
| `levels.json` | The level rule for every palette size from 2 to 16 (idx 0, 1 and 15 called out), n = 0, 1 and 17 as errors, and every named palette with its `palette16`. |
| `quantize.json` | rgb24 quantization per palette: exact entries, every true midpoint tie (ties go to the lower index), extremes, and random colours. |
| `grid.json` | `check_grid` at 0, 1, 256 and 257, and at 65,537 cells, for non-integer values, and the legal frame lengths per grid. |
| `interop.json` | BLP and MCUF packets: maxval 1, 2 (a half), 7, 15 and 255; one and three channels; which display each goes to; and every malformed header. |
| `messages.json` | Messages against the schemas (ttl 0, 1, 900, 901, -1, 1.5, 1.0 and "10"; the formats; the reasons; 16-colour palettes). For a message to the relay, the reason the relay answers. |

**Frame cases in `frames.json`:**
- **By preset and format:** one valid frame; each wrong length, one off either way. That is w\*h±1 and w\*h+3 for pal16 (w\*h and w\*h+2 are the legal lengths), and h(w+1)−1 and h(w+1)+2 for hex.
- **Bad content:** an empty frame; a pal16 byte of 15 (valid), 16 or 255; a hex `f` (valid), `g`, upper-case `F`, CRLF line ends, or a misplaced LF.
- **The largest frames:** 65,538 bytes for pal16 and 65,793 for hex.

Replaying a fixture file needs only JSON. `test_display_contract.py` has a replay function per file, which is the pattern to copy, and a test that each replay fails on a tampered file.

## Python reference (`display_contract.py`)

| area | functions |
|---|---|
| the pin | `capabilities()`, `presets()`, `preset_order()` (parsed from the spec's Presets table), `palette(name)`, `check_grid(w, h)` |
| levels and colour | `level(idx, n)`, `palette16(colours)`, `colour_table(palette)`, `quantize(rgb, pal16)` |
| frames | `decode_pal16` and `encode_pal16`, `decode_hex` and `encode_hex`, `decode_rgb24` and `encode_rgb24`, `decode_source_frame` (the relay's rule), `fanout_frame`, `black_frame` |
| interop | `parse_interop`, `interop_cells`, `encode_blp`, `encode_mcuf`, `udp_display(w, h)` |
| sequence | `seq_accepts(last, seq, rule)`, with rules `literal` and `serial` |
| messages | `validate(instance, schema)` (a small draft 2020-12 subset; unknown keywords raise), `check_message(msg)` |
| the fold | `initial_state`, `reduce_event(state, event)` (total), `dirty(state, prev)`, `check_invariants` |

The spec's invariants are hypothesis properties in `test_display_contract.py`:
- `reduce_event` is total over arbitrary JSON values and malformed events;
- the cell count is `w*h` and every cell is 0..15;
- `seq` never falls;
- a dropped frame changes no cell, and a drop changes nothing but `dropped`;
- `dirty` applied to the previous state gives the new one;
- decode is format-symmetric;
- the level rule is monotone and keeps lit cells lit;
- `quantize` is the brute-force nearest, with ties going low;
- an rgb24 round trip renders alike;
- the `serial` rule is RFC 1982.

## check_session.py

A session is JSONL, one record per message the relay received (`in`) or sent (`out`): `{"t", "conn", "dir", "kind": open|close|text|binary|udp|meta, "payload"}` (`schemas/session-record.json`). The demo relay writes one with `--record`, one line at a time. Its first record is `meta`: the relay's capabilities.json.

It checks the following, and exits 1 if there are any findings:
- every record and every message the relay sent is valid against its schema;
- every malformed, unknown or non-holder input gets the right error;
- there is one holder per display at a time, and `busy` names that holder;
- only frames the holder had accepted are fanned out, at the length `caps.format` gives;
- expiry sends `lease holder:null` and then an all-zero frame to every viewer, and not early;
- rate and sequence drops happen when they should, and not otherwise;
- no display has more than 32 viewers.

`test_check_session.py` records a live session with every rule in it, which passes. It then checks ten tampered copies of that session, and the checker fails each one.

## relay_conformance.py

```sh
$PY -m pytest contract/relay_conformance.py                                  # the demo relay, started for you
$PY -m pytest contract/relay_conformance.py --relay-url ws://127.0.0.1:8765/tools/display/ws --relay-udp 127.0.0.1:2323
$PY -m pytest contract/relay_conformance.py --relay-cmd "CMD that prints its ws:// URL"
$PY -m pytest contract/relay_conformance.py --source-url ws://127.0.0.1:9000/tools/display/ws \
                                            --viewer-url ws://127.0.0.1:8765/tools/display/ws   # behind a gatekeeper
```

It covers:
- `caps` for all 12 presets, with hub75 at 60 fps;
- the advertised default display;
- the grid limits;
- `granted`, `busy` and `not-holder`, and one holder per display;
- ttl 900 and 901 (901 is clamped or refused);
- expiry counted from the last frame or `renew`, then a black frame;
- release and close;
- every fixture frame on every preset and format, plus the four boundary grids;
- hex fan-out, and rgb24 ties;
- the sequence fixtures (`--seq-rule`);
- back-to-back drops without a backlog, frames at fps passing, and frames above fps dropped;
- `unknown-op`;
- the 32-viewer cap;
- no history for a late viewer;
- UDP BLP and MCUF, a 5 s UDP holder, and busy against it.

Two marks:
- `choice` marks this kit's readings of the spec where it is silent. `--skip-choices` skips them.
- `same_url` marks tests that need view and reserve on one URL. They skip when the source and viewer URLs differ, so a gatekeeper that fronts sources only (and answers `view` with unknown-op) still runs the rest.

Displays and the default come from the relay's own `capabilities.json` when it serves one. Otherwise they come from the pinned presets, with the default from `--relay-default`.

**Timing under load.** The "frames at fps pass" test allows one clean round out of three. With the jail at load average 7, two frames sent 25 ms apart sometimes arrived in one read at the relay, and no relay can tell that from a burst. An isolated run showed no drops: 35 of 35 frames passed, with relay-side gaps from 25.5 to 99 ms.

## Implementer choices (CHOICE in the code)

These are the readings this kit and the demo relay take where the spec is silent. Fixture cases tagged `choice`, and conformance tests marked `choice`, pin them.

1. **Error precedence:** a pal16 frame that is both the wrong length and holds a byte over 15 is `bad-frame-length`.
2. **Hex is positional:** with the length right, any digit or LF out of place is `bad-format`; with the length wrong, it is `bad-frame-length`. So CRLF is `bad-frame-length` when h > 1 and `bad-format` when h = 1. Upper-case `A-F` are accepted.
3. **Short palettes:** `caps` and `granted` announce a short palette as its 16-entry level expansion (`palette16`).
4. **Malformed control** gets `bad-format`: bad JSON, no `op`, a bad field, or an unknown display. An op the relay does not take (including `caps` sent to it) gets `unknown-op`.
5. **ttl:** an integer ≥ 1 (1.0 counts); anything else is `bad-format`. Values over 900 are clamped to 900. The default is 300.
6. **Sequence prefix:** `literal` is the default (seq ≥ last accepted), with `serial` (RFC 1982) as an option. A frame with no prefix is never dropped by sequence and leaves the last accepted value alone. A new lease resets the sequence.
7. **Rate:** a GCRA at the display's fps with a 20% jitter tolerance. Accepted frames are at least 0.8/fps apart, and the rate never exceeds fps over time. Only accepted frames renew the lease or count for the rate.
8. **The reserved format** only decides how binary is read (rgb24 or pal16). Hex text is accepted from any holder, and so are BLP and MCUF binary. A holder that reserves again is granted again, with the new format. The reservation gatekeeper relies on this to switch pal16 to hex, and the conformance suite pins it in `test_the_holder_may_reserve_again`, marked `choice`.
9. **Viewer cap:** the demo relay closes a 33rd viewer with WebSocket code 1013 ("try again later"). The spec names no code, so the conformance suite checks only that the 33rd viewer gets no `caps` and no frames, and accepts any close. babashka's http-kit can only close with 1000.
10. **`lease` is re-sent** to viewers whenever the whole-second `expires` changes, so a viewer's `tick` never marks a live display idle. Release sends `lease holder:null` but no black frame; only expiry sends the black frame.
11. **BLP and MCUF headers** are 12 bytes, big-endian: BLP is magic, frame count, width, height; MCUF is magic, height, width, channels, maxval.
    - A BLP value other than 0 or 1 is `bad-format`.
    - One-channel values scale to 0..15 with round half up (maxval 2: 1 → 8).
    - Three channels scale to 0..255, then quantize.
    - maxval 0 is `bad-format`, and so is a value over maxval.
12. **UDP display choice:** the first display with the packet's width and height in the spec's Presets table order, so tetris before c64, dc32 before gameboy, and green-building before remote. The UDP listener is off unless `--udp-port` is given. The spec's port is 2323.
13. **Fold:**
    - `:tick` idles when now ≥ expires.
    - A `caps` refused by a fixed grid changes only status and error.
    - A pal16 sink accepts the 2-byte prefix and ignores its value.
    - A sink on pal16 drops text frames, and a sink on hex drops binary ones.
14. **The repo default display is green-building.** This is the user's choice for this repo; the relay's `--default` changes it. The spec's default is cga40.

## Open questions for the user (the spec is silent or ambiguous)

1. **Sequence wrap.** Read literally, "lower than the last accepted is dropped" drops every prefixed frame after 65535 → 0 until the lease ends, which is 36 minutes at 30 fps. Should it be serial-number arithmetic? The spec itself also asks whether the prefix should be mandatory.
2. **The black frame after expiry.** The table sets status `:live` on any valid frame, so after `lease holder:null` and the black frame, the DOM shows `live`, not `idle` (`expiry.json`). Should a frame with no holder leave the status alone?
3. **ttl over 900:** clamp it or reject it? ttl 0, a negative ttl, or a non-integer: which error? And what is the default ttl?
4. **The 33rd viewer, an unknown display, malformed JSON:** none of the five reasons fits. This kit uses a 1013 close and `bad-format`.
5. **Hex:** upper case (the spec writes "0 to f"), CRLF, and whether length means bytes or characters when a non-ASCII character makes them differ.
6. **Short palettes in `caps`:** the spec shows `"palette":[16 hex]`. Should a relay send the level expansion (as here), or the short list with `levels`?
7. **The level rule with n = 1** maps lit indices to level 1, which does not exist. Is n ≥ 2 required?
8. **BLP and MCUF.** The header layouts are not in the pin. BLP values other than 0/1 are rejected here, though the wiki may treat any nonzero value as on. MCUF rounding is not specified (it matters for even maxval), and neither is the meaning of "scaled to 0..15" for three channels.
9. **Width × height ties** for UDP (tetris/c64, dc32/gameboy, green-building/remote): this kit takes the first in table order.
10. **Frame size.** rgb24 at 256×256 is 196,610 bytes, over `max.frameBytes` (65,538). Does the limit bind relay input, or only the sink's wire?
11. **Leases:**
    - Does a dropped frame renew the lease?
    - Should `lease` be re-sent on renewal?
    - Should release black out the display?
    - May a holder reserve again to change format (granted here, while the gatekeeper expects `busy`)?
12. **The cell cap never binds on its own:** 256² = 65,536, and 65,537 is prime, so only a dimension over 256 can reach it.
13. **`caps` against a fixed grid:**
    - Should a refused `caps` also withhold fps, format and palette (it does here)?
    - Should frames still fold after the refusal (they do here)?
14. **The expiry message has two shapes.** The Rules write it as `{"op":"lease","holder":null}`, while the Viewer section's `lease` carries display, holder and expires.
    - `schemas/lease.json` accepts both.
    - The relays here send the full form.
    - `reduce_event` folds either (`expiry.json` has the short form).
    - `check_session` resolves a short form to the one display its viewer watches.
    - The displays-cljc workstream found this.
15. **A text message that does not start with `{` is a hex frame** by the first-character rule. That includes a JSON value such as `[]` or `"view"`, so a non-holder gets `not-holder` for it (`messages.json`; found by displays-cljc).
