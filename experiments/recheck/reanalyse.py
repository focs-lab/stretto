#!/usr/bin/env python3
"""Analyse every kept trace of a manifest (analysis/stretto/keep.py) with two versions of the
analysis and compare the reports: which committed verdicts a change of the analysis can move.
Each archive is unpacked into a scratch directory, analysed by `python3 -m stretto analyze --json`
with each version's analysis directory on PYTHONPATH, and deleted, so that the disk holds one
unpacked trace per job. Two reports are the same when they are equal but for the analysis commit
and for fields a later version added and left empty; a pair that differs is kept under
OUT/differ/.

  experiments/recheck/reanalyse.py (--manifest <MANIFEST.tsv> | --list <file> --root <dir>)
      --base <analysis dir> [--new <analysis dir>] --out <dir> [--jobs N] [--campaigns a,b]
      [--skip-names re] [--timeout S]

A list file names trace directories or .tar.xz archives of one, one per line, under ROOT: the
first component of a path below ROOT is its campaign, the rest its name (the work directories
of the experiments that kept their traces without a manifest).

OUT/rows.csv has one row per trace: campaign, name, same, exit statuses and seconds of both
versions, and the commit of this repository (experiments/provenance.py).

With --unaffected-only-sampled N, a trace that the fixes of November cannot change is analysed
only if it falls in a sample of one in N (by a hash of its name), and its row says "skipped"
otherwise: every change since 6fd7a06 acts on a rank with several chains or on a receive freed
while pending (trace.Trace.multi, Recv.freed), which the trace is loaded once to look for."""
import argparse
import concurrent.futures as cf
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from provenance import code_commit  # noqa: E402

FIELDS = ["campaign", "name", "affected", "same", "base_exit", "new_exit", "base_seconds", "new_seconds", "commit"]


def run(code: str, d: str, timeout: int):
    env = dict(os.environ, PYTHONPATH=code)
    t = time.time()
    try:
        p = subprocess.run([sys.executable, "-m", "stretto", "analyze", "--json", d], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode, p.stdout, p.stderr, time.time() - t
    except subprocess.TimeoutExpired:
        return "timeout", b"", b"", time.time() - t


def normal(out: bytes):
    """The report without what names the version: the analysis commit, and fields a later
    version added that are empty (null)."""
    try:
        rep = json.loads(out)
    except ValueError:
        return out

    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if v is not None and k != "analysis_commit"}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    return strip(rep)


PROBE = ("import sys; from stretto import trace; t = trace.load(sys.argv[1]); "
         "print(int(t.multi or any(r.freed for r in t.recvs.values())))")


def affected(code: str, d: str, timeout: int) -> str:
    """1 if the trace has a rank with several chains or a freed receive, 0 if not, ? if it
    could not be loaded."""
    try:
        p = subprocess.run([sys.executable, "-c", PROBE, d], env=dict(os.environ, PYTHONPATH=code),
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "?"
    out = p.stdout.decode().strip().splitlines()
    return out[-1] if p.returncode == 0 and out else "?"


def trace_dir(root: str) -> str:
    """The directory of the unpacked archive that holds the rank files."""
    for d, _, files in os.walk(root):
        if any(f.startswith("rank-") for f in files):
            return d
    return root


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest")
    ap.add_argument("--list")
    ap.add_argument("--root")
    ap.add_argument("--base", required=True)
    ap.add_argument("--new", default=os.path.join(ROOT, "analysis"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--campaigns", default="", help="only these campaigns (comma-separated prefixes)")
    ap.add_argument("--skip-names", default="", help="leave out traces whose name matches this pattern")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--unaffected-only-sampled", type=int, default=0, metavar="N")
    ap.add_argument("--scratch", default=None, help="where archives are unpacked (default: a temporary directory)")
    args = ap.parse_args()
    if args.manifest:
        base_dir = os.path.dirname(os.path.abspath(args.manifest))
        with open(args.manifest) as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
    else:
        base_dir = os.path.abspath(args.root)
        rows = []
        with open(args.list) as fh:
            for line in fh:
                rel = os.path.relpath(os.path.join(base_dir, line.strip()), base_dir)
                if line.strip():
                    camp, _, name = rel.partition("/")
                    rows.append({"campaign": camp, "name": name, "archive": rel})
    pre = [c for c in args.campaigns.split(",") if c]
    skip = re.compile(args.skip_names) if args.skip_names else None
    rows = [r for r in rows if (not pre or any(r["campaign"].startswith(c) for c in pre))
            and not (skip and skip.search(r["name"]))]
    os.makedirs(os.path.join(args.out, "differ"), exist_ok=True)
    out_csv = os.path.join(args.out, "rows.csv")
    done = set()
    if os.path.exists(out_csv):
        with open(out_csv) as fh:
            done = {(r["campaign"], r["name"]) for r in csv.DictReader(fh)}
    rows = [r for r in rows if (r["campaign"], r["name"]) not in done]
    commit = code_commit()

    def one(r):
        tmp = tempfile.mkdtemp(dir=args.scratch)
        try:
            src = os.path.join(base_dir, r["archive"])
            if os.path.isdir(src):
                d = src
            else:
                with tarfile.open(src) as tf:
                    tf.extractall(tmp, filter="data")
                d = trace_dir(tmp)
            hit = affected(args.new, d, args.timeout) if args.unaffected_only_sampled else "?"
            sampled = zlib.crc32((r["campaign"] + "/" + r["name"]).encode()) % max(args.unaffected_only_sampled, 1) == 0
            if hit == "0" and not sampled:
                return {"campaign": r["campaign"], "name": r["name"], "affected": hit, "same": "skipped",
                        "base_exit": "", "new_exit": "", "base_seconds": "", "new_seconds": "", "commit": commit}
            a, b = run(args.base, d, args.timeout), run(args.new, d, args.timeout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        same = a[0] == b[0] and normal(a[1]) == normal(b[1])
        if not same:
            tag = (r["campaign"] + "_" + r["name"]).replace("/", "_")
            for name, x in (("base", a), ("new", b)):
                with open(os.path.join(args.out, "differ", f"{tag}.{name}"), "wb") as fh:
                    fh.write(x[1] + b"\n--- stderr\n" + x[2][-20000:] + f"\n--- exit status {x[0]}\n".encode())
        return {"campaign": r["campaign"], "name": r["name"], "affected": hit, "same": int(same), "base_exit": a[0],
                "new_exit": b[0], "base_seconds": f"{a[3]:.2f}", "new_seconds": f"{b[3]:.2f}", "commit": commit}

    new_file = not os.path.exists(out_csv)
    n = differ = 0
    with cf.ThreadPoolExecutor(args.jobs) as ex, open(out_csv, "a", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        if new_file:
            w.writeheader()
        for row in ex.map(one, rows):
            w.writerow(row)
            fh.flush()
            n += 1
            differ += row["same"] == 0
    print(f"traces {n}, differing {differ}")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
