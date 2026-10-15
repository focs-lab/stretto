#!/usr/bin/env python3
"""E1 harness over the MPI Bugs Initiative (MBI) corpus.

For every selected generated program: parse the MBI header (run command, np, expected
outcome and detail), build it with the system mpicc, run it once under the Rematch tracer
with the header's np, analyse the trace, and record the Rematch classes. Optionally run
MUST on the same source (scripts/must_run.sh: its own MPICH build and run) and record its
verdict. Output: one CSV row per program plus per-program logs under results/mbi/<name>/.

usage: run_mbi.py [--select REGEX] [--limit N] [--must] [--timeout S] [--jobs J]
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CORPORA = {
    "mbi": os.path.join(ROOT, "study", "apps", "MBI", "gencodes"),
    "mbb": os.path.join(ROOT, "third_party", "mpi-bugbench", "scripts", "gencodes"),
}
GENCODES = CORPORA["mbi"]
RESULTS = os.path.join(ROOT, "experiments", "results", "mbi")
MPICC = "/usr/bin/mpicc.openmpi"
MPIRUN = "/usr/bin/mpirun.openmpi"
TRACER = os.path.join(ROOT, "tracer", "bin", "openmpi", "librematch-trace.so")
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from rematch import confirm as confirm_mod, report, trace  # noqa: E402


def parse_header(path: str) -> dict:
    """MBI header: BEGIN_MBI_TESTS / $ mpirun -np N ${EXE} / | OK or | ERROR: detail.
    MBB header: BEGIN_MBB_TESTS / ... / | ERROR CallOrdering / | CallOrdering-ANY_SOURCE."""
    cmd = expect = detail = None
    category = ""
    can_deadlock = False
    with open(path) as fh:
        inside = False
        for line in fh:
            if "BEGIN_MBI_TESTS" in line or "BEGIN_MBB_TESTS" in line:
                inside = True
                continue
            if "END_MBI_TESTS" in line or "END_MBB_TESTS" in line:
                break
            m = re.match(r"\s*Category:\s*(\S+)", line)
            if m:
                category = m.group(1)
            if inside:
                m = re.match(r"\s*\$ ?(.*)", line)
                if m:
                    cmd = m.group(1).strip()
                    continue
                m = re.match(r"\s*\|\s*(OK|ERROR:?\s*(.*))", line)
                if m and expect is None:
                    if m.group(1).startswith("OK"):
                        expect, detail = "OK", ""
                    else:
                        expect, detail = "ERROR", (m.group(2) or "").strip()
            if "can result in a Deadlock" in line or "can result in a deadlock" in line:
                can_deadlock = True
    np = 2
    if cmd:
        m = re.search(r"-np\s+(\d+)", cmd)
        if m:
            np = int(m.group(1))
    return {"cmd": cmd, "np": np, "expect": expect, "detail": detail, "can_deadlock": can_deadlock, "category": category}


def features(path: str) -> dict:
    src = open(path).read()
    return {
        "anysrc": "MPI_ANY_SOURCE" in src, "anytag": "MPI_ANY_TAG" in src,
        "probe": bool(re.search(r"MPI_I?probe", src)), "persistent": "MPI_Start" in src,
        "waitany": "MPI_Waitany" in src,
    }


def pin_label(pin: str) -> str:
    """REMATCH_PIN=1:0:0:0 -> 1/0/0/0 (rank/widx/src[/tag]); REMATCH_PIN_PROBE=... -> probe 1/0/2."""
    name, _, spec = (pin or "").partition("=")
    return ("probe " if name.endswith("PROBE") else "") + spec.replace(":", "/")


def classify_rc(rc: int, stderr: str) -> str:
    if rc == 0:
        return "FINISHED"
    if rc in (124, 137):
        return "TIMEOUT"
    m = re.search(r"MPI_ERR_[A-Z_]+", stderr)
    if m:
        return "MPI-ERROR:" + m.group(0)
    if re.search(r"truncat", stderr, re.I):
        return "MPI-ERROR:MPI_ERR_TRUNCATE"
    if rc >= 128:
        return f"SIGNAL:{rc - 128}"
    return f"APP-ERROR:{rc}"


def run_one(name: str, args) -> dict:
    src = os.path.join(args.gencodes, name)
    out = os.path.join(args.results, name[:-2].replace("/", "__"))
    os.makedirs(out, exist_ok=True)
    row = {"name": name[:-2].replace("/", "__"), **parse_header(src), **features(src)}
    exe = os.path.join(out, "main")
    b = subprocess.run([MPICC, "-O0", "-g", "-o", exe, src, "-lm"], capture_output=True, text=True)
    if b.returncode != 0:
        row.update(build="CE", run="", classes="", findings=0, notes=b.stderr[-300:])
        return row
    row["build"] = "ok"
    tracedir = os.path.join(out, "trace")
    shutil.rmtree(tracedir, ignore_errors=True)
    os.makedirs(tracedir)
    env = ["-x", f"REMATCH_TRACE_DIR={tracedir}", "-x", f"LD_PRELOAD={TRACER}"]
    cmd = ["timeout", "--foreground", "-k", "5", str(args.timeout), MPIRUN, "-np", str(row["np"]), *env, exe]
    r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={**os.environ, "OMPI_MCA_rmaps_base_oversubscribe": "1"})
    open(os.path.join(out, "run.stdout"), "w").write(r.stdout)
    open(os.path.join(out, "run.stderr"), "w").write(r.stderr)
    row["run"] = classify_rc(r.returncode, r.stderr)
    try:
        t = trace.load(tracedir)
        rep = report.analyze(t, with_sp=not args.no_sp, sp_max_events=args.sp_max_events, max_states=args.max_states)
        row["classes"] = " ".join(rep.classes())
        row["levels"] = " ".join(rep.levels())
        row["findings"] = len(rep.findings)
        row["wildcard_recvs"] = sum(1 for x in t.recvs.values() if x.wildcard)
        row["events"] = sum(len(v) for v in t.ranks.values())
        row["notes"] = "; ".join(rep.notes)[:300]
        with open(os.path.join(out, "report.txt"), "w") as fh:
            fh.write(report.format_report(t, rep, show_infeasible=True))
        with open(os.path.join(out, "report.json"), "w") as fh:
            json.dump([f.__dict__ for f in rep.findings], fh, default=str, indent=1)
        if args.confirm:
            res = confirm_mod.confirm(rep, row["np"], exe, timeout=args.timeout)
            row["confirmed"] = sum(1 for c in res if c.verdict == "confirmed")
            row["not_confirmed"] = sum(1 for c in res if c.verdict == "not-confirmed")
            # pins written with "/" separators: "1d 1/0/0/0 -> TIMEOUT" (colon runs read as IPv6 literals)
            row["replays"] = "; ".join(f"{c.finding.cls} {pin_label(c.pin)} -> {c.result}" for c in res)[:300]
    except Exception as e:  # noqa: BLE001
        row.update(classes="", findings=0, notes=f"analysis failed: {e}"[:300])
    if args.must:
        m = subprocess.run([os.path.join(ROOT, "scripts", "must_run.sh"), str(row["np"]), exe],
                           capture_output=True, text=True, cwd=out, stdin=subprocess.DEVNULL)
        row["must"] = m.stdout.strip().splitlines()[-1] if m.stdout.strip() else f"rc={m.returncode}"
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", default=r"MessageRace|ANY|Probe|Correct", help="regex on file names")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--must", action="store_true")
    ap.add_argument("--confirm", action="store_true", help="replay every error finding with its pin")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--no-sp", action="store_true")
    ap.add_argument("--sp-max-events", type=int, default=600)
    ap.add_argument("--max-states", type=int, default=30000, help="explorer state cap per forced alternative")
    ap.add_argument("--corpus", default="mbi", choices=sorted(CORPORA))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    args.gencodes = CORPORA[args.corpus]
    args.results = os.path.join(ROOT, "experiments", "results", args.corpus)
    args.out = args.out or os.path.join(args.results, f"{args.corpus}.csv")
    names = []
    for dp, _, fs in os.walk(args.gencodes):
        for f in fs:
            rel = os.path.relpath(os.path.join(dp, f), args.gencodes)
            if f.endswith(".c") and re.search(args.select, rel):
                names.append(rel)
    names.sort()
    if args.limit:
        names = names[:args.limit]
    os.makedirs(args.results, exist_ok=True)
    rows = []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(run_one, n, args): n for n in names}
        for i, f in enumerate(as_completed(futs), 1):
            row = f.result()
            rows.append(row)
            print(f"[{i}/{len(names)}] {row['name']:<50} {row.get('run', ''):<22} {row.get('classes', '')}", flush=True)
    rows.sort(key=lambda r: r["name"])
    keys = ["name", "category", "expect", "detail", "can_deadlock", "np", "anysrc", "anytag", "probe", "persistent", "waitany",
            "build", "run", "wildcard_recvs", "events", "findings", "classes", "levels", "confirmed", "not_confirmed", "replays", "must", "notes"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})
    print(f"wrote {args.out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
