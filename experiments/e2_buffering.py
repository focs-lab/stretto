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
from stretto import trace  # noqa: E402
from stretto.confirm import classify_rc, run_pinned  # noqa: E402
from stretto.feasible import feasible_profile, profile_summary  # noqa: E402
from stretto.matching import candidates_full  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from provenance import code_commit  # noqa: E402
from b_impl import TABLE, tcp, tcp_header  # noqa: E402


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
    ap.add_argument("--replay", action="store_true", help="rerun 2-buffering programs with the default limits and with the eager threshold set just below and at their bound")
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()
    replays = []
    # the TCP transport's header: threshold B = eager limit - header
    bi = os.path.join(os.path.dirname(args.out), "b_impl.csv")
    header = tcp_header(bi if os.path.exists(bi) else TABLE)
    dirs = args.dirs or sorted(glob.glob(os.path.join(ROOT, "traces", "openmpi", "*")) +
                               glob.glob(os.path.join(ROOT, "experiments", "results", "*", "*", "trace")))
    rows = []
    unloadable = []
    for d in dirs:
        try:
            t = trace.load(d)
        except (OSError, ValueError, KeyError) as e:
            # counted and named below; any other exception is a bug of the loader and stops the run
            unloadable.append(f"{d.replace(ROOT + '/', '')}: {type(e).__name__}: {e}"[:200])
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
        if args.replay and finished and at_inf and minB and minB > 0 and os.path.exists(exe) and header is not None:
            # a run that finished and is realisable only from minB bytes on: it finishes again with
            # the default limits; on the TCP transport, whose every send path buffers L - header
            # bytes for eager limit L, it hangs with the threshold at minB - 1 and finishes with it at
            # minB. A self-send goes through the self transport, which sends up to 256 bytes inline
            # whatever its limit, so below the bound a program with one is untestable.
            self_send = any(x.dst == x.rank for x in t.sends.values())
            for setting, margs in (("default", []), ("below the bound", tcp(minB - 1 + header)), ("at the bound", tcp(minB + header))):
                r = run_pinned(t.np, exe, [], None, timeout=args.timeout, mpirun_args=margs)
                res = classify_rc(r.returncode, r.stderr, r.elapsed, args.timeout)
                if setting == "below the bound":
                    verdict = ("untestable" if self_send and minB <= 256 else
                               "as predicted" if res in ("TIMEOUT", "KILLED") else "not as predicted")
                else:
                    verdict = "as predicted" if res == "FINISHED" else "not as predicted"
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
    if unloadable:
        print(f"{len(unloadable)} trace directories could not be loaded and are not in the table:")
        for u in unloadable:
            print("  " + u)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
