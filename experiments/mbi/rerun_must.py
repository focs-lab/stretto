#!/usr/bin/env python3
"""Rerun MUST for some rows of a results CSV and merge the verdicts into it.

usage: rerun_must.py [--timeout S] results.csv [name ...]   (options first: Python 3.6 argparse)
Without names: every row whose MUST verdict is TIMEOUT, RE or missing. With names: exactly
those rows (for example runs that were interrupted by pausing their containers)."""
import argparse, csv, os, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CORPORA = {"mbi": os.path.join(ROOT, "study", "apps", "MBI", "gencodes"), "mbb": os.path.join(ROOT, "third_party", "mpi-bugbench", "scripts", "gencodes")}
ap = argparse.ArgumentParser(); ap.add_argument("csv"); ap.add_argument("--timeout", default="120"); ap.add_argument("names", nargs="*")
args = ap.parse_args()
corpus = os.path.basename(args.csv).split(".")[0]
rows = list(csv.DictReader(open(args.csv)))
if args.names:
    todo = [r for r in rows if r["name"] in set(args.names)]
else:
    todo = [r for r in rows if r.get("must", "").startswith(("MUST:TIMEOUT", "MUST:RE")) or not r.get("must")]
def one(r):
    src = os.path.join(CORPORA[corpus], r["name"].replace("__", "/") + ".c")
    p = subprocess.run([os.path.join(ROOT, "scripts", "must_run.sh"), str(r["np"]), src, args.timeout],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)  # Python 3.6 compatible
    return r, (p.stdout.strip().splitlines() or ["MUST:RE"])[-1]
with ThreadPoolExecutor(max_workers=1) as ex:
    for i, f in enumerate(as_completed([ex.submit(one, r) for r in todo]), 1):
        r, v = f.result(); r["must"] = v; print(f"[{i}/{len(todo)}] {r['name']:<50} {v[:60]}", flush=True)
with open(args.csv, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n"); w.writeheader(); w.writerows(rows)
print("updated", args.csv)
