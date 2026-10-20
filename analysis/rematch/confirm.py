"""Confirming replay: rerun the program with each finding's REMATCH_PIN and classify the
outcome (hang / MPI error / finished). A finding is confirmed only if (1) every pinned
receive *matched* in the rerun (the replay library prints "matched" when a pinned receive
completes, which with its source rewritten means it took the pinned sender; "applied" only
says the argument was set, and forcing an impossible match also hangs), (2) the outcome is
the predicted one, and (3) a control rerun that pins the same receives to their observed
senders finishes, so that the outcome is the alternative's and not the program's anyway.
A finding whose control does not finish is inconclusive. Prediction stays single-run; this
only separates true predictions from model or tool divergence."""
from __future__ import annotations
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .report import Finding, Report
from .trace import ANY

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@dataclass
class Confirmation:
    finding: Finding
    pin: str
    result: str          # FINISHED | TIMEOUT | KILLED | MPI-ERROR:<name> | SIGNAL:<n> | APP-ERROR:<rc> | NO-PIN
    verdict: str         # confirmed | not-confirmed | n/a
    stderr_tail: str = ""
    pin_applied: bool = False   # every pin of the spec reported itself applied in the rerun
    pin_matched: bool = False   # every pinned receive completed, i.e. took its pinned sender
    control: str = ""           # result of the control rerun (observed senders pinned), "" if none


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


def pins_matched(pin: str, stderr: str) -> bool:
    """Did every pinned receive complete? '[rematch-pin] rank R: matched widx=W src=S' per
    receive pin; a probe pin counts when the probe found its message ('applied probe pidx=P',
    printed only then)."""
    name, _, spec = pin.partition("=")
    probe = name == "REMATCH_PIN_PROBE"
    for tok in spec.split(","):
        parts = tok.split(":")
        if len(parts) < 3:
            return False
        rank, idx = parts[0], parts[1]
        pat = rf"\[rematch-pin\] rank {rank}: " + (rf"applied probe pidx={idx}\b" if probe else rf"matched widx={idx}\b")
        if not re.search(pat, stderr):
            return False
    return True


def control_pin(t, pin: str) -> Optional[str]:
    """The same receives (or probes) pinned to the senders they took in the recorded run.
    Receives that never completed there are left unpinned. None if no pin remains."""
    name, _, spec = pin.partition("=")
    probe = name == "REMATCH_PIN_PROBE"
    toks = []
    for tok in spec.split(","):
        parts = tok.split(":")
        if len(parts) < 3:
            continue
        rank, idx = int(parts[0]), int(parts[1])
        if probe:
            x = next((p for p in t.probes.values() if p.rank == rank and p.pidx == idx), None)
            src = t.sends[x.seen].rank if x is not None and x.seen is not None else None
        else:
            x = next((r for r in t.recvs.values() if r.rank == rank and r.widx == idx), None)
            src = x.msrc if x is not None else None
        if src is not None:
            toks.append(f"{rank}:{idx}:{src}")
    return f"{name}={','.join(toks)}" if toks else None


def horizon_pins(t, f: Finding, B=None) -> Optional[Tuple[List[str], List[str]]]:
    """Pins that force, in a replay, the alternative's execution over its whole horizon, and the
    control: every wildcard receive (REMATCH_PIN) and every wildcard probe that found a message
    (REMATCH_PIN_PROBE) posted inside the horizon is pinned to the sender it took in the run,
    and the changed one to the alternative's (the control keeps the observed sender there too).
    Pinning the changed operation alone leaves the earlier wildcard operations free to match
    differently in the rerun, and then neither the alternative nor its control is the execution
    theorem T1 constructs. Returns (alternative, control) as lists of VAR=value, or None if the
    finding names no single change or the alternative is not feasible at B."""
    from .feasible import check
    if f.probe is not None and f.send is not None:
        chk = check(t, t.observed, set(), B, "L1", changed_probes={f.probe}, probe_alt={f.probe: f.send})
        change = ("probe", f.probe, f.send)
    elif f.recv is not None and f.send is not None and f.level in ("L1", "L1+SP"):
        M = dict(t.observed)
        victim = next((rk for rk, sk in t.observed.items() if sk == f.send and rk != f.recv), None)
        if victim is not None:
            del M[victim]
        M[f.recv] = f.send
        chk = check(t, M, {f.recv}, B, "L1")
        change = ("recv", f.recv, f.send)
    else:
        return None
    if not chk.feasible or chk.cut is None:
        return None
    cut = chk.cut

    def specs(alternative: bool) -> List[str]:
        rtok, ptok = [], []
        for r in sorted(t.recvs.values(), key=lambda r: (r.rank, r.idx)):
            if r.widx is None or r.idx >= cut[r.rank]:
                continue
            sk = change[2] if alternative and change[0] == "recv" and change[1] == r.key else t.observed.get(r.key)
            if sk is None:
                continue
            m = t.sends[sk]
            rtok.append(f"{r.rank}:{r.widx}:{m.rank}" + (f":{m.tag}" if r.tag == ANY else ""))
        for q in sorted(t.probes.values(), key=lambda q: (q.rank, q.idx)):
            if q.pidx is None or not q.flag or q.idx >= cut[q.rank]:
                continue
            sk = change[2] if alternative and change[0] == "probe" and change[1] == q.key else q.seen
            if sk is None:
                continue
            m = t.sends[sk]
            ptok.append(f"{q.rank}:{q.pidx}:{m.rank}" + (f":{m.tag}" if q.tag == ANY else ""))
        out = []
        if rtok:
            out.append("REMATCH_PIN=" + ",".join(rtok))
        if ptok:
            out.append("REMATCH_PIN_PROBE=" + ",".join(ptok))
        return out

    return specs(True), specs(False)


def all_matched(pins: List[str], stderr: str) -> bool:
    return all(pins_matched(p, stderr) for p in pins)


def expected_outcomes(f: Finding) -> set:
    """Which replay results confirm a finding of this class, provided its pins were applied."""
    if f.cls in ("1a",):
        return {"MPI-ERROR:MPI_ERR_TRUNCATE", "MPI-ERROR:MPI_ERR_TYPE"}
    if f.cls in ("1c", "1d", "2-buffering"):
        return {"TIMEOUT"}
    return set()   # 1b, 1f, probe-alt, waitany-order: any outcome is consistent (info only)


def run_pinned(np: int, exe: str, args: List[str], pin, timeout: int = 20, mpi: str = "openmpi",
               mpirun_args: Optional[List[str]] = None, extra_env: Optional[dict] = None) -> subprocess.CompletedProcess:
    """pin: one "REMATCH_PIN=..." assignment, a list of them, or None."""
    pinlib = os.path.join(ROOT, "replay", "bin", mpi, "librematch-pin.so")
    env_args = ["-x", f"LD_PRELOAD={pinlib}"]
    for assignment in ([pin] if isinstance(pin, str) else (pin or [])):
        env_args += ["-x", assignment]   # "REMATCH_PIN=..." or "REMATCH_PIN_PROBE=..."
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
            only_errors: bool = True, t=None) -> List[Confirmation]:
    """t, the trace, gives the horizon pins and the control reruns; without it no finding can be
    confirmed. A finding whose consequence holds in every execution with the change is replayed
    with the horizon pins (every wildcard operation of the alternative's horizon pinned); one
    whose consequence holds in some execution with the pins of the explorer's witness."""
    out = []
    seen = set()
    controls: dict = {}

    def control(pins: List[str]) -> str:
        if t is None or not pins:
            return ""
        key = tuple(pins)
        if key not in controls:
            r = run_pinned(np, exe, list(args), pins, timeout, mpi)
            controls[key] = classify_rc(r.returncode, r.stderr, r.elapsed, timeout)
        return controls[key]

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
        hp = horizon_pins(t, f) if t is not None and f.certainty != "some" else None
        if hp is not None:
            pins, ctl_pins = hp
        elif pin is not None:
            pins = [pin]
            cp = control_pin(t, pin) if t is not None else None
            ctl_pins = [cp] if cp else []
        else:
            out.append(Confirmation(f, "", "NO-PIN", "n/a"))
            continue
        key = (tuple(pins), f.cls)
        if key in seen:
            continue
        seen.add(key)
        r = run_pinned(np, exe, list(args), pins, timeout, mpi)
        res = classify_rc(r.returncode, r.stderr, r.elapsed, timeout)
        applied = all(pins_applied(x, r.stderr) for x in pins)
        matched = all_matched(pins, r.stderr)
        want = expected_outcomes(f)
        ctl = control(ctl_pins) if want and res in want and matched else ""
        if not want:
            verdict = "n/a"
        elif res not in want or not matched:
            verdict = "not-confirmed"
        else:
            verdict = "confirmed" if ctl == "FINISHED" else "inconclusive"
        out.append(Confirmation(f, " ".join(pins), res, verdict, r.stderr[-300:], applied, matched, ctl))
    return out
