"""The simulator: geometry, the level rule in colour, PNG, GIF (with an LZW
decoder of its own, to check the encoder), ANSI, the CLI over all 12 presets,
the remote sink, and the committed gallery.

    cd contrib/displays && python -m pytest demo/test_sim.py
"""
import asyncio
import io
import pathlib
import random
import struct

import pytest
from contract import display_contract as dc

from demo import render, sim
from demo.__main__ import main
from demo.relay import PROFILES, Relay

GALLERY = pathlib.Path(__file__).resolve().parent / "media" / "gallery"


def lzw_decode(data, min_size):
    """A GIF LZW decoder, written apart from the encoder, to check it."""
    clear, eoi = 1 << min_size, (1 << min_size) + 1
    pos = acc = nbits = 0
    out, prev = bytearray(), None
    size, table = min_size + 1, {}

    def reset():
        return min_size + 1, {i: bytes([i]) for i in range(clear)}
    size, table = reset()
    while True:
        while nbits < size:
            acc |= data[pos] << nbits
            pos, nbits = pos + 1, nbits + 8
        code, acc, nbits = acc & ((1 << size) - 1), acc >> size, nbits - size
        if code == clear:
            size, table = reset()
            prev = None
            continue
        if code == eoi:
            return bytes(out)
        if prev is None:
            entry = table[code]
        else:
            entry = table[code] if code in table else table[prev] + table[prev][:1]
            table[len(table) + 2] = table[prev] + entry[:1]
            if len(table) + 2 == 1 << size and size < 12:
                size += 1
        out += entry
        prev = code


def gif_frames(data):
    """(w, h, colours, [index images]) of a GIF gif_bytes wrote."""
    assert data[:6] == b"GIF89a" and data[-1:] == b"\x3b"
    w, h, packed = struct.unpack("<HHB", data[6:11])
    n = 2 << (packed & 7)
    colours = [tuple(data[13 + 3 * i:16 + 3 * i]) for i in range(n)]
    pos, frames = 13 + 3 * n, []
    while data[pos] != 0x3B:
        if data[pos] == 0x21:                       # an extension: skip its sub-blocks
            pos += 2
            while data[pos]:
                pos += data[pos] + 1
            pos += 1
        else:
            assert data[pos] == 0x2C
            min_size, pos, body = data[pos + 10], pos + 11, b""
            while data[pos]:
                body += data[pos + 1:pos + 1 + data[pos]]
                pos += data[pos] + 1
            pos += 1
            frames.append(lzw_decode(body, min_size))
    return w, h, colours, frames


def test_geometry_follows_aspect_and_gap():
    g = render.geometry(9, 17, 1.5, 0.35, 12)
    assert (g.cw, g.ch, g.gx, g.gy, g.size) == (18, 12, 6, 4, (162, 204))
    assert render.geometry(64, 32, 1, 0.15, 4) == render.Geometry(64, 32, 4, 4, 1, 1)
    assert render.geometry(40, 25, 1.2, 0, 5).cw == 6        # CGA's 6:5 pixel
    for name, p in dc.presets().items():
        g = render.fit(p["w"], p["h"], p["aspect"], p["gap"], 256, 256)
        assert g.size[0] <= 256 and g.size[1] <= 256 and g.cw - g.gx >= 1, name


def test_index_image_puts_masonry_in_the_gap():
    g = render.geometry(3, 2, 1.5, 0.35, 8)                  # cw 12, gx 4; ch 8, gy 3
    cells = bytes([1, 2, 3, 4, 5, 6])
    idx = render.index_image(cells, g)
    wpx = g.size[0]
    assert len(idx) == g.size[0] * g.size[1]
    for c in cells:
        assert idx.count(c) == (g.cw - g.gx) * (g.ch - g.gy)
    assert idx.count(render.MASONRY_INDEX) == len(idx) - 6 * (g.cw - g.gx) * (g.ch - g.gy)
    assert idx[0] == render.MASONRY_INDEX and idx[wpx * 1 + 2] == 1  # top-left window at (2, 1)


@pytest.mark.parametrize("name", list(PROFILES))
def test_every_preset_draws_its_levels(name):
    """bars shows every index; a short palette shows only its levels."""
    s = list(sim.local_states(name, "bars", 1))[-1]
    table = render.colour_table(s["palette"])
    g = render.fit(s["w"], s["h"], *sim.looks(name))
    drawn = {table[i] for i in set(render.index_image(s["cells"], g))} - {render.MASONRY}
    p = dc.preset(name)
    want = p["levels"] if p["w"] >= 16 else len({dc.level(c, p["levels"]) for c in s["cells"]})
    assert len(drawn) == want and drawn <= set(dc.palette(p["palette"]))


def test_png_round_trip():
    rnd = random.Random(1)
    rgb = bytes(rnd.randrange(256) for _ in range(7 * 5 * 3))
    assert render.png_pixels(render.png_bytes(7, 5, rgb)) == (7, 5, rgb)


@pytest.mark.parametrize("seed", range(4))
def test_lzw_round_trip(seed):
    rnd = random.Random(seed)
    data = bytes(rnd.randrange(17) for _ in range(20000)) + bytes([3]) * 30000 + bytes(
        rnd.choice((0, 16)) for _ in range(20000))
    for min_size in (2, 5, 8):
        sub = bytes(b % (1 << min_size) for b in data)
        assert lzw_decode(render.lzw(sub, min_size), min_size) == sub


def test_gif_holds_every_frame():
    states = list(sim.local_states("dc32", "fishbowl", 5))
    g = render.fit(10, 18, 1, 0)
    frames = [render.index_image(s["cells"], g) for s in states]
    table = render.colour_table(states[-1]["palette"])
    w, h, colours, got = gif_frames(render.gif_bytes(*g.size, frames, table))
    assert (w, h) == g.size and got == frames
    assert colours[:17] == [dc.hex_rgb(c) for c in table]


def test_ansi_is_half_blocks():
    g = render.geometry(9, 17, 1.5, 0.35, 3)
    text = render.ansi(render.index_image(bytes(153), g), g, render.colour_table("cga"))
    lines = text.split("\n")
    assert len(lines) == (g.size[1] + 1) // 2 and lines[0].count("▀") == g.size[0]


def test_sim_cli_on_every_preset(tmp_path, capsys):
    assert main(["sim", "-d", "all", "fishbowl", "--frames", "3", "--png", str(tmp_path),
                 "--gif", str(tmp_path), "--no-ansi"]) == 0
    for name in PROFILES:
        png, gif = tmp_path / f"{name}-fishbowl.png", tmp_path / f"{name}-fishbowl.gif"
        w, h, _ = render.png_pixels(png.read_bytes())
        assert len(gif_frames(gif.read_bytes())[3]) == 3 and w <= 256 and h <= 256
    assert capsys.readouterr().out.count('"display"') == len(PROFILES)


def test_sim_ansi_mode():
    out = io.StringIO()
    done = sim.simulate_local("blinkenlights", "matrix", 2, out=out, pace=False)
    assert done["frames"] == 2 and out.getvalue().count("blinkenlights 18x8") == 2


def test_the_simulator_is_a_remote_sink(tmp_path):
    """--url: the simulator views the mock relay; its picture is the local one."""
    async def go():
        async with Relay().serve() as url:
            from demo import source
            watcher = asyncio.create_task(sim.simulate_remote(
                url, "dc32", 2, ansi=False, png=tmp_path, gif=tmp_path))
            await asyncio.sleep(0.2)
            await source.run(url, "dc32", "bars", frames=2, fps=20)
            return await asyncio.wait_for(watcher, 5)
    done = asyncio.run(asyncio.wait_for(go(), 20))
    local = sim.simulate_local("dc32", "bars", 1, ansi=False, png=tmp_path / "local")
    assert done["frames"] == 2
    assert render.png_pixels(pathlib.Path(done["png"]).read_bytes()) == render.png_pixels(
        pathlib.Path(local["png"]).read_bytes())


@pytest.mark.parametrize("name", list(PROFILES))
def test_the_gallery_is_reproducible(name):
    """demo/media/gallery/<preset>.png is `sim -d all bars --frames 1 --png ...`."""
    s = list(sim.local_states(name, "bars", 1))[-1]
    g = render.fit(s["w"], s["h"], *sim.looks(name))
    rgb = render.rgb_image(render.index_image(s["cells"], g), render.colour_table(s["palette"]))
    assert render.png_pixels((GALLERY / f"{name}.png").read_bytes()) == (*g.size, rgb)
