#!/usr/bin/env python3
"""E6: the polynomial checker against the exhaustive explorer on the trace as a
straight-line program (single-path). For every trace with <= --max-events events and every
candidate alternative (r <- s), at B in {0, inf}:
  checker : feasible.check(M', mode L1), the claim the report makes
  explorer: explore(program_from_trace, B, force={r: s}) over every interleaving
The checker's claim is about the kept prefix (its cut): the receives *judged* are those posted
inside the cut whose message in M' is issued inside the cut, or which M' leaves unmatched; a
match is decided when message and receive are both present, so a receive whose wait lies
beyond the cut is still judged. "Every judged pair of M' is realised together by some
execution, and the judged receives M' leaves unmatched stay unmatched."
  Soundness   : the checker accepts  =>  some execution realises the judged pairs    (unsound otherwise)
  Completeness: some execution agrees with M' on every judged receive (matched alike, or
                unmatched), on the changed receive and on the receive a steal leaves without a
                message  =>  the checker accepts  (incomplete otherwise)
Each pair also records whether some execution realises M' in full (explorer_exact), which the
single-change checker does not claim: the receives beyond the horizon are not its business.

Besides the corpus traces, --synthetic N adds N random straight-line programs that complete
(dsl.random_matched_program: rounds of sends then receives, mostly wildcards, standard and
buffered and nonblocking sends, sub-communicators, shaped collectives) whose execution the
explorer chose is written back as a trace (dsl.trace_from_execution), so that the comparison
also covers traces with more events and several wildcard receives per rank.
"""
from __future__ import annotations
import argparse
import csv
import glob
import os
import random
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from rematch import dsl, explore, trace  # noqa: E402
from rematch.feasible import check  # noqa: E402
from rematch.matching import candidates_full  # noqa: E402


def judged(t, M, cut):
    """Receives the checker's claim covers: posted inside the cut and either unmatched in M
    or matched to a send issued inside the cut."""
    out = []
    for rk, r in t.recvs.items():
        if cut is not None and r.idx >= cut.get(r.rank, 10**9):
            continue
        sk = M.get(rk)
        if sk is None or cut is None or t.sends[sk].idx < cut.get(sk[0], 10**9):
            out.append(rk)
    return out


def compare(t, name, args, rows, counts, times):
    prog = dsl.program_from_trace(t)
    alts, _ = candidates_full(t)
    for a in alts:
        for B in (0, None):
            t0 = time.perf_counter()
            v = check(t, a.M, a.changed, B, "L1")
            t1 = time.perf_counter()
            try:
                res = explore.explore(prog, B, force={a.recv: a.send}, max_states=args.max_states, comms=t.comm_members)
                outs = [{rk: sk for rk, sk in o.matching if rk in t.recvs} for o in res.outcomes]
                keys = set(judged(t, a.M, v.cut)) | set(a.changed)
                if a.kind == "steal" and a.stolen_from is not None:
                    keys.add(a.stolen_from)        # left without a message: the execution must leave it so
                pairs = {rk: a.M[rk] for rk in keys if rk in a.M}
                # soundness: the judged pairs of M' occur together in some execution
                reach_prefix = any(all(om.get(rk) == sk for rk, sk in pairs.items()) for om in outs)
                # completeness: some execution agrees with M' on every judged receive, matched
                # alike or unmatched, and on the changed receive (a rejection that cuts the changed
                # receive itself claims its new match cannot happen within what the trace determines)
                reach_kept_exact = any(all(om.get(rk) == a.M.get(rk) for rk in keys) for om in outs)
                reach_exact = any(om == a.M for om in outs)
                exp_err = ""
            except RuntimeError:
                reach_prefix = reach_kept_exact = reach_exact = None
                exp_err = "state-space"
            except explore.ExploreError as e:
                reach_prefix = reach_kept_exact = reach_exact = None
                exp_err = str(e)
            t2 = time.perf_counter()
            times[0] += t1 - t0
            times[1] += t2 - t1
            if reach_prefix is None:
                kind = "skip"
            elif v.feasible and not reach_prefix:
                kind = "UNSOUND"
            elif not v.feasible and reach_kept_exact:
                kind = "incomplete"
            else:
                kind = "agree"
            counts[kind] += 1
            rows.append({"trace": name, "events": sum(len(v_) for v_ in t.ranks.values()), "alt": f"{a.recv}<-{a.send} [{a.kind}]",
                         "B": "inf" if B is None else B, "checker": v.feasible, "checker_reason": v.reason,
                         "explorer_prefix": reach_prefix, "explorer_kept_exact": reach_kept_exact, "explorer_exact": reach_exact,
                         "note": exp_err, "t_checker_ms": round((t1 - t0) * 1000, 2), "t_explorer_ms": round((t2 - t1) * 1000, 2), "kind": kind})


def synthetic(args, rows, counts, times) -> int:
    """Random straight-line programs whose explorer-chosen execution becomes the trace."""
    rng = random.Random(args.seed)
    made = 0
    attempts = 0
    while made < args.synthetic and attempts < args.synthetic * 20:
        attempts += 1
        nranks = rng.choice([3, 4, 4, 5])
        prog, comms = dsl.random_matched_program(rng, nranks)
        try:
            res = explore.explore(prog, None, comms=comms, max_states=args.max_states)
        except (RuntimeError, explore.ExploreError):
            continue
        done = [o for o in res.outcomes if o.kind == "done" and not o.leaked and not o.errors]
        if not done:
            continue
        o = rng.choice(done)
        t = trace.Trace()
        trace._build(t, dsl.trace_from_execution(prog, dict(o.matching), comms))
        if not any(r.wildcard for r in t.recvs.values()):
            continue
        made += 1
        compare(t, f"synthetic/{made:03d}", args, rows, counts, times)
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--max-events", type=int, default=2000)
    ap.add_argument("--max-states", type=int, default=300000)
    ap.add_argument("--synthetic", type=int, default=0, help="also compare on N random straight-line programs")
    ap.add_argument("--seed", type=int, default=20261016)
    ap.add_argument("--out", default=os.path.join(ROOT, "experiments", "results", "e6_checker_vs_explorer.csv"))
    args = ap.parse_args()
    dirs = args.dirs or sorted(glob.glob(os.path.join(ROOT, "traces", "openmpi", "*")) +
                               glob.glob(os.path.join(ROOT, "experiments", "results", "*", "*", "trace")))
    rows: list = []
    counts = {"agree": 0, "UNSOUND": 0, "incomplete": 0, "skip": 0}
    times = [0.0, 0.0]
    skipped = 0
    for d in dirs:
        try:
            t = trace.load(d)
        except Exception:
            continue
        if sum(len(v) for v in t.ranks.values()) > args.max_events:
            skipped += 1
            continue
        compare(t, d.replace(ROOT + "/", ""), args, rows, counts, times)
    made = synthetic(args, rows, counts, times) if args.synthetic else 0
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    keys = ["trace", "events", "alt", "B", "checker", "checker_reason", "explorer_prefix", "explorer_kept_exact", "explorer_exact",
            "note", "t_checker_ms", "t_explorer_ms", "kind"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    ev = [r["events"] for r in rows]
    print(f"pairs={len(rows)} agree={counts['agree']} unsound={counts['UNSOUND']} incomplete={counts['incomplete']} "
          f"skipped-traces={skipped} synthetic-programs={made} events max={max(ev) if ev else 0} "
          f"checker-time={times[0]:.2f}s explorer-time={times[1]:.2f}s ratio={times[1] / times[0] if times[0] else 0:.1f}x")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
