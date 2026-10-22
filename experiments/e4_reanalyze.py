#!/usr/bin/env python3
"""Refresh the analysis_s / findings columns of experiments/results/e4/e4.csv with the current
analysis code (the traced runs themselves are not repeated). The commit column keeps naming
the code that traced the runs; analysis_commit names the analysis that gave the new columns.
An analysis that fails (exit status 2 or 3, or none) leaves CRASH in the classes column and no
time, and the script exits with status 1."""
import csv, os, subprocess, sys, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from provenance import code_commit  # noqa: E402
OUT = os.path.join(ROOT, "experiments", "results", "e4")
with open(os.path.join(OUT, "e4.csv")) as fh:
    rows = list(csv.DictReader(fh))
keys = list(rows[0].keys()) + ([] if "analysis_commit" in rows[0] else ["analysis_commit"])
failed = []
for r in rows:
    td = os.path.join(OUT, f"trace.{r['benchmark']}.{r['class']}.{r['np']}")
    if not os.path.isdir(td):
        continue
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable, "-m", "stretto", "analyze", "--no-sp", td], capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": os.path.join(ROOT, "analysis")})
    r["analysis_commit"] = code_commit() if code_commit() != r.get("commit") else ""
    if p.returncode not in (0, 1):            # 0: no error class, 1: one found; else the analysis failed
        r["analysis_s"], r["findings"], r["classes"] = "", "", "CRASH"
        failed.append(f"{r['benchmark']}.{r['class']}.{r['np']} (status {p.returncode})")
        print(r["benchmark"], r["class"], r["np"], "analysis failed with status", p.returncode, flush=True)
        continue
    r["analysis_s"] = f"{time.perf_counter() - t0:.2f}"
    lines = [l for l in p.stdout.splitlines() if l.startswith("[")]
    r["findings"] = str(len(lines)); r["classes"] = " ".join(sorted({l.split("]")[0].strip("[ ") for l in lines}))
    print(r["benchmark"], r["class"], r["np"], r["analysis_s"], "s", r["findings"], flush=True)
with open(os.path.join(OUT, "e4.csv"), "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=keys, restval="", lineterminator="\n"); w.writeheader(); w.writerows(rows)
if failed:
    sys.exit("the analysis failed on " + ", ".join(failed))
