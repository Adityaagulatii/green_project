#!/usr/bin/env python3
"""
d54_sim.py - a pygame simulator for the MIT Green Building display (Building 54).

Two things are simulated here:

  1. The DISPLAY: 9 wide x 17 tall = 153 pixels, 24-bit colour, 15 fps.
     Drawn as the south facade of the building.

  2. The ARCADE PROTOCOL from edu.mit.d54.ArcadeController:
     a TCP server on port 12345. The game is the SERVER; the physical
     controller box is the CLIENT. The client sends one ASCII byte per
     button press ('U' 'D' 'L' 'R') and receives one ASCII byte per LED
     command (uppercase = on, lowercase = off).

     That means an ESP32/Pico W breadboard controller does NOT need to
     know anything about the display. It only needs to open a socket,
     write 4 possible bytes, and read back 8 possible bytes.

Modes
-----
  python3 d54_sim.py                  run the built-in demo plugin
  python3 d54_sim.py --frames 12346   render frames pushed in on port 12346
                                      (153 * 3 raw RGB bytes per frame),
                                      e.g. from a Java DisplayListener

Local play: arrow keys act as a virtual controller. An external client on
port 12345 works at the same time.

Requires: pip install pygame
"""

import argparse
import random
import socket
import struct
import sys
import threading
import time

WIDTH = 9
HEIGHT = 17
NUM_PIXELS = WIDTH * HEIGHT          # 153
FRAMERATE = 15                       # what the real wireless link sustained
ARCADE_PORT = 12345

# Window geometry. The real windows are wider than they are tall relative to
# their spacing, so the facade is not a square grid of squares.
CELL_W, CELL_H = 46, 34
GAP_X, GAP_Y = 14, 20
MARGIN = 34
FACADE = (26, 32, 30)
UNLIT = (16, 20, 19)
FRAME = (44, 52, 48)


# ---------------------------------------------------------------------------
# Display2D equivalent
# ---------------------------------------------------------------------------

class Display:
    """Mirror of edu.mit.d54.Display2D: a mutable RGB pixel buffer."""

    def __init__(self, width=WIDTH, height=HEIGHT):
        self.width = width
        self.height = height
        self._px = [(0, 0, 0)] * (width * height)
        self._lock = threading.Lock()

    def clear(self):
        with self._lock:
            self._px = [(0, 0, 0)] * (self.width * self.height)

    def set_pixel(self, x, y, rgb):
        if 0 <= x < self.width and 0 <= y < self.height:
            with self._lock:
                self._px[y * self.width + x] = rgb

    def get_pixel(self, x, y):
        with self._lock:
            return self._px[y * self.width + x]

    def snapshot(self):
        with self._lock:
            return list(self._px)

    def load_raw(self, data):
        """Accept NUM_PIXELS * 3 bytes of row-major RGB."""
        if len(data) != self.width * self.height * 3:
            return False
        px = [tuple(data[i:i + 3]) for i in range(0, len(data), 3)]
        with self._lock:
            self._px = px
        return True


# ---------------------------------------------------------------------------
# ArcadeController equivalent
# ---------------------------------------------------------------------------

class ArcadeController(threading.Thread):
    """
    Port of edu.mit.d54.ArcadeController.

    Differences from the Java original, all deliberate:

      * The Java version holds the object monitor across accept() and
        read(), so setLED() blocks until a controller connects and sends
        something. Here the socket state has its own lock and the writer
        never waits on the reader.

      * set_leds() uses bitmasks. The Java setLEDs() uses `leds % 8`,
        `% 4`, `% 2`, `% 1` where it clearly meant `& 8`, `& 4`, `& 2`,
        `& 1`. `leds % 1` is always 0, so the R lamp can never light.
    """

    BUTTONS = {ord('U'): 'U', ord('D'): 'D', ord('L'): 'L', ord('R'): 'R'}

    def __init__(self, listener=None, port=ARCADE_PORT):
        super().__init__(daemon=True)
        self.listener = listener
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(('0.0.0.0', port))
        self._srv.listen(1)
        self._sock = None
        self._sock_lock = threading.Lock()
        self._stop = threading.Event()
        self.port = port

    @property
    def connected(self):
        with self._sock_lock:
            return self._sock is not None

    def run(self):
        while not self._stop.is_set():
            try:
                sock, addr = self._srv.accept()
            except OSError:
                return
            with self._sock_lock:
                self._sock = sock
            print(f"controller connected from {addr[0]}")
            try:
                while not self._stop.is_set():
                    b = sock.recv(1)
                    if not b:
                        break
                    name = self.BUTTONS.get(b[0])
                    if name and self.listener:
                        self.listener(name)
            except OSError:
                pass
            finally:
                print("controller disconnected")
                self._cleanup()

    def _cleanup(self):
        with self._sock_lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def set_led(self, ch):
        """Send one raw LED byte, matching Java's setLED(byte)."""
        with self._sock_lock:
            if self._sock is None:
                return
            try:
                self._sock.sendall(bytes([ord(ch)]))
            except OSError:
                pass

    def set_leds(self, mask):
        """
        Bit 3 = up, bit 2 = down, bit 1 = left, bit 0 = right.
        This is what the Java setLEDs() was reaching for.
        """
        for bit, on, off in ((8, 'U', 'u'), (4, 'D', 'd'),
                             (2, 'L', 'l'), (1, 'R', 'r')):
            self.set_led(on if mask & bit else off)

    def shutdown(self):
        self._stop.set()
        self._cleanup()
        try:
            self._srv.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Optional frame input: let the real Java code drive this window
# ---------------------------------------------------------------------------

class FrameServer(threading.Thread):
    """
    Listens for length-prefixed frames so the original Java plugins can drive
    this renderer. Wire protocol, deliberately trivial:

        uint32 big-endian length, then that many bytes of row-major RGB
        (153 * 3 = 459 for a stock GBDisplay).

    On the Java side, implement DisplayListener, read getBufferedImage()
    from the Display2D you are handed, and write the pixels out in that
    shape. Check the method name in DisplayListener.java before you build.
    """

    def __init__(self, display, port):
        super().__init__(daemon=True)
        self.display = display
        self.port = port
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(('0.0.0.0', port))
        self._srv.listen(1)
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            try:
                sock, addr = self._srv.accept()
            except OSError:
                return
            print(f"frame source connected from {addr[0]}")
            try:
                while not self._stop.is_set():
                    header = self._recv_exact(sock, 4)
                    if header is None:
                        break
                    (n,) = struct.unpack('>I', header)
                    if n > 1 << 20:
                        break
                    payload = self._recv_exact(sock, n)
                    if payload is None:
                        break
                    self.display.load_raw(payload)
            except OSError:
                pass
            finally:
                print("frame source disconnected")
                sock.close()

    @staticmethod
    def _recv_exact(sock, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def shutdown(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Demo plugin: falling tetromino, enough to prove the input path works
# ---------------------------------------------------------------------------

SHAPES = [
    [(0, 0), (1, 0), (0, 1), (1, 1)],            # O
    [(0, 0), (0, 1), (0, 2), (0, 3)],            # I
    [(0, 0), (0, 1), (0, 2), (1, 2)],            # J
    [(1, 0), (1, 1), (1, 2), (0, 2)],            # L
    [(0, 0), (1, 0), (1, 1), (2, 1)],            # S
    [(1, 0), (2, 0), (0, 1), (1, 1)],            # Z
    [(0, 0), (1, 0), (2, 0), (1, 1)],            # T
]
COLOURS = [(255, 196, 0), (0, 210, 255), (60, 100, 255), (255, 130, 0),
           (60, 230, 90), (255, 60, 60), (200, 80, 255)]


class DemoPlugin:
    """Stand-in for MITrisPlugin. Same contract: loop() once per frame."""

    def __init__(self, display, controller):
        self.d = display
        self.ctl = controller
        self.board = {}
        self.tick = 0
        self.drop_every = FRAMERATE // 2
        self._spawn()

    def _spawn(self):
        i = random.randrange(len(SHAPES))
        self.shape = SHAPES[i]
        self.colour = COLOURS[i]
        self.px, self.py = WIDTH // 2 - 1, 0
        if self._hits(self.px, self.py, self.shape):
            self.board.clear()

    def _cells(self, ox, oy, shape):
        return [(ox + x, oy + y) for x, y in shape]

    def _hits(self, ox, oy, shape):
        for cx, cy in self._cells(ox, oy, shape):
            if cx < 0 or cx >= WIDTH or cy >= HEIGHT or (cx, cy) in self.board:
                return True
        return False

    def _rotate(self):
        h = max(y for _, y in self.shape)
        turned = [(h - y, x) for x, y in self.shape]
        if not self._hits(self.px, self.py, turned):
            self.shape = turned

    def button(self, name):
        """This is the ArcadeListener.arcadeButton() callback."""
        if name == 'L' and not self._hits(self.px - 1, self.py, self.shape):
            self.px -= 1
        elif name == 'R' and not self._hits(self.px + 1, self.py, self.shape):
            self.px += 1
        elif name == 'D':
            self._fall()
        elif name == 'U':
            self._rotate()
        self._update_lamps()

    def _update_lamps(self):
        mask = 8 | 4
        if not self._hits(self.px - 1, self.py, self.shape):
            mask |= 2
        if not self._hits(self.px + 1, self.py, self.shape):
            mask |= 1
        self.ctl.set_leds(mask)

    def _fall(self):
        if self._hits(self.px, self.py + 1, self.shape):
            for c in self._cells(self.px, self.py, self.shape):
                if c[1] >= 0:
                    self.board[c] = self.colour
            self._clear_rows()
            self._spawn()
        else:
            self.py += 1

    def _clear_rows(self):
        full = [y for y in range(HEIGHT)
                if all((x, y) in self.board for x in range(WIDTH))]
        for y in full:
            for x in range(WIDTH):
                del self.board[(x, y)]
            above = {k: v for k, v in self.board.items() if k[1] < y}
            for (cx, cy) in sorted(above, key=lambda k: -k[1]):
                self.board[(cx, cy + 1)] = self.board.pop((cx, cy))

    def loop(self):
        self.tick += 1
        if self.tick % self.drop_every == 0:
            self._fall()
        self.d.clear()
        for (x, y), col in self.board.items():
            self.d.set_pixel(x, y, col)
        for x, y in self._cells(self.px, self.py, self.shape):
            self.d.set_pixel(x, y, self.colour)


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def run(use_frames_port=None):
    import pygame

    pygame.init()
    w = MARGIN * 2 + WIDTH * CELL_W + (WIDTH - 1) * GAP_X
    h = MARGIN * 2 + HEIGHT * CELL_H + (HEIGHT - 1) * GAP_Y + 30
    screen = pygame.display.set_mode((w, h))
    pygame.display.set_caption("Green Building display - 9 x 17")
    font = pygame.font.SysFont(None, 20)
    clock = pygame.time.Clock()

    display = Display()
    plugin = None
    frames = None

    controller = ArcadeController()
    if use_frames_port:
        frames = FrameServer(display, use_frames_port)
        frames.start()
        controller.listener = lambda name: print(f"button {name}")
    else:
        plugin = DemoPlugin(display, controller)
        controller.listener = plugin.button
    controller.start()

    keymap = {pygame.K_UP: 'U', pygame.K_DOWN: 'D',
              pygame.K_LEFT: 'L', pygame.K_RIGHT: 'R'}

    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key in keymap and controller.listener:
                    controller.listener(keymap[ev.key])

        if plugin:
            plugin.loop()

        px = display.snapshot()
        screen.fill(FACADE)
        for y in range(HEIGHT):
            for x in range(WIDTH):
                rx = MARGIN + x * (CELL_W + GAP_X)
                ry = MARGIN + y * (CELL_H + GAP_Y)
                rect = pygame.Rect(rx, ry, CELL_W, CELL_H)
                col = px[y * WIDTH + x]
                pygame.draw.rect(screen, col if any(col) else UNLIT, rect)
                pygame.draw.rect(screen, FRAME, rect, 1)

        src = "frame source" if use_frames_port else "demo plugin"
        state = "controller connected" if controller.connected else \
                f"listening on :{controller.port}"
        label = font.render(f"{src} - {state} - arrows play locally",
                            True, (150, 160, 155))
        screen.blit(label, (MARGIN, h - 24))
        pygame.display.flip()
        clock.tick(FRAMERATE)

    controller.shutdown()
    if frames:
        frames.shutdown()
    pygame.quit()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--frames', type=int, metavar='PORT',
                    help='listen for external RGB frames instead of running '
                         'the demo plugin')
    args = ap.parse_args()
    try:
        run(args.frames)
    except ImportError:
        print("pygame is not installed. Try: pip install pygame",
              file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
