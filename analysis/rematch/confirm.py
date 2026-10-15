"""Confirming replay: rerun the program with each finding's REMATCH_PIN and classify the
outcome (hang / MPI error / finished). Prediction stays single-run; this only separates
true predictions from model or tool divergence."""
from __future__ import annotations
import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from .report import Finding, Report

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@dataclass
class Confirmation:
    finding: Finding
    pin: str
    result: str          # FINISHED | TIMEOUT | MPI-ERROR:<name> | SIGNAL:<n> | APP-ERROR:<rc> | NO-PIN
    verdict: str         # confirmed | not-confirmed | n/a
    stderr_tail: str = ""


def classify_rc(rc: int, stderr: str) -> str:
    if rc == 0:
        return "FINISHED"
    if rc in (124, 137):
        return "TIMEOUT"
    m = re.search(r"MPI_ERR_[A-Z_]+", stderr)
    if m:
        return "MPI-ERROR:" + m.group(0)
    if re.search(r"truncat", stderr, re.I):
        return "MPI-ERROR:MPI_ERR_TRUNCATE"
    if rc >= 128:
        return f"SIGNAL:{rc - 128}"
    return f"APP-ERROR:{rc}"


def expected_outcomes(f: Finding) -> set:
    """Which replay results confirm a finding of this class. An MPI error raised on the way
    to a predicted deadlock or orphan (e.g. a truncation on the same re-matched receive)
    also confirms that the alternative matching was reached."""
    if f.cls in ("1a",):
        return {"MPI-ERROR:MPI_ERR_TRUNCATE", "MPI-ERROR:MPI_ERR_TYPE"}
    if f.cls in ("1c", "1d", "2-buffering"):
        return {"TIMEOUT", "MPI-ERROR:*"}
    return set()   # 1b, 1f, probe-alt, waitany-order: any outcome is consistent (info only)


def run_pinned(np: int, exe: str, args: List[str], pin: Optional[str], timeout: int = 20, mpi: str = "openmpi",
               mpirun_args: Optional[List[str]] = None, extra_env: Optional[dict] = None) -> subprocess.CompletedProcess:
    pinlib = os.path.join(ROOT, "replay", "bin", mpi, "librematch-pin.so")
    env_args = ["-x", f"LD_PRELOAD={pinlib}"]
    if pin:
        env_args += ["-x", pin]   # "REMATCH_PIN=..."
    for k, v in (extra_env or {}).items():
        env_args += ["-x", f"{k}={v}"]
    cmd = ["timeout", "--foreground", "-k", "5", str(timeout), "/usr/bin/mpirun.openmpi", "-np", str(np),
           *(mpirun_args or []), *env_args, exe, *args]
    return subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env={**os.environ, "OMPI_MCA_rmaps_base_oversubscribe": "1"})


def confirm(rep: Report, np: int, exe: str, args: List[str] = (), timeout: int = 20, mpi: str = "openmpi",
            only_errors: bool = True) -> List[Confirmation]:
    out = []
    seen = set()
    for f in rep.findings:
        if f.level == "observed":
            continue
        if only_errors and f.cls not in ("1a", "1c", "1d", "1b", "2-buffering"):
            continue
        pin = f.pin
        if pin is None:
            # SP findings share the pin of their L1 parent (same recv/send)
            parent = next((g for g in rep.findings if g.pin and g.recv == f.recv and g.send == f.send), None)
            pin = parent.pin if parent else None
        if pin is None:
            out.append(Confirmation(f, "", "NO-PIN", "n/a"))
            continue
        key = (pin, f.cls)
        if key in seen:
            continue
        seen.add(key)
        r = run_pinned(np, exe, list(args), pin, timeout, mpi)
        res = classify_rc(r.returncode, r.stderr)
        want = expected_outcomes(f)
        hit = res in want or ("MPI-ERROR:*" in want and res.startswith("MPI-ERROR:"))
        verdict = "n/a" if not want else ("confirmed" if hit else "not-confirmed")
        out.append(Confirmation(f, pin, res, verdict, r.stderr[-300:]))
    return out
