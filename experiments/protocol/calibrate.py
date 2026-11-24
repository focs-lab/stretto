#!/usr/bin/env python3
"""The timeouts of the uniform campaign (experiments/protocol/RULE.md, Section 2.1), fixed before
a block's runs: for each configuration and each tool setting,

    T = max(30 s, 10 x the slowest of 10 runs of the fixed version under the same tool and setting),

the same T for the buggy version. The calibration runs are the plan's "<subject>/calibrate"
blocks, which schedule.py runs like every other block (one run.py job per run, at most
CALIBRATION_CAP seconds each; a traced run's trace is neither kept nor analysed there):

    schedule.py --blocks '/calibrate$'
    calibrate.py derive

`derive` reads RESULTS/<subject>/calibrate.csv and writes RESULTS/<subject>/timeouts.csv: subject,
config, tool, setting, runs, slowest, T, capped (how many runs did not end within the cap: tool
failures, left out of T; if none ended, T is the cap, RULE.md amendment 5), results (how the runs
ended), commit. A setting with fewer than 10 current (not superseded) runs is left out and named. Only
the first 10 current runs of a setting count, in the order of their run numbers.

A subject without a fixed version takes 10 runs of the buggy version whose symptom did not strike
(--buggy SUBJECT: the rows of the subject's version "bug" whose outcome is not a symptom); every
application subject of Section 4 has a fixed version, so the option serves the thread campaigns'
controls when they are calibrated the same way.

usage: calibrate.py derive [--results DIR] [--subjects S ...] [--buggy S ...] [--runs 10]
Python 3.6 runs this file.
"""
import argparse
import collections
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rows  # noqa: E402

RUNS = 10
FLOOR = 30
FACTOR = 10
CALIBRATION_CAP = 1800
FIELDS = ["subject", "config", "tool", "setting", "runs", "slowest", "T", "capped", "results", "commit"]


def timeout_of(seconds, cap=CALIBRATION_CAP):
    """(T, capped runs) from the calibration runs' seconds (RULE.md, amendment 5): a run that did not
    end within the cap is a tool failure, and T comes from the runs that ended; if none ended, T is
    the cap."""
    ended = [s for s in seconds if s < cap]
    capped = len(seconds) - len(ended)
    if not ended:
        return cap, capped
    return max(FLOOR, int(math.ceil(FACTOR * max(ended)))), capped


def derive(results, subject, buggy=False, runs=RUNS):
    path = os.path.join(results, subject, "calibrate.csv")
    want = "bug" if buggy else "fix"
    by = collections.defaultdict(list)
    for r in rows.current(rows.read(path)):
        if r["version"] != want:
            continue
        if buggy and r.get("symptom") == "True":
            continue
        by[(r["config"], r["tool"], r["setting"])].append(r)
    out, short = [], []
    for (config, tool, setting), rs in sorted(by.items()):
        rs = sorted(rs, key=lambda r: int(r["run"]))[:runs]
        if len(rs) < runs:
            short.append("%s %s %s: %d runs" % (config, tool, setting, len(rs)))
            continue
        secs = [float(r["seconds"]) for r in rs]
        cap = min(max(int(r["timeout"]) for r in rs), CALIBRATION_CAP)
        t, capped = timeout_of(secs, cap)
        ends = collections.Counter(r["result"] for r in rs)
        out.append({"subject": subject, "config": config, "tool": tool, "setting": setting, "runs": len(rs),
                    "slowest": max(secs), "T": t, "capped": capped,
                    "results": ";".join("%s=%d" % kv for kv in sorted(ends.items())),
                    "commit": ";".join(sorted(set(r["commit"] for r in rs)))})
    dest = os.path.join(results, subject, "timeouts.csv")
    if out:
        if os.path.exists(dest):
            os.replace(dest, dest + ".previous")
        rows.append(dest, out, FIELDS)
    return out, short


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["derive"])
    ap.add_argument("--results", default=os.path.join(HERE, "results"))
    ap.add_argument("--subjects", nargs="*", default=None)
    ap.add_argument("--buggy", nargs="*", default=[])
    ap.add_argument("--runs", type=int, default=RUNS, help="calibration runs per setting (10; fewer only for the smoke test)")
    a = ap.parse_args()
    subs = a.subjects if a.subjects else sorted(d for d in os.listdir(a.results)
                                                if os.path.exists(os.path.join(a.results, d, "calibrate.csv")))
    bad = 0
    for s in subs:
        out, short = derive(a.results, s, buggy=s in a.buggy, runs=a.runs)
        for r in out:
            print("%s %s %s %s: slowest %.1f s -> T %s%s" % (s, r["config"], r["tool"], r["setting"], r["slowest"],
                                                             r["T"], " (%d runs reached the cap: tool failures)" % r["capped"]
                                                             if r["capped"] else ""))
        for x in short:
            print("%s %s: too few calibration runs" % (s, x))
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
