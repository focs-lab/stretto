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

The suites (RULE.md amendment 15): each suite gets one T per tool. Its "<suite>/<mpi>-calibrate"
blocks run every program the suite labels correct once under each of the tool's settings, at
most SUITE_CAP (300 s) each; `derive-suites` reads RESULTS/<suite>/*-calibrate.csv and writes
RESULTS/<suite>/suite-timeouts.csv: suite, tool, runs, slowest, T, capped, settings, commit, with
    T = max(30 s, 10 x the slowest of the runs of every setting of the tool, under each MPI),
a run past the cap a tool failure left out of T (amendment 5). A run's seconds are its script's
own: the traced run's (run_mbi.py, corrbench/run.py's traced mode, stretto_bench.sh's runs after
the untraced one), stress_baseline.py's and ninja/run.py corpus's per program, suites.py must's
(MUST's compilation of the program included).

usage: calibrate.py derive [--results DIR] [--subjects S ...] [--buggy S ...] [--runs 10]
       calibrate.py derive-suites [--results DIR] [--subjects S ...]
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
SUITE_CAP = 300
FIELDS = ["subject", "config", "tool", "setting", "runs", "slowest", "T", "capped", "results", "commit"]
SUITE_FIELDS = ["suite", "tool", "runs", "slowest", "T", "capped", "settings", "commit"]


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


def suite_seconds(r):
    """The seconds of a calibration row's run under the tool, or None for a row that is not one
    (corrbench/run.py's untraced and build rows, stretto_bench.sh's untraced run 0)."""
    if r.get("mode") in ("native", "build") or (r.get("threads") not in (None, "") and r.get("run") == "0"):
        return None
    try:
        return float(r["seconds"])
    except (KeyError, TypeError, ValueError):
        return None


def derive_suites(results, suite):
    import glob
    by, settings, commits = collections.defaultdict(list), collections.defaultdict(set), collections.defaultdict(set)
    for path in sorted(glob.glob(os.path.join(results, suite, "*-calibrate.csv"))):
        for r in rows.current(rows.read(path)):
            tool, _, setting = r.get("calibrated", "").partition(":")
            sec = suite_seconds(r)
            if not tool or sec is None:
                continue
            by[tool].append(sec)
            settings[tool].add("%s/%s" % (r.get("mpi") or os.path.basename(path)[:-len("-calibrate.csv")], setting))
            commits[tool].add(r.get("commit", ""))
    out = []
    for tool, secs in sorted(by.items()):
        t, capped = timeout_of(secs, SUITE_CAP)
        out.append({"suite": suite, "tool": tool, "runs": len(secs), "slowest": max(secs), "T": t, "capped": capped,
                    "settings": ";".join(sorted(settings[tool])), "commit": ";".join(sorted(commits[tool]))})
    dest = os.path.join(results, suite, "suite-timeouts.csv")
    if out:
        if os.path.exists(dest):
            os.replace(dest, dest + ".previous")
        rows.append(dest, out, SUITE_FIELDS)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["derive", "derive-suites"])
    ap.add_argument("--results", default=os.path.join(HERE, "results"))
    ap.add_argument("--subjects", nargs="*", default=None)
    ap.add_argument("--buggy", nargs="*", default=[])
    ap.add_argument("--runs", type=int, default=RUNS, help="calibration runs per setting (10; fewer only for the smoke test)")
    a = ap.parse_args()
    if a.cmd == "derive-suites":
        import glob
        subs = a.subjects if a.subjects else sorted(d for d in os.listdir(a.results)
                                                    if glob.glob(os.path.join(a.results, d, "*-calibrate.csv")))
        for s in subs:
            for r in derive_suites(a.results, s):
                print("%s %s: %d runs (%s), slowest %.1f s -> T %s%s" % (s, r["tool"], r["runs"], r["settings"], r["slowest"],
                                                                       r["T"], " (%d reached the cap: tool failures)" % r["capped"]
                                                                       if r["capped"] else ""))
        return 0
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
