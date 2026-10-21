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
    return r["name"], (p.stdout.strip().splitlines() or ["MUST:RE"])[-1]
out = os.path.join(os.path.dirname(args.csv), col + ".csv")
if not args.merge_only:
    # the code commit of scripts/must_run.sh, as experiments/provenance.py finds it (Python 3.6 here)
    commit = os.environ.get("STRETTO_COMMIT") or (open(os.path.join(ROOT, "COMMIT")).read().strip() if os.path.exists(os.path.join(ROOT, "COMMIT")) else "unknown")
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
