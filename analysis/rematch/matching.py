"""Constraint graph, observed happens-before, candidate alternative matchings and the
MPI matching-order checks. See docs/model.md section 2."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

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


@dataclass
class Graph:
    nodes: List[Node]
    succ: Dict[Node, Set[Node]]
    _reach_cache: Dict[Node, Set[Node]] = field(default_factory=dict)

    def add(self, a: Node, b: Node):
        if a == b:
            return
        self.succ.setdefault(a, set()).add(b)

    def reach_set(self, a: Node) -> Set[Node]:
        if a in self._reach_cache:
            return self._reach_cache[a]
        seen, stack = set(), [a]
        while stack:
            x = stack.pop()
            for y in self.succ.get(x, ()):
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        self._reach_cache[a] = seen
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
            stack = [(start, iter(sorted(self.succ.get(start, ()))))]
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
                    stack.append((nxt, iter(sorted(self.succ.get(nxt, ())))))
                elif color[nxt] == GREY:
                    cyc, x = [nxt], node
                    while x != nxt:
                        cyc.append(x)
                        x = parent[x]
                    cyc.reverse()
                    return cyc
        return None


def send_blocks(s: Send, B: Optional[int]) -> bool:
    """Does the send's request complete only once matched, under threshold B?"""
    if s.rendezvous_always:
        return True
    if s.never_blocks:
        return False
    return B is not None and s.bytes > B


def build_graph(t: Trace, M: Dict[Key, Key], cut: Optional[Dict[int, int]], B: Optional[int],
                extra_edges: Iterable[Tuple[Node, Node]] = (), strong_only: bool = False) -> Graph:
    """Constraint graph over the kept prefix (idx < cut[rank]; cut=None keeps all).
    B=None means infinite buffering. strong_only drops edges whose necessity is not
    guaranteed by the standard (collectives of unknown shape, probe observations): use it
    when a cycle is to be reported as a certain deadlock."""
    def kept(rank: int, idx: int) -> bool:
        return cut is None or idx < cut.get(rank, 10**9)

    nodes: List[Node] = []
    g = Graph(nodes, {})
    for rank, recs in t.ranks.items():
        prev = None
        for rec in recs:
            if not kept(rank, rec.idx):
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

    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        rc, sc = r.complete_node(), s.complete_node()
        if both(s.issue_node(), rc):
            g.add(s.issue_node(), rc)                         # delivery
        if send_blocks(s, B) and both(r.issue_node(), sc):
            g.add(r.issue_node(), sc)                         # rendezvous
        if s.ready_mode and both(r.issue_node(), s.issue_node()):
            g.add(r.issue_node(), s.issue_node())             # ready send: receive posted first
        # receiver-side order: an earlier-posted receive that accepts s must have taken its
        # own message before s arrives (same-source pairs are covered by FIFO). Cached for
        # pairs that are unchanged from the observed matching.
        if t.observed.get(rk) == sk:
            for a, b in t.rs_edges_observed(rk):
                if both(a, b):
                    g.add(a, b)
        else:
            for r2 in t.wild_recvs_of(r.rank):
                if r2.idx >= r.idx:
                    break
                if r2.accepts(s) and r2.key in M:
                    s2 = t.sends[M[r2.key]]
                    if s2.rank != s.rank and both(s2.issue_node(), rc):
                        g.add(s2.issue_node(), rc)
    if not strong_only:
        for p in t.probes.values():
            if p.seen is not None and both(t.sends[p.seen].issue_node(), p.complete_node()):
                g.add(t.sends[p.seen].issue_node(), p.complete_node())
    for cs in t.coll_sets():
        shape, known = coll_shape(cs[0].name)
        if strong_only and not known:
            continue
        members = t.comm_ranks.get(cs[0].comm, [])
        pos = {r: i for i, r in enumerate(members)}
        for a in cs:
            for b in cs:
                if a is b or not both(a.issue_node(), b.complete_node()):
                    continue
                if shape == "all":
                    g.add(a.issue_node(), b.complete_node())
                elif shape == "root-out":
                    if a.root is not None and a.rank == a.root:
                        g.add(a.issue_node(), b.complete_node())
                elif shape == "root-in":
                    if b.root is not None and b.rank == b.root:
                        g.add(a.issue_node(), b.complete_node())
                elif shape == "prefix":
                    if pos.get(a.rank, 0) < pos.get(b.rank, 0):
                        g.add(a.issue_node(), b.complete_node())
    for a, b in extra_edges:
        if both(a, b):
            g.add(a, b)
    return g


def observed_hb(t: Trace, B: Optional[int] = None):
    """Predicate hb(a, b) over nodes: a happens-before b in every execution that has the
    observed matching (program order, delivery, collectives, rendezvous if B given)."""
    return build_graph(t, t.observed, cut=None, B=B).reaches


# --------------------------------------------------------------- candidates

@dataclass
class Alternative:
    """An alternative matching M' that differs from the observed one at one receive."""
    recv: Key                      # the wildcard receive that changes
    send: Key                      # the send it takes instead
    M: Dict[Key, Key]              # full alternative matching (recv -> send)
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


def candidates_full(t: Trace) -> Tuple[List[Alternative], List[Tuple[str, str]]]:
    """All single-change alternatives at wildcard receives (completed or not). Sends that
    causally follow the receive's completion in the observed run are pruned and listed."""
    hb = observed_hb(t)
    sender_of: Dict[Key, Key] = {sk: rk for rk, sk in t.observed.items()}
    out: List[Alternative] = []
    pruned: List[Tuple[str, str]] = []
    for r in sorted(t.recvs.values(), key=lambda r: r.key):
        if not r.wildcard:
            continue
        s_obs = t.observed_send(r)
        rc = r.complete_node()
        for s in sorted(t.sends.values(), key=lambda s: s.key):
            if s_obs is not None and s.key == s_obs.key:
                continue
            if not r.accepts(s):
                continue
            if rc is not None and hb(rc, s.issue_node()):
                pruned.append((f"{r} <- {s}", "send causally follows the receive's completion"))
                continue
            r2k = sender_of.get(s.key)
            M = dict(t.observed)
            M[r.key] = s.key
            if r2k is not None:
                r2 = t.recvs[r2k]
                if s_obs is not None and r2.accepts(s_obs):
                    M2 = dict(M)
                    M2[r2k] = s_obs.key
                    out.append(Alternative(r.key, s.key, M2, "swap", {r.key, r2k}, r2k))
                del M[r2k]
                out.append(Alternative(r.key, s.key, M, "steal", {r.key}, r2k))
            else:
                out.append(Alternative(r.key, s.key, M, "steal", {r.key}, None))
    return out, pruned


def probe_candidates(t: Trace) -> List[Tuple[Probe, Send]]:
    """Other sends a wildcard probe could have observed: per source, only the earliest
    send the probe accepts that is not consumed by a receive posted before the probe
    (non-overtaking), and not causally after the probe."""
    hb = observed_hb(t)
    out = []
    for p in sorted(t.probes.values(), key=lambda p: p.key):
        if not p.wildcard or not p.flag or p.seen is None:
            continue
        consumed_before = {t.observed[r.key] for r in t.recvs_of(p.rank) if r.idx < p.idx and r.key in t.observed}
        seen_src = t.sends[p.seen].rank
        for src in sorted({s.rank for s in t.sends.values()}):
            if src == seen_src:
                continue
            for s in t.sends_of(src):
                if not p.accepts(s) or s.key in consumed_before:
                    continue
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
        matched to a send from the same source, that send precedes s."""
    post_idx: Dict[Key, int] = {rk: t.recvs[rk].idx for rk in M}
    matched_send: Dict[Key, Key] = {sk: rk for rk, sk in M.items()}
    # receives without a message in M, per rank (few): an earlier one that accepts s would take it
    unmatched_by_rank: Dict[int, List[Recv]] = {}
    for r in t.recvs.values():
        if r.key not in M and (ranks is None or r.rank in ranks):
            unmatched_by_rank.setdefault(r.rank, []).append(r)
    for rk, sk in M.items():
        r, s = t.recvs[rk], t.sends[sk]
        if ranks is not None and r.rank not in ranks:
            continue
        if not kept_recv(rk):
            continue
        if not r.accepts(s):
            return f"{r} does not accept {s}"
        # (a) sender-side FIFO
        for s2 in t.sends_to(s.rank, s.dst, s.comm):
            if s2.idx >= s.idx:
                break
            if r.accepts(s2):
                r2k = matched_send.get(s2.key)
                if r2k is None or post_idx[r2k] > r.idx:
                    return f"non-overtaking: {s2} would reach {r} before {s}"
        # (b) receiver-side order: earlier unmatched receives, then earlier wildcard-source receives
        for r2 in unmatched_by_rank.get(r.rank, ()):
            if r2.idx < r.idx and r2.accepts(s):
                return f"receiver order: earlier {r2} accepts {s} and has no message in this matching"
        for r2 in t.wild_recvs_of(r.rank):
            if r2.idx >= r.idx:
                break
            if not r2.accepts(s) or r2.key not in M:
                continue
            s2 = t.sends[M[r2.key]]
            if s2.rank == s.rank and s2.dst == s.dst and s2.comm == s.comm and s2.idx > s.idx:
                return f"receiver order: earlier {r2} would take {s} before {s2}"
        # a specific-source earlier receive matched to a later send from s's own source is the
        # mirror of (a) for that receive's pair and is caught there
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


# backwards-compatible name
non_overtaking_ok = matching_order_ok
