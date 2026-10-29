#!/usr/bin/env python3
"""Compact tables of the latent-bug campaigns (experiments/latent/*/run.sh summary): the counts of
the per-run tables of experiments/history_predict.py and experiments/history_stress.py, one row
per version and process count (predict) or per version, label, process count and setting
(stress). The per-run tables stay outside the repository (experiments/results/latent/<case>/).

predict rows: traced runs; clean ones (finished, correct output); clean ones with at least one
alternative of KIND (experiments/latent/<case>/classify.py or classify_nbx.py); those
alternatives; the ones replayed with their horizon pins; replays whose output was wrong or that
did not finish; replays whose pins all matched; replays whose run was valid (the pins kept the
recorded order everywhere else); controls (the same pins without the alternative) that finished
with correct output; and confirmed, the replays that were wrong, matched, valid and whose
control was correct.

stress rows: runs, and failed runs (not finished, or wrong output).

Every row carries the upstream source it ran (--source) and the Stretto commit of the per-run
table (its commit column).

usage: summarize.py predict --case C --source S --kind K --out CSV PREDICT.csv ...
       summarize.py stress --case C --source S --out CSV STRESS.csv ...
Appends to CSV, with its header when the file is new."""
import argparse
import csv
import os
import sys

PREDICT = ["case", "version", "np", "kind", "traced", "clean", "predicting", "alternatives", "replayed",
           "wrong", "matched", "valid", "controls_ok", "confirmed", "source", "commit"]
STRESS = ["case", "version", "label", "np", "setting", "runs", "failed", "source", "commit"]


def kinds(s):
    out = {}
    for kv in (s or "").split(";"):
        if "=" in kv:
            k, n = kv.split("=")
            out[k] = out.get(k, 0) + int(n)
    return out


def predict(a, path):
    rows = list(csv.DictReader(open(path, newline="")))
    out = []
    for key in sorted({(r["version"], int(r["np"])) for r in rows}):
        v = [r for r in rows if (r["version"], int(r["np"])) == key]
        runs = {r["run"]: r for r in v}
        clean = [r for r in runs.values() if r["traced"] == "FINISHED" and r["traced_ok"] == "True"]
        alt = [kinds(r["kinds"]).get(a.kind, 0) for r in clean]
        rep = [r for r in v if r["role"] == "replay" and r["kind"] == a.kind]
        wrong = [r for r in rep if not (r["replay"] == "FINISHED" and r["replay_ok"] == "True")]
        cok = [r for r in rep if r["control"] == "FINISHED" and r["control_ok"] == "True"]
        conf = [r for r in wrong if r["matched"] == "True" and r["run_valid"] == "True"
                and r["control"] == "FINISHED" and r["control_ok"] == "True"]
        out.append({"case": a.case, "version": key[0], "np": key[1], "kind": a.kind, "traced": len(runs),
                    "clean": len(clean), "predicting": sum(1 for n in alt if n), "alternatives": sum(alt),
                    "replayed": len(rep), "wrong": len(wrong), "matched": sum(r["matched"] == "True" for r in rep),
                    "valid": sum(r["run_valid"] == "True" for r in rep), "controls_ok": len(cok),
                    "confirmed": len(conf), "source": a.source,
                    "commit": "/".join(sorted({r["commit"] for r in v}))})
    return out


def stress(a, path):
    rows = list(csv.DictReader(open(path, newline="")))
    out = []
    for key in sorted({(r["version"], r["label"], int(r["np"]), r["setting"]) for r in rows}):
        v = [r for r in rows if (r["version"], r["label"], int(r["np"]), r["setting"]) == key]
        out.append({"case": a.case, "version": key[0], "label": key[1], "np": key[2], "setting": key[3],
                    "runs": len(v), "failed": sum(1 for r in v if not (r["result"] == "FINISHED" and r["ok"] == "True")),
                    "source": a.source, "commit": "/".join(sorted({r["commit"] for r in v}))})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["predict", "stress"])
    ap.add_argument("--case", required=True)
    ap.add_argument("--source", required=True, help="the upstream code the rows ran, as a short commit")
    ap.add_argument("--kind", default="cross", help="predict: the kind of alternative counted")
    ap.add_argument("--out", required=True)
    ap.add_argument("tables", nargs="+")
    a = ap.parse_args()
    keys = PREDICT if a.what == "predict" else STRESS
    rows = [r for p in a.tables for r in (predict if a.what == "predict" else stress)(a, p)]
    new = not os.path.exists(a.out)
    if not new:
        with open(a.out, newline="") as fh:
            if next(csv.reader(fh), []) != keys:
                sys.exit(f"summarize.py: {a.out} has other columns; remove it")
    with open(a.out, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
