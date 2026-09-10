#!/usr/bin/env python3
"""Frames from acc = 0 to the first drop: binary64 vs exact rationals."""

from fractions import Fraction

DEN = (48, 43, 38, 33, 28, 23, 18, 13, 8, 6, 5, 4, 3, 2, 1)

print("den binary64 rational")
for den in DEN:
    acc, n = 0.0, 0
    while acc < 1:
        acc += (1 / den) * 2  # legacy: self._gravity * 2
        n += 1
    exact, m = Fraction(0), 0
    while exact < 1:
        exact += Fraction(2, den)
        m += 1
    print(f"{den:3} {n:9} {m:8}{'   <- differs' if n != m else ''}")
