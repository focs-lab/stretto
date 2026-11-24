#!/usr/bin/env python3
"""The campaign's scheduler: the jobs of the plan's blocks (plan.csv), each in a container of its
own on a set of hardware threads no other job has, within the run processors of RULE.md Section
1.5 (0-17 and 40-57 on a-p13), every rank on its own thread; resumable after an interruption.

A job is one run of experiments/protocol/run.py. It gets the first free threads of the pool in
the order 0, 40, 1, 41, ... (a core's two threads, then the next core), as many as plan.csv's
cpus says (MUST's tool processes included); a job marked alone (NPB MG at 32 ranks, the serial
plain runs of the concurrency check) waits until no other job runs and runs with nothing beside
it. A job's row is written by run.py as JSON under STATE/rows/ and appended here to its block's
table, RESULTS/<block>.csv, with jobs (how many jobs ran at its start, itself included) and cpus
(its threads) from this scheduler.

Order: NINJA's learning runs first (at the calibration's cap: the application-centric runs of
the calibration start from their files too), then the calibration (Section 2.1), then every other
job, run by run (run 1 of every configuration, version, tool and setting, then run 2, ...), so that
the versions and tools of a configuration share the machine's conditions over the campaign. An
application-centric NINJA job waits for its learning run. A job whose T is not calibrated yet
(RESULTS/<subject>/timeouts.csv, calibrate.py) is not started unless --timeout gives one (the
smoke test, the calibration itself).

Resuming: a job whose row is in its block's table is done; a job whose row file exists but is not
in the table (the scheduler stopped between the two) is appended; every other job runs. Containers
of an earlier invocation (named PREFIX-*) that still run keep their threads until they end, and
their rows are collected then (a job among them that leaves no row runs again). Every start, end, harness error and restart is written to
RESULTS/journal.log; a run that was started and has no row is run again and the journal says so.
A job that ends without a row (run.py's HARNESS-ERROR: a library that did not load, a run killed
before its timeout, a trace that could not be kept) stops its block: no further job of the block
starts in this invocation.

SIGINT or SIGTERM stops starting jobs; running jobs finish and are collected (a second signal
kills their containers, whose runs then have no row and run again on the next invocation).

usage: schedule.py [--blocks REGEX] [--configs REGEX] [--tools REGEX] [--runs N] [--timeout T] [--results DIR] [--state DIR]
                   [--pool 0-17,40-57] [--prefix proto] [--dry-run] [--max-jobs K]
Python 3.6 runs this file (the experiment machine's host); it needs docker and the images of
plan.csv.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import rows  # noqa: E402
import external  # noqa: E402

ANALYSIS = "traced-analysis"   # the analysis job of a traced run that runs alone (amendment 14)
MAX_RERUNS = 2       # reruns of a job after a harness failure in one invocation (amendment 29), then its block stops
FIELDS = ["subject", "config", "np", "mpi", "version", "label", "tool", "setting", "run", "result", "seconds",
          "says", "symptom", "reported", "failure", "detail"] + rows.COMMON
POOL = "0-17,40-57"
from calibrate import CALIBRATION_CAP  # noqa: E402  (seconds of a calibration run)


def pool_order(cpus):
    """Hardware threads in allocation order: a core's two threads together (n and n+40 on a-p13,
    whose siblings are 40 apart), cores in ascending order."""
    cpus = sorted(cpus)
    out, seen = [], set()
    for c in cpus:
        if c in seen:
            continue
        out.append(c)
        seen.add(c)
        if c + 40 in cpus and c + 40 not in seen:
            out.append(c + 40)
            seen.add(c + 40)
    return out


class Pool:
    def __init__(self, cpus):
        self.order = pool_order(cpus)
        self.free = list(self.order)

    def take(self, n, alone=False):
        if alone and len(self.free) != len(self.order):
            return None
        if n > len(self.free):
            return None
        got, self.free = self.free[:n], self.free[n:]
        return got

    def claim(self, cpus):
        """Take these threads out of the free list (a container of an earlier invocation runs on them)."""
        self.free = [c for c in self.free if c not in set(cpus)]

    def give(self, cpus):
        back = set(self.free) | set(cpus)
        self.free = [c for c in self.order if c in back]

    def idle(self):
        return len(self.free) == len(self.order)


def key_of(j):
    return "%s|%s|%s|%s|%s|%s" % (j["subject"], j["config"], j["version"], j["tool"], j["setting"], j["run"])


def container(prefix, j):
    """The job's container name: the prefix and a digest of its block and key."""
    return "%s-%s" % (prefix, hashlib.sha1((j["block"] + "@" + key_of(j)).encode()).hexdigest()[:16])


def safe(text):
    return re.sub(r"[^A-Za-z0-9._=+-]+", "_", text)


def load_timeouts(results, subject):
    t = {}
    for r in rows.read(os.path.join(results, subject, "timeouts.csv")):
        if r["T"]:                       # a capped calibration leaves T empty: that block cannot start
            t[(r["config"], r["tool"], r["setting"])] = int(r["T"])
    return t


def timeout_key(j):
    """The calibrated setting a job's T comes from (RULE.md Section 2.1)."""
    tool, setting = j["tool"], j["setting"]
    if tool == "ninja" and setting == "learn":
        return "ninja", "system"
    if tool == "plain" and setting == "serial":
        return "plain", "-"
    if tool == "stretto" and setting in ("replay", "replay-enumerate", ANALYSIS):
        return "stretto", "traced"
    return tool, setting


def jobs_of(plan, blocks, runs_override, configs=".", tools="."):
    out = []
    # the counted traced runs an enumeration waits for (amendment 26): the bug's traced runs of the
    # configuration, the replay cell's traced runs of a thread campaign
    counted = {}
    for p in plan:
        k = None
        if p["tool"] == "stretto" and p["setting"] == "traced" and p["version"] == "bug" and p["runner"] == "run.py":
            k = (p["subject"], p["config"])
        elif p["setting"] == "replay-trace":
            k = (p["subject"], "replay-trace")
        if k:
            counted[k] = min(int(p["n"]), runs_override) if runs_override is not None else int(p["n"])
    for p in plan:
        if not re.search(blocks, p["block"]) or not re.search(configs, p["config"]):
            continue
        ts = p["setting"] if p["tool"] == "calibrate" else "%s:%s" % (p["tool"], p["setting"])
        if not re.search(tools, ts):
            continue
        ext = p["runner"][len("external:"):] if p["runner"].startswith("external:") else None
        if p["runner"] not in ("run.py", "calibrate.py", "replays.py") and not ext:
            continue
        n = int(p["n"])
        if runs_override is not None and p["setting"] not in ("learn", "warmup"):
            n = min(n, runs_override)
        tool, setting = p["tool"], p["setting"]
        if tool == "calibrate":
            tool, _, setting = p["setting"].partition(":")
        first = 0 if setting in ("learn", "warmup") or tool == "build" or (ext and setting == "replay-enumerate") else 1
        job = {"block": p["block"], "subject": p["subject"], "config": p["config"], "np": int(p["np"] or 0),
               "version": p["version"], "tool": tool, "cpus": int(p["cpus"]), "alone": p["alone"] == "1",
               "image": p["image"], "calibration": p["tool"] == "calibrate", "external": ext, "mpi": p["mpi"]}
        if p["runner"] == "replays.py":
            # the alternatives first (replays.py), once every counted traced run is in its table
            out.append(dict(job, setting="replay-enumerate", run=0, counted=counted.get((p["subject"], p["config"]), 0)))
        if ext == "threadreplay" and setting == "replay-enumerate":
            job["counted"] = counted.get((p["subject"], "replay-trace"), 0)
        # a traced run that runs alone leaves its analysis to a job of its own on one hardware
        # thread, beside the other jobs (RULE.md amendment 14)
        apart = job["alone"] and tool == "stretto" and setting == "traced" and not job["calibration"]
        for i in range(first, first + n):
            out.append(dict(job, setting=setting, run=i, defer=apart))
            if apart:
                out.append(dict(job, setting=ANALYSIS, run=i, cpus=1, alone=False, memory_cpus=job["cpus"]))
    # a job that runs alone drains the machine before it starts: they come first in their phase,
    # where the machine drains once for all of them (an invocation starts idle) and the
    # analyses of their traces (next) then run beside the phase's other jobs
    out.sort(key=lambda j: (0 if j["setting"] in ("learn", "warmup") or j["tool"] == "build" else 1 if j["calibration"] else 3 if j["setting"].startswith("replay")
                            else 2, 0 if j["alone"] else 1 if j["setting"] == ANALYSIS else 2, j["run"], j["subject"],
                            j["config"], j["version"], j["tool"], j["setting"]))
    return out


def traced_of(j):
    """The traced run an analysis job analyses."""
    return dict(j, setting="traced")


class Scheduler:
    def __init__(self, a, jobs):
        self.a, self.jobs = a, jobs
        self.pool = Pool(rows.parse_cpuset(a.pool))
        self.running = {}            # key -> (job, Popen, cpus, started)
        self.started = {}            # key -> time.time() at its start
        self.reruns = {}             # (block, key) -> reruns in this invocation (amendment 29)
        self.stop = 0
        self.broken = set()          # blocks stopped by a harness error
        self.timeouts = {}
        self.adopted = {}            # container -> (job or None, cpus): an earlier invocation's, still running
        self.last_poll = 0.0
        self.journal = os.path.join(a.results, "journal.log")
        os.makedirs(a.results, exist_ok=True)
        os.makedirs(os.path.join(a.state, "rows"), exist_ok=True)

    def log(self, text):
        line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), text)
        print(line, flush=True)
        with open(self.journal, "a") as fh:
            fh.write(line + "\n")

    def table(self, j):
        return os.path.join(self.a.results, j["block"] + ".csv")

    def row_file(self, j):
        return os.path.join(self.a.state, "rows", safe(j["block"]), safe(key_of(j)) + (".csv" if j["external"] else ".json"))

    def deferred_file(self, j):
        """The row of a traced run whose analysis is a job of its own: kept here until that job
        writes the row complete (the traced run is done once it exists)."""
        return os.path.join(self.a.state, "rows", safe(j["block"]), safe(key_of(traced_of(j))) + ".deferred.json")

    def done_keys(self):
        done = set()
        blocks = set(j["block"] for j in self.jobs)
        for j in self.jobs:                    # and the blocks the selected jobs need (a suite's build)
            if j["external"]:
                try:
                    blocks.update(b for b, _ in external.spec(j, self.ctx()).get("needs", []))
                except Exception:
                    pass
        for b in sorted(blocks):
            for r in rows.current(rows.read(os.path.join(self.a.results, b + ".csv"))):
                if r.get("protocol_job"):       # a row of an external job's own table
                    done.add((b, r["protocol_job"]))
                    continue
                k = "%s|%s|%s|%s|%s|%s" % (r["subject"], r["config"], r["version"], r["tool"], r["setting"], r["run"])
                done.add((b, k))
                if r["setting"] == "traced" and '"analysis": "apart"' in r.get("detail", ""):
                    done.add((b, "%s|%s|%s|%s|%s|%s" % (r["subject"], r["config"], r["version"], r["tool"], ANALYSIS, r["run"])))
        for j in self.jobs:                    # traced runs whose analysis is still to come
            if j.get("defer") and os.path.exists(self.deferred_file(j)):
                done.add((j["block"], key_of(j)))
        return done

    def started_before(self):
        """Jobs the journal shows started and not ended (an interrupted invocation)."""
        started = set()
        if not os.path.exists(self.journal):
            return started
        with open(self.journal) as fh:
            for line in fh:
                m = re.match(r"\S+ (start|end|harness-error|discarded) (\S+) (\S+)", line)
                if m:
                    k = (m.group(2), m.group(3))
                    if m.group(1) == "start":
                        started.add(k)
                    else:
                        started.discard(k)
        return started

    def collect(self, j, cpus, jobs_at_start):
        if j.get("defer"):
            # the traced run of a job that runs alone: its row waits for its analysis job
            f = self.deferred_file(j)
            if not os.path.exists(self.row_file(j)):
                return os.path.exists(f)
            with open(self.row_file(j)) as fh:
                row = json.load(fh)
            row["cpus"] = rows.cpuset(cpus) if cpus else row.get("cpus", "")
            if jobs_at_start:
                row["jobs"] = jobs_at_start
            with open(f + ".tmp", "w") as fh:
                json.dump(row, fh)
            os.replace(f + ".tmp", f)
            os.remove(self.row_file(j))
            self.log("end %s %s %s (analysis apart)" % (j["block"], safe(key_of(j)), row["result"]))
            return True
        f = self.row_file(j)
        if not os.path.exists(f):
            return False
        if j["external"]:
            got = rows.read(f)
            for r in got:
                r["cpus"] = rows.cpuset(cpus) if cpus else r.get("cpus", "")
                if jobs_at_start:
                    r["jobs"] = jobs_at_start
            fields = rows.header_of(f) or []
            have = rows.header_of(self.table(j))
            if have is not None and have != fields:
                # another job of the block wrote other columns (a script's optional ones): the table
                # takes the union, every row kept
                old = rows.read(self.table(j))
                fields = [c for c in have if c not in rows.COMMON] + [c for c in fields if c not in have and c not in rows.COMMON] + rows.COMMON
                os.replace(self.table(j), self.table(j) + ".before-union")
                rows.append(self.table(j), old, fields)
            rows.append(self.table(j), got, fields)
            os.rename(f, f + ".collected")
            cats = {}
            for r in got:
                cats[r.get("outcome", "")] = cats.get(r.get("outcome", ""), 0) + 1
            self.log("end %s %s %d rows %s" % (j["block"], safe(key_of(j)), len(got),
                                               ";".join("%s=%d" % kv for kv in sorted(cats.items()))))
            return True
        with open(f) as fh:
            row = json.load(fh)
        if j["setting"] != ANALYSIS:           # an analysis job's row keeps its traced run's
            row["cpus"] = rows.cpuset(cpus) if cpus else row.get("cpus", "")
            if jobs_at_start:
                row["jobs"] = jobs_at_start
        rows.append(self.table(j), [row], FIELDS)
        os.rename(f, f + ".collected")
        self.log("end %s %s %s %s" % (j["block"], safe(key_of(j)).replace(" ", "_"), row["result"], row["outcome"]))
        return True

    def leftovers(self):
        """The running containers of this prefix (an earlier invocation's jobs)."""
        p = subprocess.run(["docker", "ps", "--format", "{{.Names}}", "--filter", "name=^%s-" % self.a.prefix],
                           stdout=subprocess.PIPE, universal_newlines=True)
        return [n for n in p.stdout.split() if n.startswith(self.a.prefix + "-")]

    def inspect(self, name, fmt):
        p = subprocess.run(["docker", "inspect", "--format", fmt, name], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True)
        return p.stdout.strip()

    def oom_killed(self, name):
        """Whether Docker records the container as killed by its own memory limit, and the container
        removed (amendment 29: only that kill is the tool's failure)."""
        oom = self.inspect(name, "{{.State.OOMKilled}}") == "true"
        subprocess.run(["docker", "rm", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return oom

    def adopt_leftovers(self):
        """An earlier invocation's containers keep their threads until they end; a job of this
        selection among them is collected when it does (and runs again if it left no row). A
        container of a job this invocation did not select counts as alone if its label says so.
        Containers that ended before this invocation are judged and removed."""
        by_name = dict((container(self.a.prefix, j), j) for j in self.jobs)
        for name in self.leftovers():
            cpus = rows.parse_cpuset(self.inspect(name, "{{.HostConfig.CpusetCpus}}"))
            alone = self.inspect(name, '{{index .Config.Labels "protocol.alone"}}') == "1"
            self.pool.claim(cpus)
            self.adopted[name] = (by_name.get(name), cpus, alone)
            self.log("adopted %s cpus=%s%s %s" % (name, rows.cpuset(cpus), " alone" if alone else "",
                                                   safe(key_of(by_name[name])) if name in by_name else "(not selected)"))
        p = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}", "--filter", "status=exited",
                            "--filter", "name=^%s-" % self.a.prefix], stdout=subprocess.PIPE, universal_newlines=True)
        for name in p.stdout.split():
            j = by_name.get(name)
            if j is not None and not os.path.exists(self.row_file(j)) and self.oom_killed(name):
                self.killed_row(j, 137)
            elif j is not None and not os.path.exists(self.row_file(j)):
                self.rerun(j, "its container of an earlier invocation ended without a row")
            else:
                subprocess.run(["docker", "rm", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def reap_adopted(self, todo):
        if not self.adopted or time.time() - self.last_poll < 10:
            return
        self.last_poll = time.time()
        alive = set(self.leftovers())
        for name, (j, cpus, _) in list(self.adopted.items()):
            if name in alive:
                continue
            del self.adopted[name]
            self.pool.give(cpus)
            oom = self.oom_killed(name)
            if j is None:
                continue
            if self.collect(j, cpus, None):
                self.done.add((j["block"], key_of(j)))
            elif oom and self.killed_row(j, 137) and self.collect(j, cpus, None):
                self.done.add((j["block"], key_of(j)))
            elif self.rerun(j, "its container of an earlier invocation left no row"):
                todo.insert(0, j)

    def timeout_for(self, j):
        if j["external"] == "suite" and j["tool"] != "build":
            # a suite's T per tool, from its calibration (amendment 15); the calibration's cap
            if j["calibration"]:
                return external.SUITE_CAL_CAP
            if self.a.timeout:
                return self.a.timeout
            key = ("suite", j["subject"])
            if key not in self.timeouts:
                self.timeouts[key] = dict((r["tool"], int(r["T"])) for r in rows.read(
                    os.path.join(self.a.results, j["subject"], "suite-timeouts.csv")) if r["T"])
            return self.timeouts[key].get(j["tool"])
        if j["external"] == "suite" or (j["external"] == "threadreplay" and j["setting"] == "replay-enumerate"):
            return 0                     # a build or an enumeration: no run, no T
        if j["external"] in ("thread", "mustvc", "threadreplay"):
            # T per cell, from the thread campaigns' calibrations (amendment 20; calibrate.py derive-threads)
            if j["calibration"]:
                return CALIBRATION_CAP
            if self.a.timeout:
                return self.a.timeout
            if "threads" not in self.timeouts:
                t = {}
                for r in rows.read(os.path.join(self.a.results, "thread-timeouts.csv")):
                    if r["T"]:
                        t.setdefault(r["block"], {})[r["cell"]] = int(r["T"])
                self.timeouts["threads"] = t
            have = self.timeouts["threads"].get(external.target_block(j), {})
            need = external.cells_of(j)
            return dict((c, have[c]) for c in need) if all(c in have for c in need) else None
        if j["external"] == "locks":
            # the lock replays' programs are CorrBench's OpenMP ones and the idioms: Stretto's T there
            if self.a.timeout:
                return self.a.timeout
            return self.timeout_for(dict(j, external="suite", subject="corrbench-openmp", tool="stretto", calibration=False))
        if j["external"] == "mpiprv":
            # its one program run (LAMMPS) at the subject's plain T
            if self.a.timeout:
                return self.a.timeout
            return self.timeout_for(dict(j, external=None, tool="plain", setting="-", calibration=False))
        if j["calibration"] or j["setting"] in ("learn", "warmup"):
            # NINJA's learning run comes before the calibration (the application-centric runs of the
            # calibration start from its files too) and is retried until one finishes: it gets the cap
            return self.a.timeout or CALIBRATION_CAP
        if self.a.timeout:
            return self.a.timeout
        if j["subject"] not in self.timeouts:
            self.timeouts[j["subject"]] = load_timeouts(self.a.results, j["subject"])
        tool, setting = timeout_key(j)
        return self.timeouts[j["subject"]].get((j["config"], tool, setting))

    def memory(self, j):
        """GB of a job's container; a deferred analysis gets its traced run's, as it would have had
        inside that job (amendment 25)."""
        return max(8, 3 * j.get("memory_cpus", j["cpus"]) + 4)

    def command(self, j, cpus, njobs, timeout):
        a = self.a
        name = container(a.prefix, j)
        state_in = "/state"
        row = os.path.join(state_in, os.path.relpath(self.row_file(j), a.state))
        # no --rm: the scheduler reads Docker's verdict on the container (State.OOMKilled) and
        # removes it; the labels let a later invocation know the job and whether it runs alone
        cmd = ["docker", "run", "--label", "protocol.job=%s@%s" % (j["block"], key_of(j)),
               "--label", "protocol.alone=%d" % bool(j["alone"]), "--ulimit", "core=0", "--name", name, "--hostname", rows.host(),
               "--cpuset-cpus", rows.cpuset(cpus), "--memory", "%dg" % self.memory(j), "--memory-swap", "%dg" % self.memory(j),
               "--shm-size", "4g", "--user", "%d:%d" % (os.getuid(), os.getgid()), "-e", "HOME=/tmp",
               "-e", "MACHINE=" + rows.host(), "-e", "PROTOCOL_CPUS=" + rows.cpuset(cpus), "-e", "PROTOCOL_JOBS=%d" % njobs,
               "-e", "STRETTO_COMMIT=" + a.commit, "-e", "KEEP_TRACES=/state/traces", "-e", "PROTOCOL_STATE=/state",
               "-v", "%s:/w" % a.repo, "-v", "%s:/state" % a.state, "-v", "%s:/results" % a.results, "-w", "/w", j["image"]]
        outputs = "/state/outputs/" + safe(j["block"])
        if j["external"]:
            return self.external_command(j, cmd, row, cpus, njobs, timeout)
        if j["setting"] == "replay-enumerate":
            return cmd + ["python3", "experiments/protocol/replays.py", "enumerate", "--subject", j["subject"],
                          "--config", j["config"], "--row", row, "--results", "/results", "--outputs", outputs]
        if j["setting"] == ANALYSIS:
            deferred = os.path.join(state_in, os.path.relpath(self.deferred_file(j), a.state))
            return cmd + ["python3", "experiments/protocol/run.py", "analyse-row", "--deferred", deferred, "--row", row]
        if j["setting"] == "replay":
            return cmd + ["python3", "experiments/protocol/replays.py", "replay", "--subject", j["subject"],
                          "--config", j["config"], "--index", str(j["run"]), "--timeout", str(timeout), "--row", row,
                          "--results", "/results", "--outputs", outputs]
        cmd += ["python3", "experiments/protocol/run.py", "run", "--subject", j["subject"], "--config", j["config"],
                "--version", j["version"], "--tool", j["tool"], "--setting", j["setting"], "--run", str(j["run"]),
                "--timeout", str(timeout), "--row", row, "--outputs", outputs]
        if j["calibration"]:
            cmd.append("--calibration")
        if j.get("defer"):
            cmd.append("--defer-analysis")
        return cmd

    def ctx(self):
        if not hasattr(self, "_ctx"):
            self._ctx = {"exp_image": "stretto-exp:24.04", "must_image": "stretto-must-u:24.04",
                         "binding": external.BINDING, "machine": external.machine(), "commit": self.a.commit,
                         "owner": "%d:%d" % (os.getuid(), os.getgid()), "select": self.a.suite_select,
                         "limit": self.a.suite_limit}
        return self._ctx

    def external_command(self, j, cmd, row, cpus, njobs, timeout):
        """docker run of an external job: the committed script, then adapters.py on its table into
        the job's row file (a CSV: the script's rows with the common columns, the outcome and the
        job's key, protocol_job)."""
        sp = external.spec(dict(j, T=timeout), self.ctx())
        i = cmd.index("--user")
        user = sp.get("user")
        if user == "root":
            del cmd[i:i + 2]
        extra = list(sp.get("docker", []))
        for host, inside, mode in sp.get("mounts", []):
            if not host or not os.path.exists(host):
                raise external.MissingBuild("%s: %s is not on this machine (MACHINE_ENV)" % (j["block"], host or inside))
            extra += ["-v", "%s:%s:%s" % (host, inside, mode)]
        for k, v in sorted(sp.get("env", {}).items()):
            extra += ["-e", "%s=%s" % (k, v)]
        image = sp["image"]
        cmd = cmd[:-1] + extra + [image]
        sets = ["host=" + rows.host(), "cpus=" + rows.cpuset(cpus), "jobs=%d" % njobs,
                "commit=" + self.a.commit, "protocol_job=" + key_of(j)] + [x for x in sp.get("extra", []) if x != "--set"]
        ann = list(sp.get("annotate", []))
        if isinstance(timeout, dict):
            if j["external"] == "threadreplay":
                sets.append("timeout=%d" % list(timeout.values())[0])
            else:
                ann += ["--cell-timeouts", " ".join("%s=%d" % kv for kv in sorted(timeout.items()))]
        else:
            sets.append("timeout=%s" % timeout)
        annotate = "python3 experiments/protocol/adapters.py annotate --kind %s %s %s.part%s --set %s && mv %s.part %s" % (
            sp["kind"], external.q(sp["src"]), external.q(row), "".join(" " + external.q(x) for x in ann),
            " ".join(external.q(x) for x in sets), external.q(row), external.q(row))
        tail = ("; chown %s %s 2>/dev/null" % (self.ctx()["owner"], external.q(row))) if user == "root" else ""
        # the script's table is annotated only when the script ended well: a script that died mid-pass
        # leaves a partial table, which is no row (the scheduler judges the job by its status and log);
        # a rerun starts from no table, since some scripts append to theirs
        script = "mkdir -p %s; rm -f %s; ( %s ); rc=$?; if [ $rc -eq 0 ] && [ -s %s ]; then %s; fi%s; exit $rc" % (
            external.q(os.path.dirname(row)), external.q(sp["src"]), sp["cmd"], external.q(sp["src"]), annotate, tail)
        return cmd + ["bash", "-c", script]

    def ready(self, j, todo):
        """Whether the job's prerequisites are done: an application-centric NINJA run's learning
        run; the enumeration of the alternatives, the configuration's traced runs; a replay, the
        enumeration (and an alternative of its number); an external job, what its spec needs (a
        suite's build)."""
        if j["external"] == "threadreplay" and j["setting"] == "replay-enumerate":
            # every counted traced run of the replay cell in its table (amendment 26)
            return len(self.counted_rows(j)) >= j.get("counted", 0)
        if j["external"] == "threadreplay" and j["setting"] not in ("replay-trace",):
            return ("%s/replay-alternatives" % j["subject"],
                    "%s|%s|bug|stretto|replay-enumerate|0" % (j["subject"], j["config"])) in self.done
        if j["external"]:
            try:
                needs = external.spec(j, self.ctx()).get("needs", [])
            except Exception:
                return True
            return all(n in self.done for n in needs)
        if j["setting"] == ANALYSIS:
            return (j["block"], key_of(traced_of(j))) in self.done
        if j["tool"] == "must" and j["setting"] != "warmup" and not j["external"]:
            # no MUST run of a configuration, calibration included, before its warm-up has ended (amendment 31)
            return ("%s/must-warmup" % j["subject"], "%s|%s|fix|must|warmup|0" % (j["subject"], j["config"])) in self.done
        if j["tool"] == "ninja" and j["setting"] == "app":
            block = "%s/ninja-learn" % j["subject"]
            return (block, "%s|%s|%s|ninja|learn|0" % (j["subject"], j["config"], j["version"])) in self.done
        if j["setting"] == "replay-enumerate":
            # every counted traced run of the configuration in its table, of whatever outcome (amendment 26)
            return len(self.counted_rows(j)) >= j.get("counted", 0)
        if j["setting"] == "replay":
            if (j["block"], "%s|%s|bug|stretto|replay-enumerate|0" % (j["subject"], j["config"])) not in self.done:
                return False
            return True
        return True

    def blocked_by_alone(self, j):
        """Nothing starts beside a job that runs alone, an adopted one too (its label says so when
        this invocation did not select it), and a job that runs alone waits for an idle machine."""
        if any(r[0]["alone"] for r in self.running.values()) or any(alone for _, _, alone in self.adopted.values()):
            return True
        return bool(j["alone"] and (self.running or self.adopted))

    def counted_rows(self, j):
        """The current rows of the counted traced runs an enumeration job takes, in run order."""
        if j["external"] == "threadreplay":
            got = rows.current(rows.read(os.path.join(self.a.results, j["subject"], "replay-traces.csv")))
        else:
            got = [r for r in rows.current(rows.read(os.path.join(self.a.results, j["subject"], "stretto-traced.csv")))
                   if r["config"] == j["config"] and r["version"] == "bug" and r["setting"] == "traced"]
        return sorted(got, key=lambda r: int(r["run"]))

    def trace_set(self, j):
        """The set of counted traced runs as an enumeration records it (replays.py trace_set;
        threadreplays.py: the runs that kept an analysed trace)."""
        if j["external"] == "threadreplay":
            return " ".join("run%02d" % int(r["run"]) for r in self.counted_rows(j)
                            if r.get("kept") == "True" and r.get("analysis") == "complete")
        return ["%s:%s" % (r["run"], (json.loads(r["detail"]) if r.get("detail") else {}).get("archive", ""))
                for r in self.counted_rows(j)]

    def stale_enumerations(self):
        """An enumeration whose set of counted traced runs has changed since is superseded, with the
        replays that followed it, and runs again (amendment 26)."""
        for j in self.jobs:
            if j["setting"] != "replay-enumerate" or (j["block"], key_of(j)) not in self.done:
                continue
            got = [r for r in rows.current(rows.read(self.table(j))) if
                   (r.get("protocol_job") == key_of(j) if j["external"] else r.get("setting") == "replay-enumerate"
                    and r.get("config") == j["config"])]
            if not got:
                continue
            if j["external"]:
                recorded = got[-1].get("trace_set", "")
            else:
                recorded = (json.loads(got[-1]["detail"]) if got[-1].get("detail") else {}).get("trace_set")
            if recorded == self.trace_set(j):
                continue
            why = "the counted traced runs changed after this enumeration (amendment 26)"
            if j["external"]:
                rows.supersede(self.table(j), lambda r: r.get("protocol_job") == key_of(j), why)
                for b in set(x["block"] for x in self.jobs if x["subject"] == j["subject"] and x["external"] == "threadreplay"
                             and x["setting"] not in ("replay-trace", "replay-enumerate")):
                    rows.supersede(os.path.join(self.a.results, b + ".csv"), lambda r: True, why)
            else:
                rows.supersede(self.table(j), lambda r: r.get("config") == j["config"], why)
            self.log("superseded %s %s and its replays: %s" % (j["block"], safe(key_of(j)), why))
        self.done = self.done_keys()

    def replay_exists(self, j):
        if j["external"] == "threadreplay" and j["setting"] not in ("replay-trace", "replay-enumerate"):
            path = os.path.join(self.a.state, "threadreplays", j["subject"], "alternatives.csv")
            return any(int(r["index"]) == j["run"] for r in rows.read(path))
        if j["setting"] != "replay":
            return True
        path = os.path.join(self.a.results, j["subject"], "replay-alternatives-%s.csv" % j["config"])
        return any(int(r["index"]) == j["run"] for r in rows.read(path))

    def run(self):
        a = self.a
        self.done = self.done_keys()
        self.stale_enumerations()
        self.adopt_leftovers()
        for j in self.jobs:                    # rows written after the last collection
            if (j["block"], key_of(j)) not in self.done and os.path.exists(self.row_file(j)):
                self.collect(j, None, None)
                self.done.add((j["block"], key_of(j)))
        adopted = set((j["block"], safe(key_of(j))) for j, _, _ in self.adopted.values() if j)
        selected = set((j["block"], safe(key_of(j))) for j in self.jobs)
        by_safe = dict(((j["block"], safe(key_of(j))), j) for j in self.jobs)
        for b, k in sorted(self.started_before()):
            if (b, k) in selected and (b, k) not in self.done and (b, k) not in adopted:
                self.rerun(by_safe[(b, k)], "started by an interrupted invocation, no row")
        running = set(container(a.prefix, j) for j, _, _ in self.adopted.values() if j)
        todo = [j for j in self.jobs if (j["block"], key_of(j)) not in self.done and container(a.prefix, j) not in running]
        self.log("%d jobs, %d done, %d to run" % (len(self.jobs), len(self.jobs) - len(todo), len(todo)))
        if a.dry_run:
            for j in todo:
                print(j["block"], key_of(j), "cpus=%d" % j["cpus"], "alone" if j["alone"] else "", "T=%s" % self.timeout_for(j))
            return 0
        signal.signal(signal.SIGINT, self.on_signal)
        signal.signal(signal.SIGTERM, self.on_signal)
        blocked = set()
        while todo or self.running or self.adopted:
            self.reap(todo)
            self.reap_adopted(todo)
            started = False
            if not self.stop:
                for j in list(todo):
                    if a.max_jobs and len(self.running) >= a.max_jobs:
                        break
                    if j["block"] in self.broken:
                        todo.remove(j)
                        continue
                    if not self.ready(j, todo):
                        continue
                    if not self.replay_exists(j):
                        todo.remove(j)       # fewer distinct alternatives than the plan's most
                        continue
                    t = self.timeout_for(j)
                    if t is None:          # 0: a build or an enumeration, which runs nothing
                        if (j["subject"], j["config"], j["tool"], j["setting"]) not in blocked:
                            blocked.add((j["subject"], j["config"], j["tool"], j["setting"]))
                            self.log("no T for %s %s %s %s (calibrate.py first)" % (j["subject"], j["config"], j["tool"], j["setting"]))
                        todo.remove(j)
                        continue
                    if self.blocked_by_alone(j):
                        break
                    cpus = self.pool.take(j["cpus"], alone=j["alone"])
                    if cpus is None:
                        if j["alone"]:
                            break
                        continue
                    njobs = len(self.running) + len(self.adopted) + 1       # the jobs column counts adopted ones
                    try:
                        cmd = self.command(j, cpus, njobs, t)
                    except external.MissingBuild as e:
                        # no run: the block's build or input is missing on this machine
                        self.log("missing-build %s %s: %s" % (j["block"], safe(key_of(j)), e))
                        self.broken.add(j["block"])
                        self.pool.give(cpus)
                        todo.remove(j)
                        continue
                    os.makedirs(os.path.dirname(self.row_file(j)), exist_ok=True)
                    log = open(os.path.join(a.state, "rows", safe(j["block"]), safe(key_of(j)) + ".log"), "w")
                    p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                    log.close()
                    self.running[key_of(j) + "@" + j["block"]] = (j, p, cpus, njobs)
                    self.started[key_of(j) + "@" + j["block"]] = time.time()
                    self.log("start %s %s cpus=%s jobs=%d T=%s" % (j["block"], safe(key_of(j)), rows.cpuset(cpus), njobs, t))
                    todo.remove(j)
                    started = True
                    if j["alone"]:
                        break
            elif not self.running and not self.adopted:
                break
            if not started:
                if not self.running and not self.adopted and not self.stop and todo:
                    # nothing runs and nothing can start: prerequisites not selected or not met
                    for j in todo:
                        self.log("blocked %s %s (its prerequisite is not done and not selected)" % (j["block"], safe(key_of(j))))
                    break
                time.sleep(1)
        self.log("finished: %d left%s" % (len(todo), ", stopped blocks: " + " ".join(sorted(self.broken)) if self.broken else ""))
        return 1 if self.broken or todo else 0

    def rerun(self, j, why):
        """A harness failure (Section 1.8, amendment 29): the job runs again, at most MAX_RERUNS
        times in an invocation, each rerun listed in RESULTS/reruns.tsv (README.md); a row the job
        left is kept, superseded. False when the job has had its reruns: its block stops."""
        k = (j["block"], key_of(j))
        self.reruns[k] = self.reruns.get(k, 0) + 1
        if self.reruns[k] > MAX_RERUNS:
            return False
        if os.path.exists(self.row_file(j)):
            os.replace(self.row_file(j), self.row_file(j) + ".superseded")
        path = os.path.join(self.a.results, "reruns.tsv")
        new = not os.path.exists(path)
        with open(path, "a") as fh:
            if new:
                fh.write("time\tblock\tjob\treason\n")
            fh.write("%s\t%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), j["block"], key_of(j), why))
        self.log("rerun %s %s: %s (amendment 29)" % (j["block"], safe(key_of(j)), why))
        return True

    def reap(self, todo=None):
        for k, (j, p, cpus, njobs) in list(self.running.items()):
            if p.poll() is None:
                continue
            del self.running[k]
            self.pool.give(cpus)
            oom = self.oom_killed(container(self.a.prefix, j))
            elapsed = time.time() - self.started.pop(k, time.time())
            if self.collect(j, cpus, njobs):
                self.done.add((j["block"], key_of(j)))
            elif oom and self.killed_row(j, 137, elapsed) and self.collect(j, cpus, njobs):
                self.done.add((j["block"], key_of(j)))
            elif self.stop <= 1 and todo is not None and self.rerun(
                    j, "killed (status %s), not by its own memory limit" % p.returncode if p.returncode in (137, -9)
                    else "no row (status %s)" % p.returncode):
                todo.insert(0, j)
            else:
                logf = os.path.join(self.a.state, "rows", safe(j["block"]), safe(key_of(j)) + ".log")
                why = ""
                try:
                    with open(logf) as fh:
                        why = " | ".join(ln.strip() for ln in fh.read().strip().splitlines()[-3:])
                except OSError:
                    pass
                if self.stop > 1:
                    self.log("discarded %s %s (killed by the second signal)" % (j["block"], safe(key_of(j))))
                else:
                    self.log("harness-error %s %s rc=%s: %s" % (j["block"], safe(key_of(j)), p.returncode, why[:300]))
                    self.broken.add(j["block"])

    def killed_row(self, j, rc, elapsed=None):
        """The row of a run.py job whose container Docker killed for its own memory limit
        (State.OOMKilled, amendment 29) before it wrote one: a tool failure, never rerun (amendment
        25). A deferred analysis's row is its traced run's, the analysis killed; a replay's keeps a
        verdict (inconclusive), a delays run its seed, a calibration run the seconds it lasted.
        Other jobs, and kills of the second signal, write none."""
        if rc != 137 or self.stop > 1 or j["external"] or j["setting"] in ("replay-enumerate", "learn", "warmup"):
            return False
        if j["setting"] == ANALYSIS:
            with open(self.deferred_file(j)) as fh:
                row = json.load(fh)
            d = json.loads(row["detail"]) if row.get("detail") else {}
            d["analysis"] = "container killed"
            row.update({"reported": False, "failure": "analysis killed", "detail": json.dumps(d, sort_keys=True),
                        "outcome": "tool-failure"})
        else:
            row = {"subject": j["subject"], "config": j["config"], "np": j["np"], "mpi": j["mpi"], "version": j["version"],
                   "label": "", "tool": j["tool"], "setting": j["setting"], "run": j["run"], "result": "KILLED",
                   "seconds": round(elapsed, 1) if elapsed is not None else "", "says": "", "symptom": False,
                   "reported": False, "failure": "container killed by its memory limit before T",
                   "detail": json.dumps({"analysis": "deferred", "trace": "empty"}) if j.get("defer") else ""}
            seed = str(j["run"] * 100 + j["np"]) if j["tool"] == "delays" else ""
            row.update(rows.common(self.timeout_for(j) or 0, seed=seed, commit=self.a.commit,
                                   category="inconclusive" if j["setting"] == "replay" else "tool-failure"))
        os.makedirs(os.path.dirname(self.row_file(j)), exist_ok=True)
        with open(self.row_file(j) + ".tmp", "w") as fh:
            json.dump(row, fh)
        os.replace(self.row_file(j) + ".tmp", self.row_file(j))
        self.log("killed %s %s: a tool-failure row (amendment 25)" % (j["block"], safe(key_of(j))))
        return True

    def on_signal(self, signum, frame):
        self.stop += 1
        self.log("signal %d: %s" % (signum, "no new jobs" if self.stop == 1 else "killing the running containers"))
        if self.stop > 1:
            for j, p, _, _ in self.running.values():
                subprocess.run(["docker", "kill", container(self.a.prefix, j)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    if not os.environ.get("MACHINE"):
        sys.exit("schedule.py: set MACHINE (e.g. MACHINE=a-p13); every row records it as host")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", default=os.path.join(HERE, "plan.csv"))
    ap.add_argument("--blocks", default=".", help="a regular expression the plan's block names are searched with")
    ap.add_argument("--configs", default=".", help="a regular expression the configurations are searched with")
    ap.add_argument("--tools", default=".", help="a regular expression the jobs' tool:setting is searched with")
    ap.add_argument("--runs", type=int, default=None, help="at most this many runs per row (the smoke test)")
    ap.add_argument("--timeout", type=int, default=None, help="T for every job instead of the calibrated ones")
    ap.add_argument("--results", default=os.path.join(HERE, "results"))
    ap.add_argument("--state", default=os.environ.get("PROTOCOL_STATE_DIR", os.path.join(os.path.expanduser("~"), "protocol", "state")))
    ap.add_argument("--repo", default=ROOT)
    ap.add_argument("--pool", default=POOL)
    ap.add_argument("--prefix", default="proto")
    ap.add_argument("--commit", default=os.environ.get("STRETTO_COMMIT", ""))
    ap.add_argument("--max-jobs", type=int, default=0)
    ap.add_argument("--suite-select", default=".", help="the suites' programs (a regular expression on their names)")
    ap.add_argument("--suite-limit", type=int, default=0, help="at most this many programs per suite (the smoke test)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.commit:
        from provenance import code_commit  # noqa: E402
        a.commit = code_commit()
    a.results, a.state, a.repo = (os.path.abspath(x) for x in (a.results, a.state, a.repo))
    with open(a.plan, newline="") as fh:
        plan = list(csv.DictReader(fh))
    jobs = jobs_of(plan, a.blocks, a.runs, a.configs, a.tools)
    if not jobs:
        print("no job selected by --blocks %r" % a.blocks, file=sys.stderr)
        return 2
    return Scheduler(a, jobs).run()


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "experiments"))
    sys.exit(main())
