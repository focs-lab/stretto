#!/usr/bin/env python3
"""How often does one trace expose HYPRE 2.10.1's ParaSails race, and does each exposed
alternative hang when replayed? For each rank count, RUNS traced runs of test/ij -solver 8
-pout 0 are analysed; every probe alternative of the request exchanges (tag 222) is classified
as a request of another exchange ("cross", the race) or another neighbour's request of the same
exchange ("same", a harmless reordering), and replayed with the pins of its whole horizon
(stretto.confirm.horizon_pins: every wildcard receive and probe before it pinned to what it
took in the run, the changed probe to the alternative) and with its control.

usage: predict.py <hypre test dir> [--runs N] [--np 4 8] [--timeout S] [--jobs J]
Writes experiments/results/hypre/predict.csv: np, run, records, cross-exchange and same-exchange
alternatives of the run, pin, kind and call site of the changed probe, replay result, every
required pin matched, its final residual, control result, control matched, control residual,
commit.
"""
from __future__ import annotations
import argparse
import bisect
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

ARGS = ["-solver", "8", "-pout", "0"]
RESIDUAL = re.compile(r"Final Relative Residual Norm = (\S+)")


def residual(out: str) -> str:
    """The solver's final relative residual as printed, "" if the run printed none: a replay
    that finishes with another residual than its control took a different numerical path."""
    m = RESIDUAL.search(out)
    return m.group(1) if m else ""
TRACER = os.path.join(ROOT, "tracer", "bin", "mpich", "libstretto-trace.so")


def request_alternatives(t, rep):
    """Probe alternatives on tag 222, each with its kind. Every exchange (ExchangePrunedRows,
    ExchangeStoredRows) posts its requests, calls the Allreduce of FindNumReplies, and only then
    probes for requests, so a request sent after s collectives on the communicator belongs to
    the exchange whose Allreduce is collective s+1, and a probe after p collectives to the
    exchange of collective p. The alternative's request is of another exchange ("cross") unless
    s+1 == p ("same"). A size test does not separate them: at eight ranks requests of one
    exchange differ in size and requests of two exchanges can have the same size."""
    before = {}
    for c in t.colls:
        before.setdefault((c.rank, c.comm), []).append(c.idx)
    for v in before.values():
        v.sort()

    def phase(rank, comm, idx):
        return bisect.bisect_left(before.get((rank, comm), []), idx)

    for f in rep.findings:
        if f.probe is None or f.send is None:
            continue
        p, s = t.probes[f.probe], t.sends[f.send]
        if p.tag in (222, -1) and s.tag == 222:
            same = phase(s.rank, s.comm, s.idx) + 1 == phase(p.rank, p.comm, p.idx)
            yield f, "same" if same else "cross"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("testdir")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--np", type=int, nargs="+", default=[4, 8])
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=1, help="replays run at once")
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
            found = list(request_alternatives(t, rep))
            row.update(records=sum(len(v) for v in t.ranks.values()),
                       alternatives=sum(1 for _, k in found if k == "cross"),
                       same_alternatives=sum(1 for _, k in found if k == "same"))
            if not found:
                rows.append(row)
            def replay(f, kind):
                hp = horizon_pins(t, f)
                if hp is None:
                    return {**row, "pin": f.pin, "kind": kind, "site": f.site, "replay": "no horizon"}
                alt, ctl = hp
                ra = run_pinned(np, exe, ARGS, alt, args.timeout, mpi="mpich", cwd=args.testdir)
                rc = run_pinned(np, exe, ARGS, ctl, args.timeout, mpi="mpich", cwd=args.testdir)
                return {**row, "pin": f.pin, "kind": kind, "site": f.site, "replay": classify_rc(ra.returncode, ra.stderr, ra.elapsed, args.timeout),
                        "matched": all_matched(alt, ra.stderr, required_matches(t, f)), "residual": residual(ra.stdout),
                        "control": classify_rc(rc.returncode, rc.stderr, rc.elapsed, args.timeout),
                        "control_matched": all_matched(ctl, rc.stderr), "control_residual": residual(rc.stdout)}

            # replays of one trace are independent runs; a hanging one waits out its timeout
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
                for res in pool.map(lambda fk: replay(*fk), found):
                    rows.append(res)
                    print(res, flush=True)
            shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    keys = ["np", "run", "records", "alternatives", "same_alternatives", "pin", "kind", "site", "replay", "matched", "residual",
            "control", "control_matched", "control_residual", "commit"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
