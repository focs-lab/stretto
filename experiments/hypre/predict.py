#!/usr/bin/env python3
"""How often does one trace expose HYPRE 2.10.1's ParaSails race, and does each exposed
alternative hang when replayed? For each rank count, RUNS traced runs of test/ij -solver 8
-pout 0 are analysed; every probe alternative of the request exchange (tag 222) whose reported
message differs in size (the other exchange's request, see run.sh) is replayed with the pins
of its whole horizon (stretto.confirm.horizon_pins: every wildcard receive and probe before it
pinned to what it took in the run, the changed probe to the alternative) and with its control.

usage: predict.py <hypre test dir> [--runs N] [--np 4 8] [--timeout S]
Writes experiments/results/hypre/predict.csv: np, run, records, cross-exchange alternatives,
pin of the changed probe, replay result, every pin matched, control result, commit.
"""
from __future__ import annotations
import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from stretto import trace  # noqa: E402
from stretto.confirm import all_matched, classify_rc, horizon_pins, run_pinned  # noqa: E402
from stretto.report import analyze  # noqa: E402
from provenance import code_commit  # noqa: E402

ARGS = ["-solver", "8", "-pout", "0"]
TRACER = os.path.join(ROOT, "tracer", "bin", "mpich", "libstretto-trace.so")


def cross_exchange(t, rep):
    """Probe alternatives on tag 222 whose message differs in size from the observed one."""
    for f in rep.findings:
        if f.probe is None or f.send is None:
            continue
        p, s = t.probes[f.probe], t.sends[f.send]
        if p.tag in (222, -1) and s.tag == 222 and s.bytes != t.sends[p.seen].bytes:
            yield f


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("testdir")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--np", type=int, nargs="+", default=[4, 8])
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--out", default=os.path.join(ROOT, "experiments", "results", "hypre", "predict.csv"))
    args = ap.parse_args()
    exe = os.path.join(args.testdir, "ij")
    rows = []
    for np in args.np:
        for i in range(1, args.runs + 1):
            d = tempfile.mkdtemp(prefix=f"hypre-{np}-{i}-")
            cmd = ["/usr/bin/timeout", "300", "/usr/bin/mpirun.openmpi", "--oversubscribe", "-np", str(np),
                   "-x", f"LD_PRELOAD={TRACER}", "-x", f"STRETTO_TRACE_DIR={d}", exe, *ARGS]
            r = subprocess.run(cmd, cwd=args.testdir, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            row = {"np": np, "run": i, "commit": code_commit()}
            if r.returncode != 0:
                rows.append({**row, "replay": f"traced run rc={r.returncode}"})
                shutil.rmtree(d, ignore_errors=True)
                continue
            t = trace.load(d)
            rep = analyze(t, with_sp=False)
            found = list(cross_exchange(t, rep))
            row.update(records=sum(len(v) for v in t.ranks.values()), alternatives=len(found))
            if not found:
                rows.append(row)
            for f in found:
                hp = horizon_pins(t, f)
                if hp is None:
                    rows.append({**row, "pin": f.pin, "replay": "no horizon"})
                    continue
                alt, ctl = hp
                ra = run_pinned(np, exe, [], alt, args.timeout, mpi="mpich")
                rc = run_pinned(np, exe, [], ctl, args.timeout, mpi="mpich")
                rows.append({**row, "pin": f.pin, "replay": classify_rc(ra.returncode, ra.stderr, ra.elapsed, args.timeout),
                             "matched": all_matched(alt, ra.stderr),
                             "control": classify_rc(rc.returncode, rc.stderr, rc.elapsed, args.timeout),
                             "control_matched": all_matched(ctl, rc.stderr)})
                print(rows[-1], flush=True)
            shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    keys = ["np", "run", "records", "alternatives", "pin", "replay", "matched", "control", "control_matched", "commit"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
