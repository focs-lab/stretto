#!/usr/bin/env python3
"""Stretto on MPI-PRV's thirteen applications, inside MPI-PRV's image (experiments/mpiprv/prv.sh).

The applications are the binaries the artifact ships under /home/ubuntu/RQ2-3/MPIBenchmark,
with the command lines of its MUST scripts and the process counts of its MPI-PRV scripts, up
to MAXNP ranks (default 24: the container has four processors and 16 GB). The binaries are
built with MPI-PRV's instrumentation, which only appends its events to output.txt; Stretto's
tracer (an LD_PRELOAD library built for the same Open MPI, 4.1.6) records their MPI calls.
Each configuration runs from a copy of its directory:
  trace     one traced run; then `stretto analyze` with the corpus harness's limits on the
            single-path explorer (traces of at most 600 events, 30 000 states per alternative;
            ANALYSE_ARGS overrides them), timed and with its peak memory (/usr/bin/time)
  replays   every L1 alternative (up to 30, a random sample beyond), each replayed with the pins
            of its horizon and with its control (experiments/replay_sample.py)
  guard     a second traced run with the buffer guard (STRETTO_GUARD=1), analysed
  confirm   the error findings of either trace that carry a pin, each replayed with its pin and
            a control (`stretto confirm`, with Open MPI's default eager limits); what either
            trace says holds only below the eager threshold (an error predicted for B=0 or B < n,
            a finished run that needs buffering) again with every message above 8 bytes sent by
            rendezvous (columns rendezvous and guard_rendezvous)
Replays and confirmations need a control that finishes, so they are skipped when the traced run
itself did not end with status 0 (kf-ray's rank 0 ends with a segmentation fault).
Writes experiments/results/mpiprv/apps.csv and apps-replays.csv; the runs stay under
experiments/mpiprv/work/apps/ (git-ignored).

usage: apps.py [--select REGEX] [--maxnp N] [--timeout S]"""
from __future__ import annotations
import argparse
import collections
import csv
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from provenance import code_commit  # noqa: E402

BENCH = "/home/ubuntu/RQ2-3/MPIBenchmark"
WORK = os.path.join(HERE, "work", "apps")
OUT = os.path.join(ROOT, "experiments", "results", "mpiprv")
TRACER = os.path.join(ROOT, "tracer", "bin", "openmpi", "libstretto-trace.so")
MPIRUN = "/usr/bin/mpirun.openmpi"
ERROR_CLASSES = {"1a", "1c", "1d", "2-buffering", "2-collective", "2-blocked", "3-local", "abort"}
ANALYSE = (os.environ.get("ANALYSE_ARGS") or "--sp-max-events 600 --max-states 30000").split()

# app, directory, executable, {np: arguments}; the arguments of the artifact's MUST scripts
APPS = [
    ("DTG", "DTG", "./dtg", {5: ""}),
    ("integrate", "integrate", "./test", {2: "", 8: "", 10: ""}),
    ("diffusion2d", "diffusion2d", "./test", {4: ""}),
    ("Gauss_elim", "Gauss_elim", "./test", {2: "", 4: "", 8: ""}),
    ("image-manip", "image-manip", "./test", {n: "-f dot.bmp -o result.bmp -x 5 -y 5" for n in (4, 24, 127)}),
    ("Congrad", "Congrad", "./congrad", {4: "4"}),
    ("poisson_mpi", "poisson_mpi", "./poisson", {n: "64" for n in (4, 8, 24, 64, 96, 127)}),
    ("Heat", "Heat", "./test", {4: ""}),
    ("adept-kernel-mpi", "adept-kernel-mpi", "./kernel",
     {n: "-b stencil -o 27 -s 512 -d double -a overlapped" for n in (4, 8, 24, 64, 127)}),
    ("kf-ray", "kf-ray/src", "./kfray", {11: "-i scene3 -o img3 -b 3 -l 12"}),
    ("clustalW", "clustalW", "./clustalw-mpi", {n: "-infile=CFTR.input -newtree=dele.mytree" for n in (5, 20, 127)}),
    ("ParMETIS", "ParMETIS/build/Linux-x86_64/programs", "./pm_ptest", {4: "rotor.graph"}),
    ("MiniAMR", "MiniAMR/ref", "./miniAMR.x", {4: "--npx 2 --npy 2 --npz 1", 8: "--npx 2 --npy 2 --npz 2",
                                               16: "--npx 4 --npy 2 --npz 2"}),
]
# the environment an application needs, {top} its copied directory: pm_ptest's run path names
# directories of the machine it was built on, and its libmetis.so lies in the METIS build
APP_ENV = {"ParMETIS": {"LD_LIBRARY_PATH": "{top}/METIS/build/libmetis"}}
# Open MPI over TCP with a 64-byte eager limit sends every message above 8 bytes by rendezvous
# (experiments/results/b_impl.csv, setting openmpi-tcp-64): the setting in which to replay a
# prediction that needs B = 0 or B below a message size
RENDEZVOUS = ["--mca", "btl", "self,tcp", "--mca", "btl_tcp_if_include", "lo", "--mca", "btl_tcp_eager_limit", "64",
              "--mca", "btl_self_eager_limit", "64"]
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from stretto import trace as strace  # noqa: E402
from stretto.trace import ANY  # noqa: E402
from stretto.confirm import all_matched, classify_rc, horizon_pins, required_matches, run_pinned  # noqa: E402
from stretto.report import analyze as s_analyze  # noqa: E402


def run(cmd, cwd, timeout, env=None):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           env={**os.environ, "OMPI_MCA_rmaps_base_oversubscribe": "1", "OMP_NUM_THREADS": "1", **(env or {})})
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired as e:
        return "TIMEOUT", (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), ""


def traced(d, exe, args, np_, tdir, guard, timeout):
    shutil.rmtree(tdir, ignore_errors=True)
    os.makedirs(tdir)
    cmd = ["/usr/bin/timeout", "-k", "5", str(timeout), MPIRUN, "-np", str(np_), "-x", f"LD_PRELOAD={TRACER}",
           "-x", f"STRETTO_TRACE_DIR={tdir}", "-x", f"STRETTO_GUARD={1 if guard else 0}", exe, *args.split()]
    rc, out, err = run(cmd, d, timeout + 60)
    open(tdir + ".log", "w").write(f"$ {' '.join(cmd)}\nrc {rc}\n{out}\n{err}")
    return "TIMEOUT" if rc in (124, 137, "TIMEOUT") else f"rc={rc}"


def analyse(tdir, timeout):
    cmd = ["/usr/bin/time", "-f", "TIME %e %M", sys.executable, "-m", "stretto", "analyze", "--json", *ANALYSE, tdir]
    rc, out, err = run(cmd, ROOT, timeout, env={"PYTHONPATH": os.path.join(ROOT, "analysis")})
    open(tdir + ".report.json", "w").write(out)
    m = re.search(r"TIME ([0-9.]+) (\d+)", err or "")
    res = {"analysis_rc": rc, "analysis_s": m.group(1) if m else "", "analysis_kb": m.group(2) if m else ""}
    try:
        j = json.loads(out)
    except ValueError:
        res["summary"] = (err or "")[-300:].replace("\n", " ")
        return res, []
    res["summary"] = j["summary"]
    m = re.search(r"(\d+) sends, (\d+) receives, (\d+) wildcard receives, (\d+) probes, (\d+) collective records", j["summary"])
    if m:
        res.update(sends=m.group(1), recvs=m.group(2), wildcards=m.group(3), probes=m.group(4), colls=m.group(5))
    return res, j["findings"]


def observed_pins(t):
    """STRETTO_PIN for every wildcard receive of the trace, to the sender it took."""
    tok = []
    for r in sorted(t.recvs.values(), key=lambda r: (r.rank, r.idx)):
        sk = t.observed.get(r.key)
        if r.widx is None or sk is None:
            continue
        m = t.sends[sk]
        tok.append(f"{r.rank}:{r.widx}:{m.rank}" + (f":{m.tag}" if r.tag == ANY else ""))
    return ["STRETTO_PIN=" + ",".join(tok)] if tok else []


def rendezvous_replays(d, exe, args, np_, tdir, timeout):
    """What holds only below the eager threshold, which a replay with Open MPI's default limits
    cannot show, replayed under RENDEZVOUS: each error prediction for B = 0 or B < n with the pins
    of its horizon at B = 0, and its control; and a finished run that would deadlock below a
    threshold (2-buffering) with every wildcard receive pinned to the sender it took."""
    try:
        t = strace.load(tdir)
    except Exception:  # noqa: BLE001
        return ""
    rep = s_analyze(t, with_sp=True, sp_max_events=600, max_states=30000)
    res = set()
    for f in rep.findings:
        if f.cls not in ERROR_CLASSES or not (f.B == "B=0" or f.B.startswith("B <")):
            continue
        if f.cls == "2-buffering" and f.recv is None:
            r = run_pinned(np_, exe, args.split(), observed_pins(t), timeout, mpirun_args=RENDEZVOUS, cwd=d)
            res.add(f"observed run ({f.B}): {classify_rc(r.returncode, r.stderr, r.elapsed, timeout)}")
            continue
        if f.recv is None or f.send is None:
            continue
        hp = horizon_pins(t, f, 0)
        if hp is None:
            continue
        alt, ctl = hp
        ra = run_pinned(np_, exe, args.split(), alt, timeout, mpirun_args=RENDEZVOUS, cwd=d)
        rc = run_pinned(np_, exe, args.split(), ctl, timeout, mpirun_args=RENDEZVOUS, cwd=d)
        res.add(f"{f.cls} at {f.B}: {classify_rc(ra.returncode, ra.stderr, ra.elapsed, timeout)}/"
                f"{'matched' if all_matched(alt, ra.stderr, required_matches(t, f, 0)) else 'unmatched'}/"
                f"control-{classify_rc(rc.returncode, rc.stderr, rc.elapsed, timeout)}")
    return "; ".join(sorted(res))


def records(tdir):
    n = 0
    for f in os.listdir(tdir) if os.path.isdir(tdir) else []:
        if f.endswith(".jsonl"):
            n += sum(1 for _ in open(os.path.join(tdir, f)))
    return n


def classes(findings):
    c = collections.Counter(f["cls"] for f in findings)
    return " ".join(f"{k}*{v}" for k, v in sorted(c.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", default="")
    ap.add_argument("--maxnp", type=int, default=int(os.environ.get("MAXNP") or 24))
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("TIMEOUT") or 1800))
    ap.add_argument("--replay-timeout", type=int, default=120)
    args = ap.parse_args()
    os.makedirs(WORK, exist_ok=True)
    rows, reps = [], []
    for app, sub, exe, cfgs in APPS:
        for np_, a in sorted(cfgs.items()):
            name = f"{app}-{np_}"
            if not re.search(args.select, name):
                continue
            row = {"app": app, "np": np_, "command": f"{exe} {a}".strip(), "commit": code_commit()}
            if np_ > args.maxnp:
                rows.append({**row, "run": f"skipped (more than {args.maxnp} ranks)"})
                continue
            # the whole application directory, run from its subdirectory (kf-ray reads ../scenes)
            top, _, rest = sub.partition("/")
            shutil.rmtree(os.path.join(WORK, name), ignore_errors=True)
            shutil.copytree(os.path.join(BENCH, top), os.path.join(WORK, name), symlinks=True)
            d = os.path.join(WORK, name, rest) if rest else os.path.join(WORK, name)
            for f in os.listdir(d):          # MPI-PRV's own traces and logs are not needed
                if re.match(r"output.*\.txt$", f):
                    os.remove(os.path.join(d, f))
            saved = dict(os.environ)          # every run, replay and control of this configuration
            for k, v in APP_ENV.get(app, {}).items():
                os.environ[k] = v.format(top=os.path.join(WORK, name)) + ":" + os.environ.get(k, "")
            row["run"] = traced(d, exe, a, np_, os.path.join(d, "trace"), False, args.timeout)
            row["records"] = records(os.path.join(d, "trace"))
            res, fs = analyse(os.path.join(d, "trace"), args.timeout)
            row.update(res)
            row["classes"] = classes(fs)
            row["errors"] = sum(1 for f in fs if f["cls"] in ERROR_CLASSES)
            alts = [f for f in fs if f["level"] == "L1" and f.get("send") is not None
                    and (f.get("probe") is not None or f.get("recv") is not None)]
            row["alternatives"] = len(alts)
            # a replay is judged by its control, which cannot finish when the traced run itself did not
            replayable = row["run"] == "rc=0"
            if not replayable:
                row["replay_results"] = f"not replayed: the traced run ended with {row['run']}"
            if alts and replayable:
                rcsv = os.path.join(d, "replays.csv")
                rc, out, err = run([sys.executable, os.path.join(ROOT, "experiments", "replay_sample.py"), "--out", rcsv,
                                    "--sample", "30", "--timeout", str(args.replay_timeout),
                                    os.path.join(d, "trace"), str(np_), exe, *a.split()], d,
                                   36 * (2 * args.replay_timeout + 30), env={"PYTHONPATH": os.path.join(ROOT, "analysis")})
                open(os.path.join(d, "replays.log"), "w").write(out + err)
                rr = list(csv.DictReader(open(rcsv))) if os.path.exists(rcsv) else []
                for r in rr:
                    reps.append({"app": app, "np": np_, **r})
                row["replays"] = len(rr)
                row["replay_results"] = " ".join(f"{k}*{v}" for k, v in sorted(collections.Counter(
                    f"{r.get('replay')}/{'matched' if r.get('matched') == 'True' else 'unmatched'}/control-{r.get('control')}"
                    for r in rr).items()))
            row["guard_run"] = traced(d, exe, a, np_, os.path.join(d, "trace-guard"), True, args.timeout)
            gres, gfs = analyse(os.path.join(d, "trace-guard"), args.timeout)
            row["guard_classes"] = classes(gfs)
            row["guard_local"] = sum(1 for f in gfs if f["cls"] == "3-local")
            # the error findings of either trace that carry a pin, replayed by `stretto confirm`
            for tag, t, found in (("", "trace", fs), ("guard_", "trace-guard", gfs)):
                if replayable and any(f["cls"] in ERROR_CLASSES and f.get("pin") for f in found):
                    rc, out, err = run([sys.executable, "-m", "stretto", "confirm", "--json", *ANALYSE, "--np", str(np_),
                                        "--timeout", str(args.replay_timeout), os.path.join(d, t), exe, "--", *a.split()], d,
                                       40 * (2 * args.replay_timeout + 30), env={"PYTHONPATH": os.path.join(ROOT, "analysis")})
                    open(os.path.join(d, t + ".confirm.json"), "w").write(out + err)
                    try:
                        cs = json.loads(out)
                    except ValueError:
                        cs = []
                    row[tag + "confirmed"] = " ".join(f"{k}*{v}" for k, v in sorted(collections.Counter(
                        f"{c['cls']}:{c['verdict']}" for c in cs).items())) or f"error rc={rc}"
            if replayable:
                row["rendezvous"] = rendezvous_replays(d, exe, a, np_, os.path.join(d, "trace"), args.replay_timeout)
                row["guard_rendezvous"] = rendezvous_replays(d, exe, a, np_, os.path.join(d, "trace-guard"), args.replay_timeout)
            os.environ.clear()
            os.environ.update(saved)
            rows.append(row)
            print(name, {k: row.get(k) for k in ("run", "records", "wildcards", "classes", "alternatives",
                                                  "replay_results", "analysis_s", "guard_classes")}, flush=True)
    if args.select:      # a partial rerun replaces only its own configurations' rows
        done = {(r["app"], str(r["np"])) for r in rows}
        for f, acc in (("apps.csv", rows), ("apps-replays.csv", reps)):
            p = os.path.join(OUT, f)
            if os.path.exists(p):
                acc[:0] = [r for r in csv.DictReader(open(p)) if (r["app"], r["np"]) not in done]
        order = {f"{app}-{n}": i for i, (app, _, _, cfgs) in enumerate(APPS) for n in cfgs}
        rows.sort(key=lambda r: (order.get(f"{r['app']}-{r['np']}", 0), int(r["np"])))
    keys = ["app", "np", "command", "run", "records", "sends", "recvs", "wildcards", "probes", "colls", "classes", "confirmed",
            "errors", "alternatives", "replays", "replay_results", "rendezvous", "analysis_s", "analysis_kb", "analysis_rc",
            "guard_run", "guard_classes", "guard_local", "guard_confirmed", "guard_rendezvous", "summary", "commit"]
    with open(os.path.join(OUT, "apps.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    rkeys = ["app", "np", "finding", "cls", "pin", "site", "pins", "replay", "matched", "control", "control_matched", "commit"]
    with open(os.path.join(OUT, "apps-replays.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rkeys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(reps)
    print("wrote", os.path.join(OUT, "apps.csv"))


if __name__ == "__main__":
    main()
