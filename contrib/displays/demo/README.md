# Display protocol demo

A small, standalone test of the display binding: the user's `wal.sh/tools/display` protocol, cited in docs/PROTOCOL.md §5.4. It has three parts:
- `relay.py`, a **local mock relay**. It serves every display profile, each with its own lease.
- `source.py`, a **source**. It reserves a display and plays a demo on it: `bars`, `matrix`, `fishbowl` or `tetris`.
- `view.py`, a **viewer**. It draws the frames in the terminal in ANSI truecolor.

The demos in `producers.py` yield `w*h` CGA palette indices for any grid. The source expands them into the wire frame, `w*h*3` bytes of RGB. `tetris` here is a self-playing game on the display's own field: a mock game, not the 17×9 SPEC engine.

It needs Python 3.12 and `websockets` (the project venv has both). Everything stays on loopback. The source and the viewer refuse any other host, `wss://wal.sh` included.

```sh
cd contrib/displays
PY=/scratch/venvs/tetris-py/bin/python

$PY -m demo list                               # profiles and demos
$PY -m demo run matrix -d ws2812               # relay + source + viewer, 10 s
$PY -m demo run tetris -d hub75 --seconds 0    # forever; Ctrl-C to stop
$PY -m demo run fishbowl -d trs80 --seq --quiet   # no drawing, summary only

# the parts separately
$PY -m demo relay --port 8765 [--page path/to/display.html]
$PY -m demo view   -d green-building
$PY -m demo source fishbowl -d green-building --seconds 30

$PY -m pytest demo                             # the protocol tests
```

## Browser sink

With `--page`, the relay serves an HTML sink at `/tools/display/`. `?view=NAME` on the WebSocket URL subscribes on connect, so the canvas page's frame mode can view the display with no extra message. Its `?src=` parameter must be URL-encoded:

    http://127.0.0.1:8765/tools/display/?w=16&h=16&src=ws%3A%2F%2F127.0.0.1%3A8765%2Ftools%2Fdisplay%2Fws%3Fview%3Dws2812

## What the tests check

`test_demo.py` checks:
- the viewer's `caps` and `lease` messages;
- a `reserve` answered with `granted`, and one answered with `busy`;
- that frames from a non-holder, of the wrong length, or faster than `fps` are dropped, each with an `error`;
- that the 2-byte sequence prefix is stripped before fan-out;
- that `release`, and closing the socket, free the display;
- that on expiry the viewers get `lease` with holder `null` and then a black frame;
- that the ttl is capped at 900 s;
- the contents of `capabilities.json`;
- every demo, end to end, on the 9×17 and 64×32 profiles.

## Mock choices the protocol leaves open

- A frame is "too fast" if it comes within 0.8/fps of the last accepted frame.
- `release` sends the viewers only a `lease`, with no black frame; only expiry sends the black frame.
- Unknown ops and unknown displays get `error` reasons of their own.
- The schema of `capabilities.json` is our guess at the live one.
