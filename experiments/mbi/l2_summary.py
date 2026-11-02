#!/usr/bin/env python3
"""MBI with level L2 by re-execution, over repeated traced runs: one row per table of
run_mbi.py --rerun (experiments/results/l2auto/mbi-<k>.csv), scored on the same traced run
without L2 (score.verdict, as the corpus table is scored, of the findings of the other levels)
and with it (score.l2_verdict: also the L2 deadlocks and aborts). Columns: labelled errors reported and false alarms on the correct programs
by each, the errors only L2 reports and those neither reports, the re-executions and the
seconds of analysis summed over the programs, and the commits of the rows. A last row, "mean",
averages the counts over the runs.
usage: l2_summary.py [--dir DIR] [--out CSV]"""
from __future__ import annotations
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
KEYS = ["run", "programs", "errors", "correct", "reported", "false_alarms", "reported_l2", "false_alarms_l2",
        "only_l2", "missed_l2", "crash", "rerun_runs", "rerun_seconds", "commit"]


def without_l2(r: dict) -> dict:
    """The row as an analysis without a twin would have written it: no L2 class (an L2
    deadlock is class 1d, which other levels have too) and no L2 column."""
    lv = [x for x in r.get("levels", "").split() if not x.startswith("L2:")]
    return {**r, "levels": " ".join(lv), "classes": " ".join(sorted({x.split(":", 1)[1] for x in lv})), "l2": ""}


def summary(rows) -> dict:
    rs = score.compiled(rows)
    base = [without_l2(r) for r in rs]
    a, b = score.confusion(base, score.verdict), score.confusion(rs, score.l2_verdict)
    err = [(r, x) for r, x in zip(rs, base) if r["expect"] == "ERROR"]
    return {"programs": len(rs), "errors": len(err), "correct": len(rs) - len(err),
            "reported": a["tp"], "false_alarms": a["fp"], "reported_l2": b["tp"], "false_alarms_l2": b["fp"],
            "only_l2": " ".join(r["name"] for r, x in err if score.verdict(x) == "OK" and score.l2_verdict(r) == "ERROR"),
            "missed_l2": " ".join(r["name"] for r, _ in err if score.l2_verdict(r) == "OK"),
            "crash": a["crash"],
            "rerun_runs": sum(int(r.get("rerun_runs") or 0) for r in rs),
            "rerun_seconds": f"{sum(float(r.get('rerun_seconds') or 0) for r in rs):.0f}",
            "commit": " ".join(sorted({r["commit"] for r in rs}))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(ROOT, "experiments", "results", "l2auto"))
    ap.add_argument("--out", default=None, help="default: DIR/summary.csv")
    args = ap.parse_args()
    paths = sorted((p for p in os.listdir(args.dir) if re.fullmatch(r"mbi-\d+\.csv", p)), key=lambda p: int(p[4:-4]))
    if not paths:
        print(f"l2_summary.py: no mbi-<k>.csv in {args.dir}", file=sys.stderr)
        return 1
    out = [{"run": p[4:-4], **summary(score.rows(os.path.join(args.dir, p)))} for p in paths]
    num = ["programs", "errors", "correct", "reported", "false_alarms", "reported_l2", "false_alarms_l2", "crash",
           "rerun_runs", "rerun_seconds"]
    out.append({"run": "mean", **{k: f"{sum(float(r[k]) for r in out) / len(out):.2f}" for k in num},
                "commit": " ".join(sorted({c for r in out for c in r["commit"].split()}))})
    path = args.out or os.path.join(args.dir, "summary.csv")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=KEYS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    for r in out:
        print(f"run {r['run']:>4}: union {r['reported']}/{r['errors']} FP {r['false_alarms']}/{r['correct']}; "
              f"with L2 {r['reported_l2']}/{r['errors']} FP {r['false_alarms_l2']}/{r['correct']}; only L2: {r.get('only_l2', '')}")
    print(f"wrote {path}")
    return 1 if any(r["crash"] not in (0, "0.00") for r in out) else 0


if __name__ == "__main__":
    sys.exit(main())
