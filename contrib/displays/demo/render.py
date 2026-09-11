"""Draw a display the way it looks.

Every cell is drawn with its preset's aspect (cell width over height) and gap
(the masonry between windows, as a fraction of a cell), in the palette's
colours through the level rule.  The output is an index image, which becomes
RGB, a PNG, an animated GIF, or ANSI half blocks.

Standard library only.  The jail's venv has neither pygame nor Pillow, and
nothing is installed from the network.  The PNG writer re-tells
docs/media/render_snapshots.py's (zlib, filter 0); the GIF writer is a plain
GIF89a LZW encoder.
"""
import struct
import zlib
from dataclasses import dataclass

from contract import display_contract as dc

MASONRY = "#1C1C1C"   # the gap and the border: never lit, unlike index 0 (black)
MASONRY_INDEX = 16


@dataclass(frozen=True)
class Geometry:
    w: int      # cells
    h: int
    cw: int     # pixels per cell, the gap included
    ch: int
    gx: int     # gap pixels per cell
    gy: int

    @property
    def size(self):
        return self.w * self.cw, self.h * self.ch


def geometry(w, h, aspect=1.0, gap=0.0, cell_h=12):
    """Cells CELL_H pixels tall and round(cell_h * aspect) wide, of which
    round(gap * size) pixels, split around the lit window, are masonry; a
    window always keeps at least one lit pixel."""
    ch = max(1, int(cell_h))
    cw = max(1, round(ch * aspect))
    return Geometry(w, h, cw, ch, min(cw - 1, round(gap * cw)), min(ch - 1, round(gap * ch)))


def fit(w, h, aspect=1.0, gap=0.0, max_w=256, max_h=256, largest=24):
    """The geometry with the tallest cells (at most LARGEST px) that fits in
    MAX_W x MAX_H pixels; one-pixel cells if nothing larger fits."""
    for ch in range(largest, 0, -1):
        g = geometry(w, h, aspect, gap, ch)
        if g.size[0] <= max_w and g.size[1] <= max_h:
            return g
    return geometry(w, h, aspect, gap, 1)


def index_image(cells, g):
    """W*H colour-table indices, row-major: each cell's palette index on its
    lit window, MASONRY_INDEX on the gap."""
    m = bytes([MASONRY_INDEX])
    left, right, top = g.gx // 2, g.gx - g.gx // 2, g.gy // 2
    lit_w, lit_h = g.cw - g.gx, g.ch - g.gy
    blank = m * g.size[0]
    out = bytearray()
    for y in range(g.h):
        row = cells[y * g.w:(y + 1) * g.w]
        line = b"".join(m * left + bytes([c]) * lit_w + m * right for c in row)
        out += blank * top + line * lit_h + blank * (g.gy - top)
    return bytes(out)


def colour_table(palette):
    """17 colours: the 16 a sink draws for indices 0..15 (the level rule
    applied to PALETTE, a name or a list), then the masonry."""
    return dc.colour_table(palette) + [MASONRY]


def rgb_image(idx, table):
    rgb = [bytes(dc.hex_rgb(c)) for c in table]
    return b"".join(rgb[i] for i in idx)


# ------------------------------------------------------------------ PNG

def png_bytes(w, h, rgb):
    """An 8-bit RGB PNG, filter 0, zlib level 9."""
    raw = b"".join(b"\x00" + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def png_pixels(data):
    """(w, h, rgb) of a PNG png_bytes wrote."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat, w, h = 8, b"", None, None
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw, stride = zlib.decompress(idat), w * 3 + 1
    if any(raw[y * stride] for y in range(h)):
        raise ValueError("a PNG filter other than 0")
    return w, h, b"".join(raw[y * stride + 1:(y + 1) * stride] for y in range(h))


# ------------------------------------------------------------------ GIF

def lzw(data, min_size):
    """GIF LZW: variable-width codes packed LSB first, a clear code first,
    the table cleared when it reaches 4096 codes."""
    clear, eoi = 1 << min_size, (1 << min_size) + 1
    size, nxt, table = min_size + 1, eoi + 1, {}
    out, acc, nbits = bytearray(), 0, 0

    def emit(code):
        nonlocal acc, nbits
        acc |= code << nbits
        nbits += size
        while nbits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8

    emit(clear)
    prefix = None
    for b in data:
        if prefix is None:
            prefix = b
            continue
        code = table.get((prefix, b))
        if code is not None:
            prefix = code
            continue
        emit(prefix)
        if nxt < 4096:
            table[(prefix, b)] = nxt
            if nxt == 1 << size and size < 12:
                size += 1
            nxt += 1
        else:
            emit(clear)
            size, nxt, table = min_size + 1, eoi + 1, {}
        prefix = b
    if prefix is not None:
        emit(prefix)
    emit(eoi)
    if nbits:
        out.append(acc & 0xFF)
    return bytes(out)


def gif_bytes(w, h, frames, table, delay_cs=3, loop=0):
    """An animated GIF89a: FRAMES are W*H index images into TABLE (at most
    256 colours), each shown DELAY_CS hundredths of a second, looping LOOP
    times (0: forever)."""
    bits = max(2, (len(table) - 1).bit_length())
    colours = [dc.hex_rgb(c) for c in table] + [(0, 0, 0)] * ((1 << bits) - len(table))
    out = bytearray(b"GIF89a" + struct.pack("<HHBBB", w, h, 0x80 | (bits - 1) << 4 | (bits - 1),
                                            0, 0))
    out += b"".join(bytes(c) for c in colours)
    out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01" + struct.pack("<H", loop) + b"\x00"
    for f in frames:
        out += b"\x21\xf9\x04\x00" + struct.pack("<H", delay_cs) + b"\x00\x00"
        out += b"\x2c" + struct.pack("<HHHHB", 0, 0, w, h, 0) + bytes([bits])
        data = lzw(f, bits)
        for i in range(0, len(data), 255):
            out += bytes([len(data[i:i + 255])]) + data[i:i + 255]
        out += b"\x00"
    return bytes(out + b"\x3b")


# ------------------------------------------------------------------ ANSI

def ansi(idx, g, table):
    """The index image as ANSI truecolor upper half blocks: one character per
    pixel column, two pixel rows per line (so a pixel is about square)."""
    rgb = [dc.hex_rgb(c) for c in table]
    wpx, hpx = g.size
    lines = []
    for y in range(0, hpx, 2):
        row = []
        for x in range(wpx):
            top = rgb[idx[y * wpx + x]]
            bottom = rgb[idx[(y + 1) * wpx + x]] if y + 1 < hpx else (0, 0, 0)
            row.append("\x1b[38;2;{};{};{}m\x1b[48;2;{};{};{}m▀".format(*top, *bottom))
        lines.append("".join(row) + "\x1b[0m")
    return "\n".join(lines)
