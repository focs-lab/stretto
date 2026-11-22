#!/usr/bin/env python3
"""Which cells of the sweep predict a known bug, by RULE.md rule 11; run after every cell is analysed.

usage: match.py SWEEP.csv [GRID.csv ...]
A cell predicts a bug when an L1 finding's receive lies in the bug's function (the sites column,
experiments/slate/finding_sites.py): SLATE's slate::internal::permuteRows in a getrf_tntpiv cell,
DLA-Future's receive inside dlaf::matrix::copy (by the rule, a name that carries copy; added after
the run, the sender adaptor's receives in the redistribution miniapp, whose only point-to-point
messages are copy's), PaRSEC's parsec_mpi_sendrecv. Prints, per bug, the cells that predict
it with their axis values, the share of the project's cells and of its analysed cells, and, per
project, the cells with L1 findings elsewhere (to be examined).
"""
import csv
import re
import sys

BUGS = {
    "slate": ("SLATE CALU tags", lambda cell, fn: cell.startswith("getrf_tntpiv") and "permuteRows" in fn),
    "dlaf": ("DLA-Future copy tags", lambda cell, fn: "matrix::copy" in fn or "copy<" in fn
             # Added after the run (README, "Matching"): the one-frame site of every DLA-Future
             # receive is the sender adaptor dlaf::comm::internal::recv, not its caller, and the
             # redistribution miniapp's only point-to-point messages are dlaf::matrix::copy's
             # (miniapp_redistribution.cpp:101-119).
             or (cell.startswith("miniapp_redistribution") and "dlaf::comm::internal::recv" in fn)),
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
        analysed = [r for r in mine if r["stretto_rc"] in ("0", "1")]   # 1: findings; 124: over budget
        pred, other = [], []
        for r in mine:
            at_bug = [(c, n, fn) for c, n, fn in sites(r) if hit(r["cell"], fn)]
            elsewhere = [(c, n, fn) for c, n, fn in sites(r) if not hit(r["cell"], fn)]
            if at_bug:
                pred.append((r, at_bug))
            if elsewhere:
                other.append((r, elsewhere))
        print(f"== {name}: {len(pred)} of {len(mine)} cells ({len(analysed)} analysed within the budget)")
        for r, f in pred:
            a = axes.get((project, r["cell"]), {})
            print(f"  {r['cell']} outcome {r['outcome']} pending {r['pending_pairs']} "
                  f"vpmap {a.get('vpmap', '')} findings {sum(n for _, n, _ in f)} {sorted({c for c, _, _ in f})}")
        warn = [(r, f) for r, f in other if all(fn == "-" for _, _, fn in f)]
        print(f"  L1 findings elsewhere: {len(other)} cells, {len(warn)} of them only warnings at no "
              f"receive ({sorted({c for _, f in warn for c, _, _ in f})})")
        for r, f in other:
            if (r, f) in warn:
                continue
            print(f"    {r['cell']} outcome {r['outcome']}: " + "; ".join(f"{c} x{n} in {fn}" for c, n, fn in f))


if __name__ == "__main__":
    main()
