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
An execution in which a ready send inside the horizon, or one that must be issued before a pair
the comparison looks for is matched, is not taken by a receive posted before its issue is
erroneous (MPI 4.1 §3.4) and counts for neither (model_executions); the column note says when
such an execution alone would have changed the row's verdict.

With --reads, the checker cuts the rank of a changed receive at a read bound (rule H1',
docs/firstread.md) instead of right after the receive: `never` (no result is ever read, the
whole rest of the rank stays) or `random` (a bound drawn per receive between the receive and
the end of its rank). A straight-line program reads nothing, so every bound is sound for it, and
the comparison checks theorem T1 with the longer windows. Without --reads the checker is H1.

Besides the corpus traces, --synthetic N adds N random straight-line programs that complete
(dsl.random_matched_program: rounds of sends and receives, mostly wildcards, standard and
buffered and nonblocking sends, sub-communicators, shaped collectives; with --mixed, a share of
them interleave sends, receives and waits and use synchronous and ready sends) whose execution the
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
from stretto import dsl, explore, trace  # noqa: E402
from stretto.feasible import check  # noqa: E402
from stretto.matching import build_graph, candidates_full, match_node  # noqa: E402
from stretto.deadlock import contained_deadlock  # noqa: E402
from stretto.reads import NeverReads, Table  # noqa: E402
from provenance import code_commit  # noqa: E402


def read_bounds(t, kind, rng):
    """The bounds of --reads for trace t: None (rule H1), every result never read, or a bound
    drawn per completed receive between the record after its completion and the rank's end."""
    if kind is None:
        return None
    if kind == "never":
        return NeverReads()
    out = {}
    for r in sorted(t.recvs.values(), key=lambda r: r.key):
        if r.completed:
            c = r.complete_idx if r.complete_idx is not None else r.idx
            out[(r.rank, c)] = rng.randint(c + 1, len(t.ranks[r.rank]))
    return Table(out)


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


def model_executions(t, outcomes, cut, pairs, B):
    """The explorer's executions that are correct where the comparison looks: every ready send
    that the horizon keeps (cut, None for the whole trace), or that must be issued before one of
    the pairs (receive -> send) is matched, is taken by a receive posted before its issue
    (explore.ready_errors). The others are erroneous (MPI 4.1 §3.4) and realise nothing: the
    check's graph has the edge issue(r) -> issue(s) and its horizon removes a ready send without
    its receive (rule 7). "Must be issued before" is reachability in the constraint graph of the
    execution's matching at B, without the edges of its erroneous ready sends: a pair beyond the
    cut (the changed receive of a rejected alternative) needs what precedes it, and an accepted
    alternative's pairs lie in the horizon, which holds their predecessors. A ready send that
    precedes neither is not the claim's business, and its error does not change what the
    explorer delivers."""
    ready = {s.key for s in t.sends.values() if s.ready_mode}
    out = []
    for o in outcomes:
        bad = explore.ready_errors(o)
        if any(cut is None or t.sends[k].idx < cut.get(k[0], 10**9) for k in bad):
            continue
        if bad:
            mo = {rk: sk for rk, sk in o.matching if rk in t.recvs}
            targets = {match_node(t.recvs[rk]) for rk, sk in pairs.items() if mo.get(rk) == sk}
            if targets:
                g = build_graph(t, mo, None, B, ready=ready - bad)
                if any(targets & g.reach_set(t.sends[k].issue_node()) for k in bad):
                    continue
        out.append(o)
    return out


def compare(t, name, args, rows, counts, times):
    prog = dsl.program_from_trace(t)
    alts, _ = candidates_full(t)
    bounds = read_bounds(t, args.reads, random.Random(f"{args.seed}/{name}"))
    for a in alts:
        for B in (0, None):
            t0 = time.perf_counter()
            v = check(t, a.M, a.changed, B, "L1", bounds=bounds)
            t1 = time.perf_counter()
            try:
                res = explore.explore(prog, B, force={a.recv: a.send}, max_states=args.max_states, comms=t.comm_members)
                keys = set(judged(t, a.M, v.cut)) | set(a.changed)
                if a.kind == "steal" and a.stolen_from is not None:
                    keys.add(a.stolen_from)        # left without a message: the execution must leave it so
                pairs = {rk: a.M[rk] for rk in keys if rk in a.M}

                def reached(outcomes):
                    outs = [{rk: sk for rk, sk in o.matching if rk in t.recvs} for o in outcomes]
                    # soundness: the judged pairs of M' occur together in some execution
                    prefix = any(all(om.get(rk) == sk for rk, sk in pairs.items()) for om in outs)
                    # completeness: some execution agrees with M' on every judged receive, matched
                    # alike or unmatched, and on the changed receive (a rejection that cuts the changed
                    # receive itself claims its new match cannot happen within what the trace determines)
                    kept_exact = any(all(om.get(rk) == a.M.get(rk) for rk in keys) for om in outs)
                    return prefix, kept_exact, any(om == a.M for om in outs)

                # an execution with a ready send of the horizon issued before the receive that takes it
                # is erroneous and realises nothing; the note says when one would have changed the row
                clean = model_executions(t, res.outcomes, v.cut, pairs, B)
                reach_prefix, reach_kept_exact, reach_exact = reached(clean)
                exp_err = ""
                if len(clean) < len(res.outcomes) and reached(res.outcomes)[:2] != (reach_prefix, reach_kept_exact):
                    exp_err = "realised only with a ready send before its receive"
                # theorem T7: a contained deadlock means every execution with the changed match deadlocks
                rule1 = bounds.rule1(t, a.changed) if bounds is not None else None
                dl = contained_deadlock(t, a.M, a.changed, v.cut, v.issued, B, rule1) if v.feasible else None
                with_pair = [o for o in clean if (a.recv, a.send) in o.matching]
                t7 = "" if not dl else ("ok" if with_pair and all(o.kind == "deadlock" for o in with_pair) else "UNSOUND")
            except explore.StateCapReached:
                reach_prefix = reach_kept_exact = reach_exact = None
                exp_err, t7 = "state-space", ""
            except explore.ExploreError as e:
                reach_prefix = reach_kept_exact = reach_exact = None
                exp_err, t7 = str(e), ""
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
            if t7:
                counts["t7_" + t7] += 1
            row = {"trace": name, "events": sum(len(v_) for v_ in t.ranks.values()), "alt": f"{a.recv}<-{a.send} [{a.kind}]",
                   "B": "inf" if B is None else B, "checker": v.feasible, "checker_reason": v.reason,
                   "explorer_prefix": reach_prefix, "explorer_kept_exact": reach_kept_exact, "explorer_exact": reach_exact,
                   "note": exp_err, "t_checker_ms": round((t1 - t0) * 1000, 2), "t_explorer_ms": round((t2 - t1) * 1000, 2), "kind": kind,
                   "l1_deadlock": t7, "commit": code_commit()}
            if bounds is not None:
                # does the bound keep records of the changed rank after the receive (rule H1')?
                row["window"] = bounds.extends(t, a.changed)
            rows.append(row)


def synthetic(args, rows, counts, times) -> int:
    """Random straight-line programs whose explorer-chosen execution becomes the trace."""
    rng = random.Random(args.seed)
    made = 0
    attempts = 0
    while made < args.synthetic and attempts < args.synthetic * 20:
        attempts += 1
        nranks = rng.choice([3, 4, 4, 5])
        prog, comms = dsl.random_matched_program(rng, nranks, mixed=rng.random() < args.mixed)
        try:
            res = explore.explore(prog, None, comms=comms, max_states=args.max_states)
        except (explore.StateCapReached, explore.ExploreError):
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
    ap.add_argument("--mixed", type=float, default=0.5,
                    help="share of synthetic programs that interleave sends, receives and waits and use ssend/rsend")
    ap.add_argument("--seed", type=int, default=20261016)
    ap.add_argument("--reads", choices=["never", "random"], default=None,
                    help="judge with read bounds (rule H1'): no result ever read, or a random bound per receive")
    ap.add_argument("--out", default=None, help="default: results/e6_checker_vs_explorer.csv, or e6_reads_<kind>.csv with --reads")
    args = ap.parse_args()
    if args.out is None:
        args.out = os.path.join(ROOT, "experiments", "results",
                                f"e6_reads_{args.reads}.csv" if args.reads else "e6_checker_vs_explorer.csv")
    dirs = args.dirs or sorted(glob.glob(os.path.join(ROOT, "traces", "openmpi", "*")) +
                               glob.glob(os.path.join(ROOT, "experiments", "results", "*", "*", "trace")))
    rows: list = []
    counts = {"agree": 0, "UNSOUND": 0, "incomplete": 0, "skip": 0, "t7_ok": 0, "t7_UNSOUND": 0}
    times = [0.0, 0.0]
    skipped = 0
    unloadable = []
    for d in dirs:
        try:
            t = trace.load(d)
        except (OSError, ValueError, KeyError) as e:
            # a run that left no or a damaged trace (its program crashed, the harness killed it) is
            # counted and named; any other exception is a bug of the loader and stops the run
            unloadable.append(f"{d.replace(ROOT + '/', '')}: {type(e).__name__}: {e}"[:200])
            continue
        if sum(len(v) for v in t.ranks.values()) > args.max_events:
            skipped += 1
            continue
        compare(t, d.replace(ROOT + "/", ""), args, rows, counts, times)
    made = synthetic(args, rows, counts, times) if args.synthetic else 0
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    keys = ["trace", "events", "alt", "B", "checker", "checker_reason", "explorer_prefix", "explorer_kept_exact", "explorer_exact",
            "note", "t_checker_ms", "t_explorer_ms", "kind", "l1_deadlock", "commit"] + (["window"] if args.reads else [])
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    ev = [r["events"] for r in rows]
    if args.reads:
        print(f"read bounds {args.reads}: {sum(1 for r in rows if r.get('window'))} pairs with a window after the changed receive")
    print(f"pairs={len(rows)} agree={counts['agree']} unsound={counts['UNSOUND']} incomplete={counts['incomplete']} "
          f"l1-deadlocks ok={counts['t7_ok']} unsound={counts['t7_UNSOUND']} "
          f"skipped-traces={skipped} synthetic-programs={made} events max={max(ev) if ev else 0} "
          f"checker-time={times[0]:.2f}s explorer-time={times[1]:.2f}s ratio={times[1] / times[0] if times[0] else 0:.1f}x")
    if unloadable:
        print(f"{len(unloadable)} trace directories could not be loaded and are not in the table:")
        for u in unloadable:
            print("  " + u)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
