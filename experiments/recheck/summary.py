#!/usr/bin/env python3
"""Per campaign, the traces of a re-analysis (reanalyse.py's rows.csv): how many the fixes
could change (a rank with several chains or a freed receive), how many were compared, and how
many reports differ.

  experiments/recheck/summary.py ROWS.csv [ROWS.csv ...]"""
import collections
import csv
import sys


def main() -> int:
    per = collections.defaultdict(collections.Counter)
    for path in sys.argv[1:]:
        for r in csv.DictReader(open(path)):
            c = per[r["campaign"]]
            c["traces"] += 1
            c["affected"] += r.get("affected") == "1"
            c["compared"] += r["same"] in ("0", "1")
            c["differ"] += r["same"] == "0"
            c["timeout"] += "timeout" in (r["base_exit"], r["new_exit"])
    print("| campaign | traces | can change | compared | reports differ | timed out |")
    print("|---|---|---|---|---|---|")
    tot = collections.Counter()
    for k in sorted(per):
        c = per[k]
        tot.update(c)
        print(f"| {k} | {c['traces']} | {c['affected']} | {c['compared']} | {c['differ']} | {c['timeout']} |")
    print(f"| all | {tot['traces']} | {tot['affected']} | {tot['compared']} | {tot['differ']} | {tot['timeout']} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
