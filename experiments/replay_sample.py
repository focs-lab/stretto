#!/usr/bin/env python3
"""Replay a random sample of an application's alternatives, each with the pins of its whole
horizon (stretto.confirm.horizon_pins) and with its control, and record what each run did.

On correct, wildcard-heavy code the analysis reports alternatives that are warnings (another
sender at a racing receive, another message seen by a probe); whether they are harmless is a
claim about the program's continuation, which the trace does not determine. A sample of
replays measures it: an alternative whose replay finishes while its control finishes is
realisable and, in that run, harmless; one that hangs or fails while its control finishes is a
consequence the analysis could not see.

usage: replay_sample.py --out CSV [--sample N] [--seed K] [--timeout S] [--mpi openmpi|mpich]
       <trace dir> <np> <exe> [args ...]
The options come first: everything after <exe> is the program's own command line, which may
start with a dash, and the replays run it from the current directory, as the traced run was.
"""
from __future__ import annotations
import argparse
import csv
import os
import random
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stretto import trace  # noqa: E402
from stretto.confirm import all_matched, classify_rc, horizon_pins, required_matches, run_pinned  # noqa: E402
from stretto.report import analyze  # noqa: E402
from provenance import code_commit  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tracedir")
    ap.add_argument("np", type=int)
    ap.add_argument("exe")
    ap.add_argument("args", nargs=argparse.REMAINDER)
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--mpi", default="openmpi")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    t = trace.load(args.tracedir)
    rep = analyze(t, with_sp=False)
    pool = [f for f in rep.findings if f.level == "L1" and f.send is not None and (f.probe is not None or f.recv is not None)]
    rng = random.Random(args.seed)
    sample = rng.sample(pool, min(args.sample, len(pool)))
    rows = []
    for k, f in enumerate(sample, 1):
        hp = horizon_pins(t, f)
        row = {"finding": f.title[:160], "cls": f.cls, "pin": f.pin, "site": f.site, "commit": code_commit()}
        if hp is None:
            rows.append({**row, "replay": "no horizon"})
            continue
        alt, ctl = hp
        env = {"OMP_NUM_THREADS": "1"}           # one thread per rank, as in the traced run
        ra = run_pinned(args.np, args.exe, args.args, alt, args.timeout, mpi=args.mpi, extra_env=env)
        rc = run_pinned(args.np, args.exe, args.args, ctl, args.timeout, mpi=args.mpi, extra_env=env)
        rows.append({**row, "pins": sum(x.count(",") + 1 for x in alt),
                     "replay": classify_rc(ra.returncode, ra.stderr, ra.elapsed, args.timeout), "matched": all_matched(alt, ra.stderr, required_matches(t, f)),
                     "control": classify_rc(rc.returncode, rc.stderr, rc.elapsed, args.timeout), "control_matched": all_matched(ctl, rc.stderr)})
        print(f"[{k}/{len(sample)}] {rows[-1]['replay']} matched={rows[-1]['matched']} control={rows[-1]['control']} {f.title[:90]}", flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    keys = ["finding", "cls", "pin", "site", "pins", "replay", "matched", "control", "control_matched", "commit"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"alternatives {len(pool)}, sampled {len(sample)}; wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
