"""Deadlocks contained in the horizon (docs/model.md, theorem T7).

An alternative's horizon H keeps, on each rank, the operations every program consistent
with the trace repeats under the alternative. A rank cut by rules 2-7 stops at an operation
it issues but cannot complete inside H; a rank cut by rule 1 stops because its continuation
is unknown. A set D of ranks is a *contained deadlock* if no rank of D is cut by rule 1, every
rank of D stops at an operation that no message or receive already present can complete, and
every rank that could still complete one of those operations is in D or has terminated. Such
a deadlock happens in every program consistent with the trace up to the operations at which
D stops, so it is a level-L1 claim and needs no single-path assumption.

Every cut descends from the changed receives, so a contained deadlock needs a changed rank
that stops *before* rule 1 cuts it: under rule H1 a nonblocking wildcard receive whose rank
then blocks on another operation before the wait. A blocking changed receive cuts its rank by
rule 1 right after it, and every rank that waits on that rank waits on an unknown
continuation, unless read bounds (stretto.reads, rule H1') keep the records the rank issues
before it can read the receive's result: the rank may then stop inside that window. This
holds from B0 on, where the report looks (proof appendix, the proposition on contained
deadlocks): below B0 a send that no receive accepts can block on its own.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Set, Tuple

from .horizon import _cidx, coll_can_complete
from .matching import send_blocks
from .trace import ANY, Key, Recv, Send, Trace


def _completed_at(t: Trace) -> Dict[Tuple[int, int], list]:
    """Operations by the record that completed them in the run (rank, idx)."""
    cache = getattr(t, "_completed_at", None)
    if cache is None:
        cache = {}
        for x in list(t.sends.values()) + list(t.recvs.values()) + list(t.colls):
            if x.completed:
                cache.setdefault((x.rank, _cidx(x)), []).append(x)
        for p in t.probes.values():
            if p.completed:
                cache.setdefault((p.rank, p.idx), []).append(p)
        t._completed_at = cache
    return cache


def contained_deadlock(t: Trace, M, changed: Set[Key], cut: Dict[int, int], issued: Dict[int, int],
                       B: Optional[int], rule1: Optional[Dict[int, int]] = None) -> Optional[Dict[int, str]]:
    """The ranks of a contained deadlock under M at B with horizon (cut, issued), each with the
    operation it stops at, or None. rule1: per rank, where rule 1 cut it, when read bounds
    moved the cut (stretto.reads.ReadBounds.rule1); right after the changed receive otherwise."""
    lengths = {rank: len(recs) for rank, recs in t.ranks.items()}
    if rule1 is None:
        # per rank, the earliest completion of its changed receives, as in rule 1 of the
        # horizon: a swap changes two receives of one rank, and its cut follows the first
        rule1 = {}
        for rk in changed:
            r = t.recvs[rk]
            rule1[r.rank] = min(rule1.get(r.rank, _cidx(r) + 1), _cidx(r) + 1)

    def in_h(rank: int, idx: int) -> bool:
        return idx < cut[rank]

    def sent(s: Send) -> bool:
        return in_h(s.rank, s.idx) or issued.get(s.rank) == s.idx

    recv_of = {sk: rk for rk, sk in M.items()}
    terminated = {rank for rank, n in lengths.items()
                  if cut[rank] == n and n and t.ranks[rank][-1].op == "finalize"}

    def senders(x) -> Set[int]:
        """Ranks that could still send a message x accepts."""
        if x.src != ANY:
            return {x.src}
        return set(t.comm_ranks.get(x.comm, sorted(t.ranks))) - {x.rank}

    def message_present(x) -> bool:
        """Is a message x accepts issued inside H and not taken there by M's receive?"""
        for src in ([x.src] if x.src != ANY else t.sources_to(x.rank, x.comm)):
            for s in t.sends_to(src, x.rank, x.comm):
                if not sent(s):
                    break
                if not x.accepts(s) or s.cancelled:
                    continue
                rk = recv_of.get(s.key)
                if rk is None or not in_h(rk[0], t.recvs[rk].idx):
                    return True
        return False

    def receive_present(s: Send) -> bool:
        """Is a receive that accepts s posted inside H and still waiting for a message?"""
        for x in t.recvs_of(s.dst):
            if not in_h(x.rank, x.idx):
                break
            if x.cancelled or not x.accepts(s):
                continue
            sk = M.get(x.key)
            if sk is None or not sent(t.sends[sk]):
                return True
        return False

    def needs(x) -> Optional[Set[int]]:
        """None if x can still complete from what H already holds; else the ranks whose
        future operations could complete it."""
        if isinstance(x, Recv):
            if x.mrecv:
                return None                      # its message is reserved by its probe
            sk = M.get(x.key)
            if sk is not None and sent(t.sends[sk]):
                return None
            return None if message_present(x) else senders(x)
        if isinstance(x, Send):
            if not send_blocks(x, B):
                return None
            rk = recv_of.get(x.key)
            if rk is not None and in_h(rk[0], t.recvs[rk].idx):
                return None
            return None if receive_present(x) else {x.dst}
        if hasattr(x, "seen"):                   # a probe: only a blocking one waits for a message
            if x.kind not in ("probe", "mprobe"):
                return None
            return None if message_present(x) else senders(x)
        if hasattr(x, "ordinal"):                # a collective
            group = [c for c in t.colls if c.comm == x.comm and c.ordinal == x.ordinal]
            have = {c.rank for c in group if in_h(c.rank, c.idx)}
            need = set(t.comm_members.get(x.comm, [c.rank for c in group]))
            pos = {r: i for i, r in enumerate(t.comm_ranks.get(x.comm, []))}
            if coll_can_complete(x, have, need, pos):
                return None
            return need - have
        return None

    stops: Dict[int, Tuple[str, Set[int]]] = {}
    for rank, n in lengths.items():
        c = cut[rank]
        if c >= n or (rank in rule1 and c >= rule1[rank]):
            continue                             # whole trace kept, or the continuation is unknown
        rec = t.ranks[rank][c]
        if rec.op not in ("send", "recv", "coll", "probe", "wait"):
            continue
        kind = rec.raw.get("kind") if rec.op == "wait" else None
        if kind is not None and (kind.startswith("test") or kind == "getstatus"):
            continue                             # a test or status query returns at once: the rank does not stop there
        if rec.op == "probe" and rec.raw.get("kind") in ("iprobe", "improbe"):
            continue
        if kind in ("waitany", "waitsome"):
            # it waits for any of its requests, not only the one that completed in the run
            ops = [x for q in rec.raw.get("reqs", []) if q is not None and q >= 0
                   for x in (t.recv_by_req(rank, q), t._by_req.get(("s", rank, q))) if x is not None]
        else:
            ops = _completed_at(t).get((rank, c), [])
        if not ops:
            continue
        reqs = [needs(x) for x in ops]
        if kind in ("waitany", "waitsome"):
            if any(r is None for r in reqs):
                continue                         # one of them can complete
            partners = set().union(*reqs)
        else:
            blocked = [r for r in reqs if r is not None]
            if not blocked:
                continue
            partners = set().union(*blocked)
        stops[rank] = (f"{rec.op} #{rec.idx}", partners)

    D = set(stops)
    while True:
        drop = {r for r in D if not stops[r][1] <= D | terminated}
        if not drop:
            break
        D -= drop
    if not D:
        return None
    return {r: stops[r][0] for r in sorted(D)}
