#!/usr/bin/env python3
"""Refresh the analysis_s / findings columns of experiments/results/e4/e4.csv with the current
analysis code (the traced runs themselves are not repeated)."""
import csv, os, subprocess, sys, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "experiments", "results", "e4")
rows = list(csv.DictReader(open(os.path.join(OUT, "e4.csv"))))
for r in rows:
    td = os.path.join(OUT, f"trace.{r['benchmark']}.{r['class']}.{r['np']}")
    if not os.path.isdir(td):
        continue
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, "-m", "rematch", "analyze", "--no-sp", td], capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "analysis")})
    r["analysis_s"] = f"{time.perf_counter() - t0:.2f}"
    lines = [l for l in p.stdout.splitlines() if l.startswith("[")]
    r["findings"] = str(len(lines)); r["classes"] = " ".join(sorted({l.split("]")[0].strip("[ ") for l in lines}))
    print(r["benchmark"], r["class"], r["np"], r["analysis_s"], "s", r["findings"], flush=True)
with open(os.path.join(OUT, "e4.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), lineterminator="\n"); w.writeheader(); w.writerows(rows)
