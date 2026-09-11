# Spec seals

Each seal of [`SPEC.md`](../SPEC.md) gets one line below. The protocol is in [`docs/POLYGLOT-PLAN.md`](../docs/POLYGLOT-PLAN.md). Each line records:

- **version** and **language**: the spec version and the implementation that sealed it;
- **`sha256(SPEC.md)`**: `sha256sum SPEC.md`;
- **`sha256(traces)`**: the trace-set hash printed by `spec/conformance/run.py` on PASS (see `spec/conformance/README.md`);
- **engine commit**: the commit of the implementation that produced and passed the traces;
- **tag**: the annotated tag `spec-vN-<lang>`, kept local;
- **supported cell**: the `(toolchain, platform)` on which the gate PASS was observed.

| version | language | sha256(SPEC.md) | sha256(traces) | engine commit | tag | supported cell | gate |
|---|---|---|---|---|---|---|---|
| v3 | *pending*: the first of Clojure, Guile and Elisp whose rebuild passes the gate against the v3 text | | `981baab4…c2026` (unchanged) | | `spec-v3-<lang>` | | |
| v2 | Hy 1.3.1 | `294de89a7659e8c0182787f11fc72b013ae4858fecf53174c1150d6fc1280e90` | `981baab4279c5c9f93f42eda23f1eb9f2b0889b4c228637e2ac19b9a479c2026` (14 traces, unchanged since v1) | `5921857` | `spec-v2-hy` | FreeBSD 15.1-RELEASE amd64; Python 3.12.14, hy 1.3.1, hypothesis 6.165.10, pytest 9.1.1 (venv `/scratch/venvs/tetris-py`, `--system-site-packages`) | PASS, self-test ok (corrupted trace rejected through both drivers, 6 mutants rejected incl. the illegal-edge one, hand-edited Appendix A digest rejected); appendix PASS; python 14/14, hy 14/14; pytest thorough (`HYPOTHESIS_PROFILE=thorough TETRIS_SLOW=1`) 157 passed; legacy differential and Hy-vs-Python differential 0 divergences |
| v1 | Python 3.12 | `b45e4598bce5b61ac3b333b339f60805bf0d80ff334cf2c092652d7ff3eb88a5` | `981baab4279c5c9f93f42eda23f1eb9f2b0889b4c228637e2ac19b9a479c2026` (14 traces) | `32cc220` | `spec-v1-python` | FreeBSD 15.1-RELEASE amd64; Python 3.12.14, numpy 2.4.6, hy 1.3.1, hypothesis 6.165.10, pytest 9.1.1 (venv `/scratch/venvs/tetris-py`, `--system-site-packages`) | PASS, self-test ok (corrupted trace + 5 mutants rejected); pytest thorough green; legacy differential 0 divergences |

## Sequencing

The steward sequences engine seals **in order of gate PASS**: v2 was Hy, and v3, v4 and so on go to Clojure, Guile and Elisp, whichever passes the gate first against the current text. A rebuild's clarifications go into the next unsealed version's changelog in `SPEC.md`, whoever seals it. The steward prepares the seal commit and its note; the main session creates the tag and pushes.

# Contract seals

Each seal of the network contract ([`docs/PROTOCOL.md`](../docs/PROTOCOL.md) plus [`spec/protocol/`](protocol/)) gets one line below. Contract seals are versioned separately from SPEC, as `contract-vN`, and `N` is the protocol `version` on the wire. Each line records:

- **`sha256(PROTOCOL.md)`**: `sha256sum docs/PROTOCOL.md`;
- **`sha256(contract set)`**: the digest of `spec/protocol/schemas/*.schema.json` and `spec/protocol/transcripts/*.jsonl` that `spec/protocol/check.py` prints on PASS;
- **servers**: the servers that passed `check.py --server` over each binding they offer. At least the Python reference server is required;
- **clients**: the clients whose recorded session passed `check.py --session`. At least one of the Hy client and the Emacs client is required;
- **tag**: the annotated tag `contract-vN`, kept local and created by the main session;
- **supported cell**: where the PASS was observed.

| contract | sha256(PROTOCOL.md) | sha256(contract set) | servers | clients | tag | supported cell | gate |
|---|---|---|---|---|---|---|---|
| v1 | `6ac5ec0fa20144b06432190aefaaefe25ef4c2bc1149db1bda3e18a009b0fe51` | `e45524faf46512ddd3e2bd53e4d1dec957aba7fdd374447b979cb320e3da7c4d` | Python reference (`impl/python-ws` 676088b): `python` over tcp 21/21, `python-ws` over ws 21/21 | Emacs (`feat/emacs-client` 397dde7): `check.py --session --require-kav` 14/14 on a KAV replay of all 14 traces through `proxy.py --record`, reproduced by the steward from `git archive 397dde7` (recording sha256 prefix `9225246d`) | `contract-v1` | FreeBSD 15.1 amd64 jail, Python 3.12.14, websockets 17.1, Emacs 31.1 | `SERVERS="python python-ws" bin/verify.sh` GATE: PASS at fb816c1, 2026-09-11 09:02Z |
