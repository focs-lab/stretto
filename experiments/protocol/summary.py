#!/usr/bin/env python3
"""The campaign's tables at a glance: per subject, configuration, version, tool and setting, the
current (not superseded) runs, their outcomes (RULE.md Section 3), the main cell revealed per run
= (predicted + observed-reported) / N with its Wilson interval, the symptom rate S / N, the tool
failures, and the run seconds (mean, and the sum over the runs, from which README.md's wall-time
estimates are taken: the block's job-seconds divided by the jobs that fit beside each other).
A suite block is shown per cell and label (amendment 23: Stretto's traced runs, the plain cell of
its untraced runs, the buffer guard's appendix cell; the programs labelled erroneous apart from
the correct ones), with the share of its reveals that are timeouts (amendment 28). A thread
replay block counts each alternative once per mode, by its alternative_verdict (amendment 24),
beside the per-replay outcomes.

usage: summary.py [--results DIR] [--blocks REGEX] [--csv]
Python 3.6 runs this file.
"""
import argparse
import collections
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rows  # noqa: E402

SHORT = {"predicted": "pred", "observed-reported": "obs+rep", "observed-not-reported": "obs-rep", "missed": "missed",
         "tool-failure": "fail"}


def ended_by_timeout(r):
    """Whether a row's run did not finish within T, by whichever column its script writes."""
    return "TIMEOUT" in (r.get("outcome_end") or "", r.get("result") or "", r.get("run") or "", r.get("script_outcome") or "") \
        or any((r.get(k) or "").endswith(":TIMEOUT") for k in ("must1112", "must1112tsan", "must"))


def alternatives(rs):
    """A thread replay block's alternatives, once per (index, mode): (n, confirmed, realised)."""
    seen = {}
    for r in rs:
        if r.get("alternative_verdict"):
            seen[(r.get("index"), r.get("mode"))] = r["alternative_verdict"]
    v = list(seen.values())
    return len(v), v.count("confirmed"), v.count("realised")


def tables(results, blocks):
    for d, _, files in sorted(os.walk(results)):
        for f in sorted(files):
            if not f.endswith(".csv") or f in ("timeouts.csv",) or f.startswith("replay-alternatives"):
                continue
            block = os.path.relpath(os.path.join(d, f), results)[:-4]
            if re.search(blocks, block):
                yield block, rows.current(rows.read(os.path.join(d, f)))


def job_seconds(results, block):
    """Per (subject, config, version, tool, setting): the mean seconds of a job, the traced runs'
    analysis and the replays' controls included."""
    secs = collections.defaultdict(list)
    for r in rows.current(rows.read(os.path.join(results, block + ".csv"))):
        s = float(r.get("seconds") or 0)
        try:
            d = json.loads(r.get("detail") or "{}")
        except ValueError:
            d = {}
        s += float(d.get("analyse_s") or 0)
        if isinstance(d.get("control"), dict):
            s += float(d["control"].get("seconds") or 0)
        secs[(r["subject"], r["config"], r["version"], r["tool"], r["setting"])].append(s + JOB_OVERHEAD)
    return {k: sum(v) / len(v) for k, v in secs.items()}


JOB_OVERHEAD = 2.0      # seconds of a container's start and end around run.py
THREADS = 36


def estimate(a, _):
    """The wall time of each block at the plan's N, from the smoke's mean job seconds: a block's
    thread-seconds (jobs x N x seconds x threads) over the 36 run threads, an alone job's seconds
    as they are. It assumes perfect packing and the smoke's mean run times, in which a run that
    hung until T counts as it happened: with 2 runs per row the mean of a bug that strikes in some
    runs is coarse."""
    with open(os.path.join(HERE, "plan.csv"), newline="") as fh:
        plan = [p for p in csv.DictReader(fh) if p["runner"] in ("run.py", "calibrate.py", "replays.py")]
    cache, total, by_block = {}, 0.0, collections.OrderedDict()
    for p in plan:
        if not re.search(a.blocks, p["block"]):
            continue
        tool, setting = p["tool"], p["setting"]
        if tool == "calibrate":
            tool, _, setting = setting.partition(":")
        if p["block"] not in cache:
            cache[p["block"]] = job_seconds(a.results, p["block"])
        s = cache[p["block"]].get((p["subject"], p["config"], p["version"], tool, setting))
        if s is None:
            continue
        n, cpus = int(p["n"]), int(p["cpus"])
        wall = n * s if p["alone"] == "1" else n * s * cpus / THREADS
        by_block[p["block"]] = by_block.get(p["block"], 0.0) + wall
        total += wall
    for b, w in by_block.items():
        print("%-32s %8.2f h" % (b, w / 3600))
    print("%-32s %8.2f h" % ("total", total / 3600))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=os.path.join(HERE, "results"))
    ap.add_argument("--blocks", default=".")
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--estimate", action="store_true", help="the wall time of each block at the plan's N")
    a = ap.parse_args()
    out = []
    for block, rs in tables(a.results, a.blocks):
        by = collections.OrderedDict()
        for r in rs:
            if "outcome" not in r:
                continue
            by.setdefault((r.get("subject"), r.get("config"), r.get("version"), r.get("tool"), r.get("setting"),
                           r.get("cell", ""), r.get("expect") or r.get("label", "")), []).append(r)   # CorrBench: label
        alts = alternatives(rs)
        for k, v in by.items():
            c = collections.Counter(r["outcome"] for r in v)
            n = len(v)
            hit = c["predicted"] + c["observed-reported"]
            lo, hi = rows.wilson(hit, n)
            secs = [float(r["seconds"]) for r in v if r.get("seconds") not in (None, "")]
            sym = sum(1 for r in v if r.get("symptom") == "True")
            hung = sum(1 for r in v if r["outcome"] in ("predicted", "observed-reported") and ended_by_timeout(r))
            out.append({"block": block, "subject": k[0], "config": k[1], "version": k[2], "tool": k[3], "setting": k[4],
                        "cell": k[5] + (" (appendix)" if k[5] == "stretto-guard" else ""), "expect": k[6],
                        "timeout_share": "%d/%d" % (hung, hit) if hit else "",
                        "alternatives": "%d, %d confirmed, %d realised" % alts if alts[0] else "",
                        "n": n, "revealed": hit, "lo": round(lo, 3), "hi": round(hi, 3), "symptom": sym,
                        "failures": c["tool-failure"], "outcomes": " ".join("%s=%d" % (SHORT.get(o, o), m) for o, m in sorted(c.items())),
                        "mean_s": round(sum(secs) / len(secs), 1) if secs else "", "sum_s": round(sum(secs), 1)})
    if a.estimate:
        return estimate(a, out)
    if a.csv:
        w = csv.DictWriter(sys.stdout, fieldnames=list(out[0]) if out else ["block"], lineterminator="\n")
        w.writeheader()
        w.writerows(out)
        return 0
    for r in out:
        print("%-28s %-14s %-3s %-8s %-12s n=%-3d revealed %2d [%.2f,%.2f] S=%-3d fail=%-3d mean %6s s  %s%s%s%s" % (
            r["block"], r["config"], r["version"], r["tool"], r["setting"], r["n"], r["revealed"], r["lo"], r["hi"],
            r["symptom"], r["failures"], r["mean_s"], r["outcomes"],
            "  cell %s" % r["cell"] if r["cell"] else "", "  %s" % r["expect"] if r["expect"] else "",
            "  timeouts %s" % r["timeout_share"] if r["timeout_share"] and r["cell"] != "" or r["expect"] else "")
            + ("  alternatives %s" % r["alternatives"] if r["alternatives"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
