#!/usr/bin/env python3
"""E1 harness over the MPI Bugs Initiative (MBI) corpus.

For every selected generated program: parse the MBI header (run command, np, expected
outcome and detail), build it with the system mpicc, run it once under the Stretto tracer
with the header's np, analyse the trace, and record the Stretto classes, qualified by the
level that established them. With --confirm every error finding is replayed with its pin
and the outcome of each replay goes to replays.csv next to the table (one row per finding:
class, level, certainty, pin, result, whether the pin was applied, verdict). With --must,
MUST runs on the same source (scripts/must_run.sh: its own MPICH build and run) with the
same timeout as the traced run, and its verdict is recorded. Output: one CSV row per
program plus per-program logs under results/<corpus>/<name>/. With --replay-only the
selected programs are neither rebuilt nor rerun: their kept trace is analysed again and only
the replays are redone, so a replay fix does not cost a new corpus run; the other rows of the
table and of replays.csv are kept. With --guard the traced run has the tracer's buffer guard on
(STRETTO_GUARD=1), which adds the local-concurrency findings (class 3-local).

usage: run_mbi.py [--corpus mbi|mbb] [--select REGEX] [--limit N] [--confirm] [--must] [--guard] [--timeout S] [--jobs J]
       run_mbi.py --replay-only [--select REGEX] [--timeout S] ...   (implies --confirm)
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
import time
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
TRACER = os.path.join(ROOT, "tracer", "bin", "openmpi", "libstretto-trace.so")
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from stretto import confirm as confirm_mod, report, trace  # noqa: E402
from stretto.confirm import classify_rc  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from provenance import code_commit  # noqa: E402


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
    """STRETTO_PIN=1:0:0:0 -> 1/0/0/0 (rank/widx/src[/tag]); STRETTO_PIN_PROBE=... -> probe 1/0/2."""
    name, _, spec = (pin or "").partition("=")
    return ("probe " if name.endswith("PROBE") else "") + spec.replace(":", "/")


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
    env = ["-x", f"STRETTO_TRACE_DIR={tracedir}", "-x", f"LD_PRELOAD={TRACER}"] + (["-x", "STRETTO_GUARD=1"] if args.guard else [])
    cmd = ["/usr/bin/timeout", "-k", "5", str(args.timeout), MPIRUN, "-np", str(row["np"]), *env, exe]
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={**os.environ, "OMPI_MCA_rmaps_base_oversubscribe": "1"})
    open(os.path.join(out, "run.stdout"), "w").write(r.stdout)
    open(os.path.join(out, "run.stderr"), "w").write(r.stderr)
    row["run"] = classify_rc(r.returncode, r.stderr, time.monotonic() - t0, args.timeout)
    analyse(row, out, exe, tracedir, args)
    if args.must:
        m = subprocess.run([os.path.join(ROOT, "scripts", "must_run.sh"), str(row["np"]), exe, str(args.timeout)],
                           capture_output=True, text=True, cwd=out, stdin=subprocess.DEVNULL)
        row["must"] = m.stdout.strip().splitlines()[-1] if m.stdout.strip() else f"rc={m.returncode}"
    return row


def replay_one(row: dict, args) -> dict:
    """--replay-only: analyse the kept trace again and redo the replays; the run and MUST
    columns of the row stay."""
    out = os.path.join(args.results, row["name"])
    analyse(row, out, os.path.join(out, "main"), os.path.join(out, "trace"), args)
    return row


def analyse(row: dict, out: str, exe: str, tracedir: str, args) -> None:
    """Analyse the trace under out/, fill the Stretto columns of row and, with --confirm,
    replay every error finding with its pin."""
    try:
        t = trace.load(tracedir)
        rep = report.analyze(t, with_sp=not args.no_sp, sp_max_events=args.sp_max_events, max_states=args.max_states)
        row["classes"] = " ".join(rep.classes())
        row["levels"] = " ".join(rep.levels())
        row["findings"] = len(rep.findings)
        row["l1_alternatives"] = sum(1 for f in rep.findings if f.level == "L1" and f.family == "1")
        row["wildcard_recvs"] = sum(1 for x in t.recvs.values() if x.wildcard)
        row["events"] = sum(len(v) for v in t.ranks.values())
        row["notes"] = "; ".join(rep.notes)[:300]
        row["commit"] = code_commit()
        with open(os.path.join(out, "report.txt"), "w") as fh:
            fh.write(report.format_report(t, rep, show_infeasible=True))
        with open(os.path.join(out, "report.json"), "w") as fh:
            json.dump(report.to_json(t, rep), fh, default=str, indent=1)
        if args.confirm:
            res = confirm_mod.confirm(rep, row["np"], exe, timeout=args.timeout, t=t)
            row["confirmed"] = sum(1 for c in res if c.verdict == "confirmed")
            row["not_confirmed"] = sum(1 for c in res if c.verdict in ("not-confirmed", "inconclusive"))
            # pins written with "/" separators: "1d 1/0/0/0 -> TIMEOUT" (colon runs read as IPv6 literals)
            row["replays"] = "; ".join(f"{c.finding.cls} {pin_label(c.pin)} -> {c.result}" for c in res)[:300]
            row["_replays"] = [{"program": row["name"], "cls": c.finding.cls, "level": c.finding.level,
                                "certainty": c.finding.certainty, "pin": pin_label(c.pin), "result": c.result,
                                "pin_applied": c.pin_applied, "pin_matched": c.pin_matched, "control": c.control,
                                "verdict": c.verdict, "commit": code_commit(),
                                "stderr_tail": " ".join(c.stderr_tail.split())[-200:]} for c in res]
    except Exception as e:  # noqa: BLE001
        row.update(classes="", findings=0, notes=f"analysis failed: {e}"[:300])


REPLAY_KEYS = ["program", "cls", "level", "certainty", "pin", "result", "pin_applied", "pin_matched", "control", "verdict", "stderr_tail", "commit"]


def replay_only(args) -> int:
    rp = os.path.join(os.path.dirname(args.out), "replays.csv")
    rows = list(csv.DictReader(open(args.out)))
    keys = list(rows[0].keys()) + ([] if "commit" in rows[0] else ["commit"])
    replays = list(csv.DictReader(open(rp))) if os.path.exists(rp) else []
    todo = [r for r in rows if r["build"] == "ok" and re.search(args.select, r["name"])]
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for i, f in enumerate(as_completed([ex.submit(replay_one, r, args) for r in todo]), 1):
            row = f.result()
            print(f"[{i}/{len(todo)}] {row['name']:<50} {row.get('replays', '')}", flush=True)
    done = {r["name"] for r in todo}
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})
    with open(rp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=REPLAY_KEYS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerows(r.get("_replays", []) if r["name"] in done else [x for x in replays if x["program"] == r["name"]])
    print(f"wrote {args.out} and {rp} ({len(todo)} programs replayed again)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", default=r"MessageRace|ANY|Probe|Correct", help="regex on file names")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--must", action="store_true")
    ap.add_argument("--confirm", action="store_true", help="replay every error finding with its pin")
    ap.add_argument("--guard", action="store_true", help="trace with the buffer guard on (STRETTO_GUARD=1)")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--no-sp", action="store_true")
    ap.add_argument("--sp-max-events", type=int, default=600)
    ap.add_argument("--max-states", type=int, default=30000, help="explorer state cap per forced alternative")
    ap.add_argument("--corpus", default="mbi", choices=sorted(CORPORA))
    ap.add_argument("--out", default=None)
    ap.add_argument("--replay-only", action="store_true", help="redo the replays of the selected programs from their kept traces")
    args = ap.parse_args()
    args.gencodes = CORPORA[args.corpus]
    args.results = os.path.join(ROOT, "experiments", "results", args.corpus)
    args.out = args.out or os.path.join(args.results, f"{args.corpus}.csv")
    if args.replay_only:
        args.confirm = True
        return replay_only(args)
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
    # MUST runs separately (run_must.py, docker on the host): a rerun of the corpus keeps the
    # MUST verdicts of the previous table, since MUST's own run does not depend on this code
    if os.path.exists(args.out):
        prev = {r["name"]: r.get("must", "") for r in csv.DictReader(open(args.out))}
        for r in rows:
            if not r.get("must") and prev.get(r["name"]):
                r["must"] = prev[r["name"]]
    keys = ["name", "category", "expect", "detail", "can_deadlock", "np", "anysrc", "anytag", "probe", "persistent", "waitany",
            "build", "run", "wildcard_recvs", "events", "findings", "l1_alternatives", "classes", "levels", "confirmed", "not_confirmed", "replays", "must", "notes", "commit"]
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})
    print(f"wrote {args.out} ({len(rows)} rows)")
    if args.confirm:
        rp = os.path.join(os.path.dirname(args.out), "replays.csv")
        with open(rp, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=REPLAY_KEYS, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerows(r.get("_replays", []))
        print(f"wrote {rp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
