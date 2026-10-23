#!/usr/bin/env python3
"""MPI-PRV on every program of Stretto's MBI table, inside MPI-PRV's image (experiments/mpiprv/prv.sh).

run.py runs MPI-PRV on the MBI families its ten properties describe. This runs the same
instrumentation, MOP file, patterns and arms (run.py: a, the properties on MPI calls; b, the
buffer properties with markers.blind; c, with markers.oracle, an upper bound) on each program
of experiments/results/mbi/mbi.csv, the message-race, parameter-matching, call-ordering and
buffering programs of Stretto's own table, whatever their scope. Per program and arm, MPI-PRV
either reports a pattern (outcome "flagged"), reports none ("not flagged"), or cannot process
the program ("unsupported", with the reason: wac fails to instrument or build it, the
instrumented run leaves no trace, or the analyser fails on the trace). How the instrumented run
itself ended (rc=N or TIMEOUT) is recorded apart: a run that hangs is not a report of MPI-PRV's.
A trace of a run that hung is analysed like any other.

usage: mbi.py [--jobs J] [--select REGEX] [--timeout S]   (writes experiments/results/mpiprv/mbi-all.csv)
"""
from __future__ import annotations
import argparse
import csv
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run as prv  # noqa: E402

TABLE = os.path.join(prv.ROOT, "experiments", "results", "mbi", "mbi.csv")
OUT = os.path.join(prv.ROOT, "experiments", "results", "mpiprv", "mbi-all.csv")
FIELDS = ["program", "expect", "detail", "np", "scope", "outside_model", "arm", "markers", "build", "run", "events",
          "verdict", "outcome", "reason", "patterns", "matched", "work", "commit"]


def programs():
    res = []
    with open(TABLE) as fh:
        for r in csv.DictReader(fh):
            path = os.path.join(prv.MBI, r["name"] + ".c")
            res.append({"corpus": "mbi", "program": r["name"], "path": path, "expect": r["expect"],
                        "detail": r["detail"], "np": int(r["np"] or 2), "built": True})
    return res


def last_line(path):
    try:
        lines = [ln.strip() for ln in open(path, errors="replace") if ln.strip()]
    except OSError:
        return ""
    return lines[-1][:160] if lines else ""


def outcome(row):
    """flagged / not flagged / unsupported, and the reason for unsupported."""
    d = os.path.join(prv.WORK, row["work"]) if row.get("work") else ""
    if row.get("build") != "ok":
        return "unsupported", "wac did not build the instrumented program: " + last_line(os.path.join(d, "wac.log"))
    if row["verdict"] == "ERROR":
        return "flagged", ""
    if row["verdict"] == "HARNESS-ERROR":
        bad = [p.split("=", 1) for p in row["patterns"].split()]
        if any(v == "no-trace" for _, v in bad):
            return "unsupported", f"the instrumented run ({row['run']}) left no trace"
        errs = [n for n, v in bad if v.startswith("error")]
        return "unsupported", "the analyser failed on the trace (" + ", ".join(
            f"{n}: {last_line(os.path.join(d, n + '.log'))}" for n in errs) + ")"
    return "not flagged", ""


def one(p, args):
    rows = prv.one(p, args)
    for r in rows:
        r["outcome"], r["reason"] = outcome(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("JOBS") or 2))
    ap.add_argument("--select", default="")
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("TIMEOUT") or 60))
    args = ap.parse_args()
    os.makedirs(prv.WORK, exist_ok=True)
    progs = [p for p in programs() if re.search(args.select, p["program"])]
    missing = [p["path"] for p in progs if not os.path.exists(p["path"])]
    if missing:
        sys.exit(f"{len(missing)} program(s) of {TABLE} missing, e.g. {missing[0]} (third_party/fetch.sh corpora)")
    rows = []
    with ThreadPoolExecutor(args.jobs) as ex:
        for i, rs in enumerate(ex.map(lambda p: one(p, args), progs)):
            rows += rs
            print(f"[{i + 1}/{len(progs)}] {rs[0]['program']}: " + " ".join(f"{r['arm']}={r['outcome']}" for r in rs),
                  flush=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
