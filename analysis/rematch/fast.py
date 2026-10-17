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
  * acyclicity reduces to reachability in the observed graph of the threshold, since the
    closure removes every node an added edge could otherwise return through, and that
    reachability comes from vector clocks computed once per threshold.

The generic check stays as the reference; the tests compare the two on every alternative.
"""
from __future__ import annotations
from bisect import bisect_left, bisect_right
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .feasible import Verdict
from .horizon import coll_can_complete
from .matching import Alternative, build_graph, is_hub, send_blocks
from .trace import ANY, Coll, Key, Probe, Recv, Send, Trace

INF = 10 ** 9


def _cidx(x) -> int:
    return x.complete_idx if x.complete_idx is not None else x.idx


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
        # receives without an observed send, per rank in posting order; orphan sends
        self.unmatched: Dict[int, List[Recv]] = {}
        for r in sorted(t.recvs.values(), key=lambda r: (r.rank, r.idx)):
            if r.key not in t.observed:
                self.unmatched.setdefault(r.rank, []).append(r)
        self.unmatched_completed = [r for v in self.unmatched.values() for r in v if r.completed]
        self.orphans = [s for s in t.sends.values() if s.key not in self.recv_of0 and s.completed]
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
        comp: Dict[Key, int] = {}
        for v in self.rq.values():
            for r in v:
                lst = self.r6_idx.get((r.rank, r.comm, r.src, r.tag))
                j = bisect_right(lst, r.idx) if lst else 0
                comp[r.key] = lst[j] if lst and j < len(lst) else INF
        self.comp0 = comp
        if self._sends is None:
            self._build_sends()
        r0 = self.recv_of0
        self.r6s = {k: Suffix2([comp.get(r0[s.key], INF) if s.key in r0 else INF for s in v]) for k, v in self.sq.items()}
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
        for R, comm in sorted(self.colls_of):
            self.comms_of.setdefault(R, []).append(comm)
        self._colls = True

    def acyclic_at_zero(self) -> bool:
        """Is the observed matching's full constraint graph acyclic at B = 0? Rendezvous
        edges only disappear as B grows and the other edges do not depend on B, so a yes
        holds at every threshold and the alternatives need no graph per threshold."""
        if getattr(self, "_full0", None) is None:
            self._full0 = build_graph(self.t, self.t.observed, None, 0).find_cycle() is None
        return self._full0

    def strong_acyclic_at_zero(self) -> bool:
        """Is the observed matching's graph of guaranteed edges acyclic at B = 0 over the
        whole trace? Rendezvous edges only disappear as B grows and the kept prefix is an
        induced subgraph, so a yes answers every threshold at once (feasible.Verdict.cycle
        is then None for all B) and the per-threshold graphs need not be built."""
        if getattr(self, "_strong0", None) is None:
            self._strong0 = build_graph(self.t, self.t.observed, None, 0, strong_only=True).find_cycle() is None
        return self._strong0

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
        self._vc = None
        self._rank_pos: Dict[int, int] = {}
        self._observed: Optional[Verdict] = None

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
                mode: str = "L1", hangs: Optional[Dict[int, str]] = None) -> Tuple[Dict[int, int], Dict[int, int]]:
        """The horizon closure (horizon.compute_cut) for the observed matching with the
        receives in new_pairs re-matched, dropped_recvs left without a send and
        dropped_sends without a receive. exclude2 = (R, Q, position) names a stale entry of
        the rule-2 index (the changed receive's observed send), exclude3 one of the rule-3
        index (the receive that lost the send now taken by the changed receive). Returns
        (cut, issued)."""
        t, ix, B = self.t, self.ix, self.B
        new_pairs = new_pairs or {}
        cut = dict(ix.length)
        issued: Dict[int, int] = {}
        work: List[Tuple[int, int]] = []
        probe_ranks = {p.rank: p.idx for p in changed_probes}
        dropped_keys = {r.key for r in dropped_recvs}
        # receives whose M' message differs from the observed one, by (rank, idx): the
        # rule-6 index names them as competitors by their observed message, which is stale
        stale = {(rk[0], t.recvs[rk].idx) for rk in list(new_pairs) + list(dropped_keys)}

        record = hangs is not None and mode == "SP"

        def lower(rank: int, idx: int, why=None, issue_kept: bool = False) -> None:
            """why: a callable producing the reason text, formatted only when hangs are recorded."""
            if idx < cut[rank]:
                work.append((rank, idx))
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
            if best_r is not None:
                lower(rank, best, lambda r=r, rk2=best_r: f"{t.recvs[rk2]} never gets {t.sends[new_pairs.get(rk2) or t.observed[rk2]]}: {r}, posted before it and still pending, takes the message")

        if mode == "L1":
            for rk in changed:
                r = t.recvs[rk]
                lower(r.rank, _cidx(r) + 1)
            for p in changed_probes:
                lower(p.rank, p.idx + 1)
            for rank, idx in changed_events:
                lower(rank, idx + 1)
        new_by_send = {sk: rk for rk, sk in new_pairs.items()}
        if changed or changed_probes or changed_events or ix.unmatched_completed or dropped_recvs or self.orphans_blocking or dropped_sends:
            ix.ready()
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
        for r in ix.unmatched_completed:
            if r.key not in new_pairs and r.idx < cut[r.rank]:
                rule6(r)
        for r in dropped_recvs:
            if r.completed and r.idx < cut[r.rank]:
                rule6(r)
        while work:
            R, new = work.pop()
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
                        if (Q, v) in stale:
                            # the competitor the index names is re-matched or dropped: judge
                            # each pending receive of the suffix by its M' competitor
                            for j in range(i, len(ix.sq[key])):
                                if j != ex and ix.sq[key][j].key in ix.recv_of0:
                                    rule6(t.recvs[ix.recv_of0[ix.sq[key][j].key]])
                        else:
                            lower(Q, v, lambda key=key, pos=pos, Q=Q, v=v: f"{ix.recv_at[(Q, v)]} never gets {t.sends[t.observed[ix.recv_at[(Q, v)].key]]}: {t.recvs[ix.recv_of0[ix.sq[key][pos].key]]}, posted before it and still pending, takes the message")
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
            # rule 4: collectives R no longer issues; each member completes only if the
            # members its shape requires still issue it
            for comm in ix.comms_of.get(R, ()):
                key = (R, comm)
                need = set(t.comm_members.get(comm, []))
                pos = {r: i for i, r in enumerate(t.comm_ranks.get(comm, []))}
                # groups in order from R's first removed collective; a member lowered at a
                # group, or already cut before it, has nothing kept in the later groups, so
                # the scan stops once no member of the communicator is left to consider
                pending = set(t.comm_ranks.get(comm, []) or [c.rank for c in ix.coll_set[(comm, ix.colls_of[key][0].ordinal)]]) - {R}
                for i in range(bisect_left(ix.coll_idx[key], new), len(ix.coll_idx[key])):
                    if not pending:
                        break
                    group = ix.coll_set[(comm, ix.colls_of[key][i].ordinal)]
                    have = {c.rank for c in group if c.idx < cut[c.rank]}
                    for c in group:
                        if c.rank not in pending:
                            continue
                        if not c.completed or _cidx(c) >= cut[c.rank]:
                            pending.discard(c.rank)
                        elif not coll_can_complete(c, have, need, pos):
                            lower(c.rank, _cidx(c), lambda c=c: f"collective {c.name} #{c.ordinal} on comm {c.comm} never completes: a participant never reaches it")
                            pending.discard(c.rank)
            # rule 5: probes that observed a send of R now removed
            for Q in ix.targets.get(R, ()):
                key = (R, Q)
                i = bisect_left(ix.sq_idx[key], new)
                if i < len(ix.sq_idx[key]):
                    v, pos = ix.r5[key].query(i)
                    if v < INF and probe_ranks.get(Q) != v:
                        lower(Q, v, lambda Q=Q, v=v, key=key, pos=pos: f"{ix.probe_at[(Q, v)]} observes another message: {ix.sq[key][pos]} is never issued")
        return cut, issued

    # -------------------------------------------------------------------- graph
    def graph(self):
        if self._graph is None:
            self._graph = build_graph(self.t, self.t.observed, None, self.B)
            self._cycle = self._graph.find_cycle()
        return self._graph

    def acyclic(self) -> bool:
        if self.ix.acyclic_at_zero():
            return True
        self.graph()
        return not self._cycle

    def reaches(self, a, b) -> bool:
        """a happens-before b in every execution with the observed matching at this B."""
        if self._vc is None:
            self._build_vc()
        vb = self._vc.get(b)
        if vb is None or a not in self._vc:
            return False
        return vb[self._rank_pos[a[0]]] >= 2 * a[1] + (1 if a[2] == "c" else 0)

    def _build_vc(self) -> None:
        g = self.graph()
        if self._cycle:
            raise ValueError("observed graph is cyclic: no vector clocks")
        ranks = sorted(self.t.ranks)
        self._rank_pos = {r: i for i, r in enumerate(ranks)}
        P = len(ranks)
        indeg: Dict = {n: 0 for n in g.nodes}
        for a, succ in g.succ.items():
            for b in succ:
                indeg[b] = indeg.get(b, 0) + 1
        ready = [n for n in g.nodes if indeg[n] == 0]
        vc: Dict = {}
        while ready:
            n = ready.pop()
            v = [-1] * P
            for a in self._preds(n):
                va = vc[a]
                for i in range(P):
                    if va[i] > v[i]:
                        v[i] = va[i]
            if not is_hub(n):
                v[self._rank_pos[n[0]]] = 2 * n[1] + (1 if n[2] == "c" else 0)
            vc[n] = v
            for b in g.succ.get(n, ()):
                indeg[b] -= 1
                if indeg[b] == 0:
                    ready.append(b)
        self._vc = vc

    def _preds(self, n):
        if not hasattr(self, "_pred"):
            self._pred: Dict = {}
            for a, succ in self.graph().succ.items():
                for b in succ:
                    self._pred.setdefault(b, []).append(a)
        return self._pred.get(n, ())

    # ------------------------------------------------------------------- checks
    def check_alternative(self, alt: Alternative) -> Optional[Verdict]:
        """Verdict for a steal alternative; None for a swap, which the generic check judges."""
        if alt.kind != "steal":
            return None
        t, B = self.t, self.B
        r, s = t.recvs[alt.recv], t.sends[alt.send]
        s_obs = t.observed_send(r)
        r2 = t.recvs[alt.stolen_from] if alt.stolen_from is not None else None
        if not self.acyclic():
            return Verdict(False, "constraint cycle", None, self._cycle)
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
        if send_blocks(s, B) and s.completed and _cidx(s) < cut[s.rank] and self.reaches(s.complete_node(), r.issue_node()):
            return Verdict(False, "constraint cycle", cut)
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

    def check_probe(self, p: Probe, s: Send) -> Verdict:
        if not self.acyclic():
            return Verdict(False, "constraint cycle", None, self._cycle)
        cut, issued = self.closure(changed_probes=[p])
        if p.idx >= cut[p.rank]:
            return Verdict(False, "witness cut by the horizon closure", cut)
        if s.idx >= cut[s.rank] and issued.get(s.rank) != s.idx:
            return Verdict(False, "the message the probe would observe is never issued within the horizon", cut)
        return Verdict(True, "ok", cut)

    def check_event(self, rank: int, idx: int, must_issue: Send) -> Verdict:
        if not self.acyclic():
            return Verdict(False, "constraint cycle", None, self._cycle)
        cut, issued = self.closure(changed_events=[(rank, idx)])
        if idx >= cut[rank]:
            return Verdict(False, "witness cut by the horizon closure", cut)
        s = must_issue
        if s.idx >= cut[s.rank] and issued.get(s.rank) != s.idx:
            return Verdict(False, "the message that would complete first is never issued within the horizon", cut)
        return Verdict(True, "ok", cut)

    def observed_verdict(self) -> Verdict:
        """feasible.observed_feasible_at(t, B): the observed matching under the single-path
        assumption over guaranteed edges only; hangs are the ranks the closure removes."""
        if self._observed is None:
            hangs: Dict[int, str] = {}
            cut, issued = self.closure(mode="SP", hangs=hangs)
            if self.ix.strong_acyclic_at_zero():
                cyc = None
            else:
                g = build_graph(self.t, self.t.observed, cut, self.B, strong_only=True, issued=issued)
                cyc = g.find_cycle()
            text = "hang under the single-path assumption: " + "; ".join(hangs[r] for r in sorted(hangs)) if hangs else ""
            if cyc:
                self._observed = Verdict(False, "constraint cycle", cut, cyc, hangs)
            else:
                self._observed = Verdict(True, text or "ok", cut, None, hangs)
        return self._observed
