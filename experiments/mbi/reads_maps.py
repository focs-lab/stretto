#!/usr/bin/env python3
"""The first-read map (stretto.firstread, docs/firstread.md) of every compiled program of a
corpus table, from its source, into DIR/<name>.json, and a summary of the maps. Needs clang 18
and the Open MPI headers: run it in the image of experiments/container/Dockerfile.firstread.
The maps are inputs of `reanalyze.py --reads DIR` and `run_mbi.py --reads DIR`, which need no
clang.   usage: reads_maps.py <corpus>.csv [--corpus mbi|mbb] [--out DIR]"""
import argparse
import collections
import csv
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from stretto.firstread.__main__ import main as firstread  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_mbi import CORPORA  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("csv")
ap.add_argument("--corpus", default="mbi", choices=sorted(CORPORA))
ap.add_argument("--out", default=None, help="default: experiments/results/reads/<corpus>")
args = ap.parse_args()
out = args.out or os.path.join(ROOT, "experiments", "results", "reads", args.corpus)
os.makedirs(out, exist_ok=True)
rows = [r for r in csv.DictReader(open(args.csv)) if r.get("build") != "CE"]
failed, entries, why = [], collections.Counter(), collections.Counter()
for r in rows:
    src = os.path.join(CORPORA[args.corpus], r["name"].replace("__", "/") + ".c")
    dst = os.path.join(out, r["name"] + ".json")
    if firstread(["-o", dst, "--mpi", "openmpi", src]) != 0:
        failed.append(r["name"])
        continue
    with open(dst) as fh:
        m = json.load(fh)
    for e in m["results"]:
        entries["with free sites" if e["free"] else "without"] += 1
        if "why" in e:
            why[e["why"]] += 1
print(f"{len(rows) - len(failed)} maps in {out}; failed: {failed or 'none'}")
print("entries: " + ", ".join(f"{k} {v}" for k, v in sorted(entries.items())))
for k, v in why.most_common():
    print(f"  no free site, {v}: {k}")
