#!/usr/bin/env python3
"""E2: buffering ablation. For every trace directory given (or found under
experiments/results/*/*/trace and traces/openmpi/*), report the feasibility profile of
the observed matching and of every L1 alternative over the breakpoints
{0} U message sizes U {inf}, and count how findings move with B in {0, 4096, inf}.

With --replay, every program whose observed run is realisable only above a threshold (a
2-buffering finding, "deadlocks on any implementation with eager threshold < X bytes") is
rerun twice under Open MPI: with its default eager limits (the run finished once, it should
finish again) and with the limits forced below the message sizes (experiments/b_impl.py
measures what they are), where it should hang. The outcome of each rerun goes to
experiments/results/e2_replay.csv. The programs are the harness's binaries next to the traces.
"""
from __future__ import annotations
import argparse
import collections
import csv
import glob
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from rematch import trace  # noqa: E402
from rematch.confirm import classify_rc, run_pinned  # noqa: E402
from rematch.feasible import feasible_profile, profile_summary  # noqa: E402
from rematch.matching import candidates_full  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from provenance import code_commit  # noqa: E402
from b_impl import FORCED  # noqa: E402


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
    ap.add_argument("--replay", action="store_true", help="rerun 2-buffering programs with default and forced-rendezvous eager limits")
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()
    replays = []
    forced_eager = 0          # the largest payload the forced setting still sends eagerly
    bi = os.path.join(os.path.dirname(args.out), "b_impl.csv")
    if not os.path.exists(bi):
        bi = os.path.join(ROOT, "experiments", "results", "b_impl.csv")
    if os.path.exists(bi):
        for r in csv.DictReader(open(bi)):
            if r["setting"] == "openmpi-forced-rendezvous":
                forced_eager = int(r["eager_max_bytes"])
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
        # the program: next to a corpus trace, or the idiom's own binary for traces/<mpi>/<idiom>
        exe = os.path.join(os.path.dirname(d), "main")
        parts = os.path.relpath(d, ROOT).split(os.sep)
        if len(parts) == 3 and parts[0] == "traces":
            exe = os.path.join(ROOT, "idioms", parts[2], "bin", parts[1], "main")
        finished = not t.truncated and not t.blocked_calls()
        if args.replay and finished and at_inf and minB and minB > 0 and os.path.exists(exe):
            # a run that finished and is realisable only above minB bytes: it finishes again with
            # the default limits and hangs when the eager size is forced below minB; a bound the
            # forced size cannot get under (256 bytes on Open MPI, experiments/b_impl.py) is untestable
            for setting, margs in (("default", []), ("forced-rendezvous", FORCED)):
                r = run_pinned(t.np, exe, [], None, timeout=args.timeout, mpirun_args=margs)
                res = classify_rc(r.returncode, r.stderr, r.elapsed, args.timeout)
                if setting == "default" or minB <= forced_eager:
                    # the eager size in force is at least the bound: the run must finish (when the
                    # forced size cannot get under the bound, the deadlock below it is untested,
                    # but the prediction above it still is)
                    verdict = "as predicted" if res == "FINISHED" else "not as predicted"
                else:
                    verdict = "as predicted" if res in ("TIMEOUT", "KILLED") else "not as predicted"
                replays.append({"trace": name, "bound": minB, "setting": setting, "result": res, "verdict": verdict})
                print(f"{name}: B < {minB}: {setting} -> {res} ({verdict})", flush=True)
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
        w = csv.DictWriter(fh, fieldnames=["trace", "what", "minB", "at_inf", "profile", "B0", "B4096", "Binf", "commit"], lineterminator="\n")
        w.writeheader()
        w.writerows({**r, "commit": code_commit()} for r in rows)
    if args.replay:
        rp = os.path.join(os.path.dirname(args.out), "e2_replay.csv")
        with open(rp, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["trace", "bound", "setting", "result", "verdict", "commit"], lineterminator="\n")
            w.writeheader()
            w.writerows({**r, "commit": code_commit()} for r in replays)
        c = collections.Counter(r["verdict"] for r in replays)
        print(f"replays={len(replays)} {dict(c)} wrote {rp}")
    obs = [r for r in rows if r["what"] == "observed"]
    alt = [r for r in rows if r["what"] != "observed"]
    print(f"traces={len(obs)} observed-infeasible-at-B0={sum(1 for r in obs if not r['B0'])} "
          f"alternatives={len(alt)} feasible@0={sum(1 for r in alt if r['B0'])} feasible@4096={sum(1 for r in alt if r['B4096'])} "
          f"feasible@inf={sum(1 for r in alt if r['Binf'])} B-dependent={sum(1 for r in alt if r['B0'] != r['Binf'])}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
