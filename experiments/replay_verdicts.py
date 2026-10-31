#!/usr/bin/env python3
"""The verdict of each row of a replay_sample.py --threads table (docs/threads.md, section 16):

- inconclusive: a pin or lock pin of the replay or its control fell on another operation, a gate
  timed out, the synchronisation of either differs from the trace's, the control did not take
  its pinned messages or did not finish as the recorded run did, or the replay did not take its
  required pins (and did not end in the predicted truncation);
- confirmed: the replay took every required pin and shows the predicted outcome, and its control
  reproduced the recorded run (its pins taken, no divergence, finished with the recorded run's
  result);
- realised: the same, but the replay finished without the predicted outcome (a 1f alternative
  that the program survives in that run);
- truncated-unchecked: a 1a replay that ended in MPI_ERR_TRUNCATE, its control as confirmed
  asks, in a table written before the column "shown" said where the truncation happened.

The predicted outcome is a wrong result (the row's "hits", lines that report one, above 0) or,
for a 1a finding, MPI_ERR_TRUNCATE. The recorded run's result is right unless --recorded-wrong
names its runs (the first column), whose controls must then report a wrong result too.

usage: replay_verdicts.py [--recorded-wrong RUNS] [--out CSV] TABLE.csv
Prints the counts per class and verdict; --out writes the table with a verdict column.
"""
from __future__ import annotations
import argparse
import csv
import sys
from collections import Counter


def verdict(r: dict, recorded_wrong: bool) -> str:
    if any(r.get(k) for k in ("misapplied", "diverged", "control_misapplied", "control_diverged")):
        return "inconclusive"
    want_hits = int(r.get("control_hits") or 0) > 0
    if r.get("control") != "FINISHED" and not (recorded_wrong and r.get("control") == "APP-ERROR:1"):
        return "inconclusive"
    if r.get("control_matched") != "True" or want_hits != recorded_wrong:
        return "inconclusive"
    truncated = r.get("replay") == "MPI-ERROR:MPI_ERR_TRUNCATE"
    if r.get("cls") == "1a" and truncated:
        # the receive that truncates ends the run, so it shows no match: the replay's own trace
        # must show it pending there, every other required pin taken or pending (column shown)
        return {"True": "confirmed", "False": "inconclusive"}.get(r.get("shown") or "", "truncated-unchecked")
    if r.get("matched") != "True":
        return "inconclusive"
    if int(r.get("hits") or 0) > 0 and not recorded_wrong:
        return "confirmed"
    return "realised"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("table")
    ap.add_argument("--recorded-wrong", default="", help="comma-separated runs whose recorded result was wrong")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    wrong = set(args.recorded_wrong.split(",")) - {""}
    with open(args.table) as fh:
        rows = list(csv.DictReader(fh))
    counts: Counter = Counter()
    for r in rows:
        r["verdict"] = verdict(r, r.get("run", "") in wrong)
        counts[(r.get("cls", ""), r["verdict"])] += 1
    for (cls, v), n in sorted(counts.items()):
        print(f"{cls} {v} {n}")
    if args.out:
        with open(args.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["verdict"], lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
