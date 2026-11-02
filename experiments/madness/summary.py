"""Findings of the scan by site and class: from the tables of scan_csv.py (PREFIX-runs.csv and
PREFIX-sites.csv, one prefix per MPI), one row per (location, class) with the traces that have
it, the benchmarks, and the count per trace; and the observed results that are not a pass.
   python3 -I summary.py PREFIX [PREFIX ...]"""
import collections
import csv
import sys

per = collections.defaultdict(lambda: {"traces": set(), "tests": set(), "counts": []})
runs = {}
for prefix in sys.argv[1:]:
    for r in csv.DictReader(open(prefix + "-runs.csv")):
        runs[(r["test"], r["mpi"], r["np"], r["run"])] = r
    for s in csv.DictReader(open(prefix + "-sites.csv")):
        for kv in s["counts"].split():
            cls, n = kv.rsplit("=", 1)
            e = per[(s["location"] or s["site"], cls)]
            e["traces"].add((s["test"], s["mpi"], s["np"], s["run"]))
            e["tests"].add(f'{s["test"]} np{s["np"]} ({s["mpi"]})')
            e["counts"].append(int(n))
analysed = sum(1 for r in runs.values() if r.get("findings", "") != "")
print(f"{len(runs)} runs, {analysed} analysed")
for (loc, cls), e in sorted(per.items(), key=lambda kv: (-len(kv[1]["traces"]), kv[0])):
    c = e["counts"]
    print(f"{cls:24s} {len(e['traces']):4d} traces, {min(c)}-{max(c)} per trace  {loc}")
    print("      " + ", ".join(sorted(e["tests"])))
print("results other than pass:")
for k, r in sorted(runs.items()):
    if r["result"] != "pass":
        print("  ", *k, r["result"], r["rc"], r.get("status", ""))
