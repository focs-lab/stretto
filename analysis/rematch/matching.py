"""Constraint graph, observed happens-before, candidate alternative matchings and the
MPI matching-order checks. See docs/model.md section 2."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

from .trace import ANY, Coll, Key, Probe, Recv, Send, Trace

Node = Tuple[int, int, str]

# Collective synchronisation shapes (MPI 4.1 §6.1: a collective may return as soon as the
# caller's participation is complete). 'all': every member waits for every member;
# 'root-out': non-roots wait for the root; 'root-in': the root waits for non-roots;
# 'prefix': member j waits for members i < j.
COLL_SHAPE = {
    "barrier": "all", "allreduce": "all", "allgather": "all", "allgatherv": "all", "alltoall": "all",
    "alltoallv": "all", "alltoallw": "all", "reduce_scatter": "all", "reduce_scatter_block": "all",
    "bcast": "root-out", "scatter": "root-out", "scatterv": "root-out",
    "reduce": "root-in", "gather": "root-in", "gatherv": "root-in",
    "scan": "prefix", "exscan": "prefix",
}


def coll_shape(name: str) -> Tuple[str, bool]:
    """(shape, known). Nonblocking variants share the shape of their blocking form."""
    base = name[1:] if name.startswith("i") and name[1:] in COLL_SHAPE else name
    if base in COLL_SHAPE:
        return COLL_SHAPE[base], True
    return "all", False


def hub(comm: int, ordinal: int, part: int = 0) -> Node:
    """A synchronisation node for collective `ordinal` on `comm`: members' issue nodes point
    at it and it points at their completion nodes, so an all-to-all shape costs 2P edges
    instead of P^2. The rank field is negative so that it never names a real rank."""
    return (-1 - comm, ordinal, f"h{part}")


def is_hub(n: Node) -> bool:
    return n[0] < 0


@dataclass
class Graph:
    nodes: List[Node]
    succ: Dict[Node, List[Node]]     # insertion order: deterministic traversal without sorting

    def add(self, a: Node, b: Node):
        """Edges may repeat (a second copy costs the cycle search one skipped visit); a set
        to deduplicate them would be the graph's largest allocation on big traces."""
        if a != b:
            self.succ.setdefault(a, []).append(b)

    def reach_set(self, a: Node) -> Set[Node]:
        seen, stack = set(), [a]
        while stack:
            x = stack.pop()
            for y in self.succ.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        return seen

    def reaches(self, a: Node, b: Node) -> bool:
        return b in self.reach_set(a)

    def find_cycle(self) -> Optional[List[Node]]:
        WHITE, GREY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self.nodes}
        parent: Dict[Node, Optional[Node]] = {}
        for start in self.nodes:
            if color[start] != WHITE:
                continue
            stack = [(start, iter(self.succ.get(start, ())))]
            color[start] = GREY
            parent[start] = None
            while stack:
                node, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    color[node] = BLACK
                    stack.pop()
                    continue
                if nxt not in color:
                    continue
                if color[nxt] == WHITE:
                    color[nxt] = GREY
                    parent[nxt] = node
                    stack.append((nxt, iter(self.succ.get(nxt, ()))))
                elif color[nxt] == GREY:
                    cyc, x = [nxt], node
                    while x != nxt:
                        cyc.append(x)
                        x = parent[x]
                    cyc.reverse()
                    return [n for n in cyc if not is_hub(n)]
        return None


def match_node(r: Recv) -> Node:
    """The node at which r is matched. A blocking receive is matched in its own call and
    nothing else of its rank happens between its issue and its completion, so its completion
    node serves (with the issue node and the match as its only predecessors, reachability is
    unchanged); a nonblocking receive completes at a later wait and gets a node of its own."""
    if r.blocking:
        return (r.rank, r.idx, "c")
    return (r.rank, r.idx, "m")


def is_match(n: Node) -> bool:
    return n[2] == "m"


def send_blocks(s: Send, B: Optional[int]) -> bool:
    """Does the send's request complete only once matched, under threshold B?"""
    if s.rendezvous_always:
        return True
    if s.never_blocks:
        return False
    return B is not None and s.bytes > B


def build_graph(t: Trace, M: Dict[Key, Key], cut: Optional[Dict[int, int]], B: Optional[int],
                extra_edges: Iterable[Tuple[Node, Node]] = (), strong_only: bool = False,
                probe_alt: Optional[Dict[Key, Key]] = None, issued: Optional[Dict[int, int]] = None) -> Graph:
    """Constraint graph over the kept prefix (idx < cut[rank]; cut=None keeps all).
    B=None means infinite buffering. strong_only drops edges whose necessity is not
    guaranteed by the standard (collectives of unknown shape, probe observations): use it
    when a cycle is to be reported as a certain deadlock. probe_alt maps a probe to the
    send it observes instead of the one it saw in the run. issued names, per rank, a
    blocking send at the cut whose issue node is kept although it never completes."""
    def kept(rank: int, idx: int) -> bool:
        return cut is None or idx < cut.get(rank, 10**9)

    nodes: List[Node] = []
    g = Graph(nodes, {})
    for rank, recs in t.ranks.items():
        prev = None
        for rec in recs:
            if not kept(rank, rec.idx):
                if issued and issued.get(rank) == rec.idx:
                    nodes.append(rec.issue)
                    if prev is not None:
                        g.add(prev, rec.issue)
                break
            nodes.append(rec.issue)
            nodes.append(rec.complete)
            g.add(rec.issue, rec.complete)
            if prev is not None:
                g.add(prev, rec.issue)
            prev = rec.complete
    node_set = set(nodes)

    def both(a: Optional[Node], b: Optional[Node]) -> bool:
        return a is not None and b is not None and a in node_set and b in node_set

    # matches: a node per pair whose receive is posted and whose send is issued inside the
    # prefix (docs/model.md §2). Matching order binds the match, not a completion: a
    # nonblocking receive is matched long before its wait, a rendezvous send before its own
    # completion, so the order edges run between match nodes.
    mnode: Dict[Key, Node] = {}
    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        if r.issue_node() in node_set and s.issue_node() in node_set:
            m = match_node(r)
            if m not in node_set:
                nodes.append(m)
                node_set.add(m)
            mnode[rk] = m
    recv_of = {sk: rk for rk, sk in M.items()} if mnode else {}
    for rk, m in mnode.items():
        r, s = t.recvs[rk], t.sends[M[rk]]
        g.add(s.issue_node(), m)                              # the message is issued first
        if m[2] == "m":
            g.add(r.issue_node(), m)                          # the receive is posted first
            rc = r.complete_node()
            if rc is not None and rc in node_set:
                g.add(m, rc)                                  # it completes after the match
        if send_blocks(s, B):
            sc = s.complete_node()
            if sc is not None and sc in node_set:
                g.add(m, sc)                                  # rendezvous: the send completes after it
        if s.ready_mode:
            g.add(r.issue_node(), s.issue_node())             # ready send: receive posted first
        # receiver side: an earlier receive that accepts s is matched before r takes s
        for r2 in t.earlier_competitors(r, s):
            m2 = mnode.get(r2.key)
            if m2 is not None:
                g.add(m2, m)
        # sender side: an earlier message of s's channel that r accepts is matched first
        for s2 in t.earlier_sends(r, s):
            r2k = recv_of.get(s2.key)
            m2 = mnode.get(r2k) if r2k is not None else None
            if m2 is not None:
                g.add(m2, m)
    probe_alt = probe_alt or {}
    for p in t.probes.values():
        sk = probe_alt.get(p.key, p.seen if not strong_only else None)
        if sk is None or not both(t.sends[sk].issue_node(), p.complete_node()):
            continue
        pc = p.complete_node()
        g.add(t.sends[sk].issue_node(), pc)                  # the probed message was issued first
        # and no earlier receive or earlier message of its channel could have taken its place:
        # their matches come before the probe reports it (the probe sees what a receive posted
        # at that point would take)
        s = t.sends[sk]
        for r2 in t.earlier_competitors(p, s):
            m2 = mnode.get(r2.key)
            if m2 is not None:
                g.add(m2, pc)
        for s2 in t.earlier_sends(p, s):
            r2k = recv_of.get(s2.key) if mnode else None
            m2 = mnode.get(r2k) if r2k is not None else None
            if m2 is not None:
                g.add(m2, pc)
    for cs in t.coll_sets():
        shape, known = coll_shape(cs[0].name)
        if strong_only and not known:
            continue
        if shape in ("root-out", "root-in") and any(c.root is None for c in cs):
            shape = "all"          # no recorded root: synchronise everyone, as the horizon and the explorer do
        members = t.comm_members.get(cs[0].comm, [])
        pos = {r: i for i, r in enumerate(members)}
        comm, k = cs[0].comm, cs[0].ordinal
        kept_c = [c for c in cs if c.issue_node() in node_set]
        if shape == "all":
            h = hub(comm, k)
            nodes.append(h)
            for c in kept_c:
                g.add(c.issue_node(), h)
                if c.complete_node() in node_set:
                    g.add(h, c.complete_node())
        elif shape == "root-out":
            for a in kept_c:
                if a.root is not None and a.rank == a.root:
                    for b in kept_c:
                        if a is not b and b.complete_node() in node_set:
                            g.add(a.issue_node(), b.complete_node())
        elif shape == "root-in":
            for b in kept_c:
                if b.root is not None and b.rank == b.root and b.complete_node() in node_set:
                    for a in kept_c:
                        if a is not b:
                            g.add(a.issue_node(), b.complete_node())
        elif shape == "prefix":
            # chained hubs: issue(a_i) -> H_i -> H_{i+1} -> ... and H_i -> complete(a_{i+1}),
            # so that a_i precedes the completion of every later member
            order = sorted(kept_c, key=lambda c: pos.get(c.rank, 0))
            for i, c in enumerate(order):
                h = hub(comm, k, i)
                nodes.append(h)
                g.add(c.issue_node(), h)
                if i + 1 < len(order):
                    g.add(h, hub(comm, k, i + 1))
                    if order[i + 1].complete_node() in node_set:
                        g.add(h, order[i + 1].complete_node())
    for a, b in extra_edges:
        if both(a, b):
            g.add(a, b)
    return g


def observed_hb(t: Trace, B: Optional[int] = None):
    """Predicate hb(a, b) over nodes: a happens-before b in every execution that has the
    observed matching (program order, delivery, collectives, rendezvous if B given)."""
    return build_graph(t, t.observed, cut=None, B=B).reaches


# --------------------------------------------------------------- candidates

class Overlay(Mapping):
    """A matching that differs from a base one at a few receives: the base is shared, so an
    alternative costs O(changes) and not O(n). A trace whose wildcard receives number in the
    millions has as many alternatives (Nekbone's communication suite), and a dict copy of
    the observed matching for each of them is quadratic memory."""
    __slots__ = ("base", "changed", "removed")

    def __init__(self, base: Mapping, changed: Dict[Key, Key] = None, removed: Iterable[Key] = ()):
        self.base, self.changed = base, dict(changed or {})
        self.removed = frozenset(removed) - set(self.changed)      # a changed key is present

    def __getitem__(self, k: Key) -> Key:
        if k in self.changed:
            return self.changed[k]
        if k in self.removed:
            raise KeyError(k)
        return self.base[k]

    def get(self, k: Key, default=None):
        if k in self.changed:
            return self.changed[k]
        if k in self.removed:
            return default
        return self.base.get(k, default)

    def __contains__(self, k) -> bool:
        return k in self.changed or (k not in self.removed and k in self.base)

    def __iter__(self) -> Iterator[Key]:
        for k in self.base:
            if k not in self.removed:
                yield k
        for k in self.changed:
            if k not in self.base:
                yield k

    def __len__(self) -> int:
        return len(self.base) - sum(1 for k in self.removed if k in self.base) + sum(1 for k in self.changed if k not in self.base)

    def with_(self, changed: Dict[Key, Key] = None, removed: Iterable[Key] = ()) -> "Overlay":
        """A copy with more changes or removals; the later edit wins on a key in both."""
        rm = frozenset(removed)
        return Overlay(self.base, {k: v for k, v in {**self.changed, **(changed or {})}.items() if k not in rm},
                       (self.removed | rm) - set(changed or {}))


@dataclass
class Alternative:
    """An alternative matching M' that differs from the observed one at one receive."""
    recv: Key                      # the wildcard receive that changes
    send: Key                      # the send it takes instead
    M: Mapping                     # full alternative matching (recv -> send), an Overlay of t.observed
    kind: str                      # 'swap' or 'steal'
    changed: Set[Key] = field(default_factory=set)   # receives whose match changed
    stolen_from: Optional[Key] = None                 # receive that loses its observed send

    def describe(self, t: Trace) -> str:
        r, s = t.recvs[self.recv], t.sends[self.send]
        o = t.observed_send(r)
        obs = f"{o}" if o else "unmatched in the observed run"
        if self.kind == "swap":
            extra = f"; {t.recvs[self.stolen_from]} <- {obs}"
        else:
            extra = f"; {t.recvs[self.stolen_from]} left without {s}" if self.stolen_from else ""
        return f"{r} <- {s} (observed {obs}){extra}"


def candidates(t: Trace) -> List[Alternative]:
    return candidates_full(t)[0]


class _ConsumedBefore:
    """The sends consumed by the receives of a rank posted before a given index, for queries
    in non-decreasing posting order per rank: one pass over each rank's receives in all,
    instead of one pass per wildcard receive or probe (quadratic on a rank with hundreds of
    thousands of receives, which Nekbone's communication suite has)."""

    def __init__(self, t: Trace):
        self.t = t
        self.pos: Dict[int, int] = {}
        self.taken: Dict[int, Set[Key]] = {}
        self.last: Dict[int, int] = {}

    def before(self, rank: int, idx: int) -> Set[Key]:
        assert self.last.get(rank, -1) <= idx, "queries must come in posting order per rank"
        self.last[rank] = idx
        recvs, i, taken = self.t.recvs_of(rank), self.pos.get(rank, 0), self.taken.setdefault(rank, set())
        while i < len(recvs) and recvs[i].idx < idx:
            sk = self.t.observed.get(recvs[i].key)
            if sk is not None:
                taken.add(sk)
            i += 1
        self.pos[rank] = i
        return taken


def candidates_full(t: Trace, hb=None) -> Tuple[List[Alternative], List[Tuple[str, str]]]:
    """Single-change alternatives at wildcard receives (completed or not), by the
    non-overtaking kernel: for a receive r and a source, only the earliest send that r
    accepts and that no receive posted before r has taken can be matched to r (a later one
    would overtake it), so there is at most one candidate per source. Sends that causally
    follow the receive's completion in the observed run are pruned and listed. hb is
    observed_hb(t), passed in when the caller already has it."""
    hb = hb or observed_hb(t)
    sender_of: Dict[Key, Key] = {sk: rk for rk, sk in t.observed.items()}
    out: List[Alternative] = []
    pruned: List[Tuple[str, str]] = []
    consumed = _ConsumedBefore(t)
    for r in sorted(t.recvs.values(), key=lambda r: r.key):
        if not r.wildcard:
            continue
        s_obs = t.observed_send(r)
        rc = r.complete_node()
        consumed_before = consumed.before(r.rank, r.idx)
        for src in t.sources_to(r.rank, r.comm):
            if r.src != ANY and src != r.src:
                continue
            for s in t.sends_to(src, r.rank, r.comm):
                if not s.available_to(r):
                    continue
                if s.key in consumed_before:
                    # taken by a receive posted before r: no steal, but a swap if that receive is
                    # a wildcard that could have taken r's observed message instead
                    r2k = sender_of[s.key]
                    r2 = t.recvs[r2k]
                    if s_obs is not None and r2.wildcard and r2.accepts(s_obs) and _swap_in_horizon(r, r2):
                        M2 = Overlay(t.observed, {r.key: s.key, r2k: s_obs.key})
                        out.append(Alternative(r.key, s.key, M2, "swap", {r.key, r2k}, r2k))
                    continue
                if s_obs is not None and s.key == s_obs.key:
                    break                           # the observed message is this source's head
                if rc is not None and hb(rc, s.issue_node()):
                    pruned.append((f"{r} <- {s}", "send causally follows the receive's completion"))
                    break
                r2k = sender_of.get(s.key)
                M = Overlay(t.observed, {r.key: s.key})
                if r2k is not None:
                    r2 = t.recvs[r2k]
                    if s_obs is not None and r2.accepts(s_obs) and r2.completed and _swap_in_horizon(r, r2):
                        out.append(Alternative(r.key, s.key, M.with_(changed={r2k: s_obs.key}), "swap", {r.key, r2k}, r2k))
                    out.append(Alternative(r.key, s.key, M.with_(removed=[r2k]), "steal", {r.key}, r2k))
                else:
                    out.append(Alternative(r.key, s.key, M, "steal", {r.key}, None))
                break
    return out, pruned


def _cidx(x) -> int:
    return x.complete_idx if x.complete_idx is not None else x.idx


def _swap_in_horizon(r: Recv, r2: Recv) -> bool:
    """A swap re-matches two receives of one rank; horizon rule 1 cuts after the earlier
    completion, so both must be posted before either completes for both matches to lie
    inside the kept prefix. Otherwise the swap claims no more than the steal at r does."""
    return max(r.idx, r2.idx) < min(_cidx(r), _cidx(r2))


def candidates_k(t: Trace, k: int = 2, hb=None) -> List[Alternative]:
    """Alternatives that re-match k wildcard receives on k distinct ranks, each to a kernel
    head of the single-change enumeration (steals only). The checker judges them with the
    generic check; their count is at most C(w·P, k)."""
    singles = [a for a in candidates_full(t, hb)[0] if a.kind == "steal"]
    out: List[Alternative] = []
    if k != 2:
        raise ValueError("candidates_k: only k = 2 is implemented")
    for i, a in enumerate(singles):
        for b in singles[i + 1:]:
            if a.recv[0] == b.recv[0] or a.send == b.send or a.stolen_from in (b.recv, b.stolen_from) or b.stolen_from == a.recv:
                continue
            M = Overlay(t.observed, {a.recv: a.send, b.recv: b.send}, [d for d in (a.stolen_from, b.stolen_from) if d is not None])
            out.append(Alternative(a.recv, a.send, M, "k2", {a.recv, b.recv}, a.stolen_from))
    return out


def probe_candidates(t: Trace, hb=None) -> List[Tuple[Probe, Send]]:
    """Other sends a wildcard probe could have observed: per source, only the earliest
    send the probe accepts that is not consumed by a receive posted before the probe
    (non-overtaking), and not causally after the probe."""
    hb = hb or observed_hb(t)
    out = []
    consumed = _ConsumedBefore(t)
    for p in sorted(t.probes.values(), key=lambda p: p.key):
        if not p.wildcard or not p.flag or p.seen is None:
            continue
        consumed_before = consumed.before(p.rank, p.idx)
        seen_src = t.sends[p.seen].rank
        for src in sorted({s.rank for s in t.sends.values()}):
            if src == seen_src:
                continue
            for s in t.sends_of(src):
                if not p.accepts(s) or s.key in consumed_before or s.cancelled:
                    continue
                if s.reserved_idx is not None and s.reserved_idx < p.idx:
                    continue   # removed from the queue by an earlier matched Mprobe
                if not hb(p.complete_node(), s.issue_node()):
                    out.append((p, s))
                break   # FIFO: only the head of this source's queue can be observed
    return out


def matching_order_ok(t: Trace, M: Dict[Key, Key], kept_recv, ranks: Optional[Set[int]] = None) -> Optional[str]:
    """MPI matching order for M restricted to receives with kept_recv(key) (posting-index
    based), optionally only for receives on the given ranks (the observed matching is
    valid by construction, so only ranks touched by a change need re-checking).
    Returns None if OK, else a reason.
    (a) sender-side FIFO: if r takes s, every earlier send s' from the same (src, dst,
        comm) accepted by r is matched to a receive at dst posted before r;
    (b) receiver-side order: every receive r' at dst posted before r that accepts s is
        matched (a never-completed receive posted before r would take s), and if it is
        matched to a send from the same source, that send precedes s.
    Each pair costs a constant number of lookups (and one per tag of the channel for an
    ANY_TAG receive): the latest earlier send of each tag stands for the earlier sends of that
    tag, the latest earlier receive of each envelope class for its class, since the pairs of
    the rank are all checked and each obligation chains through them (Trace.earlier_sends,
    Trace.earlier_competitors); an unmatched receive is found by the earliest one per class."""
    post_idx: Dict[Key, int] = {rk: t.recvs[rk].idx for rk in M}
    matched_send: Dict[Key, Key] = {sk: rk for rk, sk in M.items()}
    # the earliest receive without a message in M, per (rank, comm, source or ANY, tag or ANY)
    first_unmatched: Dict[Tuple[int, int, int, int], int] = {}
    for r in t.recvs.values():
        if r.key not in M and not r.cancelled and not r.mrecv and (ranks is None or r.rank in ranks):
            k = (r.rank, r.comm, r.src, r.tag)
            if r.idx < first_unmatched.get(k, 10**18):
                first_unmatched[k] = r.idx
    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        if ranks is not None and r.rank not in ranks:
            continue
        if not kept_recv(rk):
            continue
        if not r.accepts(s):
            return f"{r} does not accept {s}"
        # (a) sender-side FIFO
        for s2 in t.earlier_sends(r, s):
            r2k = matched_send.get(s2.key)
            if r2k is None or post_idx[r2k] > r.idx:
                return f"non-overtaking: {s2} would reach {r} before {s}"
        # (b) receiver-side order: an earlier receive without a message, then earlier receives
        # matched to a later message of s's channel
        for src in (s.rank, ANY):
            for tag in (s.tag, ANY):
                if first_unmatched.get((r.rank, r.comm, src, tag), 10**18) < r.idx:
                    return f"receiver order: an earlier receive of rank {r.rank} accepts {s} and has no message in this matching"
        for r2 in t.earlier_competitors(r, s):
            if r2.key not in M:
                continue
            s2 = t.sends[M[r2.key]]
            if s2.rank == s.rank and s2.dst == s.dst and s2.comm == s.comm and s2.idx > s.idx:
                return f"receiver order: earlier {r2} would take {s} before {s2}"
    return None


def affected_ranks(t: Trace, M: Dict[Key, Key], changed) -> Set[int]:
    """Ranks whose matching-order obligations can differ from the observed run: the ranks
    of changed receives, of receives that lost their observed send, and of the sends moved."""
    out: Set[int] = set()
    for rk in changed:
        out.add(t.recvs[rk].rank)
        if rk in M:
            out.add(t.sends[M[rk]].rank)
    for rk, sk in t.observed.items():
        if M.get(rk) != sk:
            out.add(t.recvs[rk].rank)
            out.add(t.sends[sk].rank)
    return out

