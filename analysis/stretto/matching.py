"""Constraint graph, observed happens-before, candidate alternative matchings and the
MPI matching-order checks. See docs/model.md section 2."""
from __future__ import annotations
import itertools
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Set, Tuple

from .trace import ANY, Coll, Key, Probe, Recv, Send, Trace

Node = Tuple[int, int, str]

# Collective synchronisation shapes (MPI 4.1 §7.1: a collective may return as soon as the
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
                probe_alt: Optional[Dict[Key, Key]] = None, issued: Optional[Dict[int, int]] = None,
                ready: Optional[Collection[Key]] = None, thread_orient: bool = True) -> Graph:
    """Constraint graph over the kept prefix (idx < cut[rank]; cut=None keeps all).
    B=None means infinite buffering. strong_only drops edges whose necessity is not
    guaranteed by the standard (collectives of unknown shape, probe observations): use it
    when a cycle is to be reported as a certain deadlock. probe_alt maps a probe to the
    send it observes instead of the one it saw in the run (an Iprobe that returned false
    included, a flag flip), or to None for one that now observes nothing. issued names, per rank, a
    blocking send at the cut whose issue node is kept although it never completes. ready
    names the ready-mode sends whose edge issue(r) -> issue(s) is drawn (None: every one),
    for an execution in which the others are issued before their receive."""
    def kept(rank: int, idx: int) -> bool:
        return cut is None or idx < cut.get(rank, 10**9)

    nodes: List[Node] = []
    g = Graph(nodes, {})
    succ = g.succ
    for rank, recs in t.ranks.items():
        prev = None
        end = len(recs) if cut is None else min(len(recs), max(0, cut.get(rank, 10**9)))
        # program order, the bulk of the graph, written out (Graph.add per edge was a fifth of
        # the analysis of AMG's trace at 32 processes): each node is new here, so its list is
        # created with its first edge
        for idx in range(end):
            i, c = (rank, idx, "i"), (rank, idx, "c")
            nodes.append(i)
            nodes.append(c)
            succ[i] = [c]
            if prev is not None:
                succ[prev] = [i]
            prev = c
        if end < len(recs) and issued and issued.get(rank) == end:
            i = (rank, end, "i")
            nodes.append(i)
            if prev is not None:
                succ[prev] = [i]
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
        succ.setdefault(s.issue_node(), []).append(m)        # the message is issued first
        if m[2] == "m":
            succ.setdefault(r.issue_node(), []).append(m)    # the receive is posted first
            rc = r.complete_node()
            if rc is not None and rc in node_set:
                succ.setdefault(m, []).append(rc)            # it completes after the match
        if send_blocks(s, B):
            sc = s.complete_node()
            if sc is not None and sc in node_set:
                g.add(m, sc)                                  # rendezvous: the send completes after it
        if s.ready_mode and (ready is None or s.key in ready):
            g.add(r.issue_node(), s.issue_node())             # ready send: receive posted first
        # receiver side: an earlier receive that accepts s is matched before r takes s (another
        # receive, so another node)
        for r2 in t.earlier_competitors(r, s):
            m2 = mnode.get(r2.key)
            if m2 is not None:
                succ.setdefault(m2, []).append(m)
        # sender side: an earlier message of s's channel that r accepts is matched first, by
        # another receive than r, which takes s
        for s2 in t.earlier_sends(r, s):
            r2k = recv_of.get(s2.key)
            m2 = mnode.get(r2k) if r2k is not None else None
            if m2 is not None:
                succ.setdefault(m2, []).append(m)
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
                if a.root is not None and a.proc == a.root:
                    for b in kept_c:
                        if a is not b and b.complete_node() in node_set:
                            g.add(a.issue_node(), b.complete_node())
        elif shape == "root-in":
            for b in kept_c:
                if b.root is not None and b.proc == b.root and b.complete_node() in node_set:
                    for a in kept_c:
                        if a is not b:
                            g.add(a.issue_node(), b.complete_node())
        elif shape == "prefix":
            # chained hubs: issue(a_i) -> H_i -> H_{i+1} -> ... and H_i -> complete(a_{i+1}),
            # so that a_i precedes the completion of every later member
            order = sorted(kept_c, key=lambda c: pos.get(c.proc, 0))
            for i, c in enumerate(order):
                h = hub(comm, k, i)
                nodes.append(h)
                g.add(c.issue_node(), h)
                if i + 1 < len(order):
                    g.add(h, hub(comm, k, i + 1))
                    if order[i + 1].complete_node() in node_set:
                        g.add(h, order[i + 1].complete_node())
    if t.multi:
        # several chains (stretto.threads): synchronisation, and an orientation per concurrent
        # pair whose order decides a match (left out for the observed happens-before)
        from .threads import thread_edges
        for a, b in thread_edges(t, M, node_set, mnode, probe_alt, thread_orient):
            if both(a, b):
                g.add(a, b)
    for a, b in extra_edges:
        if both(a, b):
            g.add(a, b)
    return g


def observed_hb(t: Trace, B: Optional[int] = None):
    """Predicate hb(a, b) over nodes: a happens-before b in every execution that has the
    observed matching (program order, delivery, collectives, rendezvous if B given)."""
    return build_graph(t, t.observed, cut=None, B=B, thread_orient=False).reaches


def observed_ready_errors(t: Trace) -> Tuple[List[Tuple[Recv, Send]], List[Send]]:
    """Ready-mode sends that the recorded run issued before the receive it matched them to was
    posted. Such a run is erroneous (MPI 4.1 §4.4; Open MPI and MPICH usually deliver the send
    all the same), and the edge issue(r) -> issue(s) closes a cycle in its observed graph at
    every threshold. Judged over guaranteed edges at B = inf, what every execution with the
    observed matching has, it returns (certain, others): the pairs (r, s) whose receive every
    such execution posts after issuing s (the graph without ready edges reaches issue(r) from
    issue(s)), and the ready sends of a cycle that remains once the ready edges of those pairs
    are left out, at least one of which is issued before its receive. Both are empty when the
    graph is acyclic, or cyclic without any ready edge (inconsistent for another reason). A
    trace whose matched sends include no ready send costs one pass over its matching."""
    ready = {sk: rk for rk, sk in t.observed.items() if t.sends[sk].ready_mode}
    if not ready or build_graph(t, t.observed, None, None, strong_only=True).find_cycle() is None:
        return [], []
    bare = build_graph(t, t.observed, None, None, strong_only=True, ready=())
    if bare.find_cycle() is not None:
        return [], []
    certain = [(t.recvs[rk], t.sends[sk]) for sk, rk in sorted(ready.items())
               if bare.reaches(t.sends[sk].issue_node(), t.recvs[rk].issue_node())]
    rest = set(ready) - {s.key for _, s in certain}
    cyc = build_graph(t, t.observed, None, None, strong_only=True, ready=rest).find_cycle() if rest else None
    others: List[Send] = []
    if cyc:
        edges = set(zip(cyc, cyc[1:] + cyc[:1]))
        others = [t.sends[sk] for sk in sorted(rest)
                  if (t.recvs[ready[sk]].issue_node(), t.sends[sk].issue_node()) in edges]
    return certain, others


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
    follow the receive's completion in the observed run, by a path the alternative keeps
    (_follows), are pruned and listed. hb is observed_hb(t), passed in when the caller
    already has it."""
    if t.multi:
        from .threads import candidates as threads_candidates
        return threads_candidates(t)
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
                r2k = sender_of.get(s.key)
                if rc is not None and _follows(hb, rc, s, r2k):
                    pruned.append((f"{r} <- {s}", "send causally follows the receive's completion"))
                    break
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


def _follows(hb, rc: Node, s: Send, r2k: Optional[Key]) -> bool:
    """Does issue(s) follow rc, the completion of the receive that would take s, in the
    observed graph by a path that an alternative giving s to that receive keeps? r2k is the
    receive that took s in the run, if any. The alternative drops the observed graph's edges
    of the pairs it changes, and of those only one matters here: the ready-send edge
    issue(r2k) -> issue(s), which it replaces by one from the new receive. The others touch
    a node that follows issue(s) or precedes rc, which no path from rc into issue(s) passes
    (the graph is acyclic), or leave rc itself, at a blocking receive, for a node that the
    alternative's horizon removes, so that the pruned pair loses its witness all the same
    (docs/model.md, section 2, pruning). A path that counts therefore enters issue(s) by
    program order."""
    if not s.ready_mode or r2k is None:
        return hb(rc, s.issue_node())
    prev = (s.rank, s.idx - 1, "c")             # program-order predecessor of issue(s)
    return s.idx > 0 and (prev == rc or hb(rc, prev))


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
    if t.multi:
        from .threads import probe_candidates as threads_probes
        return threads_probes(t)
    hb = hb or observed_hb(t)
    out = []
    consumed = _ConsumedBefore(t)
    # per (rank, source, comm) queue, how many sends at its head are gone for every probe
    # from here on: consumed by a receive posted earlier, cancelled, or reserved by an earlier
    # matched Mprobe. Probes come in posting order per rank, so the prefix only grows and
    # each queue is walked once instead of once per probe.
    gone: Dict[Tuple[int, int, int], int] = {}
    for p in sorted(t.probes.values(), key=lambda p: p.key):
        if not p.wildcard or not p.flag or p.seen is None:
            continue
        consumed_before = consumed.before(p.rank, p.idx)
        seen_src = t.sends[p.seen].rank
        for src in t.sources_to(p.rank, p.comm):
            if src == seen_src:
                continue
            q, k = t.sends_to(src, p.rank, p.comm), (p.rank, src, p.comm)
            i = gone.get(k, 0)
            while i < len(q) and (q[i].key in consumed_before or q[i].cancelled
                                  or (q[i].reserved_idx is not None and q[i].reserved_idx < p.idx)):
                i += 1
            gone[k] = i
            for s in itertools.islice(q, i, None):
                if not p.accepts(s) or s.key in consumed_before or s.cancelled:
                    continue
                if s.reserved_idx is not None and s.reserved_idx < p.idx:
                    continue   # removed from the queue by an earlier matched Mprobe
                if not hb(p.complete_node(), s.issue_node()):
                    out.append((p, s))
                break   # FIFO: only the head of this source's queue can be observed
    return out


POLL_PROBES = ("iprobe", "improbe")     # the probes whose flag can go either way


def _canonical_polls(t: Trace, items) -> list:
    """Of items (x, rank, record index, identity), in record order per rank, the first of each
    identity and call site among the failed polls of one key (Trace.poll_key: the rank's calls
    before them that are not failed polls; in the trace, the polls of one segment). These
    records have only failed polls between them, which have no edge but program order, so a
    flip at a later one is the flip at the first record of its identity, with the same horizon
    and the same graph up to failed polls; and the replay library forces the first call of the
    identity with that key (STRETTO_PIN_FLIP)."""
    out, seen = [], set()
    for x, rank, idx, ident in items:
        k = (rank, t.poll_key.get((rank, idx)), ident, t.site_of(rank, idx))
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def flip_candidates(t: Trace, hb=None) -> List[Tuple[Probe, Optional[Send]]]:
    """Flag flips of polling probes (docs/model.md §3, flag flips): (p, s) for an Iprobe or
    Improbe p that returned false and could have returned true with s, and (p, None) for one
    that returned true and could have returned false. For a failed probe and each source it
    accepts, s is the earliest send of that source's queue that p accepts and that no receive
    posted before p took, no matched probe before p removed and no cancel withdrew (the
    non-overtaking kernel, as for probe_candidates), and that does not causally follow p's
    completion in the observed run. Failed probes are taken once per identity and poll key
    (_canonical_polls). A true probe returning false needs no message: a false flag
    is always a possible result, the model guaranteeing no progress, so every Iprobe or
    Improbe that saw a message is a candidate."""
    if t.multi:
        return []                    # not for several chains yet (docs/threads.md)
    hb = hb or observed_hb(t)
    out: List[Tuple[Probe, Optional[Send]]] = []
    failed = [(p, p.rank, p.idx, (p.kind, p.src, p.tag, p.comm)) for p in sorted(t.probes.values(), key=lambda p: p.key)
              if p.kind in POLL_PROBES and p.completed and not p.flag]
    canon = {p.key for p in _canonical_polls(t, failed)}
    consumed = _ConsumedBefore(t)
    gone: Dict[Tuple[int, int, int], int] = {}
    for p in sorted(t.probes.values(), key=lambda p: p.key):
        if p.kind not in POLL_PROBES or not p.completed:
            continue
        if p.flag:
            if p.seen is not None:
                out.append((p, None))
            continue
        if p.key not in canon:
            continue
        consumed_before = consumed.before(p.rank, p.idx)
        for src in t.sources_to(p.rank, p.comm):
            if p.src != ANY and src != p.src:
                continue
            q, k = t.sends_to(src, p.rank, p.comm), (p.rank, src, p.comm)
            i = gone.get(k, 0)
            while i < len(q) and (q[i].key in consumed_before or q[i].cancelled
                                  or (q[i].reserved_idx is not None and q[i].reserved_idx < p.idx)):
                i += 1
            gone[k] = i
            for s in itertools.islice(q, i, None):
                if not p.accepts(s) or s.key in consumed_before or s.cancelled:
                    continue
                if s.reserved_idx is not None and s.reserved_idx < p.idx:
                    continue
                if not hb(p.complete_node(), s.issue_node()):
                    out.append((p, s))
                break   # FIFO: only the head of this source's queue can be seen
    return out


def test_flip_candidates(t: Trace, hb=None) -> List[Tuple[object, Recv, Send]]:
    """(record, r, s) for an MPI_Test or MPI_Request_get_status on the request of a receive r
    that returned false, while r was later completed in the run with s: the call could have
    found r complete, had r been matched to s before it (docs/model.md §3, flag flips). s must
    not causally follow the call's completion in the observed run. Calls on several requests
    (Testall, Testany, Testsome) are not flipped. Taken once per request and poll key."""
    if t.multi:
        return []                    # not for several chains yet (docs/threads.md)
    hb = hb or observed_hb(t)
    cands = []
    for rank, recs in t.ranks.items():
        for rec in recs:
            if (rank, rec.idx) not in t.poll_key or rec.op != "wait" or rec.raw.get("kind") not in ("test", "getstatus"):
                continue
            reqs = [q for q in rec.raw.get("reqs", []) if q is not None and q >= 0]
            if len(reqs) != 1:
                continue
            r = t.recv_by_req(rank, reqs[0])
            if r is None or not r.completed or r.complete_idx is None or not (r.idx < rec.idx < r.complete_idx):
                continue
            s = t.observed_send(r)
            if s is None or hb(rec.complete, s.issue_node()):
                continue
            cands.append(((rec, r, s), rank, rec.idx, (rec.raw.get("kind"), r.key)))
    return _canonical_polls(t, cands)


def matching_order_ok(t: Trace, M: Dict[Key, Key], kept_recv, ranks: Optional[Set[int]] = None,
                      kept_probe=None, probe_alt: Optional[Dict[Key, Key]] = None) -> Optional[str]:
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
    if t.multi:
        from .threads import matching_order_ok as threads_order
        return threads_order(t, M, kept_recv, ranks, kept_probe, probe_alt)
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
    # (c) a probe reports what a receive posted at its point would take: the earlier messages of
    # its channel that it accepts are matched to receives posted before it, and every receive
    # posted before it that accepts the reported message is matched to another message
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
                if r2k is None or post_idx[r2k] > q.idx:
                    return f"probe order: {s2} would be reported by {q} before {s}"
            for src in (s.rank, ANY):
                for tag in (s.tag, ANY):
                    if first_unmatched.get((q.rank, q.comm, src, tag), 10**18) < q.idx:
                        return f"probe order: an earlier receive of rank {q.rank} accepts {s} and has no message in this matching"
            rk = matched_send.get(sk)
            if rk is not None and rk[0] == q.rank and post_idx[rk] < q.idx and not t.recvs[rk].mrecv:
                return f"probe order: {t.recvs[rk]}, posted before {q}, takes {s}"
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
    if t.multi:
        # a split rank's obligations involve all its chains (stretto.threads)
        out = {c for x in out for c in t.chains_of(t.proc.get(x, x))}
    return out

