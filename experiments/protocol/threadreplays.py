#!/usr/bin/env python3
"""The replays of the thread campaigns as Section 6 asks them (RULE.md, amendment 17): traced runs
of a subject's replay cell with the lock order recorded, the distinct alternatives over their
traces (by the pins of each alternative's horizon), a seeded sample of 40 if there are more, and
each sampled alternative replayed with its control in each gate mode. The runs and replays are
the committed replay.sh's (experiments/{slate,parsec,dlaf}/replay.sh: the same cell, program,
settings, oracle and experiments/replay_sample.py), split into three steps, each a job of
experiments/protocol/schedule.py, inside the campaign's image:

  trace K       traced run K of the cell (STRETTO_SYNC_ORDER=1), its trace kept under
                STATE/threadreplays/<subject>/run<K> when it counts (SLATE: a right factor, as the
                committed campaign's RIGHT_ONLY=1; the others: any run that did not crash)
  enumerate     every alternative of every kept trace, as replay_sample.py pools them (L1, the
                subject's class and title filters), distinct by their horizon pins; a sample of
                40 drawn by random.Random("<seed>:<subject>") if there are more; written to
                STATE/threadreplays/<subject>/alternatives.csv
  replay K MODE alternative K replayed REPEAT times with its control in gate mode MODE
                (experiments/replay_modes.sh), by replay_sample.py on its trace, the alternative
                chosen by its title (--only, a pool of one)

The settings are those under which the committed campaigns confirmed the race (READMEs): SLATE's
4x1 cell of 2 Nov, 4 threads in the trace and the replays, the alternatives of classes 1a and 1f
in the collision range, REPEAT 2; PaRSEC's 1-rank cell, REPEAT 2; DLA-Future's 2x1 cell, 1f,
REPEAT 2, in its one mode. The builds of PaRSEC and DLA-Future that survive are experiments/
atomics' (amendment 18): their atomics are recorded unless STRETTO_ATOMICS=0, and with them
recorded every replay and control synchronises otherwise than the trace (sync_divergence,
experiments/parsec/README.md, 2 Nov: 30 of 30 inconclusive; 30 of 30 confirmed on the same binaries
with the recording off), so these replays trace and replay with STRETTO_ATOMICS=0, as the
committed replays of the uninstrumented builds recorded no atomics.

usage: threadreplays.py trace --subject S --run K --out CSV
       threadreplays.py enumerate --subject S --out CSV [--seed K]
       threadreplays.py replay --subject S --index K --mode MODE --out CSV
"""
import argparse
import csv
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
STATE = os.environ.get("PROTOCOL_STATE", "/state")
SAMPLE = 40
SEED = "20261124"
TRACER = os.path.join(ROOT, "tracer", "bin", "openmpi", "libstretto-trace.so")
MODES = {"before": {"STRETTO_GATE_YIELD_MS": "0", "STRETTO_REPLAY_TASKS": "0", "STRETTO_REPLAY_TAILS": "none"},
         "yield": {"STRETTO_REPLAY_TAILS": "none"}, "after": {"STRETTO_REPLAY_TAILS": "control"},
         "both": {"STRETTO_REPLAY_TAILS": "both"}}      # experiments/replay_modes.sh
WRONG = re.compile(r"^summary reps [0-9]* wrong ([0-9]*)$", re.M)

SPECS = {
    "slate": {"np": 4, "exe": "/h/wide_lu", "args": ["1000", "2000", "100", "4", "1", "C", "1", "1"],
              "env": {"LD_LIBRARY_PATH": "/h/lib/plain", "OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "1"},
              "right_only": True, "only": r"tag=2[0-9] ", "cls": ("1a", "1f"), "grep": "WRONG", "timeout": 120},
    "parsec": {"np": 1, "exe": "/h/build-tsan/parsec-plain/tests/collections/reshape/reshape_check",
               "args": ["-N", "120", "-t", "9", "-c", "6", "-m", "1", "--", "--mca", "runtime_vpmap", "hwloc"],
               "env": {"REPS": "1", "HWLOC_SYNTHETIC": "package:2 core:3 pu:1", "STRETTO_ATOMICS": "0"},
               "right_only": False, "only": None, "cls": None, "grep": r"summary reps [0-9]+ wrong [1-9]", "timeout": 60},
    "dlaf": {"np": 2, "exe": "/h/bin/copy_check",
             "args": ["512", "512", "64", "64", "64", "64", "2", "1", "0", "0", "1", "0", "1", "200", "--pika:threads=4",
                      "--pika:bind=none"],
             "env": {"COPY_CHECK_TILES": "1", "OPENBLAS_NUM_THREADS": "1", "STRETTO_ATOMICS": "0"},
             "right_only": False, "only": None, "cls": ("1f",), "grep": "WRONG", "timeout": 60},
}
REPEAT = 2
BASE_ENV = {"OMPI_MCA_rmaps_base_oversubscribe": "1", "OMPI_MCA_btl_vader_single_copy_mechanism": "none",
            "OMPI_MCA_hwloc_base_binding_policy": "none"}


def home(subject):
    return os.path.join(STATE, "threadreplays", subject)


def result_of(subject, text):
    if subject == "slate":
        m = re.search(r"^rep 0 .* (ok|WRONG)$", text, re.M)
        return {"ok": "ok", "WRONG": "wrong"}[m.group(1)] if m else "crash"
    m = WRONG.search(text)
    return "crash" if not m else ("wrong" if int(m.group(1)) > 0 else "ok")


def write(path, rows, fields):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def trace(a):
    sp = SPECS[a.subject]
    d = os.path.join(home(a.subject), "run%02d" % a.run)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, "trace"))
    env = dict(os.environ, **BASE_ENV)
    env.update(sp["env"])
    xs = []
    for k, v in sorted(dict(sp["env"], STRETTO_TRACE_DIR=os.path.join(d, "trace"), STRETTO_SYNC_ORDER="1",
                            LD_PRELOAD=TRACER).items()):
        xs += ["-x", "%s=%s" % (k, v)]
    p = subprocess.run(["/usr/bin/timeout", "300", "/usr/bin/mpirun.openmpi", "--bind-to", "none", "-np", str(sp["np"])] + xs
                       + [sp["exe"]] + sp["args"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, errors="replace", cwd=d)
    with open(os.path.join(d, "log"), "w") as fh:
        fh.write(p.stdout[-65536:])
    res = result_of(a.subject, p.stdout)
    kept = res == "ok" if sp["right_only"] else res != "crash"
    if not kept:
        shutil.rmtree(os.path.join(d, "trace"), ignore_errors=True)
    write(a.out, [{"subject": a.subject, "run": a.run, "result": res, "rc": p.returncode, "kept": kept}],
          ["subject", "run", "result", "rc", "kept"])
    print("%s traced run %d: %s, trace %s" % (a.subject, a.run, res, "kept" if kept else "not kept"))
    return 0


def enumerate_(a):
    sys.path.insert(0, os.path.join(ROOT, "analysis"))
    from stretto import trace as trace_mod
    from stretto.confirm import horizon_pins, with_sync
    from stretto.report import analyze
    sp = SPECS[a.subject]
    h = home(a.subject)
    runs = sorted(x for x in os.listdir(h) if x.startswith("run") and os.path.isdir(os.path.join(h, x, "trace")))
    distinct, order, pooled = {}, [], 0
    for run in runs:
        tdir = os.path.join(h, run, "trace")
        t = with_sync(trace_mod.load(tdir), tdir)
        rep = analyze(t, with_sp=False)
        pool = [f for f in rep.findings if f.level == "L1" and f.send is not None and (f.probe is not None or f.recv is not None)]
        if sp["only"]:
            pool = [f for f in pool if re.search(sp["only"], f.title)]
        if sp["cls"]:
            pool = [f for f in pool if f.cls in sp["cls"]]
        for f in pool:
            pooled += 1
            hp = horizon_pins(t, f)
            # distinct by the pins, over the traces, as replays.py's enumerate (an alternative without a
            # horizon by its trace and title)
            k = tuple(hp[0]) if hp else (run, f.title)
            if k not in distinct:
                distinct[k] = {"run": run, "finding": f.title, "cls": f.cls, "pin": f.pin or "", "site": f.site or ""}
                order.append(k)
    chosen = list(range(len(order)))
    sampled = len(order) > SAMPLE
    if sampled:
        chosen = sorted(random.Random("%s:%s" % (a.seed, a.subject)).sample(chosen, SAMPLE))
    rows = [dict(distinct[order[c]], index=i, distinct=len(order), pooled=pooled, sampled=sampled, seed=a.seed if sampled else "")
            for i, c in enumerate(chosen, 1)]
    fields = ["index", "run", "finding", "cls", "pin", "site", "distinct", "pooled", "sampled", "seed"]
    write(os.path.join(h, "alternatives.csv"), rows, fields)
    write(a.out, [{"subject": a.subject, "traces": len(runs), "pooled": pooled, "distinct": len(order), "replayed": len(rows),
                   "sampled": sampled, "seed": a.seed if sampled else ""}],
          ["subject", "traces", "pooled", "distinct", "replayed", "sampled", "seed"])
    print("%s: %d traces, %d alternatives, %d distinct, %d to replay" % (a.subject, len(runs), pooled, len(order), len(rows)))
    return 0


def replay(a):
    sp = SPECS[a.subject]
    h = home(a.subject)
    with open(os.path.join(h, "alternatives.csv"), newline="") as fh:
        alt = next(r for r in csv.DictReader(fh) if int(r["index"]) == a.index)
    with open(os.path.join(h, alt["run"], "log")) as fh:
        recorded = result_of(a.subject, fh.read())
    w = tempfile.mkdtemp(prefix="threadreplay-")
    env = dict(os.environ, **BASE_ENV)
    env.update(sp["env"])
    env.update(MODES[a.mode])
    out = os.path.join(w, "replay.csv")
    # the alternative by its whole title, a pool of one (replay_sample.py's --from-csv reads titles of
    # one thread per rank only, "recv(1, 5)", not a chain's "recv(0.5, 0)")
    cmd = [sys.executable, os.path.join(ROOT, "experiments", "replay_sample.py"), "--threads", "--repeat", str(REPEAT),
           "--sample", "1", "--only", "^" + re.escape(alt["finding"]) + "$", "--cls", alt["cls"], "--grep", sp["grep"],
           "--timeout", str(sp["timeout"]), "--keep-output", os.path.join(h, "out", "%02d-%s" % (a.index, a.mode)),
           "--out", out]
    for k, v in sorted(sp["env"].items()):
        cmd += ["--env", "%s=%s" % (k, v)]
    cmd += [os.path.join(h, alt["run"], "trace"), str(sp["np"]), sp["exe"]] + sp["args"]
    p = subprocess.run(cmd, env=env, cwd=os.path.join(h, alt["run"]), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, errors="replace")
    if not os.path.exists(out):
        sys.exit("replay_sample.py wrote no table: " + p.stdout[-500:])
    with open(out, newline="") as fh:
        got = list(csv.DictReader(fh))
    for r in got:
        r.update({"index": a.index, "mode": a.mode, "trace_run": alt["run"], "recorded": recorded})
    fields = ["index", "mode", "trace_run", "recorded"] + [c for c in (got[0].keys() if got else []) if c not in
                                                           ("index", "mode", "trace_run", "recorded")]
    write(a.out, got, fields)
    shutil.rmtree(w, ignore_errors=True)
    print("%s alternative %d, mode %s: %d rows" % (a.subject, a.index, a.mode, len(got)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("trace")
    e = sub.add_parser("enumerate")
    r = sub.add_parser("replay")
    for p in (t, e, r):
        p.add_argument("--subject", required=True, choices=sorted(SPECS))
        p.add_argument("--out", required=True)
    t.add_argument("--run", type=int, required=True)
    e.add_argument("--seed", default=SEED)
    r.add_argument("--index", type=int, required=True)
    r.add_argument("--mode", required=True, choices=sorted(MODES))
    a = ap.parse_args()
    return {"trace": trace, "enumerate": enumerate_, "replay": replay}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
