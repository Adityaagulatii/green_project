#!/usr/bin/env python3
"""Drive a real program in a real pseudo-terminal, typing like a person.

    python docs/media/pty_drive.py repl          # python -i, engine session
    python docs/media/pty_drive.py bsd-tetris    # contrib/bsd-tetris-mit build

This is what ``asciinema rec -c`` runs for the REPL and BSD casts (see
``docs/media/record_casts.sh``). Nothing is faked: the child runs on the
slave side of a new pty (stdlib ``pty``), sized like our own terminal. We
wait for its real prompt, write keystrokes one at a time to the master
side, and copy everything the child prints (prompts, echo, ANSI frames)
to stdout, where asciinema records it. Standard library only (no pexpect).
"""

import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import fcntl
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


class Session:
    def __init__(self, argv, env=None, cwd=None):
        try:
            cols, rows = os.get_terminal_size(sys.stdout.fileno())
        except OSError:
            cols, rows = 100, 32
        pid, fd = pty.fork()
        if pid == 0:  # child: stdin/stdout/stderr are the pty slave
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            if cwd:
                os.chdir(cwd)
            os.execvpe(argv[0], argv, env or os.environ)
        self.pid, self.fd, self.seen = pid, fd, b""
        self.out = sys.stdout.buffer

    def pump(self, seconds):
        """Copy child output to stdout for ``seconds``; False once it exits."""
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return True
            r, _, _ = select.select([self.fd], [], [], left)
            if not r:
                continue
            try:
                data = os.read(self.fd, 4096)
            except OSError:  # EIO: slave closed, child gone
                return False
            if not data:
                return False
            self.out.write(data)
            self.out.flush()
            self.seen += data

    def expect(self, pattern, timeout=30.0):
        rx = re.compile(pattern.encode() if isinstance(pattern, str) else pattern)
        end = time.monotonic() + timeout
        while not rx.search(self.seen):
            if time.monotonic() > end:
                raise TimeoutError("waiting for %r" % pattern)
            if not self.pump(0.05):
                raise EOFError("child exited while waiting for %r" % pattern)
        m = rx.search(self.seen)
        self.seen = self.seen[m.end():]
        return m

    def type(self, text, cps=22.0, enter=True):
        """Type ``text`` a keystroke at a time (the tty echoes it back)."""
        for ch in text + ("\r" if enter else ""):
            os.write(self.fd, ch.encode())
            self.pump(1.0 / cps)

    def wait(self, timeout=60.0):
        end = time.monotonic() + timeout
        while self.pump(0.1):
            if time.monotonic() > end:
                os.kill(self.pid, signal.SIGTERM)
                break
        _, status = os.waitpid(self.pid, 0)
        return os.waitstatus_to_exitcode(status)


# ------------------------------------------------------------- scenarios

PROMPT = r">>> $"
MORE = r"\.\.\. $"

REPL_LINES = [
    ("from tetris_engine import new_game, step, render, frame_digest", 0.4),
    ("from tetris_sim.ansi import frame_to_ansi", 0.4),
    ("s = new_game(seed=42)       # SPEC 9.1: pure state, boot countdown first", 0.6),
    ("s = step(s, [])             # frame 0", 0.4),
    ("s.phase, s.timer", 1.2),
    ("print(frame_to_ansi(render(s)))", 2.5),
    ("for k in range(1, 91): s = step(s, [])", None),   # continuation line
    ("frame_digest(render(s))     # frame 90 is all black: SPEC Appendix A", 2.0),
    ("s = step(s, [])             # frame 91: first logical play frame", 0.4),
    ("s.phase, s.active", 1.5),
    ("s = step(s, [('rotate_cw', True), ('rotate_cw', False)])", 0.4),
    ("s = step(s, [('left', True), ('left', True)])", 0.4),
    ("print(frame_to_ansi(render(s)))", 2.5),
    ("s = step(s, [('hard_drop', True), ('hard_drop', False)])", 0.4),
    ("print(frame_to_ansi(render(s)))", 2.5),
    ("s.score, s.spawns, s.active.shape", 1.5),
    ("frame_digest(render(s))", 2.5),
]


def repl():
    env = dict(os.environ)
    env.pop("PYTHONSTARTUP", None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "impl/python/engine"), str(ROOT / "impl/python/sim")])
    env["PYTHON_BASIC_REPL"] = "1"  # 3.13+: plain REPL; ignored by 3.12
    sess = Session([sys.executable, "-q", "-i"], env=env, cwd=str(ROOT))
    for line, pause in REPL_LINES:
        sess.expect(PROMPT)
        sess.pump(0.5)
        sess.type(line)
        if pause is None:  # a compound statement: close it with an empty line
            sess.expect(MORE)
            sess.pump(0.4)
            sess.type("")
        else:
            sess.pump(pause)
    sess.expect(PROMPT)
    sess.pump(0.8)
    os.write(sess.fd, b"\x04")  # ^D ends the REPL
    return sess.wait(10)


def bsd_tetris():
    work = ROOT / "contrib/bsd-tetris-mit/work"
    binary = work / "tetris-mit"
    if not binary.exists():
        sys.exit("build it first: contrib/bsd-tetris-mit/build.sh")
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HOME=str(home), TERM=os.environ.get("TERM", "xterm-256color"))
    # Unattended: no keys at all. Pieces fall from the centre until one no
    # longer fits; then tetris(6) asks for RETURN before the score table.
    sess = Session([str(binary), "-p", "-l", "6"], env=env)
    sess.expect(r"Hit RETURN", timeout=240)
    sess.pump(1.5)
    sess.type("", enter=True)
    return sess.wait(15)


SCENARIOS = {"repl": repl, "bsd-tetris": bsd_tetris}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in SCENARIOS:
        sys.exit("usage: pty_drive.py {%s}" % "|".join(SCENARIOS))
    sys.exit(SCENARIOS[sys.argv[1]]())
