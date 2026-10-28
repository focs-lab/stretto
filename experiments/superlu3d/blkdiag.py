#!/usr/bin/env python3
"""Write a block-diagonal Matrix Market matrix: k independent copies of a convection-diffusion
operator on an m x m grid (5-point stencil, upwinded, so the matrix is nonsymmetric and
diagonally dominant). Each copy gets its own coefficients, drawn from random.Random(seed + b),
so the blocks are not identical. usage: blkdiag.py m k out.mtx [seed]"""
import random
import sys

m, k, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1
ent = []
for b in range(k):
    r = random.Random(seed + b)
    cx, cy = r.uniform(0.0, 0.5), r.uniform(0.0, 0.5)     # convection, upwinded
    base = b * m * m
    for i in range(m):
        for j in range(m):
            row = base + i * m + j
            ent.append((row, row, 4.0 + cx + cy + r.uniform(0.0, 0.2)))
            if j > 0:
                ent.append((row, row - 1, -1.0 - cx))
            if j < m - 1:
                ent.append((row, row + 1, -1.0))
            if i > 0:
                ent.append((row, row - m, -1.0 - cy))
            if i < m - 1:
                ent.append((row, row + m, -1.0))
n = k * m * m
with open(out, "w") as fh:
    fh.write("%%MatrixMarket matrix coordinate real general\n")
    fh.write(f"% {k} independent {m}x{m} grid operators (blkdiag.py, seed {seed})\n")
    fh.write(f"{n} {n} {len(ent)}\n")
    for r_, c_, v in ent:
        fh.write(f"{r_ + 1} {c_ + 1} {v:.15g}\n")
