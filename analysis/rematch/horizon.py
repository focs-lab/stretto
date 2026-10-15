"""The predictive horizon as a causal closure.

Given an alternative matching M' and the set of receives (and probes) whose match
changed, compute per rank the cut index: events with idx < cut[rank] are still
determined by the trace. Rules, iterated to a fixpoint:
  1. a changed receive keeps its completion; everything after it in its rank is cut
     (its payload and metadata differ, so the continuation is unknown). A swap of two
     receives on one rank therefore cuts at the *earlier* one: the second re-match is
     beyond the L1 horizon by definition and is certified only at L1+SP or L2;
  2. a receive that completed in the trace but whose M' send is cut, or which has no M'
     send, never completes: cut at its completion (a receive that never completed in the
     trace imposes nothing);
  3. a send whose request completes only when matched (rendezvous, ssend) and whose M'
     receive is cut or absent never completes: cut at its completion;
  4. a collective with any participant cut never completes for anyone.
mode='L1' applies all rules; mode='SP' (single-path assumption, MOPPER's setting)
skips rule 1: the continuation is assumed identical to the trace.
"""
from __future__ import annotations
from typing import Dict, Optional, Set

from .matching import send_blocks
from .trace import Key, Trace

INF = 10**9


def _cidx(x) -> int:
    return x.complete_idx if x.complete_idx is not None else x.idx


def compute_cut(t: Trace, M: Dict[Key, Key], changed: Set[Key], B: Optional[int], mode: str = "L1",
                changed_probes: Set[Key] = frozenset()) -> Dict[int, int]:
    cut: Dict[int, int] = {rank: len(recs) for rank, recs in t.ranks.items()}

    def lower(rank: int, idx: int) -> bool:
        if idx < cut[rank]:
            cut[rank] = idx
            return True
        return False

    if mode == "L1":
        for rk in changed:
            r = t.recvs[rk]
            lower(r.rank, _cidx(r) + 1)
        for pk in changed_probes:
            p = t.probes[pk]
            lower(p.rank, p.idx + 1)

    def kept(rank: int, idx: int) -> bool:
        return idx < cut[rank]

    recv_of = {sk: rk for rk, sk in M.items()}
    while True:
        moved = False
        for r in t.recvs.values():
            if not r.completed:
                continue
            cidx = _cidx(r)
            if not kept(r.rank, cidx):
                continue
            sk = M.get(r.key)
            if sk is None or not kept(t.sends[sk].rank, t.sends[sk].idx):
                moved |= lower(r.rank, cidx)
        for s in t.sends.values():
            if not s.completed or not send_blocks(s, B):
                continue
            cidx = _cidx(s)
            if not kept(s.rank, cidx):
                continue
            rk = recv_of.get(s.key)
            if rk is None:
                moved |= lower(s.rank, cidx)
            else:
                r = t.recvs[rk]
                if not r.completed or not kept(r.rank, _cidx(r)):
                    moved |= lower(s.rank, cidx)
        for cs in t.coll_sets():
            if any(not kept(c.rank, c.idx) for c in cs):
                for c in cs:
                    if c.completed and kept(c.rank, _cidx(c)):
                        moved |= lower(c.rank, _cidx(c))
        if not moved:
            break
    return cut


def witness_kept(t: Trace, cut: Dict[int, int], changed: Set[Key], changed_probes: Set[Key] = frozenset()) -> bool:
    """The alternative is meaningful only if every changed receive still completes."""
    for rk in changed:
        r = t.recvs[rk]
        if r.completed and _cidx(r) >= cut[r.rank]:
            return False
        if not r.completed and r.idx >= cut[r.rank]:
            return False
    for pk in changed_probes:
        if t.probes[pk].idx >= cut[t.probes[pk].rank]:
            return False
    return True
