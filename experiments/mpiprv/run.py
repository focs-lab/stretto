#!/usr/bin/env python3
"""MPI-PRV on Stretto's corpora, inside MPI-PRV's image (experiments/mpiprv/prv.sh).

Programs: MPI-BugBench level 2, the LocalConcurrency and Correct programs of Stretto's corpus
table (experiments/results/mbb/mbb.csv); MBI, the families of the bug classes MPI-PRV's ten
properties describe (LocalConcurrency on point-to-point buffers, ReqLifecycle, ResLeak), with
their correct versions; and for every local-concurrency error with a twin
(experiments/mpiprv/markers.py), the corrected program, labelled OK.

Each program is instrumented with wac and one MOP file that defines every event the shipped
properties use (all.mop below), run once with its header's process count, and the trace is
analysed with MPI-PRV once per pattern. The patterns are those of the artifact's RQ1 property
files (/home/ubuntu/RQ1/MPI<n>aspect/Pattern.txt), plus, for properties 2 and 3, the other
member of the union in the paper's table, since the shipped analyser reads one pattern per
file. Three arms:
  a  the shipped properties whose events are all MPI calls (1, 6, 7, 9), program unchanged
  b  the buffer properties (2, 3, 8) with markers inserted by markers.blind, which does not
     read the labels
  c  the buffer properties with markers at the labelled lines only (markers.oracle; for a twin,
     at the moved lines): an upper bound, since it needs the answer
A program's verdict in an arm is ERROR if any of the arm's patterns is reported.

Scope: programs that use probes, or whose label is a message race or a buffering hazard, are
outside MPI-PRV's declared scope and are listed as such. The column `outside_model` lists the
MPI calls a program makes that the shipped instrumentation does not record (it records
MPI_Send, MPI_Recv, MPI_Isend, MPI_Irecv, MPI_Wait and MPI_Barrier, and the MOP's events).

usage: run.py [--jobs J] [--select REGEX] [--timeout S]   (writes experiments/results/mpiprv/corpus.csv)
"""
from __future__ import annotations
import argparse
import csv
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "experiments"))
import markers  # noqa: E402
from provenance import code_commit  # noqa: E402

MBB = os.path.join(ROOT, "third_party", "mpi-bugbench", "scripts", "gencodes")
MBI = os.path.join(ROOT, "study", "apps", "MBI", "gencodes")
WORK = os.path.join(HERE, "work", "corpus")
OUT = os.path.join(ROOT, "experiments", "results", "mpiprv", "corpus.csv")

MOP = """#include <stdio.h>
MPI(){
	event MPIFinalize before(): call(% MPI_Finalize(...)){}
	event Use before(): call(% usebuf2(...)){}
	event Write before(): call(% writebuf1(...)){}
	event write before(): call(% writebuf2(...)){}
	event Recvinit before(): call(% MPI_Recv_init(...)){}
	event Start before(): call(% MPI_Start(...)){}
	event Ireduce before(): call(% MPI_Ireduce(...)){}
	event Wincreate before(): call(% MPI_Win_create(...)){}
	event Winfree before(): call(% MPI_Win_free(...)){}
	event WinPut before(): call(% MPI_Put(...)){}
	pattern :  Irecv Use Isend
        @ match {
          printf("error use! \\n");
        }
}
"""
# name -> (allPattern, Pattern), as in the shipped Pattern.txt files
PATTERNS = {
    "P1": ("MPIFinalize,Use", "MPIFinalize,Irecv"),
    "P6": ("Wincreate,Winfree,WinPut", "Wincreate,Winfree,WinPut"),
    "P7": ("Wincreate,Winfree", "Wincreate,Winfree"),
    "P9": ("Ireduce", "Ireduce,Barrier,Wait"),
    "P2": ("MPIFinalize,Write", "Isend,Write,Recv"),
    "P2irecv": ("MPIFinalize,Write", "Isend,Write,Irecv"),
    "P3": ("MPIFinalize,Use", "Irecv,Use,Isend"),
    "P3send": ("MPIFinalize,Use", "Irecv,Use,Send"),
    "P8": ("Recvinit,Start,write", "Recvinit,Start,write,Wait"),
}
ARM_PATTERNS = {"a": ["P1", "P6", "P7", "P9"], "b": ["P2", "P2irecv", "P3", "P3send", "P8"],
                "c": ["P2", "P2irecv", "P3", "P3send", "P8"]}
MODELLED = {"MPI_Send", "MPI_Recv", "MPI_Isend", "MPI_Irecv", "MPI_Wait", "MPI_Barrier", "MPI_Init", "MPI_Finalize",
            "MPI_Comm_rank", "MPI_Comm_size", "MPI_Recv_init", "MPI_Start", "MPI_Ireduce", "MPI_Win_create",
            "MPI_Win_free", "MPI_Put"}
OUT_OF_SCOPE_LABELS = ("MessageRace", "Buffering", "BufferingHazard", "P2PBuffering")


def header(path):
    """Expected outcome, detail and process count from an MBI or MPI-BugBench header."""
    expect = detail = None
    np_ = 2
    for line in open(path, errors="replace"):
        m = re.search(r"-np\s+(\d+)", line)
        if m and "$" in line:
            np_ = int(m.group(1))
        m = re.match(r"\s*\|\s*(OK|ERROR:?\s*(\S*))", line)
        if m and expect is None:
            expect, detail = ("OK", "") if m.group(1).startswith("OK") else ("ERROR", m.group(2))
        if "END_MBI_TESTS" in line or "END_MBB_TESTS" in line:
            break
    return expect, detail, np_


def programs():
    res = []
    with open(os.path.join(ROOT, "experiments", "results", "mbb", "mbb.csv")) as fh:
        for r in csv.DictReader(fh):
            if r["detail"] == "LocalConcurrency" or r["expect"] == "OK":
                cat, base = r["name"].split("__", 1)
                path = os.path.join(MBB, cat, base + ".c")
                if os.path.exists(path):
                    res.append({"corpus": "mbb", "program": r["name"], "path": path, "expect": r["expect"],
                                "detail": r["detail"], "np": int(r["np"] or 2), "built": r["build"] == "ok"})
    for path in sorted(glob.glob(os.path.join(MBI, "*.c"))):
        base = os.path.basename(path)[:-2]
        fam = base.split("_")[0]
        p2p_lc = fam == "LocalConcurrency" and re.match(r"LocalConcurrency_(Irecv|Recv)", base)
        if p2p_lc or fam in ("ReqLifecycle", "ResLeak"):
            expect, detail, np_ = header(path)
            res.append({"corpus": "mbi", "program": base, "path": path, "expect": expect, "detail": detail or fam,
                        "np": np_, "built": True})
    twins = []
    for p in res:
        if p["expect"] == "ERROR" and "LocalConcurrency" in p["detail"] + p["program"]:
            src, where = markers.twin(open(p["path"]).read())
            if src is not None:
                twins.append({**p, "program": "twin:" + p["program"], "expect": "OK", "detail": "twin",
                              "twin_src": src, "twin_where": where})
    return res + twins


def scope(src, p):
    if re.search(r"\bMPI_(I?m?probe|Improbe|Mprobe)\s*\(", markers.blank_comments(src), re.I):
        return "out: probe"
    if any(x in (p["detail"] or "") for x in OUT_OF_SCOPE_LABELS):
        return "out: " + p["detail"]
    return "in"


def outside_model(src):
    code = markers.blank_comments(src)
    used = set(re.findall(r"\b(MPI_[A-Z][a-z_]*\w*)\s*\(", code))
    return " ".join(sorted(used - MODELLED))


def sh(cmd, cwd, timeout):
    try:
        r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"") + (e.stderr or b"")
        return "TIMEOUT", out.decode(errors="replace") if isinstance(out, bytes) else out


def instrument_run_analyse(src, np_, names, timeout):
    """Build src with wac, run it, analyse the trace with each named pattern. Cached by source."""
    key = hashlib.sha1(src.encode()).hexdigest()[:16]
    d = os.path.join(WORK, key)
    done = os.path.join(d, "done.txt")
    results = {}
    if os.path.exists(done):
        for line in open(done):
            k, v = line.rstrip("\n").split("\t", 1)
            results[k] = v
    if "build" not in results:
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        open(os.path.join(d, "prog.c"), "w").write(src)
        open(os.path.join(d, "all.mop"), "w").write(MOP)
        rc, out = sh("wac -mop all.mop prog.c -o prog -np -MPI", d, 600)
        open(os.path.join(d, "wac.log"), "w").write(out)
        results["build"] = "ok" if os.path.exists(os.path.join(d, "prog")) else "fail"
        if results["build"] == "ok":
            rc, out = sh(f"rm -f output.txt; /usr/bin/timeout -k 5 {timeout} mpirun -np {np_} ./prog", d, timeout + 30)
            open(os.path.join(d, "run.log"), "w").write(out)
            results["run"] = "TIMEOUT" if rc in (124, 137, "TIMEOUT") else f"rc={rc}"
            results["events"] = str(sum(1 for line in open(os.path.join(d, "output.txt")) if line.startswith("event"))) \
                if os.path.exists(os.path.join(d, "output.txt")) else "0"
    for n in names:
        if n in results or results["build"] != "ok":
            continue
        allp, pat = PATTERNS[n]
        pf = os.path.join(d, f"{n}.txt")
        open(pf, "w").write(f"parametric: 0\nallPattern : {{{allp}}}\nPattern : {{{pat}}}\n")
        if not os.path.exists(os.path.join(d, "output.txt")):
            results[n] = "no-trace"
            continue
        rc, out = sh(f"MPI-PRV -trace output.txt -pattern {n}.txt -np {np_}", d, 600)
        open(os.path.join(d, f"{n}.log"), "w").write(out)
        if "Pattern Matched!" in out:
            seq = out.split("Pattern Matched!", 1)[1].strip().splitlines()[0].strip()
            results[n] = "matched:" + seq
        elif "Pattern Not Matched!" in out:
            results[n] = "none"
        else:
            results[n] = f"error:{rc}"
    with open(done, "w") as fh:
        for k, v in results.items():
            fh.write(f"{k}\t{v}\n")
    return key, results


def one(p, args):
    src = p.get("twin_src") or open(p["path"]).read()
    rows = []
    sc = scope(src, p)
    om = outside_model(src)
    if not p["built"]:
        return [{**base_row(p, sc, om), "arm": a, "build": "not built with Open MPI 4.1.6"} for a in "abc"]
    if p.get("twin_src"):
        # the twin carries its markers at the moved statements; arms a and b start from the
        # corrected program without them
        orig = markers_stripped(src)
        oracle_src, owhere = src, p["twin_where"]
        blind_src, bwhere = markers.blind(orig)
    else:
        orig = src
        blind_src, bwhere = markers.blind(src)
        oracle_src, owhere = markers.oracle(src)
    variants = {"a": (orig, "-"), "b": (blind_src if bwhere else orig, bwhere or []),
                "c": (oracle_src if owhere else orig, owhere or [])}
    for arm, (s, where) in variants.items():
        names = ARM_PATTERNS[arm]
        key, res = instrument_run_analyse(s, p["np"], names, args.timeout)
        matched = [n for n in names if res.get(n, "").startswith("matched")]
        # a run that left no trace, or an analyser that failed, gives MPI-PRV no verdict: it is
        # a harness error, never "no pattern reported" (score.confusion counts it apart)
        failed = [n for n in names if res.get(n, "") == "no-trace" or res.get(n, "").startswith("error")]
        verdict = ("ERROR" if matched else "HARNESS-ERROR" if failed else "OK") if res.get("build") == "ok" else "-"
        rows.append({**base_row(p, sc, om), "arm": arm, "markers": where if isinstance(where, str) else
                     ";".join(f"{ln}:{'+'.join(k)}" for ln, k in where),
                     "build": res.get("build", ""), "run": res.get("run", ""), "events": res.get("events", ""),
                     "verdict": verdict,
                     "patterns": " ".join(f"{n}={res.get(n, '-').split(':')[0]}" for n in names),
                     "matched": " | ".join(f"{n}: {res[n].split(':', 1)[1]}" for n in matched),
                     "work": key})
    return rows


def markers_stripped(src):
    """A twin without its marker calls and their definitions (the corrected program itself)."""
    s = src.replace(markers.DEFS, "")
    return re.sub(r"\b(usebuf2|writebuf1|writebuf2)\(\);\s?", "", s)


def base_row(p, sc, om):
    return {"corpus": p["corpus"], "program": p["program"], "expect": p["expect"], "detail": p["detail"],
            "np": p["np"], "scope": sc, "outside_model": om, "commit": code_commit()}


FIELDS = ["corpus", "program", "expect", "detail", "np", "scope", "outside_model", "arm", "markers", "build", "run",
          "events", "verdict", "patterns", "matched", "work", "commit"]


RQ1 = {"P1": "CallOrdering_Finalize_nok", "P2": "Concurrency_Recv_Isend_nok", "P3": "Concurrency_Irecv_Send_nok",
       "P6": "MPIRMA_Use_error_nok", "P7": "MPIRMA_doublefree_nok", "P8": "LocalConcurrency_Recv_init_Send_nok",
       "P9": "CallOrdering_Ireduce_Barrier_nok"}


def rq1_check(args):
    """The combined MOP against the artifact's own RQ1 programs: each must still be reported by
    its property (the ones whose events all.mop defines)."""
    out = os.path.join(os.path.dirname(OUT), "corpus-rq1check.csv")
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["pattern", "program", "result", "commit"])
        for n, prog in RQ1.items():
            _, res = instrument_run_analyse(open(f"/home/ubuntu/RQ1/{prog}.c").read(), 2, [n], args.timeout)
            w.writerow([n, prog, res.get(n, res.get("build")), code_commit()])
            print(n, prog, res.get(n, res.get("build")), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=int(os.environ.get("JOBS") or 2))
    ap.add_argument("--select", default="")
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("TIMEOUT") or 60))
    ap.add_argument("--rq1-check", action="store_true", help="only check all.mop on the artifact's RQ1 programs")
    args = ap.parse_args()
    os.makedirs(WORK, exist_ok=True)
    if args.rq1_check:
        return rq1_check(args)
    progs = [p for p in programs() if re.search(args.select, p["program"])]
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    rows = []
    with ThreadPoolExecutor(args.jobs) as ex:
        for i, rs in enumerate(ex.map(lambda p: one(p, args), progs)):
            rows += rs
            print(f"[{i + 1}/{len(progs)}] {rs[0]['program']}: " + " ".join(f"{r['arm']}={r.get('verdict', '-')}" for r in rs),
                  flush=True)
    with open(OUT, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
