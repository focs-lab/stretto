"""Confirming replay: rerun the program with each finding's STRETTO_PIN and classify the
outcome (hang / MPI error / finished). A finding is confirmed only if (1) every pinned
receive *matched* in the rerun (the replay library prints "matched" when a pinned receive
completes, which with its source rewritten means it took the pinned sender; "applied" only
says the argument was set, and forcing an impossible match also hangs), (2) the outcome is
the predicted one, and (3) a control rerun that pins the same receives to their observed
senders finishes, so that the outcome is the alternative's and not the program's anyway.
A finding whose control does not finish is inconclusive. Prediction stays single-run; this
only separates true predictions from model or tool divergence.

A pin turns a wildcard receive into a receive from one source, which is another program: a
replay can match every pin of an alternative that no run of the program has. When the tracer
is built, the replays and controls run under it (run_checked): it applies the pins and records
the run with the original envelopes, and the replay's own trace is judged as a run of the
program without the pins (unpinned_violation). The strict verdict (strict_verdict) confirms a
finding only if, in addition, its replay is such a run up to where it ended.

A pin names an operation by an ordinal, and carries the traced operation's communicator, tag
and call site (pin_token): a replay whose pin falls on another operation, because it diverged
before it, is ended by the replay library and judged inconclusive ("pin misapplied"), never
confirmed or refuted."""
from __future__ import annotations
import itertools
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import keep as keep_mod
from .report import Finding, Report
from .horizon import _cidx
from .trace import ANY

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class HarnessError(RuntimeError):
    """A replay could not be run as asked: the library it preloads is missing, or it did not
    load. ld.so ignores a preloaded library it cannot open and the program then runs unpinned
    and undelayed, which would read as a replay or a baseline run that shows nothing."""


# what the replay library, and the tracer applying pins or delays, print at MPI_Init (replay/pin.c)
LOADED = re.compile(r"^\[stretto-pin\] rank 0: loaded\b", re.M)
DELAY_VARS = ("STRETTO_SEND_DELAY_MS", "STRETTO_RECV_DELAY_MS", "STRETTO_DELAY_AT")


def need_preload(lib: str) -> None:
    """Raise HarnessError unless lib, a library to preload, exists."""
    if not os.path.isfile(lib):
        raise HarnessError(f"{lib} is missing (make -C replay or make -C tracer, with MPI=openmpi or mpich)")


def check_loaded(lib: str, stderr: str) -> None:
    """Raise HarnessError unless the run's stderr has the line rank 0 prints when lib loaded."""
    if not LOADED.search(stderr):
        why = next((ln for ln in stderr.splitlines() if "LD_PRELOAD" in ln or "cannot be preloaded" in ln), "")
        raise HarnessError(f"{lib} did not announce itself in the run's output" + (f": {why.strip()}" if why else ""))


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
    run_valid: str = ""         # "True": the replay's own trace is a run of the program without the pins; "False";
                                # "unknown" (unreadable, or too many readings); "" when the replay was not recorded
    violation: str = ""         # why it is not such a run, or why that is unknown
    control_valid: str = ""     # the same for the control
    control_violation: str = ""
    strict: str = ""            # strict_verdict(verdict, run_valid)
    misapplied: str = ""        # pins of the replay or control that fell on another operation than the
                                # traced one (the run diverged before them): the verdict is inconclusive


def strict_verdict(verdict: str, run_valid: str) -> str:
    """The verdict when a confirmation also needs the replay to be a run of the program without
    its pins: a confirmed finding whose replay is no such run is confirmed by the pins only, one
    whose replay could not be judged is undetermined; every other verdict stays."""
    if verdict != "confirmed":
        return verdict
    return {"True": "confirmed", "False": "pins only"}.get(run_valid, "undetermined")


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


# ------------------------------------------------------------------ pins and their identity
#
# A pin names an operation by its ordinal among the rank's wildcard receives (widx) or among its
# wildcard probes that found a message (pidx), counted over every communicator, tag and call
# site. A replay that diverges before the pin (one wildcard receive more or less, a polling loop
# that finds a message in another order) would put the pin on another operation, which can only
# take the pinned source and so reports "matched". Each token therefore carries the identity of
# the traced operation after '@': its communicator (the tracer's local id, the communicator's
# creation ordinal on the rank), its tag argument (-1 for ANY_TAG) and its call site (the offset
# of the MPI call's return address in its object), "rank:idx:src[:tag]@comm:tag[:site]"; 'x'
# leaves a part unchecked. The replay library compares them with the operation at the ordinal
# and ends the run on a difference ("misapplied", replay/pin.h); its report lines carry the
# run's identity, and the replay's own trace is compared too (trace_matches,
# trace_misapplied). A token without '@', as pins were written before, is not checked.

PIN_MISAPPLIED = 86      # the exit code the replay library ends a run with (replay/pin.h STRETTO_PIN_MISAPPLIED)

Ident = Tuple[Optional[int], Optional[int], Optional[int]]     # (communicator, tag, site); None: unchecked


@dataclass(frozen=True)
class PinToken:
    kind: str                 # "recv" (STRETTO_PIN), "probe" (STRETTO_PIN_PROBE) or "flip" (STRETTO_PIN_FLIP)
    rank: int
    idx: int                  # widx, pidx, or a flip's poll key (Trace.poll_key)
    src: int                  # the sender's world rank; -1 a flag 0, -2 a test's request (flips, replay/pin.h)
    tag: Optional[int]        # the tag an ANY_TAG operation takes, None if the pin names none
    ident: Optional[Ident]    # the traced operation's identity, None in a token without it


def _parse_ident(text: str) -> Ident:
    f = (text.split(":") + ["x", "x", "x"])[:3]
    return (None if f[0] == "x" else int(f[0]), None if f[1] == "x" else int(f[1]),
            None if f[2] == "x" else int(f[2], 16))


def _ident_text(ident: Ident) -> str:
    c, g, s = ident
    return f"{'x' if c is None else c}:{'x' if g is None else g}" + ("" if s is None else f":{s:x}")


def _pin_kind(name: str) -> str:
    return {"STRETTO_PIN_PROBE": "probe", "STRETTO_PIN_FLIP": "flip"}.get(name, "recv")


def pin_tokens(pins) -> List[PinToken]:
    """The tokens of "STRETTO_PIN=..." and "STRETTO_PIN_PROBE=..." assignments (one, a list, or
    None), in both formats."""
    out = []
    for a in ([pins] if isinstance(pins, str) else (pins or [])):
        name, _, spec = a.partition("=")
        kind = _pin_kind(name)
        for tok in re.split(r"[,\s]+", spec):
            head, at, ident = tok.partition("@")
            parts = head.split(":")
            if len(parts) < 3:
                continue
            out.append(PinToken(kind, int(parts[0]), int(parts[1]), int(parts[2]),
                                int(parts[3]) if len(parts) > 3 else None, _parse_ident(ident) if at else None))
    return out


def strip_identity(pins: str) -> str:
    """The pins as written before they carried identities (for tables, whose pin columns name
    the pins in that form)."""
    return re.sub(r"@[^,\s]*", "", pins)


def pin_name(pin: Optional[str]) -> str:
    """A pin as part of a file name, for the trace a replay with it keeps (stretto.keep):
    STRETTO_PIN=1:0:0:0@... -> 1.0.0.0, STRETTO_PIN_PROBE=1:0:2 -> probe-1.0.2."""
    if not pin:
        return "nopin"
    name, _, spec = strip_identity(pin).partition("=")
    return {"probe": "probe-", "flip": "flip-"}.get(_pin_kind(name), "") + spec.replace(":", ".").replace(",", "_")


def _local_comms(t) -> Dict[Tuple[int, int], int]:
    """(rank, global communicator id) -> the communicator's local id on that rank, its creation
    ordinal there (the "comm" field of the records, which the replay library sees), from the
    communicator records the loader keeps; the loader's own mapping run backwards. Cached on t."""
    cache = t.__dict__.get("_stretto_local_comms")
    if cache is not None:
        return cache
    from . import trace as trace_mod
    folded = {rank: [rec.raw for rec in recs if rec.op == "comm" and rec.raw is not None] for rank, recs in t.ranks.items()}
    scratch = trace_mod.Trace()
    scratch.ranks = {rank: [] for rank in t.ranks}
    all_ranks = list(range(t.np)) if t.np else sorted(t.ranks)
    try:
        mapping = trace_mod._comm_ids(scratch, folded, all_ranks)
    except (TypeError, AttributeError):       # the loader's mapping changed shape: communicators unchecked
        mapping = {}
    cache, seen = {}, set()
    for (rank, local), g in mapping.items():
        if (rank, g) in seen:
            cache.pop((rank, g), None)         # two local ids of one rank on one communicator: unchecked
            continue
        seen.add((rank, g))
        cache[(rank, g)] = local
    t.__dict__["_stretto_local_comms"] = cache
    return cache


def identity(t, x) -> Ident:
    """The identity of receive or probe x of trace t: its communicator's local id on its rank,
    its tag argument (ANY_TAG is -1) and its call site's offset, None where the trace lacks it."""
    site = t.site_of(x.rank, x.idx)
    return _local_comms(t).get((x.rank, x.comm)), x.tag, (site[1] if site else None)


def pin_token(t, x, src: int, tag: Optional[int] = None) -> str:
    """The pin of receive or probe x of trace t to world rank src (and to tag, for an ANY_TAG
    operation), with x's identity."""
    idx = x.pidx if hasattr(x, "pidx") else x.widx
    return f"{x.rank}:{idx}:{src}" + (f":{tag}" if x.tag == ANY and tag is not None else "") + "@" + _ident_text(identity(t, x))


def _op_at(t, kind: str, rank: int, idx: int):
    """The receive with widx idx, or the probe with pidx idx, of rank in trace t, or None. The
    index is built once per trace: a replay's trace can hold millions of receives and its
    horizon pin tens of thousands of them."""
    ix = t.__dict__.get("_stretto_ordinals")
    if ix is None:
        ix = {("recv", r.rank, r.widx): r for r in t.recvs.values() if r.widx is not None}
        ix.update({("probe", q.rank, q.pidx): q for q in t.probes.values() if q.pidx is not None})
        t.__dict__["_stretto_ordinals"] = ix
    return ix.get((kind, rank, idx))


def with_identity(t, pins: List[str]) -> List[str]:
    """The pins with the identity of the operation each names in trace t added where a token
    has none (a finding's own pin, a witness's)."""
    out = []
    for a in pins:
        name, _, spec = a.partition("=")
        kind = _pin_kind(name)
        toks = []
        for tok in spec.split(","):
            parts = tok.split("@")[0].split(":")
            x = _op_at(t, kind, int(parts[0]), int(parts[1])) if "@" not in tok and len(parts) >= 3 else None
            toks.append(tok if x is None else tok + "@" + _ident_text(identity(t, x)))
        out.append(f"{name}={','.join(toks)}")
    return out


def _agrees(expected: Optional[Ident], seen: Optional[Ident]) -> bool:
    """Does an operation of identity seen fit a pin that expects identity expected? A pin
    without one fits anything; one with one needs every part it names, equal."""
    if expected is None:
        return True
    if seen is None:
        return False
    return all(e is None or e == s for e, s in zip(expected, seen))


def _expected(pins) -> Dict[Tuple[str, int, int], Optional[Ident]]:
    return {(k.kind, k.rank, k.idx): k.ident for k in pin_tokens(pins)}


_LINE = re.compile(r"\[stretto-pin\] rank (\d+): (matched widx|applied widx|applied probe pidx|applied unflip pidx"
                   r"|applied flip key|matched flip key)=(\d+)\b([^\n]*)")
# what each line says, and the pin it shows taken: a receive pin when its receive completed, a
# probe pin when the probe found its message or returned false as pinned (unflip), a flip pin
# when the forced call returned true (matched flip) or false as pinned (applied flip ... flag=0)
_WHAT = {"matched widx": "matched", "applied widx": "applied", "applied probe pidx": "probe",
         "applied unflip pidx": "unflip", "applied flip key": "flip-applied", "matched flip key": "flip"}
_LINE_ID = re.compile(r"\bid=(-?[0-9]+|x):(-?[0-9]+|x):([0-9a-f]+|x)")
_MISAPPLIED = re.compile(r"\[stretto-pin\] rank \d+: (?:probe |flip )?pin (?:widx|pidx|key)=\d+ misapplied[^\n]*")


def _lines(stderr: str):
    """(what, rank, idx, identity or None) of every pin report line: what is "matched",
    "applied" (a receive) or "probe" (a probe that found its message). Read once per run: a
    horizon can pin tens of thousands of receives, and a search of the whole output per pin is
    quadratic."""
    for m in _LINE.finditer(stderr):
        i = _LINE_ID.search(m.group(4))
        what = _WHAT[m.group(2)]
        if what == "flip-applied" and " flag=0" in m.group(4):
            what = "flip"          # the control's flip: the call returned false as pinned
        yield what, int(m.group(1)), int(m.group(3)), _parse_ident(":".join(i.groups())) if i else None


def _line_key(what: str, rank: int, idx: int) -> Tuple[str, int, int]:
    """The pin a report line is about."""
    return ({"flip": "flip", "flip-applied": "flip"}.get(what) or ("probe" if what in ("probe", "unflip") else "recv"), rank, idx)


def pins_misapplied(stderr: str) -> List[str]:
    """The replay library's reports of a pin that fell on an operation of another identity."""
    return [m.group(0) for m in _MISAPPLIED.finditer(stderr)]


def pins_applied(pin: str, stderr: str) -> bool:
    """Did the replay library report every pin of the spec as applied, on an operation of the
    pin's identity? It prints '[stretto-pin] rank R: applied widx=W ... id=C:G:X' (or
    'applied probe pidx=P ...', printed when the probe found its message) for each."""
    want = _expected(pin)
    seen = {}
    for what, rank, idx, ident in _lines(stderr):
        if what != "matched":
            seen[_line_key(what, rank, idx)] = ident
    return bool(want) and all(k in seen and _agrees(e, seen[k]) for k, e in want.items())


def _reported(stderr: str, pins=None) -> Set[Tuple[str, int, int]]:
    """The pins a replay reported as taken: ("recv", rank, widx) for each 'matched widx=W',
    ("probe", rank, pidx) for each 'applied probe pidx=P', on an operation whose identity fits
    the pin of pins that names it (a line without an identity fits no pin that has one)."""
    want = _expected(pins) if pins else {}
    out = set()
    for what, rank, idx, ident in _lines(stderr):
        if what in ("applied", "flip-applied"):
            continue
        key = _line_key(what, rank, idx)
        if _agrees(want.get(key), ident):
            out.add(key)
    return out


def _taken(pin: str, reported: Set[Tuple[str, int, int]]) -> bool:
    name, _, spec = pin.partition("=")
    kind = _pin_kind(name)
    for tok in spec.split(","):
        parts = tok.split(":")
        if len(parts) < 3 or (kind, int(parts[0]), int(parts[1])) not in reported:
            return False
    return True


def pins_matched(pin: str, stderr: str) -> bool:
    """Did every pinned receive complete? '[stretto-pin] rank R: matched widx=W src=S id=...'
    per receive pin; a probe pin counts when the probe found its message ('applied probe
    pidx=P', printed only then); either on an operation of the pin's identity."""
    return _taken(pin, _reported(stderr, pin))


def control_pin(t, pin: str) -> Optional[str]:
    """The same receives (or probes) pinned to the senders they took in the recorded run, with
    their identity. Receives that never completed there are left unpinned. None if no pin
    remains."""
    name, _, spec = pin.partition("=")
    toks = []
    if _pin_kind(name) == "flip":
        # the trace's own result: the failed poll returns flag 0; the identity as it is (it may
        # carry a fourth field, the probes found before the call, replay/pin.h)
        for tok in re.split(r"[,\s]+", spec):
            head, at, ident = tok.partition("@")
            parts = head.split(":")
            if len(parts) >= 3:
                toks.append(f"{parts[0]}:{parts[1]}:-1" + (at + ident if at else ""))
        return f"{name}={','.join(toks)}" if toks else None
    for k in pin_tokens(pin):
        x = _op_at(t, k.kind, k.rank, k.idx)
        if x is None:
            continue
        if k.kind == "probe":
            src = t.sends[x.seen].rank if x.seen is not None else None
        else:
            src = x.msrc
        if src is not None:
            toks.append(pin_token(t, x, src))
    return f"{name}={','.join(toks)}" if toks else None


def horizon_pins(t, f: Finding, B=None, infeasible_ok: bool = False, M=None, changed=None) -> Optional[Tuple[List[str], List[str]]]:
    """Pins that force, in a replay, the alternative's execution over its whole horizon, and the
    control: every wildcard receive (STRETTO_PIN) and every wildcard probe that found a message
    (STRETTO_PIN_PROBE) posted inside the horizon is pinned to the sender it took in the run,
    and the changed one to the alternative's (the control keeps the observed sender there too);
    the failed poll of a flag flip takes the finding's STRETTO_PIN_FLIP, and the control its
    recorded false.
    The receive a steal leaves without a message is not pinned in the alternative: it has no
    sender there, and pinning it to its observed one would ask for a match the alternative
    excludes. Pinning the changed operation alone leaves the earlier wildcard operations free
    to match differently in the rerun, and then neither the alternative nor its control is the
    execution theorem T1 constructs. Returns (alternative, control) as lists of VAR=value, or
    None if the finding names no single change or the alternative is not feasible at B.
    infeasible_ok=True also pins an alternative the check rejects, over the horizon its closure
    computes, with the changed operation pinned even where the closure cuts it: a replay of it
    tests the rejection (experiments/e8_hb.py). M and changed name another alternative of the
    finding's receive than its steal, such as a swap (matching.Alternative.M, .changed)."""
    h = _horizon(t, f, B, infeasible_ok, M, changed)
    return None if h is None else (h[0], h[1])


def required_matches(t, f: Finding, B=None, infeasible_ok: bool = False, M=None, changed=None) -> Optional[Set[Tuple[str, int, int]]]:
    """The pins of horizon_pins(t, f, B) whose match a replay must show: ("recv", rank, widx)
    for a pinned receive whose completion lies inside the horizon, ("probe", rank, pidx) for a
    pinned probe, ("flip", rank, key) for a flag flip's forced poll. A receive posted inside the
    horizon but completed beyond it is pinned to steer the run, yet its wait may lie after the
    predicted hang, where its match cannot be logged."""
    h = _horizon(t, f, B, infeasible_ok, M, changed)
    return None if h is None else h[2]


def changed_pin(t, f: Finding) -> Optional[Tuple[str, int, int]]:
    """The changed operation's pin as all_matched reports it: ("recv", rank, widx) or ("probe",
    rank, pidx); None if it has no ordinal."""
    if f.probe is not None:
        q = t.probes[f.probe]
        return None if q.pidx is None else ("probe", q.rank, q.pidx)
    if f.recv is not None:
        r = t.recvs[f.recv]
        return None if r.widx is None else ("recv", r.rank, r.widx)
    return None


def _horizon(t, f: Finding, B=None, infeasible_ok: bool = False, M=None, changed=None):
    """M and changed, if given, are the alternative matching and its changed receives (a swap);
    by default the finding is a steal: f.recv takes f.send and the receive that took it in the
    run is left without a message."""
    from .feasible import check
    if f.probe is not None and f.send is not None:
        M = t.observed
        chk = check(t, M, set(), B, "L1", changed_probes={f.probe}, probe_alt={f.probe: f.send})
        change = ("probe", f.probe, f.send)
    elif f.recv is not None and f.send is not None and f.level in ("L1", "L1+SP"):
        if M is None:
            M = dict(t.observed)
            victim = next((rk for rk, sk in t.observed.items() if sk == f.send and rk != f.recv), None)
            if victim is not None:
                del M[victim]
            M[f.recv] = f.send
        chk = check(t, M, set(changed) if changed else {f.recv}, B, "L1")
        change = ("recv", f.recv, f.send)
    else:
        return None
    if chk.cut is None or not (chk.feasible or infeasible_ok):
        return None
    cut = chk.cut
    required: Set[Tuple[str, int, int]] = set()
    # a flag flip changes a failed poll, which no probe pin names: its own pin (STRETTO_PIN_FLIP,
    # the message to return) forces it in the alternative, and the control pins the recorded
    # false (control_pin); the alternative must show "matched flip key" for the pin's key, the
    # forced call returning true
    flip = change[0] == "probe" and not t.probes[change[1]].flag
    if flip:
        ftoks = [k for k in pin_tokens(f.pin) if k.kind == "flip"] if f.pin else []
        if len(ftoks) != 1:
            return None
        flip_ctl = control_pin(t, f.pin)

    def specs(alternative: bool) -> List[str]:
        rtok, ptok = [], []
        for r in sorted(t.recvs.values(), key=lambda r: (r.rank, r.idx)):
            outside = r.idx >= cut[r.rank] and not (change[0] == "recv" and change[1] == r.key)
            if r.widx is None or outside or (alternative and r.key in chk.deprived):
                continue          # a receive rule 6 deprived of its message may take another: not pinned
            # the alternative's sender (none for the receive a steal leaves without a message)
            sk = M.get(r.key) if alternative else t.observed.get(r.key)
            if sk is None:
                continue
            m = t.sends[sk]
            rtok.append(pin_token(t, r, m.rank, m.tag))
            if alternative and r.completed and _cidx(r) < cut[r.rank]:
                required.add(("recv", r.rank, r.widx))
        for q in sorted(t.probes.values(), key=lambda q: (q.rank, q.idx)):
            outside = q.idx >= cut[q.rank] and not (change[0] == "probe" and change[1] == q.key)
            if q.pidx is None or not q.flag or outside:
                continue
            sk = change[2] if alternative and change[0] == "probe" and change[1] == q.key else q.seen
            if sk is None:
                continue
            m = t.sends[sk]
            ptok.append(pin_token(t, q, m.rank, m.tag))
            if alternative:
                required.add(("probe", q.rank, q.pidx))
        out = []
        if rtok:
            out.append("STRETTO_PIN=" + ",".join(rtok))
        if ptok:
            out.append("STRETTO_PIN_PROBE=" + ",".join(ptok))
        if flip:
            out.append(f.pin if alternative else flip_ctl)
            if alternative:
                required.add(("flip", ftoks[0].rank, ftoks[0].idx))
        return out

    return specs(True), specs(False), required


def parse_pins(pins) -> Tuple[Dict[Tuple[int, int], Tuple[int, Optional[int]]], Dict[Tuple[int, int], Tuple[int, Optional[int]]]]:
    """The receive and probe pins of a replay, from "STRETTO_PIN=..." and "STRETTO_PIN_PROBE=..."
    assignments (one, a list, or None): (rank, widx) and (rank, pidx) -> (world source, tag or
    None when the pin names none)."""
    recv: Dict[Tuple[int, int], Tuple[int, Optional[int]]] = {}
    probe: Dict[Tuple[int, int], Tuple[int, Optional[int]]] = {}
    for k in pin_tokens(pins):
        if k.kind == "flip" or k.src < 0:
            # a flip forces a poll's result without changing its envelope, and a probe pinned to
            # flag 0 leaves its ordinal to the next probe that finds a message, unpinned
            continue
        (probe if k.kind == "probe" else recv)[(k.rank, k.idx)] = (k.src, k.tag)
    return recv, probe


def _envelope(x, pin) -> Tuple[int, int]:
    """(source, tag) that operation x accepted in the replay: the pin's source in place of
    ANY_SOURCE, and its tag, if it names one, in place of ANY_TAG (replay/pin.c)."""
    if pin is None:
        return x.src, x.tag
    src, tag = pin
    return (src if x.src == ANY else x.src), (tag if x.tag == ANY and tag is not None else x.tag)


def _takes(env: Tuple[int, int], queue: Tuple[int, int, int, int]) -> bool:
    """Does a receive with this (source, tag) take the messages of queue (src, dst, comm, tag)?
    The caller has checked the destination and the communicator."""
    return env[0] in (ANY, queue[0]) and env[1] in (ANY, queue[3])


def _queues(t):
    """The (src, dst, comm, tag) queues in issue order, and per rank its receives and probes in
    posting order: what trace._reconstruct_observed walks."""
    queues: Dict[Tuple[int, int, int, int], list] = {}
    for s in sorted(t.sends.values(), key=lambda s: (s.rank, s.idx)):
        queues.setdefault((s.rank, s.dst, s.comm, s.tag), []).append(s)
    events: Dict[int, list] = {}
    for x in list(t.recvs.values()) + list(t.probes.values()):
        events.setdefault(x.rank, []).append(x)
    for v in events.values():
        v.sort(key=lambda x: x.idx)
    return queues, events


def _rematch(t, queues, events, assign):
    """The run's matching, and the probes whose observed message differs from t's, if the
    pending receives in assign took the head of the queue it names while the receives and
    probes before them took theirs (trace._reconstruct_observed with those receives added;
    every later receive of the queue then took the next message). None if a queue runs out."""
    from .trace import Probe
    M: Dict = {}
    alt: Dict = {}
    ptr: Dict = {}
    for rank, evs in events.items():
        reserved: Dict = {}
        for e in evs:
            if isinstance(e, Probe):
                if not e.flag or e.msrc is None:
                    continue
                key = (e.msrc, rank, e.comm, e.mtag)
                q, i = queues.get(key), ptr.get(key, 0)
                if q and i < len(q):
                    if q[i].key != e.seen:
                        alt[e.key] = q[i].key
                    if e.kind in ("mprobe", "improbe"):
                        reserved.setdefault(key, []).append(q[i])
                        ptr[key] = i + 1
                continue
            if e.completed and e.msrc is not None:
                key = (e.msrc, rank, e.comm, e.mtag)
            elif e.key in assign:
                key = assign[e.key]
            else:
                continue
            if e.mrecv and reserved.get(key):
                M[e.key] = reserved[key].pop(0).key
                continue
            q, i = queues.get(key), ptr.get(key, 0)
            if not q or i >= len(q):
                if e.key in assign:
                    return None
                continue
            M[e.key] = q[i].key
            ptr[key] = i + 1
    return M, alt


def _blocked_on(t) -> Set:
    """The receives a hung run shows unmatched at its end: a blocking receive a rank was blocked
    in, and the receives of a wait, waitany or waitsome a rank was blocked in (one of them
    matched would have ended the call). A waitall can be held by any one of its requests."""
    out = set()
    for rec in t.blocked_calls():
        call = rec.raw.get("call")
        if call == "recv":
            r = next((x for x in t.recvs_of(rec.rank) if x.idx == rec.idx), None)
            if r is not None:
                out.add(r.key)
        elif call == "wait" and rec.raw.get("kind") in ("wait", "waitany", "waitsome"):
            for q in rec.raw.get("reqs") or []:
                r = t.recv_by_req(rec.rank, q) if q is not None else None
                if r is not None and not r.completed:
                    out.add(r.key)
    return out


def _end_probes(t, ppins) -> list:
    """(probe, its envelope in the replay, its pin) for every probe a hung run ended in without
    a message: a blocking probe a rank was blocked in, or a failed Iprobe that is its rank's
    last record (a polling loop). Its pin is the one of the rank's next probe ordinal
    (replay/pin.c counts the wildcard probes that found a message)."""
    nxt: Dict[int, int] = {}
    for q in t.probes.values():
        if q.pidx is not None:
            nxt[q.rank] = max(nxt.get(q.rank, 0), q.pidx + 1)
    last = {}
    for rank in t.truncated:
        recs = [r for r in t.ranks.get(rank, []) if r.op != "guard"]
        if recs:
            last[rank] = recs[-1].idx
    out = []
    for p in t.probes.values():
        polling = not p.flag and last.get(p.rank) == p.idx
        if p.completed and not polling:
            continue
        pin = ppins.get((p.rank, nxt.get(p.rank, 0))) if p.wildcard else None
        out.append((p, _envelope(p, pin), pin))
    return out


def _end_state(t, queues, M, pend, env, pinned, end_probes) -> Tuple[bool, Optional[str]]:
    """The end of a hung run, given which pending receives were matched (M): every message
    taken is a prefix of its queue, the rest of the queue is still there. A pending receive left
    unmatched, or a probe the run ended in, whose envelope in the replay takes a message still
    there contradicts the replay (MPI's progress rule would have matched them): (False, None),
    a reading to drop. One whose original envelope alone takes it is the pin's doing: the
    program would have taken that message, and the hang is no hang of the program."""
    taken = Counter()
    for sk in M.values():
        s = t.sends[sk]
        taken[(s.rank, s.dst, s.comm, s.tag)] += 1
    left: Dict[Tuple[int, int], list] = {}
    for key, q in queues.items():
        rest = [s for s in q[taken[key]:] if not s.cancelled and s.reserved_idx is None]
        if rest:
            left.setdefault((key[1], key[2]), []).append((key, rest[0]))
    why = None
    ops = [(r, env[r.key], pinned.get(r.key)) for r in pend if r.key not in M] + list(end_probes)
    for x, e, pin in ops:
        for key, s in left.get((x.rank, x.comm), ()):
            if _takes(e, key):
                return False, None
            if why is None and _takes((x.src, x.tag), key):
                how = f" (pinned to rank {pin[0]}" + (f", tag {pin[1]}" if pin[1] is not None else "") + ")" if pin else ""
                why = (f"end of the run: {x}{how} was still waiting while {s}, which it takes without the pin, "
                       "was not taken: the program would have matched them")
    return True, why


def unpinned_violation(t, finished: bool = True, hung: bool = False, pins=None, limit: int = 4096) -> Optional[str]:
    """Is the recorded run of a pinned replay a run of the program without the pins? A pin
    rewrites a wildcard receive into one from a given source, so a later receive of its rank can
    take a message the original receive accepts while the pinned one is still pending, which MPI
    forbids for the original receive (non-overtaking, MPI 4.1 §3.5), and a probe can report such
    a message. The pinned run then forces a match that no run of the program has. The tracer
    records the original envelope of a pinned operation, so t, the replay's own trace, is judged
    by MPI's matching order on its matches with those envelopes (matching.matching_order_ok
    over every receive and probe) and by the acyclicity of its constraint graph with infinite
    buffering, the weakest.

    A receive still pending at the end of the trace (a run that did not finish, or one freed
    while pending) may have been matched without its wait returning, which the trace cannot
    show; then every later receive of its queue took the next message. Each pending receive is
    therefore read as unmatched or as matched to the head of a queue with a message no completed
    receive took (its pinned envelope, pins, decides which queues), and the run is one of the
    program if some reading passes both checks. In a hung run (hung) the receives the run was
    blocked on are unmatched, and the end itself is judged: a pending receive or a probe the run
    ended in that, without its pin, takes a message nobody took would have taken it, and the
    hang is the pins'. A reading in which the pinned envelope takes such a message contradicts
    the replay itself and is dropped. Returns the violation of the first reading, None if some
    reading is a run of the program, or a reason starting with "undetermined" when the readings
    number more than limit or none agrees with the replay's end."""
    from .matching import build_graph, matching_order_ok
    rpins, ppins = parse_pins(pins)
    pend = sorted((r for r in t.recvs.values() if not r.completed and not r.cancelled and not r.mrecv), key=lambda r: r.key)
    end_probes = _end_probes(t, ppins) if hung else []
    if not pend and not end_probes:
        why = matching_order_ok(t, t.observed, lambda rk: True, None, lambda q: True)
        if why is None and build_graph(t, t.observed, None, None).find_cycle():
            why = "constraint cycle over the run's own matches"
        return why
    pinned = {r.key: rpins.get((r.rank, r.widx)) for r in pend if r.widx is not None}
    env = {r.key: _envelope(r, pinned.get(r.key)) for r in pend}
    queues, events = _queues(t)
    used = Counter()
    for sk in t.observed.values():
        s = t.sends[sk]
        used[(s.rank, s.dst, s.comm, s.tag)] += 1
    by_dst: Dict[Tuple[int, int], list] = {}
    for key, q in queues.items():
        if len(q) > used[key]:
            by_dst.setdefault((key[1], key[2]), []).append(key)
    forced = _blocked_on(t) if hung else set()
    choices = [[None] + ([] if r.key in forced else [k for k in by_dst.get((r.rank, r.comm), ()) if _takes(env[r.key], k)])
               for r in pend]
    n = math.prod(len(c) for c in choices)
    if n > limit:
        return f"undetermined: {len(pend)} pending receives have {n} readings, more than {limit}"
    first = None
    for combo in itertools.product(*choices):
        got = _rematch(t, queues, events, {r.key: k for r, k in zip(pend, combo) if k is not None})
        if got is None:
            continue
        M, alt = got
        end = None
        if hung:
            agrees, end = _end_state(t, queues, M, pend, env, pinned, end_probes)
            if not agrees:
                continue
        why = matching_order_ok(t, M, lambda rk: True, None, lambda q: True, alt) or end
        if why is None and build_graph(t, M, None, None, probe_alt=alt).find_cycle():
            why = "constraint cycle over the run's own matches"
        if why is None:
            return None
        first = first or why
    return first or "undetermined: no reading of the pending receives agrees with the end of the replay"


def trace_matches(t, pins) -> Set[Tuple[str, int, int]]:
    """The pins a replay's own trace shows taken, as _reported has them: ("recv", rank, widx)
    for a pinned receive that completed from its pinned source (with its pinned tag, if the pin
    names one), ("probe", rank, pidx) for a pinned probe that saw a message of its pinned
    source. The trace says what the run did; the replay library's report lines, which Open
    MPI's mpirun merges from the ranks' stderr in chunks, can be cut apart and missed. A pin
    with an identity counts only on an operation of that identity (trace_misapplied)."""
    toks = {(k.kind, k.rank, k.idx): k for k in pin_tokens(pins)}
    out: Set[Tuple[str, int, int]] = set()
    for r in t.recvs.values():
        k = toks.get(("recv", r.rank, r.widx)) if r.widx is not None else None
        if k and r.completed and r.msrc == k.src and (k.tag is None or r.mtag == k.tag) and _agrees(k.ident, identity(t, r)):
            out.add(("recv", r.rank, r.widx))
    for q in t.probes.values():
        k = toks.get(("probe", q.rank, q.pidx)) if q.pidx is not None else None
        if k and q.flag and q.msrc == k.src and (k.tag is None or q.mtag == k.tag) and _agrees(k.ident, identity(t, q)):
            out.add(("probe", q.rank, q.pidx))
    return out


def trace_misapplied(t, pins) -> List[str]:
    """The pins of pins whose ordinal falls, in the replay's own trace t, on an operation of
    another identity than the traced one the pin names: the replay diverged before them."""
    out = []
    for k in pin_tokens(pins):
        if k.ident is None:
            continue
        x = _op_at(t, k.kind, k.rank, k.idx)
        if x is not None and not _agrees(k.ident, identity(t, x)):
            out.append(f"rank {k.rank} {'pidx' if k.kind == 'probe' else 'widx'}={k.idx}: the replay's operation has "
                       f"id={_ident_text(identity(t, x))}, the pin's id={_ident_text(k.ident)}")
    return out


def judge_replay(tracedir: str, result: str, pins=None, seen: Optional[set] = None,
                 misapplied: Optional[list] = None, ended: Optional[set] = None) -> Tuple[str, str]:
    """("True", "") if the trace a replay recorded in tracedir is a run of the program without
    its pins up to where the run ended (unpinned_violation; result is the replay's
    classify_rc), ("False", the violation), or ("unknown", why) when the trace cannot be read or
    its readings are too many. seen, if given, receives the pins the trace shows taken
    (trace_matches), misapplied the pins that fell on another operation (trace_misapplied),
    ended the pinned receives still pending where their rank stopped (trace_ended)."""
    from . import trace as trace_mod
    try:
        t = trace_mod.load(tracedir)
    except Exception as e:  # noqa: BLE001  (a run killed while writing: judged unknown, not fatal)
        return "unknown", f"trace unreadable: {e}"[:200]
    if seen is not None:
        seen |= trace_matches(t, pins)
    if misapplied is not None:
        misapplied += trace_misapplied(t, pins)
    if ended is not None:
        ended |= trace_ended(t, pins)
    why = unpinned_violation(t, finished=result == "FINISHED", hung=result == "TIMEOUT", pins=pins)
    if why is None:
        return "True", ""
    return ("unknown" if why.startswith("undetermined") else "False"), why


def all_matched(pins: List[str], stderr: str, required: Optional[Set[Tuple[str, int, int]]] = None,
                seen: Optional[Set[Tuple[str, int, int]]] = None) -> bool:
    """Every pin matched (pins_matched), or, given required (required_matches), every required one.
    A pin counts when stderr reports it on an operation of its identity or when seen, the pins
    the replay's own trace shows taken (Replay.seen, trace_matches), has it."""
    reported = _reported(stderr, pins) | (seen or set())
    if required is None:
        return all(_taken(p, reported) for p in pins)
    return required <= reported


def error_of(f: Finding) -> str:
    """The error a 1a finding predicts (Finding.error), read from its title for a finding made
    before the field existed."""
    if f.error or f.cls != "1a":
        return f.error
    if f.title.startswith(("count mismatch", "truncation error")) or "MPI_ERR_TRUNCATE" in " ".join(f.detail):
        return "truncate"
    if f.title.startswith("ready-mode send"):
        return "rsend-no-receive"
    return "type" if "type signature" in " ".join([f.title, *f.detail]) else ""


def expected_outcomes(f: Finding) -> set:
    """Which replay results confirm a finding of this class, provided its pins were applied.
    Of class 1a only a truncation is confirmable: the receive that takes the longer message
    fails with MPI_ERR_TRUNCATE (and, under MPI_ERRORS_ARE_FATAL, the run ends inside it, so its
    match is shown applied, not matched: confirm). A type signature mismatch is erroneous but
    neither Open MPI nor MPICH reports it (no MPI_ERR_TYPE), and a ready send with no posted
    receive is erroneous with an undefined outcome, which neither library reports either: a
    replay can show neither, and they stay unconfirmed whatever it does (n/a)."""
    if f.cls == "1a":
        return {"MPI-ERROR:MPI_ERR_TRUNCATE"} if error_of(f) == "truncate" else set()
    if f.cls in ("1c", "1d", "2-buffering"):
        return {"TIMEOUT"}
    return set()   # 1b, 1f, probe-alt, waitany-order: any outcome is consistent (info only)


def _applied(stderr: str, pins) -> Set[Tuple[str, int, int]]:
    """("recv", rank, widx) of every receive pin the replay library reported applied, on an
    operation of the pin's identity."""
    want = _expected(pins)
    return {("recv", rank, idx) for what, rank, idx, ident in _lines(stderr)
            if what == "applied" and _agrees(want.get(("recv", rank, idx)), ident)}


def trace_ended(t, pins) -> Set[Tuple[str, int, int]]:
    """("recv", rank, widx) of every pinned receive that, in the replay's own trace t, was still
    pending when its rank stopped without MPI_Finalize: posted with its pin (on an operation of
    the pin's identity), never completed. A run that MPI ends with an error inside a receive
    (MPI_ERR_TRUNCATE under MPI_ERRORS_ARE_FATAL) leaves its pinned receive so."""
    toks = {(k.rank, k.idx): k for k in pin_tokens(pins) if k.kind == "recv"}
    stopped = set(t.truncated)
    return {("recv", r.rank, r.widx) for r in t.recvs.values()
            if r.widx is not None and (r.rank, r.widx) in toks and r.rank in stopped and not r.completed
            and _agrees(toks[(r.rank, r.widx)].ident, identity(t, r))}


def truncation_shown(changed: Optional[Tuple[str, int, int]], pins, stderr: str,
                     required: Optional[Set[Tuple[str, int, int]]], matched: Set[Tuple[str, int, int]],
                     ended: Optional[Set[Tuple[str, int, int]]]) -> bool:
    """Did a replay that failed with MPI_ERR_TRUNCATE follow a 1a finding's alternative? The run
    ends inside the receive that takes the longer message, so that receive reports no match: it
    counts when the replay's own trace (ended, trace_ended; None without the tracer) shows it
    still pending where its rank stopped, or, without a trace, when the library reported it
    applied. Every other required pin (every pin, without required) must have matched or be
    such a receive: the error ends every rank, some inside pinned receives that would have
    matched later. changed, the changed receive of an L1 finding, is the one that truncates, and
    must not have matched: a match there took a message that fits, not the alternative's."""
    shown = ended if ended is not None else _applied(stderr, pins)
    need = set(required) if required is not None else {(k.kind, k.rank, k.idx) for k in pin_tokens(pins)}
    if changed is not None:
        if changed not in shown or changed in matched:
            return False
        need.add(changed)
    return bool(need) and all(k in matched or k in shown for k in need)


# The launcher of a program built with each MPI. Open MPI's mpirun runs both: Ubuntu's MPICH is
# built with PMIx, and its own hydra launcher starts every rank as a singleton
# (experiments/RUNBOOK.md, Notes). With another MPICH, set STRETTO_MPIRUN_MPICH to its mpiexec.
DEFAULT_LAUNCHERS = {"openmpi": "/usr/bin/mpirun.openmpi", "mpich": "/usr/bin/mpirun.openmpi"}


def launcher(mpi: str) -> Tuple[str, str]:
    """(path, style) of the launcher for programs built with mpi: STRETTO_MPIRUN_<MPI> (for
    example STRETTO_MPIRUN_MPICH=/usr/bin/mpiexec.hydra), else STRETTO_MPIRUN, else
    DEFAULT_LAUNCHERS. style is "openmpi" (environment by -x NAME=VALUE) or "hydra" (by -genv
    NAME VALUE), from what the launcher says of its version."""
    path = (os.environ.get(f"STRETTO_MPIRUN_{mpi.upper()}") or os.environ.get("STRETTO_MPIRUN")
            or DEFAULT_LAUNCHERS.get(mpi) or DEFAULT_LAUNCHERS["openmpi"])
    if not (os.path.isfile(path) or shutil.which(path)):
        raise HarnessError(f"no launcher {path} for {mpi} (set STRETTO_MPIRUN_{mpi.upper()})")
    return path, _launcher_style(path)


_STYLES: Dict[str, str] = {}


def _launcher_style(path: str) -> str:
    if path not in _STYLES:
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            text = out.stdout + out.stderr
        except (OSError, subprocess.TimeoutExpired):
            text = ""
        if "Open MPI" in text or "OpenRTE" in text:
            _STYLES[path] = "openmpi"
        elif "HYDRA" in text.upper() or "mpich" in text.lower():
            _STYLES[path] = "hydra"
        else:
            _STYLES[path] = "openmpi" if "openmpi" in os.path.basename(path) or "orterun" in path else "hydra"
    return _STYLES[path]


def launcher_env(style: str, name: str, value: str) -> List[str]:
    """The launcher arguments that give every rank the variable name=value."""
    return ["-x", f"{name}={value}"] if style == "openmpi" else ["-genv", name, value]


def run_pinned(np: int, exe: str, args: List[str], pin, timeout: int = 20, mpi: str = "openmpi",
               mpirun_args: Optional[List[str]] = None, extra_env: Optional[dict] = None,
               cwd: Optional[str] = None, preload: Optional[str] = None) -> subprocess.CompletedProcess:
    """pin: one "STRETTO_PIN=..." assignment, a list of them, or None. args and cwd must be
    those of the traced run: the pins number the wildcard operations of that execution.
    preload: the library that applies the pins, the replay library by default; the tracer
    applies them too and records the replay (with STRETTO_TRACE_DIR in extra_env). A library
    that is missing, or that does not announce itself in the run's output (the replay library
    always does, the tracer when it applies pins or delays), raises HarnessError: the run would
    otherwise be a plain one."""
    pinlib = preload or os.path.join(ROOT, "replay", "bin", mpi, "libstretto-pin.so")
    need_preload(pinlib)
    announces = preload is None or bool(pin) or any(k in (extra_env or {}) for k in DELAY_VARS)
    mpirun, style = launcher(mpi)
    if style != "openmpi" and mpirun_args:
        raise HarnessError(f"launcher arguments {mpirun_args} are Open MPI's, and the launcher for {mpi} is {mpirun} ({style})")
    env_args = launcher_env(style, "LD_PRELOAD", pinlib)
    with tempfile.TemporaryDirectory(prefix="stretto-pin-") as d:
        for assignment in ([pin] if isinstance(pin, str) else (pin or [])):
            env_args += launcher_env(style, *pin_assignment(assignment, d).split("=", 1))   # STRETTO_PIN or STRETTO_PIN_PROBE
        for k, v in (extra_env or {}).items():
            env_args += launcher_env(style, k, str(v))
        # no --foreground: timeout then signals the whole process group, so the ranks go with mpirun
        cmd = ["/usr/bin/timeout", "-k", "5", str(timeout), mpirun, "-np", str(np),
               *(mpirun_args or []), *env_args, exe, *args]
        t0 = time.monotonic()
        r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=cwd,
                           env={**os.environ, "OMPI_MCA_rmaps_base_oversubscribe": "1"})
        r.elapsed = time.monotonic() - t0   # for classify_rc: a SIGKILL past the timeout is the timeout's
    if announces:
        check_loaded(pinlib, r.stderr)
    return r


def tracer_lib(mpi: str = "openmpi") -> str:
    return os.path.join(ROOT, "tracer", "bin", mpi, "libstretto-trace.so")


@dataclass
class Replay:
    """A pinned run: the process, its classify_rc, whether its own trace is a run of the
    program without the pins (judge_replay; "" when the tracer was not there to record it), and
    the pins that trace shows taken (trace_matches), for all_matched."""
    proc: subprocess.CompletedProcess
    result: str
    valid: str = ""
    why: str = ""
    seen: Set[Tuple[str, int, int]] = field(default_factory=set)
    misapplied: List[str] = field(default_factory=list)   # pins on another operation (misapplied_pins)
    ended: Optional[Set[Tuple[str, int, int]]] = None      # pinned receives pending where their rank stopped
                                                           # (trace_ended); None when no trace was recorded


def misapplied_pins(r: subprocess.CompletedProcess, result: str, from_trace: List[str] = ()) -> List[str]:
    """Why the run's pins fell on other operations than the traced ones: the replay library's
    report lines, the replay's own trace (trace_misapplied), or, when the lines were lost, the
    exit code the library ends such a run with."""
    out = pins_misapplied(r.stderr if r is not None else "") + list(from_trace)
    if not out and result == f"APP-ERROR:{PIN_MISAPPLIED}":
        out.append(f"the run ended with the exit code of a misapplied pin ({PIN_MISAPPLIED})")
    return out


def run_checked(np: int, exe: str, args: List[str], pin, timeout: int = 20, mpi: str = "openmpi",
                mpirun_args: Optional[List[str]] = None, extra_env: Optional[dict] = None,
                cwd: Optional[str] = None, keep_as: Optional[Tuple[str, str]] = None) -> Replay:
    """run_pinned with the tracer preloaded when it is built: the tracer applies the pins as the
    replay library does and records the run with the original envelopes into a directory of
    its own, which judge_replay reads and removes. Without the tracer, the replay library runs
    and the replay is not judged. With STRETTO_KEEP_REPLAYS set to a directory, a replay whose
    trace is not, or not known to be, a run of the program is kept there, its pins, result,
    verdict and stderr next to the trace, for a closer look. keep_as, (campaign, name), keeps
    the replay's trace with KEEP_TRACES set (stretto.keep), whatever its verdict, with the same
    note and the run's stdout in replay.txt."""
    tracer = tracer_lib(mpi)
    if not os.path.exists(tracer):
        r = run_pinned(np, exe, args, pin, timeout, mpi, mpirun_args, extra_env, cwd)
        res = classify_rc(r.returncode, r.stderr, r.elapsed, timeout)
        return Replay(r, res, misapplied=misapplied_pins(r, res))
    d = tempfile.mkdtemp(prefix="stretto-replay-")
    try:
        r = run_pinned(np, exe, args, pin, timeout, mpi, mpirun_args, {**(extra_env or {}), "STRETTO_TRACE_DIR": d},
                       cwd, preload=tracer)
        res = classify_rc(r.returncode, r.stderr, r.elapsed, timeout)
        seen: Set[Tuple[str, int, int]] = set()
        mis: List[str] = []
        ended: Set[Tuple[str, int, int]] = set()
        valid, why = judge_replay(d, res, pin, seen, mis, ended)

        def note() -> str:
            return (f"exe: {exe} {' '.join(args)}\nnp: {np}\nresult: {res}\nvalid: {valid}\nwhy: {why}\npins:\n"
                    + "\n".join([pin] if isinstance(pin, str) else (pin or [])) + f"\n--- stderr\n{r.stderr}")

        keep = os.environ.get("STRETTO_KEEP_REPLAYS")
        if keep and valid != "True":
            os.makedirs(keep, exist_ok=True)
            dest = tempfile.mkdtemp(prefix="replay-", dir=keep)
            for f in os.listdir(d):
                shutil.copy(os.path.join(d, f), dest)
            with open(os.path.join(dest, "replay.txt"), "w") as fh:
                fh.write(note())
        if keep_as is not None and keep_mod.enabled():
            keep_mod.keep(d, keep_as[0], keep_as[1], notes={"replay.txt": note() + f"\n--- stdout\n{r.stdout}"})
        return Replay(r, res, valid, why, seen, misapplied_pins(r, res, mis),
                      None if why.startswith("trace unreadable") else ended)
    finally:
        shutil.rmtree(d, ignore_errors=True)


PIN_INLINE_MAX = 16 * 1024


def pin_assignment(assignment: str, d: str) -> str:
    """The assignment as passed to the ranks: unchanged if short, else NAME=@file with the
    value written to a file in d. Linux limits one environment string to 128 KiB, and the
    horizon of a late alternative in a wildcard-heavy run pins tens of thousands of receives."""
    name, _, value = assignment.partition("=")
    if len(value) <= PIN_INLINE_MAX:
        return assignment
    path = os.path.join(d, name.lower())
    with open(path, "w") as fh:
        fh.write(value)
    return f"{name}=@{path}"


def confirm(rep: Report, np: int, exe: str, args: List[str] = (), timeout: int = 20, mpi: str = "openmpi",
            only_errors: bool = True, t=None, keep_as: Optional[Tuple[str, str]] = None) -> List[Confirmation]:
    """t, the trace, gives the horizon pins and the control reruns; without it no finding can be
    confirmed. A finding whose consequence holds in every execution with the change is replayed
    with the horizon pins (every wildcard operation of the alternative's horizon pinned); one
    whose consequence holds in some execution with the pins of the explorer's witness. Replays
    and controls run under the tracer when it is built (run_checked), which judges each as a run
    of the program without its pins; Confirmation.strict is the verdict that needs it.
    keep_as, (campaign, prefix), keeps the trace of each replay and control with KEEP_TRACES set
    (stretto.keep), named <prefix>__<class>-<level>-<pin> and __replay or __control after it."""
    out = []
    seen = set()
    controls: dict = {}

    def kept(f: Finding, pin: Optional[str], role: str) -> Optional[Tuple[str, str]]:
        return None if keep_as is None else (keep_as[0], f"{keep_as[1]}__{f.cls}-{f.level}-{pin_name(pin)}__{role}")

    def control(pins: List[str], name: Optional[Tuple[str, str]]) -> Replay:
        if t is None or not pins:
            return Replay(None, "")
        key = tuple(pins)
        if key not in controls:
            controls[key] = run_checked(np, exe, list(args), pins, timeout, mpi, keep_as=name)
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
        hp = _horizon(t, f) if t is not None and f.certainty != "some" else None
        required = None
        if hp is not None:
            pins, ctl_pins, required = hp
        elif pin is not None:
            pins = with_identity(t, [pin]) if t is not None else [pin]
            cp = control_pin(t, pin) if t is not None else None
            ctl_pins = [cp] if cp else []
        else:
            out.append(Confirmation(f, "", "NO-PIN", "n/a", strict="n/a"))
            continue
        key = (tuple(pins), f.cls)
        if key in seen:
            continue
        seen.add(key)
        rp = run_checked(np, exe, list(args), pins, timeout, mpi, keep_as=kept(f, pin, "replay"))
        r, res = rp.proc, rp.result
        applied = all(pins_applied(x, r.stderr) for x in pins)
        want = expected_outcomes(f)
        if f.cls == "1a" and want:
            # a truncation ends the run inside the receive that takes the longer message, which
            # therefore reports no match: it is shown applied, or pending where the run stopped
            changed = changed_pin(t, f) if t is not None and f.level == "L1" else None
            matched = res in want and truncation_shown(changed, pins, r.stderr, required,
                                                       _reported(r.stderr, pins) | rp.seen, rp.ended)
        else:
            matched = all_matched(pins, r.stderr, required, rp.seen)
        ctl = control(ctl_pins, kept(f, pin, "control")) if want and res in want and matched else Replay(None, "")
        # a pin on another operation than the traced one: the replay diverged before it, and its
        # outcome says nothing about the alternative, whichever it is
        mis = rp.misapplied + ctl.misapplied
        if not want:
            verdict = "n/a"
        elif mis:
            verdict = "inconclusive"
        elif res not in want or not matched:
            verdict = "not-confirmed"
        else:
            verdict = "confirmed" if ctl.result == "FINISHED" else "inconclusive"
        out.append(Confirmation(f, " ".join(pins), res, verdict, r.stderr[-300:], applied, matched, ctl.result,
                                rp.valid, rp.why, ctl.valid, ctl.why, strict_verdict(verdict, rp.valid),
                                "; ".join(mis)[:300]))
    return out
