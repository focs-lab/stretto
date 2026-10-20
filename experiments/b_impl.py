#!/usr/bin/env python3
"""B_impl: the eager threshold of an MPI implementation, measured. Two ranks exchange one
message of n bytes head to head (both Send, then both Recv): the exchange finishes when the
message is eager and hangs when it is rendezvous, so a bisection over n finds the largest
payload the implementation still buffers. Measured for the default settings and for the
mpirun arguments the E2 replay uses to force rendezvous. Output: experiments/results/b_impl.csv
(setting, mpirun arguments, largest eager payload in bytes, smallest hanging payload).
usage: b_impl.py [--timeout S] [--max-bytes N]"""
from __future__ import annotations
import argparse
import csv
import os
import subprocess
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from provenance import code_commit  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MPICC, MPIRUN = "/usr/bin/mpicc.openmpi", "/usr/bin/mpirun.openmpi"
PROGRAM = r'''
#include <mpi.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    int rank, n = atoi(argv[1]); char *buf = calloc(n > 0 ? n : 1, 1);
    MPI_Init(&argc, &argv); MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Send(buf, n, MPI_BYTE, 1 - rank, 0, MPI_COMM_WORLD);
    MPI_Recv(buf, n, MPI_BYTE, 1 - rank, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    MPI_Finalize(); return 0;
}
'''
# the arguments E2 passes to force rendezvous on an intranode Open MPI run: shared-memory and
# self transports with the smallest eager limit they accept
FORCED = ["--mca", "btl", "self,vader", "--mca", "btl_vader_eager_limit", "64", "--mca", "btl_self_eager_limit", "64",
          "--mca", "btl_vader_fbox_max", "0"]   # no fast boxes either: they carry small messages eagerly
SETTINGS = {"openmpi-default": [], "openmpi-forced-rendezvous": FORCED}
# Measured on Open MPI 4.1.6: default 4040 payload bytes (4096 minus the header); forced, 256
# bytes, the smallest eager size the shared-memory transport accepts, so a buffering bound
# below 257 bytes cannot be tested by replay on this implementation


def hangs(exe: str, n: int, mpirun_args, timeout: int) -> bool:
    cmd = ["/usr/bin/timeout", "-k", "2", str(timeout), MPIRUN, "-np", "2", *mpirun_args, exe, str(n)]
    r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={**os.environ, "OMPI_MCA_rmaps_base_oversubscribe": "1"})
    return r.returncode == 124


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=5)
    ap.add_argument("--max-bytes", type=int, default=1 << 20)
    ap.add_argument("--out", default=os.path.join(ROOT, "experiments", "results", "b_impl.csv"))
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as d:
        src, exe = os.path.join(d, "h2h.c"), os.path.join(d, "h2h")
        open(src, "w").write(PROGRAM)
        subprocess.run([MPICC, "-O0", "-o", exe, src], check=True)
        rows = []
        for name, margs in SETTINGS.items():
            lo, hi = 0, args.max_bytes          # lo finishes, hi hangs (or is the cap)
            if hangs(exe, lo, margs, args.timeout):
                rows.append({"setting": name, "mpirun_args": " ".join(margs), "eager_max_bytes": -1, "rendezvous_min_bytes": 0})
                continue
            if not hangs(exe, hi, margs, args.timeout):
                rows.append({"setting": name, "mpirun_args": " ".join(margs), "eager_max_bytes": hi, "rendezvous_min_bytes": -1})
                continue
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if hangs(exe, mid, margs, args.timeout):
                    hi = mid
                else:
                    lo = mid
            rows.append({"setting": name, "mpirun_args": " ".join(margs), "eager_max_bytes": lo, "rendezvous_min_bytes": hi})
            print(f"{name}: eager up to {lo} bytes, rendezvous from {hi}", flush=True)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["setting", "mpirun_args", "eager_max_bytes", "rendezvous_min_bytes", "commit"], lineterminator="\n")
        w.writeheader()
        w.writerows({**r, "commit": code_commit()} for r in rows)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
