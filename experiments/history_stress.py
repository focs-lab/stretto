#!/usr/bin/env python3
"""The testing baseline of a documented bug: untraced runs of the program, RUNS per process count
and setting, and how each ended.

Settings:
  plain    the program as it is, nothing preloaded;
  delays   with random send and receive delays from the replay library (STRETTO_SEND_DELAY_MS
           and STRETTO_RECV_DELAY_MS): per rank one of 0, 0, 0, 1, 5 and 20 ms before every send
           and every receive, drawn for run i at np processes from random.Random(i * 100 + np)
           and random.Random(i * 100 + np + 7), the delays of experiments/npbmg/run.sh and
           experiments/dealii/run.sh; a run in which the library did not announce itself stops
           the step (stretto.confirm.run_pinned), since it would be a plain run;
  eager    with Open MPI's eager threshold forced down to --eager payload bytes, as stress
           testing on the corpora does with the limit forced down (experiments/stress_baseline.py
           --eager: the TCP transport over the loopback interface with its eager limit, and the
           self transport's, at B plus the TCP header measured in experiments/results/b_impl.csv),
           and no delays.
A run is a failure when it does not finish (TIMEOUT for a hang, MPI-ERROR:... or APP-ERROR:N,
stretto.confirm.classify_rc) or when its output is wrong: it does not match --ok, or the
function judge(stdout, *ARG) of --judge FILE [ARG ...] does not return "ok" (its verdict is the
says column, as in experiments/history_predict.py). Every run runs in a fresh directory, empty
or, with --base DIR, a copy of DIR (for a program that writes into its directory); the output
of a failure is kept under outputs/ next to the table (not committed). --label names what the
table's rows run (for example the test and its repetitions), so that several invocations, each
with its own arguments, append to one table.

usage: history_stress.py --version V --label L --np N [N ...] --runs R --out CSV
                         [--settings plain delays eager] [--eager B] [--timeout S] [--jobs J]
                         [--mpi openmpi] [--ok REGEX] [--judge FILE [ARG ...]] [--base DIR]
                         -- <exe> [args ...]
Appends to CSV (written with its header when new, refused when its header differs) one row per
run: version, label, np, run, setting, result, ok, says, seconds, commit.
"""
from __future__ import annotations
import argparse
import collections
import concurrent.futures
import csv
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
from stretto import keep  # noqa: E402
from stretto.confirm import classify_rc, run_pinned  # noqa: E402
from provenance import code_commit  # noqa: E402
from b_impl import tcp, tcp_header  # noqa: E402

DELAYS_MS = [0, 0, 0, 1, 5, 20]
KEYS = ["version", "label", "np", "run", "setting", "result", "ok", "says", "seconds", "commit"]


def delays(np: int, i: int) -> dict:
    r1, r2 = random.Random(i * 100 + np), random.Random(i * 100 + np + 7)
    return {"STRETTO_SEND_DELAY_MS": ",".join(f"{k}:{r1.choice(DELAYS_MS)}" for k in range(np)),
            "STRETTO_RECV_DELAY_MS": ",".join(f"{k}:{r2.choice(DELAYS_MS)}" for k in range(np))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--np", type=int, nargs="+", required=True)
    ap.add_argument("--runs", type=int, default=40)
    ap.add_argument("--settings", nargs="+", default=["plain", "delays"], choices=["plain", "delays", "eager"])
    ap.add_argument("--eager", type=int, default=0, help="the eager threshold of the eager setting, in payload bytes")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--mpi", default="openmpi")
    ap.add_argument("--ok", default=None)
    ap.add_argument("--judge", nargs="+", default=None, metavar=("FILE", "ARG"),
                    help="a Python file defining judge(stdout, *ARG) -> 'ok' or what is wrong, and its arguments")
    ap.add_argument("--base", default=None, help="run every run in a fresh copy of this directory")
    ap.add_argument("--out", required=True)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        ap.error("no program given")
    exe, args = os.path.abspath(cmd[0]), cmd[1:]
    out = os.path.abspath(a.out)
    judge = runpy.run_path(os.path.abspath(a.judge[0]))["judge"] if a.judge else None
    base = os.path.abspath(a.base) if a.base else None
    eager = []
    if "eager" in a.settings:
        header = tcp_header()
        if header is None:
            sys.exit("history_stress.py: no TCP header in experiments/results/b_impl.csv (experiments/b_impl.py measures it)")
        eager = tcp(a.eager + header)
    commit = code_commit()

    def run(np: int, i: int, setting: str) -> dict:
        w = tempfile.mkdtemp(prefix="history-stress-")
        try:
            if base:
                shutil.copytree(base, w, dirs_exist_ok=True, symlinks=True)
            if setting == "delays":
                p = run_pinned(np, exe, args, None, a.timeout, a.mpi, extra_env=delays(np, i), cwd=w)
                el = p.elapsed
            else:
                c = ["/usr/bin/timeout", "-k", "5", str(a.timeout), "/usr/bin/mpirun.openmpi", "--oversubscribe", "-np", str(np),
                     *(eager if setting == "eager" else []), exe, *args]
                t0 = time.monotonic()
                p = subprocess.run(c, cwd=w, capture_output=True, text=True, stdin=subprocess.DEVNULL)
                el = time.monotonic() - t0
        finally:
            shutil.rmtree(w, ignore_errors=True)
        res = classify_rc(p.returncode, p.stderr, el, a.timeout)
        says = judge(p.stdout, *a.judge[1:]) if judge else "ok"
        ok = (bool(re.search(a.ok, p.stdout + p.stderr)) if a.ok else True) and says == "ok"
        if res != "FINISHED" or not ok:
            d = os.path.join(os.path.dirname(out), "outputs")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, keep.safe(f"stress-{a.version}-{a.label}-np{np}-{setting}-{i:02d}") + ".txt"), "w") as fh:
                fh.write(f"{' '.join(p.args) if isinstance(p.args, list) else p.args}\nstatus {p.returncode}\n--- stdout\n"
                         f"{p.stdout[-20000:]}\n--- stderr\n{p.stderr[-20000:]}")
        return {"version": a.version, "label": a.label, "np": np, "run": i, "setting": setting, "result": res, "ok": ok,
                "says": says, "seconds": round(el, 1), "commit": commit}

    jobs = [(np, i, s) for np in a.np for i in range(1, a.runs + 1) for s in a.settings]
    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for r in ex.map(lambda x: run(*x), jobs):
            rows.append(r)
            print(r, flush=True)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    new = not os.path.exists(out)
    if not new:
        with open(out, newline="") as fh:
            if next(csv.reader(fh), []) != KEYS:     # rows under another header would land in the wrong columns
                sys.exit(f"history_stress.py: {out} has other columns than this version writes; remove it")
    with open(out, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=KEYS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerows(rows)
    for np in a.np:
        for s in a.settings:
            c = collections.Counter((r["result"], r["ok"]) for r in rows if r["np"] == np and r["setting"] == s)
            print(f"{a.label} np={np} {s}: " + ", ".join(f"{res}{'' if ok else ' (output not ok)'} {n}" for (res, ok), n in sorted(c.items())))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
