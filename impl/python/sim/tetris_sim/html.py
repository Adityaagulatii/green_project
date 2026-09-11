"""Self-contained HTML replay: one file, inline canvas JS, no network.

Frames are palette-indexed (one character per window) and run-length
encoded, so a 900-frame demo is a few tens of kilobytes.
"""

import json
import os

from .building import Building

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def encode(frames, info=None):
    """Return (palette, runs): runs are [codes, count, hud] with codes a
    153-char palette-index string."""
    palette, index, runs = [], {}, []
    info = info or [None] * len(frames)
    for frame, hud in zip(frames, info, strict=True):
        chars = []
        for row in frame:
            for rgb in row:
                k = index.get(rgb)
                if k is None:
                    if len(palette) == len(_ALPHABET):
                        raise ValueError("too many distinct colours")
                    k = index[rgb] = len(palette)
                    palette.append(list(rgb))
                chars.append(_ALPHABET[k])
        codes = "".join(chars)
        h = [hud["score"], hud["level"], hud["lines"], hud["high"],
             hud["phase"]] if hud else None
        if runs and runs[-1][0] == codes and runs[-1][2] == h:
            runs[-1][1] += 1
        else:
            runs.append([codes, 1, h])
    return palette, runs


def render_html(frames, info=None, meta=None, fps=30, building=None):
    building = building or Building()
    palette, runs = encode(frames, info)
    data = {"fps": fps, "palette": palette, "runs": runs,
            "meta": meta or {}, "building": building.as_dict()}
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA__", blob)


def write_html(path, frames, info=None, meta=None, fps=30, building=None):
    html = render_html(frames, info, meta, fps, building)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
    return path


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>17x9 Tetris: Green Building replay</title>
<style>
  :root { --bg:#0a0f1e; --panel:#121a2e; --fg:#e7ebf3; --muted:#8d97ad;
          --line:#26304a; --accent:#7fd4ff; }
  * { box-sizing: border-box; }
  html, body { margin:0; background:var(--bg); color:var(--fg);
    font:14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width:1000px; margin:0 auto; padding:18px;
    display:grid; grid-template-columns:minmax(0,1fr) 280px; gap:18px; }
  @media (max-width:760px) { main { grid-template-columns:1fr; } }
  .stage { background:#050811; border:1px solid var(--line);
    border-radius:10px; overflow:hidden; }
  canvas { display:block; width:100%; height:auto; }
  aside { background:var(--panel); border:1px solid var(--line);
    border-radius:10px; padding:16px; align-self:start; }
  h1 { font-size:17px; margin:0 0 4px; }
  .sub { color:var(--muted); margin:0 0 14px; font-size:12.5px; }
  .row { display:flex; gap:8px; align-items:center; margin:10px 0; flex-wrap:wrap; }
  button, select { background:#1b2540; color:var(--fg); border:1px solid var(--line);
    border-radius:7px; padding:6px 11px; font:inherit; cursor:pointer; }
  button:hover, select:hover { border-color:var(--accent); }
  input[type=range] { width:100%; accent-color:var(--accent); }
  dl { display:grid; grid-template-columns:auto 1fr; gap:4px 12px; margin:14px 0 0; }
  dt { color:var(--muted); } dd { margin:0; font-variant-numeric:tabular-nums; }
  .note { color:var(--muted); font-size:12px; margin-top:14px; }
  kbd { background:#1b2540; border:1px solid var(--line); border-radius:4px;
    padding:0 4px; font-size:11.5px; }
</style>
</head>
<body>
<main>
  <div class="stage"><canvas id="c" width="600" height="940"
    aria-label="Building facade replay"></canvas></div>
  <aside>
    <h1>17&times;9 Tetris replay</h1>
    <p class="sub" id="sub"></p>
    <div class="row">
      <button id="play">Pause</button>
      <button id="back" title="Step back">&#9664;</button>
      <button id="fwd" title="Step forward">&#9654;</button>
      <button id="restart">Restart</button>
    </div>
    <div class="row">
      <label for="speed">Speed</label>
      <select id="speed">
        <option value="0.25">0.25&times;</option><option value="0.5">0.5&times;</option>
        <option value="1" selected>1&times;</option><option value="2">2&times;</option>
        <option value="4">4&times;</option>
      </select>
      <label for="view">View</label>
      <select id="view"><option value="facade">Facade</option>
        <option value="grid">Grid</option></select>
    </div>
    <input type="range" id="scrub" min="0" value="0" aria-label="Frame">
    <dl>
      <dt>Frame</dt><dd id="fr"></dd>
      <dt>Time</dt><dd id="tm"></dd>
      <dt>Phase</dt><dd id="ph">&ndash;</dd>
      <dt>Score</dt><dd id="sc">&ndash;</dd>
      <dt>Level</dt><dd id="lv">&ndash;</dd>
      <dt>High</dt><dd id="hi">&ndash;</dd>
    </dl>
    <p class="note" id="note"></p>
    <p class="note"><kbd>Space</kbd> play/pause &middot; <kbd>&larr;</kbd>
      <kbd>&rarr;</kbd> step &middot; <kbd>Home</kbd> restart</p>
  </aside>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  "use strict";
  var D = JSON.parse(document.getElementById("data").textContent);
  var AL = "0123456789abcdefghijklmnopqrstuvwxyz";
  var ROWS = D.building.rows, COLS = D.building.cols;
  var runs = D.runs, starts = [], total = 0;
  for (var i = 0; i < runs.length; i++) { starts.push(total); total += runs[i][1]; }
  var pal = D.palette.map(function (p) { return p; });

  function runAt(f) {  // binary search: run containing frame f
    var lo = 0, hi = runs.length - 1;
    while (lo < hi) {
      var mid = (lo + hi + 1) >> 1;
      if (starts[mid] <= f) lo = mid; else hi = mid - 1;
    }
    return runs[lo];
  }

  var cv = document.getElementById("c"), cx = cv.getContext("2d");
  var W = cv.width, H = cv.height;
  var B = D.building, stories = B.stories;
  var towerW = 380, tx = (W - towerW) / 2, roofY = 110, groundY = H - 70;
  var storyH = (groundY - 50 - roofY) / stories, bayW = towerW / COLS;

  function rgb(p, a) { return "rgba(" + p[0] + "," + p[1] + "," + p[2] + "," + a + ")"; }
  function lum(p) { return p[0] + p[1] + p[2]; }

  function drawSky() {
    var g = cx.createLinearGradient(0, 0, 0, H);
    g.addColorStop(0, "#050914"); g.addColorStop(1, "#16213d");
    cx.fillStyle = g; cx.fillRect(0, 0, W, H);
    cx.fillStyle = "#0c1222"; cx.fillRect(0, groundY, W, H - groundY);
  }

  function drawTower() {
    // radome on the roof, the Green Building's landmark
    cx.fillStyle = "#cfd6e4";
    cx.beginPath(); cx.arc(W / 2, roofY - 8, 34, Math.PI, 0); cx.fill();
    cx.fillStyle = "#9aa4b8"; cx.fillRect(W / 2 - 40, roofY - 10, 80, 10);
    cx.fillStyle = "#3a4152"; cx.fillRect(tx - 8, roofY, towerW + 16, groundY - 50 - roofY);
    // open arcade at ground level
    cx.fillStyle = "#2b3140"; cx.fillRect(tx - 8, groundY - 50, towerW + 16, 8);
    for (var k = 0; k < 4; k++) {
      cx.fillRect(tx - 8 + k * (towerW + 4) / 3, groundY - 42, 12, 42);
    }
  }

  function drawFacade(codes) {
    drawSky(); drawTower();
    for (var s = 0; s < stories; s++) {  // s = 0 is the top story
      var floor = stories - s, r = B.top_floor - floor;
      for (var c = 0; c < COLS; c++) {
        var x = tx + c * bayW + 6, y = roofY + s * storyH + 6;
        var w = bayW - 12, h = storyH - 10;
        var p = null;
        if (r >= 0 && r < ROWS) p = pal[AL.indexOf(codes.charAt(r * COLS + c))];
        if (p && lum(p) > 0) {
          cx.shadowColor = rgb(p, 0.9); cx.shadowBlur = 16;
          cx.fillStyle = rgb(p, 1); cx.fillRect(x, y, w, h);
          cx.shadowBlur = 0;
        } else {
          cx.fillStyle = "#121827"; cx.fillRect(x, y, w, h);
        }
      }
    }
  }

  function drawGrid(codes) {
    cx.fillStyle = "#050811"; cx.fillRect(0, 0, W, H);
    var cell = Math.min((W - 40) / COLS, (H - 40) / ROWS);
    var ox = (W - cell * COLS) / 2, oy = (H - cell * ROWS) / 2;
    for (var r = 0; r < ROWS; r++) for (var c = 0; c < COLS; c++) {
      var p = pal[AL.indexOf(codes.charAt(r * COLS + c))];
      cx.fillStyle = rgb(p, 1);
      cx.fillRect(ox + c * cell + 1, oy + r * cell + 1, cell - 2, cell - 2);
    }
  }

  var el = function (id) { return document.getElementById(id); };
  var scrub = el("scrub"); scrub.max = String(Math.max(0, total - 1));
  var m = D.meta || {};
  el("sub").textContent = [m.title || "Replay", m.seed != null ? "seed " + m.seed : "",
    total + " frames @ " + D.fps + " fps"].filter(Boolean).join(" · ");
  el("note").textContent = B.provisional ?
    "Window mapping is provisional (TBD at the hack): row 0 = floor " + B.top_floor + "." : "";

  var frame = 0, playing = true, speed = 1, view = "facade", acc = 0, last = null;

  function show(f) {
    frame = Math.max(0, Math.min(total - 1, f));
    var run = runAt(frame);
    (view === "grid" ? drawGrid : drawFacade)(run[0]);
    scrub.value = String(frame);
    el("fr").textContent = frame + " / " + (total - 1);
    el("tm").textContent = (frame / D.fps).toFixed(2) + " s";
    var h = run[2];
    el("ph").textContent = h ? h[4] : "–";
    el("sc").textContent = h ? h[0] : "–";
    el("lv").textContent = h ? h[1] + " (" + h[2] + " lines)" : "–";
    el("hi").textContent = h ? h[3] : "–";
  }

  function tick(ts) {
    if (last === null) last = ts;
    var dt = (ts - last) / 1000; last = ts;
    if (playing && total > 0) {
      acc += dt * D.fps * speed;
      var n = Math.floor(acc);
      if (n > 0) {
        acc -= n;
        if (frame + n >= total) { show(total - 1); setPlaying(false); }
        else show(frame + n);
      }
    }
    requestAnimationFrame(tick);
  }

  function setPlaying(p) { playing = p; el("play").textContent = p ? "Pause" : "Play"; acc = 0; }
  el("play").onclick = function () {
    if (!playing && frame >= total - 1) show(0);
    setPlaying(!playing);
  };
  el("back").onclick = function () { setPlaying(false); show(frame - 1); };
  el("fwd").onclick = function () { setPlaying(false); show(frame + 1); };
  el("restart").onclick = function () { show(0); setPlaying(true); };
  el("speed").onchange = function (e) { speed = parseFloat(e.target.value); };
  el("view").onchange = function (e) { view = e.target.value; show(frame); };
  scrub.oninput = function () { setPlaying(false); show(parseInt(scrub.value, 10)); };
  document.addEventListener("keydown", function (e) {
    if (e.target && e.target.tagName === "SELECT") return;
    if (e.key === " ") { e.preventDefault(); el("play").onclick(); }
    else if (e.key === "ArrowLeft") el("back").onclick();
    else if (e.key === "ArrowRight") el("fwd").onclick();
    else if (e.key === "Home") el("restart").onclick();
  });

  if (total > 0) { show(0); requestAnimationFrame(tick); }
})();
</script>
</body>
</html>
"""
