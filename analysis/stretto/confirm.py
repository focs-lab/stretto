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
# trace_misapplied). A token without '@', as pins were written before, is not checked. A probe
# pin's identity has a fourth field since 29 Oct 2026, the probe's ordinal among the rank's
# wildcard probes of its communicator and tag argument that found a message (envelope_ordinal):
# the library pins by it, since two polling loops of other tags at one call site interleave
# differently in a replay, and the pidx then names a probe of the other loop.

PIN_MISAPPLIED = 86      # the exit code the replay library ends a run with (replay/pin.h STRETTO_PIN_MISAPPLIED)
GATE_TIMEOUT = 87        # and the one of an order gate that did not open in time (STRETTO_PIN_GATE_TIMEOUT)

Ident = Tuple[Optional[int], Optional[int], Optional[int]]     # (communicator, tag, site); None: unchecked


@dataclass(frozen=True)
class PinToken:
    kind: str                 # "recv" (STRETTO_PIN), "probe" (STRETTO_PIN_PROBE) or "flip" (STRETTO_PIN_FLIP)
    rank: int
    idx: int                  # widx, pidx, or a flip's poll key (Trace.poll_key)
    src: int                  # the sender's world rank; -1 a flag 0, -2 a test's request (flips, replay/pin.h)
    tag: Optional[int]        # the tag an ANY_TAG operation takes, None if the pin names none
    ident: Optional[Ident]    # the traced operation's identity, None in a token without it
    # the identity's fourth field: of a probe pin, the probe's ordinal among the rank's wildcard
    # probes of its envelope that found a message (envelope_ordinal), which the replay library
    # pins by; of a flip pin, the number of those found before the poll
    n: Optional[int] = None
    # a chain pin (STRETTO_PIN_CHAIN, kind "crecv"): the chain's logical name, idx being the
    # receive's ordinal on it, and what follows '=': the message the receive must take and the
    # happens-before it must have ("sproc:schain:sidx|chain:n;..."), for the harness alone
    chain: Optional[str] = None
    want: Optional[str] = None

    @property
    def key(self) -> tuple:
        """The pin as the report lines and the replay's trace show it taken: (kind, rank, idx),
        or ("crecv", rank, chain, idx) for a chain pin."""
        return ("crecv", self.rank, self.chain, self.idx) if self.kind == "crecv" else (self.kind, self.rank, self.idx)


def _parse_ident(text: str) -> Ident:
    f = (text.split(":") + ["x", "x", "x"])[:3]
    return (None if f[0] == "x" else int(f[0]), None if f[1] == "x" else int(f[1]),
            None if f[2] == "x" else int(f[2], 16))


def _parse_n(text: str) -> Optional[int]:
    f = text.split(":")
    return int(f[3]) if len(f) > 3 and f[3] != "x" else None


def _ident_text(ident: Ident, n: Optional[int] = None) -> str:
    c, g, s = ident
    text = f"{'x' if c is None else c}:{'x' if g is None else g}"
    if n is not None:
        return text + f":{'x' if s is None else format(s, 'x')}:{n}"
    return text + ("" if s is None else f":{s:x}")


def _pin_kind(name: str) -> str:
    return {"STRETTO_PIN_PROBE": "probe", "STRETTO_PIN_FLIP": "flip", "STRETTO_PIN_CHAIN": "crecv",
            "STRETTO_PIN_ORDER": "order", "STRETTO_PIN_LOCK": "order"}.get(name, "recv")


def pin_tokens(pins) -> List[PinToken]:
    """The tokens of "STRETTO_PIN=...", "STRETTO_PIN_PROBE=...", "STRETTO_PIN_FLIP=..." and
    "STRETTO_PIN_CHAIN=..." assignments (one, a list, or None), in both formats. The order
    gates (STRETTO_PIN_ORDER) pin no operation and give no token."""
    out = []
    for a in ([pins] if isinstance(pins, str) else (pins or [])):
        name, _, spec = a.partition("=")
        kind = _pin_kind(name)
        if kind == "order":
            continue
        for tok in re.split(r"[,\s]+", spec):
            if kind == "crecv":
                main, _, want = tok.partition("=")
                head, at, ident = main.partition("@")
                parts = head.split(":")
                if len(parts) < 4:
                    continue
                out.append(PinToken(kind, int(parts[0]), int(parts[2]), int(parts[3]),
                                    int(parts[4]) if len(parts) > 4 else None, _parse_ident(ident) if at else None,
                                    None, parts[1], want or None))
                continue
            head, at, ident = tok.partition("@")
            parts = head.split(":")
            if len(parts) < 3:
                continue
            out.append(PinToken(kind, int(parts[0]), int(parts[1]), int(parts[2]),
                                int(parts[3]) if len(parts) > 3 else None, _parse_ident(ident) if at else None,
                                _parse_n(ident) if at else None))
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
    communicator records the loader keeps; the loader's own mapping run backwards. The records
    of a rank whose threads the loader split into chains are taken together, in the rank's file
    order. Cached on t."""
    cache = t.__dict__.get("_stretto_local_comms")
    if cache is not None:
        return cache
    from . import trace as trace_mod
    by_proc: Dict[int, list] = {}
    for rank, recs in t.ranks.items():
        proc = t.proc.get(rank, rank)
        by_proc.setdefault(proc, []).extend((t.fpos.get((rank, rec.idx), rec.idx), rec.raw) for rec in recs
                                            if rec.op == "comm" and rec.raw is not None)
    folded = {proc: [raw for _, raw in sorted(v, key=lambda e: e[0])] for proc, v in by_proc.items()}
    scratch = trace_mod.Trace()
    scratch.ranks = {proc: [] for proc in folded}
    all_ranks = list(range(t.np)) if t.np else sorted(folded)
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


def _site_of(t, x) -> Optional[Tuple[str, int]]:
    """The call site of operation x (Trace.site_of, which looks a chain's sites up under its
    process)."""
    return t.site_of(x.rank, x.idx)


def identity(t, x) -> Ident:
    """The identity of receive or probe x of trace t: its communicator's local id on its rank,
    its tag argument (ANY_TAG is -1) and its call site's offset, None where the trace lacks it."""
    site = _site_of(t, x)
    return _local_comms(t).get((getattr(x, "proc", x.rank), x.comm)), x.tag, (site[1] if site else None)


def _envelopes(t) -> Dict:
    """Per probe of trace t that found a message as a wildcard probe (one with a pidx): its
    envelope, (rank, the communicator's local id, the tag argument), and its ordinal among the
    probes of that envelope, as the replay library counts them (replay/pin.h, STRETTO_PIN_PROBE's
    n); and the reverse map. Cached on t."""
    cache = t.__dict__.get("_stretto_envelopes")
    if cache is None:
        of, at, count = {}, {}, Counter()
        for q in sorted((q for q in t.probes.values() if q.pidx is not None),
                        key=lambda q: (q.proc, t.fpos.get((q.rank, q.idx), q.idx))):
            raw = t.ranks[q.rank][q.idx].raw if q.idx < len(t.ranks.get(q.rank, ())) else None
            local = raw.get("comm") if raw is not None else None
            if local is None:
                continue
            env = (q.proc, local, q.tag)
            of[q.key] = (env, count[env])
            at[env + (count[env],)] = q
            count[env] += 1
        cache = t.__dict__["_stretto_envelopes"] = (of, at)
    return cache


def envelope_ordinal(t, q) -> Optional[int]:
    """The ordinal of probe q of trace t among the wildcard probes of its rank, communicator and
    tag argument that found a message (the replay library's n), None if q has none or its
    communicator's local id is not the one its identity names."""
    e = _envelopes(t)[0].get(q.key)
    if e is None or identity(t, q)[0] != e[0][1]:
        return None
    return e[1]


def probe_at_envelope(t, rank: int, comm: int, tag: int, n: int):
    """The probe of rank in trace t that is the n-th wildcard probe of communicator comm (local
    id) and tag argument tag to find a message, or None: where a pin with an envelope ordinal
    falls in a replay's own trace."""
    return _envelopes(t)[1].get((rank, comm, tag, n))


def ident_text(t, x) -> str:
    """The identity of receive or probe x of trace t as a pin writes it after '@'; a probe's
    carries its envelope ordinal as a fourth field when it has one."""
    n = envelope_ordinal(t, x) if hasattr(x, "pidx") else None
    return _ident_text(identity(t, x), n)


def pin_token(t, x, src: int, tag: Optional[int] = None) -> str:
    """The pin of receive or probe x of trace t to world rank src (and to tag, for an ANY_TAG
    operation), with x's identity."""
    idx = x.pidx if hasattr(x, "pidx") else x.widx
    return f"{x.proc}:{idx}:{src}" + (f":{tag}" if x.tag == ANY and tag is not None else "") + "@" + ident_text(t, x)


def _op_at(t, kind: str, rank: int, idx: int):
    """The receive with widx idx, or the probe with pidx idx, of rank in trace t, or None. The
    index is built once per trace: a replay's trace can hold millions of receives and its
    horizon pin tens of thousands of them."""
    ix = t.__dict__.get("_stretto_ordinals")
    if ix is None:
        ix = {("recv", r.proc, r.widx): r for r in t.recvs.values() if r.widx is not None}
        ix.update({("probe", q.proc, q.pidx): q for q in t.probes.values() if q.pidx is not None})
        t.__dict__["_stretto_ordinals"] = ix
    return ix.get((kind, rank, idx))


def _op_of(t, k: PinToken):
    """The operation pin token k falls on in trace t (a replay's own): a probe pin with an
    envelope ordinal on that envelope's probe at it, any other pin on the operation of its
    ordinal."""
    if k.kind == "probe" and k.n is not None and k.ident is not None and k.ident[0] is not None and k.ident[1] is not None:
        return probe_at_envelope(t, k.rank, k.ident[0], k.ident[1], k.n)
    if k.kind == "crecv":
        return _chain_ops(t)[1].get((k.rank, k.chain, "r", k.idx))
    return _op_at(t, k.kind, k.rank, k.idx)


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
            toks.append(tok if x is None else tok + "@" + ident_text(t, x))
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


def _expected(pins) -> Dict[tuple, Optional[Ident]]:
    return {k.key: k.ident for k in pin_tokens(pins)}


_LINE = re.compile(r"\[stretto-pin\] rank (\d+): (matched widx|applied widx|applied probe pidx|applied unflip pidx"
                   r"|applied flip key|matched flip key)=(\d+)\b([^\n]*)")
# what each line says, and the pin it shows taken: a receive pin when its receive completed, a
# probe pin when the probe found its message or returned false as pinned (unflip), a flip pin
# when the forced call returned true (matched flip) or false as pinned (applied flip ... flag=0)
_WHAT = {"matched widx": "matched", "applied widx": "applied", "applied probe pidx": "probe",
         "applied unflip pidx": "unflip", "applied flip key": "flip-applied", "matched flip key": "flip"}
_LINE_ID = re.compile(r"\bid=(-?[0-9]+|x):(-?[0-9]+|x):([0-9a-f]+|x)")
_MISAPPLIED = re.compile(r"\[stretto-pin\] rank \d+: (?:probe |flip )?pin (?:widx|pidx|key)=\d+ misapplied[^\n]*"
                         r"|\[stretto-pin\] rank \d+ chain \S+: (?:pin ridx=\d+ misapplied|lock pin a#\d+ misapplied|gate timed out)[^\n]*")
# a chain pin's lines (replay/pin.h, STRETTO_PIN_CHAIN): applied when its receive is posted,
# matched when it completes; which message it took only the replay's own trace says
_CLINE = re.compile(r"\[stretto-pin\] rank (\d+) chain (\S+): (matched|applied) ridx=(\d+)\b([^\n]*)")


def _lines(stderr: str):
    """(what, rank, idx, identity or None) of every pin report line: what is "matched",
    "applied" (a receive) or "probe" (a probe that found its message); a chain pin's lines are
    "cmatched" and "capplied", with (rank, chain) as rank. Read once per run: a horizon can pin
    tens of thousands of receives, and a search of the whole output per pin is quadratic."""
    for m in _LINE.finditer(stderr):
        i = _LINE_ID.search(m.group(4))
        what = _WHAT[m.group(2)]
        if what == "flip-applied" and " flag=0" in m.group(4):
            what = "flip"          # the control's flip: the call returned false as pinned
        yield what, int(m.group(1)), int(m.group(3)), _parse_ident(":".join(i.groups())) if i else None
    if "chain" in stderr:
        for m in _CLINE.finditer(stderr):
            i = _LINE_ID.search(m.group(5))
            yield "c" + m.group(3), (int(m.group(1)), m.group(2)), int(m.group(4)), _parse_ident(":".join(i.groups())) if i else None


def _line_key(what: str, rank, idx: int) -> tuple:
    """The pin a report line is about."""
    if what in ("cmatched", "capplied"):
        return ("crecv", rank[0], rank[1], idx)
    return ({"flip": "flip", "flip-applied": "flip"}.get(what) or ("probe" if what in ("probe", "unflip") else "recv"), rank, idx)


def pins_misapplied(stderr: str) -> List[str]:
    """The replay library's reports of a pin that fell on an operation of another identity, and
    of an order gate that never opened (the replay diverged before it)."""
    return [m.group(0) for m in _MISAPPLIED.finditer(stderr)]


def pins_applied(pin: str, stderr: str) -> bool:
    """Did the replay library report every pin of the spec as applied, on an operation of the
    pin's identity? It prints '[stretto-pin] rank R: applied widx=W ... id=C:G:X' (or
    'applied probe pidx=P ...', printed when the probe found its message, or 'rank R chain C:
    applied ridx=K ...') for each. Order gates apply no pin: always true."""
    if _pin_kind(pin.partition("=")[0]) == "order":
        return True
    want = _expected(pin)
    seen = {}
    for what, rank, idx, ident in _lines(stderr):
        if what not in ("matched", "cmatched"):
            seen[_line_key(what, rank, idx)] = ident
    return bool(want) and all(k in seen and _agrees(e, seen[k]) for k, e in want.items())


def _reported(stderr: str, pins=None) -> Set[tuple]:
    """The pins a replay reported as taken: ("recv", rank, widx) for each 'matched widx=W',
    ("probe", rank, pidx) for each 'applied probe pidx=P', on an operation whose identity fits
    the pin of pins that names it (a line without an identity fits no pin that has one). A chain
    pin is taken only when the replay's own trace shows its receive taking the predicted
    message (trace_matches): its report line names the source, which two messages of one
    channel share."""
    want = _expected(pins) if pins else {}
    out = set()
    for what, rank, idx, ident in _lines(stderr):
        if what in ("applied", "flip-applied", "capplied", "cmatched"):
            continue
        key = _line_key(what, rank, idx)
        if _agrees(want.get(key), ident):
            out.add(key)
    return out


def _taken(pin: str, reported: Set[tuple]) -> bool:
    name, _, spec = pin.partition("=")
    kind = _pin_kind(name)
    if kind == "order":
        return True
    if kind == "crecv":
        return all(k.key in reported for k in pin_tokens(pin) if not (k.want or "").startswith("-|"))
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
            src = t.sends[x.seen].proc if x.seen is not None else None
        else:
            src = x.msrc
        if src is not None:
            toks.append(pin_token(t, x, src))
    return f"{name}={','.join(toks)}" if toks else None


# ------------------------------------------------------------------ several threads
#
# docs/threads.md, sections 4 and 11. In a process whose threads call MPI the rank's ordinals of
# its wildcard operations (widx, pidx) are taken in the order the threads happen to reach them,
# which a replay does not reproduce. A trace with several chains is therefore replayed with chain
# pins (STRETTO_PIN_CHAIN): every receive of the horizon, wildcard or not, is named by its
# chain's logical name and its ordinal among the chain's receives, pinned to the sender's process
# where it is a wildcard, and carries the message it must take (the sender's process, chain and
# send ordinal) and the happens-before it has in the trace (per other chain of its process, how
# many of that chain's operations precede it). Order gates (STRETTO_PIN_ORDER) realise the
# physical order the alternative needs (section 3.2): for every pair of concurrent operations of
# one process in the horizon that could take or report one message (two receives or probes with
# overlapping envelopes, two sends to one process on one communicator), the later one in a
# topological order of the alternative's constraint graph waits until the earlier one is
# posted. Only the pairs the alternative orders otherwise than the run are gated; the control
# gates them in the run's issue order. Only the tracer applies
# chain pins and gates (it names the chains); the replay's own trace then says which message
# each receive took (P-T4: the tracer's issue order) and whether its synchronisation order is
# the trace's (sync_divergence), without which the chains' ordinals need not name the same
# work: a pool of threads may hand a request to another thread in the replay.

def chain_label(t, ch) -> str:
    """The logical name of chain ch, as the tracer names it ("0" for the thread that initialised
    MPI, "c.k" for the k-th thread chain c created, "c/p.j" for thread j of chain c's p-th
    parallel region); "0" for a rank that has one chain."""
    return t.chain_name.get(ch, "0")


def _chain_ops(t):
    """(of, at, pos) for trace t: of[(kind, key)] = (process, chain name, ordinal) of every
    receive ("r", Mrecv/Imrecv left out), send ("s") and probe that found a message ("p"), the
    ordinals the replay library counts (replay/pin.h); at the reverse map, keyed (process, chain
    name, kind, ordinal); pos[chain] the sorted record indexes of the chain's operations of the
    three kinds. Cached on t."""
    cache = t.__dict__.get("_stretto_chain_ops")
    if cache is not None:
        return cache
    probes: Dict[int, list] = {}
    for q in t.probes.values():
        if q.flag and q.completed:
            probes.setdefault(q.rank, []).append(q)
    of, at, pos = {}, {}, {}
    for ch in t.ranks:
        proc, name = t.proc.get(ch, ch), chain_label(t, ch)
        lists = {"r": sorted((r for r in t.recvs_of(ch) if not r.mrecv), key=lambda r: r.idx),
                 "s": sorted(t.sends_of(ch), key=lambda s: s.idx),
                 "p": sorted(probes.get(ch, ()), key=lambda q: q.idx)}
        idxs = []
        for kind, lst in lists.items():
            for k, x in enumerate(lst):
                of[(kind, x.key)] = (proc, name, k)
                at[(proc, name, kind, k)] = x
                idxs.append(x.idx)
        pos[ch] = sorted(idxs)
    cache = t.__dict__["_stretto_chain_ops"] = (of, at, pos)
    return cache


def _hb_text(t, x) -> str:
    """The happens-before of operation x of trace t on the other chains of its process: per
    chain, by name, how many of its receives, sends and probes that found a message precede x,
    as "name:n;..." ("" for a process of one chain)."""
    proc = t.proc.get(x.rank, x.rank)
    if not t.multi or proc not in t.chains or t.hb is None:
        return ""
    from bisect import bisect_right
    pos = _chain_ops(t)[2]
    parts = []
    for c in t.chains[proc]:
        if c != x.rank:
            parts.append(f"{chain_label(t, c)}:{bisect_right(pos.get(c, []), t.hb.last_before(x.rank, x.idx, c))}")
    return ";".join(sorted(parts))


def chain_pin_token(t, r, s, posted_only: bool = False) -> str:
    """The chain pin of receive r of trace t to send s: "rank:chain:ridx:src[:tag]@identity=
    sproc:schain:sidx|happens-before" (replay/pin.h; the part after '=' is the harness's).
    posted_only: r completes beyond the horizon, where the run need not take s ("-" in place of
    the message: the pin steers the posting and is not required)."""
    of = _chain_ops(t)[0]
    proc, name, k = of[("r", r.key)]
    sp, sn, sk = of[("s", s.key)]
    src = s.proc if r.src == ANY else r.src
    tag = f":{s.tag}" if r.tag == ANY else ""
    want = "-" if posted_only else f"{sp}:{sn}:{sk}"
    return f"{proc}:{name}:{k}:{src}{tag}@{ident_text(t, r)}={want}|{_hb_text(t, r)}"


def _topo_positions(t, g) -> Dict:
    """A topological order of constraint graph g as node -> position: Kahn's algorithm, taking
    among the nodes that are free the one earliest in its rank's file, so that operations whose
    order the alternative leaves free keep the run's."""
    import heapq
    indeg = {n: 0 for n in g.nodes}
    for a, outs in g.succ.items():
        if a in indeg:
            for b in outs:
                if b in indeg:
                    indeg[b] += 1
    kinds = {"i": 0, "m": 1, "c": 2}

    def prio(n):
        if n[0] < 0:
            return (-1, 0)
        return (t.fpos.get((n[0], n[1]), n[1]), kinds.get(n[2], 3))

    heap = [(prio(n), i, n) for i, n in enumerate(g.nodes) if indeg[n] == 0]
    heapq.heapify(heap)
    order = {n: i for i, n in enumerate(g.nodes)}
    pos: Dict = {}
    while heap:
        _, _, n = heapq.heappop(heap)
        if n in pos:
            continue
        pos[n] = len(pos)
        for b in g.succ.get(n, ()):
            if b in indeg:
                indeg[b] -= 1
                if indeg[b] == 0:
                    heapq.heappush(heap, (prio(b), order[b], b))
    return pos


def _conflict(k1: str, a, k2: str, b) -> bool:
    """Could operations a and b of one process (kinds "r", "s", "p") take or report one
    message, so that their order decides a match?"""
    if (k1 == "s") != (k2 == "s"):
        return False
    if k1 == "s":
        return a.dst == b.dst and a.comm == b.comm
    return a.comm == b.comm and (a.src == ANY or b.src == ANY or a.src == b.src) \
        and (a.tag == ANY or b.tag == ANY or a.tag == b.tag)


GATE_SCAN = 64     # per operation and other chain, how far back the latest conflicting operation is looked for


def order_gates(t, cut, position, keep=frozenset(), pairs: Optional[list] = None) -> List[str]:
    """The order gates of trace t over the horizon cut (STRETTO_PIN_ORDER tokens): for every
    operation x of a process of several chains and every other chain, the latest operation y of
    that chain before x in position's order that conflicts with x (_conflict), when the two are
    concurrent: y before x. position(kind, op) gives the order; keep names receives kept although
    the cut is at them (the changed receive of a steal). An operation beyond the horizon waits
    in the same way for the operations of the horizon, and nothing waits for it: the replay must
    not let a receive the horizon leaves out take a message of the horizon (the receive a steal
    leaves without its message), and what the program does beyond the horizon the trace does not
    say. pairs, if given, receives ((kind, y), (kind, x)) of each gate y < x, in the order of the
    tokens."""
    from .chains import concurrent
    of = _chain_ops(t)[0]
    out = []
    for proc, chs in sorted(t.chains.items()):
        if len(chs) < 2:
            continue
        evs = []
        for ch in chs:
            evs += [("r", r) for r in t.recvs_of(ch) if not r.mrecv]
            evs += [("s", s) for s in t.sends_of(ch)]
        evs += [("p", q) for q in t.probes.values() if q.rank in chs and q.flag and q.completed]
        evs.sort(key=lambda e: position(*e))
        seen: Dict[int, list] = {ch: [] for ch in chs}
        done: Set[tuple] = set()
        for kind, x in evs:
            inside = x.idx < cut[x.rank] or x.key in keep
            for c in chs:
                if c == x.rank:
                    continue
                for k2, y in reversed(seen[c][-GATE_SCAN:]):
                    if _conflict(kind, x, k2, y):
                        if concurrent(t, y.rank, y.idx, x.rank, x.idx) and (inside or (x.rank, k2, y.key) not in done):
                            done.add((x.rank, k2, y.key))
                            if pairs is not None:
                                pairs.append(((k2, y), (kind, x)))
                            out.append(f"{proc}:{of[(k2, y.key)][1]}#{k2}#{of[(k2, y.key)][2]}<"
                                       f"{of[(kind, x.key)][1]}#{kind}#{of[(kind, x.key)][2]}")
                        break
            if inside:
                seen[x.rank].append((kind, x))
    return out


def _probe_pinnable(t) -> Set[int]:
    """The processes whose wildcard probes the rank ordinal (pidx) names in a replay too: those
    of one chain, or whose wildcard probes that found a message are all on one chain."""
    on: Dict[int, set] = {}
    for q in t.probes.values():
        if q.pidx is not None:
            on.setdefault(q.proc, set()).add(q.rank)
    return {p for p, chs in on.items() if len(chs) == 1}


def _envelope_pinnable(t) -> Set:
    """The wildcard probes that found a message whose envelope ordinal (envelope_ordinal, the
    replay library's n) names them in a replay too: those of an envelope whose probing threads
    took turns, each probing in one stretch that no other thread's probe of the envelope
    interrupted (HDF5 Subfiling's I/O concentrators, one per file, end on an atomic flag that
    happens-before does not see). A replay that does not follow the turns misapplies a pin."""
    by: Dict = {}
    for q in t.probes.values():
        e = _envelopes(t)[0].get(q.key)
        if q.pidx is not None and e is not None and envelope_ordinal(t, q) is not None:
            by.setdefault(e[0], []).append((e[1], q))
    out = set()
    for qs in by.values():
        qs.sort(key=lambda p: p[0])
        stretches = 1 + sum(1 for (_, a), (_, b) in zip(qs, qs[1:]) if a.rank != b.rank)
        if stretches == len({q.rank for _, q in qs}):
            out |= {q.key for _, q in qs}
    return out


def _chain_specs(t, M, chk, keep, B, probe_alt):
    """horizon_pins for a trace of several threads (the section's comment): (alternative,
    control, required) over the horizon of check verdict chk, M the alternative matching."""
    from .chains import physical_key
    from .feasible import without
    from .matching import build_graph
    cut = chk.cut
    of = _chain_ops(t)[0]
    Md = without(M, chk.deprived)
    g = build_graph(t, Md, cut, B, (), False, probe_alt, chk.issued)
    pos = _topo_positions(t, g)
    far = 1 << 60

    def node(kind, x):
        return (x.rank, x.idx, "c") if kind == "p" else x.issue_node()

    def alt_pos(kind, x):
        return (pos.get(node(kind, x), far), physical_key(t, x))

    def ctl_pos(kind, x):
        return physical_key(t, x)

    def forced(a, b) -> bool:
        """Does the alternative's constraint graph order operation a before b: a path, or b
        beyond it (the horizon goes first)? Only nodes placed before b in its topological order
        can lie on a path."""
        src, dst = node(*a), node(*b)
        if src in pos and dst not in pos:
            return True          # b lies beyond the alternative's graph (a receive it leaves without its message)
        if src not in pos or pos[src] >= pos[dst]:
            return False
        seen, stack = {src}, [src]
        while stack:
            n = stack.pop()
            for m in g.succ.get(n, ()):
                if m == dst:
                    return True
                if m not in seen and pos.get(m, far) < pos[dst]:
                    seen.add(m)
                    stack.append(m)
        return False

    # Without a sync log, only the pairs the alternative orders otherwise than the run are gated,
    # in its order in the replay and in the run's in the control; the rest are left free in both
    # (section 4: "other pairs are left free"): a gate on a pair both orders agree on fails where
    # the program shares out its work among threads differently in each run (a pool whose threads
    # take other requests posts other numbers of operations). With one, the lock gates fix how the
    # work is shared out, and every pair is gated (section 14): in the control in the run's order;
    # in the replay in the alternative's order where its constraint graph forces it and in the
    # run's elsewhere, since one topological order also reorders pairs nothing orders, among them
    # pairs whose order synchronisation the trace does not see decides (HDF5's I/O
    # concentrators, one after another, end on an atomic flag)
    # a changed receive, or one rule 6 left without its message, cuts the synchronisation after it
    # on the chain that completed it, which is another than the posting chain where a thread
    # completed another's request (a hand-off, section 15: rule 1 cuts the completing chain)
    stops = {(ch, ci + 1) for ch, ci in (r.complete_at() for r in t.recvs.values()
             if r.key in chk.deprived or (r.key in Md and Md.get(r.key) != t.observed.get(r.key)))}

    def done_inside(r) -> bool:
        """Does receive r complete inside the horizon, on the chain that completed it?"""
        ch, ci = r.complete_at()
        return r.completed and ci < cut[ch]
    locks = lock_gates(t, cut, stops)
    pa: list = []
    pc: list = []
    ga, gc = order_gates(t, cut, alt_pos, keep, pa), order_gates(t, cut, ctl_pos, keep, pc)
    if locks is None:
        alt_gates = [g for g in ga if g not in set(gc)]
        ctl_gates = [g for g in gc if g not in set(ga)]
    else:
        alt_gates = [x for x, (a, b) in zip(ga, pa) if forced(a, b)]
        have = set(alt_gates)
        alt_gates += [x for x, (a, b) in zip(gc, pc) if x not in have and not forced(b, a)]
        ctl_gates = gc
    pinnable = _probe_pinnable(t)
    # with the lock order fixed, a probe is also pinned by its envelope ordinal where the probes of
    # its envelope follow one another
    env_ok = _envelope_pinnable(t) if locks is not None else set()
    required: Set[tuple] = set()

    def specs(alternative: bool) -> List[str]:
        rtok, ptok = [], []
        for r in sorted(t.recvs.values(), key=lambda r: (r.proc, chain_label(t, r.rank), r.idx)):
            if r.mrecv or not (r.idx < cut[r.rank] or r.key in keep) or (alternative and r.key in chk.deprived):
                continue
            sk = Md.get(r.key) if alternative else t.observed.get(r.key)
            if sk is None:
                continue
            # with lock gates (section 14) a receive that completes beyond the horizon is not
            # required to take its message in the control either
            posted_only = locks is not None and not done_inside(r)
            rtok.append(chain_pin_token(t, r, t.sends[sk], posted_only))
            if alternative and done_inside(r):
                required.add(("crecv",) + of[("r", r.key)])
        for q in sorted(t.probes.values(), key=lambda q: (q.proc, q.idx)):
            if q.pidx is None or not q.flag or q.idx >= cut[q.rank] or (q.proc not in pinnable and q.key not in env_ok) \
                    or q.seen is None:
                continue
            m = t.sends[q.seen]
            ptok.append(pin_token(t, q, m.proc, m.tag))
            if alternative:
                required.add(("probe", q.proc, q.pidx))
        gates = alt_gates if alternative else ctl_gates
        out = []
        if rtok:
            out.append("STRETTO_PIN_CHAIN=" + ",".join(rtok))
        if ptok:
            out.append("STRETTO_PIN_PROBE=" + ",".join(ptok))
        if gates:
            out.append("STRETTO_PIN_ORDER=" + ",".join(gates))
        if locks:
            out.append("STRETTO_PIN_LOCK=" + ",".join(locks))
        return out

    return specs(True), specs(False), required


# ------------------------------------------------------------------ lock order
#
# docs/threads.md, section 14. Chain pins name a chain's k-th receive, which names the same
# piece of work only if the chains share out their work as in the trace: a pool of threads that
# takes requests from a queue under a mutex, or OpenMP tasks that take roles in the order they
# take a lock, does so in the order its threads take the locks. A trace recorded with
# STRETTO_SYNC_ORDER=1 lists each rank's lock acquisitions in order (sync-<r>.txt, the tracer's
# section "lock order"), and the replay and its control then gate every acquisition of the
# horizon in that order (STRETTO_PIN_LOCK, replay/pin.h), so that every chain does the work it
# did in the trace up to its cut; with the work so fixed, every pair of concurrent operations
# that could take one message is gated, in the alternative's order in the replay and in the
# run's in the control (order_gates), and not only the pairs the two orders disagree on.

def read_sync(tracedir: str) -> Optional[Dict[int, List[Tuple[int, str, int, int, str]]]]:
    """The sync logs of the trace in tracedir: per rank, (lock, chain name, ordinal, seq, flag)
    of every attempt to take a lock, in file order (flag "" an acquisition, "f" a try that
    failed, "t" a condition wait that timed out, "l" a thread that lost a single construct);
    None when the run wrote none."""
    out: Dict[int, list] = {}
    try:
        names = [f for f in os.listdir(tracedir) if f.startswith("sync-") and f.endswith(".txt")]
    except OSError:
        return None
    for f in names:
        rows = []
        with open(os.path.join(tracedir, f)) as fh:
            for line in fh:
                p = line.split()
                if len(p) < 4 or line.startswith("#"):
                    continue
                try:
                    rows.append((int(p[0]), p[1], int(p[2]), int(p[3]), p[4] if len(p) > 4 else ""))
                except ValueError:
                    break                  # a line cut off by the end of the run
        out[int(f[len("sync-"):-len(".txt")])] = rows
    return out or None


def with_sync(t, tracedir: str):
    """Trace t, loaded from tracedir, with its sync logs (read_sync) for horizon_pins, and the
    sequence number of each record of the ranks that have one (the loader keeps no seq), read
    again from the rank's file as the loader folds it."""
    import json
    from .chains import THREAD_SIDE
    from .trace import _fold_begins
    sync = read_sync(tracedir)
    seq: Dict[Tuple[int, int], int] = {}
    for rank in (sync or {}):
        path = os.path.join(tracedir, f"rank-{rank}.jsonl")
        if not os.path.exists(path):
            continue
        recs = []
        with open(path) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("op") != "site":
                    recs.append(r)
        recs = [r for r in _fold_begins(recs) if r.get("op") not in THREAD_SIDE]
        place = [r.get("seq", -1) for r in recs]
        for ch in t.chains.get(rank, [rank]):
            for i in range(len(t.ranks.get(ch, ()))):
                k = t.fpos.get((ch, i), i) if rank in t.chains else i
                seq[(ch, i)] = place[k] if k < len(place) else -1
    t.__dict__["_stretto_sync"] = sync
    t.__dict__["_stretto_seq"] = seq
    return t


def lock_gates(t, cut, stops=frozenset()) -> Optional[List[str]]:
    """The STRETTO_PIN_LOCK tokens of trace t over the horizon cut: per lock, its acquisitions
    in the trace's order up to the first that lies beyond the horizon, as
    "proc:chain#n>chain#n...", a condition wait that timed out marked "t"; each try in the
    horizon that failed as "proc:chain#n!". An acquisition lies in the horizon when the record
    its chain writes next does, or when the record before it does and the chain is not cut right
    after that record by a changed receive (stops: (chain, cut) of each such cut; rule 1 cuts
    the synchronisation after the receive, rule 8 a later record whose own predecessors are
    missing); a chain without records is not cut. A single construct's attempts come winner
    first within each of its instances. None when the trace has no sync log (with_sync)."""
    from bisect import bisect_right
    sync = t.__dict__.get("_stretto_sync")
    if not sync:
        return None
    out = []
    for proc, rows in sorted(sync.items()):
        chs = t.chains.get(proc, [proc])
        names = {chain_label(t, ch): ch for ch in chs}
        seq = t.__dict__.get("_stretto_seq", {})
        seqs = {ch: [seq.get((ch, i), -1) for i in range(len(t.ranks.get(ch, ())))] for ch in chs}

        def inside(name: str, seq: int) -> bool:
            ch = names.get(name)
            if ch is None:
                return True
            c = cut.get(ch, len(seqs[ch])) if isinstance(cut, dict) else cut[ch]
            j = bisect_right(seqs[ch], seq)
            return j < c or (j == c and c > 0 and (ch, c) not in stops)

        locks: Dict[int, list] = {}
        for lid, name, n, seq, flag in rows:
            if flag == "f":
                if inside(name, seq):
                    out.append(f"{proc}:{name}#{n}!")
                continue
            locks.setdefault(lid, []).append((name, n, seq, flag))
        for lid, seqn in locks.items():
            if any(e[3] == "l" for e in seqn):
                k: Counter = Counter()
                inst = []
                for e in seqn:
                    inst.append(k[e[0]])
                    k[e[0]] += 1
                seqn = [e for _, e in sorted(zip(inst, seqn), key=lambda p: (p[0], p[1][3] == "l"))]
            listed = list(itertools.takewhile(lambda e: inside(e[0], e[2]), seqn))
            if listed:
                out.append(f"{proc}:" + ">".join(f"{e[0]}#{e[1]}{'t' if e[3] == 't' else ''}" for e in listed))
    return out


def sync_divergence(t, pins) -> List[str]:
    """The chain pins whose receive, in a replay's own trace t, has another happens-before on
    the other chains of its process than in the traced run (the part of the token after '|'):
    the replay synchronised its threads in another order, and a chain's ordinals may then name
    another piece of work (a pool of threads that hands a request to another thread)."""
    out = []
    at = _chain_ops(t)[1]
    for k in pin_tokens(pins):
        if k.kind != "crecv" or not k.want or "|" not in k.want:
            continue
        want = k.want.split("|", 1)[1]
        r = at.get((k.rank, k.chain, "r", k.idx))
        if r is None:
            continue
        have = dict(p.split(":") for p in _hb_text(t, r).split(";") if p)
        diff = [f"{c} {have.get(c, '0')} (trace {n})" for c, n in (p.split(":") for p in want.split(";") if p) if have.get(c, "0") != n]
        if diff:
            out.append(f"rank {k.rank} chain {k.chain} ridx={k.idx}: operations of other threads before it: " + ", ".join(diff))
    return out


def _swap_of(t, f: Finding):
    """(M, changed) of a concurrent swap finding (report: "concurrent swap" in its details, or
    the second receive's 1a "(concurrent swap with ...)"): f.recv takes f.send and the receive
    that took f.send in the run takes f.recv's message; None for another finding."""
    if not t.multi or f.recv is None or f.send is None:
        return None
    if not (any(d.startswith("concurrent swap") for d in f.detail) or "(concurrent swap with" in f.title):
        return None
    victim = next((rk for rk, sk in t.observed.items() if sk == f.send and rk != f.recv), None)
    mine = t.observed.get(f.recv)
    if victim is None or mine is None:
        return None
    M = dict(t.observed)
    M[f.recv], M[victim] = f.send, mine
    return M, {f.recv, victim}


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
    rank, pidx), ("crecv", rank, chain, ridx) in a trace of several threads; None if it has no
    ordinal."""
    if f.probe is not None:
        q = t.probes[f.probe]
        return None if q.pidx is None else ("probe", q.rank, q.pidx)
    if f.recv is not None:
        r = t.recvs[f.recv]
        if t.multi:
            return ("crecv",) + _chain_ops(t)[0][("r", r.key)]
        return None if r.widx is None else ("recv", r.rank, r.widx)
    return None


def _test_flip_event(t, f: Finding):
    """(record, receive, send) of a test-flip finding: the failed MPI_Test or
    MPI_Request_get_status at f.event, the receive of its request and the message the run
    completed it with (report.test_flip_finding); None if the trace does not give them."""
    if f.event is None:
        return None
    rank, idx = f.event
    recs = t.ranks.get(rank, [])
    rec = recs[idx] if idx < len(recs) else None
    if rec is None or rec.raw is None or rec.op != "wait":
        return None
    reqs = [q for q in rec.raw.get("reqs", []) if q is not None and q >= 0]
    r = t.recv_by_req(rank, reqs[0]) if len(reqs) == 1 else None
    s = t.observed_send(r) if r is not None else None
    return None if s is None else (rec, r, s)


def _horizon(t, f: Finding, B=None, infeasible_ok: bool = False, M=None, changed=None):
    """M and changed, if given, are the alternative matching and its changed receives (a swap);
    by default the finding is a steal: f.recv takes f.send and the receive that took it in the
    run is left without a message. A probe finding without a message (probe-unflip) is the
    probe returning false, and a test-flip finding (f.event) the failed test finding its
    receive complete, each judged as the report judged it (report.probe_kw, report.wait_kw)."""
    from .feasible import check
    from .report import wait_kw
    if f.probe is not None and f.send is not None:
        M = t.observed
        chk = check(t, M, set(), B, "L1", changed_probes={f.probe}, probe_alt={f.probe: f.send})
        change = ("probe", f.probe, f.send)
    elif f.probe is not None and f.cls == "probe-unflip":
        M = t.observed
        chk = check(t, M, set(), B, "L1", changed_probes={f.probe}, probe_alt={f.probe: None})
        change = ("unflip", f.probe, None)
    elif f.cls == "test-flip" and _test_flip_event(t, f) is not None:
        rec, r_j, s_j = _test_flip_event(t, f)
        M = t.observed
        chk = check(t, M, set(), B, "L1", **wait_kw(rec, None, r_j, s_j))
        change = ("event", f.event, None)
    elif f.recv is not None and f.send is not None and f.level in ("L1", "L1+SP"):
        if M is None and _swap_of(t, f) is not None:
            M, changed = _swap_of(t, f)          # a concurrent swap: both receives change
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
    if t.multi:
        # several threads: chain pins and order gates (the section above); receive alternatives only
        return _chain_specs(t, M, chk, {change[1]}, B, None) if change[0] == "recv" else None
    required: Set[Tuple[str, int, int]] = set()
    # a flag flip changes a failed poll, which no probe pin names: its own pin (STRETTO_PIN_FLIP,
    # the message to return, or for a test its request's completion) forces it in the
    # alternative, and the control pins the recorded false (control_pin); the alternative must
    # show "matched flip key" for the pin's key, the forced call returning true. An unflip pins
    # its probe to flag 0 in the alternative (STRETTO_PIN_PROBE with source -1, "applied
    # unflip") and to its observed message in the control.
    flip = change[0] == "event" or (change[0] == "probe" and not t.probes[change[1]].flag)
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
            rtok.append(pin_token(t, r, m.proc, m.tag))
            if alternative and r.completed and _cidx(r) < cut[r.rank]:
                required.add(("recv", r.rank, r.widx))
        for q in sorted(t.probes.values(), key=lambda q: (q.rank, q.idx)):
            mine = change[0] in ("probe", "unflip") and change[1] == q.key
            outside = q.idx >= cut[q.rank] and not mine
            if q.pidx is None or not q.flag or outside:
                continue
            if alternative and mine and change[0] == "unflip":
                ptok.append(f"{q.rank}:{q.pidx}:-1@" + ident_text(t, q))
                required.add(("probe", q.rank, q.pidx))
                continue
            sk = change[2] if alternative and mine else q.seen
            if sk is None:
                continue
            m = t.sends[sk]
            ptok.append(pin_token(t, q, m.proc, m.tag))
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
        if k.kind in ("flip", "crecv") or k.src < 0:
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


def _end_probes(t, ppins, tokens=()) -> list:
    """(probe, its envelope in the replay, its pin) for every probe a hung run ended in without
    a message: a blocking probe a rank was blocked in, or a failed Iprobe that is its rank's
    last record (a polling loop). Its pin is the one of its envelope's next ordinal, for a pin
    that names one (tokens), else that of the rank's next probe ordinal (replay/pin.c counts the
    wildcard probes that found a message, per rank and per envelope)."""
    nxt: Dict[int, int] = {}
    for q in t.probes.values():
        if q.pidx is not None:
            nxt[q.rank] = max(nxt.get(q.rank, 0), q.pidx + 1)
    by_env = {(k.rank, k.ident[0], k.ident[1], k.n): (k.src, k.tag) for k in tokens
              if k.kind == "probe" and k.src >= 0 and k.n is not None and k.ident is not None and None not in k.ident[:2]}
    global_pidx = {(k.rank, k.idx) for k in tokens if k.kind == "probe" and k.n is not None}
    env_next = Counter(env for env, _ in _envelopes(t)[0].values()) if by_env else Counter()
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
        pin = None
        if p.wildcard:
            raw = t.ranks[p.rank][p.idx].raw if by_env else None
            env = (p.rank, raw.get("comm"), p.tag) if raw is not None else None
            pin = by_env.get(env + (env_next[env],)) if env is not None else None
            if pin is None and (p.rank, nxt.get(p.rank, 0)) not in global_pidx:
                pin = ppins.get((p.rank, nxt.get(p.rank, 0)))
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
    forbids for the original receive (non-overtaking, MPI 4.1 §4.5), and a probe can report such
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
    end_probes = _end_probes(t, ppins, pin_tokens(pins)) if hung else []
    if not pend and not end_probes:
        why = matching_order_ok(t, t.observed, lambda rk: True, None, lambda q: True)
        if why is None and build_graph(t, t.observed, None, None).find_cycle():
            why = "constraint cycle over the run's own matches"
        return why
    if t.multi:
        # which message a pending receive of one of several threads may have taken needs the
        # readings per process in issue order, which _rematch does not make
        return "undetermined: a replay of several threads that left receives pending or hung is not judged"
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
    source, pidx the pin's (a pin with an envelope ordinal falls on its envelope's probe, whatever
    that probe's pidx in the replay). The trace says what the run did; the replay library's
    report lines, which Open MPI's mpirun merges from the ranks' stderr in chunks, can be cut
    apart and missed. A pin with an identity counts only on an operation of that identity
    (trace_misapplied)."""
    tokens = pin_tokens(pins)
    toks = {(k.kind, k.rank, k.idx): k for k in tokens}
    out: Set[Tuple[str, int, int]] = set()
    for r in t.recvs.values():
        k = toks.get(("recv", r.proc, r.widx)) if r.widx is not None else None
        if k and r.completed and r.msrc == k.src and (k.tag is None or r.mtag == k.tag) and _agrees(k.ident, identity(t, r)):
            out.add(("recv", r.proc, r.widx))
    of, at, _ = _chain_ops(t) if any(k.kind == "crecv" for k in tokens) else ({}, {}, {})
    for k in tokens:
        if k.kind == "crecv":
            # the receive of the chain at the ordinal took the message the token names (the
            # sender's process, chain and send ordinal), as the replay's issue order reconstructs it
            r = at.get((k.rank, k.chain, "r", k.idx))
            sk = t.observed.get(r.key) if r is not None and r.completed else None
            got = of.get(("s", sk)) if sk is not None else None
            if got is not None and k.want and ":".join(map(str, got)) == k.want.split("|", 1)[0] \
                    and _agrees(k.ident, identity(t, r)):
                out.add(k.key)
            continue
        if k.kind != "probe":
            continue
        q = _op_of(t, k)
        if q is not None and q.flag and q.msrc == k.src and (k.tag is None or q.mtag == k.tag) and _agrees(k.ident, identity(t, q)):
            out.add(("probe", k.rank, k.idx))
    return out


def trace_misapplied(t, pins) -> List[str]:
    """The pins of pins whose ordinal falls, in the replay's own trace t, on an operation of
    another identity than the traced one the pin names: the replay diverged before them."""
    out = []
    for k in pin_tokens(pins):
        if k.ident is None:
            continue
        x = _op_of(t, k)
        if x is not None and not _agrees(k.ident, identity(t, x)):
            what = {"probe": "pidx", "crecv": f"chain {k.chain} ridx"}.get(k.kind, "widx")
            out.append(f"rank {k.rank} {what}={k.idx}: the replay's operation has "
                       f"id={_ident_text(identity(t, x))}, the pin's id={_ident_text(k.ident)}")
    return out


# The largest replay trace judge_replay reads, about two million records. A replay that hangs
# in a polling loop of two alternating calls (an Iprobe and a Test, which the tracer's folding of
# repeats does not cover) writes records until its timeout: gigabytes in 20 s on a driver of
# deal.II's NBX algorithm, which took a worker past 24 GB to load (28 Oct 2026).
MAX_JUDGED_TRACE_BYTES = 200 * 1024 * 1024


def judge_replay(tracedir: str, result: str, pins=None, seen: Optional[set] = None,
                 misapplied: Optional[list] = None, ended: Optional[set] = None,
                 diverged: Optional[list] = None) -> Tuple[str, str]:
    """("True", "") if the trace a replay recorded in tracedir is a run of the program without
    its pins up to where the run ended (unpinned_violation; result is the replay's
    classify_rc), ("False", the violation), or ("unknown", why) when the trace cannot be read or
    its readings are too many. seen, if given, receives the pins the trace shows taken
    (trace_matches), misapplied the pins that fell on another operation (trace_misapplied),
    ended the pinned receives still pending where their rank stopped (trace_ended), diverged
    the chain pins whose synchronisation differs from the trace's (sync_divergence). A trace of
    more than MAX_JUDGED_TRACE_BYTES is not read: ("unknown", "trace not judged: ..."), and the
    replay's pins are then judged from its pin log alone (all_matched on its stderr)."""
    from . import trace as trace_mod
    size = sum(os.path.getsize(os.path.join(tracedir, f)) for f in os.listdir(tracedir)
               if os.path.isfile(os.path.join(tracedir, f))) if os.path.isdir(tracedir) else 0
    if size > MAX_JUDGED_TRACE_BYTES:
        return "unknown", f"trace not judged: {size} bytes, over the limit of {MAX_JUDGED_TRACE_BYTES}"
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
    if diverged is not None:
        diverged += sync_divergence(t, pins)
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


def _applied(stderr: str, pins) -> Set[tuple]:
    """("recv", rank, widx) of every receive pin the replay library reported applied, on an
    operation of the pin's identity; ("crecv", rank, chain, ridx) of every such chain pin."""
    want = _expected(pins)
    out = set()
    for what, rank, idx, ident in _lines(stderr):
        if what in ("applied", "capplied"):
            key = _line_key(what, rank, idx)
            if _agrees(want.get(key), ident):
                out.add(key)
    return out


def trace_ended(t, pins) -> Set[tuple]:
    """("recv", rank, widx) of every pinned receive that, in the replay's own trace t, was still
    pending when its rank stopped without MPI_Finalize: posted with its pin (on an operation of
    the pin's identity), never completed; ("crecv", rank, chain, ridx) of such a chain pin's
    receive. A run that MPI ends with an error inside a receive (MPI_ERR_TRUNCATE under
    MPI_ERRORS_ARE_FATAL) leaves its pinned receive so."""
    tokens = pin_tokens(pins)
    toks = {(k.rank, k.idx): k for k in tokens if k.kind == "recv"}
    stopped = set(t.truncated)
    out = {("recv", r.proc, r.widx) for r in t.recvs.values()
           if r.widx is not None and (r.proc, r.widx) in toks and r.proc in stopped and not r.completed
           and _agrees(toks[(r.proc, r.widx)].ident, identity(t, r))}
    if any(k.kind == "crecv" for k in tokens):
        at = _chain_ops(t)[1]
        for k in tokens:
            r = at.get((k.rank, k.chain, "r", k.idx)) if k.kind == "crecv" else None
            if r is not None and r.proc in stopped and not r.completed and _agrees(k.ident, identity(t, r)):
                out.add(k.key)
    return out


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
    need = set(required) if required is not None else {k.key for k in pin_tokens(pins)}
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
    diverged: List[str] = field(default_factory=list)      # chain pins whose synchronisation differs from the
                                                           # trace's (sync_divergence)


def misapplied_pins(r: subprocess.CompletedProcess, result: str, from_trace: List[str] = ()) -> List[str]:
    """Why the run's pins fell on other operations than the traced ones: the replay library's
    report lines, the replay's own trace (trace_misapplied), or, when the lines were lost, the
    exit code the library ends such a run with; an order gate that timed out (the replay
    diverged before the operation it waits for) is reported with them."""
    out = pins_misapplied(r.stderr if r is not None else "") + list(from_trace)
    if not out and result == f"APP-ERROR:{PIN_MISAPPLIED}":
        out.append(f"the run ended with the exit code of a misapplied pin ({PIN_MISAPPLIED})")
    if not out and result == f"APP-ERROR:{GATE_TIMEOUT}":
        out.append(f"the run ended with the exit code of an order gate that timed out ({GATE_TIMEOUT})")
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
    if any(a.startswith(("STRETTO_PIN_ORDER=", "STRETTO_PIN_LOCK=")) for a in ([pin] if isinstance(pin, str) else (pin or []))):
        # a gate gives up before the run's own timeout, so that a closed one is told from a hang
        extra_env = {"STRETTO_GATE_TIMEOUT_MS": str(int(timeout * 800)), **(extra_env or {})}
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
        diverged: List[str] = []
        valid, why = judge_replay(d, res, pin, seen, mis, ended, diverged)

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
                      None if why.startswith(("trace unreadable", "trace not judged")) else ended, diverged)
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
        # several threads: a replay or control that synchronised its threads otherwise than the
        # trace (sync_divergence), or a control that did not take its pinned messages, follows
        # another run than the one the pins name
        mis += rp.diverged + ctl.diverged
        if t is not None and t.multi and ctl.proc is not None and not all_matched(ctl_pins, ctl.proc.stderr, None, ctl.seen):
            mis.append("the control did not take every pinned message")
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
