"""The predictive horizon as a causal closure.

Given an alternative matching M' and the set of receives (and probes) whose match
changed, compute per rank the cut index: events with idx < cut[rank] are still
determined by the trace. Rules, iterated to a fixpoint:
  1. a changed receive keeps its completion; everything after it in its rank is cut
     (its payload and metadata differ, so the continuation is unknown). A swap of two
     receives on one rank therefore cuts at the *earlier* one: the second re-match is
     beyond the L1 horizon by definition and is certified only at L1+SP or L2. With read
     bounds (stretto.reads, docs/firstread.md) the cut moves to the receive's bound, the
     first record the program may issue after reading its result (rule H1');
  2. a receive that completed in the trace but whose M' send is cut, or which has no M'
     send, never completes: cut at its completion (a receive that never completed in the
     trace imposes nothing);
  3. a send whose request completes only when matched (rendezvous, ssend) and whose M'
     receive is cut or absent never completes: cut at its completion. The receive counts
     as present when it is *posted* inside the kept prefix: MPI matches a rendezvous send
     against a posted receive whether or not its wait has happened;
  4. a collective completes for a member only if the members its shape requires have
     issued it inside the kept prefix (matching.coll_shape: everyone for barrier-like ones,
     the root for bcast-like ones, everyone for the root of a reduce-like one and nobody for
     its other members, the members before it for a scan); otherwise its completion is cut;
  5. a probe whose observed send is cut (never issued inside the kept prefix) observes
     something else: cut at the probe;
  6. a receive posted inside the prefix whose M' send is not issued there stays pending, so
     a later receive or probe of its rank cannot take or report a message it accepts: cut
     there (the standard's second non-overtaking clause);
  7. a ready-mode send whose M' receive is not posted inside the prefix is erroneous: cut at
     the send.
mode='L1' applies all rules; mode='SP' (single-path assumption, MOPPER's setting)
skips rule 1: the continuation is assumed identical to the trace. Under that assumption a
lowering by rules 2-7 is not an unknown continuation but a rank that never completes the
operation, i.e. a hang: with hangs={} given, compute_cut records one per rank.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Set, Tuple

from .matching import coll_shape, send_blocks
from bisect import bisect_right

from .trace import ANY, Key, Recv, Trace


def coll_can_complete(c, issued: Set[int], need: Set[int], pos: Dict[int, int]) -> bool:
    """Can collective record c complete when `issued` are the members whose issue is kept?
    By the shape of the collective; a root-shaped collective without a known root, or an
    unknown collective, needs everyone."""
    shape, _ = coll_shape(c.name)
    if c.root is None and shape in ("root-out", "root-in"):
        shape = "all"
    if shape == "all":
        return issued >= need
    if shape == "root-out":
        return c.rank == c.root or c.root in issued
    if shape == "root-in":
        return c.rank != c.root or issued >= need
    return all(r in issued for r in need if pos.get(r, 0) < pos.get(c.rank, 0))   # prefix

def _cidx(x) -> int:
    return x.complete_idx if x.complete_idx is not None else x.idx


def compute_cut(t: Trace, M: Dict[Key, Key], changed: Set[Key], B: Optional[int], mode: str = "L1",
                changed_probes: Set[Key] = frozenset(), hangs: Optional[Dict[int, str]] = None,
                changed_events: Set[Tuple[int, int]] = frozenset(),
                issued: Optional[Dict[int, int]] = None, probe_alt: Optional[Dict[Key, Key]] = None,
                bounds=None) -> Dict[int, int]:
    """The horizon closure as a worklist: every rule is evaluated once at the start and then
    only where an input of its condition changed, which happens when a record loses its
    issue (each record does so at most once, since cuts only decrease). The result is the
    greatest fixpoint of the rules, the one compute_cut_fixpoint reaches by rescanning.
    Cost O((n + |M|) log n) per call: the bisections of rule 6.

    changed_events: (rank, idx) of records whose outcome changes without being a receive
    or a probe, such as a Waitany completing another index; the horizon cuts after them.
    issued, if given, receives rank -> idx of a blocking send that rule 3 cut at its own
    record: the rank never gets past it, but the send *is* issued (a probe can see it).
    probe_alt maps a changed probe to the send it reports instead of the observed one.
    bounds (stretto.reads): rule 1 cuts the rank of a changed receive at the receive's read
    bound instead of right after its completion (H1', docs/firstread.md); probes and events
    keep rule H1."""
    cut: Dict[int, int] = {rank: len(recs) for rank, recs in t.ranks.items()}
    if issued is None:
        issued = {}
    work: List[Tuple[int, int, int, bool]] = []     # (rank, lo, hi, issue kept at lo)

    def lower(rank: int, idx: int, why=None, issue_kept: bool = False) -> None:
        """Cut rank at idx; issue_kept keeps the issue of the record at idx (a blocking send
        that never completes). States are ordered (idx, issue removed) < (idx, issue kept) <
        (idx + 1, ...), so a rule that removes the issue at the current cut still applies."""
        old = cut[rank]
        old_issued = issued.get(rank) == old
        if idx == old and old_issued and not issue_kept:
            issued.pop(rank, None)
            if hangs is not None and mode == "SP" and rank not in hangs:
                hangs[rank] = why() if callable(why) else (why or "")
            work.append((rank, idx, idx + 1, False))
            return
        if idx >= old:
            return
        cut[rank] = idx
        if issue_kept:
            issued[rank] = idx
        else:
            issued.pop(rank, None)
        if hangs is not None and mode == "SP" and rank not in hangs:
            hangs[rank] = why() if callable(why) else (why or "")
        work.append((rank, idx, old + 1 if old_issued else old, issue_kept))

    def kept(rank: int, idx: int) -> bool:
        return idx < cut[rank]

    recv_of = {sk: rk for rk, sk in M.items()}
    probe_alt = probe_alt or {}
    # rule 6 index: by (rank, comm, source or ANY, tag or ANY) of the message each receive
    # takes under M and each probe reports, the posting indexes, so that a pending receive
    # finds the first later receive or probe it deprives with one bisection
    comp: Dict[Tuple[int, int, int, int], List[int]] = {}
    for rk, sk in M.items():
        x, m = t.recvs[rk], t.sends[sk]
        for src in (ANY, m.rank):
            for tag in (ANY, m.tag):
                comp.setdefault((x.rank, x.comm, src, tag), []).append(x.idx)
    probes_seeing: Dict[Key, List] = {}
    for p in t.probes.values():
        sk = probe_alt.get(p.key, p.seen)
        if p.completed and p.flag and sk is not None:
            m = t.sends[sk]
            for src in (ANY, m.rank):
                for tag in (ANY, m.tag):
                    comp.setdefault((p.rank, p.comm, src, tag), []).append(p.idx)
        if p.key not in changed_probes and p.completed and p.flag and p.seen is not None:
            probes_seeing.setdefault(p.seen, []).append(p)
    for v in comp.values():
        v.sort()
    send_at = {(s.rank, s.idx): s for s in t.sends.values()}
    recv_at = {(r.rank, r.idx): r for r in t.recvs.values()}
    coll_at = {(c.rank, c.idx): c for c in t.colls}
    sets = {(cs[0].comm, cs[0].ordinal): cs for cs in t.coll_sets()}

    def rule2(r: Recv) -> None:          # a receive with no message does not complete
        if r.completed and kept(r.rank, _cidx(r)):
            lower(r.rank, _cidx(r), lambda: f"{r} never completes: no send for it")

    def rule6(r: Recv) -> None:          # a pending receive deprives a later receive or probe
        if not r.completed or not kept(r.rank, r.idx):
            return
        lst = comp.get((r.rank, r.comm, r.src, r.tag))
        if lst:
            j = bisect_right(lst, r.idx)
            if j < len(lst) and lst[j] < cut[r.rank]:
                lower(r.rank, lst[j], lambda: f"the receive or probe at {r.rank}#{lst[j]} is deprived: {r}, posted before it and still pending, takes the message")

    def rule3(s, rk) -> None:            # a rendezvous send with no posted receive does not complete
        if s.completed and send_blocks(s, B) and kept(s.rank, _cidx(s)) and (rk is None or not kept(rk[0], t.recvs[rk].idx)):
            lower(s.rank, _cidx(s), lambda: f"{s} never completes: rendezvous with no posted receive", _cidx(s) == s.idx)

    def rule7(s, rk) -> None:            # a ready send needs its receive posted
        issued_now = kept(s.rank, s.idx) or issued.get(s.rank) == s.idx
        if s.ready_mode and issued_now and (rk is None or not kept(rk[0], t.recvs[rk].idx)):
            lower(s.rank, s.idx, lambda: f"{s} is a ready send whose receive is never posted")

    def rule4(cs) -> None:               # a collective completes when its shape's members issue it
        need = set(t.comm_members.get(cs[0].comm, [c.rank for c in cs]))
        have = {c.rank for c in cs if kept(c.rank, c.idx)}
        if have >= need:
            return
        pos = {r: i for i, r in enumerate(t.comm_ranks.get(cs[0].comm, []))}
        for c in cs:
            if c.completed and kept(c.rank, _cidx(c)) and not coll_can_complete(c, have, need, pos):
                lower(c.rank, _cidx(c), lambda c=c: f"collective {c.name} #{c.ordinal} on comm {c.comm} never completes: a participant never reaches it")

    if mode == "L1":
        for rk in changed:
            r = t.recvs[rk]
            lower(r.rank, bounds.recv(t, r) if bounds is not None else _cidx(r) + 1)
        for pk in changed_probes:
            p = t.probes[pk]
            lower(p.rank, p.idx + 1)
        for rank, idx in changed_events:
            lower(rank, idx + 1)
    for r in t.recvs.values():
        if r.key not in M:
            rule2(r)
            rule6(r)
    for s in t.sends.values():
        rk = recv_of.get(s.key)
        if rk is None:
            rule3(s, None)
            rule7(s, None)
    for cs in sets.values():
        rule4(cs)
    while work:
        rank, lo, hi, kept_issue = work.pop()
        for idx in range(lo, hi):
            s = send_at.get((rank, idx))
            if s is not None:
                rk = recv_of.get(s.key)
                if rk is not None:
                    rule2(t.recvs[rk])                       # rule 2 asks for the send's record
                    if not (idx == lo and kept_issue):
                        rule6(t.recvs[rk])                   # rule 6 for its issue
                for p in probes_seeing.get(s.key, ()):        # rule 5: the probe saw a message now gone
                    if idx == lo and kept_issue:
                        break                                # the send's call is kept: still issued
                    if kept(p.rank, p.idx):
                        lower(p.rank, p.idx, lambda p=p, s=s: f"{p} observes another message: {s} is never issued")
            x = recv_at.get((rank, idx))
            if x is not None:
                sk = M.get(x.key)
                if sk is not None:
                    rule3(t.sends[sk], x.key)
                    rule7(t.sends[sk], x.key)
            c = coll_at.get((rank, idx))
            if c is not None:
                rule4(sets[(c.comm, c.ordinal)])
    return cut


def compute_cut_fixpoint(t: Trace, M: Dict[Key, Key], changed: Set[Key], B: Optional[int], mode: str = "L1",
                changed_probes: Set[Key] = frozenset(), hangs: Optional[Dict[int, str]] = None,
                changed_events: Set[Tuple[int, int]] = frozenset(),
                issued: Optional[Dict[int, int]] = None, probe_alt: Optional[Dict[Key, Key]] = None,
                bounds=None) -> Dict[int, int]:
    """The closure by iterating every rule over the whole trace until nothing moves: the
    reference for compute_cut, which reaches the same fixpoint with a worklist (the tests
    compare the two). changed_events: (rank, idx) of records whose outcome changes without being a receive
    or a probe, such as a Waitany completing another index; the horizon cuts after them.
    issued, if given, receives rank -> idx of a blocking send that rule 3 cut at its own
    record: the rank never gets past it, but the send *is* issued (a probe can see it).
    probe_alt maps a changed probe to the send it reports instead of the observed one.
    bounds: read bounds for rule 1, as in compute_cut."""
    cut: Dict[int, int] = {rank: len(recs) for rank, recs in t.ranks.items()}
    if issued is None:
        issued = {}

    def lower(rank: int, idx: int, why: str = "", issue_kept: bool = False) -> bool:
        if idx == cut[rank] and issued.get(rank) == idx and not issue_kept:
            issued.pop(rank, None)          # (idx, issue removed) is below (idx, issue kept)
            if hangs is not None and mode == "SP" and rank not in hangs:
                hangs[rank] = why
            return True
        if idx < cut[rank]:
            cut[rank] = idx
            if issue_kept:
                issued[rank] = idx
            else:
                issued.pop(rank, None)
            if hangs is not None and mode == "SP" and rank not in hangs:
                hangs[rank] = why
            return True
        return False

    if mode == "L1":
        for rk in changed:
            r = t.recvs[rk]
            lower(r.rank, bounds.recv(t, r) if bounds is not None else _cidx(r) + 1)
        for pk in changed_probes:
            p = t.probes[pk]
            lower(p.rank, p.idx + 1)
        for rank, idx in changed_events:
            lower(rank, idx + 1)

    def kept(rank: int, idx: int) -> bool:
        return idx < cut[rank]

    def sent(sk: Optional[Key]) -> bool:
        """Is the send issued inside the kept prefix (kept, or a blocking send cut at its own record)?"""
        if sk is None:
            return False
        s = t.sends[sk]
        return kept(s.rank, s.idx) or issued.get(s.rank) == s.idx

    recv_of = {sk: rk for rk, sk in M.items()}
    recvs_of: Dict[int, List[Recv]] = {}
    for r in sorted(t.recvs.values(), key=lambda r: (r.rank, r.idx)):
        recvs_of.setdefault(r.rank, []).append(r)
    probe_alt = probe_alt or {}
    probes_of: Dict[int, list] = {}      # probes that report a message, with the message
    for p in sorted(t.probes.values(), key=lambda p: (p.rank, p.idx)):
        sk = probe_alt.get(p.key, p.seen)
        if p.completed and p.flag and sk is not None:
            probes_of.setdefault(p.rank, []).append((p, t.sends[sk]))
    ready = [s for s in t.sends.values() if s.ready_mode]
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
                moved |= lower(r.rank, cidx, f"{r} never completes: no send for it")
        # rule 6: a receive posted inside the prefix whose message is not issued there stays
        # pending through the prefix, so a later receive of its rank cannot take a message it
        # accepts (the standard's second non-overtaking clause)
        for rank, rs in recvs_of.items():
            for i, r in enumerate(rs):
                if not kept(rank, r.idx):
                    break
                if not r.completed or sent(M.get(r.key)):
                    continue
                for r2 in rs[i + 1:]:
                    if not kept(rank, r2.idx):
                        break
                    sk2 = M.get(r2.key)
                    if sk2 is not None and r.accepts(t.sends[sk2]):
                        moved |= lower(rank, r2.idx, f"{r2} never gets {t.sends[sk2]}: {r}, posted before it and still pending, takes the message")
                        break
                for p, sp in probes_of.get(rank, ()):
                    if p.idx <= r.idx:
                        continue
                    if not kept(rank, p.idx):
                        break
                    if r.accepts(sp):
                        moved |= lower(rank, p.idx, f"{p} never reports {sp}: {r}, posted before it and still pending, takes the message")
                        break
        # rule 7: a ready send needs its receive posted
        for s in ready:
            if not kept(s.rank, s.idx) and issued.get(s.rank) != s.idx:
                continue
            rk = recv_of.get(s.key)
            if rk is None or not kept(t.recvs[rk].rank, t.recvs[rk].idx):
                moved |= lower(s.rank, s.idx, f"{s} is a ready send whose receive is never posted")
        for s in t.sends.values():
            if not s.completed or not send_blocks(s, B):
                continue
            cidx = _cidx(s)
            if not kept(s.rank, cidx):
                continue
            rk = recv_of.get(s.key)
            if rk is None or not kept(t.recvs[rk].rank, t.recvs[rk].idx):
                moved |= lower(s.rank, cidx, f"{s} never completes: rendezvous with no posted receive", issue_kept=cidx == s.idx)
        for cs in t.coll_sets():
            need = set(t.comm_members.get(cs[0].comm, [c.rank for c in cs]))
            pos = {r: i for i, r in enumerate(t.comm_ranks.get(cs[0].comm, []))}
            have = {c.rank for c in cs if kept(c.rank, c.idx)}
            if have >= need:
                continue
            for c in cs:
                if c.completed and kept(c.rank, _cidx(c)) and not coll_can_complete(c, have, need, pos):
                    moved |= lower(c.rank, _cidx(c), f"collective {c.name} #{c.ordinal} on comm {c.comm} never completes: a participant never reaches it")
        for p in t.probes.values():
            if p.key in changed_probes or not p.completed or not p.flag or p.seen is None:
                continue
            if not kept(p.rank, p.idx):
                continue
            seen = t.sends[p.seen]
            if not kept(seen.rank, seen.idx) and issued.get(seen.rank) != seen.idx:
                moved |= lower(p.rank, p.idx, f"{p} observes another message: {seen} is never issued")
        if not moved:
            break
    return cut


def witness_kept(t: Trace, cut: Dict[int, int], changed: Set[Key], changed_probes: Set[Key] = frozenset(),
                 changed_events: Set[Tuple[int, int]] = frozenset(), M: Optional[Dict[Key, Key]] = None,
                 issued: Optional[Dict[int, int]] = None) -> bool:
    """The alternative is meaningful only if every changed receive is matched inside the
    kept prefix: a blocking receive must complete there; a nonblocking one must be posted
    there with its new message issued there (the match is decided then, whether or not the
    wait that completes it is kept)."""
    for rank, idx in changed_events:
        if idx >= cut[rank]:
            return False
    for rk in changed:
        r = t.recvs[rk]
        if r.idx >= cut[r.rank]:
            return False
        if r.completed and r.blocking and _cidx(r) >= cut[r.rank]:
            return False
        if not r.blocking and M is not None and rk in M:
            s = t.sends[M[rk]]
            if s.idx >= cut[s.rank] and (issued is None or issued.get(s.rank) != s.idx):
                return False
    for pk in changed_probes:
        if t.probes[pk].idx >= cut[t.probes[pk].rank]:
            return False
    return True
