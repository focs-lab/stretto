#!/usr/bin/env python3
"""E7 without hand-written twins: level L2 by re-execution (stretto.rerun, docs/l2-auto.md).

Every selected MBI program (default: the message-race templates) and, with --idioms, every
idiom is built with the system wrapper and run once under the tracer with payloads
(STRETTO_PAYLOAD). Each L1 alternative is then judged at B=0 and B=inf by exploring
  ref    the hand-written twin where there is one (experiments/mbi/twins.py for MBI,
         analysis/tests/idioms_dsl.py for the idioms), else the single-path program of the
         trace, which E7 takes as exact on the straight-line templates;
  all    the program re-executed rank by rank, every rank re-executable;
  one    the same with only the changed rank re-executed (the minimal variant).
A verdict is the set of claims the executions with the changed pair support: abort and
deadlock in some or in every execution, a departure from the trace's calls in some or every
execution, every execution finishing, and the dismissal (every execution finishes, receives
every message and keeps the trace's calls). An execution that reaches an undetermined continuation supports no "every
execution aborts / deadlocks" and no dismissal (a departure it has made stands). A re-executed
verdict is "same" as the reference, "weaker" (a subset of its claims) or "other". The
report-level classes per program (as E7's table) are given too.

Writes experiments/results/e7_rerun.csv (one row per alternative and threshold) and
experiments/results/e7_rerun.md. Needs tracer/ and rerun/ built for Open MPI.
usage: e7_rerun.py [--select REGEX] [--idioms] [--only-idioms] [--repeat N] [--timeout S] [--jobs J]"""
from __future__ import annotations
import argparse
import csv
import glob
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "analysis", "tests"))
sys.path.insert(0, os.path.join(ROOT, "experiments", "mbi"))
from stretto import dsl, explore, report, rerun, trace  # noqa: E402
from stretto.confirm import classify_rc  # noqa: E402
import idioms_dsl  # noqa: E402
import twins  # noqa: E402
from run_mbi import parse_header  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from provenance import code_commit  # noqa: E402

MPI = "openmpi"
MPICC = f"/usr/bin/mpicc.{MPI}"
MPIRUN = "/usr/bin/mpirun.openmpi"
TRACER = os.path.join(ROOT, "tracer", "bin", MPI, "libstretto-trace.so")
GENCODES = os.path.join(ROOT, "study", "apps", "MBI", "gencodes")
OUT = os.path.join(ROOT, "experiments", "results", "e7rerun")
PAYLOAD = 1 << 16
IDIOM_NP = {3: 2, 7: 2, 10: 4, 12: 4, 13: 4}


def claims(t, outs) -> frozenset:
    if not outs:
        return frozenset({"never"})
    c = set()
    determined = not any(o.kind == "unknown" for o in outs)
    for kind in ("abort", "deadlock"):
        hit = [o.kind == kind for o in outs]
        if any(hit):
            c.add(f"{kind}-some")
        if all(hit):
            c.add(f"{kind}-every")
    # a departure is a fact about the execution so far, so an undetermined execution that has
    # departed counts; an abort or a deadlock is not, until the execution is over
    div = [bool(report.skeleton_divergence(t, o)) for o in outs]
    if any(div):
        c.add("diverge-some")
    if all(div):
        c.add("diverge-every")
    if all(o.kind == "done" for o in outs):
        c.add("done-every")
    if determined and not any(div) and all(o.kind == "done" and not o.orphans for o in outs):
        c.add("dismissed")
    if not determined:
        c.add("undetermined")
    return frozenset(c)


def compare(ref: frozenset, got: frozenset) -> str:
    g = got - {"undetermined"}
    if g == ref:
        return "same"
    return "weaker" if g < ref else "other"


def judge(prog, t, rk, sk, B, max_states):
    try:
        res = explore.explore(prog, B, force={rk: sk}, max_states=max_states, comms=t.comm_members)
    except explore.StateCapReached:
        return frozenset({"state-cap"})
    return claims(t, res.outcomes_with(rk, sk))


def fmt(c: frozenset) -> str:
    return " ".join(sorted(c)) or "-"


def traced_run(exe: str, np: int, out: str, timeout: int) -> str:
    td = os.path.join(out, "trace")
    subprocess.run(["rm", "-rf", td])
    os.makedirs(td)
    cmd = ["/usr/bin/timeout", "-k", "5", str(timeout), MPIRUN, "-np", str(np),
           "-x", f"STRETTO_TRACE_DIR={td}", "-x", f"STRETTO_PAYLOAD={PAYLOAD}", "-x", f"LD_PRELOAD={TRACER}", exe]
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={**os.environ, "OMPI_MCA_rmaps_base_oversubscribe": "1"})
    open(os.path.join(out, "run.stderr"), "w").write(r.stderr)
    return classify_rc(r.returncode, r.stderr, time.monotonic() - t0, timeout)


def one_program(item, args):
    corpus, name, src, np, expect, hand, ref_kind = item
    out = os.path.join(OUT, name)
    os.makedirs(out, exist_ok=True)
    exe = os.path.join(out, "main")
    if corpus == "mbi":
        b = subprocess.run([MPICC, "-O0", "-g", "-o", exe, src, "-lm"], capture_output=True, text=True)
        if b.returncode != 0:
            return {"name": name, "error": "build: " + b.stderr[-200:]}, []
    else:
        exe = src
    run = traced_run(exe, np, out, args.timeout)
    t = trace.load(os.path.join(out, "trace"))
    if hand is None:
        hand_prog = dsl.program_from_trace(t)
    else:
        tag = next(iter(t.sends.values())).tag if t.sends else 0
        hand_prog = hand(np, tag)
    runner = rerun.ShimRunner(exe, [], np=np, mpi=MPI, timeout=args.rerun_timeout)
    t0 = time.monotonic()
    shared = {}
    auto_all = rerun.Oracle(t, runner, runs=shared, max_runs=args.max_runs).program()
    by_rank = {}
    rep_l1 = report.analyze(t, with_sp=False)
    alts = sorted({(f.recv, f.send) for f in rep_l1.findings if f.level == "L1" and f.family == "1" and f.recv is not None})
    rows = []
    for rk, sk in alts:
        if rk[0] not in by_rank:
            by_rank[rk[0]] = rerun.Oracle(t, runner, live={rk[0]}, runs=shared, max_runs=args.max_runs).program()
        for B in (0, None):
            ref = judge(hand_prog, t, rk, sk, B, args.max_states)
            a = judge(auto_all, t, rk, sk, B, args.max_states)
            o = judge(by_rank[rk[0]], t, rk, sk, B, args.max_states)
            rows.append({"corpus": corpus, "program": name, "np": np, "run": run, "reference": ref_kind,
                         "alternative": f"{t.recvs[rk]} <- {t.sends[sk]}", "B": "inf" if B is None else B,
                         "ref": fmt(ref), "all": fmt(a), "one": fmt(o), "cmp_all": compare(ref, a), "cmp_one": compare(ref, o),
                         "commit": code_commit()})
    elapsed = time.monotonic() - t0
    # the report itself, as E7 prints it: L2 classes with the hand-written twin and by re-execution
    rep_hand = report.analyze(t, twin=hand_prog if hand is not None else None, max_states=args.max_states)
    rep_auto = report.analyze(t, twin=auto_all, twin_label="re-execution", max_states=args.max_states)
    by = lambda rep, lvl: " ".join(sorted({f.cls for f in rep.findings if f.level == lvl})) or "-"
    unreliable = "; ".join(f"rank {r}: {w}" for r, w in sorted(auto_all.oracle.unreliable.items()))
    summary = {"corpus": corpus, "name": name, "expect": expect, "run": run, "alternatives": len(alts),
               "L1": by(rep_auto, "L1"), "L1+SP": by(rep_auto, "L1+SP"),
               "L2 twin": by(rep_hand, "L2") if hand is not None else "(no twin)", "L2 re-execution": by(rep_auto, "L2"),
               "runs": len(shared), "seconds": f"{elapsed:.1f}", "unreliable": unreliable,
               "findings": "; ".join(sorted({f.title.split(' after ')[0] for f in rep_auto.findings if f.level == 'L2'}))}
    with open(os.path.join(out, "report.txt"), "w") as fh:
        fh.write(report.format_report(t, rep_auto, show_infeasible=True))
    return summary, rows


def items(args):
    out = []
    if not args.only_idioms:
        for path in sorted(glob.glob(os.path.join(GENCODES, "*.c"))):
            base = os.path.basename(path)[:-2]
            if not re.search(args.select, base):
                continue
            h = parse_header(path)
            hand = (lambda np, tag, base=base: twins.twin_for(base, np, tag)) if twins.twin_for(base, h["np"]) is not None else None
            for k in range(args.repeat):
                name = base if args.repeat == 1 else f"{base}.{k}"
                out.append(("mbi", name, path, h["np"], f"{h['expect']} {h['detail']}".strip(), hand,
                            "twin" if hand else "single-path"))
    if args.idioms or args.only_idioms:
        subprocess.run(["make", "-s", "-C", os.path.join(ROOT, "idioms"), f"MPI={MPI}"], check=True)
        for d in sorted(glob.glob(os.path.join(ROOT, "idioms", "[0-9][0-9]_*"))):
            n = int(os.path.basename(d)[:2])
            mk = idioms_dsl.ALL.get(n)
            hand = (lambda np, tag, mk=mk: mk()) if mk else None
            for k in range(args.repeat):
                name = os.path.basename(d) if args.repeat == 1 else f"{os.path.basename(d)}.{k}"
                out.append(("idiom", name, os.path.join(d, "bin", MPI, "main"), IDIOM_NP.get(n, 3), "", hand,
                            "twin" if hand else "single-path"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", default=r"^MessageRace_")
    ap.add_argument("--idioms", action="store_true", help="also the idioms")
    ap.add_argument("--only-idioms", action="store_true")
    ap.add_argument("--repeat", type=int, default=1, help="traced runs per program")
    ap.add_argument("--timeout", type=int, default=30, help="seconds per traced run")
    ap.add_argument("--rerun-timeout", type=float, default=20, help="seconds per re-execution")
    ap.add_argument("--max-runs", type=int, default=2000, help="re-executions per program")
    ap.add_argument("--max-states", type=int, default=30000, help="explorer states per alternative")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--out", default=os.path.join(ROOT, "experiments", "results", "e7_rerun"))
    args = ap.parse_args()
    todo = items(args)
    summaries, rows = [], []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for i, (s, r) in enumerate(ex.map(lambda it: one_program(it, args), todo), 1):
            summaries.append(s)
            rows.extend(r)
            print(f"[{i}/{len(todo)}] {s['name']:<48} {s.get('run', ''):<12} L2 twin: {s.get('L2 twin', '')!s:<10} "
                  f"L2 re-execution: {s.get('L2 re-execution', s.get('error', ''))}", flush=True)
    keys = ["corpus", "program", "np", "run", "reference", "alternative", "B", "ref", "all", "one", "cmp_all", "cmp_one", "commit"]
    with open(args.out + ".csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    with open(args.out + ".md", "w") as fh:
        fh.write(markdown(summaries, rows))
    print(f"wrote {args.out}.csv ({len(rows)} rows) and {args.out}.md")
    return 0


def markdown(summaries, rows) -> str:
    lines = ["Rows by code commit: " + ", ".join(f"{c} ({n})" for c, n in sorted(
        {(r["commit"], sum(1 for x in rows if x["commit"] == r["commit"])) for r in rows})) + ".", ""]
    lines += ["| corpus | reference | judged (alternative, B) | all ranks: same | weaker | other | changed rank only: same | weaker | other |",
              "|---|---|---|---|---|---|---|---|---|"]
    groups = {}
    for r in rows:
        key = (r["corpus"] + (" (E7 twins)" if r["corpus"] == "mbi" and r["reference"] == "twin" else ""), r["reference"])
        groups.setdefault(key, []).append(r)
    for (corpus, ref), rs in sorted(groups.items()):
        n = lambda col, v: sum(1 for r in rs if r[col] == v)
        lines.append(f"| {corpus} | {ref} | {len(rs)} | {n('cmp_all', 'same')} | {n('cmp_all', 'weaker')} | {n('cmp_all', 'other')} | "
                     f"{n('cmp_one', 'same')} | {n('cmp_one', 'weaker')} | {n('cmp_one', 'other')} |")
    lines += ["", "| program | label | observed run | L1 alternatives | L1 | L1+SP | L2 (twin) | L2 (re-execution) | runs | seconds | L2 findings by re-execution |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in sorted(summaries, key=lambda s: (s.get("corpus", ""), s["name"])):
        if "error" in s:
            lines.append(f"| {s['name']} | | {s['error']} | | | | | | | | |")
            continue
        lines.append(f"| {s['name']} | {s['expect']} | {s['run']} | {s['alternatives']} | {s['L1']} | {s['L1+SP']} | {s['L2 twin']} | "
                     f"{s['L2 re-execution']} | {s['runs']} | {s['seconds']} | {s['findings']} |")
    unrel = [s for s in summaries if s.get("unreliable")]
    if unrel:
        lines += ["", "Ranks whose re-execution was not trusted:", ""]
        lines += [f"- {s['name']}: {s['unreliable']}" for s in unrel]
    others = [r for r in rows if "other" in (r["cmp_all"], r["cmp_one"])]
    if others:
        lines += ["", "Verdicts that differ from the reference:", "", "| program | alternative | B | reference | all ranks | changed rank only |", "|---|---|---|---|---|---|"]
        lines += [f"| {r['program']} | {r['alternative']} | {r['B']} | {r['ref']} | {r['all']} | {r['one']} |" for r in others]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
