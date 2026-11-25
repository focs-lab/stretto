#!/usr/bin/env python3
"""The suites of the uniform campaign (experiments/protocol/RULE.md, Section 5): MBI, MPI-BugBench,
MPI-CorrBench's 513 programs outside its OpenMP directory and its 103 OpenMP programs. The
committed scripts run a pass of Stretto (experiments/mbi/run_mbi.py, experiments/corrbench/run.py,
experiments/mustvc/stretto_bench.sh), of random delays (experiments/stress_baseline.py) and of NINJA
(experiments/ninja/run.py corpus); this file adds what they lack for a pass of every tool on every
suite, inside the job's container:

  fetch-corrbench DIR   clone MPI-CorrBench at a05644b into DIR
  build                 every program of a suite compiled with the MPI's mpicc into OUT/<name>/main,
                        and OUT/<suite>.csv (name, np, expect, build, source): the corpus table that
                        stress_baseline.py and ninja/run.py corpus read (their binaries beside it)
  must                  one pass of MUST 1.11.2 (stretto-must-u:24.04) over a build's programs, each
                        compiled by MUST's run (scripts/must_once.sh: the MPI library's mpicc,
                        Open MPI's mpirun, MUST's distributed layout at fan-in 4, RULE.md amendments
                        1 and 2): OUT (name, expect, np, must1112, seconds, commit). --tsan: MUST's
                        ThreadSanitizer analysis (must-tsan-cc, --must:tsan; amendment 16), column
                        must1112tsan. Before the pass, one uncounted run per process count builds
                        MUST's layout into the cache, one pass at a time (amendment 6)

Names: an MBI program by its file name, an MPI-BugBench one and a CorrBench one by its path with
"/" as "__", without ".c". Expect: MBI's and MPI-BugBench's headers (run_mbi.parse_header);
CorrBench's label (erroneous unless under correct/, experiments/corrbench/programs.csv for the 513,
the path for the OpenMP ones). Every CorrBench program runs on 2 processes (CorrBench's harness),
the OpenMP ones with 2 threads each.

usage: suites.py fetch-corrbench DIR
       suites.py build --suite S --mpi M --out DIR [--select REGEX] [--limit N] [--jobs J]
       suites.py must --suite S --mpi M --table CSV --out CSV [--timeout S] [--jobs J] [--select REGEX]
       suites.py regex --suite S [--select REGEX] [--limit N] [--form name|path] [--correct]
       suites.py check --suite S --table CSV [--select REGEX] [--limit N] [--correct] [--built CSV]

check: a pass's table has a row of every program the pass selected (those the build compiled,
--built, for the tools that run its binaries); a program missing is a harness error (exit 3),
since a script that stopped mid-pass would count as a whole pass without it.
"""
import argparse
import csv
import fcntl
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CORRBENCH_COMMIT = "a05644b"
CORRBENCH_URL = "https://github.com/tudasc/MPI-Corrbench.git"
CORRBENCH = os.path.join(ROOT, "third_party", "corrbench")
SOURCES = {"mbi": os.path.join(ROOT, "study", "apps", "MBI", "gencodes"),
           "mbb": os.path.join(ROOT, "third_party", "mpi-bugbench", "scripts", "gencodes"),
           "corrbench": os.path.join(CORRBENCH, "micro-benches", "0-level"),
           "corrbench-openmp": os.path.join(CORRBENCH, "micro-benches", "0-level", "openmp")}
FIELDS = ["name", "np", "expect", "build", "source"]
WARMUP_TIMEOUT = 1800     # MUST's warm-up run per process count (it builds the layout), uncounted


def programs(suite, select=".", limit=0, correct=False):
    """[(name, source path, np, expect)] of a suite, in a fixed order (correct: only the programs
    the suite labels correct, expect OK)."""
    src = SOURCES[suite]
    out = []
    if suite in ("mbi", "mbb"):
        # the programs of the committed corpus table (Section 5: MBI's 115 of tab:rq2, MPI-BugBench's
        # 388 that build), out of the generators' whole output
        sys.path.insert(0, os.path.join(ROOT, "experiments", "mbi"))
        from run_mbi import parse_header
        with open(os.path.join(ROOT, "experiments", "results", suite, suite + ".csv"), newline="") as fh:
            names = [r["name"] for r in csv.DictReader(fh) if r.get("build") == "ok"]
        for name in names:
            path = os.path.join(src, name.replace("__", "/") + ".c")
            h = parse_header(path)
            out.append((name, path, h["np"], h["expect"]))
    elif suite == "corrbench":
        with open(os.path.join(ROOT, "experiments", "corrbench", "programs.csv"), newline="") as fh:
            for r in csv.DictReader(fh):
                out.append((r["program"][:-2].replace("/", "__"), os.path.join(src, r["program"]), 2,
                            "OK" if r["label"] == "correct" else "ERROR"))
    else:
        for d, _, files in sorted(os.walk(src)):
            for f in sorted(files):
                if f.endswith(".c"):
                    path = os.path.join(d, f)
                    rel = os.path.relpath(path, src)
                    out.append((rel[:-2].replace("/", "__"), path, 2, "OK" if "correct/" in rel else "ERROR"))
    out = [p for p in out if re.search(select, p[0])]
    out = out[:limit] if limit else out
    return [p for p in out if p[3] == "OK"] if correct else out


def labels(suite):
    """{name: (erroneous, datarace)} of a suite's programs: whether the suite labels the program
    erroneous (a run of it that does not finish within T shows the symptom, amendment 28) and a data
    race (a data-race report of MUST's ThreadSanitizer build counts, amendment 22). MBI and
    MPI-BugBench: the header of an erroneous program names a data race or one of the concurrency
    classes; CorrBench: its error's description in experiments/corrbench/programs.csv; its OpenMP
    programs: the path (an erroneous one under data_race/)."""
    out = {}
    why = {}
    if suite == "corrbench":
        with open(os.path.join(ROOT, "experiments", "corrbench", "programs.csv"), newline="") as fh:
            why = dict((r["program"][:-2].replace("/", "__"), r["why"]) for r in csv.DictReader(fh))
    for name, src, _, expect in programs(suite):
        if expect == "OK":
            out[name] = (False, False)
        elif suite in ("mbi", "mbb"):
            head = []
            with open(src, errors="replace") as fh:
                for line in fh:
                    head.append(line)
                    if "END_MBI_TESTS" in line or "END_MBB_TESTS" in line:
                        break
            text = "".join(head)
            out[name] = (True, bool(re.search(r"concurrency|data ?race", text, re.I)))
        elif suite == "corrbench":
            w = why.get(name, "")
            out[name] = (True, bool(re.search(r"race|buffer is written|write one buffer", w)))
        else:
            rel = os.path.relpath(src, SOURCES[suite])
            out[name] = (True, rel.startswith("data_race/"))
    return out


def cflags(suite, source):
    if suite.startswith("corrbench"):
        f = ["-I", os.path.dirname(source), "-I", os.path.join(SOURCES["corrbench"], "correct", "include")]
        if suite == "corrbench-openmp":
            f += ["-I", SOURCES["corrbench-openmp"]]      # nondeterminism.h, as stretto_bench.sh's DIR
        return f + (["-fopenmp"] if suite == "corrbench-openmp" else [])
    return []


def build(a):
    os.makedirs(a.out, exist_ok=True)

    def one(p):
        name, src, np, expect = p
        d = os.path.join(a.out, name)
        os.makedirs(d, exist_ok=True)
        b = subprocess.run(["/usr/bin/mpicc.%s" % a.mpi, "-O0", "-g", *cflags(a.suite, src), "-o", os.path.join(d, "main"),
                            src, "-lm"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return {"name": name, "np": np, "expect": expect, "build": "ok" if b.returncode == 0 else "CE", "source": src}
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        rows = list(ex.map(one, programs(a.suite, a.select, a.limit)))
    with open(os.path.join(a.out, a.suite + ".csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("%s: %d programs, %d built, into %s" % (a.suite, len(rows), sum(r["build"] == "ok" for r in rows), a.out))
    return 0


def must(a):
    """One MUST run per program of the build's table, as scripts/must_run.sh runs one, inside this
    container: the program's source compiled by MUST's run with the MPI's mpicc."""
    with open(a.table, newline="") as fh:
        todo = [r for r in csv.DictReader(fh) if r["build"] == "ok" and re.search(a.select, r["name"])]
    # the layouts MUST builds at run time, per MPI library and analysis
    cache = os.path.join(a.cache, a.mpi + ("-tsan" if a.tsan else ""))
    env = dict(os.environ, STRETTO_MUST_DIR="/opt/must/%s" % a.mpi, STRETTO_MUST_CC="mpicc.%s" % a.mpi,
               STRETTO_MUST_MPIEXEC="/usr/bin/mpirun.openmpi", STRETTO_MUST_LAYOUT="--must:distributed --must:fanin 4",
               XDG_CACHE_HOME=cache, OMP_NUM_THREADS="2")
    if a.tsan:
        # MUST_DISABLE_TSAN_ONREPORT as scripts/must_run.sh sets it for clang 14 and later; PMIx's
        # shared-memory store maps its segment where ThreadSanitizer's shadow lies (a SEGV in
        # pmix_gds_shmem_fetch under MPI_Init_thread), so the store is PMIx's hash one
        env.update(STRETTO_MUST_TSAN="1", MUST_DISABLE_TSAN_ONREPORT="1", PMIX_MCA_gds="hash")
        if a.mpi == "openmpi":
            # Open MPI's shared-memory transport hangs these runs at random (a correct two-rank
            # MBI program: 5 of 7 runs past 60 s, a-p13, 24 Nov 2026); over TCP 12 of 12 ended in 2 s
            env["OMPI_MCA_btl"] = "self,tcp"
    column = "must1112tsan" if a.tsan else "must1112"
    commit = os.environ.get("STRETTO_COMMIT", "")

    def one(r, timeout=None):
        w = tempfile.mkdtemp(prefix="suite-must-")
        try:
            os.makedirs(os.path.join(w, "src"))
            src = r["source"]
            for h in os.listdir(os.path.dirname(src)):
                if h.endswith(".h"):
                    shutil.copy(os.path.join(os.path.dirname(src), h), w)
            shutil.copy(src, os.path.join(w, "src", "prog.c"))
            e = dict(env, STRETTO_MUST_CFLAGS=" ".join(cflags(a.suite, src)))
            t0 = time.monotonic()
            p = subprocess.run([os.path.join(ROOT, "scripts", "must_once.sh"), str(r["np"]), str(timeout or a.timeout)],
                               cwd=os.path.join(w, "src"), env=e, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               universal_newlines=True)
            v = (p.stdout.strip().splitlines() or ["HARNESS-ERROR:must_once.sh printed nothing"])[-1]
            return {"name": r["name"], "expect": r["expect"], "np": r["np"], column: "MUST:" + v,
                    "seconds": round(time.monotonic() - t0, 1), "commit": commit}
        finally:
            shutil.rmtree(w, ignore_errors=True)
    os.makedirs(cache, exist_ok=True)
    # the warm-up (amendment 6): MUST's first run of a process count builds its layout, which a
    # timed run must not pay; one pass builds at a time (the lock), the others then find it built
    with open(os.path.join(cache, ".warmup.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for np_ in sorted(set(r["np"] for r in todo), key=int):
            mark = os.path.join(cache, ".warm-np%s" % np_)
            if not os.path.exists(mark):
                # a correct program where there is one: an erroneous one may hang until the warm-up's cap
                same = [r for r in todo if r["np"] == np_]
                got = one(next((r for r in same if r["expect"] == "OK"), same[0]), timeout=WARMUP_TIMEOUT)
                print("warm-up np %s: %s, %.1f s (uncounted)" % (np_, got[column], got["seconds"]), flush=True)
                if not got[column].startswith("MUST:HARNESS-ERROR") and got[column] != "MUST:TIMEOUT":
                    open(mark, "w").close()
        fcntl.flock(lock, fcntl.LOCK_UN)
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        rows = list(ex.map(one, todo))
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["name", "expect", "np", column, "seconds", "commit"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("MUST on %d programs of %s into %s" % (len(rows), a.table, a.out))
    return 0


def fetch_corrbench(a):
    if not os.path.isdir(os.path.join(a.dir, ".git")):
        subprocess.run(["git", "clone", "-q", CORRBENCH_URL, a.dir], check=True)
    subprocess.run(["git", "-C", a.dir, "checkout", "-q", CORRBENCH_COMMIT], check=True)
    print("%s at %s" % (a.dir, CORRBENCH_COMMIT))
    return 0


def regex(a):
    """A regular expression that selects exactly the programs --select and --limit choose: by name
    (run_mbi.py --select, stress_baseline.py, ninja/run.py corpus) or by path relative to the
    suite's directory (corrbench/run.py --select, stretto_bench.sh ONLY)."""
    if a.select == "." and not a.limit and not a.correct and a.suite not in ("mbi", "mbb"):
        print(".")       # every program; MBI's and MPI-BugBench's are the committed tables' programs, named
        return 0
    ps = programs(a.suite, a.select, a.limit, a.correct)
    if a.form == "name":
        items = [re.escape(n) for n, _, _, _ in ps]
    else:
        items = [re.escape(os.path.relpath(src, SOURCES[a.suite])) for _, src, _, _ in ps]
    print("^(%s)$" % "|".join(items) if items else "^$")
    return 0


def program_name(r):
    """The suite name of a script table's row: its name, or its program's path as a name."""
    name = r.get("name") or r.get("program") or ""
    if name.endswith(".c") or name.endswith(".cpp"):
        name = name.rsplit(".", 1)[0].replace("/", "__")
    return name


def missing(expected, table_rows):
    """The programs of expected that no row of the table is of."""
    have = set(program_name(r) for r in table_rows)
    return [n for n in expected if n not in have]


def check(a):
    expected = [n for n, _, _, _ in programs(a.suite, a.select, a.limit, a.correct)]
    if a.built:
        with open(a.built, newline="") as fh:
            ok = set(r["name"] for r in csv.DictReader(fh) if r["build"] == "ok")
        expected = [n for n in expected if n in ok]
    with open(a.table, newline="") as fh:
        gone = missing(expected, csv.DictReader(fh))
    if gone:
        print("HARNESS-ERROR: %s has %d of the pass's %d programs; missing %s" % (
            a.table, len(expected) - len(gone), len(expected), " ".join(gone[:5])), file=sys.stderr)
        return 3
    print("%s: every one of the pass's %d programs" % (a.table, len(expected)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch-corrbench")
    f.add_argument("dir")
    b = sub.add_parser("build")
    m = sub.add_parser("must")
    for p in (b, m):
        p.add_argument("--suite", required=True, choices=sorted(SOURCES))
        p.add_argument("--mpi", default="openmpi", choices=("openmpi", "mpich"))
        p.add_argument("--select", default=".")
        p.add_argument("--jobs", type=int, default=2)
        p.add_argument("--out", required=True)
    b.add_argument("--limit", type=int, default=0)
    m.add_argument("--table", required=True)
    m.add_argument("--timeout", type=int, default=60)
    m.add_argument("--cache", default=os.path.join(os.environ.get("PROTOCOL_STATE", "/state"), "must-cache"))
    m.add_argument("--tsan", action="store_true", help="MUST's ThreadSanitizer analysis, column must1112tsan")
    r = sub.add_parser("regex")
    r.add_argument("--suite", required=True, choices=sorted(SOURCES))
    r.add_argument("--select", default=".")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--form", default="name", choices=("name", "path"))
    r.add_argument("--correct", action="store_true", help="only the programs the suite labels correct (a calibration)")
    c = sub.add_parser("check")
    c.add_argument("--suite", required=True, choices=sorted(SOURCES))
    c.add_argument("--table", required=True)
    c.add_argument("--select", default=".")
    c.add_argument("--limit", type=int, default=0)
    c.add_argument("--correct", action="store_true")
    c.add_argument("--built", default=None, help="the build's table: only the programs it compiled")
    a = ap.parse_args()
    return {"fetch-corrbench": fetch_corrbench, "build": build, "must": must, "regex": regex, "check": check}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
