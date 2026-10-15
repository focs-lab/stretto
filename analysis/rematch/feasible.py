"""Feasibility of an alternative matching: horizon closure + matching-order checks +
acyclicity of the constraint graph over the kept prefix."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .horizon import compute_cut, witness_kept
from .matching import Alternative, affected_ranks, build_graph, matching_order_ok
from .trace import Key, Trace


@dataclass
class Verdict:
    feasible: bool
    reason: str
    cut: Optional[Dict[int, int]] = None
    cycle: Optional[list] = None


def check(t: Trace, M: Dict[Key, Key], changed, B: Optional[int], mode: str = "L1",
          extra_edges: Iterable = (), changed_probes: Set[Key] = frozenset(), strong_only: bool = False) -> Verdict:
    seen = {}
    for rk, sk in M.items():
        if rk not in t.recvs or sk not in t.sends:
            return Verdict(False, f"unknown receive or send in matching: {rk} <- {sk}")
        if not t.recvs[rk].accepts(t.sends[sk]):
            return Verdict(False, f"{t.recvs[rk]} does not accept {t.sends[sk]}")
        if sk in seen:
            return Verdict(False, f"send {t.sends[sk]} consumed twice")
        seen[sk] = rk
    cut = compute_cut(t, M, set(changed), B, mode, changed_probes)
    if not witness_kept(t, cut, changed, changed_probes):
        # in L1 the horizon cut it; in SP a receive that can never complete precedes it
        return Verdict(False, "witness cut by the horizon closure" if mode == "L1" else
                       "witness unreachable: an earlier receive on its rank can never complete", cut)

    def kept_recv(rk: Key) -> bool:      # posting index: matching order is decided at posting
        r = t.recvs[rk]
        return r.idx < cut[r.rank]

    why = matching_order_ok(t, M, kept_recv, affected_ranks(t, M, changed) if changed or M != t.observed else set())
    if why:
        return Verdict(False, why, cut)
    g = build_graph(t, M, cut, B, extra_edges, strong_only)
    cyc = g.find_cycle()
    if cyc:
        return Verdict(False, "constraint cycle", cut, cyc)
    return Verdict(True, "ok", cut)


def check_alternative(t: Trace, alt: Alternative, B: Optional[int], mode: str = "L1") -> Verdict:
    return check(t, alt.M, alt.changed, B, mode)


def observed_feasible_at(t: Trace, B: Optional[int], strong_only: bool = True) -> Verdict:
    """Is the observed matching itself realisable under buffering threshold B? A cycle
    (over guaranteed edges only) means: with eager limit <= B the same run deadlocks."""
    return check(t, t.observed, set(), B, mode="SP", strong_only=strong_only)


def breakpoints(t: Trace) -> List[int]:
    return [0] + t.message_sizes()


def feasible_profile(t: Trace, M: Dict[Key, Key], changed, mode: str = "L1", full: bool = False, **kw) -> Dict[Optional[int], bool]:
    """Feasibility at the breakpoints {0} U message sizes U {inf}. By default the ends are
    checked and, if they differ, the sizes are bisected (feasibility is monotone in B for
    the constraint graph; the horizon closure only grows with B); full=True checks every
    breakpoint, which is what the monotonicity tests use."""
    bps = breakpoints(t)
    prof: Dict[Optional[int], bool] = {}
    if full or len(bps) <= 3:
        for B in bps + [None]:
            prof[B] = check(t, M, changed, B, mode, **kw).feasible
        return prof
    lo, hi = bps[0], None
    prof[lo] = check(t, M, changed, lo, mode, **kw).feasible
    prof[hi] = check(t, M, changed, hi, mode, **kw).feasible
    if prof[lo] == prof[hi]:
        for B in bps[1:]:
            prof[B] = prof[lo]
        return prof
    # find the smallest breakpoint that is feasible
    sizes = bps[1:]
    a, b = 0, len(sizes) - 1
    first = len(sizes)
    while a <= b:
        m = (a + b) // 2
        ok = check(t, M, changed, sizes[m], mode, **kw).feasible
        prof[sizes[m]] = ok
        if ok:
            first = m
            b = m - 1
        else:
            a = m + 1
    for i, B in enumerate(sizes):
        prof.setdefault(B, i >= first)
    return prof


def min_feasible_B(t: Trace, M: Dict[Key, Key], changed, mode: str = "L1", **kw) -> Optional[int]:
    """Smallest threshold at which M is feasible; None if never. Uses the full profile
    rather than assuming monotonicity (see docs/model.md)."""
    prof = feasible_profile(t, M, changed, mode, **kw)
    for B in breakpoints(t):
        if prof[B]:
            return B
    return None


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
    if minB == 0 and not later and at_inf:
        return 0, True, "any B"
    if not later and at_inf:
        return minB, True, f"B >= {minB}"
    return minB, at_inf, "B in " + ",".join(("inf" if B is None else str(B)) for B, ok in prof.items() if ok)
