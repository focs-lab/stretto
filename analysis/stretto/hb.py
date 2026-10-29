"""Happens-before selection of alternative matchings, the rule of earlier tools, next to the
check that decides them (experiments/e8_hb.py; paper, section "The constraint graph").

Tools that predict or explore message races from one run select the sends a wildcard receive
could have taken by happens-before concurrency over the run, refined by non-overtaking. This
module computes those selections, so that they can be compared pair by pair with the
alternatives the constraint graph accepts.

The relation. hb is the causal order of the observed run, Lamport's happened-before over MPI
calls: program order on each rank (issue(e) -> complete(e) -> issue(next), as in
trace.py); a message edge from the issue of every send to the completion of the receive that
took it in the run, which for a nonblocking receive is the wait or test that returned it, the
point at which its status (and a piggybacked clock) becomes visible; an edge from the issue
of a send to the completion of every probe that reported it; and collectives by their shape
(matching.coll_shape: barrier-like ones order every member's issue before every member's
completion, bcast-like ones the root's issue before the others' completions, reduce-like
ones the others' issues before the root's completion, scans each member's issue before the
later members' completions; unknown and rootless ones as barriers). It has none of the
constraint graph's rendezvous edges, match nodes or matching-order edges
(matching.build_graph): those are rules of MPI's matching, not information that travels with
a message. Every edge of hb is a path of the constraint graph at B = inf, so hb is contained
in matching.observed_hb. The vector clocks of Clocks are exact for hb.

The head. For a wildcard receive r (source or tag ANY) and a sending rank q, the
non-overtaking head is the earliest send of q to rank(r) on comm(r) that r accepts, is not
cancelled, was not removed by an Mprobe before r, and was not taken in the run by a receive of
rank(r) posted before r. A later send of q would overtake it, an earlier one is consumed. Heads
other than r's observed message are the pairs the rules consider; they are computed by the
kernel of matching.candidates_full (its steals with no causal pruning), so that the rules and
the check start from the same candidates. A send taken in the run by an earlier receive is
never a head: the rules do not consider swaps.

The rules. For a head s of r:
  (a) POST, selection at the posting clock: s is selected iff not issue(r) hb issue(s): a send
      is a potential match of a wildcard receive if it is not causally after the receive, with
      the clock the receive had when it was posted, and only the first such send of each source
      counts (non-overtaking). DAMPI ("A Scalable and Distributed Dynamic Formal Verifier for
      MPI Programs", SC 2010) gives a nonblocking receive a later clock, at its wait or at a
      later receive that must be matched after it.
  (b) COMP, selection at the completion clock: s is selected iff not c(r) hb issue(s), where
      c(r) is the completion of r (the wait or test that returned it). A receive that never
      completed has no completion clock; c(r) is then the last record of its rank, so every
      head is selected.
For a blocking receive the two coincide, since issue(r) has no other successor than its own
completion, and both are the race set of reachability testing ("Reachability Testing of
Concurrent Programs", TSE 2006, `lei06reachability`): the sends not causally after the
receive that are the earliest unconsumed message of their source. They differ only for a
nonblocking receive whose rank sends while it is pending. Rule (a) rejects every send that
causally follows the posting, which the pending receive may well take; rule (b) accepts a send
that is concurrent with the wait although the receive must have been matched before an earlier
event of its rank (the paper's example: a later receive takes a message the pending one
accepts, and the send depends on that later receive).
Probes: a wildcard probe that reported a message is one call, so posting and completion
coincide; its heads are those of matching.probe_candidates and it selects the heads that are
not causally after it.

Stretto's side. judge() states, for every pair the analysis enumerates, the verdict
report.analyze gives: an alternative is accepted if it is feasible at some threshold at which
the observed run is realisable, with the fast checks of stretto.fast or the generic check;
a head the analysis prunes as causally after the receive's completion in the constraint
graph is rejected with that reason.
"""
from __future__ import annotations
from array import array
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import fast
from .feasible import profile_summary
from .matching import candidates_full, coll_shape, observed_hb, observed_ready_errors, probe_candidates
from .report import _profiles_fast, _profiles_generic
from .trace import Key, Probe, Recv, Send, Trace

Node = Tuple[int, int, str]
POST, COMP = "post", "comp"


def _cidx(x) -> int:
    return x.complete_idx if x.complete_idx is not None else x.idx


def _never(a, b) -> bool:
    return False


class Clocks:
    """Vector clocks of hb (the module docstring) over the observed run. A node's own entry is
    2 * idx for its issue and 2 * idx + 1 for its completion, as in fast.Observed; a hb b iff
    b's clock covers a's own entry. Only the clocks of send issues are kept, since every query
    asks whether a node of a receive or probe precedes a send. Raises ValueError if hb is
    cyclic, which a recorded run cannot be unless a collective of unknown shape does not
    synchronise."""

    def __init__(self, t: Trace):
        self.t = t
        ranks = sorted(t.ranks)
        self.pos = {r: i for i, r in enumerate(ranks)}
        P = len(ranks)
        # cross-rank predecessors of completion nodes, by (rank, idx): issue nodes (rank, idx)
        deps: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

        def dep(target: Tuple[int, int], source: Tuple[int, int]) -> None:
            if target != source:
                deps.setdefault(target, []).append(source)

        for rk, sk in t.observed.items():
            r, s = t.recvs[rk], t.sends[sk]
            if r.completed:
                dep((r.rank, _cidx(r)), (s.rank, s.idx))
        for p in t.probes.values():
            if p.completed and p.flag and p.seen is not None:
                s = t.sends[p.seen]
                dep((p.rank, p.idx), (s.rank, s.idx))
        for cs in t.coll_sets():
            shape, _ = coll_shape(cs[0].name)
            if shape in ("root-out", "root-in") and any(c.root is None for c in cs):
                shape = "all"
            members = t.comm_members.get(cs[0].comm, [])
            order = {r: i for i, r in enumerate(members)}
            done = [c for c in cs if c.completed]
            for b in done:
                target = (b.rank, _cidx(b))
                for a in cs:
                    if a is b:
                        continue
                    if (shape == "all"
                            or (shape == "root-out" and a.rank == a.root)
                            or (shape == "root-in" and b.rank == b.root)
                            or (shape == "prefix" and order.get(a.rank, 0) < order.get(b.rank, 0))):
                        dep(target, (a.rank, a.idx))
        sources = {d for v in deps.values() for d in v} | {(s.rank, s.idx) for s in t.sends.values()}
        self.vc: Dict[Tuple[int, int], array] = {}
        cur = {R: array("q", [-1] * P) for R in ranks}
        progress = {R: 0 for R in ranks}          # nodes processed: node n is record n // 2, issue if n is even
        total = {R: 2 * len(t.ranks[R]) for R in ranks}
        moved = True
        while moved:
            moved = False
            for R in ranks:
                c, me, n, end = cur[R], self.pos[R], progress[R], total[R]
                while n < end:
                    idx = n >> 1
                    if n & 1:
                        ds = deps.get((R, idx))
                        if ds:
                            if any(progress[q] <= 2 * i for q, i in ds):
                                break                     # a predecessor on another rank is not done yet
                            for d in ds:
                                v = self.vc[d]
                                for k in range(P):
                                    if v[k] > c[k]:
                                        c[k] = v[k]
                    c[me] = n
                    if not n & 1 and (R, idx) in sources:
                        self.vc[(R, idx)] = array("q", c)
                    n += 1
                if n != progress[R]:
                    progress[R] = n
                    moved = True
        stuck = [R for R in ranks if progress[R] < total[R]]
        if stuck:
            raise ValueError(f"happens-before over the run is cyclic (ranks {stuck} stop at node "
                             f"{[progress[R] for R in stuck]})")
        self.last = {R: (R, len(t.ranks[R]) - 1, "c") for R in ranks if t.ranks[R]}

    def before(self, a: Node, s: Send) -> bool:
        """a hb issue(s)."""
        return self.vc[(s.rank, s.idx)][self.pos[a[0]]] >= 2 * a[1] + (1 if a[2] == "c" else 0)

    def reference(self, r: Recv, rule: str) -> Node:
        """The node at which a rule compares r: its posting (POST) or its completion (COMP), the
        last record of its rank if it never completed."""
        if rule == POST:
            return r.issue_node()
        rc = r.complete_node()
        return rc if rc is not None else self.last[r.rank]

    def selects(self, r: Recv, s: Send, rule: str) -> bool:
        return not self.before(self.reference(r, rule), s)

    def selects_probe(self, p: Probe, s: Send) -> bool:
        return not self.before(p.issue_node(), s)


def heads(t: Trace) -> List[Tuple[Key, Key]]:
    """(receive, send): the non-overtaking head of each source of each wildcard receive, other
    than its observed message (the module docstring), in the order of candidates_full."""
    alts, _ = candidates_full(t, _never)
    out, seen = [], set()
    for a in alts:
        if a.kind == "steal" and (a.recv, a.send) not in seen:
            seen.add((a.recv, a.send))
            out.append((a.recv, a.send))
    return out


def probe_heads(t: Trace) -> List[Tuple[Key, Key]]:
    """(probe, send): the heads of each wildcard probe that reported a message, other than the
    source it saw."""
    return [(p.key, s.key) for p, s in probe_candidates(t, _never)]


def receive_candidates(t: Trace, rule: str, clocks: Optional[Clocks] = None) -> List[Tuple[Key, Key]]:
    """The pairs rule (a) POST or (b) COMP selects."""
    clocks = clocks or Clocks(t)
    return [(rk, sk) for rk, sk in heads(t) if clocks.selects(t.recvs[rk], t.sends[sk], rule)]


def probe_selected(t: Trace, clocks: Optional[Clocks] = None) -> List[Tuple[Key, Key]]:
    clocks = clocks or Clocks(t)
    return [(pk, sk) for pk, sk in probe_heads(t) if clocks.selects_probe(t.probes[pk], t.sends[sk])]


class _Windows:
    """Per rank, the number of sends and collectives issued up to each record, so that a
    receive's window between posting and completion is judged in constant time."""

    def __init__(self, t: Trace):
        self.t, self.sent = t, {}

    def any_between(self, rank: int, lo: int, hi: int) -> bool:
        a = self.sent.get(rank)
        if a is None:
            a = self.sent[rank] = array("q", [0])
            for rec in self.t.ranks[rank]:
                call = rec.raw.get("call") if rec.op == "blocked" and rec.raw else rec.op
                a.append(a[-1] + (1 if call in ("send", "coll") else 0))
        return a[hi] - a[lo + 1] > 0        # records lo+1 .. hi-1


def receive_kind(t: Trace, r: Recv, windows: Optional[_Windows] = None) -> str:
    """blocking; nonblocking, nothing sent while pending (no send or collective of its rank
    between posting and completion, so that the two clocks coincide); nonblocking, pending
    across a send (one lies in between: the clocks can differ); nonblocking, never completed."""
    if r.blocking:
        return "blocking"
    if not r.completed:
        return "nonblocking, never completed"
    w = windows or _Windows(t)
    if w.any_between(r.rank, r.idx, _cidx(r)):
        return "nonblocking, pending across a send"
    return "nonblocking, nothing sent while pending"


def receive_kinds(t: Trace) -> Dict[Key, str]:
    """receive_kind of every wildcard receive."""
    w = _Windows(t)
    return {r.key: receive_kind(t, r, w) for r in t.recvs.values() if r.wildcard}


def completed_by(t: Trace, r: Recv) -> str:
    """The call that completed r: recv for a blocking receive, else the kind of the wait or
    test record (wait, waitall, waitany, test, ...), '' if it never completed."""
    if r.blocking:
        return "recv"
    if not r.completed or r.complete_idx is None:
        return ""
    rec = t.ranks[r.rank][r.complete_idx]
    return (rec.raw or {}).get("kind", rec.op)


# --------------------------------------------------------------- Stretto's verdicts

@dataclass
class Decision:
    accepted: bool
    kinds: str            # the analysis' alternatives for the pair: steal, swap, probe, pruned
    reason: str           # why rejected (the generic or fast check's reason at B = inf), '' if accepted
    B: str                # the thresholds at which it is accepted (profile_summary), '' if rejected


def declines(t: Trace) -> Optional[str]:
    """Why report.analyze predicts nothing for t (observed findings only), None if it does."""
    if t.multi:
        return "several threads (chains): the comparison with happens-before does not cover them yet"
    multi = [rank for rank, n in t.threads.items() if n > 1]
    if multi:
        return "MPI_THREAD_MULTIPLE with several calling threads" if t.thread_level >= 3 else "several threads calling MPI"
    if t.unmodelled:
        return "calls outside the modelled fragment"
    if t.incomplete:
        return "an incomplete trace"
    if any(observed_ready_errors(t)):
        return "a ready send issued before its receive was posted"
    return None


def judge(t: Trace, generic: bool = False) -> Optional[Dict[Tuple[str, Key, Key], Decision]]:
    """Stretto's verdict on every pair it enumerates, keyed ("recv", receive, send) or ("probe",
    probe, send), exactly as report.analyze states it (with_sp=False): alternatives profiled
    over the breakpoints with the fast checks (generic=False) or the generic check, accepted when
    feasible at some threshold at which the observed run is realisable. A receive pair may stand
    for a steal and a swap; it is accepted if either is. Heads the enumeration prunes as causally
    after the receive's completion in the constraint graph are rejected as such. None when the
    analysis declines to predict (declines)."""
    if declines(t):
        return None
    ob_inf = None if generic else fast.Observed(t, None)
    hb = observed_hb(t) if generic else ob_inf.reaches
    alts, _ = candidates_full(t, hb)
    probes = probe_candidates(t, hb)
    pr = _profiles_generic(t, alts, probes, []) if generic else _profiles_fast(t, alts, probes, [], ob_inf=ob_inf)
    realisable = {B for B, ok in pr.real.items() if ok} or set(pr.real)
    out: Dict[Tuple[str, Key, Key], Decision] = {}

    def put(key, ok: bool, kind: str, reason: str, brange: str) -> None:
        v = out.get(key)
        if v is None:
            out[key] = Decision(ok, kind, "" if ok else reason, brange if ok else "")
            return
        v.kinds = v.kinds + "," + kind
        if ok and not v.accepted:
            v.accepted, v.reason, v.B = True, "", brange
        elif not ok and not v.accepted and reason not in v.reason.split("; "):
            v.reason += "; " + reason

    for i, a in enumerate(alts):
        minB, at_inf, brange = profile_summary({B: ok for B, ok in pr.alt[i].items() if B in realisable})
        put(("recv", a.recv, a.send), not (minB is None and not at_inf), a.kind, pr.alt_reason[i], brange)
    for rk, sk in heads(t):
        if ("recv", rk, sk) not in out:
            put(("recv", rk, sk), False, "pruned", "send causally follows the receive's completion", "")
    for j, (p, s) in enumerate(probes):
        minB, at_inf, brange = profile_summary({B: ok for B, ok in pr.probe[j].items() if B in realisable})
        put(("probe", p.key, s.key), not (minB is None and not at_inf), "probe", pr.probe_reason[j], brange)
    for pk, sk in probe_heads(t):
        if ("probe", pk, sk) not in out:
            put(("probe", pk, sk), False, "pruned", "send causally follows the probe", "")
    return out


# --------------------------------------------------------------- the comparison

@dataclass
class Pair:
    op: str               # recv | probe
    at: Key               # the receive or probe
    send: Key             # the send it would take or report instead
    head: bool            # a head: the pair the rules consider
    post: bool            # selected by rule (a)
    comp: bool            # selected by rule (b)
    stretto: Optional[Decision]   # None if the analysis does not enumerate the pair
    kind: str             # receive_kind, or the probe's kind


def compare(t: Trace, generic: bool = False, clocks: Optional[Clocks] = None) -> Optional[List[Pair]]:
    """Every pair that a rule selects or the analysis enumerates, with both verdicts. None when
    the analysis declines to predict."""
    verdicts = judge(t, generic)
    if verdicts is None:
        return None
    clocks = clocks or Clocks(t)
    windows = _Windows(t)
    out: List[Pair] = []
    seen = set()
    for rk, sk in heads(t):
        r, s = t.recvs[rk], t.sends[sk]
        seen.add(("recv", rk, sk))
        out.append(Pair("recv", rk, sk, True, clocks.selects(r, s, POST), clocks.selects(r, s, COMP),
                        verdicts.get(("recv", rk, sk)), receive_kind(t, r, windows)))
    for pk, sk in probe_heads(t):
        p, s = t.probes[pk], t.sends[sk]
        seen.add(("probe", pk, sk))
        sel = clocks.selects_probe(p, s)
        out.append(Pair("probe", pk, sk, True, sel, sel, verdicts.get(("probe", pk, sk)), p.kind))
    for (op, at, sk), v in sorted(verdicts.items()):
        if (op, at, sk) not in seen:     # a swap with a send taken by an earlier receive: no head
            kind = receive_kind(t, t.recvs[at], windows) if op == "recv" else t.probes[at].kind
            out.append(Pair(op, at, sk, False, False, False, v, kind))
    return out
