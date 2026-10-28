#!/usr/bin/env python3
"""NINJA, the noise-injection baseline: run a program under NINJA's noise (experiments/ninja/
build.sh builds libninja.so) and record which runs do not finish.

NINJA delays sends from a helper thread and keeps the non-overtaking order per destination.
The runs load libninja-fixed.so, whose random draws work and which runs Fortran programs
(build.sh), or the library that NINJA_LIBRARY names (libninja-fixed-some.so: with the statuses
of MPI_Waitsome repaired as well); --shipped loads libninja.so as shipped, whose random draws
are all equal, so that random noise delays no send below a ratio of 100. Its modes, as NINJA
reads them from the environment:
  random  NIN_PATTERN=1: each send is delayed with probability NIN_RAND_RATIO percent by
          NIN_RAND_DELAY microseconds (--rand-ratio, --rand-delay)
  system  NIN_PATTERN=2 NIN_MODE=0, system-centric: a model of the network queue delays a send
          while earlier sends would still be in flight; at MPI_Finalize every rank writes what it
          learned about the intervals between sends of one tag (.ninja/<program>.<rank>.nin)
  app     NIN_PATTERN=2 NIN_MODE=1, application-centric: reads those files and delays the sends
          of a tag to a random fifth of the ranks by 1.2 times the learned interval, so that a
          later group of sends with the same tag can overtake an earlier one. It needs one
          system-centric learning run that finishes; that run is recorded as run 0 (retried up
          to three times if it does not finish), and every run starts from a fresh copy of its
          files, since NINJA rewrites them at MPI_Finalize. With --learn-mark M a learning run
          whose output reached the mark M counts as well, for a program that NINJA's library
          makes fail after its work and after MPI_Finalize, where NINJA has written its files
  plain   no NINJA, the reference
Every run gets its own working directory (NINJA's learning directory is relative to it).
A run that is not a NINJA run stops the campaign with HARNESS-ERROR and exit status 3: the
library missing, or ld.so reporting that it "cannot be preloaded" (the run then goes on
without it), and a run killed before its timeout (a memory cap or an outside kill, not a run
the noise exposed). Rows written before stay. A corpus selection that selects nothing, a
selected program without its binary and a missing program end the run before any row.

usage:
  run.py runs --np N --mode MODE --runs K --out CSV [--label L] [--timeout S] [--mpi mpich|openmpi]
              [--marks name=regex,...] [--value REGEX] [--check CMD] [--line-buffered] [--keep DIR]
              [--learn-mark NAME] -- exe [args]
      K runs of one program, one row each: label, np, mode, setting, run, result, seconds,
      reached (the last of --marks found in its output), value (the group of the last match of
      --value; with --check, the last line that the shell command CMD prints when run in the run's
      working directory after the run, for a program that writes its result to a file), commit.
      Rows are appended to CSV, which must have these columns if it exists
      (experiments/ninja/campaign.sh starts each table afresh).
  run.py corpus <corpus csv> --mode MODE --out CSV [--runs 30] [--timeout 60] [--jobs J]
              [--select REGEX] [--mpi openmpi]
      the stress baseline's protocol (experiments/stress_baseline.py) under NINJA: every
      compiled program of a corpus table that --select selects (experiments/selection.py: a
      path with "/" and a table name with "__" select the same programs), up to --runs runs (in app mode the learning run counts
      as the first), stopping at the first run that does not finish. One row per program:
      program, expect, mode, setting, runs, first_failure, outcome, learn, seconds, commit.
"""
from __future__ import annotations
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from stretto.confirm import HarnessError, classify_rc  # noqa: E402
from provenance import code_commit  # noqa: E402
from selection import selects  # noqa: E402

NINJA = os.environ.get("NINJA_DIR", os.path.join(ROOT, "third_party", "ninja"))
MODES = ("plain", "random", "system", "app")
LEARN_TRIES = 3


def mode_env(mode: str, args) -> dict:
    if mode == "random":
        return {"NIN_PATTERN": "1", "NIN_RAND_RATIO": str(args.rand_ratio), "NIN_RAND_DELAY": str(args.rand_delay)}
    if mode in ("system", "learn"):
        return {"NIN_PATTERN": "2", "NIN_MODE": "0"}
    if mode == "app":
        return {"NIN_PATTERN": "2", "NIN_MODE": "1"}
    return {}


def setting(mode: str, args) -> str:
    if mode != "random":
        return ""
    return f"ratio={args.rand_ratio:g}% delay={args.rand_delay}us" + (" as shipped" if args.shipped else "")


def ninja_lib(args) -> str:
    return os.path.join(NINJA, args.mpi, "libninja.so" if args.shipped else os.environ.get("NINJA_LIBRARY") or "libninja-fixed.so")


def run_once(np_: int, exe: str, argv: list, mode: str, wd: str, args) -> dict:
    """One run in working directory wd. mode 'learn' is the system-centric learning run."""
    env_args = []
    if mode != "plain":
        env_args += ["-x", f"LD_PRELOAD={ninja_lib(args)}"]
        for k, v in mode_env(mode, args).items():
            env_args += ["-x", f"{k}={v}"]
    prog = ["/usr/bin/stdbuf", "-oL", exe] if args.line_buffered else [exe]
    cmd = ["/usr/bin/timeout", "-k", "5", str(args.timeout), "/usr/bin/mpirun.openmpi", "-np", str(np_), *env_args, *prog, *argv]
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL, cwd=wd,
                       env={**os.environ, "OMPI_MCA_rmaps_base_oversubscribe": "1", "OMPI_MCA_hwloc_base_binding_policy": "none"})
    elapsed = time.monotonic() - t0
    out = {"result": classify_rc(r.returncode, r.stderr, elapsed, args.timeout), "seconds": round(elapsed, 1),
           "stdout": r.stdout, "stderr": r.stderr, "reached": "", "value": ""}
    if mode != "plain" and "cannot be preloaded" in r.stderr:
        # ld.so could not preload NINJA (wrong MPI, wrong architecture) and the run went on without it
        why = next(ln for ln in r.stderr.splitlines() if "cannot be preloaded" in ln).strip()
        raise HarnessError(f"{ninja_lib(args)} was not preloaded: {why}")
    if out["result"] == "KILLED":
        # a SIGKILL before the timeout is a memory cap or an outside kill, not a run the noise exposed
        raise HarnessError(f"a {mode} run was killed after {out['seconds']} s, before its timeout of {args.timeout} s "
                           f"(status {r.returncode}): a memory cap or an outside kill")
    text = r.stdout + r.stderr
    for name, rx in getattr(args, "mark_list", []):
        if re.search(rx, text):
            out["reached"] = name
    if getattr(args, "value", None):
        m = re.findall(args.value, text)
        out["value"] = m[-1] if m else ""
    if getattr(args, "check", None):
        c = subprocess.run(args.check, shell=True, cwd=wd, capture_output=True, text=True, errors="replace",
                           stdin=subprocess.DEVNULL)
        lines = c.stdout.strip().splitlines()
        out["value"] = lines[-1].strip() if lines else f"check-failed:rc={c.returncode}"
    return out


def learning_files(wd: str, exe: str, np_: int) -> int:
    """How many ranks wrote a learning file. A rank whose file is missing gets an empty one,
    which NINJA reads as nothing learned (it does not check that the file opens)."""
    d = os.path.join(wd, ".ninja")
    os.makedirs(d, exist_ok=True)
    found = 0
    for rank in range(np_):
        f = os.path.join(d, f"{os.path.basename(exe)}.{rank}.nin")
        if os.path.exists(f):
            found += 1
        else:
            open(f, "w").close()
    return found


def keep_log(args, name: str, res: dict) -> None:
    if args.keep and res["result"] != "FINISHED":
        os.makedirs(args.keep, exist_ok=True)
        with open(os.path.join(args.keep, name + ".log"), "w") as fh:
            fh.write(f"result={res['result']} seconds={res['seconds']}\n--- stdout\n{res['stdout'][-4000:]}\n--- stderr\n{res['stderr'][-4000:]}\n")


def open_table(path: str, fields: list):
    """The table at path opened for appending rows of these columns, with its header if new;
    a table with other columns is refused (its rows would land in the wrong columns)."""
    new = not os.path.exists(path)
    if not new:
        with open(path, newline="") as fh:
            if next(csv.reader(fh), []) != fields:
                raise HarnessError(f"{path} has other columns than {','.join(fields)}; remove it or choose another --out")
    fh = open(path, "a", newline="")
    w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
    if new:
        w.writeheader()
    return fh, w


def cmd_runs(args) -> int:
    args.mark_list = [tuple(m.split("=", 1)) for m in args.marks.split(",")] if args.marks else []
    exe = os.path.abspath(args.exe)
    if not os.path.isfile(exe):
        raise HarnessError(f"no program {exe}; nothing written")
    fields = ["label", "np", "mode", "setting", "run", "result", "seconds", "reached", "value", "commit"]
    fh, w = open_table(args.out, fields)

    def emit(run, res, mode):
        w.writerow({"label": args.label, "np": args.np, "mode": mode, "setting": setting(mode, args) if run else
                    ("learning run (system-centric)" if mode == "app" else setting(mode, args)), "run": run,
                    "result": res["result"], "seconds": res["seconds"], "reached": res["reached"], "value": res["value"],
                    "commit": code_commit()})
        fh.flush()
        print(f"{args.label} np={args.np} {mode} run {run}: {res['result']} {res['seconds']} s {res['reached']} {res['value']}", flush=True)
        keep_log(args, f"{args.label}-{args.np}-{mode}-{run}", res)

    with tempfile.TemporaryDirectory(prefix="ninja-") as tmp:
        learned = None
        if args.mode == "app":
            for _ in range(LEARN_TRIES):
                wd = tempfile.mkdtemp(dir=tmp)
                res = run_once(args.np, exe, args.argv, "learn", wd, args)
                emit(0, res, "app")
                if res["result"] == "FINISHED" or (args.learn_mark and res["reached"] == args.learn_mark):
                    learning_files(wd, exe, args.np)
                    learned = os.path.join(wd, ".ninja")
                    break
            if learned is None:
                print("no learning run finished; no application-centric runs", flush=True)
                return 1
        for i in range(1, args.runs + 1):
            wd = tempfile.mkdtemp(dir=tmp)
            if learned:
                shutil.copytree(learned, os.path.join(wd, ".ninja"))
            res = run_once(args.np, exe, args.argv, args.mode, wd, args)
            emit(i, res, args.mode)
            shutil.rmtree(wd, ignore_errors=True)
            if args.stop_at_first and res["result"] != "FINISHED":
                break
    return 0


def corpus_one(row: dict, exe: str, args) -> dict:
    np_ = int(row["np"])
    t0 = time.monotonic()
    out = {"program": row["name"], "expect": row["expect"], "mode": args.mode, "setting": setting(args.mode, args),
           "runs": 0, "first_failure": "", "outcome": "", "learn": "", "seconds": 0.0, "commit": code_commit()}
    with tempfile.TemporaryDirectory(prefix="ninja-") as tmp:
        learned = None
        for i in range(1, args.runs + 1):
            wd = tempfile.mkdtemp(dir=tmp)
            mode = args.mode
            if args.mode == "app" and learned is None:
                mode = "learn"          # the learning run is the first run of the budget
            elif learned:
                shutil.copytree(learned, os.path.join(wd, ".ninja"))
            res = run_once(np_, exe, [], mode, wd, args)
            out["runs"] = i
            if mode == "learn":
                out["learn"] = res["result"]
                if res["result"] == "FINISHED":
                    out["learn"] = f"FINISHED, {learning_files(wd, exe, np_)}/{np_} ranks wrote a file"
                    learned = os.path.join(wd, ".ninja")
            if res["result"] != "FINISHED":
                out["first_failure"], out["outcome"] = i, res["result"]
                break
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


def cmd_corpus(args) -> int:
    base = os.path.dirname(os.path.abspath(args.csv))
    if not os.path.exists(args.csv):
        raise HarnessError(f"no corpus table {args.csv}")
    with open(args.csv) as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("build") == "ok" and selects(args.select, r["name"])]
    if not rows:
        raise HarnessError(f"--select {args.select!r} selects no compiled program of {args.csv}; nothing written")
    todo, missing = [], []
    for r in rows:
        e = os.path.join(base, r["name"].replace("/", "__"), "main")
        (todo if os.path.exists(e) else missing).append((r, e))
    if missing:
        # a program the corpus run built whose binary is gone would silently leave the table
        raise HarnessError(f"{len(missing)} selected program(s) have no binary, e.g. {missing[0][1]}; nothing written "
                 "(run the corpus step first)")
    fields = ["program", "expect", "mode", "setting", "runs", "first_failure", "outcome", "learn", "seconds", "commit"]
    fh, w = open_table(args.out, fields)
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for i, res in enumerate(ex.map(lambda x: corpus_one(x[0], x[1], args), todo), 1):
            w.writerow(res)
            fh.flush()
            print(f"[{i}/{len(todo)}] {args.mode:<6} {res['program']:<50} runs={res['runs']:<3} "
                  f"first_failure={res['first_failure'] or '-'} {res['outcome']} {res['learn']}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("runs", "corpus"):
        p = sub.add_parser(name)
        p.add_argument("--mode", choices=MODES, required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--timeout", type=int, default=60)
        p.add_argument("--mpi", default="openmpi" if name == "corpus" else "mpich")
        p.add_argument("--rand-ratio", type=float, default=10)
        p.add_argument("--rand-delay", type=int, default=50000)
        p.add_argument("--shipped", action="store_true")
        p.add_argument("--line-buffered", action="store_true")
        p.add_argument("--keep", default="")
    p = sub.choices["runs"]
    p.add_argument("--np", type=int, required=True)
    p.add_argument("--runs", type=int, default=40)
    p.add_argument("--label", default="")
    p.add_argument("--marks", default="")
    p.add_argument("--value", default="")
    p.add_argument("--check", default="")
    p.add_argument("--stop-at-first", action="store_true")
    p.add_argument("--learn-mark", default="")
    p.add_argument("exe")
    p.add_argument("argv", nargs="*")
    p = sub.choices["corpus"]
    p.add_argument("csv")
    p.add_argument("--runs", type=int, default=30)
    p.add_argument("--jobs", type=int, default=3)
    p.add_argument("--select", default=".")
    args = ap.parse_args()
    if args.cmd == "runs" and not args.label:
        args.label = os.path.basename(args.exe)
    try:
        # the dynamic loader skips an LD_PRELOAD entry it cannot open with only a warning on
        # stderr, so without the library every run would be a plain run recorded as a NINJA one
        if args.mode != "plain" and not os.path.isfile(ninja_lib(args)):
            raise HarnessError(f"{ninja_lib(args)} is missing; build NINJA first (experiments/ninja/build.sh fetch "
                               "build) or point NINJA_DIR at a build")
        return cmd_runs(args) if args.cmd == "runs" else cmd_corpus(args)
    except HarnessError as e:
        # status 3, apart from 1 (no application-centric learning run finished): the rows
        # written before stay, and experiments/ninja/campaign.sh starts its tables afresh
        print(f"run.py: HARNESS-ERROR: {e}", file=sys.stderr, flush=True)
        return 3


if __name__ == "__main__":
    sys.exit(main())
