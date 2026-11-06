#!/usr/bin/env python3
"""Tables of the MPI-CorrBench runs outside its OpenMP directory (experiments/corrbench/README.md)
from the CSVs of run.py (stretto-<mpi>.csv; the rows of a program in stretto-<mpi>-again.csv,
run again after a fix, replace its first ones) and must.sh (must.csv) in DIR:

  experiments/corrbench/summary.py DIR [--list]

A run of Stretto is flagged when its report has a finding of an error class
(experiments/mbi/score.py, ERROR), with L2 by re-execution an abort or a deadlock too (score.l2_verdict);
"failed run only" when the run itself did not finish (a hang, an MPI error, a signal: what any
run shows) and every error finding is observed, or when the one error class is observed:abort
(the run ended without MPI_Finalize). A run of MUST is flagged when its verdict is ERROR. A program
is flagged by a tool when every run of it with a verdict is (column "every"), or some is
("some"); runs without a verdict (Stretto: CRASH, NO-TRACE, BUILD-FAILED; MUST: CE, RE, TIMEOUT,
HARNESS-ERROR) are counted apart. Stretto's configurations: trace (L1 and L1+SP from the traced
runs), +L2 (the same runs with L2), guard (the runs with the buffer guard), all (any of them).
--list prints every flagged correct program and every in-scope program not flagged, with the
classes of each run."""
from __future__ import annotations
import collections
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mbi"))
from score import ERROR  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NO_VERDICT = ("CRASH", "NO-TRACE")


def stretto_flag(r: dict, with_l2: bool):
    """True, False, "abort" (failed run only) or None (no verdict) for one run of Stretto."""
    if r["mode"] == "build":
        return None
    if r["analysis"] in NO_VERDICT:
        return None
    errs = {lv for lv in r["levels"].split() if lv.split(":", 1)[1] in ERROR}
    if with_l2 and r.get("l2") and set(r["l2"].split()) & {"abort", "deadlock"}:
        errs.add("L2")
    if not errs:
        return False
    if errs == {"observed:abort"} or r["result"] != "FINISHED" and all(e.startswith("observed:") for e in errs):
        return "abort"
    return True


def must_flag(r: dict):
    v = r["must"]
    if v.startswith("ERROR"):
        return True
    if v == "OK":
        return False
    return None


def program_verdict(flags: list):
    """(every, some, abort_only, runs with a verdict, runs without one)"""
    have = [f for f in flags if f is not None]
    pos = [f for f in have if f]
    every = bool(have) and len(pos) == len(have)
    some = bool(pos)
    abort_only = some and all(f == "abort" for f in pos)
    return every, some, abort_only, len(have), len(flags) - len(have)


def load(d: str):
    meta = {m["program"]: m for m in csv.DictReader(open(os.path.join(HERE, "programs.csv")))}
    stretto = {}
    for mpi in ("openmpi", "mpich"):
        p = os.path.join(d, f"stretto-{mpi}.csv")
        if os.path.exists(p):
            by = collections.defaultdict(list)
            for r in csv.DictReader(open(p)):
                by[r["program"]].append(r)
            # programs run again by a later commit (stretto-<mpi>-again.csv): their rows replace the first ones
            again = os.path.join(d, f"stretto-{mpi}-again.csv")
            if os.path.exists(again):
                redone = collections.defaultdict(list)
                for r in csv.DictReader(open(again)):
                    redone[r["program"]].append(r)
                by.update(redone)
            stretto[mpi] = by
    must = collections.defaultdict(list)
    p = os.path.join(d, "must.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            must[r["program"]].append(r)
    return meta, stretto, must


CONFIGS = {
    "trace": lambda rs: [stretto_flag(r, False) for r in rs if r["mode"] in ("trace", "build")],
    "+L2": lambda rs: [stretto_flag(r, True) for r in rs if r["mode"] in ("trace", "build")],
    "guard": lambda rs: [stretto_flag(r, False) for r in rs if r["mode"] in ("guard", "build")],
}


def all_config(rs):
    """A run index flagged in any configuration: the trace run k (with L2) or the guard run k."""
    t = [stretto_flag(r, True) for r in rs if r["mode"] == "trace"]
    g = [stretto_flag(r, False) for r in rs if r["mode"] == "guard"]
    if not t and not g:
        return [None]
    out = []
    for a, b in zip(t, g):
        vals = [x for x in (a, b) if x is not None]
        out.append(None if not vals else True if True in vals else "abort" if "abort" in vals else False)
    return out


def group_of(m: dict) -> str:
    if m["label"] == "correct":
        return "correct"
    return f"erroneous, {'in scope' if m['scope'] == 'in' else 'out of scope'}"


def table(meta, stretto, must):
    groups = collections.OrderedDict()
    order = ["erroneous, in scope", "erroneous, out of scope", "correct"]
    for g in order:
        groups[g] = [p for p, m in sorted(meta.items()) if group_of(m) == g]
    lines = []
    for mpi, by in stretto.items():
        lines.append(f"\nStretto, {mpi} (programs flagged in every run / in some run; (of these, flagged only in runs "
                     "that failed by themselves); [programs with a run without a verdict])\n")
        lines.append("| programs | n | native: finished / hang / error | trace | +L2 | guard | all |")
        lines.append("|---|---|---|---|---|---|---|")
        for g, progs in groups.items():
            for sub, ps in [(g, progs)] + ([(f"  {g}, {a}", [p for p in progs if meta[p]["area"] == a])
                                            for a in ("pt2pt", "coll", "usertypes", "rma")] if g != "erroneous, in scope" else []):
                ps = [p for p in ps if p in by]
                if not ps:
                    continue
                nat = collections.Counter()
                for p in ps:
                    rs = [r for r in by[p] if r["mode"] in ("native", "build")]
                    res = rs[0]["result"] if rs else ""
                    nat["finished" if res == "FINISHED" else "hang" if res == "TIMEOUT" else "error"] += 1
                cells = []
                for name in ("trace", "+L2", "guard", "all"):
                    vs = [program_verdict(CONFIGS[name](by[p]) if name != "all" else all_config(by[p])) for p in ps]
                    every = sum(v[0] for v in vs); some = sum(v[1] for v in vs)
                    ab = sum(v[2] for v in vs); nov = sum(1 for v in vs if v[4])
                    cells.append(f"{every} / {some}" + (f" ({ab} failed run)" if ab else "") + (f" [{nov}]" if nov else ""))
                lines.append(f"| {sub} | {len(ps)} | {nat['finished']} / {nat['hang']} / {nat['error']} | " + " | ".join(cells) + " |")
    if must:
        lines.append("\nMUST 1.11.2 (MPICH; programs flagged in every run / in some run; programs with a run without a verdict)\n")
        lines.append("| programs | n | flagged | without a verdict: TIMEOUT / CE / RE / HARNESS |")
        lines.append("|---|---|---|---|")
        for g, progs in groups.items():
            for sub, ps in [(g, progs)] + ([(f"  {g}, {a}", [p for p in progs if meta[p]["area"] == a])
                                            for a in ("pt2pt", "coll", "usertypes", "rma")] if g != "erroneous, in scope" else []):
                ps = [p for p in ps if p in must]
                if not ps:
                    continue
                vs = [program_verdict([must_flag(r) for r in must[p]]) for p in ps]
                why = collections.Counter()
                for p in ps:
                    for r in must[p]:
                        if must_flag(r) is None:
                            why[r["must"].split(":", 1)[0]] += 1
                lines.append(f"| {sub} | {len(ps)} | {sum(v[0] for v in vs)} / {sum(v[1] for v in vs)} | "
                             f"{why['TIMEOUT']} / {why['CE']} / {why['RE']} / {why['HARNESS-ERROR']} runs |")
    return "\n".join(lines)


def classes_of(rs, mode):
    return "; ".join(f"{r['result'][:12]}:{r['levels'] or '-'}" + (f" L2:{r['l2']}" if r.get("l2") else "") for r in rs if r["mode"] == mode)


def listing(meta, stretto, must):
    lines = []
    for mpi, by in stretto.items():
        lines.append(f"\n### {mpi}: correct programs flagged in some run\n")
        for p, m in sorted(meta.items()):
            if m["label"] == "correct" and p in by and program_verdict(all_config(by[p]))[1]:
                lines.append(f"- {p}: trace [{classes_of(by[p], 'trace')}] guard [{classes_of(by[p], 'guard')}]"
                             f"; MUST [{'; '.join(r['must'][:40] for r in must.get(p, []))}]")
        lines.append(f"\n### {mpi}: in-scope programs not flagged in every run\n")
        for p, m in sorted(meta.items()):
            if m["scope"] == "in" and p in by and not program_verdict(all_config(by[p]))[0]:
                lines.append(f"- {p} ({m['why']}): trace [{classes_of(by[p], 'trace')}] guard [{classes_of(by[p], 'guard')}]"
                             f"; MUST [{'; '.join(r['must'][:40] for r in must.get(p, []))}]")
        lines.append(f"\n### {mpi}: in-scope programs\n")
        for p, m in sorted(meta.items()):
            if m["scope"] == "in" and p in by:
                lines.append(f"- {p}: trace [{classes_of(by[p], 'trace')}] guard [{classes_of(by[p], 'guard')}]"
                             f"; MUST [{'; '.join(r['must'][:40] for r in must.get(p, []))}]")
    return "\n".join(lines)


def main():
    d = sys.argv[1]
    meta, stretto, must = load(d)
    print(table(meta, stretto, must))
    if "--list" in sys.argv:
        print(listing(meta, stretto, must))


if __name__ == "__main__":
    main()
