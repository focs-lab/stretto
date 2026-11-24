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
    the fan-in run.py gives it (must_layout: 4, and 16 at 32 ranks), measured 24 Nov 2026 in
    stretto-must-u:24.04 by mustrun's own count (must_temp/mustrun.info)."""
    return np + MUST_EXTRA[np]


MUST_EXTRA = {3: 2, 4: 2, 6: 3, 8: 3, 16: 5, 32: 3}


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
                                notes="T = max(30 s, 10 x the slowest of these runs of the fix)"))
                n, notes = (NONOISE_RUNS, "library loaded, no send delayed") if setting == "nonoise" else \
                    (N, "T calibrated (calibrate.py)")
                for version in ("bug", "fix"):
                    out.append(dict(base, block="%s/%s" % (name, block_name(tool, setting)), version=version, tool=tool,
                                    setting=setting, n=n, table=kind or table, cpus=cpus, image=image, runner="run.py",
                                    notes=notes))
                    if tool == "ninja" and setting == "app":
                        out.append(dict(base, block="%s/ninja-learn" % name, version=version, tool="ninja", setting="learn",
                                        n=1, table="prerequisite", cpus=cpus, image=image, runner="run.py",
                                        notes="system-centric learning run (T of ninja-system), up to 3 tries; "
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
                    "table": "main", "cpus": configs[first]["np"], "alone": 0, "image": "mpiprv/mpiprv",
                    "runner": "experiments/mpiprv/apps.py",
                    "notes": "attempted on every subject, Fortran ones included; build or instrumentation failure recorded"})
    return [r for r in out if r]


SUITES = [("mbi", 115, ["openmpi"]), ("mbb", 388, ["openmpi"]), ("corrbench", 513, ["openmpi", "mpich"]),
          ("corrbench-openmp", 103, ["openmpi", "mpich"])]
SUITE_TOOLS = [("stretto", "traced"), ("delays", "D50"), ("ninja", "random"), ("ninja", "system"), ("ninja", "app"),
               ("must", "1.11.2")]
SUITE_RUNNERS = {"stretto": "experiments/run_remaining.sh corpus (one traced run per program per pass)",
                 "delays": "experiments/stress_baseline.py --runs 1 per pass; --runs 30 for the budget",
                 "ninja": "experiments/ninja/run.py corpus --runs 1 per pass; --runs 30 for the budget",
                 "must": "experiments/mbi/run_must.py (MUST_IMAGE=stretto-must-u:24.04)"}


def suite_rows():
    out = []
    for suite, programs, mpis in SUITES:
        for mpi in mpis:
            for tool, setting in SUITE_TOOLS:
                image = MUST_IMAGE if tool == "must" else EXP
                b = {"subject": suite, "config": "%d programs" % programs, "np": "", "mpi": mpi, "version": "-",
                     "tool": tool, "setting": setting, "cpus": "np of each program", "alone": 0, "image": image,
                     "runner": SUITE_RUNNERS[tool]}
                out.append(dict(b, block="%s/%s-%s-%s" % (suite, mpi, tool, setting), n=PASSES,
                                table="main" if (tool, setting) != ("ninja", "system") and (tool, setting) != ("ninja", "app")
                                else "appendix",
                                notes="R = 10 passes, one run per program per pass; per-run view and F1 per pass"))
                if tool in ("delays", "ninja"):
                    out.append(dict(b, block="%s/%s-%s-%s-budget" % (suite, mpi, tool, setting), n=BUDGET, table="main",
                                    notes="budget view: up to 30 runs per program, stopping at the first failure"))
    out.append({"block": "mbi/openmpi-l2", "subject": "mbi", "config": "115 programs", "np": "", "mpi": "openmpi",
                "version": "-", "tool": "stretto", "setting": "L2-reexecution", "n": PASSES, "table": "main",
                "cpus": "np of each program", "alone": 0, "image": EXP, "runner": "experiments/mbi/reanalyze.py (L2 by re-execution)",
                "notes": "MBI's headline stays at L1; L2 by re-execution is a row of its own"})
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


THREADS = [("slate", "experiments/slate/series.csv", ("cell", "variant"), "series", "openmpi", "hunt-slate:24.04",
            "experiments/slate/series.sh"),
           ("slate", "experiments/slate/series-mpich.csv", ("cell", "variant"), "series", "mpich", "hunt-slate:24.04",
            "experiments/slate/series.sh"),
           ("slate", "experiments/slate/traced-cells-16.csv", ("cell", "variant"), "traced", "openmpi", "hunt-slate:24.04",
            "experiments/slate/traced_cells.sh"),
           ("dlaf", "experiments/dlaf/series.csv", ("cell", "variant"), "series", "openmpi", "hunt2-dlaf:24.04",
            "experiments/dlaf/series.sh"),
           ("dlaf", "experiments/dlaf/traced.csv", ("cell", "variant"), "traced", "openmpi", "hunt2-dlaf:24.04",
            "experiments/dlaf/traced.sh"),
           ("parsec", "experiments/parsec/series.csv", ("cell", "variant"), "series", "openmpi", "hunt2-parsec:24.04",
            "experiments/parsec/series.sh"),
           ("parsec", "experiments/parsec/traced.csv", ("cell", "variant"), "traced", "openmpi", "hunt2-parsec:24.04",
            "experiments/parsec/traced.sh"),
           ("uintah", "experiments/uintah/series.csv", ("mpi", "build", "np", "args"), "series", None, "uintah image",
            "experiments/uintah/series.sh")]


def thread_rows():
    out = []
    for subject, path, keys, kind, mpi, image, runner in THREADS:
        for k in _cells(path, keys):
            if subject == "uintah":
                cell, variant, m = "np%s %s" % (k[2], k[3]), k[1], k[0]
            else:
                cell, variant, m = k[0], k[1], mpi
            version = "bug" if variant == "plain" else "fix"
            tool = "stretto" if kind == "traced" else "plain"
            setting = "traced" if kind == "traced" else "series"
            out.append({"block": "%s/%s-%s" % (subject, kind, m), "subject": subject, "config": cell, "np": "",
                        "mpi": m, "version": version if variant in ("plain", "fixed", "fix") else variant,
                        "tool": tool, "setting": setting, "n": N, "table": "main", "cpus": "ranks x threads", "alone": 0,
                        "image": image, "runner": runner,
                        "notes": "cell as committed (%s, variant %s)" % (path, variant)})
            if kind == "traced" and variant == "plain":
                for mode, table in (("after", "main"), ("before", "appendix"), ("yield", "appendix"), ("both", "appendix")):
                    out.append({"block": "%s/replay-%s" % (subject, mode), "subject": subject, "config": cell, "np": "",
                                "mpi": m, "version": "bug", "tool": "stretto", "setting": "replay-" + mode, "n": REPLAYS,
                                "table": table, "cpus": "ranks x threads", "alone": 0, "image": image,
                                "runner": "experiments/replay_modes.sh",
                                "notes": "gates give way after 1 000 ms; the control follows the recorded order beyond the horizon"})
    for subject in ("uintah", "slate", "dlaf", "parsec"):
        out.append({"block": "mustvc/%s" % subject, "subject": subject, "config": "the thread bug's cells", "np": "",
                    "mpi": "as above", "version": "bug", "tool": "mustvc", "setting": "vector-clock", "n": N,
                    "table": "main", "cpus": "ranks x threads", "alone": 0, "image": "mustvc:1.9-omp",
                    "runner": "experiments/mustvc/apps.sh",
                    "notes": "MUST's vector-clock checks on the cells of the four thread bugs"})
    out.append({"block": "locks/replay", "subject": "locks", "config": "every L1+L finding", "np": "", "mpi": "",
                "version": "bug", "tool": "stretto", "setting": "replay-L1+L", "n": 2, "table": "main", "cpus": "",
                "alone": 0, "image": EXP, "runner": "experiments/mustvc/lock_replays.sh",
                "notes": "two replays per finding, as committed"})
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
