#!/usr/bin/env python3
"""E6: the polynomial checker against the exhaustive explorer on the trace as a
straight-line program (single-path). For every trace with <= --max-events events and
every candidate alternative (r <- s):
  checker : feasible.check(M', SP mode) at B in {0, inf}
  explorer: explore(program_from_trace, B, force={r: s}) reaches an outcome with (r, s)
Soundness: if the checker accepts M', some execution realises every pair of M' on the
receives that complete inside its cut (disagreements of that kind must be 0).
Completeness: if some execution realises exactly M' (receives absent from M' stay
unmatched), the checker must accept it. A matching that needs a second re-matched receive
is a different alternative (the k-change rung) and is not counted against the
single-change checker.
"""
from __future__ import annotations
import argparse
import csv
import glob
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from rematch import dsl, explore, trace  # noqa: E402
from rematch.feasible import check  # noqa: E402
from rematch.matching import candidates_full  # noqa: E402


def completes_inside(r, cut) -> bool:
    """A receive is kept by the cut when it completes before it (a nonblocking receive
    completes at its wait, not at its post)."""
    c = r.complete_idx if r.complete_idx is not None else r.idx
    return cut is None or c < cut.get(r.rank, 10**9)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("--max-events", type=int, default=2000)
    ap.add_argument("--max-states", type=int, default=300000)
    ap.add_argument("--out", default=os.path.join(ROOT, "experiments", "results", "e6_checker_vs_explorer.csv"))
    args = ap.parse_args()
    dirs = args.dirs or sorted(glob.glob(os.path.join(ROOT, "traces", "openmpi", "*")) +
                               glob.glob(os.path.join(ROOT, "experiments", "results", "*", "*", "trace")))
    rows = []
    unsound = incomplete = agree = skipped = 0
    tc = te = 0.0
    for d in dirs:
        try:
            t = trace.load(d)
        except Exception:
            continue
        if sum(len(v) for v in t.ranks.values()) > args.max_events:
            skipped += 1
            continue
        prog = dsl.program_from_trace(t)
        alts, _ = candidates_full(t)
        for a in alts:
            for B in (0, None):
                t0 = time.perf_counter()
                v = check(t, a.M, a.changed, B, "SP")
                t1 = time.perf_counter()
                try:
                    res = explore.explore(prog, B, force={a.recv: a.send}, max_states=args.max_states, comms=t.comm_ranks)
                    outs = [{rk: sk for rk, sk in o.matching if rk in t.recvs} for o in res.outcomes]
                    # soundness: the checker's claim is about the kept prefix, so every pair of M on
                    # a receive that completes inside the cut must occur together in some execution
                    kept = {rk: sk for rk, sk in a.M.items() if completes_inside(t.recvs[rk], v.cut)}
                    reach_any = any(all(om.get(rk) == sk for rk, sk in kept.items()) for om in outs)
                    # completeness: some execution realises exactly M (receives absent from M stay unmatched)
                    reach = any(om == a.M for om in outs)
                    exp_err = ""
                except RuntimeError:
                    reach, reach_any, exp_err = None, None, "state-space"
                except explore.ExploreError as e:
                    reach, reach_any, exp_err = None, None, str(e)
                t2 = time.perf_counter()
                tc += t1 - t0
                te += t2 - t1
                if reach is None:
                    kind = "skip"
                elif v.feasible and not reach_any:
                    kind = "UNSOUND"
                elif not v.feasible and reach:
                    kind = "incomplete"
                else:
                    kind = "agree"
                if kind == "agree":
                    agree += 1
                elif kind == "UNSOUND":
                    unsound += 1
                elif kind == "incomplete":
                    incomplete += 1
                rows.append({"trace": d.replace(ROOT + "/", ""), "alt": f"{a.recv}<-{a.send} [{a.kind}]", "B": "inf" if B is None else B,
                             "checker": v.feasible, "checker_reason": v.reason, "explorer_exact": reach, "explorer_prefix": reach_any, "note": exp_err,
                             "t_checker_ms": round((t1 - t0) * 1000, 2), "t_explorer_ms": round((t2 - t1) * 1000, 2), "kind": kind})
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["trace"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"pairs={len(rows)} agree={agree} unsound={unsound} incomplete={incomplete} skipped-traces={skipped} "
          f"checker-time={tc:.2f}s explorer-time={te:.2f}s ratio={te / tc if tc else 0:.1f}x")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
