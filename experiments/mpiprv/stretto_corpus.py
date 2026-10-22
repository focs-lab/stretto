#!/usr/bin/env python3
"""Stretto with the buffer guard on the programs of experiments/mpiprv/run.py (MPI-BugBench's
LocalConcurrency and Correct programs, MBI's point-to-point LocalConcurrency, ReqLifecycle and
ResLeak families, and the corrected twins), so that MPI-PRV's arms are scored against Stretto on
the same programs. Each program is built and traced once with STRETTO_GUARD=1 and analysed, as
experiments/mbi/run_mbi.py --guard does (it is called for each program); the twins are written,
without markers, to experiments/mpiprv/work/stretto-corpus/twins/. With KEEP_TRACES set, each
trace is kept as mpiprv/stretto-corpus/<corpus>/<program> (stretto.keep).
Runs in the experiment image (experiments/container/run.sh).

usage: stretto_corpus.py [--jobs J] [--select REGEX --out CSV]   (writes experiments/results/mpiprv/stretto-corpus.csv)"""
from __future__ import annotations
import argparse
import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "experiments", "mbi"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
import run as prv  # noqa: E402  (the program list of the MPI-PRV runner)
import run_mbi  # noqa: E402
from selection import selects  # noqa: E402
import score  # noqa: E402

WORK = os.path.join(HERE, "work", "stretto-corpus")
OUT = os.path.join(ROOT, "experiments", "results", "mpiprv", "stretto-corpus.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("JOBS") or 2))
    ap.add_argument("--select", default="", help="regex on program names (experiments/selection.py); needs --out")
    ap.add_argument("--out", default=None, help="the table to write (default: the canonical one, for every program only)")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()
    twins = os.path.join(WORK, "twins")
    os.makedirs(twins, exist_ok=True)
    jobs = []
    for p in prv.programs():
        if not selects(args.select, p["program"]):
            continue
        if p.get("twin_src"):
            name = p["program"].replace("twin:", "twin__").replace("/", "__") + ".c"
            open(os.path.join(twins, name), "w").write(prv.markers_stripped(p["twin_src"]))
            jobs.append((p, twins, name))
        else:
            jobs.append((p, os.path.dirname(p["path"]), os.path.basename(p["path"])))
    if not jobs:
        print(f"stretto_corpus.py: --select {args.select!r} selects no program; nothing written", file=sys.stderr)
        return 1
    if args.select and not args.out:
        print(f"stretto_corpus.py: --select would overwrite {os.path.relpath(OUT, ROOT)} with part of it; give --out",
              file=sys.stderr)
        return 2
    out_path = args.out or OUT

    def one(job):
        p, gencodes, name = job
        # run_mbi's own defaults (an option it gains reaches this script too), with the guard on
        a = run_mbi.options(gencodes=gencodes, results=os.path.join(WORK, p["corpus"]), guard=True,
                            timeout=args.timeout, must=False, confirm=False, no_sp=False, sp_max_events=600,
                            max_states=30000, keep_campaign=f"mpiprv/stretto-corpus/{p['corpus']}")
        r = run_mbi.run_one(name, a)
        return {"corpus": p["corpus"], "program": p["program"], "expect": p["expect"], "detail": p["detail"],
                "np": p["np"], "build": r.get("build"), "run": r.get("run"), "classes": r.get("classes", ""),
                "levels": r.get("levels", ""), "verdict": score.verdict(r) if r.get("build") == "ok" else "-",
                "notes": r.get("notes", ""), "commit": r.get("commit") or prv.code_commit()}

    rows = []
    with ThreadPoolExecutor(args.jobs) as ex:
        for i, r in enumerate(ex.map(one, jobs), 1):
            rows.append(r)
            print(f"[{i}/{len(jobs)}] {r['program']}: {r['run']} {r['classes']} -> {r['verdict']}", flush=True)
    keys = ["corpus", "program", "expect", "detail", "np", "build", "run", "classes", "levels", "verdict", "notes", "commit"]
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote", out_path)
    crashed = [r["program"] for r in rows if r["verdict"] == score.CRASH]
    if crashed:
        print(f"the analysis crashed on {len(crashed)} program(s), whose rows say CRASH: {', '.join(crashed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
