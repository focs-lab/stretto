#!/usr/bin/env python3
"""Multi-node runs inside one Slurm allocation (experiments/cluster/README.md, docs/cluster.md).

The job script (slurm.sbatch) runs this script on the allocation's first node; every step
starts its ranks through Slurm, never over ssh (the compute nodes take none). The launcher is a
parameter of the job:

  srun     experiments/cluster/srun-mpirun: srun --mpi=pmix|pmi2 for every rank, inside the image
           when the runtime is apptainer (apptainer exec ... on every node); the harness itself
           (this script, the analysis, stretto.confirm) runs outside the image, on Python 3.10+
  mpirun   the MPI's own mpirun inside the allocation, which starts its daemons with srun: a
           native build only (modules.sh), since an mpirun inside the image has no srun

Programs built with either MPI library run under either launcher (the image's MPICH is built for
PMIx: srun --mpi=pmix, or Open MPI's mpirun as on one node). The steps share one output directory
OUT on the shared file system:

  setup    the job's record (OUT/job.json: machine, scheduler, job id, nodes, ranks per node,
           launcher, runtime, versions, commit, image and its sha256) and the environment every
           later step runs under (OUT/mpi.env); a probe checks that every node answers and sees OUT
  bimpl    the eager limit between two ranks on different nodes, and on one node, by
           experiments/b_impl.py (OUT/b_impl-<mpi>-{nodes,node}.csv)
  run      N runs of a program with a tool: plain, traced or delays (the replay library's
           random delays of the campaign, D50). A traced run writes its per-rank traces to the
           nodes' local disks (LOCAL), and they are collected into OUT/<name>/traced/runNN/trace
  analyse  the analysis of every collected trace (OUT/<name>/analyses.csv): its findings, the
           nodes the ranks ran on, and which alternatives take a message from another node
  replay   every finding of every trace forced by stretto.confirm on the same nodes, with its
           control (OUT/<name>/replays.csv)
  protocol one run of the campaign's own harness (experiments/protocol/run.py run: its file
           unchanged, its launcher constant set to the job's launcher) on the job's nodes, the
           row in OUT/protocol/results/<subject>/<tool>[-<setting>].csv
  protocol-replays  the campaign's replays (experiments/protocol/replays.py, unchanged) of the
           alternatives those traced runs predicted, on the job's nodes

Rows carry the campaign's common columns (experiments/protocol/rows.py: host, cpus, jobs,
timeout, seed, commit, superseded, outcome) and the job's: nodes, ppn, map, scheduler, job.

Where the files go: the harness's own temporary files (pin files, replay traces, run.py's run and
trace directories, b_impl.py's program) must be visible on every node, so the steps run with
TMPDIR=OUT/tmp; Open MPI's session directory, which holds sockets, stays node-local
(orte_tmpdir_base=/tmp).

usage: job.py setup --out OUT [--launcher srun|mpirun] [--pmi pmix|pmi2] [--runtime apptainer|native]
                    [--sif SIF] [--binds DIRS] [--mpirun PATH] [--local DIR] [--net IFACE]
                    [--hosts FILE] [--ppn N]       (--hosts outside Slurm only)
       job.py bimpl --out OUT [--mpi M]
       job.py run --out OUT --name NAME --tool plain|traced|delays --np N [--mpi M] [--runs R]
                  [--timeout T] [--ok REGEX] [--map node|slot] [--cwd DIR] -- EXE [ARGS...]
       job.py analyse --out OUT --name NAME [--sp]
       job.py replay --out OUT --name NAME [--timeout T] [--all] [--max K] -- EXE [ARGS...]
       job.py protocol --out OUT -- <run.py run arguments without --row>
       job.py protocol-replays --out OUT --subject S --config C --timeout T [--max K]
"""
import argparse
import csv
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
PROTOCOL = os.path.join(ROOT, "experiments", "protocol")
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
sys.path.insert(0, PROTOCOL)
import rows  # noqa: E402  (the campaign's common columns, read only)

SRUN_MPIRUN = os.path.join(HERE, "srun-mpirun")
TRACER = os.path.join(ROOT, "tracer", "bin", "{mpi}", "libstretto-trace.so")
PINLIB = os.path.join(ROOT, "replay", "bin", "{mpi}", "libstretto-pin.so")
JOB_FIELDS = ["nodes", "ppn", "map", "scheduler", "job"]


# ------------------------------------------------------------------ the job

def scheduler():
    """(scheduler, job id) from the environment Slurm gives the job."""
    if os.environ.get("SLURM_JOB_ID"):
        return "slurm", os.environ["SLURM_JOB_ID"]
    return os.environ.get("STRETTO_SCHEDULER", "none"), os.environ.get("STRETTO_JOB_ID", str(os.getpid()))


def read_hosts(path):
    """The distinct hosts of a host list, in order: one per line (scontrol show hostnames),
    repeated once per slot (a PBS nodefile), or "host slots=N" (an Open MPI hostfile)."""
    out = []
    with open(path) as fh:
        for line in fh:
            h = line.split("#")[0].split()
            if h and h[0] not in out:
                out.append(h[0])
    return out


def tasks_per_node(text):
    """The ranks per node of Slurm's SLURM_NTASKS_PER_NODE or SLURM_TASKS_PER_NODE ("8(x2)", "8,4"):
    the first node's."""
    m = re.match(r"\d+", text or "")
    return int(m.group(0)) if m else 1


def slurm_nodes():
    """The allocation's nodes and ranks per node, from Slurm's environment."""
    p = subprocess.run(["scontrol", "show", "hostnames", os.environ["SLURM_JOB_NODELIST"]], stdout=subprocess.PIPE,
                       universal_newlines=True, check=True)
    return p.stdout.split(), tasks_per_node(os.environ.get("SLURM_NTASKS_PER_NODE") or os.environ.get("SLURM_TASKS_PER_NODE"))


def load(out):
    with open(os.path.join(out, "job.json")) as fh:
        return json.load(fh)


def mpi_env(job, mapping="node"):
    """The environment of every launch of the job: the launcher (STRETTO_MPIRUN, which
    stretto.confirm and b_impl.py take too), the placement (by node: rank r on node r mod n; by
    slot: PPN ranks on a node before the next), one core per rank, the image's prefix and
    compilers, the network interface; MPI's session directory node-local, the harness's
    temporary files on the shared file system."""
    env = {"STRETTO_MPIRUN": job["mpirun"], "STRETTO_PPN": str(job["ppn"]),
           "OMPI_MCA_rmaps_base_mapping_policy": "node" if mapping == "node" else "slot",
           "OMPI_MCA_hwloc_base_binding_policy": "core",
           "OMPI_MCA_orte_tmpdir_base": "/tmp",
           "TMPDIR": job["tmp"]}
    if job.get("hostfile"):
        env["OMPI_MCA_orte_default_hostfile"] = job["hostfile"]
    if job["launcher"] == "srun":
        env.update({"STRETTO_SRUN_MPI": job["pmi"], "STRETTO_NODE_EXEC": job.get("node_exec", "")})
    if job.get("node_exec"):
        # b_impl.py compiles its program with the image's wrappers
        env.update({"STRETTO_MPICC_OPENMPI": job["node_exec"] + " /usr/bin/mpicc.openmpi",
                    "STRETTO_MPICC_MPICH": job["node_exec"] + " /usr/bin/mpicc.mpich"})
    if job.get("net"):
        # MPI's TCP transport (and Open MPI's own traffic) on that interface, MPICH's UCX too
        env.update({"OMPI_MCA_btl_tcp_if_include": job["net"], "OMPI_MCA_oob_tcp_if_include": job["net"],
                    "UCX_NET_DEVICES": job["net"], "STRETTO_TCP_IF": job["net"]})
    return env


def apply_env(job, mapping="node"):
    os.environ.update(mpi_env(job, mapping))
    os.makedirs(job["tmp"], exist_ok=True)


def version_of(cmd):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        return p.stdout.strip().splitlines()[0] if p.stdout.strip() else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def setup(a):
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    sched, jid = scheduler()
    hostfile = ""
    if sched == "slurm":
        hosts, ppn = slurm_nodes()
        ppn = a.ppn or ppn
    else:
        if not (a.hosts and a.ppn):
            raise SystemExit("job.py: outside a Slurm allocation, give --hosts and --ppn")
        hosts, ppn = read_hosts(a.hosts), a.ppn
        hostfile = os.path.join(out, "hostfile")
        with open(hostfile, "w") as fh:
            fh.writelines("%s slots=%d\n" % (h, ppn) for h in hosts)
    if a.runtime == "apptainer" and a.launcher == "mpirun":
        raise SystemExit("job.py: an mpirun inside the image cannot start daemons on the other nodes (no srun "
                         "inside, no ssh between nodes): use --launcher srun with --runtime apptainer")
    node_exec = ""
    if a.runtime == "apptainer":
        if not a.sif:
            raise SystemExit("job.py: --runtime apptainer needs --sif")
        node_exec = "apptainer exec" + (" --bind " + a.binds if a.binds else "") + " " + os.path.abspath(a.sif)
    mpirun = SRUN_MPIRUN if a.launcher == "srun" else (a.mpirun or shutil.which("mpirun") or "/usr/bin/mpirun.openmpi")
    inside = node_exec.split()
    from provenance import code_commit
    job = {"machine": rows.host(), "scheduler": sched, "job": jid, "nodes": hosts, "ppn": ppn, "hostfile": hostfile,
           "tmp": os.path.join(out, "tmp"), "local": a.local or os.environ.get("STRETTO_LOCAL") or "/tmp",
           "launcher": a.launcher, "pmi": a.pmi, "runtime": a.runtime, "mpirun": mpirun, "node_exec": node_exec,
           "net": a.net,
           "versions": {"openmpi": version_of(inside + ["/usr/bin/mpirun.openmpi", "--version"]) if inside or a.launcher == "srun"
                        else version_of([mpirun, "--version"]),
                        "mpich": version_of(inside + ["mpichversion"]),
                        "srun": version_of(["srun", "--version"]) if sched == "slurm" else "",
                        "python": sys.version.split()[0]},
           "commit": os.environ.get("STRETTO_COMMIT") or code_commit(),
           "image": {"path": os.path.abspath(a.sif), "sha256": sha256(a.sif)} if a.sif else {},
           "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    apply_env(job)
    # every node answers, and the job's processes see the same file system there
    probe = subprocess.run(["/usr/bin/timeout", "300", mpirun, "-np", str(len(hosts)), "--map-by", "node", "sh", "-c",
                            'echo "$(hostname) $(test -d %s && echo shared || echo not-shared)"' % shlex.quote(out)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, stdin=subprocess.DEVNULL)
    seen = sorted(probe.stdout.split("\n")[:-1])
    job["probe"] = seen
    with open(os.path.join(out, "job.json"), "w") as fh:
        json.dump(job, fh, indent=1)
    with open(os.path.join(out, "mpi.env"), "w") as fh:
        fh.writelines("export %s=%s\n" % (k, shlex.quote(v)) for k, v in sorted(mpi_env(job).items()))
    print("\n".join(seen) or probe.stderr[-2000:])
    if probe.returncode != 0 or len(seen) != len(hosts) or any(not s.endswith(" shared") for s in seen):
        raise SystemExit("job.py: the probe did not reach every node with the shared directory %s: %s"
                         % (out, probe.stderr[-1000:]))
    return 0


# ------------------------------------------------------------------ rows

def job_columns(job, mapping):
    return {"nodes": len(job["nodes"]), "ppn": job["ppn"], "map": mapping, "scheduler": job["scheduler"], "job": job["job"]}


def append(path, row):
    """Append a row to a CSV table, its header from the first row."""
    new = not os.path.exists(path) or os.path.getsize(path) == 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row), lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow(row)


def common(job, timeout, seed="", outcome=""):
    return rows.common(timeout, seed=seed, jobs=1, cpus="", commit=job["commit"], category=outcome)


# ------------------------------------------------------------------ steps

# the settings of experiments/b_impl.py that two ranks on two nodes can run: not the forced
# shared-memory one; the TCP one only on a named interface (setup --net)
ACROSS = {"openmpi": ["openmpi-default", "openmpi-tcp-64"], "mpich": ["mpich-default", "mpich-nolocal-ucx-tcp"]}


def bimpl(a):
    """b_impl.py twice: two ranks on two nodes (by node), and two ranks on one node (by slot)."""
    job = load(a.out)
    rc = 0
    for mapping, where in (("node", "nodes"), ("slot", "node")):
        apply_env(job, mapping)
        out = os.path.join(os.path.abspath(a.out), "b_impl-%s-%s.csv" % (a.mpi, where))
        settings = []
        if where == "nodes":
            settings = ["--settings"] + [x for x in ACROSS[a.mpi] if job.get("net") or "tcp-64" not in x]
        p = subprocess.run([sys.executable, os.path.join(ROOT, "experiments", "b_impl.py"), "--mpi", a.mpi,
                            "--timeout", str(a.timeout), "--out", out] + settings)
        rc = rc or p.returncode
    return rc


def collect(job, src, dest):
    """Every node's part of a node-local directory, copied into dest on the shared file system
    and removed from the node: one process per node."""
    os.makedirs(dest, exist_ok=True)
    script = 'if [ -d "$0" ]; then cp -a "$0"/. "$1"/ && rm -rf "$0"; fi'
    p = subprocess.run(["/usr/bin/timeout", "600", job["mpirun"], "-np", str(len(job["nodes"])), "--map-by", "node",
                        "sh", "-c", script, src, dest], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise SystemExit("job.py: collecting %s failed: %s" % (src, p.stderr[-1000:]))


def delays(np, i):
    """The campaign's random delays of run i (experiments/protocol/run.py, D50)."""
    import run as protocol_run
    return protocol_run.delays(np, i)


def run(a):
    from stretto.confirm import classify_rc
    job = load(a.out)
    apply_env(job, a.map)
    exe, args = a.cmd[0], a.cmd[1:]
    base = os.path.join(os.path.abspath(a.out), a.name)
    lib = (TRACER if a.tool == "traced" else PINLIB).format(mpi=a.mpi)
    if a.tool in ("traced", "delays") and not os.path.isfile(lib):
        raise SystemExit("job.py: %s is missing (make -C tracer, make -C replay, MPI=%s)" % (lib, a.mpi))
    for i in range(1, a.runs + 1):
        rd = os.path.join(base, a.tool, "run%02d" % i)
        if os.path.exists(rd):
            shutil.rmtree(rd)
        os.makedirs(rd)
        xs, seed = [], ""
        if a.tool == "traced":
            local = os.path.join(job["local"], "stretto-%s" % re.sub(r"[^A-Za-z0-9._-]", "_", job["job"]), a.name, "run%02d" % i)
            xs = ["-x", "LD_PRELOAD=" + lib, "-x", "STRETTO_TRACE_DIR=" + local]
        elif a.tool == "delays":
            xs = ["-x", "LD_PRELOAD=" + lib]
            for k, v in delays(a.np, i).items():
                xs += ["-x", "%s=%s" % (k, v)]
            seed = str(i * 100 + a.np)
        cmd = ["/usr/bin/timeout", "-k", "5", str(a.timeout), job["mpirun"], "-np", str(a.np)] + xs + [exe] + args
        t0 = time.monotonic()
        p = subprocess.run(cmd, cwd=a.cwd or rd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, errors="replace", stdin=subprocess.DEVNULL)
        el = time.monotonic() - t0
        for k, v in (("stdout", p.stdout), ("stderr", p.stderr)):
            with open(os.path.join(rd, k), "w") as fh:
                fh.write(v)
        result = classify_rc(p.returncode, p.stderr, el, a.timeout)
        ok = result == "FINISHED" and (not a.ok or bool(re.search(a.ok, p.stdout + p.stderr)))
        ranks = ""
        if a.tool == "traced":
            collect(job, local, os.path.join(rd, "trace"))
            ranks = len([f for f in os.listdir(os.path.join(rd, "trace")) if f.startswith("rank-")])
        row = {"name": a.name, "exe": exe, "args": " ".join(args), "tool": a.tool, "run": i, "np": a.np, "mpi": a.mpi,
               "result": result, "seconds": round(el, 1), "ok": ok, "trace_ranks": ranks}
        row.update(job_columns(job, a.map))
        row.update(common(job, a.timeout, seed, rows.outcome(not ok, not ok)))
        append(os.path.join(base, "runs.csv"), row)
        print("%s %s run %d: %s %.1f s ok=%s%s" % (a.name, a.tool, i, result, el, ok,
                                                  " ranks traced %s/%d" % (ranks, a.np) if a.tool == "traced" else ""),
              flush=True)
    return 0


def traces(a):
    d = os.path.join(os.path.abspath(a.out), a.name, "traced")
    return [(r, os.path.join(d, r, "trace")) for r in sorted(os.listdir(d)) if os.path.isdir(os.path.join(d, r, "trace"))]


def cross_node(t, f):
    """Whether the finding's alternative takes a message from a rank on another node than the
    receiver's: None when the trace does not name the nodes or the finding has no pair."""
    if not t.node or f.recv is None or f.send is None:
        return None
    return t.node.get(f.recv[0]) != t.node.get(f.send[0])


def analyse(a):
    from stretto import trace
    from stretto.report import analyze
    job = load(a.out)
    for run_name, tdir in traces(a):
        t = trace.load(tdir)
        t0 = time.monotonic()
        rep = analyze(t, with_sp=a.sp)
        el = time.monotonic() - t0
        with open(os.path.join(os.path.dirname(tdir), "report.txt"), "w") as fh:
            fh.write("\n".join(str(f) for f in rep.findings) + "\nstatus: %s\n" % rep.status())
        counts, cross = {}, 0
        for f in rep.findings:
            k = "%s/%s" % (f.cls, f.level)
            counts[k] = counts.get(k, 0) + 1
            cross += bool(cross_node(t, f))
        row = {"name": a.name, "run": run_name, "np": t.np, "records": sum(len(v) for v in t.ranks.values()),
               "analyse_s": round(el, 1), "status": rep.status(), "trace_nodes": len(set(t.node.values())) if t.node else "",
               "findings": ";".join("%s=%d" % kv for kv in sorted(counts.items())), "cross_node": cross}
        row.update(job_columns(job, ""))
        row.update(common(job, 0))
        append(os.path.join(os.path.abspath(a.out), a.name, "analyses.csv"), row)
        print("%s %s: %s, %s, %d of them across nodes" % (a.name, run_name, rep.status(), row["findings"] or "no findings",
                                                         cross), flush=True)
    return 0


def replay(a):
    from stretto import confirm, trace
    from stretto.report import analyze
    job = load(a.out)
    apply_env(job, a.map)
    exe, args = a.cmd[0], a.cmd[1:]
    for run_name, tdir in traces(a):
        t = trace.load(tdir)
        rep = analyze(t, with_sp=a.sp)
        confirm.with_sync(t, tdir)
        if a.max:
            rep.findings = rep.findings[:a.max]
        res = confirm.confirm(rep, t.np, exe, args, timeout=a.timeout, mpi=a.mpi, only_errors=not a.all, t=t)
        for c in res:
            row = {"name": a.name, "run": run_name, "cls": c.finding.cls, "level": c.finding.level, "B": c.finding.B,
                   "cross_node": cross_node(t, c.finding), "pin": c.pin[:200], "result": c.result,
                   "pin_matched": c.pin_matched, "control": c.control, "run_valid": c.run_valid,
                   "misapplied": c.misapplied[:200], "verdict": c.verdict, "strict": c.strict}
            row.update(job_columns(job, a.map))
            row.update(common(job, a.timeout, outcome=c.verdict))
            append(os.path.join(os.path.abspath(a.out), a.name, "replays.csv"), row)
            print("%s %s [%s %s] %s -> %s, control %s: %s (strict %s)" % (
                a.name, run_name, c.finding.cls, c.finding.level, c.pin[:60], c.result, c.control or "-", c.verdict,
                c.strict or "-"), flush=True)
    return 0


# experiments/protocol/run.py as it is, with two of its constants set to the job's before it runs:
# its launcher (LAUNCHER, Open MPI's mpirun of the image) and, with a network interface, the
# interface of its eager-0 configurations (subjects.EAGER0 names the loopback, which carries
# nothing between nodes)
RUN_PY = ("import os, sys; sys.path.insert(0, %r); sys.argv[0] = os.path.join(%r, 'run.py'); "
          "import run, subjects; run.LAUNCHER = os.environ['STRETTO_MPIRUN']; "
          "subjects.EAGER0['OMPI_MCA_btl_tcp_if_include'] = os.environ.get('STRETTO_TCP_IF') or 'lo'; "
          "sys.exit(run.main())") % (PROTOCOL, PROTOCOL)


def protocol_env(job, d):
    apply_env(job, "slot")
    os.environ.setdefault("STRETTO_COMMIT", job["commit"])
    os.environ.setdefault("PROTOCOL_STATE", os.path.join(d, "state"))
    os.environ.setdefault("KEEP_TRACES", os.path.join(d, "keep"))


def protocol(a):
    """One run of experiments/protocol/run.py on the job's nodes, as the campaign runs it on one
    node. run.py sets its own placement (every rank on a hardware thread of its own, filled in
    order), so the ranks fill a node's slots (PPN) before the next. The row lands in
    OUT/protocol/results/<subject>/<tool>[-<setting>].csv with the job's columns, where
    protocol-replays (replays.py) finds the traced runs."""
    job = load(a.out)
    d = os.path.join(os.path.abspath(a.out), "protocol")
    protocol_env(job, d)
    ap = argparse.ArgumentParser()
    for k in ("subject", "tool", "setting"):
        ap.add_argument("--" + k, required=True)
    known, _ = ap.parse_known_args(a.cmd)
    tag = re.sub(r"[^A-Za-z0-9._=-]+", "_", "-".join(x.lstrip("-") for x in a.cmd))
    row = os.path.join(d, "rows", tag + ".json")
    p = subprocess.run([sys.executable, "-c", RUN_PY, "run"] + a.cmd + ["--row", row, "--outputs", os.path.join(d, "outputs")])
    if p.returncode == 0 and os.path.exists(row):
        import run as protocol_run
        with open(row) as fh:
            r = json.load(fh)
        r.update(job_columns(job, "slot"))
        table = known.tool if known.setting == "-" else "%s-%s" % (known.tool, known.setting)
        rows.append(os.path.join(d, "results", known.subject, table + ".csv"), [r], protocol_run.FIELDS + JOB_FIELDS)
    return p.returncode


def protocol_replays(a):
    """The replays of the campaign (experiments/protocol/replays.py, unchanged) for the traced
    runs the protocol step made: enumerate the distinct alternatives of their pins, then replay
    the first --max of them, each with its control, on the job's nodes (stretto.confirm takes
    the job's launcher from STRETTO_MPIRUN)."""
    job = load(a.out)
    d = os.path.join(os.path.abspath(a.out), "protocol")
    protocol_env(job, d)
    res = os.path.join(d, "results")
    script = os.path.join(PROTOCOL, "replays.py")
    rdir = os.path.join(d, "rows")
    p = subprocess.run([sys.executable, script, "enumerate", "--subject", a.subject, "--config", a.config,
                        "--results", res, "--row", os.path.join(rdir, "enumerate-%s-%s.json" % (a.subject, a.config))])
    if p.returncode:
        return p.returncode
    import replays as protocol_replays_mod
    alts = rows.read(protocol_replays_mod.alternatives_path(res, a.subject, a.config))
    rc = 0
    for alt in alts[:a.max or None]:
        k = int(alt["index"])
        row = os.path.join(rdir, "replay-%s-%s-%d.json" % (a.subject, a.config, k))
        p = subprocess.run([sys.executable, script, "replay", "--subject", a.subject, "--config", a.config, "--index", str(k),
                            "--timeout", str(a.timeout), "--results", res, "--row", row,
                            "--outputs", os.path.join(d, "outputs")])
        rc = rc or p.returncode
        if p.returncode == 0:
            import run as protocol_run
            with open(row) as fh:
                r = json.load(fh)
            r.update(job_columns(job, "slot"))
            rows.append(os.path.join(res, a.subject, "stretto-replay.csv"), [r], protocol_run.FIELDS + JOB_FIELDS)
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="step", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--out", required=True)
    s.add_argument("--launcher", choices=["srun", "mpirun"], default="srun")
    s.add_argument("--pmi", choices=["pmix", "pmi2"], default="pmix", help="srun --mpi (launcher srun)")
    s.add_argument("--runtime", choices=["apptainer", "native"], default="apptainer")
    s.add_argument("--sif", default="", help="the image (runtime apptainer); its sha256 is recorded")
    s.add_argument("--binds", default="", help="apptainer --bind: the shared and node-local directories, comma-separated")
    s.add_argument("--mpirun", default="", help="the MPI's mpirun (launcher mpirun; default: mpirun on PATH)")
    s.add_argument("--local", default="", help="node-local directory for traces (default STRETTO_LOCAL, else /tmp)")
    s.add_argument("--net", default="", help="the network interface MPI's TCP transport uses between nodes")
    s.add_argument("--hosts", default="", help="outside Slurm: the host list")
    s.add_argument("--ppn", type=int, default=0, help="ranks per node (default: Slurm's tasks per node)")
    for name in ("bimpl", "run", "analyse", "replay", "protocol", "protocol-replays"):
        p = sub.add_parser(name)
        p.add_argument("--out", required=True)
        if name == "protocol-replays":
            p.add_argument("--subject", required=True)
            p.add_argument("--config", required=True)
            p.add_argument("--timeout", type=int, required=True)
            p.add_argument("--max", type=int, default=0, help="replay the first K alternatives only")
        if name in ("bimpl", "run", "replay"):
            p.add_argument("--mpi", default="openmpi", choices=["openmpi", "mpich"])
            # b_impl.py's: the seconds after which an exchange counts as hung
            p.add_argument("--timeout", type=int, default=10 if name == "bimpl" else 60)
            p.add_argument("--map", default="node", choices=["node", "slot"])
        if name in ("run", "analyse", "replay"):
            p.add_argument("--name", required=True)
            p.add_argument("--sp", action="store_true", help="run the single-path explorer too")
        if name == "run":
            p.add_argument("--tool", required=True, choices=["plain", "traced", "delays"])
            p.add_argument("--np", type=int, required=True)
            p.add_argument("--runs", type=int, default=1)
            p.add_argument("--ok", default="", help="a regular expression a correct run's output matches")
            p.add_argument("--cwd", default="", help="the program's working directory (default: the run's own)")
        if name == "replay":
            p.add_argument("--all", action="store_true", help="also replay informational findings")
            p.add_argument("--max", type=int, default=0, help="at most this many findings per trace")
        if name in ("run", "replay", "protocol"):
            p.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    if getattr(a, "cmd", None) and a.cmd[0] == "--":
        a.cmd = a.cmd[1:]
    return {"setup": setup, "bimpl": bimpl, "run": run, "analyse": analyse, "replay": replay, "protocol": protocol,
            "protocol-replays": protocol_replays}[a.step](a)


if __name__ == "__main__":
    sys.exit(main())
