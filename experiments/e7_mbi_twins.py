#!/usr/bin/env python3
"""E7: the value skeleton on MBI. For every MBI program with a hand-written L2 twin
(experiments/mbi/twins.py), analyse its kept trace with the twin and report the classes per
level: the payload-dependent races that L1 only flags (1f) and the single-path explorer
cannot certify become L2 findings (the program aborts on its own check under the alternative).
Writes experiments/results/e7.md (what experiments/tables.py includes).
usage: e7_mbi_twins.py [results/mbi/mbi.csv]"""
from __future__ import annotations
import csv
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "mbi"))
from stretto import report, trace  # noqa: E402
import twins  # noqa: E402


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "experiments", "results", "mbi", "mbi.csv")
    rows = list(csv.DictReader(open(path)))
    out = ["| program | label | observed run | L1 | L1+SP | L2 (twin) | L2 findings |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        if twins.twin_for(r["name"], int(r["np"])) is None:
            continue
        td = os.path.join(os.path.dirname(os.path.abspath(path)), r["name"], "trace")
        if not os.path.isdir(td):
            out.append(f"| {r['name']} | {r['expect']} | {r['run']} | (no trace) | | | |")
            continue
        t = trace.load(td)
        tag = next(iter(t.sends.values())).tag if t.sends else 0
        rep = report.analyze(t, twin=twins.twin_for(r["name"], int(r["np"]), tag))
        by = {lvl: sorted({f.cls for f in rep.findings if f.level == lvl}) for lvl in ("L1", "L1+SP", "L2")}
        l2 = [f for f in rep.findings if f.level == "L2"]
        out.append(f"| {r['name']} | {r['expect']} {r['detail']} | {r['run']} | {' '.join(by['L1']) or '-'} | {' '.join(by['L1+SP']) or '-'} | "
                   f"{' '.join(by['L2']) or '-'} | {'; '.join(sorted({f.title.split(' after ')[0] for f in l2}))} |")
    text = "\n".join(out) + "\n"
    print(text)
    with open(os.path.join(ROOT, "experiments", "results", "e7.md"), "w") as fh:
        fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
