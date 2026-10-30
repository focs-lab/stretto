#!/usr/bin/env python3
"""Buffer-guard findings at forced eager limits (docs/model.md, section 7).

For each case, a program run once under the tracer with the buffer guard and message sums
(STRETTO_GUARD=1 STRETTO_SUMS=1) at the library's defaults, the report states, per guard
finding, at which thresholds B the misused operation's data can change (stretto.buffers). The
case is then run again, traced the same way, with the eager limit set so that the operation's
message of n bytes is just above it (B = n - 1, rendezvous) and just at it (B = n, copied), and
the replay's own sums say what happened: a send's message changed when the receive that took it
got bytes whose checksum differs from the one its send buffer had when the send was called.

  Open MPI   shared memory with btl_vader_eager_limit B + 56 and no fast boxes: the transport
             buffers L - 56 bytes for limit L (experiments/results/b_impl.csv: 4 040 bytes at the
             default 4 096). Its send paths send up to 256 bytes inline at any limit, so B < 256
             is not reachable on them (b_impl.py): such a case is "untestable" there.
  MPICH      MPIR_CVAR_NOLOCAL=1 (every message through the UCX netmod) and UCX_RNDV_THRESH B + 1:
             UCX sends by rendezvous from that many bytes on.

A prediction that the data changes in every execution with the observed matching is borne out
when the replay's message changed; one that it does not change (copied in the call, or read
before the access) when it did not; one that it changes in some executions is consistent with
either. The replay of a program that departs from the traced run (another matching, fewer
records) is not judged.

usage: thresholds.py [--mpi openmpi,mpich] [--out CSV] [--timeout S] NAME=NP:EXE[:ARG...] ...
       ({mpi} in EXE stands for the implementation)
"""
from __future__ import annotations
import argparse
import csv
import os
import shutil
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from stretto import trace  # noqa: E402
from stretto.buffers import COPIED, EVERY, HARMLESS, SOME  # noqa: E402
from stretto.confirm import classify_rc, run_pinned  # noqa: E402
from stretto.report import local_consequence  # noqa: E402
from stretto.trace import Recv, Send  # noqa: E402
from provenance import code_commit  # noqa: E402

VADER_HEADER = 56          # b_impl.csv: the default limit of 4 096 bytes buffers 4 040
INLINE = 256               # Open MPI's ob1 sends this many bytes inline on the send and isend paths


def settings(mpi: str, B: int):
    """(mpirun arguments, environment) that put the eager threshold at B bytes, or None."""
    if mpi == "openmpi":
        return (["--mca", "btl", "self,vader", "--mca", "btl_vader_eager_limit", str(B + VADER_HEADER),
                 "--mca", "btl_vader_fbox_max", "0", "--mca", "btl_self_eager_limit", str(B + VADER_HEADER)], {})
    return ([], {"MPIR_CVAR_NOLOCAL": "1", "UCX_RNDV_THRESH": str(B + 1)})


def traced(mpi: str, np: int, exe: str, args, timeout: int, mpirun_args=None, env=None):
    d = tempfile.mkdtemp(prefix="stretto-thr-")
    lib = os.path.join(ROOT, "tracer", "bin", mpi, "libstretto-trace.so")
    r = run_pinned(np, exe, list(args), None, timeout=timeout, mpi=mpi, mpirun_args=mpirun_args, preload=lib, cwd=d,
                   extra_env={"STRETTO_TRACE_DIR": d, "STRETTO_GUARD": "1", "STRETTO_SUMS": "1", **(env or {})})
    res = classify_rc(r.returncode, r.stderr, r.elapsed, timeout)
    try:
        t = trace.load(d)
    except (OSError, ValueError, KeyError):
        t = None
    shutil.rmtree(d, ignore_errors=True)
    return res, t


def ops_of(t):
    ops = {}
    for x in list(t.sends.values()) + list(t.recvs.values()):
        if x.req is not None:
            ops[(x.proc, x.req)] = x
    return ops


def findings(t):
    """(guard record, operation, consequence) for every guard record about a send or receive."""
    from stretto.buffers import Orders
    ops, o, out, seen = ops_of(t), Orders(t), [], set()
    for rec in t.guards:
        g = rec.raw
        x = ops.get((t.proc.get(rec.rank, rec.rank), g.get("req")))
        key = (rec.rank, g.get("req"), g.get("what"), g.get("kind"))
        if x is None or key in seen:
            continue
        seen.add(key)
        cons = local_consequence(t, rec, x, ops, lambda: o)
        if cons is not None:
            out.append((rec, x, cons))
    return out


def verdict_at(cons, B: int) -> str:
    v = None
    for b, w in cons.profile:
        if b is not None and b <= B:
            v = w
    return v


def outcome(t, x):
    """changed, intact, unchanged buffer (the program's write left the send buffer as it was at the
    call, so the replay cannot tell) or unknown: did the message of the operation with x's identity carry
    what its send buffer held at the call?"""
    if t is None:
        return "no trace"
    y = (t.sends if isinstance(x, Send) else t.recvs).get(x.key)
    if y is None:
        return "departed"
    if isinstance(y, Send):
        s = y
        r = next((t.recvs[rk] for rk, sk in t.observed.items() if sk == s.key), None)
    else:
        r = y
        s = t.sends.get(t.observed.get(r.key)) if t.observed.get(r.key) is not None else None
    if s is None or r is None:
        return "departed"
    if s.sum is None or r.sum is None:
        return "no sum"
    if s.sum == r.sum:
        # a write of the bytes already there cannot show in the message
        return "unchanged buffer" if isinstance(x, Send) and s.sumc is not None and s.sumc == s.sum else "intact"
    return "changed"


def judged(pred: str, out: str) -> str:
    if out not in ("changed", "intact"):
        return "not judged"
    if pred == EVERY:
        return "as predicted" if out == "changed" else "not as predicted"
    if pred in (COPIED, HARMLESS):
        return "as predicted" if out == "intact" else "not as predicted"
    if pred == SOME:
        return "consistent"
    return "not judged"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+", help="NAME=NP:EXE[:ARG...]")
    ap.add_argument("--mpi", default="openmpi,mpich")
    ap.add_argument("--out", default=os.path.join(ROOT, "experiments", "results", "guard", "thresholds.csv"))
    ap.add_argument("--timeout", type=int, default=30)
    a = ap.parse_args()
    rows, commit = [], code_commit()
    for spec in a.cases:
        name, rest = spec.split("=", 1)
        parts = rest.split(":")
        np, exe_t, args = int(parts[0]), parts[1], parts[2:]
        for mpi in a.mpi.split(","):
            exe = os.path.abspath(exe_t.replace("{mpi}", mpi))     # the runs start in their trace directory
            res0, t0 = traced(mpi, np, exe, args, a.timeout)
            if t0 is None:
                rows.append({"case": name, "mpi": mpi, "setting": "default", "result": res0, "verdict": "no trace"})
                continue
            for rec, x, cons in findings(t0):
                what = rec.raw.get("what") + (":" + rec.raw["kind"] if rec.raw.get("kind") else "")
                n = x.bytes if isinstance(x, Send) else (t0.sends[t0.observed[x.key]].bytes if x.key in t0.observed else 0)
                base = {"case": name, "mpi": mpi, "op": "send" if isinstance(x, Send) else "recv", "key": f"{x.key[0]}.{x.key[1]}",
                        "what": what, "bytes": n, "B": cons.B or "none"}
                rows.append({**base, "setting": "default", "result": res0, "outcome": outcome(t0, x),
                             "predicted": "", "verdict": ""})
                for setting, B in (("below", n - 1), ("at", n)):
                    if B < 0:
                        continue
                    pred = verdict_at(cons, B)
                    if mpi == "openmpi" and B < INLINE:
                        rows.append({**base, "setting": setting, "threshold": B, "result": "-", "outcome": "-",
                                     "predicted": pred, "verdict": "untestable"})
                        continue
                    margs, env = settings(mpi, B)
                    res, t = traced(mpi, np, exe, args, a.timeout, margs, env)
                    out = outcome(t, x) if t is not None else res     # a run may fail after the message
                    rows.append({**base, "setting": setting, "threshold": B, "result": res, "outcome": out,
                                 "predicted": pred, "verdict": judged(pred, out)})
                    print(f"{name} {mpi} {base['op']} {base['key']} {what} {n}B {setting} B={B}: {res} {out} "
                          f"(predicted {pred}: {rows[-1]['verdict']})", flush=True)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    cols = ["case", "mpi", "op", "key", "what", "bytes", "B", "setting", "threshold", "result", "outcome", "predicted",
            "verdict", "commit"]
    with open(a.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**{c: "" for c in cols}, **r, "commit": commit})
    judged_rows = [r for r in rows if r.get("verdict") in ("as predicted", "not as predicted", "consistent")]
    print(f"{len(judged_rows)} replays judged: " + ", ".join(
        f"{v} {sum(1 for r in judged_rows if r['verdict'] == v)}" for v in ("as predicted", "not as predicted", "consistent"))
        + f"; wrote {a.out}")
    return 0 if not any(r.get("verdict") == "not as predicted" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
