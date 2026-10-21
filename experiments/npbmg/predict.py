#!/usr/bin/env python3
"""Does one traced run of NPB MG predict that a wildcard receive takes a message meant for
another exchange, and does the run then compute another result?

MG 2.4.1 posts the receives of every halo exchange (ready() in mg.f) with MPI_ANY_SOURCE and
a tag that depends only on the axis and the direction, not on the grid level; NPB 3.3.1's
Changes.log: "could cause MPI_Wait return a message meant for the wrong k", fixed in 3.2.1 by
naming the neighbour. Every receive of one exchange has its own tag and its one sender, so an
alternative the analysis accepts is a message of another exchange. For each process count,
RUNS traced runs are analysed and every alternative is replayed with the pins of its whole
horizon and with its control; the residual norm MG prints tells whether the replay computed
another result, and two checks judge it: 2.4.1's own (absolute difference to the reference at
most 1e-8) and the one of later versions (relative difference at most 1e-8).

usage: predict.py <bin dir> --version V [--class S] [--runs N] [--np 16 32] [--timeout S]
                  [--jobs J] [--out CSV]
Writes one row per replayed alternative (one per run without any): version, class, np, run,
records, alternatives, pin, bytes of the alternative's message and of the observed one,
replay result, every required pin matched, norm, control result, control matched, control
norm, commit.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from stretto import trace  # noqa: E402
from stretto.confirm import all_matched, classify_rc, horizon_pins, required_matches, run_pinned  # noqa: E402
from stretto.report import analyze  # noqa: E402
from provenance import code_commit  # noqa: E402

TRACER = os.path.join(ROOT, "tracer", "bin", "mpich", "libstretto-trace.so")
NORM = re.compile(r"L2 Norm is\s+(\S+)")


def norm(out: str) -> str:
    m = NORM.search(out)
    return m.group(1) if m else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bindir")
    ap.add_argument("--version", required=True)
    ap.add_argument("--class", dest="cls", default="S")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--np", type=int, nargs="+", default=[16, 32])
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--jobs", type=int, default=1, help="replays run at once")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows = []
    for np in args.np:
        exe = os.path.join(args.bindir, f"mg.{args.cls}.{np}")
        for i in range(1, args.runs + 1):
            d = tempfile.mkdtemp(prefix=f"mg-{np}-{i}-")
            cmd = ["/usr/bin/timeout", "600", "/usr/bin/mpirun.openmpi", "--oversubscribe", "-np", str(np),
                   "-x", f"LD_PRELOAD={TRACER}", "-x", f"STRETTO_TRACE_DIR={d}", exe]
            r = subprocess.run(cmd, cwd=args.bindir, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            row = {"version": args.version, "class": args.cls, "np": np, "run": i, "commit": code_commit()}
            if r.returncode != 0:
                rows.append({**row, "replay": f"traced run rc={r.returncode}"})
                shutil.rmtree(d, ignore_errors=True)
                continue
            t = trace.load(d)
            rep = analyze(t, with_sp=False)
            found = [f for f in rep.findings if f.level == "L1" and f.recv is not None and f.send is not None]
            row.update(records=sum(len(v) for v in t.ranks.values()), alternatives=len(found), traced_norm=norm(r.stdout))
            if not found:
                rows.append(row)

            def replay(f):
                rv, s = t.recvs[f.recv], t.sends[f.send]
                res = {**row, "pin": f.pin, "bytes": s.count * s.tsize,
                       "observed_bytes": (rv.mcount or 0) * rv.tsize}
                hp = horizon_pins(t, f)
                if hp is None:
                    return {**res, "replay": "no horizon"}
                alt, ctl = hp
                ra = run_pinned(np, exe, [], alt, args.timeout, mpi="mpich", cwd=args.bindir)
                rc = run_pinned(np, exe, [], ctl, args.timeout, mpi="mpich", cwd=args.bindir)
                return {**res, "replay": classify_rc(ra.returncode, ra.stderr, ra.elapsed, args.timeout),
                        "matched": all_matched(alt, ra.stderr, required_matches(t, f)), "norm": norm(ra.stdout),
                        "control": classify_rc(rc.returncode, rc.stderr, rc.elapsed, args.timeout),
                        "control_matched": all_matched(ctl, rc.stderr), "control_norm": norm(rc.stdout)}

            # replays of one trace are independent runs
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
                for res in pool.map(replay, found):
                    rows.append(res)
                    print({k: res.get(k) for k in ("np", "run", "pin", "bytes", "observed_bytes", "replay", "matched", "norm",
                                                   "control", "control_norm")}, flush=True)
            print(f"np={np} run={i}: {row['records']} records, {len(found)} alternatives", flush=True)
            shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    keys = ["version", "class", "np", "run", "records", "alternatives", "traced_norm", "pin", "bytes", "observed_bytes",
            "replay", "matched", "norm", "control", "control_matched", "control_norm", "commit"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
