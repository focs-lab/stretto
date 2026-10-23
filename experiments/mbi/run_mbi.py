#!/usr/bin/env python3
"""E1 harness over the MPI Bugs Initiative (MBI) corpus.

For every selected generated program: parse the MBI header (run command, np, expected
outcome and detail), build it with the system mpicc, run it once under the Stretto tracer
with the header's np, analyse the trace, and record the Stretto classes, qualified by the
level that established them. With --confirm every error finding is replayed with its pin
and the outcome of each replay goes to replays.csv next to the table (one row per finding:
class, level, certainty, pin, result, whether the pin was applied, verdict; with the tracer
built, replays and controls run under it, and the row says whether each is a run of the
program without its pins and gives the strict verdict, stretto.confirm). With --must,
MUST runs on the same source (scripts/must_run.sh: its own MPICH build and run) with the
same timeout as the traced run, and its verdict is recorded. Output: one CSV row per
program plus per-program logs under results/<corpus>/<name>/. With --replay-only the
selected programs are neither rebuilt nor rerun: their kept trace is analysed again and only
the replays are redone, so a replay fix does not cost a new corpus run; the other rows of the
table and of replays.csv are kept (with --replays-out the new replays go to that file and
neither is rewritten). --out and --work put a run's table and its programs'
directories elsewhere, so that repeated runs keep apart (without --confirm: replays.csv is
next to the table). With --guard the traced run has the tracer's buffer guard on
(STRETTO_GUARD=1), which adds the local-concurrency findings (class 3-local); its table goes to
results/guard/<corpus>.csv and its programs' directories under results/guard/<corpus>/. With
--reads DIR each program is analysed with its first-read map DIR/<name>.json (reads_maps.py,
docs/firstread.md), if there is one. With KEEP_TRACES set (stretto.keep), each traced run's
trace is kept as <campaign>/<program>, and each replay's and control's as
<campaign>/<program>__<class>-<level>-<pin>__replay (or __control); the campaign is the table's
path under experiments/results (mbi/mbi, guard/mbb, reps/mbi-3), that of --replays-out, or
--keep-campaign.

usage: run_mbi.py [--corpus mbi|mbb] [--select REGEX] [--limit N] [--confirm] [--must] [--guard] [--timeout S] [--jobs J] [--reads DIR]
       run_mbi.py --replay-only [--select REGEX] [--timeout S] [--replays-out CSV] ...   (implies --confirm)
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
import traceback
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
from stretto import confirm as confirm_mod, keep, reads, report, trace  # noqa: E402
from stretto.confirm import classify_rc  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from provenance import code_commit  # noqa: E402
from selection import DEFAULT_SELECT, selects  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score import CRASH  # noqa: E402


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
    """STRETTO_PIN=1:0:0:0 -> 1/0/0/0 (rank/widx/src[/tag]); STRETTO_PIN_PROBE=... -> probe 1/0/2. The
    identity a pin carries (stretto.confirm) is left out, as the tables name pins without it."""
    name, _, spec = confirm_mod.strip_identity(pin or "").partition("=")
    return ("probe " if name.endswith("PROBE") else "") + spec.replace(":", "/")


def run_one(name: str, args) -> dict:
    src = os.path.join(args.gencodes, name)
    out = os.path.join(args.results, name[:-2].replace("/", "__"))
    os.makedirs(out, exist_ok=True)
    row = {"name": name[:-2].replace("/", "__"), **parse_header(src), **features(src), "commit": code_commit()}
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
    keep.keep(tracedir, kept_as(args), row["name"], commit=row["commit"])
    analyse(row, out, exe, tracedir, args)
    if args.must:
        m = subprocess.run([os.path.join(ROOT, "scripts", "must_run.sh"), str(row["np"]), exe, str(args.timeout)],
                           capture_output=True, text=True, cwd=out, stdin=subprocess.DEVNULL)
        row["must"] = m.stdout.strip().splitlines()[-1] if m.stdout.strip() else f"rc={m.returncode}"
    return row


def kept_as(args) -> str:
    """The campaign under which a run keeps its traces (KEEP_TRACES, stretto.keep): --keep-campaign,
    else main's, from the table (keep.campaign_of)."""
    return getattr(args, "keep_campaign", None) or "run_mbi"


def replay_one(row: dict, args) -> dict:
    """--replay-only: analyse the kept trace again and redo the replays; the run and MUST
    columns of the row stay."""
    out = os.path.join(args.results, row["name"])
    analyse(row, out, os.path.join(out, "main"), os.path.join(out, "trace"), args)
    return row


def analyse(row: dict, out: str, exe: str, tracedir: str, args) -> None:
    """Analyse the trace under out/, fill the Stretto columns of row and, with --confirm,
    replay every error finding with its pin. An analysis that raises has no verdict: the row
    says CRASH in its analysis column (score.verdict), its classes stay empty, and the
    traceback goes to out/analysis.err. An error in the replays is the harness's and stops the
    run. The row's commit is that of the traced run (run_one); a re-analysis of a kept trace
    by other code records its own commit in analysis_commit."""
    row["commit"] = row.get("commit") or code_commit()
    row["analysis_commit"] = code_commit() if code_commit() != row["commit"] else ""
    try:
        t = trace.load(tracedir)
        bounds = None
        if args.reads:
            mp = os.path.join(args.reads, row["name"] + ".json")
            bounds = (reads.SiteMap.load(mp, reads.addr2line_resolver(out), t=t, objdir=out, unchecked=args.reads_unchecked)
                      if os.path.exists(mp) else None)
        rep = report.analyze(t, with_sp=not args.no_sp, sp_max_events=args.sp_max_events, max_states=args.max_states,
                             reads=bounds)
    except Exception as e:  # noqa: BLE001  (recorded as CRASH, never as a verdict)
        with open(os.path.join(out, "analysis.err"), "w") as fh:
            fh.write(traceback.format_exc())
        row.update(analysis=CRASH, classes="", levels="", findings="", l1_alternatives="",
                   notes=f"analysis failed: {type(e).__name__}: {e}"[:300])
        return
    row["analysis"] = "ok"
    row["classes"] = " ".join(rep.classes())
    row["levels"] = " ".join(rep.levels())
    row["findings"] = len(rep.findings)
    row["l1_alternatives"] = sum(1 for f in rep.findings if f.level == "L1" and f.family == "1")
    row["wildcard_recvs"] = sum(1 for x in t.recvs.values() if x.wildcard)
    row["events"] = sum(len(v) for v in t.ranks.values())
    row["notes"] = "; ".join(rep.notes)[:300]
    # an analysis with read bounds keeps the run's own reports, which E3 counts, as reanalyze.py does
    stem = "report-reads" if args.reads else "report"
    with open(os.path.join(out, stem + ".txt"), "w") as fh:
        fh.write(report.format_report(t, rep, show_infeasible=True))
    with open(os.path.join(out, stem + ".json"), "w") as fh:
        json.dump(report.to_json(t, rep), fh, default=str, indent=1)
    if args.confirm:
        res = confirm_mod.confirm(rep, row["np"], exe, timeout=args.timeout, t=t, keep_as=(kept_as(args), row["name"]))
        row["confirmed"] = sum(1 for c in res if c.verdict == "confirmed")
        row["confirmed_strict"] = sum(1 for c in res if c.strict == "confirmed")
        row["not_confirmed"] = sum(1 for c in res if c.verdict in ("not-confirmed", "inconclusive"))
        # pins written with "/" separators: "1d 1/0/0/0 -> TIMEOUT" (colon runs read as IPv6 literals)
        row["replays"] = "; ".join(f"{c.finding.cls} {pin_label(c.pin)} -> {c.result}" for c in res)[:300]
        row["_replays"] = [{"program": row["name"], "cls": c.finding.cls, "level": c.finding.level,
                            "certainty": c.finding.certainty, "pin": pin_label(c.pin), "result": c.result,
                            "pin_applied": c.pin_applied, "pin_matched": c.pin_matched, "control": c.control,
                            "verdict": c.verdict, "run_valid": c.run_valid, "violation": c.violation[:300],
                            "control_valid": c.control_valid, "control_violation": c.control_violation[:300],
                            "strict_verdict": c.strict, "misapplied": c.misapplied, "commit": code_commit(),
                            "stderr_tail": " ".join(c.stderr_tail.split())[-200:]} for c in res]


def crashed_status(rows) -> int:
    """1 (and the names on stderr) if the analysis crashed on a row: the table is written, with
    CRASH in those rows, but the run did not succeed."""
    crashed = [r["name"] for r in rows if r.get("analysis") == CRASH]
    if crashed:
        print(f"the analysis crashed on {len(crashed)} program(s) (CRASH; tracebacks in their analysis.err): "
              + ", ".join(crashed), file=sys.stderr)
    return 1 if crashed else 0


REPLAY_KEYS = ["program", "cls", "level", "certainty", "pin", "result", "pin_applied", "pin_matched", "control", "verdict",
               "run_valid", "violation", "control_valid", "control_violation", "strict_verdict", "misapplied", "stderr_tail", "commit"]


def replay_only(args) -> int:
    rp = os.path.join(os.path.dirname(args.out), "replays.csv")
    if not os.path.exists(args.out):
        print(f"run_mbi.py --replay-only: no table {args.out}", file=sys.stderr)
        return 1
    with open(args.out) as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print(f"run_mbi.py --replay-only: {args.out} has no rows", file=sys.stderr)
        return 1
    keys = list(rows[0].keys()) + [k for k in ("analysis", "commit", "analysis_commit") if k not in rows[0]]
    replays = list(csv.DictReader(open(rp))) if os.path.exists(rp) else []
    todo = [r for r in rows if r["build"] == "ok" and selects(args.select, r["name"])]
    if not todo:
        print(f"run_mbi.py --replay-only: --select {args.select!r} selects no compiled program of {args.out}; nothing written",
              file=sys.stderr)
        return 1
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for i, f in enumerate(as_completed([ex.submit(replay_one, r, args) for r in todo]), 1):
            row = f.result()
            print(f"[{i}/{len(todo)}] {row['name']:<50} {row.get('replays', '')}", flush=True)
    if args.replays_out:
        # the replays alone, of the selected programs; the table and its replays stay as they were
        os.makedirs(os.path.dirname(os.path.abspath(args.replays_out)), exist_ok=True)
        with open(args.replays_out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=REPLAY_KEYS, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerows(r.get("_replays", []))
        print(f"wrote {args.replays_out} ({len(todo)} programs replayed again)")
        return crashed_status(todo)
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
    return crashed_status(todo)


def options(**given) -> argparse.Namespace:
    """The options of a run with every default of the command line, and the given ones in
    their place: for scripts that call run_one directly (experiments/mpiprv/stretto_corpus.py),
    so that an option added here reaches them with its default."""
    args = parser().parse_args([])
    for k, v in given.items():
        setattr(args, k, v)
    return args


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", default=None,
                    help="regex on program names, as table names (P2P__CallOrdering__x) or paths (P2P/CallOrdering/x.c), "
                         "experiments/selection.py; default: the campaign's selection of the corpus")
    ap.add_argument("--limit", type=int, default=0, help="only the first N programs selected (needs --out)")
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
    ap.add_argument("--work", default=None, help="directory for the programs' builds, traces and logs (default: next to the table)")
    ap.add_argument("--replay-only", action="store_true", help="redo the replays of the selected programs from their kept traces")
    ap.add_argument("--replays-out", default=None,
                    help="with --replay-only: write the new replays of the selected programs to this CSV and leave the table "
                         "and its replays.csv as they are")
    ap.add_argument("--reads-unchecked", action="store_true",
                    help="with --reads: accept maps without checking them against the traced programs' sources")
    ap.add_argument("--reads", default=None,
                    help="directory of first-read maps, DIR/<name>.json (reads_maps.py): analyse with read bounds (docs/firstread.md)")
    ap.add_argument("--keep-campaign", default=None,
                    help="with KEEP_TRACES set, keep the traced runs' and replays' traces under this campaign (stretto.keep; "
                         "default: the table's path under experiments/results, or that of --replays-out)")
    return ap


def main() -> int:
    args = parser().parse_args()
    args.gencodes = CORPORA[args.corpus]
    partial = args.select is not None and args.select != DEFAULT_SELECT[args.corpus] or bool(args.limit)
    if args.select is None:
        args.select = DEFAULT_SELECT[args.corpus]
    # a run with the guard keeps its programs' directories apart, so it never replaces the traces
    # of the plain run that E3 and the replays read
    base = os.path.join(ROOT, "experiments", "results", *(["guard"] if args.guard else []))
    args.results = args.work or os.path.join(base, args.corpus)
    canonical = os.path.join(base, f"{args.corpus}.csv") if args.guard else os.path.join(args.results, f"{args.corpus}.csv")
    if args.replay_only:
        args.out = args.out or canonical
        args.confirm = True
        args.keep_campaign = args.keep_campaign or keep.campaign_of(args.replays_out or args.out)
        return replay_only(args)
    # the canonical table is written only by a run of the campaign's selection: a part of the
    # corpus (another --select, --limit) goes to --out, never over the whole table
    if partial and not args.out:
        print(f"run_mbi.py: a selection other than the corpus's ({DEFAULT_SELECT[args.corpus]!r}) or --limit would overwrite "
              f"{os.path.relpath(canonical, ROOT)} with part of it; give --out", file=sys.stderr)
        return 2
    args.out = args.out or canonical
    args.keep_campaign = args.keep_campaign or keep.campaign_of(args.out)
    if not os.path.isdir(args.gencodes):
        print(f"run_mbi.py: no corpus at {args.gencodes} (third_party/fetch.sh corpora)", file=sys.stderr)
        return 1
    names = []
    for dp, _, fs in os.walk(args.gencodes):
        for f in fs:
            rel = os.path.relpath(os.path.join(dp, f), args.gencodes)
            if f.endswith(".c") and selects(args.select, rel):
                names.append(rel)
    names.sort()
    if args.limit:
        names = names[:args.limit]
    if not names:
        print(f"run_mbi.py: --select {args.select!r} selects no program of {args.gencodes}; nothing written", file=sys.stderr)
        return 1
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
    # MUST verdicts of the previous table, since MUST's own run does not depend on this code;
    # so are the columns of other MUST versions (run_must.py --column must...)
    other_must = []
    if os.path.exists(args.out):
        prev_rows = list(csv.DictReader(open(args.out)))
        other_must = [k for k in (prev_rows[0].keys() if prev_rows else []) if k.startswith("must") and k != "must"]
        prev = {r["name"]: r for r in prev_rows}
        for r in rows:
            for k in ["must"] + other_must:
                if not r.get(k) and prev.get(r["name"], {}).get(k):
                    r[k] = prev[r["name"]][k]
    keys = ["name", "category", "expect", "detail", "can_deadlock", "np", "anysrc", "anytag", "probe", "persistent", "waitany",
            "build", "run", "wildcard_recvs", "events", "findings", "l1_alternatives", "classes", "levels", "analysis", "confirmed", "confirmed_strict",
            "not_confirmed", "replays", "must", "notes", "commit", "analysis_commit"]
    keys += other_must
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
    return crashed_status(rows)


if __name__ == "__main__":
    sys.exit(main())
