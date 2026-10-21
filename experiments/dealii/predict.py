#!/usr/bin/env python3
"""Does one traced run of a deal.II RemotePointEvaluation test predict that a probe takes a
message of the next call, and does the test then print other values?

In deal.II 9.3.0-rc1, RemotePointEvaluation::evaluate_and_process() Isends one message to
each rank it serves, all with one tag (Tags::remote_point_evaluation, 750), then calls
MPI_Probe(MPI_ANY_SOURCE, 750) once per rank it expects a message from, receives from the
probed source and files the values under it; nothing collective separates two calls on one
cached object. In mapping_01 every rank serves every other one in every call, so the k-th
message on a (sender, receiver, communicator) channel with that tag belongs to the k-th call on
that communicator (both objects of the test use MPI_COMM_WORLD, which the triangulation does
not duplicate). An alternative is "cross" if the probe would see a message whose ordinal on its
channel differs from that of the message it saw (a message of another call, the race), and
"same" if it would see another sender's message of the same call (a reordering inside the
call, which the per-source filing absorbs). For each process count, RUNS traced runs are
analysed and every probe alternative on the tag is replayed with the pins of its whole horizon
(stretto.confirm.horizon_pins) and with its control, each run in its own directory; the test
writes its log to the file "output" there, which is normalised as deal.II's test harness
normalises it (its cmake/scripts/normalize.pl: the JobId line with host and date goes) and
compared with the test's expected output and with the control's.

usage: predict.py <driver dir> --version V --expected PATTERN --normalize SCRIPT
                  [--test mapping_01] [--runs N] [--np 4] [--timeout S] [--jobs J] [--tag 750]
                  [--out CSV]
Writes one row per replayed alternative (one per run without any): version, test, np, run,
records, cross, same and other alternatives of the run (other: on other tags, or at wildcard
receives), whether the traced run's output equals the expected one, pin, kind, call site,
replay result, every required pin matched, whether the output differs from the expected one and
from the control's, how many lines differ from the expected ones, control result, control
matched, whether the control's output equals the expected one, commit. Outputs that differ
are kept under outputs/ next to the table, which is not committed.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import csv
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from stretto import trace  # noqa: E402
from stretto.confirm import all_matched, classify_rc, horizon_pins, required_matches, run_pinned  # noqa: E402
from stretto.report import analyze  # noqa: E402
from provenance import code_commit  # noqa: E402

TRACER = os.path.join(ROOT, "tracer", "bin", "mpich", "libstretto-trace.so")


def output_of(d: str, normalize: str) -> str:
    """The test's log, normalised: rank 0 writes "output" and appends the other ranks' files
    to it. "" if the run left none."""
    path = os.path.join(d, "output")
    if not os.path.exists(path):
        return ""
    return subprocess.run(["perl", "-p", normalize, path], capture_output=True, text=True, check=True).stdout


def lines_differing(a: str, b: str) -> int:
    x, y = a.splitlines(), b.splitlines()
    return sum(1 for i in range(max(len(x), len(y))) if i >= len(x) or i >= len(y) or x[i] != y[i])


def ordinal(t, s) -> int:
    """The position of send s among the sends with its tag on its (sender, receiver,
    communicator) channel: its call, as long as every call sends on every channel."""
    return [x.key for x in t.sends_to(s.rank, s.dst, s.comm) if x.tag == s.tag].index(s.key)


def classify(t, rep, tag):
    """(finding, kind) for every probe alternative on tag; the count of the other findings
    with an alternative message (probes on other tags, wildcard receives)."""
    found, other = [], 0
    for f in rep.findings:
        if f.level != "L1" or f.send is None:
            continue
        if f.probe is None:
            other += f.recv is not None
            continue
        p, s = t.probes[f.probe], t.sends[f.send]
        if s.tag != tag:
            other += 1
            continue
        found.append((f, "same" if ordinal(t, s) == ordinal(t, t.sends[p.seen]) else "cross"))
    return found, other


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bindir")
    ap.add_argument("--version", required=True)
    ap.add_argument("--expected", required=True, help="the test's expected output, {np} for the process count")
    ap.add_argument("--normalize", required=True, help="deal.II's cmake/scripts/normalize.pl")
    ap.add_argument("--test", default="mapping_01")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--np", type=int, nargs="+", default=[4])
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--jobs", type=int, default=1, help="replays run at once")
    ap.add_argument("--tag", type=int, default=750, help="Tags::remote_point_evaluation")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    exe = os.path.join(os.path.abspath(args.bindir), args.test)
    keep = os.path.join(os.path.dirname(os.path.abspath(args.out)), "outputs")
    rows = []
    for np in args.np:
        with open(args.expected.format(np=np)) as fh:
            expected = fh.read()
        for i in range(1, args.runs + 1):
            d = tempfile.mkdtemp(prefix=f"dealii-{np}-{i}-")
            cwd = tempfile.mkdtemp(prefix=f"dealii-run-{np}-{i}-")
            cmd = ["/usr/bin/timeout", "600", "/usr/bin/mpirun.openmpi", "--oversubscribe", "-np", str(np),
                   "-x", f"LD_PRELOAD={TRACER}", "-x", f"STRETTO_TRACE_DIR={d}", exe]
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            traced = output_of(cwd, args.normalize)
            shutil.rmtree(cwd, ignore_errors=True)
            row = {"version": args.version, "test": args.test, "np": np, "run": i, "commit": code_commit()}
            if r.returncode != 0:
                rows.append({**row, "replay": f"traced run rc={r.returncode}"})
                shutil.rmtree(d, ignore_errors=True)
                continue
            t = trace.load(d)
            rep = analyze(t, with_sp=False)
            found, other = classify(t, rep, args.tag)
            row.update(records=sum(len(v) for v in t.ranks.values()),
                       alternatives=sum(1 for _, k in found if k == "cross"),
                       same_alternatives=sum(1 for _, k in found if k == "same"),
                       other_alternatives=other, traced_expected=traced == expected)
            if not found:
                rows.append(row)

            def run(pins):
                """One pinned run in a directory of its own: (process, its output)."""
                w = tempfile.mkdtemp(prefix="dealii-replay-")
                try:
                    p = run_pinned(np, exe, [], pins, args.timeout, mpi="mpich", cwd=w)
                    return p, output_of(w, args.normalize)
                finally:
                    shutil.rmtree(w, ignore_errors=True)

            def replay(f, kind):
                res = {**row, "pin": f.pin, "kind": kind, "site": f.site}
                hp = horizon_pins(t, f)
                if hp is None:
                    return {**res, "replay": "no horizon"}
                alt, ctl = hp
                (ra, oa), (rc, oc) = run(alt), run(ctl)
                res.update(replay=classify_rc(ra.returncode, ra.stderr, ra.elapsed, args.timeout),
                           matched=all_matched(alt, ra.stderr, required_matches(t, f)),
                           differs=oa != expected, differs_control=oa != oc, lines=lines_differing(oa, expected),
                           control=classify_rc(rc.returncode, rc.stderr, rc.elapsed, args.timeout),
                           control_matched=all_matched(ctl, rc.stderr), control_expected=oc == expected)
                if oa != expected or oc != expected:
                    os.makedirs(keep, exist_ok=True)
                    name = f"{args.version}-{np}-{i}-{f.pin.split('=', 1)[-1].replace(':', '-')}"
                    for role, text in (("replay", oa), ("control", oc)):
                        with open(os.path.join(keep, f"{name}-{role}.txt"), "w") as fh:
                            fh.write(text)
                return res

            # replays of one trace are independent runs
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
                for res in pool.map(lambda fk: replay(*fk), found):
                    rows.append(res)
                    print({k: res.get(k) for k in ("np", "run", "pin", "kind", "replay", "matched", "differs", "differs_control",
                                                   "lines", "control", "control_expected")}, flush=True)
            print(f"np={np} run={i}: {row['records']} records, {row['alternatives']} cross, {row['same_alternatives']} same, "
                  f"{other} other alternatives; traced output as expected: {row['traced_expected']}", flush=True)
            shutil.rmtree(d, ignore_errors=True)
            write(args.out, rows)   # after every trace: a campaign takes hours
    write(args.out, rows)
    print("wrote", args.out)
    return 0


def write(path, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    keys = ["version", "test", "np", "run", "records", "alternatives", "same_alternatives", "other_alternatives",
            "traced_expected", "pin", "kind", "site", "replay", "matched", "differs", "differs_control", "lines",
            "control", "control_matched", "control_expected", "commit"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
