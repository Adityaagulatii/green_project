"""Simulator: recorder, building model, ANSI/HTML renderers, bot, CLI,
Animation interface, legacy Display adapter and the lazy desktop viewer.
All headless."""

import io
import json
import re
import shutil
import subprocess
import sys

import pytest

from legacy_harness import load_legacy
from tetris_engine import core, render
from tetris_engine.adapter import run, to_legacy_frame
from tetris_engine.animation import TetrisAnimation
from tetris_engine.conformance import run_trace
from tetris_sim import ansi, cli
from tetris_sim.animations import ANIMATIONS
from tetris_sim.bot import Bot
from tetris_sim.building import Building
from tetris_sim.html import render_html
from tetris_sim.recorder import Recorder, normalize, record


def test_recorder_records_frames_times_and_events():
    rec, state = record(TetrisAnimation(7), 300, Bot())
    assert len(rec) == 300 and rec.times[30] == 1.0
    assert state.phase in core.PHASES
    trace = {"seed": 7, "frames": 300, "digest_every": 1, "events": rec.events}
    assert run_trace(trace)["digests"] == rec.digests()  # a run is a trace


def test_recorder_clamps_and_validates():
    frame = [[(300, -5, 12.7)] * 9 for _ in range(17)]
    assert normalize(frame)[0][0] == (255, 0, 12)
    with pytest.raises(ValueError):
        Recorder().send([[(0, 0, 0)] * 9] * 16)


def test_building_model():
    b = Building()
    assert len(b.windows()) == 153 and b.provisional
    assert b.window(0, 0).floor == 20 and b.window(16, 8) == \
        type(b.window(0, 0))(16, 8, 4, 9)
    assert b.lit_floors() == list(range(20, 3, -1))
    with pytest.raises(ValueError):
        Building(rows=10)
    with pytest.raises(IndexError):
        b.window(17, 0)


def test_ansi_renderer():
    frame = render(core.step(core.new_game(1)))
    text = ansi.frame_to_ansi(frame)
    assert text.count("\n") == 16 and "48;2;255;255;255m" in text
    out = io.StringIO()
    ansi.play([frame, frame], out=out, sleep=lambda _t: None)
    assert out.getvalue().count("\x1b[H") == 1  # unchanged frame not redrawn


def _decode(html):
    blob = re.search(r'<script id="data" type="application/json">(.*?)</script>',
                     html, re.S).group(1)
    data = json.loads(blob)
    pal, frames = data["palette"], []
    for codes, count, _hud in data["runs"]:
        f = tuple(tuple(tuple(pal[int(codes[r * 9 + c], 36)]) for c in range(9))
                  for r in range(17))
        frames.extend([f] * count)
    return data, frames


def test_html_is_self_contained_and_round_trips():
    rec, _ = record(TetrisAnimation(42), 400, Bot())
    html = render_html(rec.frames, rec.info, meta={"seed": 42})
    assert html.count("<html") == 1 and "<canvas" in html
    assert not re.search(r'(?:src|href)\s*=\s*["\']?(?:https?:)?//', html)
    assert "fetch(" not in html and "import(" not in html
    data, frames = _decode(html)
    assert frames == rec.frames and data["building"]["provisional"]


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_html_script_parses(tmp_path):
    rec, _ = record(TetrisAnimation(1), 60)
    html = render_html(rec.frames, rec.info)
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    path = tmp_path / "replay.js"
    path.write_text(js)
    subprocess.run(["node", "--check", str(path)], check=True)


def test_cli_html_and_trace_out(tmp_path, capsys):
    html, trace = tmp_path / "out.html", tmp_path / "t.json"
    assert cli.main(["--seed", "42", "--bot", "--frames", "240", "--html",
                     str(html), "--trace-out", str(trace)]) == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["phase"] in core.PHASES
    t = json.loads(trace.read_text())
    assert run_trace(t)["digests"] == t["digests"]
    assert html.read_text().startswith("<!doctype html>")


def test_cli_replays_a_trace(tmp_path, capsys):
    trace = tmp_path / "t.json"
    cli.main(["--seed", "5", "--bot-fast", "--frames", "200", "--trace-out",
              str(trace)])
    first = capsys.readouterr().out
    cli.main(["--trace", str(trace)])
    assert capsys.readouterr().out == first


@pytest.mark.parametrize("name", sorted(ANIMATIONS))
def test_other_animations_produce_valid_frames(name):
    rec, _ = record(ANIMATIONS[name](), 120)
    assert len({f for f in rec.frames}) > 1


def test_bot_clears_lines():
    s = core.new_game(3)
    bot = Bot()
    for _ in range(1500):
        s = core.step(s, bot(s))
    assert s.score > 0 or s.high_score > 0


def test_adapter_drives_a_legacy_display():
    load_legacy()
    display_mod = sys.modules["utilities.display"]
    sent = []

    class Sink(display_mod.Display):
        def send(self, frame):
            sent.append(tuple(tuple((c.r, c.g, c.b) for c in frame.row(r))
                              for r in range(frame.nrows())))

        def makeframe(self):
            return display_mod.Frame()

    anim = TetrisAnimation(3)
    final = run(Sink(), anim, 150, controller=Bot(), realtime=False)
    s, bot, expect = anim.init(), Bot(), []
    for _ in range(150):
        s = anim.tick(s, bot(s))
        expect.append(render(s))
    assert sent == expect and final == s
    assert to_legacy_frame(expect[0], Sink()).nrows() == 17


def test_viewer_is_lazy_and_fails_cleanly(monkeypatch):
    import tetris_sim.viewer as viewer
    assert "pygame" not in sys.modules or sys.modules["pygame"] is None \
        or hasattr(sys.modules["pygame"], "K_UP")
    monkeypatch.setitem(sys.modules, "pygame", None)  # "not installed"
    with pytest.raises(SystemExit, match="pygame"):
        viewer.run_viewer(frames=1)
