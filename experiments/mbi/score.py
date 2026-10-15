#!/usr/bin/env python3
"""Score an E1 CSV (MBI or MPI-BugBench harness output) MBB-style.

Rematch verdict per program: ERROR if any finding of an error class is present, else OK.
  error classes   : 1a 1c 1d 2-buffering 2-collective 2-blocked
  warning classes : 1b 1f probe-alt waitany-order   (reported, not counted as errors)
Prints TP/FP/FN/TN, precision/recall/F1, and the E1 breakdown for message-race programs:
racing receive flagged (L1), consequence certified (L1+SP), observed deadlock in the run.
usage: score.py results.csv [--md]
"""
import csv
import sys

ERROR = {"1a", "1c", "1d", "2-buffering", "2-collective", "2-blocked"}
WARN = {"1b", "1f", "probe-alt", "waitany-order"}


def verdict(classes: str) -> str:
    cs = set(classes.split())
    return "ERROR" if cs & ERROR else "OK"


def main(path: str, md: bool = False) -> int:
    rows = list(csv.DictReader(open(path)))
    tp = fp = fn = tn = 0
    ce = 0
    for r in rows:
        if r.get("build") == "CE":
            ce += 1
            continue
        v = verdict(r.get("classes", ""))
        e = r.get("expect")
        if e == "ERROR" and v == "ERROR":
            tp += 1
        elif e == "ERROR":
            fn += 1
        elif v == "ERROR":
            fp += 1
        else:
            tn += 1
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    print(f"programs={len(rows)} CE={ce} TP={tp} FP={fp} FN={fn} TN={tn} precision={prec:.2f} recall={rec:.2f} F1={f1:.2f}")
    race = [r for r in rows if r.get("detail") == "MessageRace"]
    if race:
        clean = [r for r in race if r.get("run") == "FINISHED"]
        flagged = [r for r in race if set(r.get("classes", "").split()) & {"1a", "1b", "1f", "1c", "1d"}]
        certified = [r for r in race if set(r.get("classes", "").split()) & {"1a", "1c", "1d"}]
        clean_flagged = [r for r in clean if r in flagged]
        clean_cert = [r for r in clean if r in certified]
        observed = [r for r in race if r.get("run") != "FINISHED"]
        print(f"message-race programs={len(race)}: clean runs={len(clean)}, racing receive flagged={len(flagged)} "
              f"(on clean runs {len(clean_flagged)}/{len(clean)}), consequence certified={len(certified)} "
              f"(on clean runs {len(clean_cert)}/{len(clean)}), observed deadlock/abort in the run={len(observed)}")
    ok = [r for r in rows if r.get("expect") == "OK"]
    warned = [r for r in ok if set(r.get("classes", "").split()) & WARN]
    print(f"correct programs={len(ok)}: with warnings only={len(warned)}, with error findings (FP)={fp}")
    if "must" in rows[0]:
        m_tp = sum(1 for r in rows if r.get("expect") == "ERROR" and "ERROR" in r.get("must", ""))
        m_fp = sum(1 for r in rows if r.get("expect") == "OK" and "ERROR" in r.get("must", ""))
        print(f"MUST: TP={m_tp} FP={m_fp}")
        inv = [r for r in rows if r.get("expect") == "ERROR" and verdict(r.get("classes", "")) == "ERROR" and "ERROR" not in r.get("must", "")]
        print(f"errors Rematch reports and MUST does not on the same program: {len(inv)}")
    if md:
        print("\n| label | detail | run | classes | count |\n|---|---|---|---|---|")
        from collections import Counter
        c = Counter((r["expect"], r["detail"], r["run"].split(":")[0], r["classes"]) for r in rows)
        for k, v in sorted(c.items()):
            print(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {v} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--md" in sys.argv))
