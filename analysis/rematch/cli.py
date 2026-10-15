"""python -m rematch <command> ..."""
from __future__ import annotations
import argparse
import json
import sys

from . import confirm as confirm_mod, dsl, explore, report, trace
from .feasible import check
from .matching import candidates_full


def _B(s: str):
    return None if s in ("inf", "none", "None") else int(s)


def cmd_show(args):
    t = trace.load(args.tracedir)
    for rank in sorted(t.ranks):
        print(f"rank {rank}:")
        for rec in t.ranks[rank]:
            raw = {k: v for k, v in rec.raw.items() if k not in ("r", "seq", "op")}
            print(f"  #{rec.idx:<3} {rec.op:<8} {raw}")
    print("communicators: " + ", ".join(f"{k}={v}" for k, v in sorted(t.comm_ranks.items())))
    print("observed matching:")
    for rk, sk in sorted(t.observed.items()):
        print(f"  {t.recvs[rk]}  <-  {t.sends[sk]}")
    unmatched = [r for r in t.recvs.values() if r.key not in t.observed]
    if unmatched:
        print("unmatched receives: " + ", ".join(str(r) for r in unmatched))
    for p in t.probes.values():
        print(f"  {p} saw {t.sends[p.seen] if p.seen else 'nothing'}")
    for w in t.warnings:
        print("warning: " + w)
    return 0


def cmd_analyze(args):
    t = trace.load(args.tracedir)
    rep = report.analyze(t, with_sp=not args.no_sp)
    if args.json:
        print(json.dumps([f.__dict__ for f in rep.findings], default=str, indent=1))
    else:
        print(report.format_report(t, rep, show_infeasible=args.verbose))
    return 0


def cmd_candidates(args):
    t = trace.load(args.tracedir)
    alts, pruned = candidates_full(t)
    for alt in alts:
        v0 = check(t, alt.M, alt.changed, 0, args.mode)
        vi = check(t, alt.M, alt.changed, None, args.mode)
        print(f"{alt.describe(t)} [{alt.kind}]  B=0: {'ok' if v0.feasible else v0.reason}  B=inf: {'ok' if vi.feasible else vi.reason}")
    for d, why in pruned:
        print(f"pruned: {d}: {why}")
    return 0


def cmd_oracle(args):
    t = trace.load(args.tracedir)
    prog = dsl.program_from_trace(t)
    for B in [_B(b) for b in args.B]:
        res = explore.explore(prog, B, comms=t.comm_ranks)
        print(f"B={'inf' if B is None else B}: {res.states} states, {len(res.outcomes)} distinct outcomes")
        for o in res.outcomes:
            print("  " + str(o))
    return 0


def cmd_confirm(args):
    t = trace.load(args.tracedir)
    rep = report.analyze(t, with_sp=not args.no_sp)
    res = confirm_mod.confirm(rep, args.np or t.np, args.exe, args.args, timeout=args.timeout, mpi=args.mpi,
                              only_errors=not args.all)
    if args.json:
        print(json.dumps([{"cls": c.finding.cls, "level": c.finding.level, "title": c.finding.title, "pin": c.pin,
                           "result": c.result, "verdict": c.verdict} for c in res], indent=1))
    else:
        for c in res:
            print(f"[{c.finding.cls:>12}] {c.finding.level:<6} {c.pin:<28} -> {c.result:<28} {c.verdict}")
        n = sum(1 for c in res if c.verdict == "confirmed")
        m = sum(1 for c in res if c.verdict == "not-confirmed")
        print(f"confirmed {n}, not confirmed {m}, informational {len(res) - n - m}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rematch", description="sound prediction of alternative MPI matchings")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("show", help="print events and the observed matching"); a.add_argument("tracedir"); a.set_defaults(fn=cmd_show)
    a = sub.add_parser("analyze", help="predict alternatives and classify them"); a.add_argument("tracedir")
    a.add_argument("--no-sp", action="store_true", help="skip the single-path explorer"); a.add_argument("--json", action="store_true")
    a.add_argument("-v", "--verbose", action="store_true", help="also list rejected alternatives"); a.set_defaults(fn=cmd_analyze)
    a = sub.add_parser("candidates", help="list every candidate alternative with its verdict"); a.add_argument("tracedir")
    a.add_argument("--mode", default="L1", choices=["L1", "SP"]); a.set_defaults(fn=cmd_candidates)
    a = sub.add_parser("confirm", help="replay each finding with its REMATCH_PIN and classify the outcome")
    a.add_argument("tracedir"); a.add_argument("exe"); a.add_argument("args", nargs="*")
    a.add_argument("--np", type=int, default=0); a.add_argument("--timeout", type=int, default=20)
    a.add_argument("--mpi", default="openmpi"); a.add_argument("--all", action="store_true", help="also replay informational findings")
    a.add_argument("--no-sp", action="store_true"); a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_confirm)
    a = sub.add_parser("oracle", help="exhaustively explore the trace as a single-path program"); a.add_argument("tracedir")
    a.add_argument("--B", nargs="*", default=["0", "inf"]); a.set_defaults(fn=cmd_oracle)
    args = p.parse_args(argv)
    return args.fn(args)
