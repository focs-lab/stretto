"""The subjects of the uniform campaign and their configurations (experiments/protocol/RULE.md,
Section 4), as the campaign's runner (run.py) and plan (plan.py) read them.

Each configuration names its process count, its MPI library and, for the version with the bug
("bug") and for its fix ("fix"), the program and its arguments as the subject's committed scripts
build and run them (experiments/<subject>/run.sh; paths under the repository root, where the
containers mount it at /w). The judges are the committed ones:

  ok        a regular expression a correct run's output (stdout and stderr) matches
  judge     (file, args): judge(stdout, *args) of a committed judge returns "ok" for a correct output
  learn_mark  a regular expression: NINJA's learning run counts when its output has it, though the run
            did not end correctly (MFEM's first mesh done: experiments/mfem332/run.sh --learn-mark)
  output    a function of the run's directory that returns "ok" or what is wrong with the file the
            program wrote (deal.II's test log, normalised as deal.II's harness does)

and the bug's symptom (S of Section 3) is decided from how the run ended:

  hang      a run past its timeout shows the bug (HYPRE's, Cabana's, LAMMPS's and SuperLU_DIST's
            hangs); on any other subject it is a tool failure (RULE.md, Section 2.1)
  wrong     a finished run with a wrong output shows the bug (NPB MG's norm, deal.II's log, the
            NBX answers, MFEM's rows, SuperLU_DIST's x, Quantum ESPRESSO's table)
  abort     a regular expression: a run that ended badly with this in its output shows the bug
            (stempy's cereal exception; MFEM's "integer set not found" and the truncation of a receive
            that takes the next mesh's longer row); a run that ended badly
            otherwise is a tool failure

Stretto's report names the bug (F) when the analysis of the run's trace has an alternative of the
bug's kind at its site: `stretto` is a function (trace, report) -> the findings of such
alternatives, from the subject's committed classifier (the replays of Section 6 replay them), and `sp` says whether the analysis runs the
single-path explorer (Cabana's deadlock is an L1+SP finding; the others are L1 alternatives).
MUST's report names the bug when one of its messages is about one of the racing calls (`must_calls`)
at the racing site (`must_site`, matched against the message's text, its locations and stack
included): the judgement is by call site, not by the kind of error (RULE.md, Section 2.3).
"""
import importlib.util
import os
import re
import runpy
import subprocess

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
APPS = os.path.join(ROOT, "third_party", "apps")


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ------------------------------------------------------------------ Stretto's classifiers

def kinds_count(path, kind="cross"):
    """A classifier file's alternatives(t, rep) -> [(finding, kind)], those of kind."""
    def f(t, rep):
        return [x for x, k in runpy.run_path(os.path.join(ROOT, path))["alternatives"](t, rep) if k == kind]
    return f


def hypre_requests(tags):
    def f(t, rep):
        m = _module("experiments/hypre/predict.py", "hypre_predict")
        return [x for x, k in m.request_alternatives(t, rep, tags) if k == "cross"]
    return f


def parasails_levels(t, rep):
    m = _module("experiments/hypre3/predict.py", "hypre3_predict")
    return [x for x, k, _ in m.classify(t, rep) if k == "cross"]


def dealii_rpe(t, rep):
    m = _module("experiments/dealii/predict.py", "dealii_predict")
    found, _ = m.classify(t, rep, 750)
    return [x for x, k in found if k == "cross"]


def npb_mg(t, rep):
    """NPB MG 2.4.1: every receive of one exchange has its own tag and its one sender, so every
    alternative the analysis accepts is a message of another exchange (experiments/npbmg/predict.py)."""
    return [f for f in rep.findings if f.level == "L1" and f.recv is not None and f.send is not None]


def deadlock(t, rep):
    """Cabana: a deadlock (class 1d) the explorer reaches from an alternative (L1+SP)."""
    return [f for f in rep.findings if f.cls == "1d" and f.level != "observed"]


def buffering(t, rep):
    """LAMMPS: the 2-buffering finding of the blocking count sends (experiments/lammps/predict.py)."""
    return [f for f in rep.findings if f.cls == "2-buffering"]


# ------------------------------------------------------------------ output judges

MG_REFERENCE = 0.5307707005734e-04          # NPB 3.3.1's verification value, class S (experiments/tables.py)
MG_NORM = re.compile(r"L2 Norm is\s+(\S+)")


def mg_judge(stdout):
    """NPB MG class S: the residual norm within 1e-8 (relative) of the reference, the check of
    later NPB versions (experiments/must/once.sh mgnorm, experiments/tables.py)."""
    m = MG_NORM.search(stdout)
    if not m:
        return "no norm"
    try:
        v = float(m.group(1).replace("D", "E"))
    except ValueError:
        return "norm " + m.group(1)
    return "ok" if abs(v - MG_REFERENCE) / MG_REFERENCE <= 1e-8 else "norm " + m.group(1)


def dealii_output(version, np):
    """deal.II's test log in the run's directory, normalised by deal.II's normalize.pl, against the
    expected output of that version and process count (experiments/dealii/predict.py)."""
    src = os.path.join(APPS, "dealii", "dealii-" + version)
    expected = os.path.join(src, "tests", "remote_point_evaluation", "mapping_01.mpirun=%d.output" % np)
    normalize = os.path.join(src, "cmake", "scripts", "normalize.pl")

    def f(wd):
        path = os.path.join(wd, "output")
        if not os.path.exists(path):
            return "no output"
        got = subprocess.run(["perl", "-p", normalize, path], stdout=subprocess.PIPE, universal_newlines=True).stdout
        with open(expected) as fh:
            want = fh.read()
        x, y = got.splitlines(), want.splitlines()
        d = sum(1 for i in range(max(len(x), len(y))) if i >= len(x) or i >= len(y) or x[i] != y[i])
        return "ok" if d == 0 else "%d lines differ" % d
    return f


# ------------------------------------------------------------------ the subjects

def _a(*parts):
    return os.path.join(APPS, *parts)


PS = ["-m", "48", "-n", "48", "-pc_type", "hypre", "-pc_hypre_type", "parasails",
      "-pc_hypre_parasails_nlevels", "2", "-pc_hypre_parasails_thresh", "0.01"]
CABANA = ["--gtest_filter=CommPlan.Test10NoTopo"]
LAMMPS = ["-in", os.path.join(ROOT, "experiments", "lammps", "in.displace"), "-log", "none"]
LAMMPS_BUG, LAMMPS_FIX = "3a2da51a8210970a2382185f003fd512c12bada9", "411c069ba66fb10b0e1121d0499b7e888cac9c80"
SLU = {"nos2": ("1x3x1", "nos2.mtx"), "mhd3200b": ("1x3x1", "mhd3200b.mtx"), "blk12x6": ("1x3x1", "blk12x6.mtx"),
       "blk8x10": ("2x3x1", "blk8x10.mtx"), "g20": ("1x3x1", "g20.rua")}
QE_RUN = _a("qeelph", "run", "k39")
QE_REF = _a("qeelph", "run", "ref-nk1.out")
# Open MPI's eager threshold forced down to 0 payload bytes, as the corpora's stress testing does
# (experiments/b_impl.py tcp(), as environment variables so that every tool's launcher passes them)
EAGER0 = {"OMPI_MCA_btl": "self,tcp", "OMPI_MCA_btl_tcp_if_include": "lo"}


def subjects():
    """{subject: {"mpi", "configs": {config: {...}}, ...}}; see the module's docstring."""
    S = {}
    S["hypre"] = dict(
        title="HYPRE 2.10.1 / 2.11.0", mpi="mpich", hang=True, ok=r"Final Relative Residual Norm",
        stretto={"bug": hypre_requests((222,)), "fix": hypre_requests((221, 222))}, sp=False,
        must_calls=r"MPI_(I)?[Pp]robe", must_site=r"ParaSails|ReceiveRequest|ExchangePrunedRows|ExchangeStoredRows",
        versions={"bug": ("2.10.1", _a("hypre", "hypre-2.10.1", "test", "ij")),
                  "fix": ("2.11.0", _a("hypre", "hypre-2.11.0", "src", "test", "ij"))},
        args=["-solver", "8", "-pout", "0"], configs={"np4": {"np": 4}, "np8": {"np": 8}})
    S["hypre3"] = dict(
        title="HYPRE 3.2.0 ex5, two levels / fix", mpi="openmpi", hang=True, ok=r"Final Relative Residual Norm",
        stretto=parasails_levels, sp=False,
        must_calls=r"MPI_(I)?[Pp]robe", must_site=r"ParaSails|ReceiveRequest|ExchangePrunedRows",
        versions={"bug": ("3.2.0", _a("hypre3", "hypre-3.2.0", "src", "examples", "ex5_levels")),
                  "fix": ("3.2.0-fix", _a("hypre3", "hypre-3.2.0-fix", "src", "examples", "ex5_levels"))},
        args=["-solver", "8", "-sai_levels", "2", "-n", "33"], configs={"np4": {"np": 4}, "np8": {"np": 8}})
    S["petsc"] = dict(
        title="PETSc ex2 on HYPRE 3.2.0, two levels / fix", mpi="openmpi", hang=True, ok=r"[Ii]terations *[0-9]+",
        stretto=parasails_levels, sp=False,
        must_calls=r"MPI_(I)?[Pp]robe", must_site=r"ParaSails|ReceiveRequest|ExchangePrunedRows",
        versions={"bug": ("3.26.0", _a("petsc", "petsc-3.26.0", "src", "ksp", "ksp", "tutorials", "ex2")),
                  "fix": ("3.26.0-hypre-fix", _a("petsc", "tutorials-fix", "ex2"))},
        args=PS, configs={"np4": {"np": 4}, "np8": {"np": 8}}, memory_per_rank=3)
    S["npbmg"] = dict(
        title="NPB MG 2.4.1 / 3.3.1", mpi="mpich", wrong=True, judge=mg_judge, fortran=True,
        stretto=npb_mg, sp=False,
        must_calls=r"MPI_(Irecv|Wait|Waitall|Recv)", must_site=r"mg\.f|ready|give3|take3|comm3",
        versions={"bug": ("2.4.1", _a("npb-mg", "NPB2.4.1", "NPB2.4-MPI", "bin", "mg.S.{np}")),
                  "fix": ("3.3.1", _a("npb-mg", "NPB3.3.1", "NPB3.3-MPI", "bin", "mg.S.{np}"))},
        args=[], configs={"np16": {"np": 16}, "np32": {"np": 32, "alone": True}}, memory_per_rank=1)
    S["dealii"] = dict(
        title="deal.II 9.3.0-rc1 / 9.3.0", mpi="mpich", wrong=True,
        output={"bug": dealii_output("9.3.0-rc1", 4), "fix": dealii_output("9.3.0", 4)},
        stretto=dealii_rpe, sp=False,
        must_calls=r"MPI_(I)?[Pp]robe|MPI_(I)?[Rr]ecv", must_site=r"remote_point_evaluation|RemotePointEvaluation",
        versions={"bug": ("9.3.0-rc1", _a("dealii", "drivers-9.3.0-rc1", "mapping_01")),
                  "fix": ("9.3.0", _a("dealii", "drivers-9.3.0", "mapping_01"))},
        args=[], configs={"np4": {"np": 4}})
    S["dealii8929"] = dict(
        title="deal.II NBX (issue 8929) / fix", mpi="openmpi", wrong=True,
        ok=r"nbx_calls: [0-9]+ calls on [0-9]+ ranks, 0 wrong answers: ok",
        stretto=kinds_count("experiments/dealii8929/classify.py"), sp=False,
        must_calls=r"MPI_(I)?[Pp]robe", must_site=r"ConsensusAlgorithm|NBX|mpi_compute_index_owner|consensus",
        versions={"bug": ("2c1b27fb", _a("dealii8929", "driver-2c1b27fb", "nbx_calls")),
                  "fix": ("f9ea3396", _a("dealii8929", "driver-f9ea3396", "nbx_calls"))},
        args=["3"], configs={"np4": {"np": 4}, "np8": {"np": 8}})
    S["cabana"] = dict(
        title="Cabana before / after PR 835", mpi="openmpi", hang=True, ok=r"\[  PASSED  \] 1 test\.",
        stretto=deadlock, sp=True,
        must_calls=r"MPI_(I)?[Rr]ecv|MPI_(I)?[Pp]robe", must_site=r"CommunicationPlan|createFromImports|createFromExports",
        versions={"bug": ("fbbcc37f", _a("cabana", "build-fbbcc37f", "core", "unit_test", "Cabana_Core_Test_CommunicationPlan_MPI_SERIAL")),
                  "fix": ("38170631", _a("cabana", "build-38170631", "core", "unit_test", "Cabana_Core_Test_CommunicationPlan_MPI_SERIAL"))},
        args=CABANA, configs={"np3": {"np": 3}, "np4": {"np": 4},
                              "np3-repeat2": {"np": 3, "args": CABANA + ["--gtest_repeat=2"], "supplementary": True},
                              "np4-repeat2": {"np": 4, "args": CABANA + ["--gtest_repeat=2"], "supplementary": True}})
    S["stempy"] = dict(
        title="stempy 3.3.5 / 3.3.6", mpi="openmpi", wrong=True, abort=r"Failed to read|cereal", ok=r"gather ok",
        stretto=kinds_count("experiments/stempy/classify.py"), sp=False,
        must_calls=r"MPI_(I)?[Rr]ecv|MPI_(I)?[Pp]robe", must_site=r"gatherEvents|electron|harness",
        versions={"bug": ("3.3.5", _a("stempy", "harness-3.3.5")), "fix": ("3.3.6", _a("stempy", "harness-3.3.6"))},
        args=["256", "12", "5"], configs={"np4": {"np": 4}, "np8": {"np": 8}})
    S["mfem332"] = dict(
        title="MFEM 3.3.2 / 3.4", mpi="openmpi", wrong=True, abort=r"integer set not found|WRONG|MPI_ERR_TRUNCATE",
        ok=r"(?s)mesh 1:[^\n]* ok.*mesh 2:[^\n]* ok", learn_mark=r"mesh 1:[^\n]* ok",
        stretto=kinds_count("experiments/mfem332/classify.py"), sp=False,
        must_calls=r"MPI_(I)?[Rr]ecv", must_site=r"GroupTopology|communication\.cpp",
        versions={"bug": ("3.3.2", _a("mfem332", "twomesh-3.3.2")), "fix": ("3.4", _a("mfem332", "twomesh-3.4"))},
        args=[], configs={"np4": {"np": 4}, "np8": {"np": 8}})
    S["lammps"] = dict(
        title="LAMMPS before / after the March 2017 fix", mpi="openmpi", hang=True, ok=r"Total wall time",
        stretto=buffering, sp=False,
        must_calls=r"MPI_(Send|Recv|Irecv|Wait)", must_site=r"[Ii]rregular|create_atom",
        versions={"bug": (LAMMPS_BUG[:8], _a("lammps", "lammps-" + LAMMPS_BUG, "src", "lmp_mpi")),
                  "fix": (LAMMPS_FIX[:8], _a("lammps", "lammps-" + LAMMPS_FIX, "src", "lmp_mpi"))},
        args=LAMMPS, configs={"np4": {"np": 4}, "np8": {"np": 8},
                              "np4-eager0": {"np": 4, "eager": 0, "supplementary": True},
                              "np8-eager0": {"np": 8, "eager": 0, "supplementary": True}})
    slu = {}
    for mpi in ("openmpi", "mpich"):
        for m in ("nos2", "mhd3200b", "blk12x6", "blk8x10", "g20"):
            grid, f = SLU[m]
            r, c, d = (int(x) for x in grid.split("x"))
            slu["%s-%s" % (m, mpi)] = {"np": r * c * d, "mpi": mpi, "supplementary": m == "g20",
                                       "args": ["-r", str(r), "-c", str(c), "-d", str(d), _a("superlu3d", "mats", f)]}
    S["superlu3d"] = dict(
        title="SuperLU_DIST 9.3.1 / fix", mpi=None, hang=True, wrong=True,
        judge=("experiments/superlu3d/judge.py", []),
        stretto=kinds_count("experiments/superlu3d/classify.py"), sp=False,
        must_calls=r"MPI_(I)?[Rr]ecv", must_site=r"trs|solve|pdgstrs|lsum",
        versions={"bug": ("9f7c819", _a("superlu3d", "build-{mpi}-9f7c819", "EXAMPLE", "pddrive3d")),
                  "fix": ("9f7c819-barrier", _a("superlu3d", "build-{mpi}-9f7c819-barrier", "EXAMPLE", "pddrive3d"))},
        args=[], configs=slu, env={"OMP_NUM_THREADS": "1"})
    S["qeelph"] = dict(
        title="Quantum ESPRESSO elph_prt / fix", mpi="mpich", wrong=True, fortran=True,
        judge=("experiments/qeelph/judge.py", [QE_REF]), base=QE_RUN,
        stretto=kinds_count("experiments/qeelph/classify.py"), sp=False,
        must_calls=r"MPI_(I)?[Rr]ecv|mpi_recv", must_site=r"elph|elphon|mp_",
        versions={"bug": ("615e40c", _a("qeelph", "build-615e40c", "bin", "ph.x")),
                  "fix": ("615e40c-spintags", _a("qeelph", "build-615e40c-spintags", "bin", "ph.x"))},
        args=["-nk", "3", "-in", "ph.in"], configs={"np3": {"np": 3}}, env={"OMP_NUM_THREADS": "1"})
    for name, s in S.items():
        s["name"] = name
        for c in s["configs"].values():
            c.setdefault("mpi", s["mpi"])
    return S


def program(s, config, version):
    """(executable, arguments) of a configuration's version."""
    c = s["configs"][config]
    exe = s["versions"][version][1].format(np=c["np"], mpi=c["mpi"])
    return exe, list(c.get("args", s["args"]))


def env_of(s, config):
    """The environment every tool's run of the configuration gets (OMP_NUM_THREADS, the eager limit)."""
    c = s["configs"][config]
    env = dict(s.get("env", {}))
    if "eager" in c:
        sys_path = os.path.join(ROOT, "experiments")
        import sys
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        from b_impl import tcp_header
        limit = str(c["eager"] + (tcp_header() or 0))
        env.update(EAGER0)
        env.update({"OMPI_MCA_btl_tcp_eager_limit": limit, "OMPI_MCA_btl_self_eager_limit": limit})
    return env
