"""Fast L1 checks for single-change alternatives (docs/model.md §3).

feasible.check builds the horizon closure and the constraint graph from scratch for every
alternative and every threshold, O(n + m) each. The checks here answer the same questions
from indexes built once per trace and once per threshold:

  * the horizon closure runs as a worklist over ranks: when a rank is cut at an index, the
    earliest receive, rendezvous sender, collective or probe that the removed suffix drags
    along is found by a suffix-minimum query on a per-pair-of-ranks list, so one check costs
    O(P^2 log n) lookups instead of a scan of the trace;
  * matching order follows from the kernel that enumerated the candidate (the send is the
    earliest unconsumed one of its source), so only the receives of the changed rank need a
    look;
  * for a steal at a blocking receive, acyclicity reduces to reachability in the observed
    graph of the threshold (vector clocks computed once per threshold): rule 1 cuts the
    rank right after the receive, so a cycle through the new match must leave it through
    a rendezvous send's completion and return through one of its predecessors.

Steals at nonblocking receives, swaps, and steals whose read bounds (stretto.reads) keep records
of the rank after the receive go to the generic check; the closure applies the bounds like
horizon.compute_cut. The generic check stays as the reference; the tests compare the two on
every alternative.
"""
from __future__ import annotations
from array import array
from bisect import bisect_left, bisect_right
from heapq import heappop, heappush
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .feasible import Verdict
from .horizon import coll_can_complete
from .matching import Alternative, Graph, build_graph, is_hub, is_match, match_node, send_blocks
from .trace import ANY, Coll, Key, Probe, Recv, Send, Trace

INF = 10 ** 9


def _cidx(x) -> int:
    return x.complete_idx if x.complete_idx is not None else x.idx


def lamport_times(t: Trace, g: Graph) -> Optional[Dict[int, array]]:
    """The Lamport time of every record of the observed run in graph g: for each rank an array
    with the length of the longest path to record k's issue node at 2k and to its completion
    node at 2k + 1. None if g is cyclic."""
    lam = {rank: array("l", [0]) * (2 * len(recs)) for rank, recs in t.ranks.items()}
    other: Dict = {}                      # match nodes and collective hubs

    def get(n) -> int:
        if n[0] >= 0 and n[2] != "m":
            return lam[n[0]][2 * n[1] + (n[2] == "c")]
        return other.get(n, 0)

    indeg: Dict = {}
    for succ in g.succ.values():
        for b in succ:
            indeg[b] = indeg.get(b, 0) + 1
    ready = [n for n in g.nodes if n not in indeg]
    while ready:
        n = ready.pop()
        v = get(n) + 1
        for b in g.succ.get(n, ()):
            if v > get(b):
                if b[0] >= 0 and b[2] != "m":
                    lam[b[0]][2 * b[1] + (b[2] == "c")] = v
                else:
                    other[b] = v
            indeg[b] -= 1
            if indeg[b] == 0:
                ready.append(b)
    if any(indeg.values()):
        return None
    return lam


class Suffix2:
    """Suffix minima of an integer array, with one excludable position: query(i, x) is the
    smallest value at a position >= i other than x, and where it is."""
    __slots__ = ("m1", "p1", "m2", "p2")

    def __init__(self, vals: List[int]):
        n = len(vals)
        m1, p1, m2, p2 = [INF] * (n + 1), [-1] * (n + 1), [INF] * (n + 1), [-1] * (n + 1)
        for i in range(n - 1, -1, -1):
            v = vals[i]
            if v <= m1[i + 1]:
                m1[i], p1[i], m2[i], p2[i] = v, i, m1[i + 1], p1[i + 1]
            elif v <= m2[i + 1]:
                m1[i], p1[i], m2[i], p2[i] = m1[i + 1], p1[i + 1], v, i
            else:
                m1[i], p1[i], m2[i], p2[i] = m1[i + 1], p1[i + 1], m2[i + 1], p2[i + 1]
        self.m1, self.p1, self.m2, self.p2 = m1, p1, m2, p2

    def query(self, i: int, exclude: int = -1) -> Tuple[int, int]:
        if self.p1[i] == exclude:
            return self.m2[i], self.p2[i]
        return self.m1[i], self.p1[i]


class Indexes:
    """Threshold-independent indexes of a trace (Trace.fast_indexes()). Each group is built on
    first use: a trace without wildcard receives, probes or orphans never touches the
    cascade indexes, and loading it costs nothing extra."""

    def __init__(self, t: Trace):
        self.t = t
        self.length = {rank: len(recs) for rank, recs in t.ranks.items()}
        self.recv_of0: Dict[Key, Key] = {sk: rk for rk, sk in t.observed.items()}
        self._sends = self._recvs = self._colls = self._unmatched = None
        self._lam = None                  # Lamport times (lamport()); False if unavailable
        self._acyc: Dict[Optional[int], bool] = {}   # threshold -> observed graph acyclic (acyclic_known)
        self._acyc_strong: Dict[Optional[int], bool] = {}   # the same over guaranteed edges
        # receives without an observed send, per rank in posting order; orphan sends
        self.unmatched: Dict[int, List[Recv]] = {}
        for r in sorted(t.recvs.values(), key=lambda r: (r.rank, r.idx)):
            if r.key not in t.observed:
                self.unmatched.setdefault(r.rank, []).append(r)
        self.unmatched_completed = [r for v in self.unmatched.values() for r in v if r.completed]
        self.orphans = [s for s in t.sends.values() if s.key not in self.recv_of0 and s.completed]
        self.orphan_ready = [s for s in t.sends.values() if s.ready_mode and s.key not in self.recv_of0]
        # posting indexes of every receive of a rank, so that a scan of the receives posted
        # after a given one starts by bisection and not from the rank's first receive
        self.recv_idx: Dict[int, List[int]] = {rank: [r.idx for r in t.recvs_of(rank)] for rank in t.ranks}

    def _build_sends(self) -> None:
        """Sends of R to Q in issue order, with the rule-2 (receiver completion) and rule-5
        (earliest probe) suffix minima."""
        t = self.t
        self.sq: Dict[Tuple[int, int], List[Send]] = {}
        for s in sorted(t.sends.values(), key=lambda s: (s.rank, s.idx)):
            self.sq.setdefault((s.rank, s.dst), []).append(s)
        self.sq_idx = {k: [s.idx for s in v] for k, v in self.sq.items()}
        self.sq_pos: Dict[Key, int] = {s.key: i for v in self.sq.values() for i, s in enumerate(v)}
        self.targets: Dict[int, List[int]] = {}
        for R, Q in sorted(self.sq):
            self.targets.setdefault(R, []).append(Q)
        probe_min: Dict[Key, int] = {}
        self.probe_at: Dict[Tuple[int, int], Probe] = {}
        for p in t.probes.values():
            self.probe_at[(p.rank, p.idx)] = p
            if p.completed and p.flag and p.seen is not None:
                probe_min[p.seen] = min(probe_min.get(p.seen, INF), p.idx)
        r0 = self.recv_of0
        self.r2 = {k: Suffix2([_cidx(t.recvs[r0[s.key]]) if s.key in r0 and t.recvs[r0[s.key]].completed else INF for s in v])
                   for k, v in self.sq.items()}
        self.r5 = {k: Suffix2([probe_min.get(s.key, INF) for s in v]) for k, v in self.sq.items()}
        # the same, record by record, for the closure's short spans (Observed.closure)
        self.probe_min = probe_min
        self.send_at: Dict[Tuple[int, int], Send] = {(s.rank, s.idx): s for s in t.sends.values()}
        self._sends = True

    def _build_recvs(self) -> None:
        """Receives on Q matched to sends from S, in posting order (rule 3)."""
        t = self.t
        self.rq: Dict[Tuple[int, int], List[Recv]] = {}
        for r in sorted(t.recvs.values(), key=lambda r: (r.rank, r.idx)):
            sk = t.observed.get(r.key)
            if sk is not None:
                self.rq.setdefault((r.rank, sk[0]), []).append(r)
        self.rq_idx = {k: [r.idx for r in v] for k, v in self.rq.items()}
        self.rq_pos: Dict[Key, int] = {r.key: i for v in self.rq.values() for i, r in enumerate(v)}
        self.senders_to: Dict[int, List[int]] = {}
        for Q, S in sorted(self.rq):
            self.senders_to.setdefault(Q, []).append(S)
        # rule 6: the matched receives of a rank by (comm, source, tag) pattern, in posting
        # order, so that a pending receive finds the first later receive whose message it
        # accepts with one bisection (ANY stands for either wildcard); from that, each
        # matched receive's competitor (the posting index of that first later receive, INF
        # if none) and, per (R, Q), the suffix minima of the competitors of the receives of
        # the sends R -> Q, so that cutting R's sends from some index on cuts Q at the
        # smallest competitor in one query, as rule 2 cuts at the smallest completion
        self.r6: Dict[Tuple[int, int, int, int], List[Tuple[int, Key]]] = {}
        for v in self.rq.values():
            for r in v:
                s = t.sends[t.observed[r.key]]
                for src in (ANY, s.rank):
                    for tag in (ANY, s.tag):
                        self.r6.setdefault((r.rank, r.comm, src, tag), []).append((r.idx, r.key))
        for v in self.r6.values():
            v.sort()
        self.r6_idx = {k: [i for i, _ in v] for k, v in self.r6.items()}
        self.recv_at: Dict[Tuple[int, int], Recv] = {(r.rank, r.idx): r for r in t.recvs.values()}
        # rule 6 for probes: the probes that report a message, by the same patterns of that
        # message, so that a pending receive finds the first later probe it would deprive
        self.p6: Dict[Tuple[int, int, int, int], List[int]] = {}
        for p in t.probes.values():
            if p.completed and p.flag and p.seen is not None:
                sp = t.sends[p.seen]
                for src in (ANY, sp.rank):
                    for tag in (ANY, sp.tag):
                        self.p6.setdefault((p.rank, p.comm, src, tag), []).append(p.idx)
        for v in self.p6.values():
            v.sort()
        comp: Dict[Key, int] = {}
        for v in self.rq.values():
            for r in v:
                comp[r.key] = self.first_competitor(r, self.p6)
        self.comp0 = comp
        # rule 7: per (R, S), the receives on R matched to sends from S; value = the send's
        # index if it is a ready send (cut when its receive is no longer posted), INF otherwise
        self.r7 = {k: Suffix2([t.sends[t.observed[r.key]].idx if t.sends[t.observed[r.key]].ready_mode else INF for r in v])
                   for k, v in self.rq.items()}
        if self._sends is None:
            self._build_sends()
        r0 = self.recv_of0
        self.r6s = {k: Suffix2([comp.get(r0[s.key], INF) if s.key in r0 else INF for s in v]) for k, v in self.sq.items()}
        # rule 6 for a re-matched receive: comp0 names competitors by their observed messages,
        # but under an alternative the changed receive y takes a new message m, and a receive
        # posted before y that accepts m, left pending, deprives y of it. Per (R, Q) and
        # envelope class (comm, source or ANY, tag or ANY) of the receive that took each send
        # R -> Q, the positions of those sends in sq[(R, Q)] and the suffix minima of the
        # receives' posting indexes: cutting R's sends from some position on finds the
        # earliest receive of each class left pending in one bisection (Observed.closure)
        self.r6c: Dict[Tuple[int, int, int, int, int], Tuple[List[int], List[int]]] = {}
        for (R, Q), v in self.sq.items():
            for i, s in enumerate(v):
                rk = r0.get(s.key)
                if rk is not None:
                    x = t.recvs[rk]
                    ent = self.r6c.setdefault((R, Q, x.comm, x.src, x.tag), ([], []))
                    ent[0].append(i)
                    ent[1].append(x.idx)
        for _, idxs in self.r6c.values():
            for j in range(len(idxs) - 2, -1, -1):
                if idxs[j + 1] < idxs[j]:
                    idxs[j] = idxs[j + 1]
        self._recvs = True

    def _build_colls(self) -> None:
        """Collectives of R on comm, in order, and the match sets (rule 4)."""
        self.colls_of: Dict[Tuple[int, int], List[Coll]] = {}
        self.coll_set: Dict[Tuple[int, int], List[Coll]] = {}
        for c in sorted(self.t.colls, key=lambda c: (c.rank, c.idx)):
            self.colls_of.setdefault((c.rank, c.comm), []).append(c)
            self.coll_set.setdefault((c.comm, c.ordinal), []).append(c)
        self.coll_idx = {k: [c.idx for c in v] for k, v in self.colls_of.items()}
        self.comms_of: Dict[int, List[int]] = {}
        self.coll_ranks: Dict[int, Set[int]] = {}       # the ranks with a collective on each comm
        for R, comm in sorted(self.colls_of):
            self.comms_of.setdefault(R, []).append(comm)
            self.coll_ranks.setdefault(comm, set()).add(R)
        self._colls = True

    def first_competitor(self, r: Recv, p6, skip_probes=frozenset()) -> int:
        """The posting index of the first receive or probe of r's rank after r that takes or
        reports a message r accepts (INF if none): where rule 6 cuts when r stays pending."""
        key = (r.rank, r.comm, r.src, r.tag)
        best = INF
        lst = self.r6_idx.get(key)
        if lst:
            j = bisect_right(lst, r.idx)
            if j < len(lst):
                best = lst[j]
        lst = p6.get(key)
        if lst:
            j = bisect_right(lst, r.idx)
            while j < len(lst) and (r.rank, lst[j]) in skip_probes:
                j += 1
            if j < len(lst) and lst[j] < best:
                best = lst[j]
        return best

    def acyclic_at_zero(self) -> bool:
        """Is the observed matching's full constraint graph acyclic at B = 0? Rendezvous
        edges only disappear as B grows and the other edges do not depend on B, so a yes
        holds at every threshold and the alternatives need no graph per threshold."""
        if getattr(self, "_full0", None) is None:
            ok = self._acyc.get(0)
            self._full0 = build_graph(self.t, self.t.observed, None, 0).find_cycle() is None if ok is None else ok
            self._acyc[0] = self._full0
        return self._full0

    def acyclic_seen(self, B: Optional[int], ok: bool, strong: bool = False) -> None:
        """Record whether the observed graph at threshold B (over guaranteed edges only, if
        strong) is acyclic: Observed.graph, Observed.observed_verdict."""
        (self._acyc_strong if strong else self._acyc)[B] = ok

    def acyclic_known(self, B: Optional[int], strong: bool = False) -> Optional[bool]:
        """Whether the observed graph at B (over guaranteed edges only, if strong) is acyclic,
        if a graph already built decides it: the graphs share their nodes and lose rendezvous
        edges as B grows (matching.send_blocks is antitone in B), so acyclic at B' <= B means
        acyclic at B, cyclic at B' >= B cyclic."""
        def le(x, y):                     # x <= y, None standing for infinity
            return y is None or (x is not None and x <= y)
        for B2, ok in (self._acyc_strong if strong else self._acyc).items():
            if ok and le(B2, B):
                return True
            if not ok and le(B, B2):
                return False
        return None

    def strong_acyclic_at_zero(self) -> bool:
        """Is the observed matching's graph of guaranteed edges acyclic at B = 0 over the
        whole trace? Rendezvous edges only disappear as B grows and the kept prefix is an
        induced subgraph, so a yes answers every threshold at once (feasible.Verdict.cycle
        is then None for all B) and the per-threshold graphs need not be built."""
        if getattr(self, "_strong0", None) is None:
            self._strong0 = build_graph(self.t, self.t.observed, None, 0, strong_only=True).find_cycle() is None
            self._acyc_strong[0] = self._strong0
        return self._strong0

    def lamport(self, g: Optional[Graph] = None) -> Optional[Dict[int, array]]:
        """Lamport times of the observed graph at B = inf (lamport_times), the order in which
        Observed.closure processes its worklist; None if that graph is cyclic. g, if given, is
        that graph already built (Observed(t, None).graph())."""
        if self._lam is None:
            g = g if g is not None else build_graph(self.t, self.t.observed, None, None)
            self._lam = lamport_times(self.t, g) or False
        return self._lam or None

    def ready(self) -> None:
        if self._sends is None:
            self._build_sends()
        if self._recvs is None:
            self._build_recvs()
        if self._colls is None:
            self._build_colls()


class Observed:
    """The observed run at one threshold B: the rule-3 index, the constraint graph, its
    vector clocks and the closure of the observed matching, each built on first use."""

    def __init__(self, t: Trace, B: Optional[int], ix: Optional[Indexes] = None):
        self.t, self.B = t, B
        self.ix = ix or t.fast_indexes()
        ix = self.ix
        self._r3: Dict[Tuple[int, int], Suffix2] = {}
        self.orphans_blocking = [s for s in ix.orphans if send_blocks(s, B)]
        self._graph = None
        self._cycle: Optional[list] = None
        self._acyclic: Optional[bool] = None      # is the graph at B acyclic, once known
        self._rows = self._rows_other = None      # vector clocks (_build_vc)
        self._reach: Dict = {}         # reachability by source node, when the graph is cyclic
        self._rank_pos: Dict[int, int] = {}
        self._obs_closure = None
        self._observed: Dict[bool, Verdict] = {}
        self._probe_closure: Optional[Tuple[Key, Tuple[Dict[int, int], Dict[int, int]]]] = None
        self._base = None                 # closure() of the observed matching (probe_closure)
        self._chain = None                # (rank, idx, (cut, issued)) of the last probe_closure

    def r3(self, key: Tuple[int, int]) -> Suffix2:
        """Rule-3 index for the receives on key[0] matched to sends from key[1]: value =
        2 * cidx(sender) + (1 if the send is blocking, so that its issue stays when its
        completion is cut), INF when the send completes regardless of B. Built on first use,
        since a trace without wildcards never queries it."""
        sx = self._r3.get(key)
        if sx is None:
            t = self.t
            vals = []
            for r in self.ix.rq[key]:
                s = t.sends[t.observed[r.key]]
                vals.append(2 * _cidx(s) + (1 if s.blocking else 0) if s.completed and send_blocks(s, self.B) else INF)
            sx = self._r3[key] = Suffix2(vals)
        return sx

    # ------------------------------------------------------------------ closure
    def closure(self, changed: Iterable[Key] = (), new_pairs: Optional[Dict[Key, Key]] = None,
                dropped_recvs: Iterable[Recv] = (), dropped_sends: Iterable[Send] = (),
                exclude2: Optional[Tuple[int, int, int]] = None, exclude3: Optional[Tuple[int, int, int]] = None,
                changed_probes: Iterable[Probe] = (), changed_events: Iterable[Tuple[int, int]] = (),
                mode: str = "L1", hangs: Optional[Dict[int, str]] = None,
                bounds=None, cuts: Iterable[Tuple[int, int]] = (),
                start: Optional[Tuple[Dict[int, int], Dict[int, int]]] = None) -> Tuple[Dict[int, int], Dict[int, int]]:
        """The horizon closure (horizon.compute_cut) for the observed matching with the
        receives in new_pairs re-matched, dropped_recvs left without a send and
        dropped_sends without a receive. exclude2 = (R, Q, position) names a stale entry of
        the rule-2 index (the changed receive's observed send), exclude3 one of the rule-3
        index (the receive that lost the send now taken by the changed receive). bounds: read
        bounds for rule 1, as in horizon.compute_cut. cuts: (rank, index) pairs that rule 1
        cuts at, as for a changed probe or event but without exempting the record before the
        cut from rules 5 and 6. start: the (cut, issued) of a closure computed with the same
        arguments but fewer cuts, a fixpoint of the same rules, to continue from instead of
        the whole trace (its rule-1 cuts and the seeds of rules 2, 3, 6 and 7 hold there
        already). Returns (cut, issued).

        The worklist holds ranks whose cut moved. It is taken in the order of the Lamport
        times of the observed run (Indexes.lamport), the earliest removed record first: a
        removal makes the operations that depend on the removed one lose their completion,
        and those come later, so a rank is usually processed once, at the cut where it ends.
        Taken last in first out, as before 22 Oct 2026, the closure of a probe of AMG's trace
        processed each rank 4.0 times on average at 16 processes, 5.5 at 32 and 7.7 at 64; in
        this order, once at each. The closure is the greatest fixpoint of the rules whatever
        the order; the order of the lowerings only decides which reason is recorded first for a
        rank, so a closure that records hangs keeps the old order and its texts."""
        t, ix, B = self.t, self.ix, self.B
        new_pairs = new_pairs or {}
        dropped_sends = list(dropped_sends)
        cut = dict(start[0]) if start is not None else dict(ix.length)
        issued: Dict[int, int] = dict(start[1]) if start is not None else {}
        work: List = []
        probe_ranks = {p.rank: p.idx for p in changed_probes}
        changed_probe_at = {(p.rank, p.idx) for p in changed_probes}
        dropped_keys = {r.key for r in dropped_recvs}
        # receives whose M' message differs from the observed one, by (rank, idx): the
        # rule-6 index names them as competitors by their observed message, which is stale
        stale = {(rk[0], t.recvs[rk].idx) for rk in list(new_pairs) + list(dropped_keys)}

        record = hangs is not None and mode == "SP"
        lam = None if record else ix.lamport()
        done: Dict[int, Tuple[int, bool]] = {}     # the state at which each rank was last processed

        def push(rank: int, idx: int, kept: bool) -> None:
            if lam is None:
                work.append((rank, idx))
            else:
                heappush(work, (lam[rank][2 * idx + kept], rank))

        def lower(rank: int, idx: int, why=None, issue_kept: bool = False) -> None:
            """why: a callable producing the reason text, formatted only when hangs are recorded.
            A rule that removes the issue kept at the current cut still applies (horizon.compute_cut)."""
            if idx == cut[rank] and issued.get(rank) == idx and not issue_kept:
                issued.pop(rank, None)
                push(rank, idx, False)
                if record and rank not in hangs:
                    hangs[rank] = why() if why else ""
                return
            if idx < cut[rank]:
                push(rank, idx, issue_kept)
                cut[rank] = idx
                if issue_kept:
                    issued[rank] = idx
                else:
                    issued.pop(rank, None)
                if record and rank not in hangs:
                    hangs[rank] = why() if why else ""

        def rule6(r: Recv) -> None:
            """r is posted inside the prefix and its message is not issued there: it stays
            pending, so the rank is cut at the first later receive that takes a message r
            accepts (the standard's second non-overtaking clause)."""
            rank = r.rank
            best, best_r = cut[rank], None
            key = (rank, r.comm, r.src, r.tag)
            ents = ix.r6.get(key)
            if ents:
                j = bisect_right(ix.r6_idx[key], r.idx)
                while j < len(ents) and ents[j][0] < best:
                    rk2 = ents[j][1]
                    if rk2 not in new_pairs and rk2 not in dropped_keys:
                        best, best_r = ents[j][0], rk2
                        break
                    j += 1
            for rk2, sk2 in new_pairs.items():
                if rk2[0] == rank and r.idx < t.recvs[rk2].idx < best and r.accepts(t.sends[sk2]):
                    best, best_r = t.recvs[rk2].idx, rk2
            pl = ix.p6.get(key)
            if pl:
                j = bisect_right(pl, r.idx)
                while j < len(pl) and (rank, pl[j]) in changed_probe_at:
                    j += 1
                if j < len(pl) and pl[j] < best:
                    best, best_r = pl[j], ("probe", pl[j])
            if best_r is not None:
                lower(rank, best, lambda r=r, rk2=best_r, best=best: f"{ix.probe_at[(rank, best)] if rk2[0] == 'probe' else t.recvs[rk2]} is deprived: {r}, posted before it and still pending, takes the message")

        def deprived(Q: int, v: int) -> str:
            """What rule 6 takes from the record at v of rank Q, a receive or a probe."""
            p = ix.probe_at.get((Q, v))
            if p is not None:
                return f"{p} never reports {t.sends[p.seen]}"
            x = ix.recv_at[(Q, v)]
            return f"{x} never gets {t.sends[t.observed[x.key]]}"

        def pending_before(R: int, Q: int, i: int, y: Recv, m: Send) -> bool:
            """Rule 6 for a re-matched receive y of Q with its new message m: is a receive of Q
            posted before y that accepts m left pending by the removal of R's sends to Q from
            position i on? The suffix query on comp0 names competitors by their observed
            messages, so it misses y when what y took in the run is one the pending receive
            does not accept (the generic check indexes M'). A receive that took a send of R
            has the source ANY or R; one that accepts m has the source ANY or m's and the tag
            ANY or m's (Indexes.r6c)."""
            for src in ((ANY, R) if m.rank == R else (ANY,)):
                for tag in (ANY, m.tag):
                    e = ix.r6c.get((R, Q, m.comm, src, tag))
                    if e is not None:
                        j = bisect_left(e[0], i)
                        if j < len(e[0]) and e[1][j] < y.idx:
                            return True
            return False

        # A rank whose cut moved only a little since it was last processed, as in a chain of
        # probe closures, has its newly removed records taken one by one: what the suffix-minimum
        # queries below would add over them, at a few lookups per record instead of O(P log n)
        # per lowering. Only for the observed matching without exemptions (no re-matched,
        # dropped or changed receive or probe), where the queries read nothing else.
        plain = lam is not None and not new_pairs and not dropped_keys and not dropped_sends \
            and exclude2 is None and exclude3 is None and not changed_probes
        span = 2 * len(ix.length)
        r0 = ix.recv_of0

        def records(R: int, a: int, b: int) -> None:
            """Rules 2, 3, 5, 6, 7 and 4 for the records a..b-1 of rank R, removed since R was
            last processed (the record at the cut keeps its issue if issued names it)."""
            kept_at = issued.get(R)
            for idx in range(a, b):
                s = ix.send_at.get((R, idx))
                if s is not None:
                    gone = kept_at != idx                  # the send is not issued any more
                    rk = r0.get(s.key)
                    if rk is not None:
                        if gone:
                            v = ix.comp0.get(rk, INF)      # rule 6
                            if v < cut[s.dst]:
                                lower(s.dst, v)
                        x = t.recvs[rk]
                        if x.completed:                    # rule 2
                            lower(x.rank, _cidx(x))
                    if gone:
                        v = ix.probe_min.get(s.key)        # rule 5
                        if v is not None:
                            lower(s.dst, v)
                    continue
                x = ix.recv_at.get((R, idx))
                if x is not None:
                    sk = t.observed.get(x.key)
                    if sk is not None:
                        s = t.sends[sk]
                        if s.completed and send_blocks(s, B):     # rule 3
                            lower(s.rank, _cidx(s), None, s.blocking)
                        if s.ready_mode:                          # rule 7
                            lower(s.rank, s.idx)
            for comm in ix.comms_of.get(R, ()):                   # rule 4, as in the loop below
                cidx = ix.coll_idx[(R, comm)]
                i, j = bisect_left(cidx, a), bisect_left(cidx, b)
                if i < j:
                    rule4(R, comm, i, j)

        def rule4(R: int, comm: int, i: int, j: int) -> None:
            """Rule 4 for R's collectives on comm at positions i..j-1 of colls_of[(R, comm)],
            whose issue R no longer keeps: each member completes one only if the members its
            shape requires still issue it. Groups are taken in order, and a member drops out of
            the scan once its own issue of a group is cut, since it issues its later collectives
            on the comm later still, and completes them later than that. A member cut at a
            group's completion stays in: a nonblocking collective issued later can complete
            earlier (Ibarrier b0, Ibarrier b1, Wait b1, ..., Wait b0), so a later group can
            still remove a completion it keeps. Before 22 Oct 2026 such a member left the scan
            at once, and so did every member of the other group of an intercommunicator."""
            colls = ix.colls_of[(R, comm)]
            members = ix.coll_ranks[comm]
            pos = {r: k for k, r in enumerate(t.comm_ranks.get(comm, []))}
            gone: Set[int] = set()
            for k in range(i, j):
                if len(gone) == len(members):
                    break
                group = ix.coll_set[(comm, colls[k].ordinal)]
                need = set(t.comm_members.get(comm, [c.rank for c in group]))
                have = {c.rank for c in group if c.idx < cut[c.rank]}
                for c in group:
                    if c.rank in gone:
                        continue
                    if c.idx >= cut[c.rank]:
                        gone.add(c.rank)
                    elif c.completed and _cidx(c) < cut[c.rank] and not coll_can_complete(c, have, need, pos):
                        lower(c.rank, _cidx(c), lambda c=c: f"collective {c.name} #{c.ordinal} on comm {c.comm} never completes: a participant never reaches it")
                        if c.idx >= cut[c.rank]:
                            gone.add(c.rank)

        if mode == "L1":
            for rk in changed:
                r = t.recvs[rk]
                lower(r.rank, bounds.recv(t, r, (new_pairs or {}).get(rk)) if bounds is not None else _cidx(r) + 1)
            for p in changed_probes:
                lower(p.rank, p.idx + 1)
            for rank, idx in changed_events:
                lower(rank, idx + 1)
            for rank, idx in cuts:
                lower(rank, idx)
        new_by_send = {sk: rk for rk, sk in new_pairs.items()}
        # the re-matched receives by rank, with their new messages (rule 6 below)
        new_on: Dict[int, List[Tuple[Recv, Send]]] = {}
        for rk, sk in new_pairs.items():
            new_on.setdefault(rk[0], []).append((t.recvs[rk], t.sends[sk]))
        if changed or changed_probes or changed_events or work or ix.unmatched_completed or dropped_recvs or self.orphans_blocking or dropped_sends or ix.orphan_ready:
            ix.ready()
        if start is None:
            for r in ix.unmatched_completed:
                if r.key not in new_pairs:
                    lower(r.rank, _cidx(r), lambda r=r: f"{r} never completes: no send for it")
            for r in dropped_recvs:
                if r.completed:
                    lower(r.rank, _cidx(r), lambda r=r: f"{r} never completes: no send for it")
            for s in self.orphans_blocking:
                if s.key not in new_by_send:
                    lower(s.rank, _cidx(s), lambda s=s: f"{s} never completes: rendezvous with no posted receive", _cidx(s) == s.idx)
            for s in dropped_sends:
                if s.completed and send_blocks(s, B):
                    lower(s.rank, _cidx(s), lambda s=s: f"{s} never completes: rendezvous with no posted receive", _cidx(s) == s.idx)
            # rule 7: ready sends without a receive
            for s in ix.orphan_ready + [s for s in dropped_sends if s.ready_mode]:
                if s.key not in new_by_send:
                    lower(s.rank, s.idx, lambda s=s: f"{s} is a ready send whose receive is never posted")
            for r in ix.unmatched_completed:
                if r.key not in new_pairs and r.idx < cut[r.rank]:
                    rule6(r)
            for r in dropped_recvs:
                if r.completed and r.idx < cut[r.rank]:
                    rule6(r)
        while work:
            if lam is None:
                R, new = work.pop()
            else:
                # the rank's records from its current cut on are removed: one pass covers every
                # lowering of it since it was last processed
                R = heappop(work)[1]
                new = cut[R]
                st = (new, issued.get(R) == new)
                prev = done.get(R)
                if prev == st:
                    continue
                done[R] = st
                if plain:
                    if prev is None:     # last processed in the closure continued from, or never
                        prev = (start[0][R], start[1].get(R) == start[0][R]) if start is not None else (ix.length[R], False)
                    end = prev[0] + prev[1]        # a send at the old cut that kept its issue loses it
                    if end - new <= span:
                        records(R, new, end)
                        continue
            # rule 6: the receives of R's removed sends stay pending; Q is cut at the smallest
            # competitor among them (a send cut at its own record with its issue kept is
            # still delivered, so it starts the suffix after itself)
            for Q in ix.targets.get(R, ()):
                key = (R, Q)
                i = bisect_left(ix.sq_idx[key], new)
                if i < len(ix.sq_idx[key]) and issued.get(R) == ix.sq_idx[key][i]:
                    i += 1
                if i < len(ix.sq_idx[key]):
                    ex = exclude2[2] if exclude2 is not None and exclude2[0] == R and exclude2[1] == Q else -1
                    v, pos = ix.r6s[key].query(i, ex)
                    if v < cut[Q]:
                        exempt = (Q, v) in changed_probe_at
                        if exempt and cut[Q] <= v + 1:
                            # the changed probe, exempt from rule 6 (check_probe judges what
                            # depends on the message it reports instead); every competitor after
                            # it lies beyond rule 1's cut right after it
                            pass
                        elif (Q, v) in stale or exempt:
                            # the competitor the index names is re-matched, dropped or exempt:
                            # judge each pending receive of the suffix by its M' competitor
                            for j in range(i, len(ix.sq[key])):
                                if j != ex and ix.sq[key][j].key in ix.recv_of0:
                                    rule6(t.recvs[ix.recv_of0[ix.sq[key][j].key]])
                        else:
                            lower(Q, v, lambda key=key, pos=pos, Q=Q, v=v: f"{deprived(Q, v)}: {t.recvs[ix.recv_of0[ix.sq[key][pos].key]]}, posted before it and still pending, takes the message")
                    for y, m in new_on.get(Q, ()):
                        if y.idx < cut[Q] and pending_before(R, Q, i, y, m):
                            lower(Q, y.idx, lambda y=y, m=m: f"{y} never gets {m}: a receive posted before it that accepts the message is still pending and takes it")
            for sk, rk in new_by_send.items():
                if sk[0] == R and t.sends[sk].idx >= new and issued.get(R) != t.sends[sk].idx:
                    r = t.recvs[rk]
                    if r.completed and r.idx < cut[r.rank]:
                        rule6(r)
            # rule 2: receives whose send on R is removed
            for Q in ix.targets.get(R, ()):
                key = (R, Q)
                i = bisect_left(ix.sq_idx[key], new)
                if i < len(ix.sq_idx[key]):
                    ex = exclude2[2] if exclude2 is not None and exclude2[0] == R and exclude2[1] == Q else -1
                    v, pos = ix.r2[key].query(i, ex)
                    if v < INF:
                        lower(Q, v, lambda key=key, pos=pos: f"{t.recvs[ix.recv_of0[ix.sq[key][pos].key]]} never completes: no send for it")
            for sk, rk in new_by_send.items():
                if sk[0] == R and t.sends[sk].idx >= new:
                    r = t.recvs[rk]
                    if r.completed:
                        lower(r.rank, _cidx(r), lambda r=r: f"{r} never completes: no send for it")
            # rule 3: rendezvous sends whose receive on R is no longer posted
            for S in ix.senders_to.get(R, ()):
                key = (R, S)
                i = bisect_left(ix.rq_idx[key], new)
                if i < len(ix.rq_idx[key]):
                    ex = exclude3[2] if exclude3 is not None and exclude3[0] == R and exclude3[1] == S else -1
                    v, pos = self.r3(key).query(i, ex)
                    if v < INF:
                        lower(S, v // 2, lambda key=key, pos=pos: f"{t.sends[t.observed[ix.rq[key][pos].key]]} never completes: rendezvous with no posted receive", v % 2 == 1)
            for rk, sk in new_pairs.items():
                r, s = t.recvs[rk], t.sends[sk]
                if r.rank == R and r.idx >= new and s.completed and send_blocks(s, B):
                    lower(s.rank, _cidx(s), lambda s=s: f"{s} never completes: rendezvous with no posted receive", _cidx(s) == s.idx)
                if r.rank == R and r.idx >= new and s.ready_mode:
                    lower(s.rank, s.idx, lambda s=s: f"{s} is a ready send whose receive is never posted")
            # rule 7: ready sends whose receive on R is no longer posted
            for S in ix.senders_to.get(R, ()):
                key = (R, S)
                i = bisect_left(ix.rq_idx[key], new)
                if i < len(ix.rq_idx[key]):
                    ex = exclude3[2] if exclude3 is not None and exclude3[0] == R and exclude3[1] == S else -1
                    v, pos = ix.r7[key].query(i, ex)
                    if v < INF:
                        lower(S, v, lambda S=S, v=v: f"a ready send of rank {S} at {v} loses its posted receive")
            # rule 4: collectives R no longer issues; each member completes only if the
            # members its shape requires still issue it
            for comm in ix.comms_of.get(R, ()):
                cidx = ix.coll_idx[(R, comm)]
                i = bisect_left(cidx, new)
                if i < len(cidx):
                    rule4(R, comm, i, len(cidx))
            # rule 5: probes that observed a send of R now removed (a send cut at its own
            # record with its issue kept is still issued, so a probe can still see it)
            for Q in ix.targets.get(R, ()):
                key = (R, Q)
                i = bisect_left(ix.sq_idx[key], new)
                if i < len(ix.sq_idx[key]) and issued.get(R) == ix.sq_idx[key][i]:
                    i += 1
                if i < len(ix.sq_idx[key]):
                    v, pos = ix.r5[key].query(i)
                    if v < INF and probe_ranks.get(Q) != v:
                        lower(Q, v, lambda Q=Q, v=v, key=key, pos=pos: f"{ix.probe_at[(Q, v)]} observes another message: {ix.sq[key][pos]} is never issued")
        return cut, issued

    # -------------------------------------------------------------------- graph
    def graph(self):
        if self._graph is None:
            self._graph = build_graph(self.t, self.t.observed, None, self.B)
            if self._acyclic is None:
                self._cycle = self._graph.find_cycle()
                self._acyclic = not self._cycle
                self.ix.acyclic_seen(self.B, self._acyclic)
            if self.B is None and not self._acyclic and self.ix._lam is None:
                self.ix._lam = False          # the graph of Indexes.lamport, cyclic: no Lamport times
        return self._graph

    def acyclic(self, keep: bool = False) -> bool:
        """Is the observed graph at B acyclic? Known at once when it is at B = 0 or, since
        the graph only loses rendezvous edges as B grows, at a smaller threshold (or cyclic
        at a larger one: Indexes.acyclic_known); otherwise the graph is built, and kept if
        cyclic, where reaches() searches it, or if keep (the vector clocks are built next)."""
        if self._acyclic is None:
            if self.ix.acyclic_at_zero():
                self._acyclic = True
            else:
                self._acyclic = self.ix.acyclic_known(self.B)
                if self._acyclic is None:
                    self.graph()
                    if self._acyclic and not keep:
                        self._graph = None
        return self._acyclic

    def reaches(self, a, b) -> bool:
        """a happens-before b in every execution with the observed matching at this B. a is an
        issue or completion node; b may also be a match node. A cyclic graph (a run the model
        cannot realise at this B: a ready send issued before its receive, a collective of
        unknown shape that did not synchronise) has no vector clocks; the answer is then the
        graph's own reachability, as matching.observed_hb gives it."""
        if self._rows is None:
            if not self.acyclic(keep=True):
                self.graph()
                reach = self._reach.get(a)
                if reach is None:
                    reach = self._reach[a] = self._graph.reach_set(a)
                return b in reach
            self._build_vc()
        rb = self._row(b)
        if rb is None or self._row(a) is None:
            return False
        pa = 2 * a[1] + (1 if a[2] == "c" else 0)
        if a[0] == b[0] and b[2] != "m":
            return 2 * b[1] + (1 if b[2] == "c" else 0) >= pa     # program order on a's rank
        return rb[self._rank_pos[a[0]]] >= pa

    def _row(self, n):
        """The clock row of node n (Observed._build_vc), None if n is not a node of the graph."""
        if n[0] >= 0 and n[2] != "m":
            rows = self._rows.get(n[0])
            k = 2 * n[1] + (1 if n[2] == "c" else 0)
            return rows[k] if rows is not None and 0 <= k < len(rows) else None
        return self._rows_other.get(n)

    def _build_vc(self) -> None:
        """Vector clocks of the observed graph at B, in one topological pass that pushes each
        node's clock to its successors. A node of a rank keeps its row without its own
        component, which reaches() reads off the node itself; so a node whose only
        predecessor is the previous node of its rank, or a match node or hub, has its
        predecessor's row and shares it, and rows are made only where a predecessor on another
        rank joins: receive, collective and probe completions, match nodes, hubs. Rows are
        held per rank by position, not in a dictionary keyed by node. Before 22 Oct 2026 every
        node had a row of its own and a list of its predecessors, which at AMG's 64 processes
        came to gigabytes per threshold. The graph is dropped once the clocks are built."""
        if not self.acyclic():
            raise ValueError("observed graph is cyclic: no vector clocks")
        g = self.graph()
        t = self.t
        ranks = sorted(t.ranks)
        rp = self._rank_pos = {r: i for i, r in enumerate(ranks)}
        P = len(ranks)
        rows: Dict[int, list] = {rank: [None] * (2 * len(recs)) for rank, recs in t.ranks.items()}
        other: Dict = {}
        indeg = {rank: array("l", [0]) * (2 * len(recs)) for rank, recs in t.ranks.items()}
        indeg_other: Dict = {}
        for succ in g.succ.values():
            for b in succ:
                if b[0] >= 0 and b[2] != "m":
                    indeg[b[0]][2 * b[1] + (b[2] == "c")] += 1
                else:
                    indeg_other[b] = indeg_other.get(b, 0) + 1
        # at B = inf this is the graph of Indexes.lamport: its Lamport times come with the clocks
        lam = {rank: array("l", [0]) * (2 * len(recs)) for rank, recs in t.ranks.items()} \
            if self.B is None and self.ix._lam is None else None
        lam_other: Dict = {}
        base = [-1] * P
        ready = []
        for n in g.nodes:
            if n[0] >= 0 and n[2] != "m":
                if indeg[n[0]][2 * n[1] + (n[2] == "c")] == 0:
                    rows[n[0]][2 * n[1] + (n[2] == "c")] = base
                    ready.append(n)
            elif indeg_other.get(n, 0) == 0:
                other[n] = base
                ready.append(n)
        acc: Dict = {}          # rows being joined at nodes with a predecessor still to come
        succ_of = g.succ
        while ready:
            n = ready.pop()
            n0, n1, n2 = n
            own = n0 >= 0 and n2 != "m"
            if own:
                pn = 2 * n1 + (n2 == "c")
                r = rows[n0][pn]
                k = rp[n0]
                ln = lam[n0][pn] + 1 if lam is not None else 0
            else:
                r = other[n]
                ln = lam_other.get(n, 0) + 1 if lam is not None else 0
            for b in succ_of.get(n, ()):
                b0, b1, b2 = b
                b_own = b0 >= 0 and b2 != "m"
                if b_own:
                    pb = 2 * b1 + (b2 == "c")
                    arr = indeg[b0]
                    d = arr[pb] - 1
                    arr[pb] = d
                    if lam is not None and ln > lam[b0][pb]:
                        lam[b0][pb] = ln
                else:
                    d = indeg_other[b] - 1
                    indeg_other[b] = d
                    if lam is not None and ln > lam_other.get(b, 0):
                        lam_other[b] = ln
                if d == 0 and b not in acc:
                    # the only predecessor: its row, shared when it is the previous node of
                    # b's rank or has no own component
                    if not own or (b_own and b0 == n0):
                        cur = r
                    else:
                        cur = list(r)
                        if cur[k] < pn:
                            cur[k] = pn
                    if b_own:
                        rows[b0][pb] = cur
                    else:
                        other[b] = cur
                    ready.append(b)
                    continue
                cur = acc.pop(b, None)
                if cur is None:
                    cur = list(r)
                elif cur != r:
                    cur = [x if x > y else y for x, y in zip(cur, r)]
                if own and cur[k] < pn:
                    cur[k] = pn
                if d == 0:
                    if b_own:
                        rows[b0][pb] = cur
                    else:
                        other[b] = cur
                    ready.append(b)
                else:
                    acc[b] = cur
        self._rows, self._rows_other = rows, other
        if lam is not None:
            self.ix._lam = lam
        self._graph = None                    # the clocks answer reaches(); acyclic() is known

    # ------------------------------------------------------------------- checks
    def check_alternative(self, alt: Alternative, bounds=None) -> Optional[Verdict]:
        """Verdict for a steal at a blocking receive; None for a swap or a nonblocking
        receive, which the generic check judges, and None whenever the observed graph at B is
        cyclic (below the observed run's threshold), since the cycle need not lie in the
        horizon (proof appendix, conservative step 1). A blocking receive is matched in its own
        call and rule 1 cuts its rank right after it, so the only kept successor of its match
        is the completion of a rendezvous send, and a cycle through the new match exists iff
        that completion reaches one of the match's predecessors (docs/model.md §2). With read
        bounds that keep records of the rank after the receive (rule H1'), the match has more
        kept successors and the generic check judges the alternative (None)."""
        if alt.kind != "steal":
            return None
        t, B = self.t, self.B
        r, s = t.recvs[alt.recv], t.sends[alt.send]
        if not r.blocking:
            return None
        if bounds is not None and bounds.extends(t, alt.changed, alt.M):
            return None
        s_obs = t.observed_send(r)
        r2 = t.recvs[alt.stolen_from] if alt.stolen_from is not None else None
        if not self.acyclic(keep=send_blocks(s, B) or s.ready_mode):     # kept for the clocks below
            return None          # the cycle may lie outside the horizon: the generic check decides
        self.ix.ready()
        exclude2 = (s_obs.rank, r.rank, self.ix.sq_pos[s_obs.key]) if s_obs is not None else None
        exclude3 = (r2.rank, s.rank, self.ix.rq_pos[r2.key]) if r2 is not None else None
        cut, issued = self.closure(changed=[r.key], new_pairs={r.key: s.key},
                                   dropped_recvs=[r2] if r2 is not None else (),
                                   dropped_sends=[s_obs] if s_obs is not None else (),
                                   exclude2=exclude2, exclude3=exclude3)
        if r.idx >= cut[r.rank] or (r.completed and r.blocking and _cidx(r) >= cut[r.rank]) or \
           (not r.blocking and s.idx >= cut[s.rank] and issued.get(s.rank) != s.idx):
            return Verdict(False, "witness cut by the horizon closure", cut)
        why = self._order(r, s, r2, cut)
        if why:
            return Verdict(False, why, cut)
        if send_blocks(s, B) and s.completed and _cidx(s) < cut[s.rank]:
            sc = s.complete_node()
            # the match's predecessors: r's issue, and the matches that must precede it
            # (earlier receives of r's rank that accept s, receivers of the earlier messages
            # of s's channel that r accepts), all posted before r and unchanged
            preds = [r.issue_node()]
            preds += [match_node(x) for x in t.earlier_competitors(r, s) if x.key in t.observed]
            preds += [match_node(t.recvs[self.ix.recv_of0[s2.key]]) for s2 in t.earlier_sends(r, s) if s2.key in self.ix.recv_of0]
            if any(self.reaches(sc, n) for n in preds):
                return Verdict(False, "constraint cycle", cut)
        if s.ready_mode and self.reaches(s.issue_node(), r.issue_node()):
            return Verdict(False, "constraint cycle", cut)      # a ready send needs the receive posted first
        return Verdict(True, "ok", cut)

    def _order(self, r: Recv, s: Send, r2: Optional[Recv], cut: Dict[int, int]) -> Optional[str]:
        """Matching order for r <- s with r2 (if any) left without a message. Sender-side
        FIFO holds by the kernel's construction; receiver order is checked on r's rank."""
        t = self.t
        d = r.rank
        if r2 is not None and r2.idx < r.idx:
            return f"receiver order: earlier {r2} accepts {s} and has no message in this matching"
        for x in self.ix.unmatched.get(d, ()):
            if x.idx >= r.idx:
                break
            if not x.cancelled and x.accepts(s):
                return f"receiver order: earlier {x} accepts {s} and has no message in this matching"
        for x in t.wild_recvs_of(d):
            if x.idx >= r.idx:
                break
            if x.key in t.observed and x.accepts(s):
                s2 = t.sends[t.observed[x.key]]
                if s2.rank == s.rank and s2.dst == s.dst and s2.comm == s.comm and s2.idx > s.idx:
                    return f"receiver order: earlier {x} would take {s} before {s2}"
        if r2 is not None and not r2.cancelled:
            # r2 is posted after r (else rejected above) and has no message: a kept receive
            # posted after it must not take a message r2 accepts
            recvs = t.recvs_of(d)
            for i in range(bisect_right(self.ix.recv_idx[d], r2.idx), len(recvs)):
                x = recvs[i]
                if x.idx >= cut[d]:
                    break
                sk = t.observed.get(x.key)
                if sk is not None and r2.accepts(t.sends[sk]):
                    return f"receiver order: earlier {r2} accepts {t.sends[sk]} and has no message in this matching"
        return None

    def probe_closure(self, p: Probe) -> Tuple[Dict[int, int], Dict[int, int]]:
        """closure(changed_probes=[p]), continued from the closure of the last probe asked for
        when that one is a later probe of the same rank. The two differ in where rule 1 cuts
        the rank, and the closure is monotone in the cut, so the earlier probe's horizon is the
        later one's lowered at the earlier probe and closed again: the probes of a rank, taken
        last to first (report._profiles_fast), cost about one closure together instead of one
        each. The chain applies rules 5 and 6 to every probe, the changed one included, which
        the closure of a changed probe exempts; the two closures are equal unless the chain
        cuts the probe itself, and then the probe's own closure is computed instead. On AMG's
        trace at 64 processes a probe's own closure processes each of the 64 ranks once, about
        34 ms, as the cascade from a probe removes the rest of the run; chained, it processes
        5.7 ranks on average."""
        ch = self._chain
        if ch is None or ch[0] != p.rank or ch[1] <= p.idx:
            if self._base is None:
                self._base = self.closure()               # the observed matching: rules 2-7 only
            start = self._base
        else:
            start = ch[2]
        cut, issued = self.closure(cuts=[(p.rank, p.idx + 1)], start=start)
        self._chain = (p.rank, p.idx, (cut, issued))
        if cut[p.rank] <= p.idx:
            return self.closure(changed_probes=[p])
        return cut, issued

    def check_probe(self, p: Probe, s: Optional[Send]) -> Optional[Verdict]:
        """The probe reports s instead of what it saw: another message, or, for an Iprobe that
        returned false, a message at all (a flag flip, docs/model.md §3); s None: an Iprobe that
        saw a message returns false. Its completion has no kept successor (the closure cuts
        right after it), so no cycle can pass through the new edges, and a probe that reports
        nothing has none; what remains is that s be issued and that no earlier receive of the
        probe's rank be pending with s acceptable to it (rule 6)."""
        if not self.acyclic():
            return None
        t = self.t
        # the closure depends on the probe, not on the message it would report, and a probe's
        # alternatives come one after another (matching.probe_candidates): the last probe's
        # closure serves them all
        if self._probe_closure is None or self._probe_closure[0] != p.key:
            self._probe_closure = (p.key, self.probe_closure(p))
        cut, issued = self._probe_closure[1]
        if p.idx >= cut[p.rank]:
            return Verdict(False, "witness cut by the horizon closure", cut)
        if s is None:
            return Verdict(True, "ok", cut)
        if s.idx >= cut[s.rank] and issued.get(s.rank) != s.idx:
            return Verdict(False, "the message the probe would observe is never issued within the horizon", cut)
        for x in self.ix.unmatched.get(p.rank, ()):
            if x.idx >= p.idx:
                break
            if not x.cancelled and not x.mrecv and x.accepts(s):
                return Verdict(False, f"receiver order: earlier {x} accepts {s} and has no message in this matching", cut)
        for x in t.earlier_competitors(p, s):
            sk = t.observed.get(x.key)
            if sk is not None:
                sx = t.sends[sk]
                if sx.idx >= cut[sx.rank] and issued.get(sx.rank) != sx.idx:
                    return Verdict(False, "witness cut by the horizon closure", cut)    # x stays pending and takes s
        return Verdict(True, "ok", cut)

    def check_event(self, rank: int, idx: int, must_issue: Send) -> Optional[Verdict]:
        if not self.acyclic():
            return None
        cut, issued = self.closure(changed_events=[(rank, idx)])
        if idx >= cut[rank]:
            return Verdict(False, "witness cut by the horizon closure", cut)
        s = must_issue
        if s.idx >= cut[s.rank] and issued.get(s.rank) != s.idx:
            return Verdict(False, "the message that would complete first is never issued within the horizon", cut)
        return Verdict(True, "ok", cut)

    def observed_verdict(self, strong: bool = True) -> Verdict:
        """feasible.observed_feasible_at(t, B, strong): the observed matching under the
        single-path assumption, over guaranteed edges only (strong: a cycle is a certain
        deadlock, the 2-buffering finding) or over every edge (B0 of theorem T2, from which
        the profiles start); hangs are the ranks the closure removes."""
        v = self._observed.get(strong)
        if v is None:
            if self._obs_closure is None:
                hangs: Dict[int, str] = {}
                cut, issued = self.closure(mode="SP", hangs=hangs)
                self._obs_closure = cut, issued, hangs
            cut, issued, hangs = self._obs_closure
            # a closure that removes nothing leaves the graph of the whole trace at B, whose
            # acyclicity the graphs built at other thresholds may decide (Indexes.acyclic_known)
            whole = not issued and all(cut[r] == n for r, n in self.ix.length.items())
            if self.ix.strong_acyclic_at_zero() if strong else self.ix.acyclic_at_zero():
                cyc = None
            elif whole and self.ix.acyclic_known(self.B, strong):
                cyc = None
            else:
                g = build_graph(self.t, self.t.observed, cut, self.B, strong_only=strong, issued=issued)
                cyc = g.find_cycle()
                if whole:
                    self.ix.acyclic_seen(self.B, not cyc, strong)
            text = "hang under the single-path assumption: " + "; ".join(hangs[r] for r in sorted(hangs)) if hangs else ""
            if cyc:
                v = Verdict(False, "constraint cycle", cut, cyc, hangs)
            else:
                v = Verdict(True, text or "ok", cut, None, hangs)
            self._observed[strong] = v
        return v
