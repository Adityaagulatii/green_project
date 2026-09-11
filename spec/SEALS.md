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
| v1 | Python 3.12 | `b45e4598bce5b61ac3b333b339f60805bf0d80ff334cf2c092652d7ff3eb88a5` | `981baab4279c5c9f93f42eda23f1eb9f2b0889b4c228637e2ac19b9a479c2026` (14 traces) | `32cc220` | `spec-v1-python` | FreeBSD 15.1-RELEASE amd64; Python 3.12.14, numpy 2.4.6, hy 1.3.1, hypothesis 6.165.10, pytest 9.1.1 (venv `/scratch/venvs/tetris-py`, `--system-site-packages`) | PASS, self-test ok (corrupted trace + 5 mutants rejected); pytest thorough green; legacy differential 0 divergences |
