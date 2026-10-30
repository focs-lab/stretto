"""The analysis of traces whose ranks have several chains (docs/threads.md, section 3).

A chain plays the role of a rank in program order, and "posted before" between two
operations of one process becomes happens-before (stretto.chains.HB): on one chain the record
order, between chains the recorded synchronisation. Two operations of a process that neither
precedes are concurrent: neither non-overtaking clause orders them, and the alternatives here
are those that thread scheduling produces on an implementation that matches in the physical
order of postings and issues. For each concurrent pair whose order decides a match, the
constraint graph gets one orientation (orientation_edges); a pick adds constraints, so an
accepted alternative is realisable, but the other pick might accept an alternative this one
rejects (section 3.6 of docs/threads.md: the check is incomplete there).

Nothing here runs on a trace in which every rank has one chain (Trace.multi is false).
"""
from __future__ import annotations
from bisect import bisect_left, bisect_right
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .chains import physical_key
from .trace import ANY, Key, Probe, Recv, Send, Trace


# ------------------------------------------------------------------------------ order

def before(t: Trace, a, b) -> bool:
    """Does operation a return before operation b is called (same process)?"""
    if t.hb is None:
        return a.rank == b.rank and a.idx < b.idx
    return t.hb.before(a.rank, a.idx, b.rank, b.idx)


def concurrent(t: Trace, a, b) -> bool:
    return a.rank != b.rank and not before(t, a, b) and not before(t, b, a)


def window(t: Trace, x, chain: int) -> Tuple[int, int]:
    """The records of chain that are concurrent with record x of another chain of its process:
    those with index in (lo, hi)."""
    n = len(t.ranks.get(chain, ()))
    lo = t.hb.last_before(x.rank, x.idx, chain)
    hi = t.hb.first_after(x.rank, x.idx, chain, n)
    return lo, hi


def _latest(t: Trace, key, limit: int):
    idx = t._recv_class_idx.get(key)
    if not idx:
        return None
    j = bisect_right(idx, limit) - 1
    return t._recv_class[key][j] if j >= 0 else None


def earlier_competitors(t: Trace, r, s: Send) -> List[Recv]:
    """Trace.earlier_competitors for a receive or probe r of a split rank: per chain and
    envelope class that accepts s, the latest receive that precedes r."""
    out = []
    for c in t.chains_of(r.proc):
        limit = r.idx - 1 if c == r.rank else t.hb.last_before(r.rank, r.idx, c)
        if limit < 0:
            continue
        for src in (s.proc, ANY):
            for tag in (s.tag, ANY):
                x = _latest(t, (c, r.comm, src, tag), limit)
                if x is not None:
                    out.append(x)
    return out


def earlier_sends(t: Trace, r, s: Send) -> List[Send]:
    """Trace.earlier_sends for a send s of a split rank: per chain of the sender and tag r
    accepts, the latest send of s's channel that precedes s and that r could take."""
    out = []
    for c in t.chains_of(s.proc):
        limit = s.idx - 1 if c == s.rank else t.hb.last_before(s.rank, s.idx, c)
        if limit < 0:
            continue
        q = (c, s.dst, s.comm)
        tags = (r.tag,) if r.tag != ANY else t._tags_on.get(q, ())
        for tag in tags:
            key = q + (tag,)
            idx = t._send_tag_idx.get(key)
            if not idx:
                continue
            j = bisect_right(idx, limit) - 1
            sends = t._send_tag[key]
            while j >= 0 and not sends[j].available_to(r):
                j -= 1
            if j >= 0:
                out.append(sends[j])
    return out


def send_before(t: Trace, a: Send, b: Send) -> bool:
    return a.proc == b.proc and before(t, a, b)


def same_channel(a: Send, b: Send) -> bool:
    return a.proc == b.proc and a.dst == b.dst and a.comm == b.comm


# ----------------------------------------------------------------------- matching order

def matching_order_ok(t: Trace, M, kept_recv, ranks: Optional[Set[int]] = None,
                      kept_probe=None, probe_alt: Optional[Dict[Key, Key]] = None) -> Optional[str]:
    """matching.matching_order_ok with "posted before" read as happens-before. Concurrent
    operations carry no obligation here: their order is the orientation edges' business."""
    matched_send: Dict[Key, Key] = {sk: rk for rk, sk in M.items()}
    first_unmatched: Dict[Tuple[int, int, int, int], Recv] = {}
    for r in t.recvs.values():
        if r.key not in M and not r.cancelled and not r.mrecv and (ranks is None or r.rank in ranks):
            k = (r.rank, r.comm, r.src, r.tag)
            if k not in first_unmatched or r.idx < first_unmatched[k].idx:
                first_unmatched[k] = r

    def unmatched_before(x, s: Send) -> bool:
        for c in t.chains_of(x.proc):
            for src in (s.proc, ANY):
                for tag in (s.tag, ANY):
                    u = first_unmatched.get((c, x.comm, src, tag))
                    if u is not None and before(t, u, x):
                        return True
        return False

    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        if ranks is not None and r.rank not in ranks:
            continue
        if not kept_recv(rk):
            continue
        if not r.accepts(s):
            return f"{r} does not accept {s}"
        for s2 in t.earlier_sends(r, s):
            r2k = matched_send.get(s2.key)
            # its receive takes it first only if posted inside the horizon: on another chain, after
            # its own cut, it is not posted, and the message waits for r
            if r2k is None or not kept_recv(r2k) or before(t, r, t.recvs[r2k]):
                return f"non-overtaking: {s2} would reach {r} before {s}"
        if unmatched_before(r, s):
            return f"receiver order: an earlier receive of rank {r.proc} accepts {s} and has no message in this matching"
        for r2 in t.earlier_competitors(r, s):
            if r2.key not in M:
                continue
            s2 = t.sends[M[r2.key]]
            if same_channel(s2, s) and send_before(t, s, s2):
                return f"receiver order: earlier {r2} would take {s} before {s2}"
    if kept_probe is not None:
        probe_alt = probe_alt or {}
        for q in t.probes.values():
            if (ranks is not None and q.rank not in ranks) or not q.completed or not (q.flag or q.key in probe_alt) \
                    or not kept_probe(q):
                continue
            sk = probe_alt.get(q.key, q.seen)
            if sk is None:
                continue
            s = t.sends[sk]
            for s2 in t.earlier_sends(q, s):
                r2k = matched_send.get(s2.key)
                if r2k is None or not kept_recv(r2k) or before(t, q, t.recvs[r2k]):
                    return f"probe order: {s2} would be reported by {q} before {s}"
            if unmatched_before(q, s):
                return f"probe order: an earlier receive of rank {q.proc} accepts {s} and has no message in this matching"
            rk = matched_send.get(sk)
            if rk is not None and t.recvs[rk].proc == q.proc and before(t, t.recvs[rk], q) and not t.recvs[rk].mrecv:
                return f"probe order: {t.recvs[rk]}, posted before {q}, takes {s}"
    return None


class OrderFilter:
    """The matching-order obligations of the changed receives of a single-change alternative,
    judged without its horizon (matching_order_ok judges every receive of the horizon). A
    changed receive lies in the horizon whenever its witness does (feasible.check rejects the
    alternative before matching order otherwise), and each obligation below only grows when
    rule 6 deprives receives of their message: a send whose receive is deprived has none,
    and a deprived competitor is a receive without a message posted before. So an alternative
    that violates one is rejected by the generic check at every threshold (with this reason
    or an earlier one), and the filter costs a few lookups per alternative instead of a
    closure and a graph."""

    def __init__(self, t: Trace):
        self.t = t
        self.sender_of: Dict[Key, Key] = {sk: rk for rk, sk in t.observed.items()}
        self.order = {rk: i for i, rk in enumerate(t.observed)}     # the generic check's order of the pairs
        # receives without a message in the run, per (chain, comm, source or ANY, tag or ANY), by index
        self.unmatched: Dict[Tuple[int, int, int, int], List[Recv]] = {}
        for r in sorted(t.recvs.values(), key=lambda r: r.idx):
            if r.key not in t.observed and not r.cancelled and not r.mrecv:
                self.unmatched.setdefault((r.rank, r.comm, r.src, r.tag), []).append(r)

    def violation(self, alt) -> Optional[str]:
        """The reason the generic check gives for the first changed receive, in the order in
        which it judges them, that breaks an obligation; None if none does."""
        t, M = self.t, alt.M
        changed = dict(getattr(M, "changed", {}))
        removed = set(getattr(M, "removed", ()))
        receiver = {sk: rk for rk, sk in changed.items()}

        def receiver_of(sk: Key) -> Optional[Key]:
            if sk in receiver:
                return receiver[sk]
            rk = self.sender_of.get(sk)
            return None if rk is None or rk in changed or rk in removed else rk

        def first_unmatched_before(x, s: Send) -> bool:
            for c in t.chains_of(x.proc):
                for src in (s.proc, ANY):
                    for tag in (s.tag, ANY):
                        key = (c, x.comm, src, tag)
                        u = next((u for u in self.unmatched.get(key, ()) if u.key not in changed), None)
                        if u is not None and before(t, u, x):
                            return True
            for uk in removed:
                u = t.recvs[uk]
                if not u.cancelled and not u.mrecv and u.proc == x.proc and u.accepts(s) and u.comm == x.comm \
                        and before(t, u, x):
                    return True
            return False

        order = self.order
        for rk in sorted(changed, key=lambda k: order.get(k, len(order))):
            r, s = t.recvs[rk], t.sends[changed[rk]]
            for s2 in t.earlier_sends(r, s):
                r2k = receiver_of(s2.key)
                if r2k is None or before(t, r, t.recvs[r2k]):
                    return f"non-overtaking: {s2} would reach {r} before {s}"
            if first_unmatched_before(r, s):
                return f"receiver order: an earlier receive of rank {r.proc} accepts {s} and has no message in this matching"
            for r2 in t.earlier_competitors(r, s):
                if r2.key in removed or (r2.key not in changed and r2.key not in t.observed):
                    continue
                s2 = t.sends[M[r2.key]]
                if same_channel(s2, s) and send_before(t, s, s2):
                    return f"receiver order: earlier {r2} would take {s} before {s2}"
        # the orientation edges (thread_edges): y, posted before r on r's chain in r's envelope
        # class, takes a message of s's channel that a concurrent thread issued after s. The
        # pair of r orders s before y's message (y is matched later), the pair of y orders y's
        # message before s (r is posted later): a cycle of two issue nodes. Without y's
        # message in the horizon, y is a receive left pending before r, and rule 6 or the
        # receiver order removes r. Rejected at every threshold
        for rk in sorted(changed, key=lambda k: order.get(k, len(order))):
            r, s = t.recvs[rk], t.sends[changed[rk]]
            if s.proc not in t.chains:
                continue
            y = _latest(t, (r.rank, r.comm, r.src, r.tag), r.idx - 1)
            if y is None or not y.completed or y.key in removed or (y.key not in changed and y.key not in t.observed):
                continue
            sy = t.sends[M[y.key]]
            if same_channel(sy, s) and sy.rank != s.rank and physical_key(t, s) < physical_key(t, sy) \
                    and not send_before(t, s, sy) and not send_before(t, sy, s):
                return f"constraint cycle: {y}, posted before {r} on its thread, takes {sy}, which a concurrent thread issued after {s}"
        return None

    def probe_violation(self, p, s: Send) -> Optional[str]:
        """The same for a probe that reports s instead of what it saw, against the observed
        matching (feasible.check with probe_alt): an earlier message of s's channel that no
        receive before the probe takes, a receive before it that accepts s and has no message,
        or s taken by a receive posted before it. Rule 6 only removes messages from receives,
        so each holds whatever the horizon."""
        t = self.t
        for s2 in t.earlier_sends(p, s):
            r2k = self.sender_of.get(s2.key)
            if r2k is None or before(t, p, t.recvs[r2k]):
                return f"probe order: {s2} would be reported by {p} before {s}"
        for c in t.chains_of(p.proc):
            for src in (s.proc, ANY):
                for tag in (s.tag, ANY):
                    u = next(iter(self.unmatched.get((c, p.comm, src, tag), ())), None)
                    if u is not None and before(t, u, p):
                        return f"receiver order: earlier {u} accepts {s} and has no message in this matching"
        rk = self.sender_of.get(s.key)
        if rk is not None and t.recvs[rk].proc == p.proc and before(t, t.recvs[rk], p) and not t.recvs[rk].mrecv:
            return f"probe order: {t.recvs[rk]}, posted before {p}, takes {s}"
        return None


# ------------------------------------------------------------------- graph edges

def _recvs_in_window(t: Trace, x, s: Send, chain: int) -> Iterable[Recv]:
    """Receives of chain, concurrent with record x, whose envelope accepts s."""
    lo, hi = window(t, x, chain)
    if hi - lo <= 1:
        return
    for src in (s.proc, ANY):
        for tag in (s.tag, ANY):
            key = (chain, x.comm, src, tag)
            idx = t._recv_class_idx.get(key)
            if not idx:
                continue
            for j in range(bisect_right(idx, lo), bisect_left(idx, hi)):
                yield t._recv_class[key][j]


def _sends_in_window(t: Trace, s: Send, chain: int, r) -> Iterable[Send]:
    """Sends of s's channel on chain, concurrent with s, that r accepts."""
    lo, hi = window(t, s, chain)
    if hi - lo <= 1:
        return
    q = (chain, s.dst, s.comm)
    for tag in ((r.tag,) if r.tag != ANY else t._tags_on.get(q, ())):
        key = q + (tag,)
        idx = t._send_tag_idx.get(key)
        if not idx:
            continue
        for j in range(bisect_right(idx, lo), bisect_left(idx, hi)):
            x = t._send_tag[key][j]
            if r.accepts(x) and not x.cancelled:
                yield x


class _Reduced:
    """The orientation edges of one operation against the concurrent operations of one other
    chain, written out up to reachability. Those of the form a -> x (the operation goes first:
    an issue node before x's issue node) need only the earliest x of the chain, whose issue
    precedes the later ones' in program order. Those of the form m(x) -> b (x's match goes
    first) form runs along a list of the chain's operations (an envelope class of receives,
    or a tag of sends) in which consecutive members are linked by an edge between their
    match nodes that build_graph draws anyway (the competitor edge between two receives of
    one class, the sender-side order edge between the receives of two consecutive sends), so
    within a run only the last member needs its edge. On the 1 024-iteration MPICH mt_*
    traces the edges written out in full numbered 2 * 10^7 per graph."""
    __slots__ = ("first", "first_node", "edges")

    def __init__(self):
        self.first, self.first_node, self.edges = None, None, []

    def run(self, items, a, b) -> None:
        """items: one list of the chain in order, as (index, 'A' | 'B' | None, linked to the
        previous item, issue node, match node); 'A' asks for a -> issue node, 'B' for match
        node -> b, None for nothing. linked: the item's match node has an edge from the
        previous item's (both exist and are consecutive in the list)."""
        reach, link_next = False, False
        for idx, typ, linked, inode, mnode in reversed(items):
            covered = link_next and reach
            if typ == "B":
                if not covered:
                    self.edges.append((mnode, b))
                reach = True
            else:
                reach = covered
                if typ == "A" and (self.first is None or idx < self.first):
                    self.first, self.first_node = idx, inode
            link_next = linked

    def emit(self, out: list, a) -> None:
        out.extend(reversed(self.edges))
        if self.first_node is not None:
            out.append((a, self.first_node))


def thread_edges(t: Trace, M, node_set, mnode: Dict[Key, tuple], probe_alt=None, orient: bool = True,
                 cut=None) -> List[Tuple[tuple, tuple]]:
    """The edges a split rank adds to the constraint graph: recorded synchronisation, and, if
    orient, one orientation per concurrent pair whose order decides a match of M or a
    probe's report (module docstring), written out up to reachability (_Reduced)."""
    if orient and not FULL_ORIENTATION:
        return _thread_edges_reduced(t, M, node_set, mnode, probe_alt)
    from .matching import match_node
    edges: List[Tuple[tuple, tuple]] = []
    for a, ia, b, ib in t.hb.edges:
        edges.append(((a, ia, "c"), (b, ib, "i")))
    if not orient:
        return edges
    recv_of = {sk: rk for rk, sk in M.items()}

    def kept(x) -> bool:
        return x.issue_node() in node_set

    def phys_before(a, b) -> bool:
        return physical_key(t, a) < physical_key(t, b)

    def match_of(rk):
        return mnode.get(rk)

    # receives of a split rank: r takes s, x on another chain is concurrent and accepts s
    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        m = match_of(rk)
        if m is None or r.proc not in t.chains:
            continue
        for c in t.chains_of(r.proc):
            if c == r.rank:
                continue
            for x in _recvs_in_window(t, r, s, c):
                if x.cancelled or x.mrecv or not kept(x) or not x.accepts(s):
                    continue
                s2k = M.get(x.key)
                s2 = t.sends[s2k] if s2k is not None else None
                if s2 is not None and same_channel(s2, s) and send_before(t, s2, s):
                    continue                     # the sender-side order edge says it already
                mx = match_of(x.key) if s2 is not None else None
                if s2 is not None and same_channel(s2, s):
                    # two messages of one channel: the postings follow the order of the sends
                    # (sends keep the order in which MPI saw them, unless happens-before says)
                    first_x = mx is not None and not send_before(t, s, s2) and phys_before(s2, s)
                else:
                    first_x = s2 is not None and mx is not None and phys_before(x, r)
                if not first_x:
                    edges.append((r.issue_node(), x.issue_node()))     # r is posted first
                else:
                    edges.append((mx, m))                               # x is matched first
    # sends of a split rank: r takes s, s' of the channel on another chain is concurrent
    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        m = match_of(rk)
        if m is None or s.proc not in t.chains:
            continue
        for c in t.chains_of(s.proc):
            if c == s.rank:
                continue
            for s2 in _sends_in_window(t, s, c, r):
                if s2.issue_node() not in node_set:
                    continue
                r2k = recv_of.get(s2.key)
                m2 = match_of(r2k) if r2k is not None else None
                if m2 is None or before(t, r, t.recvs[r2k]) or not phys_before(s2, s):
                    edges.append((s.issue_node(), s2.issue_node()))    # s is issued first
                else:
                    edges.append((m2, m))                               # s2 is matched first
    # probes: a probe reports a message no concurrent receive has taken yet
    probe_alt = probe_alt or {}
    for p in t.probes.values():
        sk = probe_alt.get(p.key, p.seen)
        pc = p.complete_node()
        if sk is None or pc is None or pc not in node_set or p.proc not in t.chains:
            continue
        s = t.sends[sk]
        if s.proc in t.chains:
            # a message of the channel that a concurrent chain of the sender issued: it was
            # issued after s, or matched before the probe looked
            for c in t.chains_of(s.proc):
                if c == s.rank:
                    continue
                for s2 in _sends_in_window(t, s, c, p):
                    if s2.issue_node() not in node_set:
                        continue
                    r2k = recv_of.get(s2.key)
                    m2 = match_of(r2k) if r2k is not None else None
                    if m2 is not None and phys_before(s2, s):
                        edges.append((m2, pc))
                    else:
                        edges.append((s.issue_node(), s2.issue_node()))
        for c in t.chains_of(p.proc):
            if c == p.rank:
                continue
            for x in _recvs_in_window(t, p, s, c):
                if x.cancelled or x.mrecv or not kept(x) or not x.accepts(s):
                    continue
                if M.get(x.key) == sk:
                    mx = match_of(x.key)
                    if mx is not None:
                        edges.append((pc, mx))                          # matched after the report
                    continue
                s2k = M.get(x.key)
                mx = match_of(x.key) if s2k is not None else None
                if mx is not None and phys_before(x, p):
                    edges.append((mx, pc))
                else:
                    edges.append((pc, x.issue_node()))
    return edges


# thread_edges writes every orientation edge out (the construction of milestone A) when set: the
# tests compare the two constructions
FULL_ORIENTATION = False
# the reduced construction answers the windows of monotone lists by bisection when set (the
# tests compare it with the walk)
FAST_WINDOWS = True


def _thread_edges_reduced(t: Trace, M, node_set, mnode: Dict[Key, tuple], probe_alt=None) -> List[Tuple[tuple, tuple]]:
    """thread_edges with the orientation edges reduced per chain (_Reduced): the same graph up
    to reachability, so the same cycles. Each item is classified as in the full construction,
    which stays above as the reference."""
    edges: List[Tuple[tuple, tuple]] = [((a, ia, "c"), (b, ib, "i")) for a, ia, b, ib in t.hb.edges]
    recv_of = {sk: rk for rk, sk in M.items()}
    pkey = {}

    def phys(x):
        k = pkey.get(x.key if isinstance(x, (Recv, Send)) else id(x))
        if k is None:
            k = pkey[x.key if isinstance(x, (Recv, Send)) else id(x)] = physical_key(t, x)
        return k

    def recv_items(x, s: Send, c: int, classify):
        """Per envelope class of chain c that accepts s, the receives concurrent with x, kept, in
        order, each classified by classify(y) -> 'A' | 'B' | None, and linked to the previous
        one by the competitor edge when both have a match node."""
        lo, hi = window(t, x, c)
        if hi - lo <= 1:
            return
        for src in (s.proc, ANY):
            for tag in (s.tag, ANY):
                key = (c, x.comm, src, tag)
                idx = t._recv_class_idx.get(key)
                if not idx:
                    continue
                lst = t._recv_class[key]
                items = []
                prev_m = False
                for j in range(bisect_right(idx, lo), bisect_left(idx, hi)):
                    y = lst[j]
                    if y.issue_node() not in node_set:
                        break                        # the kept records of a chain are a prefix
                    my = mnode.get(y.key)
                    items.append((y.idx, classify(y, my), prev_m and my is not None, y.issue_node(), my))
                    prev_m = my is not None
                yield items

    def send_items(s: Send, c: int, r, classify):
        """Per tag of s's channel on chain c that r accepts, the sends concurrent with s issued in
        the kept prefix, in order, classified, and linked to the previous one by the
        sender-side order edge between their receives (both with a match node, the earlier
        send available to the later one's receive)."""
        lo, hi = window(t, s, c)
        if hi - lo <= 1:
            return
        q = (c, s.dst, s.comm)
        for tag in ((r.tag,) if r.tag != ANY else t._tags_on.get(q, ())):
            key = q + (tag,)
            idx = t._send_tag_idx.get(key)
            if not idx:
                continue
            lst = t._send_tag[key]
            items = []
            prev = None
            for j in range(bisect_right(idx, lo), bisect_left(idx, hi)):
                s2 = lst[j]
                if not r.accepts(s2) or s2.cancelled:
                    prev = None
                    continue
                if s2.issue_node() not in node_set:
                    break
                r2k = recv_of.get(s2.key)
                m2 = mnode.get(r2k) if r2k is not None else None
                linked = prev is not None and prev[1] is not None and m2 is not None and prev[0].available_to(t.recvs[r2k])
                items.append((s2.idx, classify(s2, r2k, m2), linked, s2.issue_node(), m2))
                prev = (s2, m2)
            yield items

    def kept_len(lst) -> int:
        """How many operations of a chain's list are in the kept prefix (a prefix of the list)."""
        lo, hi = 0, len(lst)
        while lo < hi:
            mid = (lo + hi) // 2
            if lst[mid].issue_node() in node_set:
                lo = mid + 1
            else:
                hi = mid
        return lo

    views: Dict[tuple, Optional[tuple]] = {}
    observed_recv = {sk: rk for rk, sk in t.observed.items()}

    def recv_view(key):
        """For a class of receives whose source is one process: the kept length, the positions
        of the receives with a match node and the physical order of their messages (None
        unless it never decreases: then the receives whose message precedes a given one form
        a prefix of them), and the positions of those without a match node."""
        v = views.get(key, False)
        if v is not False:
            return v
        lst = t._recv_class[key]
        n = kept_len(lst)
        pos, val, nomx, exc = [], [], [], []
        for j in range(n):
            y = lst[j]
            if mnode.get(y.key) is None:
                nomx.append(j)
            elif M.get(y.key) != t.observed.get(y.key):
                exc.append(j)                    # re-matched by the alternative: judged one by one
            else:
                pos.append(j)
                val.append(phys(t.sends[M[y.key]]))
        v = views[key] = (n, pos, val, nomx, exc) if all(val[i] <= val[i + 1] for i in range(len(val) - 1)) else None
        return v

    def recv_fast(red: _Reduced, r, s: Send, ps, c: int, m) -> bool:
        """The receiver side for chain c by bisection (recv_items and classify below give the
        same edges); False when a class does not allow it."""
        lo, hi = window(t, r, c)
        if hi - lo <= 1:
            return True
        plan = []
        for tag in (s.tag, ANY):
            key = (c, r.comm, s.proc, tag)
            if key in t._recv_class_idx and (c, r.comm, ANY, tag) not in t._recv_class_idx:
                v = recv_view(key)
                if v is None:
                    return False
                plan.append((key, v))
            elif (c, r.comm, ANY, tag) in t._recv_class_idx:
                return False                     # receives from any source: messages of several channels
        for key, (n, pos, val, nomx, exc) in plan:
            idx, lst = t._recv_class_idx[key], t._recv_class[key]
            a, b = bisect_right(idx, lo), min(bisect_left(idx, hi), n)
            if a >= b:
                continue
            j0, j1 = bisect_left(pos, a), bisect_left(pos, b)
            k = bisect_right(val, ps, j0, j1)    # the first receive with a match node whose message follows s
            p = pos[k] if k < j1 else b
            if p < b and send_before(t, t.sends[M[lst[p].key]], s):
                return False                     # physical order against happens-before: walk instead
            first = p if p < b else None
            breaks = nomx[bisect_left(nomx, a):bisect_left(nomx, b)]
            for j in breaks:                     # no match node: posted after r, unless the sender orders it already
                y = lst[j]
                s2k = M.get(y.key)
                if s2k is None or not send_before(t, t.sends[s2k], s):
                    if first is None or j < first:
                        first = j
                    break
            # the receives the alternative re-matched, classified one by one as in classify
            kinds = {}
            for j in exc[bisect_left(exc, a):bisect_left(exc, b)]:
                s2 = t.sends[M[lst[j].key]]
                if send_before(t, s2, s):
                    continue
                if not send_before(t, s, s2) and phys(s2) < ps:
                    kinds[j] = "B"
                elif first is None or j < first:
                    first = j
            # the last receive matched first of each run between receives without a match node
            start = a
            for e in breaks + [b]:
                last = None
                i = bisect_left(pos, min(e, p), j0, k) - 1
                while i >= j0 and pos[i] >= start:
                    y = lst[pos[i]]
                    s2 = t.sends[M[y.key]]
                    if send_before(t, s, s2):
                        return False             # physical order against happens-before: walk instead
                    if not send_before(t, s2, s):
                        last = pos[i]
                        break
                    i -= 1
                for j in kinds:
                    if start <= j < e and (last is None or j > last):
                        last = j
                if last is not None:
                    red.edges.append((mnode[lst[last].key], m))
                start = e + 1
            if first is not None and (red.first is None or lst[first].idx < red.first):
                red.first, red.first_node = lst[first].idx, lst[first].issue_node()
        return True

    def send_view(key):
        """For a tag of a chain's channel: the kept length, the physical order of the sends
        (None unless it never decreases), the receive and match node of each, where the runs
        of linked receives start, and per receiving chain the positions of its receives and
        their indexes (None for a chain whose indexes decrease)."""
        v = views.get(("s",) + key, False)     # recv_view caches its own classes in views too
        if v is not False:
            return v
        lst = t._send_tag[key]
        n = kept_len(lst)
        ph = [phys(s2) for s2 in lst[:n]]
        if any(ph[i] > ph[i + 1] for i in range(n - 1)):
            views[("s",) + key] = None
            return None
        rks = [recv_of.get(s2.key) for s2 in lst[:n]]
        ms = [mnode.get(x) if x is not None else None for x in rks]
        starts = [0] + [i for i in range(1, n) if not (ms[i - 1] is not None and ms[i] is not None
                                                        and lst[i - 1].available_to(t.recvs[rks[i]]))]
        by: Dict[int, Tuple[List[int], List[int]]] = {}
        exc = []                                 # sends whose receive the alternative changed
        for i, x in enumerate(rks):
            if x is None:
                continue
            if x != observed_recv.get(lst[i].key):
                exc.append(i)
                continue
            e = by.setdefault(t.recvs[x].rank, ([], []))
            e[0].append(i)
            e[1].append(t.recvs[x].idx)
        chains = {d: (e if all(e[1][i] < e[1][i + 1] for i in range(len(e[1]) - 1)) else None) for d, e in by.items()}
        nom = [i for i in range(n) if ms[i] is None]
        v = views[("s",) + key] = (n, ph, rks, ms, starts, chains, nom, exc)
        return v

    def send_fast(red: _Reduced, r, s: Send, ps, c: int, m) -> bool:
        """The sender side for chain c by bisection (send_items and classify below give the
        same edges); False when a tag does not allow it."""
        lo, hi = window(t, s, c)
        if hi - lo <= 1:
            return True
        q = (c, s.dst, s.comm)
        for tag in ((r.tag,) if r.tag != ANY else t._tags_on.get(q, ())):
            key = q + (tag,)
            idx = t._send_tag_idx.get(key)
            if not idx:
                continue
            lst = t._send_tag[key]
            if lst and not r.accepts(lst[0]):
                continue
            v = send_view(key)
            if v is None:
                return False
            n, ph, rks, ms, starts, chains, nom, exc = v
            a, b = bisect_right(idx, lo), min(bisect_left(idx, hi), n)
            if a >= b:
                continue
            p = bisect_right(ph, ps, a, b)       # the first send issued after s: s goes first
            # or the first send before it whose receive has no match node or follows r
            first = p
            j = bisect_left(nom, a)
            if j < len(nom) and nom[j] < first:
                first = nom[j]
            for d, e in chains.items():
                if e is None:
                    return False
                th = r.idx + 1 if d == r.rank else t.hb.first_after(r.rank, r.idx, d, len(t.ranks[d]))
                j = max(bisect_left(e[1], th), bisect_left(e[0], a))
                if j < len(e[0]) and e[0][j] < first:
                    first = e[0][j]
            for i in exc[bisect_left(exc, a):bisect_left(exc, p)]:
                if i < first and before(t, r, t.recvs[rks[i]]):
                    first = i
                    break
            if first < b:
                x = lst[first]
                if red.first is None or x.idx < red.first:
                    red.first, red.first_node = x.idx, x.issue_node()
            # the last send matched first of each run of linked receives
            k = bisect_right(starts, a) - 1
            while k < len(starts) and starts[k] < p:
                u = max(starts[k], a)
                w = min(starts[k + 1] if k + 1 < len(starts) else n, p)
                for i in range(w - 1, u - 1, -1):
                    if ms[i] is not None and not before(t, r, t.recvs[rks[i]]):
                        red.edges.append((ms[i], m))
                        break
                k += 1
        return True

    # receives of a split rank: r takes s, x on another chain is concurrent and accepts s
    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        m = mnode.get(rk)
        if m is None or r.proc not in t.chains:
            continue
        ps, pr = phys(s), phys(r)

        def classify(x, mx, s=s, ps=ps, pr=pr):
            if x.cancelled or x.mrecv or not x.accepts(s):
                return None
            s2k = M.get(x.key)
            s2 = t.sends[s2k] if s2k is not None else None
            if s2 is not None and same_channel(s2, s):
                if send_before(t, s2, s):
                    return None                  # the sender-side order edge says it already
                first_x = mx is not None and not send_before(t, s, s2) and phys(s2) < ps
            else:
                first_x = s2 is not None and mx is not None and phys(x) < pr
            return "B" if first_x else "A"
        for c in t.chains_of(r.proc):
            if c == r.rank:
                continue
            red = _Reduced()
            if not (FAST_WINDOWS and recv_fast(red, r, s, ps, c, m)):
                red = _Reduced()
                for items in recv_items(r, s, c, classify):
                    red.run(items, r.issue_node(), m)
            red.emit(edges, r.issue_node())
    # sends of a split rank: r takes s, s' of the channel on another chain is concurrent
    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        m = mnode.get(rk)
        if m is None or s.proc not in t.chains:
            continue
        ps = phys(s)

        def classify(s2, r2k, m2, r=r, ps=ps):
            if m2 is None or before(t, r, t.recvs[r2k]) or not phys(s2) < ps:
                return "A"                       # s is issued first
            return "B"                           # s2 is matched first
        for c in t.chains_of(s.proc):
            if c == s.rank:
                continue
            red = _Reduced()
            if not (FAST_WINDOWS and send_fast(red, r, s, ps, c, m)):
                red = _Reduced()
                for items in send_items(s, c, r, classify):
                    red.run(items, s.issue_node(), m)
            red.emit(edges, s.issue_node())
    # probes: a probe reports a message no concurrent receive has taken yet
    probe_alt = probe_alt or {}
    for p in t.probes.values():
        sk = probe_alt.get(p.key, p.seen)
        pc = p.complete_node()
        if sk is None or pc is None or pc not in node_set or p.proc not in t.chains:
            continue
        s = t.sends[sk]
        ps, pp = phys(s), phys(p)
        if s.proc in t.chains:
            def classify(s2, r2k, m2, ps=ps):
                return "B" if m2 is not None and phys(s2) < ps else "A"
            for c in t.chains_of(s.proc):
                if c == s.rank:
                    continue
                red = _Reduced()
                for items in send_items(s, c, p, classify):
                    red.run(items, s.issue_node(), pc)
                red.emit(edges, s.issue_node())
        after: List[Tuple[tuple, tuple]] = []

        def classify(x, mx, s=s, sk=sk, pp=pp):
            if x.cancelled or x.mrecv or not x.accepts(s):
                return None
            if M.get(x.key) == sk:
                if mx is not None:
                    after.append((pc, mx))       # matched after the report
                return None
            return "B" if mx is not None and phys(x) < pp else "A"
        for c in t.chains_of(p.proc):
            if c == p.rank:
                continue
            red = _Reduced()
            for items in recv_items(p, s, c, classify):
                red.run(items, pc, pc)
            red.emit(edges, pc)
        edges.extend(after)
    return edges


# -------------------------------------------------------------------------- candidates

def observed_reaches(t: Trace):
    """reaches(a, b) of the observed graph without orientation edges (matching.observed_hb),
    answered by vector clocks over the chains, built in one topological pass, instead of a
    search of the graph per query: candidates asks once per candidate, which on a trace of
    10^4 receives was nearly all of the analysis. The graph's own search when it is cyclic."""
    from .matching import build_graph
    g = build_graph(t, t.observed, cut=None, B=None, thread_orient=False)
    chains = {c: i for i, c in enumerate(sorted(t.ranks))}
    width = len(chains)

    def own(n) -> bool:
        return n[0] >= 0 and n[2] != "m"

    def pos(n) -> int:
        return 2 * n[1] + (n[2] == "c")

    indeg: Dict[tuple, int] = {}
    for succ in g.succ.values():
        for b in succ:
            indeg[b] = indeg.get(b, 0) + 1
    nodes = set(g.nodes) | set(indeg) | set(g.succ)
    # rows[n][k]: the last node of chain k (by position) that reaches n; a node's own chain is
    # read off positions, so a row passes unchanged along program order and is shared there
    base = [-1] * width
    rows: Dict[tuple, list] = {n: base for n in nodes if n not in indeg}
    ready = list(rows)
    acc: Dict[tuple, list] = {}
    done = 0
    while ready:
        n = ready.pop()
        done += 1
        r = rows[n]
        n_own = own(n)
        if n_own:
            k, p = chains[n[0]], pos(n)
        for b in g.succ.get(n, ()):
            if n_own and not (own(b) and b[0] == n[0]) and r[k] < p:
                c = list(r)
                c[k] = p
            else:
                c = r
            cur = acc.get(b)
            if cur is None:
                acc[b] = c
            elif cur is not c:
                acc[b] = [x if x > y else y for x, y in zip(cur, c)]
            d = indeg[b] - 1
            indeg[b] = d
            if d == 0:
                rows[b] = acc.pop(b)
                ready.append(b)
    if done < len(nodes):
        return g.reaches                     # cyclic: no clocks

    def reaches(a, b) -> bool:
        if a == b:
            return False
        if not own(a):
            return g.reaches(a, b)
        if own(b) and b[0] == a[0]:
            return pos(b) > pos(a)
        rb = rows.get(b)
        return rb is not None and a in rows and rb[chains[a[0]]] >= pos(a)
    return reaches


class _Heads:
    """The first send of a sending chain's queue to a process that no receive (or probe) before x
    in happens-before took, for x in posting order per chain: a pointer per receiving chain and
    queue skips the sends taken before, which stay taken for the later x of the chain, so a queue
    is walked once per receiving chain instead of once per receive. A receive with a tag looks
    at the sends of its tag only."""

    def __init__(self, t: Trace, sender_of: Dict[Key, Key]):
        self.t, self.sender_of = t, sender_of
        self.queues: Dict[tuple, List[Send]] = {}
        for c, sends in t._sends_of.items():
            for s in sends:
                self.queues.setdefault((c, s.dst, s.comm, None), []).append(s)
                self.queues.setdefault((c, s.dst, s.comm, s.tag), []).append(s)
        self.ptr: Dict[tuple, int] = {}

    def taken_before(self, s: Send, x) -> bool:
        r2k = self.sender_of.get(s.key)
        return r2k is not None and before(self.t, self.t.recvs[r2k], x)

    def first(self, x, c: int) -> Optional[Send]:
        """The first send of chain c that x can take (Send.available_to) and that no receive
        before x took."""
        q = (c, x.proc, x.comm, None if x.tag == ANY else x.tag)
        lst = self.queues.get(q)
        if not lst:
            return None
        pk = (x.rank,) + q
        j = self.ptr.get(pk, 0)
        while j < len(lst) and self.taken_before(lst[j], x):
            j += 1
        self.ptr[pk] = j
        for s in lst[j:]:
            if s.available_to(x) and not self.taken_before(s, x):
                return s
        return None


def candidates(t: Trace) -> Tuple[list, List[Tuple[str, str]]]:
    """Single-change alternatives of a trace with split ranks (docs/threads.md 3.3): for every
    receive, per source, the sends it could take that no receive before it took and that no
    other such send precedes (one per sending chain at most). A send taken in the run by a
    concurrent receive gives a steal, which leaves that receive without it, and a swap when
    that receive accepts the first one's message."""
    from .matching import Alternative, Overlay, _follows
    hb = observed_reaches(t)
    sender_of: Dict[Key, Key] = {sk: rk for rk, sk in t.observed.items()}
    heads = _Heads(t, sender_of)
    out: list = []
    pruned: List[Tuple[str, str]] = []
    seen_swaps: Set[Tuple[Key, Key]] = set()
    for r in sorted(t.recvs.values(), key=lambda r: r.key):
        if r.cancelled or r.mrecv:
            continue
        s_obs = t.observed_send(r)
        rc = r.complete_node()
        split_here = r.proc in t.chains
        for src in t.sources_to(r.proc, r.comm):
            if r.src != ANY and src != r.src:
                continue
            if not (r.wildcard or split_here or src in t.chains):
                continue
            # per sending chain, the first send r accepts that was not taken before r is posted
            firsts: List[Send] = [s for s in (heads.first(r, c) for c in t.chains_of(src)) if s is not None]
            front = [s for s in firsts if not any(o is not s and send_before(t, o, s) for o in firsts)]
            for s in front:
                if s_obs is not None and s.key == s_obs.key:
                    continue
                r2k = sender_of.get(s.key)
                if rc is not None and _follows(hb, rc, s, r2k):
                    pruned.append((f"{r} <- {s}", "send causally follows the receive's completion"))
                    continue
                M = Overlay(t.observed, {r.key: s.key})
                if r2k is None:
                    out.append(Alternative(r.key, s.key, M, "steal", {r.key}, None))
                    continue
                r2 = t.recvs[r2k]
                if s_obs is not None and r2.accepts(s_obs) and not r2.mrecv and concurrent(t, r, r2):
                    pair = tuple(sorted((r.key, r2k)))
                    if pair not in seen_swaps:
                        seen_swaps.add(pair)
                        out.append(Alternative(r.key, s.key, M.with_(changed={r2k: s_obs.key}), "swap",
                                               {r.key, r2k}, r2k))
                out.append(Alternative(r.key, s.key, M.with_(removed=[r2k]), "steal", {r.key}, r2k))
    return out, pruned


def probe_candidates(t: Trace) -> List[Tuple[Probe, Send]]:
    """Other messages a probe that found one could have reported: as for receives, per source
    the sends it accepts that no receive before it took, minimal in their channel."""
    hb = observed_reaches(t)
    sender_of: Dict[Key, Key] = {sk: rk for rk, sk in t.observed.items()}
    heads = _Heads(t, sender_of)
    out = []
    for p in sorted(t.probes.values(), key=lambda p: p.key):
        if not p.flag or p.seen is None or p.complete_node() is None:
            continue
        if not (p.wildcard or p.proc in t.chains or t.sends[p.seen].proc in t.chains):
            continue
        for src in t.sources_to(p.proc, p.comm):
            if p.src != ANY and src != p.src:
                continue
            firsts = [s for s in (heads.first(p, c) for c in t.chains_of(src)) if s is not None]
            for s in firsts:
                if s.key == p.seen or any(o is not s and send_before(t, o, s) for o in firsts):
                    continue
                if not hb(p.complete_node(), s.issue_node()):
                    out.append((p, s))
    return out


# --------------------------------------------------------------- observed thread findings

def observed_findings(t: Trace, tasks: bool = False) -> List[tuple]:
    """(class, title, details) of the errors §12.6.2 and §12.2.1 of MPI 4.1 name, read from the
    chains and their happens-before (docs/threads.md 3.7). tasks: MPI was called inside OpenMP
    tasks, which are no chains of their own, so the happens-before is not known and only the
    thread level is checked."""
    out = []
    level = t.thread_level
    for rank, chs in sorted(t.chains.items()):
        if tasks:
            chs = []                     # only the thread level below (and every chain for it)
        # collectives of one communicator on two chains that neither precedes
        by_comm: Dict[int, list] = {}
        for c in t.colls:
            if c.proc == rank:
                by_comm.setdefault(c.comm, []).append(c)
        for comm, cs in (sorted(by_comm.items()) if chs else []):
            hit = next(((a, b) for i, a in enumerate(cs) for b in cs[i + 1:] if concurrent(t, a, b)), None)
            if hit:
                a, b = hit
                out.append(("2-thread-collective", f"rank {rank}: {a.name} and {b.name} on comm {comm} are called by "
                            f"chains {a.rank} and {b.rank} with no synchronisation between them",
                            ["MPI 4.1 §12.6.2: matching of collective calls is by their order at each process; "
                             "concurrent threads must order them by synchronisation"]))
        # two waits or tests on one request that neither precedes
        waits = [rec for c in chs for rec in t.ranks[c] if rec.op == "wait" and rec.raw is not None]
        reqs_of = lambda rec: {q for q in rec.raw.get("reqs", []) if q is not None and q >= 0}
        found = None
        for i, a in enumerate(waits):
            for b in waits[i + 1:]:
                if a.rank != b.rank and reqs_of(a) & reqs_of(b) and concurrent(t, a, b):
                    found = (a, b)
                    break
            if found:
                break
        if found:
            a, b = found
            out.append(("2-thread-wait", f"rank {rank}: chains {a.rank} and {b.rank} complete request "
                        f"{sorted(reqs_of(a) & reqs_of(b))[0]} concurrently",
                        ["MPI 4.1 §12.6.2: a program in which two threads block, waiting on the same request, is erroneous"]))
        # finalize on another thread, or before the other threads' calls
        fin = [rec for c in chs for rec in t.ranks[c] if rec.op == "finalize"]
        for f in fin:
            if f.rank != rank:
                out.append(("2-thread-finalize", f"rank {rank}: MPI_Finalize called by chain {f.rank}, not by the "
                            "thread that initialised MPI", ["MPI 4.1 §12.6.2"]))
            late = next((rec for c in chs if c != f.rank for rec in t.ranks[c]
                         if rec.op not in ("overflow", "guard", "comm") and not before(t, rec, f)), None)
            if late is not None:
                out.append(("2-thread-finalize", f"rank {rank}: MPI_Finalize is not preceded by chain {late.rank}'s "
                            f"record #{late.idx} ({late.op})", ["MPI 4.1 §12.6.2: MPI_Finalize only after all "
                                                                 "threads have completed their MPI calls"]))
        if level in (0, 1) and len(t.chains[rank]) > 1:
            name = "MPI_THREAD_SINGLE" if level == 0 else "MPI_THREAD_FUNNELED"
            out.append(("2-thread-level", f"rank {rank}: {len(t.chains[rank]) - 1} thread(s) besides the main one call MPI "
                        f"under {name}", ["MPI 4.1 §12.2.1"]))
    for rank in sorted(set(t.thread_overlap)):
        if level < 3:
            out.append(("2-thread-level", f"rank {rank}: two threads were inside MPI calls at once under "
                        f"thread level {level}", ["MPI 4.1 §12.2.1: under MPI_THREAD_SERIALIZED calls are not made "
                                                  "concurrently from two threads"]))
    return out


def chain_note(t: Trace) -> str:
    parts = []
    for rank, chs in sorted(t.chains.items()):
        parts.append(f"rank {rank}: " + ", ".join(f"{c} ({t.chain_name.get(c, '?')})" for c in chs))
    return "chains (id, the thread's name in the run): " + "; ".join(parts)


# ------------------------------------------------------------------ contained deadlocks

def contained_deadlock(t: Trace, M, changed: Set[Key], cut: Dict[int, int], issued: Dict[int, int],
                       B: Optional[int], rule1: Optional[Dict[int, int]] = None,
                       sync_stops: Optional[Dict[int, Tuple[str, Set[int]]]] = None) -> Optional[Dict[int, str]]:
    """deadlock.contained_deadlock over chains (docs/threads.md, theorem T7 with chains): a
    chain also stops at a synchronisation whose predecessor is not reached, and the partners
    of an operation are the chains of the ranks that could complete it. sync_stops: chains
    that stop at a synchronisation (stretto.locks: the acquire of a lock another chain holds, a
    wait for a point of another chain), as (what, the chains that could release it)."""
    sync_stops = sync_stops or {}
    from .deadlock import _completed_at
    from .horizon import _cidx, coll_can_complete
    from .matching import send_blocks
    lengths = {ch: len(recs) for ch, recs in t.ranks.items()}
    if rule1 is None:
        rule1 = {}
        for rk in changed:
            r = t.recvs[rk]
            rule1[r.rank] = min(rule1.get(r.rank, _cidx(r) + 1), _cidx(r) + 1)

    def in_h(ch: int, idx: int) -> bool:
        return idx < cut[ch]

    def sent(s: Send) -> bool:
        return in_h(s.rank, s.idx) or issued.get(s.rank) == s.idx

    def chains(procs: Iterable[int]) -> Set[int]:
        return {c for p in procs for c in t.chains_of(p)}

    recv_of = {sk: rk for rk, sk in M.items()}
    terminated = set()
    for ch, n in lengths.items():
        if ch in rule1 and n >= rule1[ch] or getattr(cut, "dead", {}).get(ch) == n or ch in sync_stops:
            continue                         # its continuation is not known: it may not have ended
        if cut[ch] == n and n and (t.ranks[ch][-1].op == "finalize" or t.proc.get(ch, ch) != ch):
            terminated.add(ch)

    def senders(x) -> Set[int]:
        if x.src != ANY:
            return chains([x.src])
        return chains(set(t.comm_ranks.get(x.comm, sorted(t.chains_of(x.proc)))) - {x.proc})

    def message_present(x) -> bool:
        for src in ([x.src] if x.src != ANY else t.sources_to(x.proc, x.comm)):
            for s in t.sends_to(src, x.proc, x.comm):
                if not sent(s) or not x.accepts(s) or s.cancelled:
                    continue
                rk = recv_of.get(s.key)
                if rk is None or not in_h(rk[0], t.recvs[rk].idx):
                    return True
        return False

    def receive_present(s: Send) -> bool:
        for c in t.chains_of(s.dst):
            for x in t.recvs_of(c):
                if not in_h(x.rank, x.idx):
                    break
                if x.cancelled or not x.accepts(s):
                    continue
                sk = M.get(x.key)
                if sk is None or not sent(t.sends[sk]):
                    return True
        return False

    def needs(x) -> Optional[Set[int]]:
        if isinstance(x, Recv):
            if x.mrecv:
                return None
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
            return None if receive_present(x) else chains([x.dst])
        if hasattr(x, "seen"):
            if x.kind not in ("probe", "mprobe"):
                return None
            return None if message_present(x) else senders(x)
        if hasattr(x, "ordinal"):
            group = [c for c in t.colls if c.comm == x.comm and c.ordinal == x.ordinal]
            have = {c.proc for c in group if in_h(c.rank, c.idx)}
            need = set(t.comm_members.get(x.comm, [c.proc for c in group]))
            pos = {r: i for i, r in enumerate(t.comm_ranks.get(x.comm, []))}
            if coll_can_complete(x, have, need, pos):
                return None
            return chains(need - have)
        return None

    # the synchronisation edges into each record
    into: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for a, ia, b, ib in t.hb.edges:
        into.setdefault((b, ib), []).append((a, ia))

    stops: Dict[int, Tuple[str, Set[int]]] = {}
    for ch, n in lengths.items():
        c = cut[ch]
        if ch in sync_stops:
            what, partners = sync_stops[ch]
            stops[ch] = (what, set(partners) - {ch})
            continue
        if c >= n or (ch in rule1 and c >= rule1[ch]):
            continue
        rec = t.ranks[ch][c]
        dead = getattr(cut, "dead", {})
        waiting = {a for a, ia in into.get((ch, c), ())
                   if ia + 1 > cut[a] or (ia + 1 == cut[a] and dead.get(a) == cut[a])}
        if waiting and issued.get(ch) != c:
            # it waits for a synchronisation point the horizon does not reach. Milestone A does not
            # claim deadlocks through synchronisation: the trace does not order the synchronisation
            # points between two records of a chain, so rule 8 treats all of them as unreached
            # once one is (docs/threads.md, section 9), and such a wait may be an artefact of that
            continue
        if rec.op not in ("send", "recv", "coll", "probe", "wait"):
            continue
        kind = rec.raw.get("kind") if rec.op == "wait" and rec.raw is not None else None
        if kind is not None and (kind.startswith("test") or kind == "getstatus"):
            continue
        if rec.op == "probe" and rec.raw is not None and rec.raw.get("kind") in ("iprobe", "improbe"):
            continue
        if kind in ("waitany", "waitsome"):
            ops = [x for q in rec.raw.get("reqs", []) if q is not None and q >= 0
                   for x in (t.recv_by_req(ch, q), t._by_req.get(("s", ch, q))) if x is not None]
        else:
            ops = _completed_at(t).get((ch, c), [])
        if not ops:
            continue
        reqs = [needs(x) for x in ops]
        if kind in ("waitany", "waitsome"):
            if any(r is None for r in reqs):
                continue
            partners = set().union(*reqs)
        else:
            blocked = [r for r in reqs if r is not None]
            if not blocked:
                continue
            partners = set().union(*blocked)
        stops[ch] = (f"{rec.op} #{rec.idx}", partners - {ch})

    D = set(stops)
    while True:
        drop = {r for r in D if not stops[r][1] <= D | terminated}
        if not drop:
            break
        D -= drop
    if not D:
        return None
    return {r: stops[r][0] for r in sorted(D)}
