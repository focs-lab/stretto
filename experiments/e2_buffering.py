#!/usr/bin/env python3
"""E2: buffering ablation. For every trace directory given (or found under
experiments/results/*/*/trace and traces/openmpi/*), report the feasibility profile of
the observed matching and of every L1 alternative over the breakpoints
{0} U message sizes U {inf}, and count how findings move with B in {0, 4096, inf}.

Optionally (--replay) rerun programs with a 2-buffering finding under small and large
eager limits to check the predicted B-interval (Open MPI vader/self limits).
"""
from __future__ import annotations
import argparse
import csv
import glob
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from rematch import trace  # noqa: E402
from rematch.feasible import feasible_profile, profile_summary  # noqa: E402
from rematch.matching import candidates_full  # noqa: E402


def profile_at(prof, B):
    if B is None:
        return prof.get(None)
    keys = sorted(k for k in prof if k is not None)
    best = 0
    for k in keys:
        if k <= B:
            best = k
    return prof.get(best, prof.get(0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--out", default=os.path.join(ROOT, "experiments", "results", "e2_buffering.csv"))
    ap.add_argument("--max-events", type=int, default=3000)
    args = ap.parse_args()
    dirs = args.dirs or sorted(glob.glob(os.path.join(ROOT, "traces", "openmpi", "*")) +
                               glob.glob(os.path.join(ROOT, "experiments", "results", "*", "*", "trace")))
    rows = []
    for d in dirs:
        try:
            t = trace.load(d)
        except Exception:
            continue
        name = d.replace(ROOT + "/", "")
        if sum(len(v) for v in t.ranks.values()) > args.max_events:
            continue
        prof = feasible_profile(t, t.observed, set(), mode="SP", strong_only=True)
        minB, at_inf, s = profile_summary(prof)
        rows.append({"trace": name, "what": "observed", "minB": minB, "at_inf": at_inf, "profile": s,
                     "B0": profile_at(prof, 0), "B4096": profile_at(prof, 4096), "Binf": profile_at(prof, None)})
        alts, _ = candidates_full(t)
        for a in alts:
            p = feasible_profile(t, a.M, a.changed, mode="L1")
            minB, at_inf, s = profile_summary(p)
            if minB is None and not at_inf:
                continue
            rows.append({"trace": name, "what": f"{a.recv}<-{a.send} [{a.kind}]", "minB": minB, "at_inf": at_inf,
                         "profile": s, "B0": profile_at(p, 0), "B4096": profile_at(p, 4096), "Binf": profile_at(p, None)})
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["trace", "what", "minB", "at_inf", "profile", "B0", "B4096", "Binf"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    obs = [r for r in rows if r["what"] == "observed"]
    alt = [r for r in rows if r["what"] != "observed"]
    print(f"traces={len(obs)} observed-infeasible-at-B0={sum(1 for r in obs if not r['B0'])} "
          f"alternatives={len(alt)} feasible@0={sum(1 for r in alt if r['B0'])} feasible@4096={sum(1 for r in alt if r['B4096'])} "
          f"feasible@inf={sum(1 for r in alt if r['Binf'])} B-dependent={sum(1 for r in alt if r['B0'] != r['Binf'])}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
