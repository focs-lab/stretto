#!/usr/bin/env python3
"""The SLATE leads of the configuration sweep, one row per cell and lead class.

    leads.py RUNS SWEEP.csv > leads.csv

RUNS is the sweep's directory of SLATE cells (one directory per cell, with report.json), SWEEP.csv
its rows (experiments/sweep/sweep-slate.csv). A lead is an observed thread class outside the known
CALU race (2-thread-finalize, 2-thread-collective, 2-collective) or a traced run that timed out.
"""
import csv
import collections
import json
import os
import sys

LEAD_CLASSES = ("2-thread-finalize", "2-thread-collective", "2-collective")


def main(runs, sweep):
    rows = {r["cell"]: r for r in csv.DictReader(open(sweep))}
    w = csv.writer(sys.stdout, lineterminator="\n")
    w.writerow(["cell", "lead", "findings", "outcome", "chain_overflow", "overflow_chain_named",
                "kept_trace", "sweep_commit"])
    for cell, r in rows.items():
        d = os.path.join(runs, cell.replace("/", "_").replace(":", "_").replace("#", "+"))
        try:
            rep = json.load(open(os.path.join(d, "report.json")))
        except (OSError, ValueError):
            rep = {"findings": [], "notes": []}
        ovf = any("chain table overflowed" in n for n in rep.get("notes", []))
        kept = os.path.exists(os.path.join(d, "trace.tar.xz")) if os.path.isdir(d) else ""
        by = collections.defaultdict(list)
        for f in rep.get("findings", []):
            if f["cls"] in LEAD_CLASSES:
                by[f["cls"]].append(f)
        for cls, fs in sorted(by.items()):
            named = sum(".-1" in f["title"] for f in fs)
            w.writerow([cell, cls, len(fs), r["outcome"], int(ovf), named, int(bool(kept)), r["commit"]])
        if r["outcome"] == "timeout":
            w.writerow([cell, "timeout", "", r["outcome"], int(ovf), "", int(bool(kept)), r["commit"]])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
