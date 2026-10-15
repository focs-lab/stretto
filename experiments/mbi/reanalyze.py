#!/usr/bin/env python3
"""Re-run the analysis on the traces a harness run left behind and refresh the Rematch
columns of its table (classes, findings, notes, report files). Run results, replay
confirmations and MUST verdicts are kept. Use after a change to the analysis that does not
need new runs.   usage: reanalyze.py experiments/results/<corpus>/<corpus>.csv"""
import csv
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from rematch import report, trace  # noqa: E402

path = sys.argv[1]
base = os.path.dirname(os.path.abspath(path))
reader = csv.DictReader(open(path))
rows = list(reader)
keys = list(reader.fieldnames)
if "levels" not in keys:          # tables written before the column existed
    keys.insert(keys.index("classes") + 1, "levels")
changed = 0
for r in rows:
    out = os.path.join(base, r["name"])
    td = os.path.join(out, "trace")
    if r.get("build") == "CE" or not os.path.isdir(td):
        continue
    try:
        t = trace.load(td)
    except FileNotFoundError:
        continue
    rep = report.analyze(t, with_sp=True, sp_max_events=600, max_states=20000)
    classes = " ".join(rep.classes())
    if classes != r.get("classes", ""):
        changed += 1
        print(f"{r['name']:<60} {r.get('classes', '')!r} -> {classes!r}")
    r["classes"], r["findings"] = classes, len(rep.findings)
    r["levels"] = " ".join(rep.levels())
    r["notes"] = "; ".join(rep.notes)[:300]
    with open(os.path.join(out, "report.txt"), "w") as fh:
        fh.write(report.format_report(t, rep, show_infeasible=True))
    with open(os.path.join(out, "report.json"), "w") as fh:
        json.dump([f.__dict__ for f in rep.findings], fh, default=str, indent=1)
with open(path, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=keys, restval="", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
print(f"{changed} of {len(rows)} rows changed classes; wrote {path}")
