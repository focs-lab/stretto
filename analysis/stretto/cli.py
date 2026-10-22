"""python -m stretto <command> ..."""
from __future__ import annotations
import argparse
import json
import os
import shlex
import sys

from . import confirm as confirm_mod, dsl, explore, report, trace
from .feasible import check
from .matching import candidates_full


def _B(s: str):
    return None if s in ("inf", "none", "None") else int(s)


class LoadError(Exception):
    """The trace directory cannot be loaded (exit status 2); an error anywhere else is a
    bug and propagates with its traceback instead of masquerading as a load failure."""


def _load(tracedir: str):
    try:
        return trace.load(tracedir)
    except (FileNotFoundError, NotADirectoryError, ValueError, KeyError) as e:
        raise LoadError(f"cannot load {tracedir}: {e!r}") from e


def cmd_show(args):
    t = _load(args.tracedir)
    for rank in sorted(t.ranks):
        print(f"rank {rank}:")
        objs = {x.idx: x for x in list(t.sends_of(rank)) + list(t.recvs_of(rank))}
        objs.update({c.idx: c for c in t.colls if c.rank == rank})
        for rec in t.ranks[rank]:
            if rec.raw is not None:
                raw = {k: v for k, v in rec.raw.items() if k not in ("r", "seq", "op")}
                print(f"  #{rec.idx:<3} {rec.op:<8} {raw}")
            else:
                print(f"  #{rec.idx:<3} {rec.op:<8} {objs.get(rec.idx, '')}")
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
    for _, w in t.incomplete:
        print("incomplete: " + w)
    return 0


def _analyze(args):
    t = _load(args.tracedir)
    twin, label = None, "twin"
    if args.rerun:
        # level L2 from the program itself: ranks re-executed alone under the shim (stretto.rerun)
        from . import rerun
        runner = rerun.ShimRunner(args.rerun, shlex.split(args.rerun_args), np=t.np, mpi=args.rerun_mpi,
                                  timeout=args.rerun_timeout, cwd=args.rerun_cwd)
        twin, label = rerun.Oracle(t, runner).program(), "re-execution"
    return t, report.analyze(t, with_sp=not args.no_sp, sp_max_events=args.sp_max_events,
                             max_states=args.max_states, generic=args.generic, full_profile=args.full_profile,
                             changes=args.changes, twin=twin, twin_label=label, reads=read_bounds(args, t),
                             cross_check=args.cross_check)


def read_bounds(args, t=None):
    """The read bounds of --reads (stretto.reads.SiteMap), or None. The trace's call sites are
    named with addr2line in the object the tracer recorded or, if it has moved, in the one of
    the same name in --objdir (default: the trace directory's parent). The map is refused
    unless it was made from the sources the traced program was built from
    (stretto.reads.check_sources), or --reads-unchecked accepts it as it is."""
    if not getattr(args, "reads", None):
        return None
    from . import reads
    objdir = args.objdir or os.path.dirname(os.path.abspath(args.tracedir.rstrip("/")))
    try:
        return reads.SiteMap.load(args.reads, reads.addr2line_resolver(objdir), t=t, objdir=objdir,
                                  unchecked=getattr(args, "reads_unchecked", False))
    except (OSError, ValueError, KeyError) as e:
        raise LoadError(f"cannot load the read bounds {args.reads}: {e}") from e


def cmd_analyze(args):
    t, rep = _analyze(args)
    if args.json:
        print(json.dumps(report.to_json(t, rep), default=str, indent=1))
    else:
        print(report.format_report(t, rep, show_infeasible=args.verbose))
    return 1 if rep.has_error() else 0


def cmd_candidates(args):
    t = _load(args.tracedir)
    alts, pruned = candidates_full(t)
    for alt in alts:
        v0 = check(t, alt.M, alt.changed, 0, args.mode)
        vi = check(t, alt.M, alt.changed, None, args.mode)
        print(f"{alt.describe(t)} [{alt.kind}]  B=0: {'ok' if v0.feasible else v0.reason}  B=inf: {'ok' if vi.feasible else vi.reason}")
    for d, why in pruned:
        print(f"pruned: {d}: {why}")
    return 0


def cmd_oracle(args):
    t = _load(args.tracedir)
    prog = dsl.program_from_trace(t)
    for B in [_B(b) for b in args.B]:
        res = explore.explore(prog, B, comms=t.comm_members)
        print(f"B={'inf' if B is None else B}: {res.states} states, {len(res.outcomes)} distinct outcomes")
        for o in res.outcomes:
            print("  " + str(o))
    return 0


def cmd_confirm(args):
    t, rep = _analyze(args)
    res = confirm_mod.confirm(rep, args.np or t.np, args.exe, args.args, timeout=args.timeout, mpi=args.mpi,
                              only_errors=not args.all, t=t, keep_as=tuple(args.keep) if args.keep else None)
    if args.json:
        print(json.dumps([{"cls": c.finding.cls, "level": c.finding.level, "title": c.finding.title, "pin": c.pin,
                           "result": c.result, "pin_applied": c.pin_applied, "pin_matched": c.pin_matched,
                           "control": c.control, "verdict": c.verdict, "run_valid": c.run_valid, "violation": c.violation,
                           "control_valid": c.control_valid, "control_violation": c.control_violation,
                           "strict_verdict": c.strict, "misapplied": c.misapplied} for c in res], indent=1))
    else:
        for c in res:
            print(f"[{c.finding.cls:>12}] {c.finding.level:<6} {c.pin:<28} -> {c.result:<14} "
                  f"{'matched' if c.pin_matched else 'applied' if c.pin_applied else 'pin NOT applied':<16} "
                  f"control {c.control or '-':<10} {c.verdict}"
                  + (f", run of the program: {c.run_valid}, strict: {c.strict}" if c.run_valid else ""))
            if c.violation:
                print(f"               {c.violation}")
            if c.misapplied:
                print(f"               pin misapplied: {c.misapplied}")
        n = sum(1 for c in res if c.verdict == "confirmed")
        m = sum(1 for c in res if c.verdict == "not-confirmed")
        k = sum(1 for c in res if c.verdict == "inconclusive")
        s = sum(1 for c in res if c.strict == "confirmed")
        print(f"confirmed {n} ({s} with a replay that is a run of the program), not confirmed {m}, inconclusive {k}, "
              f"informational {len(res) - n - m - k}")
    return 0


def cmd_sites(args):
    from . import sites as sites_mod
    t = _load(args.tracedir)
    names = {label: where for label, where, _ in sites_mod.sites(t)}
    for label, where, n in sites_mod.sites(t):
        print(f"{label:<28} {n:>8} records  {where}")
    if args.findings:
        _, rep = _analyze(args)
        for site, counts in rep.by_site():
            print(f"{site:<28} {names.get(site, ''):<40} " + ", ".join(f"{k} {n}" for k, n in sorted(counts.items())))
    return 0


def _analysis_options(a):
    a.add_argument("--no-sp", action="store_true", help="skip the single-path explorer")
    a.add_argument("--sp-max-events", type=int, default=2000, help="skip the explorer on traces with more events")
    a.add_argument("--max-states", type=int, default=300_000, help="explorer state cap per forced alternative")
    a.add_argument("--full-profile", action="store_true",
                   help="judge every message-size breakpoint instead of the two ends and a bisection (equal by theorem T2; for differential testing)")
    a.add_argument("--generic", action="store_true",
                   help="judge alternatives with the generic checker instead of the indexed one (slow; for differential testing)")
    a.add_argument("--cross-check", action="store_true",
                   help="confirm every alternative the indexed checker accepts with the generic one, and report any "
                        f"disagreement (done anyway on traces of up to {report.CROSS_CHECK_LIMIT} records)")
    a.add_argument("--changes", type=int, default=1, choices=[1, 2], help="also report alternatives that re-match two receives on different ranks")
    a.add_argument("--rerun", metavar="EXE", default=None,
                   help="level L2 by re-execution: the traced executable, whose ranks are rerun alone under the shim "
                        "(rerun/libstretto-rerun.so; the trace must carry payloads, STRETTO_PAYLOAD)")
    a.add_argument("--rerun-args", default="", help="the traced run's command-line arguments, one string")
    a.add_argument("--rerun-mpi", default="openmpi", help="the MPI the executable is built with (which shim)")
    a.add_argument("--rerun-timeout", type=float, default=20, help="seconds per re-execution")
    a.add_argument("--rerun-cwd", default=None,
                   help="working directory of the re-executions (default: an empty one; the rank's file writes land here)")
    a.add_argument("--reads", metavar="FILE", default=None,
                   help="first-read map of the program (python3 -m stretto.firstread): keep the records a rank issues "
                        "before it can read a changed receive's result in the horizon (rule H1', docs/firstread.md)")
    a.add_argument("--reads-unchecked", action="store_true",
                   help="with --reads: accept a map without checking that it was made from the traced program's sources")
    a.add_argument("--objdir", default=None,
                   help="with --reads: where to find a traced object whose recorded path no longer exists (default: "
                        "the trace directory's parent)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="stretto", description="sound prediction of alternative MPI matchings")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("show", help="print events and the observed matching"); a.add_argument("tracedir"); a.set_defaults(fn=cmd_show)
    a = sub.add_parser("analyze", help="predict alternatives and classify them; exit status 0 none found (also when the report is degraded: see its status line), 1 an error class found, 2 the trace cannot be loaded, 3 internal error")
    a.add_argument("tracedir"); _analysis_options(a); a.add_argument("--json", action="store_true")
    a.add_argument("-v", "--verbose", action="store_true", help="also list rejected alternatives"); a.set_defaults(fn=cmd_analyze)
    a = sub.add_parser("sites", help="list the call sites of the trace, named in the source when possible")
    a.add_argument("tracedir"); _analysis_options(a)
    a.add_argument("--findings", action="store_true", help="also analyse and count the findings per site"); a.set_defaults(fn=cmd_sites)
    a = sub.add_parser("candidates", help="list every candidate alternative with its verdict"); a.add_argument("tracedir")
    a.add_argument("--mode", default="L1", choices=["L1", "SP"]); a.set_defaults(fn=cmd_candidates)
    a = sub.add_parser("confirm", help="replay each finding with its STRETTO_PIN and classify the outcome")
    a.add_argument("tracedir"); a.add_argument("exe"); a.add_argument("args", nargs="*")
    a.add_argument("--np", type=int, default=0); a.add_argument("--timeout", type=int, default=20)
    a.add_argument("--mpi", default="openmpi"); a.add_argument("--all", action="store_true", help="also replay informational findings")
    a.add_argument("--keep", nargs=2, metavar=("CAMPAIGN", "PREFIX"),
                   help="with KEEP_TRACES set, archive each replay's and control's trace as CAMPAIGN/PREFIX__... (stretto.keep)")
    _analysis_options(a); a.add_argument("--json", action="store_true"); a.set_defaults(fn=cmd_confirm)
    a = sub.add_parser("oracle", help="exhaustively explore the trace as a single-path program"); a.add_argument("tracedir")
    a.add_argument("--B", nargs="*", default=["0", "inf"]); a.set_defaults(fn=cmd_oracle)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except LoadError as e:
        print(f"stretto: {e}", file=sys.stderr)
        return 2
    except confirm_mod.HarnessError as e:   # confirm: the replay library is missing or did not load
        print(f"stretto: {e}", file=sys.stderr)
        return 2
    except Exception:   # noqa: BLE001 - an internal error must not read as "an error class was found" (1)
        import traceback
        traceback.print_exc()
        return 3
