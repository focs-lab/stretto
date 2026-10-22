#!/usr/bin/env python3
"""Does one clean traced run of HYPRE 3.2.0's ParaSails with more than one pattern level predict
that a probe takes a request of the next level, and does the replay then hang?

ExchangePrunedRows (src/distributed_ls/ParaSails/ParaSails.c) runs once per pattern level:
SendRequests Isends the row requests with tag ROW_PRUNED_REQ_TAG (221), FindNumReplies
counts the requests each rank will get with an Allreduce, ReceiveRequest takes that many with
MPI_Probe(MPI_ANY_SOURCE, 221) and MPI_Recv from the probed source, and each request is
answered at once (tag 223); then the rank receives its own replies. Every level uses the same
tag and nothing collective lies between a rank's last probe of level L and another rank's
requests of level L+1, which it sends before the Allreduce of L+1. So a request sent after s
collectives on the communicator belongs to the level whose Allreduce is collective s+1: a
probe alternative is "cross" if the alternative message was sent after another number of
collectives than the message the probe saw (a request of another level), "same" if it is
another rank's request of the same level, and "other" on any other tag (replies, ParaSails'
diagonal exchange, hypre_DataExchangeList: reorderings inside one exchange).

For each process count and level count, RUNS traced runs of the driver are analysed and every
probe alternative is replayed with the pins of its whole horizon (stretto.confirm.horizon_pins)
and with its control, under the tracer, which judges each as a run of the program without its
pins (run_valid, control_valid). --kinds limits the replays to some kinds, --sample N to a
random sample of N of them drawn with --seed over all traced runs. With KEEP_TRACES set, every
trace is kept (stretto.keep) under the table's campaign (hypre3/predict): np<N>-levels<L>-run<K>
for a traced run, the same with -<pin>-replay and -control for a replay and its control.

usage: predict.py <driver> [--np 4 8] [--levels 1 2] [--runs N] [--timeout S] [--jobs J]
                  [--kinds cross same other] [--sample N] [--seed K] --out CSV
Writes one row per replayed alternative (one per traced run without any): version, driver,
np, levels, run, records, the run's cross, same and other alternatives, its residual, pin,
kind, tag, call site, replay result, every required pin matched, residual, whether the replay
is a run of the program, control result, control matched, control residual, whether the
control is one, seed of the sample, commit.
"""
from __future__ import annotations
import argparse
import bisect
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
from stretto.sites import sites  # noqa: E402
from provenance import code_commit  # noqa: E402

TRACER = os.path.join(ROOT, "tracer", "bin", "openmpi", "libstretto-trace.so")
RESIDUAL = re.compile(r"Final Relative Residual Norm = (\S+)")
REQUEST_TAGS = (221, 227)   # ROW_PRUNED_REQ_TAG; the fixed variant's tag of even levels (run.sh)


def residual(out: str) -> str:
    m = RESIDUAL.search(out)
    return m.group(1) if m else ""


def driver_args(levels: int):
    """ex5_levels: PCG with ParaSails (solver 8) on the 33x33 Laplacian of hypre's ex5, with
    ParaSails' pattern levels (nlevels of HYPRE_ParaSailsSetParams) given."""
    return ["-solver", "8", "-sai_levels", str(levels), "-n", "33"]


def classify(t, rep):
    """(finding, kind, tag) for every probe alternative of the report."""
    before = {}
    for c in t.colls:
        before.setdefault((c.rank, c.comm), []).append(c.idx)
    for v in before.values():
        v.sort()

    def phase(s):
        return bisect.bisect_left(before.get((s.rank, s.comm), []), s.idx)

    for f in rep.findings:
        if f.probe is None or f.send is None:
            continue
        p, s = t.probes[f.probe], t.sends[f.send]
        if s.tag not in REQUEST_TAGS:
            yield f, "other", s.tag
            continue
        seen = t.sends[p.seen]
        yield f, ("same" if phase(s) == phase(seen) else "cross"), s.tag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("driver")
    ap.add_argument("--version", default="3.2.0")
    ap.add_argument("--np", type=int, nargs="+", default=[4, 8])
    ap.add_argument("--levels", type=int, nargs="+", default=[1, 2])
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--jobs", type=int, default=1, help="replays run at once")
    ap.add_argument("--kinds", nargs="+", default=["cross", "same", "other"])
    ap.add_argument("--sample", type=int, default=0, help="replay a random sample of this many alternatives (0: all)")
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-campaign", default=None,
                    help="with KEEP_TRACES set, keep the traces under this campaign (stretto.keep; default: the table's)")
    args = ap.parse_args()
    need_preload(TRACER)                     # ld.so would ignore a missing tracer, and the runs leave no trace
    campaign = args.keep_campaign or keep.campaign_of(args.out)
    exe = os.path.abspath(args.driver)
    rows, traced = [], []
    for levels in args.levels:
        for np in args.np:
            for i in range(1, args.runs + 1):
                d = tempfile.mkdtemp(prefix=f"hypre3-{np}-{levels}-{i}-")
                cwd = tempfile.mkdtemp(prefix="hypre3-run-")
                cmd = ["/usr/bin/timeout", "300", "/usr/bin/mpirun.openmpi", "--oversubscribe", "-np", str(np),
                       "-x", f"LD_PRELOAD={TRACER}", "-x", f"STRETTO_TRACE_DIR={d}", exe, *driver_args(levels)]
                r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
                shutil.rmtree(cwd, ignore_errors=True)
                row = {"version": args.version, "driver": os.path.basename(exe), "np": np, "levels": levels, "run": i,
                       "seed": args.seed if args.sample else "", "commit": code_commit()}
                keep.keep(d, campaign, f"np{np}-levels{levels}-run{i:02d}", commit=row["commit"])
                if r.returncode != 0:
                    rows.append({**row, "replay": f"traced run rc={r.returncode}"})
                    shutil.rmtree(d, ignore_errors=True)
                    continue
                t = trace.load(d)
                names = {label: name for label, name, _ in sites(t)}
                shutil.rmtree(d, ignore_errors=True)
                rep = analyze(t, with_sp=False)
                found = list(classify(t, rep))
                row.update(records=sum(len(v) for v in t.ranks.values()),
                           cross=sum(1 for _, k, _ in found if k == "cross"),
                           same=sum(1 for _, k, _ in found if k == "same"),
                           other=sum(1 for _, k, _ in found if k == "other"),
                           traced_residual=residual(r.stdout))
                traced.append((row, t, names, levels, [x for x in found if x[1] in args.kinds]))
                print(f"np={np} levels={levels} run={i}: {row['records']} records, {row['cross']} cross, "
                      f"{row['same']} same, {row['other']} other; residual {row['traced_residual']}", flush=True)
    pool = [(j, f, k, tag) for j, (_, _, _, _, found) in enumerate(traced) for f, k, tag in found]
    chosen = sorted(random.Random(args.seed).sample(range(len(pool)), min(args.sample, len(pool)))) if args.sample else range(len(pool))
    todo = [pool[c] for c in chosen]
    print(f"replaying {len(todo)} of {len(pool)} alternatives", flush=True)

    def run(np, levels, pins, name):
        w = tempfile.mkdtemp(prefix="hypre3-replay-")
        try:
            return run_checked(np, exe, driver_args(levels), pins, args.timeout, mpi="openmpi", cwd=w, keep_as=(campaign, name))
        finally:
            shutil.rmtree(w, ignore_errors=True)

    def replay(j, f, kind, tag):
        row, t, names, levels, _ = traced[j]
        np = row["np"]
        s = t.sends[f.send]
        # the thresholds at which the alternative is realisable, and the size of its message: a
        # cross alternative needs the sender of the next level's request to have completed the
        # Waitall of its own replies, one of which goes to the probing rank, which has not yet
        # posted its receive, so that reply must be buffered (B at least its size)
        res = {**row, "pin": f.pin, "kind": kind, "tag": tag, "site": names.get(f.site) or f.site, "B": f.B,
               "bytes": s.count * s.tsize}
        hp = horizon_pins(t, f)
        if hp is None:
            return {**res, "replay": "no horizon"}
        alt, ctl = hp
        name = f"np{np}-levels{levels}-run{row['run']:02d}-{pin_name(f.pin)}"
        ra, rc = run(np, levels, alt, name + "-replay"), run(np, levels, ctl, name + "-control")
        res.update(replay=ra.result, matched=all_matched(alt, ra.proc.stderr, required_matches(t, f), ra.seen),
                   residual=residual(ra.proc.stdout), run_valid=ra.valid, violation=ra.why[:300],
                   control=rc.result, control_matched=all_matched(ctl, rc.proc.stderr, None, rc.seen),
                   control_residual=residual(rc.proc.stdout), control_valid=rc.valid, control_violation=rc.why[:300])
        return res

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for n, res in enumerate(ex.map(lambda x: replay(*x), todo), 1):
            rows.append(res)
            print({k: res.get(k) for k in ("np", "levels", "run", "pin", "kind", "tag", "replay", "matched", "residual",
                                           "run_valid", "control", "control_matched", "control_residual", "control_valid")},
                  flush=True)
            if n % 20 == 0:
                write(args.out, rows)
    replayed = {j for j, _, _, _ in todo}
    rows += [row for j, (row, _, _, _, _) in enumerate(traced) if j not in replayed]
    rows.sort(key=lambda r: (int(r["levels"]), int(r["np"]), int(r["run"])))
    write(args.out, rows)
    print("wrote", args.out)
    return 0


def write(path, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    keys = ["version", "driver", "np", "levels", "run", "records", "cross", "same", "other", "traced_residual",
            "pin", "kind", "tag", "site", "B", "bytes", "replay", "matched", "residual", "run_valid", "violation",
            "control", "control_matched", "control_residual", "control_valid", "control_violation", "seed", "commit"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
