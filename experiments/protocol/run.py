#!/usr/bin/env python3
"""One run of the uniform campaign (experiments/protocol/RULE.md): one subject, configuration,
version, tool, setting and run number, inside the job's container, on the processors the
scheduler gave it (experiments/protocol/schedule.py), with every rank bound to its own hardware
thread. Writes the run's row as JSON (--row), which the scheduler appends to the block's table.

Tools and settings (RULE.md, Section 2):
  plain   -          the program with nothing loaded (Section 2.5)
  plain   serial     the same, run while no other job runs (the concurrency check, Section 1.9)
  delays  D50        the replay library's random delays before every send and every receive, per
                     rank one of {0, 0, 0, 1, 5, 20, 50} ms drawn once per run from Random(i*100+np)
                     (sends) and Random(i*100+np+7) (receives) (Section 2.2)
  ninja   random     libninja-fixed-all.so (experiments/ninja/build.sh), 10% of the sends by 50 ms
  ninja   random-1.2s   the same by 1.2 s (HYPRE 2.10.1's appendix row)
  ninja   system     system-centric noise
  ninja   learn      the system-centric learning run of the application-centric mode (run 0,
                     retried up to three times), whose files the app runs start from
  ninja   app        application-centric noise
  ninja   nonoise    the library loaded, no send delayed (random mode at 1e-6%, as the
                     ninja-check steps)
  must    1.11.2     mustrun of stretto-must-u:24.04, distributed layout, JSON report
  must    warmup     the same, uncounted: MUST's first run per process count and library builds
                     its layout (RULE.md, amendment 6)
  stretto traced     a traced run, its analysis (3 600 s at most), the subject's classifier;
                     the trace is kept (KEEP_TRACES, stretto.keep). With --defer-analysis (a
                     job that runs alone, RULE.md amendment 14) the run writes its row without
                     the analysis, and analyse-row analyses the kept trace afterwards, as a job
                     of its own on one hardware thread, and writes the row complete

The row: the run's identity, how it ended (stretto.confirm.classify_rc; MUST's FINISHED, CRASHED
or TIMEOUT), its seconds, the judge's verdict (says), symptom (S), reported (F), the tool's own
details, and the common columns of experiments/protocol/rows.py with the outcome of Section 3.
The output of every run that did not end correctly goes to --outputs.

usage: run.py run --subject S --config C --version bug|fix --tool T --setting X --run I
                  --timeout T --row FILE [--state DIR] [--outputs DIR] [--calibration] [--defer-analysis]
       run.py analyse --subject S --version V --trace DIR      (the analysis, in a process of its own)
       run.py analyse-row --deferred FILE --row FILE           (a deferred row's analysis)
"""
import argparse
import json
import os
import random
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rows  # noqa: E402
import subjects as subjects_mod  # noqa: E402

D50 = [0, 0, 0, 1, 5, 20, 50]
ANALYSIS_TIMEOUT = 3600
NINJA_LIB = "libninja-fixed-all.so"
LEARN_TRIES = 3
FIELDS = ["subject", "config", "np", "mpi", "version", "label", "tool", "setting", "run", "result", "seconds",
          "says", "symptom", "reported", "failure", "detail"] + rows.COMMON
# Open MPI's launcher for both libraries, as the committed harness (stretto.confirm.DEFAULT_LAUNCHERS):
# the image's MPICH is built for PMIx, which its own Hydra launcher does not provide (an MPICH program
# under mpiexec.hydra starts as a singleton, "rank 0 (of 1)"); MUST's mustrun is pointed at it too
LAUNCHER = "/usr/bin/mpirun.openmpi"


# MUST's distributed layout, as the earlier MUST campaigns ran it, with its fan-in given: at 8 ranks
# the layout mustrun chooses by itself (two tool processes) reports a collective root mismatch that
# the program does not have (MPI_Bcast in HYPRE_IJMatrixCreate, on HYPRE 3.2.0's fix as on the bug)
# and then hangs, under both MPI libraries; with fan-in 4 (or 2, or the centralised layout) the same
# runs end with the report of the release (24 Nov 2026, stretto-must-u:24.04). Where fan-in 4 needs
# more than the 36 run threads (32 ranks: 43 processes; fan-in 16 crashed there), the centralised
# layout runs instead (RULE.md, amendment 1): one tool process beside the ranks
MUST_FANIN4_EXTRA = {3: 2, 4: 2, 6: 3, 8: 3, 16: 5, 32: 11}     # tool processes, measured
RUN_THREADS = 36


CENTRALISED = "centralised"
# MUST 1.11.2's mustrun has no option that names its centralised layout: it is the layout of a run
# without --must:distributed (and without --must:fanin), and mustrun says so ("centralized checks"),
# which must_result checks for a run that asked for it
CENTRALISED_MARK = "centralized checks"


def must_layout(np):
    """mustrun's layout options and the layout's name: distributed at fan-in 4 where its tool
    processes fit beside the ranks in the run threads, else the centralised one (amendment 1)."""
    if np + MUST_FANIN4_EXTRA.get(np, np // 4 + 2) <= RUN_THREADS:
        return ["--must:distributed", "--must:fanin", "4"], "distributed, fan-in 4"
    return [], CENTRALISED


def must_procs(wd):
    """The processes mustrun started (its layout's levels: the ranks and the tool processes), from
    must_temp/src/layout.xml; None if it wrote none."""
    try:
        with open(os.path.join(wd, "must_temp", "src", "layout.xml")) as fh:
            return sum(int(x) for x in re.findall(r'<level [^>]*size="(\d+)"', fh.read()))
    except (OSError, ValueError):
        return None


def check_must_procs(wd, cpuset):
    """A warm-up's check (amendment 1): mustrun's processes are the job's threads, one each."""
    if not cpuset:
        return
    want, got = len(rows.parse_cpuset(cpuset)), must_procs(wd)
    if got != want:
        raise SystemExit("HARNESS-ERROR: mustrun started %s processes (must_temp/src/layout.xml) for the job's %d threads"
                         % (got, want))


# Open MPI's MPI_ERRORS_ARE_FATAL aborts with the error's class as the exit status, and the
# message it prints can be lost when mpirun takes the other ranks down first: an exit status of
# 15 with no message is MPI_ERR_TRUNCATE (MFEM 3.3.2 under NINJA and in replay controls)
OMPI_ABORT_CLASSES = {15: "MPI_ERR_TRUNCATE"}


def abort_class(result, mpi):
    """classify_rc's result, with an Open MPI abort's lost message restored from its exit status."""
    if mpi == "openmpi" and result.startswith("APP-ERROR:"):
        name = OMPI_ABORT_CLASSES.get(int(result.split(":")[1]))
        if name:
            return "MPI-ERROR:%s" % name
    return result


def launcher_env(mpi):
    """Every rank on its own hardware thread of the container's cpuset, by the launcher's own
    environment variables, so that mustrun's launch binds the same way as every other tool's."""
    return {"OMPI_MCA_hwloc_base_use_hwthreads_as_cpus": "1", "OMPI_MCA_rmaps_base_mapping_policy": "hwthread",
            "OMPI_MCA_hwloc_base_binding_policy": "hwthread", "OMPI_MCA_hwloc_base_mem_bind_failure_action": "silent",
            "OMPI_MCA_btl_vader_single_copy_mechanism": "none"}


def rank_env(mpi, name, value):
    return ["-x", "%s=%s" % (name, value)]


def delays(np, i):
    r1, r2 = random.Random(i * 100 + np), random.Random(i * 100 + np + 7)
    return {"STRETTO_SEND_DELAY_MS": ",".join("%d:%d" % (k, r1.choice(D50)) for k in range(np)),
            "STRETTO_RECV_DELAY_MS": ",".join("%d:%d" % (k, r2.choice(D50)) for k in range(np))}


def ninja_env(setting):
    if setting == "random":
        return {"NIN_PATTERN": "1", "NIN_RAND_RATIO": "10", "NIN_RAND_DELAY": "50000"}
    if setting == "random-1.2s":
        return {"NIN_PATTERN": "1", "NIN_RAND_RATIO": "10", "NIN_RAND_DELAY": "1200000"}
    if setting in ("system", "learn"):
        return {"NIN_PATTERN": "2", "NIN_MODE": "0"}
    if setting == "app":
        return {"NIN_PATTERN": "2", "NIN_MODE": "1"}
    if setting == "nonoise":
        return {"NIN_PATTERN": "1", "NIN_RAND_RATIO": "0.000001", "NIN_RAND_DELAY": "1"}
    raise ValueError("no NINJA setting " + setting)


def must_result(rc, took, timeout, text):
    """How a mustrun ended (experiments/must/once.sh): mustrun exits with the status MUST chose for
    its findings, so a run is finished when mustrun printed its closing line, crashed when the
    launcher reported a process that ended badly."""
    if rc == 124 or (rc in (137, -9) and took >= timeout):
        return "TIMEOUT"
    if rc in (137, -9):
        return "KILLED"
    if "MUST detected a deadlock" in text:
        # MUST stops a program whose ranks it finds deadlocked (MPI_Abort after writing its report):
        # the program hung, and MUST cut the hang short
        return "MUST-DEADLOCK"
    if "BAD TERMINATION OF ONE OF YOUR APPLICATION PROCESSES" in text or "exited on signal" in text \
            or re.search(r"caught signal nr (?!15\b)\d+", text):
        # MUST's own handler reports a rank's fatal signal ("rank 0 (of 4), pid N caught signal nr 11")
        # and mustrun still prints its closing line; signal 15 is MUST ending the other ranks
        return "CRASHED"
    if re.search(r"^\[MUST\] Execution finished", text, re.M):
        return "FINISHED"
    return "rc=%d" % rc


def judge(s, version, config, proc_out, wd):
    """(correct output, the verdict): the subject's ok regex, judge and output judge, as committed."""
    says = "ok"
    if callable(s.get("judge")):
        says = s["judge"](proc_out)
    elif s.get("judge"):
        path, args = s["judge"]
        says = runpy.run_path(os.path.join(ROOT, path))["judge"](proc_out, *args)
    if says == "ok" and s.get("output"):
        says = s["output"][version](wd)
    ok = says == "ok"
    if ok and s.get("ok") and not re.search(s["ok"], proc_out):
        ok, says = False, "no '%s'" % s["ok"][:40]
    return ok, says


def symptom_of(s, result, ok, text):
    """(S, failure): the bug's own symptom, and why the run is a tool failure otherwise ("" if not)."""
    if result == "FINISHED" and ok:
        return False, ""
    if result == "TIMEOUT":
        return (True, "") if s.get("hang") else (False, "timeout" if result == "TIMEOUT" else result)
    if result == "FINISHED":
        return (True, "") if s.get("wrong") else (False, "finished with a wrong output")
    if s.get("abort") and re.search(s["abort"], text + "\n" + result):
        return True, ""
    return False, result


def keep_output(a, name, cmd, rc, out, err, wd=None):
    """The output of a run that did not end correctly, and MUST's report of a MUST run, under --outputs."""
    if not a.outputs:
        return
    os.makedirs(a.outputs, exist_ok=True)
    stem = os.path.join(a.outputs, re.sub(r"[^A-Za-z0-9._=-]+", "_", name))
    with open(stem + ".txt", "w") as fh:
        fh.write("%s\nstatus %s\n--- stdout\n%s\n--- stderr\n%s" % (" ".join(cmd), rc, out[-20000:], err[-20000:]))
    if wd and os.path.exists(os.path.join(wd, "MUST_Output.json")):
        shutil.copy(os.path.join(wd, "MUST_Output.json"), stem + ".must.json")


def oom_kills(path="/sys/fs/cgroup/memory.events"):
    """How often the job's own container hit its memory limit so far (the oom counter of its
    cgroup's memory.events, amendment 33: not oom_kill, which a parent's limit raises too), None if
    unknown."""
    try:
        with open(path) as fh:
            for line in fh:
                k, _, v = line.partition(" ")
                if k == "oom":
                    return int(v)
    except (OSError, ValueError):
        pass
    return None


def execute(cmd, env, cwd, timeout):
    t0 = time.monotonic()
    p = subprocess.run(["/usr/bin/timeout", "-k", "5", str(timeout)] + cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, errors="replace")
    return p, time.monotonic() - t0


def must_report(s, wd):
    """(reported, detail): whether an error of MUST's JSON report is about a racing call at the
    racing site, and the report's errors and warnings as error_id(call)*count. Warnings and
    information (MUST_WARNING_INTEGER_ZERO and the like, at every call site of the programs) are
    listed but report nothing: the call site decides among MUST's errors, whatever their kind."""
    try:
        with open(os.path.join(wd, "MUST_Output.json")) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return False, "no-report"
    msgs = d.get("messages", d if isinstance(d, list) else [])
    counts, hit, call_only = {}, False, False
    for m in msgs:
        kind = str(m.get("type", "")).lower()
        if not kind.startswith(("error", "warning")):
            continue
        call = (m.get("from") or {}).get("call", "") or "?"
        key = "%s(%s)" % (m.get("error_id", "?"), call)
        counts[key] = counts.get(key, 0) + 1
        if not kind.startswith("error"):
            continue
        # the call the message is from and the calls it refers to (a deadlock's cycle), each with its stack
        for e in [m.get("from") or {}] + list(m.get("references") or []):
            if not re.search(s["must_calls"], str(e.get("call", ""))):
                continue
            stack = json.dumps(e.get("stacktrace") or [])
            if re.search(s["must_site"], stack):
                hit = True
            elif not e.get("stacktrace"):
                call_only = True       # no stack to judge the site by
    detail = ";".join("%s*%d" % kv for kv in sorted(counts.items()))
    if not hit and call_only:
        detail = "call-only;" + detail
    return hit, detail


def analyse(a):
    """The analysis of one trace, printed as JSON: records, status, findings, and how many of the
    alternatives name the bug (the subject's classifier)."""
    from stretto import trace
    from stretto.report import analyze
    s = subjects_mod.subjects()[a.subject]
    t = trace.load(a.trace)
    t0 = time.monotonic()
    rep = analyze(t, with_sp=s["sp"])
    el = time.monotonic() - t0
    cls = s["stretto"][a.version] if isinstance(s["stretto"], dict) else s["stretto"]
    found = cls(t, rep)
    n = len(found)
    if a.pins_out:
        # the pins of each alternative that names the bug, as the replays take them (replays.py
        # enumerate reads these instead of analysing the kept trace again)
        import replays
        alts = []
        for f in found:
            got = replays.pins_of(t, f, rep)
            if got is not None:
                alts.append({"cls": f.cls, "level": f.level, "pin": f.pin or "", "pins": got[0],
                             "control_pins": got[1], "required": got[2]})
        os.makedirs(os.path.dirname(os.path.abspath(a.pins_out)), exist_ok=True)
        with open(a.pins_out, "w") as fh:
            json.dump({"findings": n, "alternatives": alts}, fh)
    counts = {}
    for f in rep.findings:
        k = "%s/%s" % (f.cls, f.level)
        counts[k] = counts.get(k, 0) + 1
    print(json.dumps({"records": sum(len(v) for v in t.ranks.values()), "analyse_s": round(el, 1),
                      "status": "complete" if not rep.degraded else "degraded: " + ",".join(x for x, _ in rep.degraded),
                      "findings": ";".join("%s=%d" % kv for kv in sorted(counts.items())), "bug": n}))
    return 0


def one(a):
    s = subjects_mod.subjects()[a.subject]
    c = s["configs"][a.config]
    np, mpi = c["np"], c["mpi"]
    exe, args = subjects_mod.program(s, a.config, a.version)
    label = s["versions"][a.version][0]
    env = dict(os.environ)
    env.update(launcher_env(mpi))
    env.update(subjects_mod.env_of(s, a.config))
    extra, seed, detail = [], "", {}
    for k, v in subjects_mod.env_of(s, a.config).items():
        extra += rank_env(mpi, k, v)
    if a.tool == "delays":
        lib = os.path.join(ROOT, "replay", "bin", mpi, "libstretto-pin.so")
        extra += rank_env(mpi, "LD_PRELOAD", lib)
        for k, v in delays(np, a.run).items():
            extra += rank_env(mpi, k, v)
        seed = str(a.run * 100 + np)
    elif a.tool == "ninja":
        lib = os.path.join(ROOT, "third_party", "ninja", mpi, NINJA_LIB)
        extra += rank_env(mpi, "LD_PRELOAD", lib)
        for k, v in ninja_env(a.setting).items():
            extra += rank_env(mpi, k, v)
    elif a.tool == "stretto":
        lib = os.path.join(ROOT, "tracer", "bin", mpi, "libstretto-trace.so")
    elif a.tool not in ("plain", "must"):
        raise SystemExit("no tool " + a.tool)
    if a.tool in ("delays", "ninja", "stretto") and not os.path.isfile(lib):
        raise SystemExit("HARNESS-ERROR: %s is missing" % lib)
    tries = LEARN_TRIES if a.setting == "learn" else 1
    for attempt in range(1, tries + 1):
        wd = tempfile.mkdtemp(prefix="protocol-run-")
        trace_dir = tempfile.mkdtemp(prefix="protocol-trace-") if a.tool == "stretto" else None
        if s.get("base"):
            shutil.copytree(s["base"], wd, dirs_exist_ok=True, symlinks=True)
        if a.tool == "ninja" and a.setting == "app":
            learned = os.path.join(a.state, "ninja-learn", a.subject, a.config, a.version, ".ninja")
            if not os.path.isdir(learned):
                return finish(a, s, c, label, "NO-LEARNING-RUN", 0.0, "", False, False, "no learning run finished", {}, "")
            shutil.copytree(learned, os.path.join(wd, ".ninja"))
        if a.tool == "must":
            # the layouts MUST builds at run time, per MPI library, in the cache suites.py must uses
            cache = os.path.join(a.state, "must-cache", mpi)
            os.makedirs(cache, exist_ok=True)
            env["XDG_CACHE_HOME"] = cache
            layout, detail["must_layout"] = must_layout(np)
            cmd = [os.path.join("/opt/must", mpi, "bin", "mustrun"), "-np", str(np), "--must:mpiexec", LAUNCHER] \
                + layout + ["--must:output", "json"]
            if s.get("fortran"):
                cmd += ["--must:language", "fortran"]
            for k, v in subjects_mod.env_of(s, a.config).items():
                env[k] = v
            cmd += [exe] + args
        else:
            cmd = [LAUNCHER, "-np", str(np)] + extra
            if a.tool == "stretto":
                cmd += rank_env(mpi, "LD_PRELOAD", lib) + rank_env(mpi, "STRETTO_TRACE_DIR", trace_dir)
            cmd += [exe] + args
        oom0 = oom_kills()
        if a.tool == "must" and a.setting == "warmup":
            # one warm-up builds a layout at a time, as suites.py must's (amendment 6)
            import fcntl
            with open(os.path.join(cache, ".warmup.lock"), "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                p, el = execute(cmd, env, wd, a.timeout)
            check_must_procs(wd, os.environ.get("PROTOCOL_CPUS", ""))
        else:
            p, el = execute(cmd, env, wd, a.timeout)
        text = p.stdout + p.stderr
        if a.tool == "must" and detail.get("must_layout") == CENTRALISED and CENTRALISED_MARK not in text \
                and "[MUST] Executing application" in text:
            raise SystemExit("HARNESS-ERROR: mustrun did not take the centralised layout")
        if a.tool == "must":
            result = must_result(p.returncode, el, a.timeout, text)
        else:
            from stretto.confirm import classify_rc
            result = abort_class(classify_rc(p.returncode, p.stderr, el, a.timeout), mpi)
        if a.tool == "delays" and not re.search(r"^\[stretto-pin\] rank 0: loaded\b", p.stderr, re.M):
            raise SystemExit("HARNESS-ERROR: the replay library did not announce itself; the run would be a plain one")
        if a.tool in ("ninja", "delays", "stretto") and "cannot be preloaded" in p.stderr:
            raise SystemExit("HARNESS-ERROR: %s was not preloaded" % lib)
        ok, says = judge(s, a.version, a.config, text, wd)
        S, failure = symptom_of(s, result, ok, text)
        if result == "KILLED":
            # killed before T: a tool failure only when the job's own container hit its memory
            # limit (amendment 33: its cgroup's oom counter rose); any other kill is the harness's,
            # and the run is done again (the scheduler reruns a HARNESS-RERUN)
            if oom0 is None or oom_kills() in (None, oom0):
                raise SystemExit("HARNESS-RERUN: the run was killed after %.1f s, before T = %d s, not by the job's "
                                 "memory limit" % (el, a.timeout))
            S, failure = False, "killed by the job's memory limit after %.1f s, before T = %d s" % (el, a.timeout)
        reported = S
        if a.tool == "must":
            reported, detail["must"] = must_report(s, wd)
            if result == "MUST-DEADLOCK":
                # MUST stopped the program at a deadlock it predicts (MUST assumes that a standard send
                # may block, so a cycle of buffered sends counts: LAMMPS), before any symptom could show:
                # S is not observed. At the racing calls the report names the bug (predicted); at other
                # calls MUST did not complete the run (a tool failure)
                S = False
                failure = "" if reported else "MUST deadlock report off the racing site"
            if result == "CRASHED" or result.startswith("rc="):
                failure = failure or result
                if S:
                    failure = ""     # the bug's own abort under MUST is the symptom
        if a.tool == "stretto":
            if a.calibration:          # a calibration times the traced run; its trace is not analysed
                reported = False
            else:
                reported, failure = traced(a, s, result, S, failure, trace_dir, detail)
            shutil.rmtree(trace_dir, ignore_errors=True)
        if a.setting == "learn":
            if result == "FINISHED" or ok or (s.get("learn_mark") and re.search(s["learn_mark"], text)):
                dest = os.path.join(a.state, "ninja-learn", a.subject, a.config, a.version)
                shutil.rmtree(dest, ignore_errors=True)
                os.makedirs(dest)
                src = os.path.join(wd, ".ninja")
                os.makedirs(src, exist_ok=True)
                found = 0
                for rank in range(np):
                    f = os.path.join(src, "%s.%d.nin" % (os.path.basename(exe), rank))
                    if os.path.exists(f):
                        found += 1
                    else:
                        open(f, "w").close()     # NINJA reads an empty file as nothing learned
                shutil.copytree(src, os.path.join(dest, ".ninja"))
                detail["learn"] = "attempt %d, %d/%d ranks wrote a file" % (attempt, found, np)
                failure = ""
            else:
                failure = "learning run %s" % result
        if result != "FINISHED" or not ok or reported:
            keep_output(a, "%s-%s-%s-%s-%s-%d" % (a.subject, a.config, a.version, a.tool, a.setting, a.run), cmd,
                        p.returncode, p.stdout, p.stderr, wd)
        shutil.rmtree(wd, ignore_errors=True)
        if a.setting != "learn" or not failure:
            break
    return finish(a, s, c, label, result, el, says, S, reported, failure, detail, seed)


def traced(a, s, result, S, failure, trace_dir, detail):
    """Keep the trace and analyse it (struck runs too, RULE.md Section 3): (F, failure). With
    --defer-analysis the analysis is left to analyse-row: F is False until then."""
    from stretto import keep
    try:
        kept = keep.keep(trace_dir, "protocol/%s/%s/%s" % (a.subject, a.config, a.version), "run%02d" % a.run)
    except Exception as e:      # a trace that cannot be kept stops the campaign: the replays need it
        raise SystemExit("HARNESS-ERROR: %s" % e)
    # the archive's name (a rerun of a superseded run gets run01~2: keep never overwrites)
    stem = os.path.basename(kept)[:-len(keep.EXT)] if kept else "run%02d" % a.run
    if kept:
        detail["archive"] = stem
    if a.defer_analysis:
        # the analysis job completes the row in every case, so that the configuration keeps its N
        detail["analysis"] = "deferred"
    if not os.listdir(trace_dir):
        detail["trace"] = "empty"          # no trace to analyse: a tool failure (amendment 25)
        return False, failure or "no trace"
    if a.defer_analysis:
        if not kept:
            raise SystemExit("HARNESS-ERROR: a deferred analysis needs the trace kept (KEEP_TRACES)")
        return False, failure
    return analysed(a.subject, a.config, a.version, a.state, stem, trace_dir, S, failure, detail)


def analysed(subject, config, version, state, stem, trace_dir, S, failure, detail):
    """The analysis of a trace in a process of its own (ANALYSIS_TIMEOUT), the pins of its
    alternatives written beside the archive's name: (F, failure)."""
    t0 = time.monotonic()
    p = subprocess.run(["/usr/bin/timeout", "-k", "5", str(ANALYSIS_TIMEOUT), sys.executable, os.path.abspath(__file__),
                        "analyse", "--subject", subject, "--version", version, "--trace", trace_dir,
                        "--pins-out", os.path.join(state, "alternatives", subject, config, version, stem + ".json")],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    el = time.monotonic() - t0
    if p.returncode == 124 or (p.returncode == 137 and el >= ANALYSIS_TIMEOUT):
        detail["analysis"] = "timeout"
        return False, "analysis timeout"
    if p.returncode in (137, -9):
        # killed before its budget ran out: out of memory (amendment 25)
        detail["analysis"] = "killed after %.0f s" % el
        return False, "analysis killed"
    try:
        res = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        detail["analysis"] = ("failed rc=%d: " % p.returncode) + p.stderr.strip().splitlines()[-1][:200] if p.stderr.strip() else "failed"
        # an unreadable trace is a tool failure, struck run or not (amendment 25)
        return False, failure or "analysis failed"
    detail.update(res)
    return res["bug"] > 0, failure


def analyse_row(a):
    """The deferred analysis of a traced run that ran alone (RULE.md amendment 14): its kept trace
    unpacked, analysed as run.py run would have, and its row written complete, with the outcome
    of Section 3 from the run's symptom and the analysis's report."""
    import tarfile
    from stretto import keep
    with open(a.deferred) as fh:
        row = json.load(fh)
    detail = json.loads(row["detail"]) if row.get("detail") else {}
    if detail.get("analysis") != "deferred":
        raise SystemExit("HARNESS-ERROR: %s is not a row whose analysis was deferred" % a.deferred)
    if detail.get("trace") == "empty":
        # nothing to analyse: the row is complete as the traced run wrote it (a tool failure)
        detail["analysis"] = "none: no trace"
        row.update({"detail": json.dumps(detail, sort_keys=True),
                    "outcome": rows.outcome(row["symptom"] in (True, "True"), False, True)})
        return write_json(a.row, row)
    stem = detail["archive"]
    archive = os.path.join(keep.directory() or "", "protocol", row["subject"], row["config"], row["version"], stem + keep.EXT)
    d = tempfile.mkdtemp(prefix="protocol-analysis-")
    t0 = time.monotonic()
    try:
        with tarfile.open(archive) as tf:
            tf.extractall(d)
        subdirs = [os.path.join(d, x) for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))]
        detail["analysis"] = "apart"
        S = row["symptom"] in (True, "True")
        reported, failure = analysed(row["subject"], row["config"], row["version"], a.state, stem,
                                     subdirs[0] if subdirs else d, S, row["failure"], detail)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    detail["analysis_seconds"] = round(time.monotonic() - t0, 1)
    detail["analysis_cpus"] = os.environ.get("PROTOCOL_CPUS", "")
    row.update({"reported": reported, "failure": failure, "detail": json.dumps(detail, sort_keys=True),
                "outcome": rows.outcome(S, reported, bool(failure))})
    write_json(a.row, row)
    print("%s %s %s analysis of run %s: %s -> %s" % (row["subject"], row["config"], row["version"], row["run"],
                                                     "reported" if reported else "not reported", row["outcome"]), flush=True)
    return 0


def write_json(path, row):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(tmp, "w") as fh:
        json.dump(row, fh)
    os.replace(tmp, path)
    return 0


def finish(a, s, c, label, result, el, says, S, reported, failure, detail, seed):
    failed = bool(failure)
    row = {"subject": a.subject, "config": a.config, "np": c["np"], "mpi": c["mpi"], "version": a.version, "label": label,
           "tool": a.tool, "setting": a.setting, "run": a.run, "result": result, "seconds": round(el, 1), "says": says,
           "symptom": S, "reported": reported, "failure": failure, "detail": json.dumps(detail, sort_keys=True) if detail else ""}
    row.update(rows.common(a.timeout, seed=seed, category=rows.outcome(S, reported, failed)))
    tmp = a.row + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(a.row)), exist_ok=True)
    with open(tmp, "w") as fh:
        json.dump(row, fh)
    os.replace(tmp, a.row)
    print("%s %s %s %s %s run %d: %s %.1f s %s -> %s" % (a.subject, a.config, a.version, a.tool, a.setting, a.run, result, el,
                                                          says, row["outcome"]), flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    for k in ("subject", "config", "version", "tool", "setting", "row"):
        r.add_argument("--" + k, required=True)
    r.add_argument("--run", type=int, required=True)
    r.add_argument("--timeout", type=int, required=True)
    r.add_argument("--state", default=os.environ.get("PROTOCOL_STATE", "/state"))
    r.add_argument("--outputs", default="")
    r.add_argument("--calibration", action="store_true",
                   help="a run of the timeout calibration: a traced run's trace is neither kept nor analysed")
    r.add_argument("--defer-analysis", action="store_true",
                   help="keep the trace and leave its analysis to analyse-row (a job that runs alone)")
    ar = sub.add_parser("analyse-row")
    ar.add_argument("--deferred", required=True, help="the row run.py run wrote with --defer-analysis")
    ar.add_argument("--row", required=True)
    ar.add_argument("--state", default=os.environ.get("PROTOCOL_STATE", "/state"))
    an = sub.add_parser("analyse")
    an.add_argument("--subject", required=True)
    an.add_argument("--version", required=True)
    an.add_argument("--trace", required=True)
    an.add_argument("--pins-out", default="", help="write the pins of the alternatives that name the bug here")
    a = ap.parse_args()
    return analyse(a) if a.cmd == "analyse" else analyse_row(a) if a.cmd == "analyse-row" else one(a)


if __name__ == "__main__":
    sys.exit(main())
