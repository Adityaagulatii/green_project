#!/usr/bin/env python3
"""Contract-v1 wire shim over the v0 Python server (experiment 007).

    PYTHONPATH=impl/python/engine:impl/python/sim \\
        python experiments/007-contract-v1-conformance/v1_shim.py \\
        --mode engine --clock lockstep --port 1709

NOT a deliverable, and never listed in bin/verify.sh's SERVERS. The Python
server belongs to the python workstream (impl/python/sim), which lands
contract v1 natively. This shim only lets spec/protocol/check.py be run
against a v1 wire before then. It patches exactly the three wire changes of
contract v1 that a v0 server lacks (docs/PROTOCOL.md §12):

  1. protocol version 1;
  2. client_role in the server hello;
  3. events (E_k, as passed to step k) on every engine frame.
"""

import sys

from tetris_sim import protocol as P
from tetris_sim import server as S

P.VERSION = 1

_send = S.Client.send


def send(self, msg):
    if msg.get("type") == "hello" and msg.get("role") == "server":
        msg = dict(msg, client_role=self.role)
    return _send(self, msg)


_step = S.EngineServer.step


def step(self, s):
    self._events = [[a, d] for a, d in s.pending]
    return _step(self, s)


_broadcast = S._Server.broadcast


def broadcast(self, msg, roles):
    if msg.get("type") == "frame" and self.mode == "engine":
        msg = dict(msg, events=getattr(self, "_events", []))
    return _broadcast(self, msg, roles)


S.Client.send = send
S.EngineServer.step = step
S._Server.broadcast = broadcast

if __name__ == "__main__":
    sys.exit(S.main())
