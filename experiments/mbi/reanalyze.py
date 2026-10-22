#!/usr/bin/env python3
"""Re-run the analysis on the traces a harness run left behind and refresh the Stretto
columns of its table (classes, findings, notes, report files). Run results, replay
confirmations and MUST verdicts are kept; the commit column keeps naming the run's commit,
and analysis_commit names the analysis's. Use after a change to the analysis that does not
need new runs.   usage: reanalyze.py [--sp-max-events N] [--max-states N] [--work DIR] [--reads DIR [--out CSV]] <corpus>.csv
The caps default to run_mbi.py's, so a re-analysis sees what a run would have seen.
--reads DIR analyses each program with its first-read map DIR/<name>.json (reads_maps.py,
docs/firstread.md); a program without a map is analysed without. The table then goes to
--out (default: reads.csv next to the maps) and each program's reports to report-reads.txt
and report-reads.json, so the run's own table and reports stay."""
import argparse
import csv
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from stretto import report, trace  # noqa: E402
from stretto.reads import SiteMap, addr2line_resolver  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from provenance import code_commit  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("csv"); ap.add_argument("--sp-max-events", type=int, default=600); ap.add_argument("--max-states", type=int, default=30000)
ap.add_argument("--work", default=None, help="the programs' directories, when the run kept them elsewhere (run_mbi.py --work)")
ap.add_argument("--reads", default=None, help="directory of first-read maps, one per program (reads_maps.py)")
ap.add_argument("--out", default=None, help="with --reads: the table to write (default: reads.csv in the maps' directory)")
args = ap.parse_args()
path = args.csv
base = os.path.abspath(args.work) if args.work else os.path.dirname(os.path.abspath(path))
out_path = (args.out or os.path.join(args.reads, "reads.csv")) if args.reads else path
suffix = "-reads" if args.reads else ""
reader = csv.DictReader(open(path))
rows = list(reader)
keys = list(reader.fieldnames)
if "levels" not in keys:          # tables written before the column existed
    keys.insert(keys.index("classes") + 1, "levels")
if "l1_alternatives" not in keys:
    keys.insert(keys.index("classes"), "l1_alternatives")
if args.reads and "reads" not in keys:
    keys.append("reads")
if "analysis_commit" not in keys:
    keys.append("analysis_commit")
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
    bounds = None
    if args.reads:
        mp = os.path.join(args.reads, r["name"] + ".json")
        bounds = SiteMap.load(mp, addr2line_resolver(out)) if os.path.exists(mp) else None
        r["reads"] = "map" if bounds is not None else "none"
    rep = report.analyze(t, with_sp=True, sp_max_events=args.sp_max_events, max_states=args.max_states, reads=bounds)
    classes = " ".join(rep.classes())
    levels = " ".join(rep.levels())
    if args.reads and levels != r.get("levels", ""):
        changed += 1
        print(f"{r['name']:<60} {r.get('levels', '')!r} -> {levels!r}")
    elif not args.reads and classes != r.get("classes", ""):
        changed += 1
        print(f"{r['name']:<60} {r.get('classes', '')!r} -> {classes!r}")
    r["classes"], r["findings"] = classes, len(rep.findings)
    r["levels"] = levels
    r["l1_alternatives"] = sum(1 for f in rep.findings if f.level == "L1" and f.family == "1")
    r["notes"] = "; ".join(rep.notes)[:300]
    # the run's commit stays (rows of older re-analyses wrote "<run> (analysis <commit>)" there);
    # the analysis's goes to analysis_commit, the latest re-analysis's
    r["analysis_commit"] = code_commit() if code_commit() != r.get("commit") else ""
    with open(os.path.join(out, f"report{suffix}.txt"), "w") as fh:
        fh.write(report.format_report(t, rep, show_infeasible=True))
    with open(os.path.join(out, f"report{suffix}.json"), "w") as fh:
        json.dump(report.to_json(t, rep), fh, default=str, indent=1)
with open(out_path, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=keys, restval="", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
print(f"{changed} of {len(rows)} rows changed {'levels' if args.reads else 'classes'}; wrote {out_path}")
