#!/usr/bin/env python3
"""Re-run the analysis on the traces a harness run left behind and refresh the Rematch
columns of its table (classes, findings, notes, report files). Run results, replay
confirmations and MUST verdicts are kept. Use after a change to the analysis that does not
need new runs.   usage: reanalyze.py [--sp-max-events N] [--max-states N] <corpus>.csv
The caps default to run_mbi.py's, so a re-analysis sees what a run would have seen."""
import argparse
import csv
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from rematch import report, trace  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("csv"); ap.add_argument("--sp-max-events", type=int, default=600); ap.add_argument("--max-states", type=int, default=30000)
args = ap.parse_args()
path = args.csv
base = os.path.dirname(os.path.abspath(path))
reader = csv.DictReader(open(path))
rows = list(reader)
keys = list(reader.fieldnames)
if "levels" not in keys:          # tables written before the column existed
    keys.insert(keys.index("classes") + 1, "levels")
if "l1_alternatives" not in keys:
    keys.insert(keys.index("classes"), "l1_alternatives")
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
    rep = report.analyze(t, with_sp=True, sp_max_events=args.sp_max_events, max_states=args.max_states)
    classes = " ".join(rep.classes())
    if classes != r.get("classes", ""):
        changed += 1
        print(f"{r['name']:<60} {r.get('classes', '')!r} -> {classes!r}")
    r["classes"], r["findings"] = classes, len(rep.findings)
    r["levels"] = " ".join(rep.levels())
    r["l1_alternatives"] = sum(1 for f in rep.findings if f.level == "L1" and f.family == "1")
    r["notes"] = "; ".join(rep.notes)[:300]
    with open(os.path.join(out, "report.txt"), "w") as fh:
        fh.write(report.format_report(t, rep, show_infeasible=True))
    with open(os.path.join(out, "report.json"), "w") as fh:
        json.dump(report.to_json(t, rep), fh, default=str, indent=1)
with open(path, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=keys, restval="", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
print(f"{changed} of {len(rows)} rows changed classes; wrote {path}")
