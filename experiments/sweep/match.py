#!/usr/bin/env python3
"""Which cells of the sweep predict a known bug, by RULE.md rule 11; run after every cell is analysed.

usage: match.py SWEEP.csv [GRID.csv ...]
A cell predicts a bug when an L1 finding's receive lies in the bug's function (the sites column,
experiments/slate/finding_sites.py): SLATE's slate::internal::permuteRows in a getrf_tntpiv cell,
DLA-Future's receive inside dlaf::matrix::copy (its sender adaptors are templates instantiated for
copy, whose names carry it), PaRSEC's parsec_mpi_sendrecv. Prints, per bug, the cells that predict
it with their axis values, the share of the project's cells and of its analysed cells, and, per
project, the cells with L1 findings elsewhere (to be examined).
"""
import csv
import re
import sys

BUGS = {
    "slate": ("SLATE CALU tags", lambda cell, fn: cell.startswith("getrf_tntpiv") and "permuteRows" in fn),
    "dlaf": ("DLA-Future copy tags", lambda cell, fn: "matrix::copy" in fn or "copy<" in fn),
    "parsec": ("PaRSEC reshape tags", lambda cell, fn: "parsec_mpi_sendrecv" in fn),
}


def sites(row):
    for s in filter(None, row["sites"].split("|")):
        cls, n, rest = s.split(":", 2)
        fn = re.sub(r":\[[^]]*\]$", "", rest)
        yield cls, int(n), fn


def main():
    rows = list(csv.DictReader(open(sys.argv[1])))
    axes = {}
    for g in sys.argv[2:]:
        for r in csv.DictReader(open(g)):
            axes[(r["project"], r["cell"])] = r
    for project, (name, hit) in BUGS.items():
        mine = [r for r in rows if r["project"] == project]
        if not mine:
            continue
        analysed = [r for r in mine if r["stretto_rc"] == "0"]
        pred, other = [], []
        for r in mine:
            at_bug = [(c, n, fn) for c, n, fn in sites(r) if hit(r["cell"], fn)]
            elsewhere = [(c, n, fn) for c, n, fn in sites(r) if not hit(r["cell"], fn)]
            if at_bug:
                pred.append((r, at_bug))
            if elsewhere:
                other.append((r, elsewhere))
        print(f"== {name}: {len(pred)} of {len(mine)} cells ({len(analysed)} analysed completely or degraded)")
        for r, f in pred:
            a = axes.get((project, r["cell"]), {})
            print(f"  {r['cell']} outcome {r['outcome']} pending {r['pending_pairs']} "
                  f"vpmap {a.get('vpmap', '')} findings {sum(n for _, n, _ in f)} {sorted({c for c, _, _ in f})}")
        print(f"  L1 findings elsewhere: {len(other)} cells")
        for r, f in other:
            print(f"    {r['cell']} outcome {r['outcome']}: " + "; ".join(f"{c} x{n} in {fn}" for c, n, fn in f))


if __name__ == "__main__":
    main()
