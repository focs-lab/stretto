#!/usr/bin/env python3
"""The stress-testing baseline: rerun each corpus program up to N times with random delays
injected by the replay library (REMATCH_SEND_DELAY_MS, REMATCH_RECV_DELAY_MS, per rank, drawn
anew for every run) and record after how many runs, if any, the run stops finishing (a hang
or an error). This is what a developer without a predictive tool can do: shake the schedule
and wait for the bad order. Rematch is compared with it from one recorded run.

usage: stress_baseline.py <corpus csv> [--runs N] [--timeout S] [--jobs J] [--select REGEX] [--seed K]

Writes stress.csv next to the corpus table: program, expect, runs (how many were made),
first_failure (the run that first did not finish, empty if none), outcome of that run,
seconds, commit. The search stops at the first failure: the question is how many runs it
takes, not the failure rate.
"""
from __future__ import annotations
import argparse
import csv
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rematch.confirm import classify_rc, run_pinned  # noqa: E402
from provenance import code_commit  # noqa: E402

DELAYS_MS = [0, 0, 0, 1, 5, 20, 50]


def delays(rng: random.Random, np: int) -> dict:
    """One random schedule perturbation: a send and a receive delay per rank."""
    send = ",".join(f"{r}:{rng.choice(DELAYS_MS)}" for r in range(np))
    recv = ",".join(f"{r}:{rng.choice(DELAYS_MS)}" for r in range(np))
    return {"REMATCH_SEND_DELAY_MS": send, "REMATCH_RECV_DELAY_MS": recv}


def stress(row: dict, exe: str, args) -> dict:
    rng = random.Random(f"{args.seed}:{row['name']}")
    np = int(row["np"])
    t0 = time.monotonic()
    out = {"program": row["name"], "expect": row["expect"], "runs": 0, "first_failure": "", "outcome": "",
           "seconds": 0.0, "commit": code_commit()}
    for i in range(1, args.runs + 1):
        r = run_pinned(np, exe, [], None, timeout=args.timeout, extra_env=delays(rng, np))
        res = classify_rc(r.returncode, r.stderr, r.elapsed, args.timeout)
        out["runs"] = i
        if res != "FINISHED":
            out["first_failure"], out["outcome"] = i, res
            break
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--select", default=".")
    ap.add_argument("--seed", type=int, default=20261020)
    args = ap.parse_args()
    base = os.path.dirname(os.path.abspath(args.csv))
    rows = [r for r in csv.DictReader(open(args.csv)) if r.get("build") == "ok" and re.search(args.select, r["name"])]
    todo = []
    for r in rows:
        exe = os.path.join(base, r["name"].replace("/", "__"), "main")
        if os.path.exists(exe):
            todo.append((r, exe))
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for i, res in enumerate(ex.map(lambda x: stress(x[0], x[1], args), todo), 1):
            results.append(res)
            print(f"[{i}/{len(todo)}] {res['program']:<50} runs={res['runs']:<3} first_failure={res['first_failure'] or '-'} {res['outcome']}", flush=True)
    out = os.path.join(base, "stress.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["program", "expect", "runs", "first_failure", "outcome", "seconds", "commit"], lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(results, key=lambda r: r["program"]))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
