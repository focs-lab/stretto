#!/usr/bin/env python3
"""The rows of a ph.x elph_prt table (Quantum ESPRESSO, electron_phonon='prt') that differ from a
reference table: experiments/must/apps.sh (once.sh) and experiments/ninja/campaign.sh judge the
runs of the elph_prt race with it.

A row is (k band, k+q band, spin, mode): enk, enk+q, omega, |g_sym|, |g|, ... It differs when its
eigenvalues differ by more than 5e-5 eV (they are printed with four decimals) or its |g_sym|, the
gauge-invariant column, by more than 5e-3 relative (the reference may come from another pool
layout, which changes |g| and |g_sym|'s last digits); a row missing from either table counts.
When the race strikes, the two spins' eigenvalues or matrix elements are exchanged or mixed, and
most rows differ. Prints the count, or "no-reference" when the reference has no table.

usage: elph_rows.py RUN.out REFERENCE.out"""
import re
import sys

ROW = re.compile(r"^\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


def table(path: str) -> dict:
    t = {}
    with open(path, errors="replace") as fh:
        for line in fh:
            m = ROW.match(line)
            if m:
                t[tuple(map(int, m.group(1, 2, 3, 4)))] = (float(m[5]), float(m[6]), float(m[8]))
    return t


def differing(a: dict, b: dict) -> int:
    bad = sum(1 for k in b if k not in a or abs(a[k][0] - b[k][0]) > 5e-5 or abs(a[k][1] - b[k][1]) > 5e-5
              or abs(a[k][2] - b[k][2]) > 5e-3 * max(abs(b[k][2]), 1e-3))
    return bad + sum(1 for k in a if k not in b)


def main() -> int:
    run, ref = table(sys.argv[1]), table(sys.argv[2])
    print(differing(run, ref) if ref else "no-reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())
