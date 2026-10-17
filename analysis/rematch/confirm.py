"""Confirming replay: rerun the program with each finding's REMATCH_PIN and classify the
outcome (hang / MPI error / finished). A finding is confirmed only if the replay library
reported every pin applied and the outcome is the predicted one; a hang with a pin that
never took effect confirms nothing. Prediction stays single-run; this only separates true
predictions from model or tool divergence."""
from __future__ import annotations
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional

from .report import Finding, Report

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@dataclass
class Confirmation:
    finding: Finding
    pin: str
    result: str          # FINISHED | TIMEOUT | KILLED | MPI-ERROR:<name> | SIGNAL:<n> | APP-ERROR:<rc> | NO-PIN
    verdict: str         # confirmed | not-confirmed | n/a
    stderr_tail: str = ""
    pin_applied: bool = False   # every pin of the spec reported itself applied in the rerun


def classify_rc(rc: int, stderr: str, elapsed: Optional[float] = None, timeout: Optional[int] = None) -> str:
    """124 is timeout's own exit status. 137 (SIGKILL) after the timeout has elapsed is
    timeout's own -k kill of a job that ignored SIGTERM (Open MPI's launcher does when its
    ranks are deadlocked), so it is a hang too; before that it is anything from a memory cap
    to a kill, and not a hang."""
    if rc == 0:
        return "FINISHED"
    if rc == 124:
        return "TIMEOUT"
    if rc == 137 or rc == -9:
        if elapsed is not None and timeout and elapsed >= timeout:
            return "TIMEOUT"
        return "KILLED"
    m = re.search(r"MPI_ERR_[A-Z_]+", stderr)
    if m:
        return "MPI-ERROR:" + m.group(0)
    if rc >= 128:
        return f"SIGNAL:{rc - 128}"
    return f"APP-ERROR:{rc}"


def pins_applied(pin: str, stderr: str) -> bool:
    """Did the replay library report every pin of the spec as applied? The library prints
    '[rematch-pin] rank R: applied widx=W ...' (or 'applied probe pidx=P') for each."""
    name, _, spec = pin.partition("=")
    probe = name == "REMATCH_PIN_PROBE"
    for tok in spec.split(","):
        parts = tok.split(":")
        if len(parts) < 3:
            return False
        rank, idx = parts[0], parts[1]
        pat = rf"\[rematch-pin\] rank {rank}: applied {'probe pidx' if probe else 'widx'}={idx}\b"
        if not re.search(pat, stderr):
            return False
    return True


def expected_outcomes(f: Finding) -> set:
    """Which replay results confirm a finding of this class, provided its pins were applied."""
    if f.cls in ("1a",):
        return {"MPI-ERROR:MPI_ERR_TRUNCATE", "MPI-ERROR:MPI_ERR_TYPE"}
    if f.cls in ("1c", "1d", "2-buffering"):
        return {"TIMEOUT"}
    return set()   # 1b, 1f, probe-alt, waitany-order: any outcome is consistent (info only)


def run_pinned(np: int, exe: str, args: List[str], pin: Optional[str], timeout: int = 20, mpi: str = "openmpi",
               mpirun_args: Optional[List[str]] = None, extra_env: Optional[dict] = None) -> subprocess.CompletedProcess:
    pinlib = os.path.join(ROOT, "replay", "bin", mpi, "librematch-pin.so")
    env_args = ["-x", f"LD_PRELOAD={pinlib}"]
    if pin:
        env_args += ["-x", pin]   # "REMATCH_PIN=..."
    for k, v in (extra_env or {}).items():
        env_args += ["-x", f"{k}={v}"]
    # no --foreground: timeout then signals the whole process group, so the ranks go with mpirun
    cmd = ["/usr/bin/timeout", "-k", "5", str(timeout), "/usr/bin/mpirun.openmpi", "-np", str(np),
           *(mpirun_args or []), *env_args, exe, *args]
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                       env={**os.environ, "OMPI_MCA_rmaps_base_oversubscribe": "1"})
    r.elapsed = time.monotonic() - t0   # for classify_rc: a SIGKILL past the timeout is the timeout's
    return r


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
        res = classify_rc(r.returncode, r.stderr, r.elapsed, timeout)
        applied = pins_applied(pin, r.stderr)
        want = expected_outcomes(f)
        verdict = "n/a" if not want else ("confirmed" if res in want and applied else "not-confirmed")
        out.append(Confirmation(f, pin, res, verdict, r.stderr[-300:], applied))
    return out
