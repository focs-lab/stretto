#!/usr/bin/env python3
"""Characterise elph_prt tables printed by ph.x against a reference table.

For every spin block s of a table, say where its data came from in the reference:
  - its enk column (et2(:,1,s)) and its enk+q column (et2(:,2,s)): which of the reference's
    k1, q1, k2, q2 (enk / enk+q of spin 1 / spin 2) it equals, to the printed digits;
  - its |g| column (unsymmetrised, from el_ph_mat2(:,:,s,:) and the frequencies only; gauge-
    dependent, so exact only against a table of the same pool layout) or, with --col gs, its
    |g_sym| column (gauge-invariant, but averaged over states degenerate in its own enk): which
    reference spin block it equals, by the largest relative difference.
A table whose spin s has e=(ks,qs) and g=s for both spins is "correct". The verdict names the
others, e.g. "g-swapped" (|g| of the two spins exchanged, eigenvalues right), "spins-swapped"
(everything exchanged), or "mixed:<s1>|<s2>" spelled out.

usage: elphtab.py --ref REF.out [--tol 1e-6] [--col g|gs] FILE.out ...   (one line per file)
       elphtab.py --compare A.out B.out                         (largest differences per column)
"""
from __future__ import annotations
import argparse
import re
import sys

ROW = re.compile(r"^\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


def parse(path: str):
    """{spin: {"k": {ibnd: enk}, "q": {jbnd: enk+q}, "g": {(i,j,nu): |g|}, "gs": {...: |g_sym|}, "w": {nu: omega}}}"""
    with open(path, errors="replace") as fh:
        return parse_text(fh.read())


def parse_text(text: str):
    """parse() of a run's output given as text."""
    t = {}
    for line in text.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        i, j, s, nu = map(int, m.group(1, 2, 3, 4))
        ek, eq, w, gs, g = float(m[5]), float(m[6]), float(m[7]), float(m[8]), float(m[9])
        b = t.setdefault(s, {"k": {}, "q": {}, "g": {}, "gs": {}, "w": {}})
        b["k"][i] = ek
        b["q"][j] = eq
        b["g"][(i, j, nu)] = g
        b["gs"][(i, j, nu)] = gs
        b["w"][nu] = w
    return t


def rel(a: dict, b: dict) -> float:
    if a.keys() != b.keys():
        return float("inf")
    return max((abs(a[x] - b[x]) / max(abs(b[x]), 1e-3) for x in a), default=0.0)


def characterise(t, ref, tol: float, col: str = "g"):
    if not t:
        return "no-table", ""
    if sorted(t) != sorted(ref):
        return f"spins:{sorted(t)}", ""
    names = {}
    for s in ref:
        names[f"k{s}"] = ref[s]["k"]
        names[f"q{s}"] = ref[s]["q"]

    def which_e(v):
        hits = [n for n, r in names.items() if r.keys() == v.keys() and all(abs(v[x] - r[x]) < 5e-5 for x in v)]
        return "=".join(hits) if hits else "?"

    parts, detail, correct = [], [], True
    for s in sorted(t):
        ek, eq = which_e(t[s]["k"]), which_e(t[s]["q"])
        errs = {r: rel(t[s][col], ref[r][col]) for r in ref}
        best = min(errs, key=errs.get)
        g = str(best) if errs[best] <= tol else f"?{best}"
        parts.append((ek, eq, g))
        detail.append(f"s{s}: e=({ek},{eq}) g={g} ({col} rel.diff to spin " +
                      ", ".join(f"{r}: {errs[r]:.2e}" for r in sorted(errs)) + ")")
        if not (ek == f"k{s}" and eq == f"q{s}" and g == str(s)):
            correct = False
    if correct:
        v = "correct"
    elif len(parts) == 2 and parts[0] == ("k1", "q1", "2") and parts[1] == ("k2", "q2", "1"):
        v = "g-swapped"
    elif len(parts) == 2 and parts[0] == ("k2", "q2", "2") and parts[1] == ("k1", "q1", "1"):
        v = "spins-swapped"
    else:
        v = "mixed:" + "|".join(f"e=({a},{b}),g={c}" for a, b, c in parts)
    return v, "; ".join(detail)


def compare(a, b):
    out = []
    for col in ("k", "q", "w", "gs", "g"):
        m = 0.0
        for s in a:
            if s in b:
                m = max(m, rel(a[s][col], b[s][col]))
        out.append(f"{col}: {m:.2e}")
    return ", ".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref")
    ap.add_argument("--tol", type=float, default=1e-6)
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--col", default="g", choices=["g", "gs"], help="the column that identifies the matrix elements: |g| (exact, same pool layout only) or |g_sym| (gauge-invariant, with a tolerance)")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    if a.compare:
        x, y = parse(a.files[0]), parse(a.files[1])
        print(f"{a.files[0]} vs {a.files[1]}: largest relative difference per column: {compare(x, y)}")
        return 0
    ref = parse(a.ref)
    for f in a.files:
        v, d = characterise(parse(f), ref, a.tol, a.col)
        print(f"{f}\t{v}\t{d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
