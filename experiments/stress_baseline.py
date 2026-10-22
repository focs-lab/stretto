#!/usr/bin/env python3
"""The stress-testing baseline: rerun each corpus program up to N times with random delays
injected by the replay library (STRETTO_SEND_DELAY_MS, STRETTO_RECV_DELAY_MS, per rank, drawn
anew for every run) and record after how many runs, if any, the run stops finishing (a hang
or an error). This is what a developer without a predictive tool can do: shake the schedule
and wait for the bad order. Stretto is compared with it from one recorded run.

usage: stress_baseline.py <corpus csv> [--runs N] [--timeout S] [--jobs J] [--select REGEX] [--seed K] [--eager B]

Writes stress.csv next to the corpus table: program, expect, runs (how many were made),
first_failure (the run that first did not finish, empty if none), outcome of that run,
seconds, commit. The search stops at the first failure: the question is how many runs it
takes, not the failure rate.

--eager B makes every run with Open MPI's eager threshold forced down to B payload bytes, by
the setting E2 replays a buffering finding with below its bound (experiments/b_impl.py): the
TCP transport over the loopback interface, which buffers L - h bytes on every send path for
eager limit L (h, its header, from experiments/results/b_impl.csv), with L = B + h, and the
self transport's limit set alike. The shared-memory transport is left out, since it sends up
to 256 bytes inline whatever its limit; the self transport does the same, so a send to the
sending rank stays buffered. B = 0, the least Open MPI accepts, buffers no payload byte. The
table is then stress-eager.csv, with B and the mpirun arguments on every row; the delays are
those of the run without the option (the same seed per program).
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
from stretto.confirm import classify_rc, run_pinned  # noqa: E402
from provenance import code_commit  # noqa: E402
from b_impl import tcp, tcp_header  # noqa: E402

DELAYS_MS = [0, 0, 0, 1, 5, 20, 50]
FIELDS = ["program", "expect", "runs", "first_failure", "outcome", "seconds", "commit"]


def delays(rng: random.Random, np: int) -> dict:
    """One random schedule perturbation: a send and a receive delay per rank."""
    send = ",".join(f"{r}:{rng.choice(DELAYS_MS)}" for r in range(np))
    recv = ",".join(f"{r}:{rng.choice(DELAYS_MS)}" for r in range(np))
    return {"STRETTO_SEND_DELAY_MS": send, "STRETTO_RECV_DELAY_MS": recv}


def stress(row: dict, exe: str, args) -> dict:
    rng = random.Random(f"{args.seed}:{row['name']}")
    np = int(row["np"])
    t0 = time.monotonic()
    out = {"program": row["name"], "expect": row["expect"], "runs": 0, "first_failure": "", "outcome": "",
           "seconds": 0.0, "commit": code_commit()}
    if args.eager is not None:
        out.update(eager_bytes=args.eager, mpirun_args=" ".join(args.mpirun_args))
    for i in range(1, args.runs + 1):
        r = run_pinned(np, exe, [], None, timeout=args.timeout, mpirun_args=args.mpirun_args, extra_env=delays(rng, np))
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
    ap.add_argument("--eager", type=int, metavar="B", help="force the eager threshold of every run down to B payload bytes")
    args = ap.parse_args()
    args.mpirun_args = []
    if args.eager is not None:
        header = tcp_header()
        if header is None:
            sys.exit("stress_baseline.py: --eager needs experiments/results/b_impl.csv (experiments/b_impl.py)")
        args.mpirun_args = tcp(args.eager + header)
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
    out = os.path.join(base, "stress.csv" if args.eager is None else "stress-eager.csv")
    with open(out, "w", newline="") as fh:
        fields = FIELDS + ([] if args.eager is None else ["eager_bytes", "mpirun_args"])
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(results, key=lambda r: r["program"]))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
