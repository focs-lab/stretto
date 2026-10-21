#!/usr/bin/env python3
"""How often does one trace expose HYPRE 2.10.1's ParaSails race, and does each exposed
alternative hang when replayed? For each rank count, RUNS traced runs of test/ij -solver 8
-pout 0 are analysed; every probe alternative of the request exchanges (tag 222) is classified
as a request of another exchange ("cross", the race) or another neighbour's request of the same
exchange ("same", a harmless reordering), and replayed with the pins of its whole horizon
(stretto.confirm.horizon_pins: every wildcard receive and probe before it pinned to what it
took in the run, the changed probe to the alternative) and with its control. On 2.11.0, the
fix, the two exchanges send their requests with tags 221 and 222 (--tags 221 222).

Replays and controls run under the tracer when it is built (stretto.confirm.run_checked), and
each row says whether the run is one of the program without its pins (run_valid,
control_valid) and, if not, why. --sample N replays a random sample of N alternatives of the
kinds --kinds, drawn with --seed from those of every traced run, instead of all of them.

usage: predict.py <hypre test dir> [--runs N] [--np 4 8] [--timeout S] [--jobs J] [--tags T..]
                  [--sample N] [--seed K] [--kinds cross same] [--out CSV]
Writes experiments/results/hypre/predict.csv: np, run, records, cross-exchange and same-exchange
alternatives of the run, pin, kind and call site of the changed probe, replay result, every
required pin matched, its final residual, whether it is a run of the program, control result,
control matched, control residual, whether the control is one, the seed of the sample, commit.
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
from stretto import trace  # noqa: E402
from stretto.confirm import all_matched, horizon_pins, required_matches, run_checked  # noqa: E402
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


def request_alternatives(t, rep, tags=(222,)):
    """Probe alternatives on the request tags, each with its kind. Every exchange (ExchangePrunedRows,
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
        if s.tag in tags and p.tag in (s.tag, -1):
            same = phase(s.rank, s.comm, s.idx) + 1 == phase(p.rank, p.comm, p.idx)
            yield f, "same" if same else "cross"


def keep_output(outdir, np, run, pin, role, r):
    """A replay or control that neither finished nor timed out ended for a reason the table
    cannot show (some 8-rank runs exited with status 15, replays and controls alike); its
    output goes to failed/ next to the table, which is not committed."""
    os.makedirs(outdir, exist_ok=True)
    name = f"{np}-{run}-{pin.split('=', 1)[-1].replace(':', '-')}-{role}.txt"
    with open(os.path.join(outdir, name), "w") as fh:
        fh.write(f"rc={r.returncode} elapsed={r.elapsed:.1f}\n--- stdout\n{r.stdout}\n--- stderr\n{r.stderr}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("testdir")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--np", type=int, nargs="+", default=[4, 8])
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=1, help="replays run at once")
    ap.add_argument("--tags", type=int, nargs="+", default=[222], help="tags of the row requests")
    ap.add_argument("--sample", type=int, default=0, help="replay a random sample of this many alternatives of every traced run (0: all)")
    ap.add_argument("--seed", type=int, default=20261022)
    ap.add_argument("--kinds", nargs="+", default=["cross", "same"], help="the kinds of alternative to replay")
    ap.add_argument("--out", default=os.path.join(ROOT, "experiments", "results", "hypre", "predict.csv"))
    args = ap.parse_args()
    failed = os.path.join(os.path.dirname(os.path.abspath(args.out)), "failed")
    exe = os.path.join(args.testdir, "ij")
    rows, traced = [], []
    for np in args.np:
        for i in range(1, args.runs + 1):
            d = tempfile.mkdtemp(prefix=f"hypre-{np}-{i}-")
            cmd = ["/usr/bin/timeout", "300", "/usr/bin/mpirun.openmpi", "--oversubscribe", "-np", str(np),
                   "-x", f"LD_PRELOAD={TRACER}", "-x", f"STRETTO_TRACE_DIR={d}", exe, *ARGS]
            r = subprocess.run(cmd, cwd=args.testdir, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            row = {"np": np, "run": i, "seed": args.seed if args.sample else "", "commit": code_commit()}
            if r.returncode != 0:
                rows.append({**row, "replay": f"traced run rc={r.returncode}"})
                shutil.rmtree(d, ignore_errors=True)
                continue
            t = trace.load(d)
            shutil.rmtree(d, ignore_errors=True)
            rep = analyze(t, with_sp=False)
            found = list(request_alternatives(t, rep, tuple(args.tags)))
            row.update(records=sum(len(v) for v in t.ranks.values()),
                       alternatives=sum(1 for _, k in found if k == "cross"),
                       same_alternatives=sum(1 for _, k in found if k == "same"))
            traced.append((row, t, [(f, k) for f, k in found if k in args.kinds]))
            print(f"np={np} run={i}: {row['alternatives']} cross, {row['same_alternatives']} same", flush=True)
    # the alternatives to replay: all, or a sample drawn from those of every traced run
    pool = [(j, f, k) for j, (_, _, found) in enumerate(traced) for f, k in found]
    chosen = sorted(random.Random(args.seed).sample(range(len(pool)), min(args.sample, len(pool)))) if args.sample else range(len(pool))
    todo = [pool[c] for c in chosen]
    print(f"replaying {len(todo)} of {len(pool)} alternatives", flush=True)

    def replay(j, f, kind):
        row, t, _ = traced[j]
        np, i = row["np"], row["run"]
        hp = horizon_pins(t, f)
        if hp is None:
            return {**row, "pin": f.pin, "kind": kind, "site": f.site, "replay": "no horizon"}
        alt, ctl = hp
        ra = run_checked(np, exe, ARGS, alt, args.timeout, mpi="mpich", cwd=args.testdir)
        rc = run_checked(np, exe, ARGS, ctl, args.timeout, mpi="mpich", cwd=args.testdir)
        res = {**row, "pin": f.pin, "kind": kind, "site": f.site, "replay": ra.result,
               "matched": all_matched(alt, ra.proc.stderr, required_matches(t, f), ra.seen), "residual": residual(ra.proc.stdout),
               "run_valid": ra.valid, "violation": ra.why[:300],
               "control": rc.result, "control_matched": all_matched(ctl, rc.proc.stderr, None, rc.seen),
               "control_residual": residual(rc.proc.stdout),
               "control_valid": rc.valid, "control_violation": rc.why[:300]}
        for role, r in (("replay", ra.proc), ("control", rc.proc)):
            if res[role] not in ("FINISHED", "TIMEOUT"):
                keep_output(failed, np, i, f.pin, role, r)
        return res

    # the replays are independent runs; a hanging one waits out its timeout
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for res in ex.map(lambda x: replay(*x), todo):
            rows.append(res)
            print(res, flush=True)
    # a traced run none of whose alternatives was replayed keeps a row of its own
    replayed = {j for j, _, _ in todo}
    rows += [row for j, (row, _, _) in enumerate(traced) if j not in replayed]
    rows.sort(key=lambda r: (int(r["np"]), int(r["run"])))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    keys = ["np", "run", "records", "alternatives", "same_alternatives", "pin", "kind", "site", "replay", "matched", "residual",
            "run_valid", "violation", "control", "control_matched", "control_residual", "control_valid", "control_violation",
            "seed", "commit"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
