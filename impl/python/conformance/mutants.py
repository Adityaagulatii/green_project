#!/usr/bin/env python3
"""Deliberately wrong engines, used to verify the verifier.

    mutants.py MUTANT TRACE.json [...]

Behaves like the reference driver (tetris_engine.conformance) but first
patches one rule of the Python engine. bin/verify.sh requires the runner
to FAIL on every mutant: a gate that passes a wrong engine is broken.
"""

import os
import sys
from dataclasses import replace
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "engine"))

from tetris_engine import (  # noqa: E402
    conformance,  # noqa: E402
    core,
    frame,
)
from tetris_engine import tables as T  # noqa: E402


def ccw_release_keeps_dcd():
    """Drop QUIRK-3."""
    original = core._rotate

    def patched(s, kind, down):
        if kind == "ccw" and not down and not s.ccw_available:
            return replace(s, ccw_available=True)
        return original(s, kind, down)
    core._rotate = patched


def gravity_ceil_cadence():
    """Drop QUIRK-8's binary64 rounding: drop every ceil(den/2) frames
    (24 at level 0 instead of 25). Note: exact 2/den Fractions would *not*
    do, since float + Fraction is float and fl(1/48)*2 == fl(2/48)."""
    core.gravity_increment = lambda level: float(
        Fraction(2, T.GRAVITY_DEN[min(level, 29)])) + 1e-12


def level_target_plus_six():
    core.level_target = lambda level: level + 6


def standard_180_kicks():
    """Drop QUIRK-1: use KICKS for the 180 source as well."""
    T.KICKS_180 = T.KICKS


def no_ghost():
    original = frame.compose
    frame.compose = lambda board, piece, ghost=True: original(board, piece, False)


MUTANTS = {
    "ccw-release-keeps-dcd": ccw_release_keeps_dcd,
    "gravity-ceil-cadence": gravity_ceil_cadence,
    "level-target-plus-six": level_target_plus_six,
    "standard-180-kicks": standard_180_kicks,
    "no-ghost": no_ghost,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in MUTANTS:
        names = "|".join(MUTANTS)
        print(f"usage: mutants.py {{{names}}} TRACE...", file=sys.stderr)
        return 2
    MUTANTS[argv[0]]()
    return conformance.main(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
