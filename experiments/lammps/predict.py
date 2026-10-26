#!/usr/bin/env python3
"""Does one traced run of LAMMPS predict the deadlock of Irregular::create_atom on an MPI that
does not buffer its 4-byte counts, and does the program hang there?

Before the fix (411c069b), Irregular::create_atom and create_data (src/irregular.cpp) send every
destination its count with a blocking MPI_Send and only then receive the counts with
MPI_Recv(MPI_ANY_SOURCE). Two ranks that send to each other wait in their sends unless the MPI
buffers the 4-byte messages: an unsafe program, whose recorded run is realisable only above an
eager threshold (the analysis' 2-buffering finding, "observed run deadlocks on any
implementation with eager threshold < B bytes", with the cycle of records it would wait in).

For each process count, RUNS traced runs are analysed (L1); a row says whether the report has
the 2-buffering finding, its bound, and the call sites of the records on its cycle. Then the
untraced program runs REPLAYS times with Open MPI's default settings and on the TCP transport
over the loopback interface with its eager threshold just below the bound and at it, as E2
replays a buffering finding (experiments/e2_buffering.py; experiments/b_impl.py: with eager
limit L the TCP transport buffers L - h payload bytes on every send path, h its header, from
experiments/results/b_impl.csv): below the bound it should hang, at the bound and by default
finish. A version whose traces have no such finding runs at the bound of the other version
(--bound, default 4), to show it finishes there too. With KEEP_TRACES set, every trace is kept
(stretto.keep) under the table's campaign (lammps/predict-<version>): np<N>-run<K>.

usage: predict.py --version V [--np 4 8] [--runs N] [--replays N] [--timeout S] [--bound B]
                  --out CSV -- <lmp> [args ...]
Writes one row per traced run (kind traced: result, records, status, analysis seconds, the
number of 2-buffering findings, their bound, the cycle's call sites, the other findings by class)
and one per rerun (kind default, below or at: eager bytes, result, seconds), with the commit.
"""
from __future__ import annotations
import argparse
import collections
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from stretto import keep, sites, trace  # noqa: E402
from stretto.confirm import classify_rc, need_preload, tracer_lib  # noqa: E402
from stretto.report import analyze  # noqa: E402
from provenance import code_commit  # noqa: E402
from b_impl import tcp, tcp_header  # noqa: E402

KEYS = ["version", "np", "run", "kind", "eager_bytes", "result", "seconds", "records", "status", "analyse_s", "buffering",
        "bound", "cycle_sites", "other", "commit"]


def run(np: int, cmd: list, mpirun_args: list, timeout: int, env: list = ()):
    """One run in a fresh directory: (result, seconds, process)."""
    w = tempfile.mkdtemp(prefix="lammps-run-")
    try:
        c = ["/usr/bin/timeout", "-k", "5", str(timeout), "/usr/bin/mpirun.openmpi", "--oversubscribe", "-np", str(np),
             *mpirun_args, *env, *cmd]
        t0 = time.monotonic()
        p = subprocess.run(c, cwd=w, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        el = time.monotonic() - t0
    finally:
        shutil.rmtree(w, ignore_errors=True)
    return classify_rc(p.returncode, p.stderr, el, timeout), round(el, 1), p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--np", type=int, nargs="+", default=[4, 8])
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--replays", type=int, default=10, help="untraced runs per setting and process count")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--bound", type=int, default=4, help="the bound to test at when the traces report none")
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-campaign", default=None,
                    help="with KEEP_TRACES set, keep the traces under this campaign (stretto.keep; default: the table's)")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        ap.error("no program given")
    cmd[0] = os.path.abspath(cmd[0])
    tracer = tracer_lib("openmpi")
    need_preload(tracer)                     # ld.so would ignore a missing tracer, and the runs leave no trace
    header = tcp_header()
    if header is None:
        sys.exit("predict.py: no TCP header in experiments/results/b_impl.csv (experiments/b_impl.py measures it)")
    campaign = a.keep_campaign or keep.campaign_of(a.out)
    commit = code_commit()
    rows = []
    for np in a.np:
        bounds = set()
        for i in range(1, a.runs + 1):
            d = tempfile.mkdtemp(prefix=f"lammps-trace-{np}-{i}-")
            res, el, _ = run(np, cmd, [], 600, ["-x", f"LD_PRELOAD={tracer}", "-x", f"STRETTO_TRACE_DIR={d}"])
            row = {"version": a.version, "np": np, "run": i, "kind": "traced", "result": res, "seconds": el, "commit": commit}
            keep.keep(d, campaign, f"np{np}-run{i:02d}", commit=commit)
            if res == "FINISHED":
                t = trace.load(d)
                t0 = time.monotonic()
                rep = analyze(t, with_sp=False)
                row["analyse_s"] = round(time.monotonic() - t0, 1)
                row["records"] = sum(len(v) for v in t.ranks.values())
                row["status"] = "complete" if not rep.degraded else "degraded: " + ",".join(x for x, _ in rep.degraded)
                buf = [f for f in rep.findings if f.cls == "2-buffering"]
                row["buffering"] = len(buf)
                if buf:
                    f = buf[0]
                    row["bound"] = f.B
                    m = re.search(r"B < (\d+)", f.B or "")
                    if m:
                        bounds.add(int(m.group(1)))
                    cycle = next((x for x in f.detail if x.startswith("cycle")), "")
                    names = {label: where for label, where, _ in sites.sites(t)}
                    at = {re.sub(r".*/", "", names.get(t.site_label(int(r), int(k)), t.site_label(int(r), int(k)) or "?"))
                          for r, k in re.findall(r"r(\d+)#(\d+)", cycle)}
                    row["cycle_sites"] = "; ".join(sorted(at))
                row["other"] = ";".join(f"{c}={n}" for c, n in sorted(collections.Counter(
                    f.cls for f in rep.findings if f.cls != "2-buffering").items()))
            shutil.rmtree(d, ignore_errors=True)
            rows.append(row)
            print(row, flush=True)
        for b in sorted(bounds) or [a.bound]:
            for kind, eager in (("default", None), ("below", b - 1), ("at", b)):
                for k in range(1, a.replays + 1):
                    res, el, _ = run(np, cmd, [] if eager is None else tcp(eager + header), a.timeout)
                    rows.append({"version": a.version, "np": np, "run": k, "kind": kind, "eager_bytes": "" if eager is None else eager,
                                 "result": res, "seconds": el, "commit": commit})
                    print(rows[-1], flush=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=KEYS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("wrote", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
