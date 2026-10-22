#!/usr/bin/env python3
"""Run MUST (via scripts/must_run.sh, containerised) on the programs listed in a harness
CSV, write name,must verdict to must.csv next to it and merge the column into the CSV.
With --merge-only, only merge an existing must.csv (the MUST runs and the harness run are
independent and may finish in either order). --column NAME writes NAME.csv and merges the
column NAME instead, for another MUST version or configuration, chosen by the environment of
scripts/must_run.sh (MUST_IMAGE, MUST_PREFIX, MUST_TSAN); the default column is MUST 1.9.2's.
usage: run_must.py [--jobs J] [--timeout S] [--column NAME] [--merge-only] results.csv   (options first: Python 3.6 argparse)"""
import argparse, csv, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CORPORA = {"mbi": os.path.join(ROOT, "study", "apps", "MBI", "gencodes"),
           "mbb": os.path.join(ROOT, "third_party", "mpi-bugbench", "scripts", "gencodes")}
ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("--jobs", type=int, default=1); ap.add_argument("--timeout", type=int, default=90)
ap.add_argument("--merge-only", action="store_true"); ap.add_argument("--column", default="must")
args = ap.parse_args()
col = args.column
corpus = os.path.basename(args.csv).split(".")[0]
rows = list(csv.DictReader(open(args.csv)))
def one(r):
    src = os.path.join(CORPORA[corpus], r["name"].replace("__", "/") + ".c")
    p = subprocess.run([os.path.join(ROOT, "scripts", "must_run.sh"), str(r["np"]), src, str(args.timeout)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)  # Python 3.6 compatible
    return r["name"], (p.stdout.strip().splitlines() or ["MUST:HARNESS-ERROR:must_run.sh printed nothing"])[-1]
out = os.path.join(os.path.dirname(args.csv), col + ".csv")
if not args.merge_only:
    # the code commit of scripts/must_run.sh (experiments/provenance.py, which Python 3.6 runs too)
    sys.path.insert(0, os.path.join(ROOT, "experiments"))
    from provenance import code_commit
    commit = code_commit()
    with ThreadPoolExecutor(max_workers=args.jobs) as ex, open(out, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n"); w.writerow(["name", col, "commit"])
        for i, f in enumerate(as_completed([ex.submit(one, r) for r in rows]), 1):
            name, v = f.result(); w.writerow([name, v, commit]); fh.flush()
            print(f"[{i}/{len(rows)}] {name:<50} {v[:70]}", flush=True)
    print("wrote", out)
# merge the verdicts into the harness table's column
verdicts = {r["name"]: r[col] for r in csv.DictReader(open(out))}
for r in rows:
    r[col] = verdicts.get(r["name"], r.get(col, ""))
keys = list(rows[0].keys()) + ([] if col in rows[0] else [col])
with open(args.csv, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=keys, lineterminator="\n"); w.writeheader(); w.writerows(rows)
print("merged into", args.csv)
# a run the harness could not complete is no verdict of MUST's (score.py counts it apart): say so
bad = sorted(n for n, v in verdicts.items() if v.startswith("MUST:HARNESS-ERROR"))
if bad:
    sys.exit(f"{len(bad)} MUST run(s) failed in the harness (HARNESS-ERROR; rerun_must.py reruns them): {', '.join(bad[:10])}")
