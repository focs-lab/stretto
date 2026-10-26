"""Feasibility of an alternative matching: horizon closure + matching-order checks +
acyclicity of the constraint graph over the kept prefix."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .horizon import compute_cut, witness_kept
from .matching import Overlay, affected_ranks, build_graph, matching_order_ok
from .trace import Key, Trace


def without(M, keys):
    """M without the pairs of the receives keys (an Overlay over M), M itself if there are none."""
    if not keys:
        return M
    if isinstance(M, Overlay):
        return M.with_(removed=keys)
    return Overlay(M, {}, keys)


@dataclass
class Verdict:
    feasible: bool                 # the matching is realisable over the kept prefix
    reason: str
    cut: Optional[Dict[int, int]] = None
    cycle: Optional[list] = None
    hangs: Dict[int, str] = field(default_factory=dict)   # SP mode: ranks that never complete an operation
    issued: Dict[int, int] = field(default_factory=dict)  # rank -> a blocking send cut at its own record, issued
    # nonblocking receives that rule 6 deprived of their message: posted inside the horizon,
    # judged by nothing (the claim leaves them out of the matching)
    deprived: Set[Key] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        """Realisable and, under the single-path assumption, every rank completes."""
        return self.feasible and not self.hangs


def check(t: Trace, M: Dict[Key, Key], changed, B: Optional[int], mode: str = "L1",
          extra_edges: Iterable = (), changed_probes: Set[Key] = frozenset(), strong_only: bool = False,
          probe_alt: Optional[Dict[Key, Key]] = None, changed_events: Set[Tuple[int, int]] = frozenset(),
          must_issue: Iterable[Key] = (), bounds=None, must_match: Iterable[Key] = ()) -> Verdict:
    """must_issue: sends the alternative needs issued inside the kept prefix besides those
    of M (the message a Waitany alternative completes with). bounds: read bounds
    (stretto.reads) for rule 1 of the horizon, H1 when None. must_match: receives that must
    keep their message of M inside the horizon (the receive a Waitany alternative or a test
    flip completes), which rule 6 may deprive of it. The matching judged is M without the
    receives rule 6 deprived (Verdict.deprived)."""
    seen = {}
    for rk, sk in M.items():
        if rk not in t.recvs or sk not in t.sends:
            return Verdict(False, f"unknown receive or send in matching: {rk} <- {sk}")
        if not t.recvs[rk].accepts(t.sends[sk]):
            return Verdict(False, f"{t.recvs[rk]} does not accept {t.sends[sk]}")
        if sk in seen:
            return Verdict(False, f"send {t.sends[sk]} consumed twice")
        seen[sk] = rk
    hangs: Dict[int, str] = {}
    issued: Dict[int, int] = {}
    deprived: Set[Key] = set()
    cut = compute_cut(t, M, set(changed), B, mode, changed_probes, hangs, changed_events, issued, probe_alt, bounds,
                      deprived)
    M = without(M, deprived)
    # SP mode only: the continuation is fixed, so a cut operation is one its rank never completes.
    # The matching may still be realisable (the run then deadlocks): feasible says the former,
    # hangs the latter, ok both.
    hang_text = "hang under the single-path assumption: " + "; ".join(hangs[r] for r in sorted(hangs)) if hangs else ""
    for pk, sk in (probe_alt or {}).items():
        if sk is None:
            continue           # the probe now observes nothing (a flag flip to false): no message to issue
        s, p = t.sends[sk], t.probes[pk]
        if s.idx >= cut[s.rank] and issued.get(s.rank) != s.idx:
            return Verdict(False, "the message the probe would observe is never issued within the horizon", cut)
        for x in t.recvs_of(p.rank):
            if x.idx >= p.idx:
                break
            if x.key not in M and not x.cancelled and not x.mrecv and x.accepts(s):
                return Verdict(False, f"receiver order: earlier {x} accepts {s} and has no message in this matching", cut)
    for sk in must_issue:
        s = t.sends[sk]
        if s.idx >= cut[s.rank] and issued.get(s.rank) != s.idx:
            return Verdict(False, "the message that would complete first is never issued within the horizon", cut)
    for rk in must_match:
        if rk in deprived:
            return Verdict(False, f"{t.recvs[rk]} is deprived of its message: a receive posted before it is pending", cut)
    if not witness_kept(t, cut, changed, changed_probes, changed_events, M, issued, deprived):
        # in L1 the horizon cut it; in SP a receive that can never complete precedes it
        return Verdict(False, "witness cut by the horizon closure" if mode == "L1" else
                       "witness unreachable: an earlier receive on its rank can never complete", cut)

    def kept_recv(rk: Key) -> bool:
        """Judged: posted in the horizon (matching order is decided at posting) with its
        message issued there. A posted receive whose message is not issued stays pending;
        rule 6 has removed the later receives it would deprive, and its own obligations
        concern a match outside the horizon."""
        r = t.recvs[rk]
        if r.idx >= cut[r.rank]:
            return False
        s = t.sends[M[rk]]
        return s.idx < cut[s.rank] or issued.get(s.rank) == s.idx

    def kept_probe(q) -> bool:
        return q.idx < cut[q.rank]

    ranks = affected_ranks(t, M, changed) if changed or M != t.observed else set()
    for pk in (probe_alt or {}):
        ranks.add(pk[0])
    why = matching_order_ok(t, M, kept_recv, ranks, kept_probe, probe_alt)
    if why:
        return Verdict(False, why, cut)
    g = build_graph(t, M, cut, B, extra_edges, strong_only, probe_alt, issued)
    cyc = g.find_cycle()
    if cyc:
        return Verdict(False, "constraint cycle", cut, cyc, hangs, issued, deprived)
    return Verdict(True, hang_text or "ok", cut, None, hangs, issued, deprived)


def observed_feasible_at(t: Trace, B: Optional[int], strong_only: bool = True) -> Verdict:
    """Is the observed matching itself realisable under buffering threshold B? A cycle
    over guaranteed edges only (strong_only) means: with eager limit <= B the same run
    deadlocks. Over every edge, which adds the probes' reports and collectives of unknown
    shape, the least such B is B0 of theorem T2, where the profiles start."""
    return check(t, t.observed, set(), B, mode="SP", strong_only=strong_only)


def breakpoints(t: Trace) -> List[int]:
    return [0] + t.message_sizes()


def feasible_profile(t: Trace, M: Dict[Key, Key], changed, mode: str = "L1", full: bool = True,
                     floor: Optional[int] = 0, **kw) -> Dict[Optional[int], bool]:
    """Feasibility at the breakpoints {0} U message sizes U {inf}: Verdict.ok, so in SP mode
    a threshold at which a rank hangs counts as infeasible. The default judges every
    breakpoint: this is the generic checker's profile, which the E2 harness and the tests
    use as the evidence for theorem T2 (docs/model.md) and as the reference for the
    bisected profile of report._profiles_fast. full=False checks the two ends and bisects
    between them, exact when feasibility is monotone in B (T2). floor (B0 of T2, None for
    infinity) is the least threshold at which the observed run is realisable: the bisection
    starts there, and the breakpoints below it are set infeasible, since T2 says nothing
    about them and the report states intervals over the realisable thresholds only."""
    bps = breakpoints(t)
    prof: Dict[Optional[int], bool] = {}
    if full or len(bps) <= 3:
        for B in bps + [None]:
            prof[B] = check(t, M, changed, B, mode, **kw).ok
        return prof
    if floor is None or floor > 0:
        below = [B for B in bps if floor is None or B < floor]
        for B in below:
            prof[B] = False
        bps = [B for B in bps if B not in below]
        if not bps:
            prof[None] = check(t, M, changed, None, mode, **kw).ok
            return prof
    lo, hi = bps[0], None
    prof[lo] = check(t, M, changed, lo, mode, **kw).ok
    prof[hi] = check(t, M, changed, hi, mode, **kw).ok
    if prof[lo] == prof[hi]:
        for B in bps[1:]:
            prof[B] = prof[lo]
        return prof
    if prof[lo] and not prof[hi]:
        for B in bps[1:]:                       # contradicts T2: judge everything
            prof[B] = check(t, M, changed, B, mode, **kw).ok
        return prof
    # find the smallest breakpoint that is feasible
    sizes = bps[1:]
    a, b = 0, len(sizes) - 1
    first = len(sizes)
    while a <= b:
        m = (a + b) // 2
        ok = check(t, M, changed, sizes[m], mode, **kw).ok
        prof[sizes[m]] = ok
        if ok:
            first = m
            b = m - 1
        else:
            a = m + 1
    for i, B in enumerate(sizes):
        prof.setdefault(B, i >= first)
    return prof


def profile_summary(prof: Dict[Optional[int], bool]) -> Tuple[Optional[int], bool, str]:
    """(min feasible B or None, feasible at inf, human string)."""
    keys = [k for k in prof if k is not None]
    minB = next((B for B in sorted(keys) if prof[B]), None)
    at_inf = prof.get(None, False)
    if minB is None and not at_inf:
        return None, False, "never"
    if minB is None:
        return None, True, "only with infinite buffering"
    later = [B for B in sorted(keys) if B > minB and not prof[B]]
    if not later and at_inf and all(prof[k] for k in keys):
        return minB, True, "any B"
    if not later and at_inf:
        return minB, True, f"B >= {minB}"
    return minB, at_inf, "B in " + ",".join(("inf" if B is None else str(B)) for B, ok in prof.items() if ok)
