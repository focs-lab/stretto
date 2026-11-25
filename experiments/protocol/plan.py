#!/usr/bin/env python3
"""The campaign's plan, generated from RULE.md Sections 1.9, 2, 4, 5 and 6: one row per subject,
configuration, version, tool and setting, with its N, written to experiments/protocol/plan.csv.

Columns:
  block      the table its rows go to, results/<block>.csv (schedule.py)
  subject, config, np, mpi, version (bug, fix, or "-" where a row has none)
  tool, setting    as experiments/protocol/run.py names them; "calibrate" rows are the timeout
             calibration of Section 2.1 (calibrate.py; setting "tool:setting" of the runs it times),
             "replay" rows the replays of Section 6
  n          runs (passes for a suite, attempts for MPI-PRV; for replays the most alternatives)
  table      main (the paper's main table), appendix, supplementary (a configuration of Section 4
             marked supplementary), check (the concurrency check of Section 1.9, the no-noise
             runs of Section 2.4), prerequisite (NINJA's learning run, the calibration)
  cpus       hardware threads one job takes (MUST's tool processes included); alone: the job runs
             with nothing beside it
  image      the container image
  runner     run.py (one job per run), or the committed script that runs the row
  notes

usage: plan.py [--out experiments/protocol/plan.csv] [--check]
       --check: exit 1 when the committed plan.csv differs from what this script generates
Python 3.6 runs this file (the experiment machine's host).
"""
import argparse
import csv
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import subjects as subjects_mod  # noqa: E402

N = 40                    # Section 1.2
CALIBRATION_RUNS = 10     # Section 2.1
NONOISE_RUNS = 10         # Section 2.4
PASSES = 10               # Section 5
BUDGET = 30               # Section 5, the budget view
REPLAYS = 40              # Section 6
EXP, MUST_IMAGE = "stretto-exp:24.04", "stretto-must-u:24.04"
FIELDS = ["block", "subject", "config", "np", "mpi", "version", "tool", "setting", "n", "table", "cpus", "alone",
          "image", "runner", "notes"]
# app tools and settings: (tool, setting, table)
APP_SETTINGS = [("plain", "-", "main"), ("delays", "D50", "main"), ("ninja", "random", "main"),
                ("ninja", "system", "appendix"), ("ninja", "app", "appendix"), ("must", "1.11.2", "main"),
                ("stretto", "traced", "main")]


def block_name(tool, setting):
    return tool if setting == "-" else "%s-%s" % (tool, setting)


def must_procs(np):
    """The processes of a mustrun at np ranks: MUST's distributed layout adds its tool processes, at
    the fan-in run.py gives it (must_layout: 4, and the centralised layout at 32 ranks), measured 24 Nov 2026 in
    stretto-must-u:24.04 by mustrun's own count (must_temp/mustrun.info)."""
    return np + MUST_EXTRA[np]


MUST_EXTRA = {3: 2, 4: 2, 6: 3, 8: 3, 16: 5, 32: 1}     # 32 ranks: the centralised layout (amendment 1)


def app_rows():
    out = []
    for name, s in subjects_mod.subjects().items():
        configs = s["configs"]
        first = next(c for c, v in configs.items() if not v.get("supplementary"))
        for config, c in configs.items():
            np, mpi = c["np"], c["mpi"]
            alone = bool(c.get("alone"))
            kind = "supplementary" if c.get("supplementary") else None
            base = {"subject": name, "config": config, "np": np, "mpi": mpi, "alone": int(alone)}
            settings = list(APP_SETTINGS)
            if name == "hypre":
                settings.append(("ninja", "random-1.2s", "appendix"))
            settings.append(("ninja", "nonoise", "check"))
            for tool, setting, table in settings:
                cpus = must_procs(np) if tool == "must" else np
                image = MUST_IMAGE if tool == "must" else EXP
                out.append(dict(base, block="%s/calibrate" % name, version="fix", tool="calibrate",
                                setting="%s:%s" % (tool, setting), n=CALIBRATION_RUNS, table="prerequisite",
                                cpus=cpus, image=image, runner="calibrate.py",
                                notes="T = max(30 s, 10 x the slowest of these runs of the fix); cannot run if "
                                      "the first 3 reach the 1 800 s cap (amendment 34)"))
                n, notes = (NONOISE_RUNS, "library loaded, no send delayed") if setting == "nonoise" else \
                    (N, "T calibrated (calibrate.py)")
                for version in ("bug", "fix"):
                    out.append(dict(base, block="%s/%s" % (name, block_name(tool, setting)), version=version, tool=tool,
                                    setting=setting, n=n, table=kind or table, cpus=cpus, image=image, runner="run.py",
                                    notes=notes))
                    if tool == "must" and version == "fix":
                        out.append(dict(base, block="%s/must-warmup" % name, version="fix", tool="must", setting="warmup",
                                        n=1, table="prerequisite", cpus=cpus, image=image, runner="run.py",
                                        notes="uncounted: MUST's first run per process count and library builds its "
                                              "layout (amendment 6)"))
                    if tool == "ninja" and setting == "app":
                        out.append(dict(base, block="%s/ninja-learn" % name, version=version, tool="ninja", setting="learn",
                                        n=1, table="prerequisite", cpus=cpus, image=image, runner="run.py",
                                        notes="system-centric learning run (1 800 s cap), up to 3 tries; "
                                              "the app runs start from its files"))
            if config == first:
                out.append(dict(base, block="%s/plain-serial" % name, version="bug", tool="plain", setting="serial", n=N,
                                table="check", cpus=np, alone=1, image=EXP, runner="run.py",
                                notes="jobs = 1, beside the 40 parallel plain runs; Fisher's exact test"))
            out.append(dict(base, block="%s/replay" % name, version="bug", tool="stretto", setting="replay", n=REPLAYS,
                            table="main", cpus=np, image=EXP, runner="replays.py",
                            notes="every distinct alternative by its pins over the configuration's traces, a seeded sample "
                                  "of 40 if more; each with its control; only if the traces predict the bug"))
        out.append({"block": "%s/mpiprv" % name, "subject": name, "config": first, "np": configs[first]["np"],
                    "mpi": configs[first]["mpi"], "version": "bug", "tool": "mpiprv", "setting": "attempt", "n": 1,
                    "table": "main", "cpus": 4, "alone": 0, "image": "mpiprv/mpiprv",
                    "runner": "external:mpiprv",
                    "notes": "experiments/mpiprv/stretto_apps.sh in MPI-PRV's image: attempted on every subject, Fortran "
                             "ones included; a build or instrumentation failure is recorded"})
    return [r for r in out if r]


SUITES = [("mbi", 115, ["openmpi"], 4), ("mbb", 388, ["openmpi"], 4), ("corrbench", 513, ["openmpi", "mpich"], 2),
          ("corrbench-openmp", 103, ["openmpi", "mpich"], 4)]
SUITE_TOOLS = [("stretto", "traced"), ("delays", "D50"), ("ninja", "random"), ("ninja", "system"), ("ninja", "app"),
               ("must", "1.11.2"), ("must", "1.11.2-tsan")]
# MUST with ThreadSanitizer on the programs with threads, and on MBI as its committed column (amendment 16)
MUST_TSAN_SUITES = ("mbi", "corrbench-openmp")
SUITE_RUNNERS = {"stretto": "run_mbi.py (MBI, MPI-BugBench), corrbench/run.py, mustvc/stretto_bench.sh (OpenMP)",
                 "delays": "stress_baseline.py on the suite's build, D50",
                 "ninja": "ninja/run.py corpus on the suite's build, libninja-fixed-all.so",
                 "must": "suites.py must (scripts/must_once.sh in stretto-must-u:24.04; 1.11.2-tsan with --tsan)",
                 "build": "suites.py build: every program compiled for the MPI"}


def suite_table(suite, tool, setting):
    """The table a suite block's cell goes to (amendment 21): NINJA's system-centric and
    application-centric modes, budget view included, in the appendix; MUST with ThreadSanitizer the
    main cell on CorrBench's OpenMP programs and without it the appendix there, the other way round
    on MBI."""
    mode = setting[:-len("-budget")] if setting.endswith("-budget") else setting
    if tool == "ninja" and mode in ("system", "app"):
        return "appendix"
    if tool == "must" and suite == "corrbench-openmp":
        return "main" if mode.endswith("-tsan") else "appendix"
    if tool == "must" and mode.endswith("-tsan"):
        return "appendix"
    return "main"


def suite_rows():
    """Section 5: R = 10 passes of every tool on every suite (a job is one pass: one run per
    program), the budget view of the delays and of each NINJA mode (one job: up to 30 runs per
    program, stopping at the first failure), MBI's L2 by re-execution as a row of its own; each
    suite and MPI built once for the tools that run its binaries. Stretto on CorrBench's OpenMP
    programs is attempted under both MPIs (stretto_bench.sh with MPI=mpich: a cell that cannot run
    says so from its attempt); MUST with ThreadSanitizer on MBI and CorrBench's OpenMP programs
    (amendment 16), the main cell on the OpenMP ones and the appendix on MBI (amendment 21). Before the passes,
    each tool setting runs every program the suite labels correct once, at most 300 s each, the
    calibration of the suite's T per tool (amendment 15, calibrate.py derive-suites)."""
    out = []
    for suite, programs, mpis, cpus in SUITES:
        for mpi in mpis:
            b = {"subject": suite, "config": "%d programs" % programs, "np": "", "mpi": mpi, "version": "-", "alone": 0,
                 "runner": "external:suite"}
            out.append(dict(b, block="%s/%s-build" % (suite, mpi), tool="build", setting="corpus", n=1, table="prerequisite",
                            cpus=cpus, image=EXP, notes=SUITE_RUNNERS["build"]))
            for tool, setting in SUITE_TOOLS:
                if setting.endswith("-tsan") and suite not in MUST_TSAN_SUITES:
                    continue
                image = MUST_IMAGE if tool == "must" else EXP
                c = cpus + 2 if tool == "must" else cpus
                out.append(dict(b, block="%s/%s-calibrate" % (suite, mpi), tool="calibrate", setting="%s:%s" % (tool, setting),
                                n=1, table="prerequisite", cpus=c, image=image,
                                notes="the programs labelled correct once, 300 s at most each (amendment 15)"))
                table = suite_table(suite, tool, setting)
                out.append(dict(b, block="%s/%s-%s-%s" % (suite, mpi, tool, setting), tool=tool, setting=setting, n=PASSES,
                                table=table, cpus=c, image=image,
                                notes="R = 10 passes, one run per program per pass (%s)" % SUITE_RUNNERS[tool]))
                if tool in ("delays", "ninja"):
                    out.append(dict(b, block="%s/%s-%s-%s-budget" % (suite, mpi, tool, setting), tool=tool,
                                    setting=setting + "-budget", n=1, table=table, cpus=c, image=image,
                                    notes="budget view: up to 30 runs per program, stopping at the first failure"))
    out.append({"block": "mbi/openmpi-stretto-L2", "subject": "mbi", "config": "115 programs", "np": "", "mpi": "openmpi",
                "version": "-", "tool": "stretto", "setting": "L2-reexecution", "n": PASSES, "table": "main", "cpus": 4,
                "alone": 0, "image": EXP, "runner": "external:suite",
                "notes": "MBI's headline stays at L1; L2 by re-execution (run_mbi.py --rerun) is a row of its own"})
    return out


def _cells(path, keys):
    """The distinct cells of a committed table: the tuples of its columns keys, in first-seen order."""
    seen = []
    with open(os.path.join(ROOT, path), newline="") as fh:
        for r in csv.DictReader(fh):
            k = tuple(r[c] for c in keys)
            if k not in seen:
                seen.append(k)
    return seen


# subject, committed table (its cells), kind, MPI, threads a job takes, image, script
THREADS = [("slate", "experiments/slate/series.csv", ("cell", "variant"), "series", "openmpi", 16, "hunt-slate:24.04",
            "experiments/slate/series.sh"),
           ("slate", "experiments/slate/series-mpich.csv", ("cell", "variant"), "series", "mpich", 16, "hunt-slate:24.04",
            "experiments/slate/series.sh"),
           ("slate", "experiments/slate/traced-cells-16.csv", ("cell", "variant"), "traced", "openmpi", 16, "hunt-slate:24.04",
            "experiments/slate/traced_cells.sh"),
           ("dlaf", "experiments/dlaf/series.csv", ("cell", "variant"), "series", "openmpi", 12, "atm-clang:24.04",
            "experiments/dlaf/series.sh"),
           ("dlaf", "experiments/dlaf/traced.csv", ("cell", "variant"), "traced", "openmpi", 12, "atm-clang:24.04",
            "experiments/dlaf/traced.sh"),
           ("parsec", "experiments/parsec/series.csv", ("cell", "variant"), "series", "openmpi", 8, "atm-clang:24.04",
            "experiments/parsec/series.sh"),
           ("parsec", "experiments/parsec/traced.csv", ("cell", "variant"), "traced", "openmpi", 8, "atm-clang:24.04",
            "experiments/parsec/traced.sh"),
           ("uintah", "experiments/uintah/series.csv", ("mpi", "build", "np", "args"), "series", "openmpi", 16,
            "stretto-uintah:24.04", "experiments/uintah/series.sh"),
           ("uintah", "experiments/uintah/series.csv", ("mpi", "build", "np", "args"), "series", "mpich", 16,
            "stretto-uintah:24.04", "experiments/uintah/series.sh")]
# the replays of amendment 17 (threadreplays.py): traced runs of the replay cell (SLATE's count as
# the committed campaign's 16, of which the right ones count; 5 for the others, 15 to 20
# alternatives each), the distinct alternatives over them, a sample of 40, in every gate mode
REPLAY_TRACES = {"slate": 16, "parsec": 5, "dlaf": 5}
REPLAY_MODES = {"slate": ("after", "before", "yield", "both"), "parsec": ("after", "before", "yield", "both"),
                "dlaf": ("after",)}
REPLAY_CPUS = {"slate": 16, "parsec": 6, "dlaf": 8}


def thread_rows():
    """The thread campaigns: one job is one run of every cell of a block (the committed script with
    RUNS=1, the job's number as the run), 40 runs; the replays of amendment 17 (threadreplays.py) in
    each gate mode (DLA-Future's has the default, after, only); MUST-vc on the four bugs' cells; the
    L1+L lock replays, two per finding, as committed."""
    out = []
    for subject, path, keys, kind, mpi, cpus, image, runner in THREADS:
        tool, setting = ("stretto", "traced") if kind == "traced" else ("plain", "series")
        # amendment 20: T per cell from 10 runs of its fixed (or, for a control cell, plain) variant
        out.append({"block": "%s/calibrate-%s-%s" % (subject, kind, mpi), "subject": subject, "config": "cells as committed",
                    "np": "", "mpi": mpi, "version": "fix", "tool": "calibrate", "setting": "%s:%s" % (tool, setting),
                    "n": CALIBRATION_RUNS, "table": "prerequisite", "cpus": cpus, "alone": 0, "image": image,
                    "runner": "external:thread",
                    "notes": "%s with VARIANTS=fixed, RUNS=1 per job, 1 800 s cap: T = max(30 s, 10 x the slowest) per "
                             "cell; a cell whose first 3 runs reach the cap cannot run (amendment 34)" % runner})
        if kind == "series" and mpi == "openmpi":
            out.append({"block": "%s/plain-serial" % subject, "subject": subject, "config": "the first cell",
                        "np": "", "mpi": mpi, "version": "bug", "tool": "plain", "setting": "serial", "n": N,
                        "table": "check", "cpus": cpus, "alone": 1, "image": image, "runner": "external:thread",
                        "notes": "%s, the first cell's plain variant alone on the machine (Section 1.9, amendment 20)" % runner})
        cells = []
        for k in _cells(path, keys):
            if subject == "uintah":
                if k[0] != mpi:
                    continue
                cells.append("np%s %s (%s)" % (k[2], k[3], k[1]))
            else:
                cells.append("%s (%s)" % (k[0], k[1]))
        out.append({"block": "%s/%s-%s" % (subject, kind, mpi), "subject": subject, "config": "cells as committed", "np": "",
                    "mpi": mpi, "version": "-", "tool": tool, "setting": setting, "n": N, "table": "main", "cpus": cpus,
                    "alone": 0, "image": image, "runner": "external:thread",
                    "notes": "%s, RUNS=1 per job; cells of %s: %s" % (runner, path, "; ".join(cells))})
        if kind == "traced" and subject in REPLAY_TRACES:
            r = {"subject": subject, "config": "the bug's replay cell", "np": "", "mpi": mpi, "version": "bug",
                 "tool": "stretto", "alone": 0, "image": image, "runner": "external:threadreplay", "cpus": REPLAY_CPUS[subject]}
            out.append(dict(r, block="%s/replay-traces" % subject, setting="replay-trace", n=REPLAY_TRACES[subject],
                            table="prerequisite", notes="threadreplays.py trace: the replay cell traced with its lock order"))
            out.append(dict(r, block="%s/replay-alternatives" % subject, setting="replay-enumerate", n=1, table="prerequisite",
                            notes="threadreplays.py enumerate: the distinct alternatives over the traces, a seeded sample of 40"))
            for mode in REPLAY_MODES[subject]:
                out.append(dict(r, block="%s/replay-%s" % (subject, mode), setting="replay-" + mode, n=REPLAYS,
                                table="main" if mode == "after" else "appendix",
                                notes="threadreplays.py replay: each alternative twice with its control, gate mode %s%s"
                                      % (mode, "" if len(REPLAY_MODES[subject]) > 1 else " (its replay.sh's one mode)")))
    for subject, cpus in (("uintah", 18), ("slate", 18), ("dlaf", 14), ("parsec", 10)):
        out.append({"block": "mustvc/calibrate-%s" % subject, "subject": subject, "config": "the thread bug's cells",
                    "np": "", "mpi": "mpich", "version": "fix", "tool": "calibrate", "setting": "mustvc:vector-clock",
                    "n": CALIBRATION_RUNS, "table": "prerequisite", "cpus": cpus, "alone": 0, "image": "mustvc:1.9-omp",
                    "runner": "external:mustvc",
                    "notes": "experiments/mustvc/apps.sh with VARIANTS=fixed: T per cell, with MUST-vc and without; "
                             "a cell whose first 3 MUST-vc runs reach the 1 800 s cap cannot run (amendment 34: Uintah, "
                             "at most 3 x 1 800 s a cell)"})
        out.append({"block": "mustvc/%s" % subject, "subject": subject, "config": "the thread bug's cells", "np": "",
                    "mpi": "mpich", "version": "-", "tool": "mustvc", "setting": "vector-clock", "n": N,
                    "table": "main", "cpus": cpus, "alone": 0, "image": "mustvc:1.9-omp", "runner": "external:mustvc",
                    "notes": "experiments/mustvc/apps.sh, RUNS=1 per job: each cell once without MUST and once with"})
    out.append({"block": "locks/replay", "subject": "locks", "config": "every L1+L finding", "np": "", "mpi": "openmpi",
                "version": "bug", "tool": "stretto", "setting": "replay-L1+L", "n": 1, "table": "main", "cpus": 8,
                "alone": 0, "image": EXP, "runner": "external:locks",
                "notes": "experiments/mustvc/lock_replays.sh: two replays per finding, as committed (one job)"})
    return out


def plan():
    return app_rows() + suite_rows() + thread_rows()


def render(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(HERE, "plan.csv"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    text = render(plan())
    if a.check:
        with open(a.out) as fh:
            same = fh.read() == text
        print("plan.csv is %s" % ("current" if same else "stale: run experiments/protocol/plan.py"))
        return 0 if same else 1
    with open(a.out, "w") as fh:
        fh.write(text)
    rows = plan()
    runs = sum(int(r["n"]) for r in rows if r["runner"] in ("run.py", "calibrate.py"))
    print("wrote %s: %d rows, %d runs of run.py and calibrate.py" % (a.out, len(rows), runs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
