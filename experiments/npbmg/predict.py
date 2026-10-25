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

Replays and controls run under the tracer when it is built (stretto.confirm.run_checked), and
each row says whether the run is one of the program without its pins (run_valid,
control_valid) and, if not, why. --sample N replays a random sample of N alternatives, drawn
with --seed from those of every traced run, instead of all of them. With KEEP_TRACES set, every
trace is kept (stretto.keep) under the table's campaign (npbmg/predict-2.4.1, valid/npbmg):
<class>-np<N>-run<K> for a traced run, the same with -<pin>-replay and -control for a replay
and its control.

usage: predict.py <bin dir> --version V [--class S] [--runs N] [--np 16 32] [--timeout S]
                  [--jobs J] [--sample N] [--seed K] [--out CSV]
Writes one row per replayed alternative (one per run without any): version, class, np, run,
records, alternatives, pin, bytes of the alternative's message and of the observed one,
replay result, every required pin matched, norm, whether it is a run of the program, control
result, control matched, control norm, whether the control is one, the seed of the sample,
commit.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import csv
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from stretto import keep, trace  # noqa: E402
from stretto.confirm import all_matched, horizon_pins, need_preload, pin_name, required_matches, run_checked  # noqa: E402
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
    ap.add_argument("--sample", type=int, default=0, help="replay a random sample of this many alternatives of every traced run (0: all)")
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-campaign", default=None,
                    help="with KEEP_TRACES set, keep the traces under this campaign (stretto.keep; default: the table's)")
    args = ap.parse_args()
    args.bindir = os.path.abspath(args.bindir)     # the runs change into it, so a relative path would point elsewhere
    need_preload(TRACER)                     # ld.so would ignore a missing tracer, and the runs leave no trace
    campaign = args.keep_campaign or keep.campaign_of(args.out)
    rows, traced = [], []
    for np in args.np:
        exe = os.path.join(args.bindir, f"mg.{args.cls}.{np}")
        for i in range(1, args.runs + 1):
            d = tempfile.mkdtemp(prefix=f"mg-{np}-{i}-")
            cmd = ["/usr/bin/timeout", "600", "/usr/bin/mpirun.openmpi", "--oversubscribe", "-np", str(np),
                   "-x", f"LD_PRELOAD={TRACER}", "-x", f"STRETTO_TRACE_DIR={d}", exe]
            r = subprocess.run(cmd, cwd=args.bindir, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            row = {"version": args.version, "class": args.cls, "np": np, "run": i, "seed": args.seed if args.sample else "",
                   "commit": code_commit()}
            keep.keep(d, campaign, f"{args.cls}-np{np}-run{i:02d}", commit=row["commit"])
            if r.returncode != 0:
                rows.append({**row, "replay": f"traced run rc={r.returncode}"})
                shutil.rmtree(d, ignore_errors=True)
                continue
            t = trace.load(d)
            shutil.rmtree(d, ignore_errors=True)
            rep = analyze(t, with_sp=False)
            found = [f for f in rep.findings if f.level == "L1" and f.recv is not None and f.send is not None]
            row.update(records=sum(len(v) for v in t.ranks.values()), alternatives=len(found), traced_norm=norm(r.stdout))
            traced.append((row, t, exe, found))
            print(f"np={np} run={i}: {row['records']} records, {len(found)} alternatives", flush=True)
    # the alternatives to replay: all, or a sample drawn from those of every traced run
    pool = [(j, f) for j, (_, _, _, found) in enumerate(traced) for f in found]
    chosen = sorted(random.Random(args.seed).sample(range(len(pool)), min(args.sample, len(pool)))) if args.sample else range(len(pool))
    todo = [pool[c] for c in chosen]
    print(f"replaying {len(todo)} of {len(pool)} alternatives", flush=True)

    def replay(j, f):
        row, t, exe, _ = traced[j]
        np = row["np"]
        rv, s = t.recvs[f.recv], t.sends[f.send]
        res = {**row, "pin": f.pin, "bytes": s.count * s.tsize, "observed_bytes": (rv.mcount or 0) * rv.tsize}
        hp = horizon_pins(t, f)
        if hp is None:
            return {**res, "replay": "no horizon"}
        alt, ctl = hp
        name = f"{args.cls}-np{np}-run{row['run']:02d}-{pin_name(f.pin)}"
        ra = run_checked(np, exe, [], alt, args.timeout, mpi="mpich", cwd=args.bindir, keep_as=(campaign, name + "-replay"))
        rc = run_checked(np, exe, [], ctl, args.timeout, mpi="mpich", cwd=args.bindir, keep_as=(campaign, name + "-control"))
        return {**res, "replay": ra.result, "matched": all_matched(alt, ra.proc.stderr, required_matches(t, f), ra.seen),
                "norm": norm(ra.proc.stdout), "run_valid": ra.valid, "violation": ra.why[:300],
                "control": rc.result, "control_matched": all_matched(ctl, rc.proc.stderr, None, rc.seen),
                "control_norm": norm(rc.proc.stdout),
                "control_valid": rc.valid, "control_violation": rc.why[:300]}

    # the replays are independent runs
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for res in ex.map(lambda x: replay(*x), todo):
            rows.append(res)
            print({k: res.get(k) for k in ("np", "run", "pin", "bytes", "observed_bytes", "replay", "matched", "norm", "run_valid",
                                           "control", "control_norm", "control_valid", "violation")}, flush=True)
    # a traced run none of whose alternatives was replayed keeps a row of its own
    replayed = {j for j, _ in todo}
    rows += [row for j, (row, _, _, _) in enumerate(traced) if j not in replayed]
    rows.sort(key=lambda r: (int(r["np"]), int(r["run"])))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    keys = ["version", "class", "np", "run", "records", "alternatives", "traced_norm", "pin", "bytes", "observed_bytes",
            "replay", "matched", "norm", "run_valid", "violation", "control", "control_matched", "control_norm", "control_valid",
            "control_violation", "seed", "commit"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote", args.out)
    # a table of failed traced runs only is no result (a wrong path, a missing build): say so
    if not traced:
        print(f"every traced run failed ({len(rows)} of {len(rows)}); see the table's replay column", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
