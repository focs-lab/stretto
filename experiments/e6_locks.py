#!/usr/bin/env python3
"""E6 for several threads with locks: the analysis of several threads (L1) and its lock
reordering (L1+L, docs/threads.md section 13) against the exhaustive explorer of
stretto.explore_threads, on random straight-line programs of 2 or 3 processes with up to two
worker threads each, whose operations sit partly in critical sections of one lock per process.
Each program is run once by the explorer's semantics (a random schedule, infinite buffering)
and written as the tracer would (clocks, hard clocks, lock events); every claim the analysis
makes at B = 0 and B = inf is checked against every interleaving of the program
(explore_threads.compare). Straight-line programs satisfy premises P-T1 and P-T8, so an
UNSOUND row is a defect of the analysis. An "incomplete" row is an upper bound: an alternative
the analysis rejects whose changed pair some execution realises, or a deadlock with a thread
waiting at a lock that no claim names.
usage: e6_locks.py [--programs N] [--seed S] [--procs P] [--out results/e6_locks.csv]
"""
from __future__ import annotations
import argparse
import collections
import csv
import os
import random
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from stretto import explore_threads as X  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--programs", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--procs", type=int, default=0, help="processes per program (0: 2 or 3 at random)")
    ap.add_argument("--max-states", type=int, default=200_000)
    ap.add_argument("--out", default=os.path.join(ROOT, "experiments", "results", "e6_locks.csv"))
    args = ap.parse_args()
    rng = random.Random(args.seed)
    counts = collections.Counter()
    rows = []
    t0 = time.time()
    for i in range(args.programs):
        prog = X.random_program(rng, nprocs=args.procs or rng.choice((2, 2, 3)))
        got = X.trace_of(prog, None, rng)
        if got is None:
            counts["no finishing run"] += 1
            continue
        t, where, _ = got
        for r in X.compare(prog, t, where, args.max_states):
            r = {"program": i, **r}
            rows.append(r)
            counts[(r["level"], r["kind"])] += 1
            if r["kind"] == "UNSOUND":
                print(f"UNSOUND program {i} {r}\n{prog}", flush=True)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["program", "level", "item", "B", "claim", "explorer", "kind"])
        w.writeheader()
        w.writerows(rows)
    for k, n in sorted(counts.items(), key=str):
        print(k, n)
    print(f"{args.programs} programs in {time.time() - t0:.0f} s -> {args.out}")
    return 1 if any(k[1] == "UNSOUND" for k in counts if isinstance(k, tuple)) else 0


if __name__ == "__main__":
    sys.exit(main())
