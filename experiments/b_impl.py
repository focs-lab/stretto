#!/usr/bin/env python3
"""B_impl: the eager threshold of an MPI implementation, measured. Two ranks exchange one
message of n bytes head to head (both send, then both receive): the exchange finishes when the
message is eager and hangs when it is rendezvous, so a bisection over n finds the largest
payload the implementation still buffers. The send is issued three ways, MPI_Send, MPI_Isend
and a persistent MPI_Send_init + MPI_Start, because Open MPI's ob1 layer sends a message of up to
256 bytes inline whatever the eager limit is on the first two paths and not on the third, so
one setting can give different thresholds per path.

Settings: the defaults; the shared-memory transport with its eager limit forced down (which
moves only the persistent path below 256 bytes); and the TCP transport, which has no inline path,
with its eager limit set to 64 bytes: there every path buffers L - h bytes for limit L, h the
transport's header, which E2 uses to put the threshold just below and at a predicted bound.
Output: experiments/results/b_impl.csv (setting, path, mpirun arguments, largest eager payload,
smallest hanging payload, commit).
usage: b_impl.py [--timeout S] [--max-bytes N]"""
from __future__ import annotations
import argparse
import csv
import os
import subprocess
import sys
import tempfile
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "analysis"))
from provenance import code_commit  # noqa: E402
from stretto.confirm import classify_rc  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MPICC, MPIRUN = "/usr/bin/mpicc.openmpi", "/usr/bin/mpirun.openmpi"
PROGRAM = r'''
#include <mpi.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv) {
    int rank, n = atoi(argv[1]); char *buf = calloc(n > 0 ? n : 1, 1), *in = calloc(n > 0 ? n : 1, 1);
    MPI_Request q;
    MPI_Init(&argc, &argv); MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    if (!strcmp(argv[2], "send")) {
        MPI_Send(buf, n, MPI_BYTE, 1 - rank, 0, MPI_COMM_WORLD);
    } else if (!strcmp(argv[2], "isend")) {
        MPI_Isend(buf, n, MPI_BYTE, 1 - rank, 0, MPI_COMM_WORLD, &q); MPI_Wait(&q, MPI_STATUS_IGNORE);
    } else {
        MPI_Send_init(buf, n, MPI_BYTE, 1 - rank, 0, MPI_COMM_WORLD, &q);
        MPI_Start(&q); MPI_Wait(&q, MPI_STATUS_IGNORE); MPI_Request_free(&q);
    }
    MPI_Recv(in, n, MPI_BYTE, 1 - rank, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    MPI_Finalize(); return 0;
}
'''
PATHS = ["send", "isend", "persistent"]
VADER = ["--mca", "btl", "self,vader", "--mca", "btl_vader_eager_limit", "64", "--mca", "btl_self_eager_limit", "64",
         "--mca", "btl_vader_fbox_max", "0"]


def tcp(limit: int):
    """TCP transport with eager limit `limit` (bytes on the wire, header included)."""
    return ["--mca", "btl", "self,tcp", "--mca", "btl_tcp_if_include", "lo", "--mca", "btl_tcp_eager_limit", str(limit),
            "--mca", "btl_self_eager_limit", str(limit)]


SETTINGS = {"openmpi-default": [], "openmpi-forced-rendezvous": VADER, "openmpi-tcp-64": tcp(64)}
TABLE = os.path.join(ROOT, "experiments", "results", "b_impl.csv")


def tcp_header(path: str = TABLE):
    """h, the TCP transport's header in bytes, from a table this script wrote: with eager limit
    L every send path buffers L - h bytes. None if the table or its TCP row is missing."""
    if not os.path.exists(path):
        return None
    for r in csv.DictReader(open(path)):
        if r["setting"] == "openmpi-tcp-64" and r.get("path") == "persistent" and int(r["eager_max_bytes"]) >= 0:
            return 64 - int(r["eager_max_bytes"])
    return None


def hangs(exe: str, n: int, path: str, mpirun_args, timeout: int) -> bool:
    """Does the head-to-head exchange of n bytes hang? A run that hangs is killed by the timeout
    (124, or 137 once the timeout has passed, when the launcher ignored SIGTERM); any other end
    than finishing is no answer, and the measurement stops instead of reading it as eager (a
    memory cap, a launcher argument the MPI rejects)."""
    cmd = ["/usr/bin/timeout", "-k", "2", str(timeout), MPIRUN, "-np", "2", *mpirun_args, exe, str(n), path]
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={**os.environ, "OMPI_MCA_rmaps_base_oversubscribe": "1"})
    res = classify_rc(r.returncode, r.stderr, time.monotonic() - t0, timeout)
    if res not in ("FINISHED", "TIMEOUT"):
        sys.exit(f"b_impl.py: HARNESS-ERROR: {n} bytes on path {path} with {mpirun_args}: {res}: {r.stderr.strip()[-300:]}")
    return res == "TIMEOUT"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=5)
    ap.add_argument("--max-bytes", type=int, default=1 << 20)
    ap.add_argument("--out", default=TABLE)
    args = ap.parse_args()
    rows = []
    with tempfile.TemporaryDirectory() as d:
        src, exe = os.path.join(d, "h2h.c"), os.path.join(d, "h2h")
        open(src, "w").write(PROGRAM)
        subprocess.run([MPICC, "-O0", "-o", exe, src], check=True)
        for name, margs in SETTINGS.items():
            for path in PATHS:
                row = {"setting": name, "path": path, "mpirun_args": " ".join(margs)}
                lo, hi = 0, args.max_bytes          # lo finishes, hi hangs (or is the cap)
                if hangs(exe, lo, path, margs, args.timeout):
                    rows.append({**row, "eager_max_bytes": -1, "rendezvous_min_bytes": 0})
                    continue
                if not hangs(exe, hi, path, margs, args.timeout):
                    rows.append({**row, "eager_max_bytes": hi, "rendezvous_min_bytes": -1})
                    continue
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    if hangs(exe, mid, path, margs, args.timeout):
                        hi = mid
                    else:
                        lo = mid
                rows.append({**row, "eager_max_bytes": lo, "rendezvous_min_bytes": hi})
                print(f"{name} {path}: eager up to {lo} bytes, rendezvous from {hi}", flush=True)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["setting", "path", "mpirun_args", "eager_max_bytes", "rendezvous_min_bytes", "commit"], lineterminator="\n")
        w.writeheader()
        w.writerows({**r, "commit": code_commit()} for r in rows)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
