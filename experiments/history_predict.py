#!/usr/bin/env python3
"""Does one clean traced run of a program predict a bug documented in the program's history, and
does the predicted execution, forced, show the bug?

The drivers of experiments/cabana/, stempy/, mfem332/, superlu3d/ and qeelph/ run it on the
version with the bug and on its fix. For each process count, RUNS traced runs are made; a run is
clean when it exits with status 0 and its output is correct (it matches --ok and the --judge
says "ok"). Each clean run is analysed (L1 and, unless --no-sp, the single-path explorer with
--max-states states per alternative), then
  - with --confirm, its findings of the error classes (truncation, orphan, deadlock, buffering)
    are replayed by stretto confirm (stretto.confirm.confirm: the pins of the alternative's
    horizon, or of the explorer's witness, and a control that pins the recorded matching),
    deadlocks first, at most --max-confirm of them per run (0: all);
  - with --classify FILE, the function alternatives(t, rep) of that file names a kind for each of
    the run's receive and probe alternatives (for example "cross" when the receive would take a
    message of another call of the routine), and every alternative of a kind in --replay is
    replayed with the pins of its whole horizon (stretto.confirm.horizon_pins) and with its
    control, as experiments/hypre3/predict.py does, at most --max-replay per run (0: all); with
    --sample N, a sample of N of them per process count and kind instead, drawn over all traced
    runs by random.Random("<seed>:<np>:<kind>") (--seed), for a case whose traces have more
    alternatives than its replays can afford (each a hang until --timeout). A sampled campaign
    keeps each clean trace on disk until the sample is drawn and analyses a sampled run's trace
    again (the analysis is deterministic) before its replays.
Replays and controls run under the tracer, which judges each one a run of the program without
its pins or not (run_valid, control_valid), and --ok and --judge judge what each printed. Every
run, traced or replayed, runs in one directory, the same for all (the pins number the wildcard
operations of the traced execution, which must have the same arguments and directory), unless
--base DIR gives each run a fresh copy of DIR as its directory, for a program that writes into
it (ph.x writes its outdir; its messages do not depend on the directory's path). The traced
runs and what follows each are independent: --jobs of them run at once, each in a process of its
own, since the explorer is single-threaded Python.

--judge FILE [ARG ...] names a Python file whose function judge(stdout, *ARG) returns "ok" for
a correct output and a short description of a wrong one otherwise (for example the x error of
a solver or which spin a table's columns belong to); its verdicts are the *_says columns.

The output of every traced run, replay or control that did not finish with a correct output is
kept under outputs/ next to the table (the result directories are not committed). With
KEEP_TRACES set, every trace is kept (stretto.keep) under the table's campaign
(<case>/predict-<version>) or --keep-campaign: np<N>-run<K> for a traced run, the same with
__<class>-<level>-<pin>__replay and __control for a confirmed finding (stretto.confirm's names),
and with -<kind>-<pin>-replay and -control for a replayed alternative.

usage: history_predict.py --version V --np N [N ...] --runs R --out CSV [--mpi openmpi]
                          [--timeout S] [--trace-timeout S] [--jobs J] [--no-sp] [--max-states N]
                          [--confirm] [--max-confirm N] [--classify FILE] [--replay KIND ...]
                          [--max-replay N] [--sample N] [--seed S] [--ok REGEX]
                          [--judge FILE [ARG ...]] [--base DIR] [--keep-campaign C]
                          -- <exe> [args ...]
Writes one row per confirmed finding or replayed alternative, and one per traced run without
any: version, np, run, traced (stretto.confirm.classify_rc: FINISHED, TIMEOUT, MPI-ERROR:...,
APP-ERROR:N, ...), traced_ok, traced_says, traced_s, records, status (complete, or degraded and
why), analyse_s, findings (the run's findings per class and level), kinds (its alternatives per
kind), kinds_B (kind:B:count per kind and realisable threshold, Finding.B), role (confirm or
replay), cls, level, kind, pin, B, title, replay (its result), matched (every required pin
matched), replay_ok, replay_says, run_valid, violation, verdict and strict (stretto confirm's),
control, control_matched, control_ok, control_says, control_valid, control_violation,
misapplied, seed (of a sampled replay), commit.
"""
from __future__ import annotations
import argparse
import collections
import concurrent.futures
import contextlib
import copy
import csv
import multiprocessing
import os
import random
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stretto import keep, trace  # noqa: E402
from stretto.confirm import (all_matched, classify_rc, confirm, horizon_pins, need_preload, pin_name,  # noqa: E402
                             required_matches, run_checked, tracer_lib)
from stretto.report import analyze  # noqa: E402
from provenance import code_commit  # noqa: E402

CONFIRMED = ("1d", "1c", "1a", "1b", "2-buffering")    # the classes stretto confirm replays, deadlocks first
KEYS = ["version", "np", "run", "traced", "traced_ok", "traced_says", "traced_s", "records", "status", "analyse_s", "findings",
        "kinds", "kinds_B", "role", "cls", "level", "kind", "pin", "B", "title", "replay", "matched", "replay_ok", "replay_says",
        "run_valid", "violation", "verdict", "strict", "control", "control_matched", "control_ok", "control_says", "control_valid",
        "control_violation", "misapplied", "seed", "commit"]
A = None            # the parsed arguments, for the worker processes
JUDGE = None        # --judge's function


def ok_in(text: str) -> bool:
    return bool(re.search(A.ok, text)) if A.ok else True


def good(proc) -> tuple:
    """(whether the output is correct, the judge's verdict on it: "ok" without a judge)."""
    says = JUDGE(proc.stdout, *A.judge[1:]) if JUDGE else "ok"
    return ok_in(proc.stdout + proc.stderr) and says == "ok", says


@contextlib.contextmanager
def rundir():
    """The directory of one run: a fresh copy of --base, or None (the campaign's one directory)."""
    if not A.base:
        yield None
        return
    w = tempfile.mkdtemp(prefix="history-base-")
    try:
        shutil.copytree(A.base, w, dirs_exist_ok=True, symlinks=True)
        yield w
    finally:
        shutil.rmtree(w, ignore_errors=True)


def keep_output(name: str, cmd: str, proc) -> None:
    """The output of a run that failed or printed something unexpected, under outputs/."""
    d = os.path.join(os.path.dirname(A.out), "outputs")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, keep.safe(name) + ".txt"), "w") as fh:
        fh.write(f"{cmd}\nstatus {proc.returncode if proc is not None else '-'}\n--- stdout\n"
                 f"{(proc.stdout if proc is not None else '')[-20000:]}\n--- stderr\n"
                 f"{(proc.stderr if proc is not None else '')[-20000:]}")


def kept_trace(np: int, i: int) -> str:
    """Where a sampled campaign keeps the trace of traced run i at np processes."""
    return os.path.join(A.work, "traces", f"np{np}-run{i:02d}")


def kinds_of(t, rep) -> list:
    return list(runpy.run_path(A.classify)["alternatives"](t, rep)) if A.classify else []


def replay(t, f, kind: str, row: dict, np: int, i: int) -> dict:
    """The replay of one alternative with the pins of its whole horizon, and its control."""
    exe, args = A.cmd[0], A.cmd[1:]
    tag = f"{A.version}-np{np}-run{i:02d}"
    res = {**row, "role": "replay", "cls": f.cls, "level": f.level, "kind": kind, "pin": f.pin, "B": f.B,
           "title": f.title[:300], "seed": A.seed if A.sample else ""}
    hp = horizon_pins(t, f)
    if hp is None:
        return {**res, "replay": "no horizon"}
    alt, ctl = hp
    name = f"np{np}-run{i:02d}-{kind}-{pin_name(f.pin)}"
    with rundir() as w:
        ra = run_checked(np, exe, args, alt, A.timeout, mpi=A.mpi, cwd=w, keep_as=(A.campaign, name + "-replay"))
    with rundir() as w:
        rc = run_checked(np, exe, args, ctl, A.timeout, mpi=A.mpi, cwd=w, keep_as=(A.campaign, name + "-control"))
    rok, rsays = good(ra.proc)
    cok, csays = good(rc.proc)
    res.update(replay=ra.result, matched=all_matched(alt, ra.proc.stderr, required_matches(t, f), ra.seen),
               replay_ok=rok, replay_says=rsays, run_valid=ra.valid, violation=ra.why[:300],
               control=rc.result, control_matched=all_matched(ctl, rc.proc.stderr, None, rc.seen),
               control_ok=cok, control_says=csays, control_valid=rc.valid,
               control_violation=rc.why[:300], misapplied="; ".join(ra.misapplied + rc.misapplied)[:300])
    for role, p, ok in (("replay", ra, rok), ("control", rc, cok)):
        if p.result != "FINISHED" or not ok:
            keep_output(f"{tag}-{kind}-{pin_name(f.pin)}-{role}", f"{exe} {' '.join(args)}", p.proc)
    print(f"  replay run {i} {kind} {f.cls} -> {ra.result} matched={res['matched']} {rsays} valid={ra.valid}; "
          f"control {rc.result} {csays} valid={rc.valid}", flush=True)
    return res


def one(np: int, i: int) -> tuple:
    """The traced run i at np processes, its analysis, confirmations and (unless sampled) replays:
    (its rows, its own row, the alternatives it offers the sample as (np, run, index in the
    classification, pin, kind))."""
    exe, args, campaign = A.cmd[0], A.cmd[1:], A.campaign
    tag = f"{A.version}-np{np}-run{i:02d}"
    d = tempfile.mkdtemp(prefix=f"history-trace-{np}-{i}-")
    cmd = ["/usr/bin/timeout", "-k", "5", str(A.trace_timeout), "/usr/bin/mpirun.openmpi", "--oversubscribe", "-np", str(np),
           "-x", f"LD_PRELOAD={tracer_lib(A.mpi)}", "-x", f"STRETTO_TRACE_DIR={d}", exe, *args]
    t0 = time.monotonic()
    with rundir() as w:
        r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=w)
    elapsed = time.monotonic() - t0
    ok, says = good(r)
    row = {"version": A.version, "np": np, "run": i, "traced": classify_rc(r.returncode, r.stderr, elapsed, A.trace_timeout),
           "traced_ok": ok, "traced_says": says, "traced_s": round(elapsed, 1), "commit": code_commit()}
    keep.keep(d, campaign, f"np{np}-run{i:02d}", commit=row["commit"])
    if row["traced"] != "FINISHED" or not row["traced_ok"]:
        keep_output(f"{tag}-traced", " ".join(cmd), r)
        print(f"{tag}: {row['traced']} {says}", flush=True)
        shutil.rmtree(d, ignore_errors=True)
        return [row], row, []
    try:
        t = trace.load(d)
    except (OSError, ValueError, KeyError) as e:
        shutil.rmtree(d, ignore_errors=True)
        bad = {**row, "status": f"trace unreadable: {type(e).__name__}: {e}"[:200]}
        return [bad], bad, []
    if A.sample:
        os.makedirs(os.path.dirname(kept_trace(np, i)), exist_ok=True)
        shutil.move(d, kept_trace(np, i))
    else:
        shutil.rmtree(d, ignore_errors=True)
    t0 = time.monotonic()
    rep = analyze(t, with_sp=not A.no_sp, max_states=A.max_states)
    row["analyse_s"] = round(time.monotonic() - t0, 1)
    row["records"] = sum(len(v) for v in t.ranks.values())
    row["status"] = "complete" if not rep.degraded else "degraded: " + ",".join(x for x, _ in rep.degraded)
    counts = collections.Counter(f"{f.cls}/{f.level}" for f in rep.findings if f.level != "observed")
    counts.update(f"{f.cls}/observed" for f in rep.findings if f.level == "observed")
    row["findings"] = ";".join(f"{k}={n}" for k, n in sorted(counts.items()))
    kinds = kinds_of(t, rep)
    if A.classify:
        row["kinds"] = ";".join(f"{k}={n}" for k, n in sorted(collections.Counter(k for _, k in kinds).items()))
        row["kinds_B"] = ";".join(f"{k}:{b}:{n}" for (k, b), n in sorted(collections.Counter((k, f.B) for f, k in kinds).items()))
    print(f"{tag}: {row['traced']} ok={row['traced_ok']} {row['records']} records, analysed in {row['analyse_s']} s, "
          f"{row['status']}; {row['findings']} {row.get('kinds', '')}", flush=True)
    rows = []
    if A.confirm:
        errs = sorted((f for f in rep.findings if f.cls in CONFIRMED and f.level != "observed"), key=lambda f: CONFIRMED.index(f.cls))
        chosen = errs[:A.max_confirm] if A.max_confirm else errs
        if chosen:
            # the other findings stay: an L1+SP finding takes the pin of its L1 parent
            sub = copy.copy(rep)
            sub.findings = [f for f in rep.findings if f.cls not in CONFIRMED] + chosen
            for c in confirm(sub, np, exe, args, timeout=A.timeout, mpi=A.mpi, only_errors=True, t=t,
                             keep_as=(campaign, f"np{np}-run{i:02d}")):
                rows.append({**row, "role": "confirm", "cls": c.finding.cls, "level": c.finding.level, "pin": c.pin,
                             "B": c.finding.B, "title": c.finding.title[:300], "replay": c.result, "matched": c.pin_matched,
                             "run_valid": c.run_valid, "violation": c.violation[:300], "verdict": c.verdict, "strict": c.strict,
                             "control": c.control, "control_valid": c.control_valid,
                             "control_violation": c.control_violation[:300], "misapplied": c.misapplied})
                print(f"  confirm {c.finding.cls} {c.finding.level} -> {c.result} matched={c.pin_matched} control {c.control}: "
                      f"{c.verdict}, strict {c.strict}", flush=True)
    todo = [(j, f, k) for j, (f, k) in enumerate(kinds) if k in A.replay]
    if A.sample:
        return rows or [row], row, [(np, i, j, f.pin, k) for j, f, k in todo]
    for _, f, kind in (todo[:A.max_replay] if A.max_replay else todo):
        rows.append(replay(t, f, kind, row, np, i))
    return rows or [row], row, []


def sampled(np: int, i: int, row: dict, picks: list) -> list:
    """The replays of the alternatives the sample drew from traced run i at np processes: its
    kept trace analysed again, each pick found by its place in the classification and its pin."""
    t = trace.load(kept_trace(np, i))
    kinds = kinds_of(t, analyze(t, with_sp=not A.no_sp, max_states=A.max_states))
    out = []
    for j, pin, kind in picks:
        if j < len(kinds) and kinds[j][0].pin == pin and kinds[j][1] == kind:
            out.append(replay(t, kinds[j][0], kind, row, np, i))
        else:
            out.append({**row, "role": "replay", "kind": kind, "pin": pin, "seed": A.seed, "replay": "not found again"})
    shutil.rmtree(kept_trace(np, i), ignore_errors=True)
    return out


def write(path: str, rows: list) -> None:
    rows = sorted(rows, key=lambda r: (int(r["np"]), int(r["run"])))
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=KEYS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    global A, JUDGE
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--np", type=int, nargs="+", required=True)
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--mpi", default="openmpi", choices=["openmpi", "mpich"])
    ap.add_argument("--timeout", type=int, default=20, help="seconds per replay and control")
    ap.add_argument("--trace-timeout", type=int, default=600, help="seconds per traced run")
    ap.add_argument("--jobs", type=int, default=1, help="traced runs (with their analyses and replays) at once")
    ap.add_argument("--no-sp", action="store_true", help="L1 only: no single-path explorer")
    ap.add_argument("--max-states", type=int, default=300_000, help="the explorer's state cap per alternative")
    ap.add_argument("--confirm", action="store_true", help="replay the findings of the error classes with stretto confirm")
    ap.add_argument("--max-confirm", type=int, default=0, help="at most this many per traced run (0: all)")
    ap.add_argument("--classify", default=None, help="a Python file defining alternatives(t, rep) -> [(finding, kind)]")
    ap.add_argument("--replay", nargs="*", default=[], help="the kinds of alternative to replay with their horizon pins")
    ap.add_argument("--max-replay", type=int, default=0, help="at most this many per traced run (0: all)")
    ap.add_argument("--sample", type=int, default=0,
                    help="replay a seeded sample of this many alternatives per process count and kind, over all runs")
    ap.add_argument("--seed", default="20261028", help="the sample's seed")
    ap.add_argument("--ok", default=None, help="a regular expression that the output of a correct run matches")
    ap.add_argument("--judge", nargs="+", default=None, metavar=("FILE", "ARG"),
                    help="a Python file defining judge(stdout, *ARG) -> 'ok' or what is wrong, and its arguments")
    ap.add_argument("--base", default=None, help="run every run in a fresh copy of this directory")
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-campaign", default=None,
                    help="with KEEP_TRACES set, keep the traces under this campaign (stretto.keep; default: the table's)")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    A = ap.parse_args()
    A.cmd = A.cmd[1:] if A.cmd and A.cmd[0] == "--" else A.cmd
    if not A.cmd:
        ap.error("no program given")
    if A.base and A.confirm:
        ap.error("--confirm runs every replay in the campaign's directory, and --base gives each run its own")
    if A.sample and A.max_replay:
        ap.error("--sample and --max-replay choose the replays in two different ways")
    A.cmd[0] = os.path.abspath(A.cmd[0])
    A.out = os.path.abspath(A.out)
    A.classify = os.path.abspath(A.classify) if A.classify else None
    A.base = os.path.abspath(A.base) if A.base else None
    if A.judge:
        JUDGE = runpy.run_path(os.path.abspath(A.judge[0]))["judge"]
    A.campaign = A.keep_campaign or keep.campaign_of(A.out)
    need_preload(tracer_lib(A.mpi))          # ld.so would ignore a missing tracer, and the runs leave no trace
    os.makedirs(os.path.dirname(A.out), exist_ok=True)
    A.work = tempfile.mkdtemp(prefix="history-run-")
    os.chdir(A.work)                         # every run's directory, traced and replayed alike, without --base
    jobs = [(np, i) for np in A.np for i in range(1, A.runs + 1)]
    per_run, own, offered = {}, {}, []
    ctx = multiprocessing.get_context("fork")
    with concurrent.futures.ProcessPoolExecutor(max_workers=A.jobs, mp_context=ctx) as ex:
        for (np, i), (res, row, alts) in zip(jobs, ex.map(one, *zip(*jobs))):
            per_run[(np, i)], own[(np, i)] = res, row
            offered += alts
            write(A.out, [r for rs in per_run.values() for r in rs])     # a campaign keeps what it has
    if A.sample:
        picks = collections.defaultdict(list)
        for np in A.np:
            for kind in A.replay:
                pool = [x for x in offered if x[0] == np and x[4] == kind]
                rng = random.Random(f"{A.seed}:{np}:{kind}")
                chosen = sorted(rng.sample(range(len(pool)), min(A.sample, len(pool))))
                print(f"np {np} {kind}: replaying {len(chosen)} of {len(pool)} alternatives", flush=True)
                for c in chosen:
                    _, i, j, pin, k = pool[c]
                    picks[(np, i)].append((j, pin, k))
        todo = sorted(picks)
        args = [(np, i, own[(np, i)], picks[(np, i)]) for np, i in todo]
        with concurrent.futures.ProcessPoolExecutor(max_workers=A.jobs, mp_context=ctx) as ex:
            for (np, i), res in zip(todo, ex.map(sampled, *zip(*args)) if args else []):
                kept = [r for r in per_run[(np, i)] if r.get("role")]       # its confirmations; the bare row goes
                per_run[(np, i)] = kept + res
                write(A.out, [r for rs in per_run.values() for r in rs])
    write(A.out, [r for rs in per_run.values() for r in rs])
    os.chdir(ROOT)
    shutil.rmtree(A.work, ignore_errors=True)
    print("wrote", A.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
