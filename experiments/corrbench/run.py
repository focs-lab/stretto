#!/usr/bin/env python3
"""Stretto on the 513 programs of MPI-CorrBench a05644b outside its OpenMP directory
(experiments/corrbench/programs.csv, classify.py). Inside the experiment image
(experiments/container/Dockerfile), with the tracer and the rerun shim built for the MPI:

  experiments/corrbench/run.py [--mpi openmpi|mpich] [--select REGEX] [--jobs J] CORRBENCH_0_LEVEL_DIR OUT.csv WORK

Each program is built with the MPI's mpicc (-O0 -g, its own directory and correct/include on
the include path, as CorrBench's MPICH tests need), and run on 2 processes, as CorrBench's own
harness runs every program (scripts/*/execute_tool.sh), under Open MPI's mpirun for both MPIs
(stretto.confirm.DEFAULT_LAUNCHERS). Runs per program, one row each (column mode):
  native  one run without the tracer, for the outcome and the time;
  trace   REPS traced runs with payloads (STRETTO_PAYLOAD, as experiments/mbi/run_mbi.py --rerun),
          each analysed as run_mbi.py analyses an MBI program (L1, L1+SP); with Open MPI each
          is analysed a second time with level L2 by re-execution (stretto.rerun, columns l2 and
          rerun_*), which the shim is built for;
  guard   REPS traced runs with the buffer guard and buffer ranges (STRETTO_GUARD=1,
          STRETTO_BUFS=1), as run_mbi.py --guard, for the local-concurrency classes.
A run that left no trace (the program stopped before MPI_Init returned) has analysis NO-TRACE.
A run's verdict follows experiments/mbi/score.py (any error class; summary.py). Settings:
REPS (3), TIMEOUT (30 s per run; the programs run for well under a second unless they hang),
STRETTO_COMMIT (else COMMIT at the repository root). Raw outputs go to WORK/<mpi>/<program>/
and may be deleted once the table is written."""
from __future__ import annotations
import argparse
import csv
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "experiments", "mbi"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
import run_mbi  # noqa: E402
from provenance import code_commit  # noqa: E402
from stretto.confirm import classify_rc  # noqa: E402

MPIRUN = "/usr/bin/mpirun.openmpi"
KEYS = ["mpi", "program", "area", "conflo", "label", "kind", "scope", "mode", "run", "result", "seconds", "analysis",
        "findings", "classes", "levels", "l2", "l2_dismissed", "l2_undetermined", "rerun_runs", "rerun_unreliable", "notes",
        "commit"]


def run(cmd, timeout, cwd, env=None):
    t0 = time.monotonic()
    r = subprocess.run(["/usr/bin/timeout", "-k", "5", str(timeout), *cmd], capture_output=True, text=True, cwd=cwd,
                       stdin=subprocess.DEVNULL, env={**os.environ, "OMPI_MCA_rmaps_base_oversubscribe": "1", **(env or {})})
    took = time.monotonic() - t0
    return r, classify_rc(r.returncode, r.stderr, took, timeout), took


def one(meta: dict, args) -> list:
    rel = meta["program"]
    src = os.path.join(args.corrbench, rel)
    out = os.path.join(args.work, args.mpi, rel[:-2].replace("/", "__"))
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    base = {**{k: meta[k] for k in ("program", "area", "conflo", "label", "kind", "scope")}, "mpi": args.mpi,
            "commit": args.commit}
    exe = os.path.join(out, "main")
    b = subprocess.run([f"/usr/bin/mpicc.{args.mpi}", "-O0", "-g", "-I", os.path.dirname(src),
                        "-I", os.path.join(args.corrbench, "correct", "include"), "-o", exe, src, "-lm"],
                       capture_output=True, text=True)
    if b.returncode != 0:
        return [{**base, "mode": "build", "run": 0, "result": "BUILD-FAILED", "notes": " ".join(b.stderr.split())[-300:]}]
    rows = []
    r, res, took = run([MPIRUN, "-np", "2", exe], args.timeout, out)
    rows.append({**base, "mode": "native", "run": 0, "result": res, "seconds": f"{took:.2f}"})
    lib = os.path.join(ROOT, "tracer", "bin", args.mpi, "libstretto-trace.so")
    for mode, extra in (("trace", {"STRETTO_PAYLOAD": str(run_mbi.PAYLOAD)}), ("guard", {"STRETTO_GUARD": "1", "STRETTO_BUFS": "1"})):
        for k in range(1, args.reps + 1):
            d = os.path.join(out, f"{mode}{k}")
            tracedir = os.path.join(d, "trace")
            os.makedirs(tracedir)
            env = ["-x", f"STRETTO_TRACE_DIR={tracedir}", "-x", f"LD_PRELOAD={lib}"]
            for name, value in extra.items():
                env += ["-x", f"{name}={value}"]
            r, res, took = run([MPIRUN, "-np", "2", *env, exe], args.timeout, d)
            open(os.path.join(d, "run.stdout"), "w").write(r.stdout)
            open(os.path.join(d, "run.stderr"), "w").write(r.stderr)
            row = {**base, "mode": mode, "run": k, "result": res, "seconds": f"{took:.2f}", "np": 2}
            if not any(f.startswith("rank-") for f in os.listdir(tracedir)):
                # the program stopped before MPI_Init returned (a call before it): nothing to analyse,
                # no verdict, and not a crash of the analysis
                rows.append({**row, "analysis": "NO-TRACE"})
                continue
            run_mbi.analyse(row, d, exe, tracedir, run_mbi.options(rerun=False, reads=None, confirm=False))
            if mode == "trace" and args.mpi == "openmpi" and row.get("analysis") == "ok":
                l2 = {"np": 2}
                os.makedirs(os.path.join(d, "l2"))
                run_mbi.analyse(l2, os.path.join(d, "l2"), exe, tracedir,
                                run_mbi.options(rerun=True, reads=None, confirm=False, rerun_timeout=args.timeout))
                if l2.get("analysis") == "ok":
                    row.update({c: l2.get(c, "") for c in ("l2", "l2_dismissed", "l2_undetermined", "rerun_runs", "rerun_unreliable")})
                else:
                    row["l2"] = "CRASH"
            rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corrbench")
    ap.add_argument("out")
    ap.add_argument("work")
    ap.add_argument("--mpi", default="openmpi", choices=("openmpi", "mpich"))
    ap.add_argument("--select", default="", help="regex on the program's path")
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()
    args.reps = int(os.environ.get("REPS", "3"))
    args.timeout = int(os.environ.get("TIMEOUT", "30"))
    args.commit = os.environ.get("STRETTO_COMMIT") or code_commit()
    import re
    progs = [m for m in csv.DictReader(open(os.path.join(HERE, "programs.csv"))) if re.search(args.select, m["program"])]
    done = set()
    if os.path.exists(args.out):
        done = {(r["mpi"], r["program"]) for r in csv.DictReader(open(args.out))}
    todo = [m for m in progs if (args.mpi, m["program"]) not in done]
    new = not os.path.exists(args.out)
    with open(args.out, "a", newline="") as fh, ThreadPoolExecutor(max_workers=args.jobs) as ex:
        w = csv.DictWriter(fh, fieldnames=KEYS, extrasaction="ignore", lineterminator="\n")
        if new:
            w.writeheader()
        futs = [ex.submit(one, m, args) for m in todo]
        for i, f in enumerate(as_completed(futs), 1):
            rows = f.result()
            w.writerows(rows)
            fh.flush()
            print(f"[{i}/{len(todo)}] {rows[0]['program']:<60} " + " ".join(f"{r['mode'][0]}:{r['result'][:9]}/{r.get('classes', '')}" for r in rows),
                  flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
