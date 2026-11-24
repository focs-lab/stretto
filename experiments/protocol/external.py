"""The jobs of plan.csv whose runs the committed scripts make (runner "external"): the suites
(experiments/protocol/suites.py), the thread campaigns, MUST's vector-clock checks, the L1+L lock
replays and the MPI-PRV attempts. schedule.py places them like every other job; each is one
container on its own threads, the committed script inside it, made to write one pass (a suite) or
one run of every cell (a thread campaign) into a table of its own, which adapters.py then appends
to the job's row file with the common columns and the outcome (RULE.md Sections 3 and 7).

Where the builds of the thread campaigns and MUST-vc are lies outside the repository: MACHINE_ENV
(machine-<MACHINE>.env beside this file) names them, as paths under the home directory.

spec(job, ctx) -> {"image", "cmd" (a bash command inside the image, cwd /w), "mounts" [(host,
container, mode)], "env" {}, "user" (uid:gid or "root"), "docker" [more options], "src" (the
script's table, a path under /state), "kind" (adapters.py's), "extra" [adapters options],
"needs" [(block, key)]}.

Python 3.6 runs this file (schedule.py imports it).
"""
import os
import shlex

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE_SEED = 20261124          # stress_baseline.py's seed of pass k is SUITE_SEED + k
SUITE_CAL_CAP = 300            # seconds per program run of a suite's calibration (amendment 15)
NINJA_LIB = "libninja-fixed-all.so"
MPIPRV_IMAGE = "mpiprv/mpiprv@sha256:af980da29375ffca673d1e5a16aa8c1d06a9d69211e79b6f948a137ba95c43cf"
MPIPRV_STEP = {"hypre": "hypre", "hypre3": "hypre", "petsc": "hypre", "npbmg": "mg", "dealii": "dealii",
               "dealii8929": "dealii8929", "cabana": "cabana", "stempy": "stempy", "mfem332": "mfem332",
               "lammps": "lammps", "superlu3d": "superlu", "qeelph": "qe"}


# every rank on its own hardware thread (RULE.md amendment 8), as run.py binds the applications'
BINDING = {"OMPI_MCA_hwloc_base_use_hwthreads_as_cpus": "1", "OMPI_MCA_rmaps_base_mapping_policy": "hwthread",
           "OMPI_MCA_hwloc_base_binding_policy": "hwthread", "OMPI_MCA_hwloc_base_mem_bind_failure_action": "silent",
           "OMPI_MCA_btl_vader_single_copy_mechanism": "none"}


class MissingBuild(RuntimeError):
    """An external job's build or input is not where MACHINE_ENV says."""


def machine(name=None):
    """The machine's build locations (KEY=VALUE lines, $HOME expanded)."""
    name = name or os.environ.get("MACHINE", "")
    path = os.environ.get("MACHINE_ENV") or os.path.join(HERE, "machine-%s.env" % name)
    env = {}
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = os.path.expandvars(v.strip())
    return env


def q(x):
    return shlex.quote(str(x))


# The cells of the thread campaigns' blocks by the names their scripts give them (TIMEOUTS of
# experiments/thread_timeouts.sh), each with its T (amendment 20); MUST-vc's runs without MUST as
# native:<cell>. The first cell is the concurrency check's (plain-serial); the replay cell the
# traced cell whose T the replays of threadreplays.py take.
THREAD_CELLS = {
    "slate/series": ["wide-2x2", "wide-4x1", "square-2x2", "wide-2x2-1thread"],
    "slate/traced": ["wide-2x2", "wide-4x1", "square-2x2", "partial-2x2", "mt9-2x2"],
    "dlaf/series": ["2x1-offset", "2x1-offset-jitter", "2x1-blocks-jitter", "4x1-offset-jitter", "3x2-offset-jitter",
                    "2x2-offset-jitter"],
    "dlaf/traced": ["2x1-offset-jitter", "4x1-offset-jitter", "3x2-offset-jitter", "2x2-offset-jitter",
                    "2x1-offset-1thread"],
    "parsec/series": ["vp2-mt", "vp2-serialized", "vp1-mt", "vp2-mt-4ranks", "shipped-vp2-mt"],
    "parsec/traced": ["vp2-mt", "vp2-mt-4ranks", "vp2-serialized", "vp1-mt"],
    # every committed cell (amendment 30): np 4 with 2 and 4 threads, np 2 with 2, and the MPI
    # scheduler's control at np 4 ("np4": no -nthreads)
    "uintah/series": ["np4-nthreads2", "np4-nthreads4", "np2-nthreads2", "np4"],
}
MUSTVC_CELLS = {"slate": ["wide-2x2", "wide-2x2-compat", "wide-4x1-compat"], "parsec": ["vp2-mt", "vp2-mt-4ranks"],
                "dlaf": ["2x1-offset-jitter", "4x1-offset-jitter"], "uintah": ["np4-t4", "np4-t2"]}
for _app, _cells in MUSTVC_CELLS.items():
    THREAD_CELLS["mustvc/" + _app] = _cells + ["native:" + c for c in _cells]
UINTAH_SETTINGS = "4 -nthreads 2;4 -nthreads 4;2 -nthreads 2;4"       # series.sh SETTINGS: THREAD_CELLS' Uintah cells
REPLAY_CELL = {"slate": "wide-4x1", "parsec": "vp2-mt", "dlaf": "2x1-offset-jitter"}


def target_block(j):
    """The block whose cells' T a thread job takes (thread-timeouts.csv): a calibration's own
    target, the concurrency check's series, the replays' traced cells."""
    b, subj = j["block"], j["subject"]
    if "/calibrate-" in b:
        return b.replace("/calibrate-", "/")
    if j.get("external") == "thread" and j.get("setting") == "serial":
        return "%s/series-openmpi" % subj
    if j.get("external") == "threadreplay":
        return "%s/traced-openmpi" % subj
    return b


def cells_of(j):
    """The cells a thread job needs a T for."""
    t = target_block(j)
    if t.startswith("mustvc/"):
        return THREAD_CELLS[t]
    subj, rest = t.split("/", 1)
    cells = THREAD_CELLS["%s/%s" % (subj, rest.split("-", 1)[0])]
    if j.get("setting") == "serial":
        return cells[:1]
    if j.get("external") == "threadreplay":
        return [REPLAY_CELL[subj]]
    return cells


def thread_timeouts(j, env):
    """TIMEOUTS (or a calibration's CAP) of experiments/thread_timeouts.sh for the job's T: a dict
    per cell, or one number (a calibration, or --timeout)."""
    T = j.get("T")
    if isinstance(T, dict):
        env["TIMEOUTS"] = " ".join("%s=%d" % (c, T[c]) for c in sorted(T))
    elif T not in (None, ""):
        env["CAP"] = str(int(T))


def calibrating(j, s, jd):
    """A calibration job of a thread block: the fixed variant only, RUNS=1, the traced runs not
    analysed, each run's seconds into its own table."""
    s["env"].update({"VARIANTS": "fix" if j["subject"] == "uintah" and not j["block"].startswith("mustvc/") else "fixed",
                     "ANALYSE": "0", "SECONDS_OUT": jd + "/seconds.csv"})
    s["extra"] += ["--set", "calibrated=" + target_block(j)]
    s["cmd"] = "mkdir -p %s && echo name,variant,run,seconds,rc > %s/seconds.csv && %s" % (jd, jd, s["cmd"])
    s["src"], s["kind"] = jd + "/seconds.csv", "none"


def suite_spec(j, ctx):
    """One pass of one tool on one suite under one MPI library, or the suite's build. The selection
    (ctx select and limit: every program in the campaign, a handful in the smoke test) is the build's
    for the tools that run its binaries, and the same programs, by suites.py regex, for Stretto's
    scripts, which build their own."""
    suite, mpi = j["subject"], j["mpi"]
    tool, setting = j["tool"], j["setting"]
    base = "/state/suites/%s-%s" % (suite, mpi)
    jd = "%s/%s-%s-%02d" % (base, tool, setting, j["run"])
    build = base + "/build"
    table = "%s/%s.csv" % (build, suite)
    sel, limit = ctx.get("select", "."), ctx.get("limit", 0)
    # a calibration runs the programs labelled correct once, at most 300 s each; a pass, at the
    # suite's T for the tool (amendment 15, calibrate.py derive-suites)
    cal = j.get("calibration", False)
    T = int(j["T"]) if not cal and str(j.get("T", "")).isdigit() else SUITE_CAL_CAP    # a build has none
    if cal:
        jd = "%s/calibrate-%s-%s" % (base, tool, setting)

    def regex(form):
        return '"$(python3 experiments/protocol/suites.py regex --suite %s --select %s --limit %d --form %s%s)"' % (
            suite, q(sel), limit, form, " --correct" if cal else "")
    jobs = 1
    s = {"image": ctx["exp_image"], "env": {"OMP_NUM_THREADS": "2"}, "needs": [], "extra": [],
         "annotate": ["--suite", suite]}
    if suite != "corrbench-openmp":
        s["env"].update(ctx["binding"])          # amendment 8
    else:
        # the OpenMP programs' threads stay unbound; every hardware thread is a slot, so that MUST's
        # tool processes fit beside the ranks
        s["env"].update({"OMPI_MCA_hwloc_base_use_hwthreads_as_cpus": "1", "OMPI_MCA_hwloc_base_binding_policy": "none",
                         "OMPI_MCA_btl_vader_single_copy_mechanism": "none"})
    if tool == "build":
        s["annotate"] = []
        s["cmd"] = "python3 experiments/protocol/suites.py build --suite %s --mpi %s --out %s --select %s --limit %d --jobs 4" % (
            suite, mpi, build, q(sel), limit)
        s["src"], s["kind"] = table, "none"
        return s
    if cal:
        s["extra"] = ["--set", "calibrated=%s:%s" % (tool, setting)]
    if tool == "stretto":
        if suite in ("mbi", "mbb"):
            rerun = " --rerun" if setting == "L2-reexecution" else ""
            s["cmd"] = ("python3 experiments/mbi/run_mbi.py --corpus %s --out %s/%s.csv --jobs %d --timeout %d --select %s%s"
                        % (suite, jd, suite, jobs, T, regex("name"), rerun))
            s["src"], s["kind"] = "%s/%s.csv" % (jd, suite), "corpus"
        elif suite == "corrbench":
            s["cmd"] = ("REPS=1 TIMEOUT=%d python3 experiments/corrbench/run.py --mpi %s --select %s --jobs %d "
                        "third_party/corrbench/micro-benches/0-level %s/run.csv %s/work" % (T, mpi, regex("path"), jobs, jd, jd))
            s["src"], s["kind"] = jd + "/run.csv", "corrbench"
        else:
            # attempted under MPICH too (amendment 9 of the review: a cell that cannot run says so from its attempt)
            s["cmd"] = ("REPS=1 TIMEOUT=%d MPI=%s ONLY=%s experiments/mustvc/stretto_bench.sh corrbench "
                        "third_party/corrbench/micro-benches/0-level/openmp %s/bench.csv %s/work" % (T, mpi, regex("path"), jd, jd))
            s["src"], s["kind"] = jd + "/bench.csv", "bench"
        s["cmd"] = "mkdir -p %s && %s" % (jd, s["cmd"])
        return s
    s["needs"] = [("%s/%s-build" % (suite, mpi), "%s|%s|-|build|corpus|0" % (suite, j["config"]))]
    budget = setting.endswith("-budget")
    mode = setting[:-len("-budget")] if budget else setting
    runs = 30 if budget else 1
    pick = " --select %s" % regex("name") if cal else ""
    if tool == "delays":
        # the committed per-program seeding, Random("<seed>:<program>"), with the pass's seed recorded (amendment 22)
        s["extra"] += ["--set", "seed=%d" % (SUITE_SEED + j["run"])]
        s["cmd"] = ("python3 experiments/stress_baseline.py %s --mpi %s --runs %d --jobs %d --timeout %d --seed %d --out %s/stress.csv%s"
                    % (table, mpi, runs, jobs, T, SUITE_SEED + j["run"], jd, pick))
        s["src"], s["kind"] = jd + "/stress.csv", "stress"
    elif tool == "ninja":
        extra = " --rand-ratio 10 --rand-delay 50000" if mode == "random" else ""
        s["env"]["NINJA_LIBRARY"] = NINJA_LIB
        s["cmd"] = ("python3 experiments/ninja/run.py corpus %s --mode %s%s --mpi %s --runs %d --jobs %d --timeout %d --out %s/ninja.csv%s"
                    % (table, mode, extra, mpi, runs, jobs, T, jd, pick))
        s["src"], s["kind"] = jd + "/ninja.csv", "stress"
    elif tool == "must":
        s["image"] = ctx["must_image"]
        tsan = setting.endswith("-tsan")
        s["cmd"] = ("python3 experiments/protocol/suites.py must --suite %s --mpi %s --table %s --out %s/must.csv --timeout %d --jobs %d%s%s"
                    % (suite, mpi, table, jd, T, jobs, " --tsan" if tsan else "", pick))
        s["src"], s["kind"] = jd + "/must.csv", "must"
        s["annotate"] += ["--must-column", "must1112tsan" if tsan else "must1112"]
    else:
        raise ValueError("no suite tool %s" % tool)
    s["cmd"] = "mkdir -p %s && %s" % (jd, s["cmd"])
    return s


def thread_spec(j, ctx):
    """One run of every cell of a thread campaign's block (the script with RUNS=1; the run number
    is the job's)."""
    m, subj, k = ctx["machine"], j["subject"], j["run"]
    jd = "/state/threads/%s/%s-%02d" % (subj, j["block"].split("/")[1], k)
    s = {"env": {"RUNS": "1", "STRETTO_COMMIT": ctx["commit"]}, "needs": [], "extra": ["--set", "run=%d" % k], "mounts": []}
    tb = target_block(j).split("/")[1]
    kind, mpi = tb.split("-", 1) if "-" in tb else (tb, "")
    thread_timeouts(j, s["env"])
    serial = j.get("setting") == "serial"
    if serial:
        # the concurrency check (amendment 20): the first cell's plain variant, alone on the machine
        s["env"].update({"ONLY": cells_of(j)[0], "VARIANTS": "plain"})
    if subj == "slate":
        # experiments/slate/build-lib.sh's output under SLATE_HOME, mounted at /h where it was built
        # (the driver's runpath names its build directory)
        s["image"] = m.get("SLATE_IMAGE", "hunt-slate:24.04")
        s["mounts"] = [(m.get("SLATE_HOME", ""), "/h", "ro")]
        sfx = "-mpich" if mpi == "mpich" else ""
        lib, d = "/h/lib" + sfx, "/h/wide_lu" + sfx
        if kind == "series":
            s["cmd"] = "experiments/slate/series.sh %s %s %s/series.csv" % (lib, d, jd)
            s["src"], s["kind"] = jd + "/series.csv", "series"
        elif kind == "traced":
            s["env"].update({"FIRST": str(k), "JOBS": "4"})
            s["cmd"] = "experiments/slate/traced_cells.sh %s %s %s/work %s/traced.csv" % (lib, d, jd, jd)
            s["src"], s["kind"] = jd + "/traced.csv", "traced"
    elif subj in ("dlaf", "parsec"):
        p = subj.upper()
        s["image"] = m.get(p + "_IMAGE", "atm-clang:24.04")
        s["mounts"] = [(m.get(p + "_BUILD", ""), "/h", "ro")]
        if m.get(p + "_LD_LIBRARY_PATH"):
            s["env"]["LD_LIBRARY_PATH"] = m[p + "_LD_LIBRARY_PATH"]
        b = m.get(p + "_BUILD_IN", "/h")
        if kind == "series":
            s["cmd"] = "experiments/%s/series.sh %s %s/series.csv" % (subj, b, jd)
            s["src"], s["kind"] = jd + "/series.csv", "series"
        elif kind == "traced":
            s["cmd"] = "experiments/%s/traced.sh %s %s/work %s/traced.csv" % (subj, b, jd, jd)
            s["src"], s["kind"] = jd + "/traced.csv", "traced"
    elif subj == "uintah":
        # the builds' roots are mounted where they were built (their runpaths name those paths)
        s["image"] = m.get("UINTAH_IMAGE", "stretto-uintah:24.04")
        pre = "UINTAH_MPICH_" if mpi == "mpich" else "UINTAH_"
        inside = m.get(pre + "ROOT_IN", "/u")
        s["mounts"] = [tuple(x.split(":", 1)) + ("ro",) for x in m.get(pre + "MOUNTS", "").split(",") if x]
        s["mounts"].append((os.path.dirname(m.get("UINTAH_UPS", "")), "/u-ups", "ro"))
        ups = "/u-ups/" + os.path.basename(m.get("UINTAH_UPS", ""))
        s["env"].update({"MPI": mpi or "openmpi", "MPIRUN": "/usr/bin/mpirun.openmpi"})
        s["cmd"] = ("experiments/uintah/series.sh %s/series.csv %s plain=%s/%s fix=%s/%s"
                    % (jd, ups, inside, m.get(pre + "PLAIN_SUS", "plain/StandAlone/sus"), inside,
                       m.get(pre + "FIX_SUS", "fix/StandAlone/sus")))
        s["env"]["SETTINGS"] = "4 -nthreads 2" if serial else UINTAH_SETTINGS
        s["src"], s["kind"] = jd + "/series.csv", "series"
    else:
        raise ValueError("no thread subject %s" % subj)
    s["cmd"] = "mkdir -p %s && %s" % (jd, s["cmd"])
    if j.get("calibration"):
        calibrating(j, s, jd)
    return s


def threadreplay_spec(j, ctx):
    """Amendment 17's replays (threadreplays.py): a traced run of the replay cell, the enumeration,
    or one alternative's replays in one gate mode, in the subject's image with its build."""
    m, subj, k, setting = ctx["machine"], j["subject"], j["run"], j["setting"]
    jd = "/state/threadreplays/%s/jobs/%s-%02d" % (subj, setting, k)
    s = {"needs": [], "extra": [], "env": {"STRETTO_COMMIT": ctx["commit"]}}
    if subj == "slate":
        s["image"], s["mounts"] = m.get("SLATE_IMAGE", "hunt-slate:24.04"), [(m.get("SLATE_HOME", ""), "/h", "ro")]
    else:
        p = subj.upper()
        s["image"], s["mounts"] = m.get(p + "_IMAGE", "atm-clang:24.04"), [(m.get(p + "_BUILD", ""), "/h", "ro")]
        if m.get(p + "_LD_LIBRARY_PATH"):
            s["env"]["LD_LIBRARY_PATH"] = m[p + "_LD_LIBRARY_PATH"]
    t = "python3 experiments/protocol/threadreplays.py"
    T = j.get("T")
    T = T[REPLAY_CELL[subj]] if isinstance(T, dict) else (T or 0)
    if setting == "replay-trace":
        s["cmd"], s["kind"] = "%s trace --subject %s --run %d --timeout %d --out %s/trace.csv" % (t, subj, k, int(T), jd), "none"
        s["src"] = jd + "/trace.csv"
    elif setting == "replay-enumerate":
        s["cmd"], s["kind"] = "%s enumerate --subject %s --out %s/enumerate.csv" % (t, subj, jd), "none"
        s["src"] = jd + "/enumerate.csv"
    else:
        s["cmd"] = "%s replay --subject %s --index %d --mode %s --timeout %d --out %s/replay.csv" % (
            t, subj, k, setting.split("-", 1)[1], int(T), jd)
        s["src"], s["kind"] = jd + "/replay.csv", "threadreplay"
    s["cmd"] = "mkdir -p %s && %s" % (jd, s["cmd"])
    return s


def mustvc_spec(j, ctx):
    m, app, k = ctx["machine"], j["subject"], j["run"]
    jd = "/state/mustvc/%s-%s%02d" % (app, "calibrate-" if j.get("calibration") else "", k)
    mvc = m.get("MUSTVC_WORK", "")
    s = {"image": m.get("MUSTVC_IMAGE", "mustvc:1.9-omp"), "needs": [], "extra": ["--set", "run=%d" % k],
         "mounts": [(mvc, "/mvc", "rw")], "env": {"RUNS": "1", "WARMUP": "1" if k == 1 else "0", "STRETTO_COMMIT": ctx["commit"]}}
    if app in ("parsec", "dlaf"):
        s["mounts"].append((os.path.join(mvc, app), "/h", "ro"))
    if app == "uintah":
        s["env"]["WARMUP"] = "0"         # its MUST runs all time out (apps.sh)
    thread_timeouts(j, s["env"])
    s["cmd"] = "mkdir -p %s && experiments/mustvc/apps.sh %s %s/apps.csv %s/work" % (jd, app, jd, jd)
    s["src"], s["kind"] = jd + "/apps.csv", "mustvc"
    if j.get("calibration"):
        calibrating(j, s, jd)
    return s


def locks_spec(j, ctx):
    m = ctx["machine"]
    jd = "/state/locks/run%02d" % j["run"]
    return {"image": ctx["exp_image"], "needs": [], "extra": [],
            "env": {"STRETTO_COMMIT": ctx["commit"], "TIMEOUT": str(int(j.get("T") or 0))},
            "mounts": [(m.get("LOCKS_CORRBENCH_OPENMP", ""), "/l/cb", "ro"), (m.get("LOCKS_ARTIFACT_TESTS", ""), "/l/at", "ro")],
            "cmd": "mkdir -p %s && make -s -C idioms MPI=openmpi && experiments/mustvc/lock_replays.sh /l/cb /l/at %s/work %s/locks.csv"
                   % (jd, jd, jd),
            "src": jd + "/locks.csv", "kind": "threadreplay"}


def mpiprv_spec(j, ctx):
    jd = "/state/mpiprv/%s" % j["subject"]
    step = MPIPRV_STEP[j["subject"]]
    return {"image": MPIPRV_IMAGE, "user": "root", "docker": ["--cap-add", "SYS_PTRACE"], "needs": [], "extra": [],
            "mounts": [], "env": {"MUST_APPS_DIR": "/w/third_party/apps", "HIST_APPS_DIR": "/w/third_party/apps",
                                  "MPIPRV_OUT": jd + "/attempt.csv", "STRETTO_COMMIT": ctx["commit"],
                                  "TIMEOUT": str(int(j.get("T") or 0)),
                                  "PYTHONDONTWRITEBYTECODE": "1"},   # the image runs as root: nothing of root's in /w
            "cmd": "source /root/.bashrc; mkdir -p %s && rm -f %s/attempt.csv && experiments/mpiprv/stretto_apps.sh %s; rc=$?; chown -R %s /state/mpiprv /w/experiments/mpiprv/work 2>/dev/null; exit $rc"
                   % (jd, jd, step, ctx["owner"]),
            "src": jd + "/attempt.csv", "kind": "mpiprv"}


def spec(j, ctx):
    k = j["external"]
    if k == "suite":
        return suite_spec(j, ctx)
    if k == "thread":
        return thread_spec(j, ctx)
    if k == "threadreplay":
        return threadreplay_spec(j, ctx)
    if k == "mustvc":
        return mustvc_spec(j, ctx)
    if k == "locks":
        return locks_spec(j, ctx)
    if k == "mpiprv":
        return mpiprv_spec(j, ctx)
    raise ValueError("no external kind %s" % k)
