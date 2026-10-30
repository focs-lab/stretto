"""Lock reordering: level L1+L (docs/threads.md, section 13).

The L1 analysis of several threads keeps every recorded release -> acquire edge in its
happens-before, so no alternative that needs two critical sections of one lock in the other
order is predicted. Here the edge between two critical sections that both contain a record of
their chain (the tracer's "soft" edges, stretto.chains.CS) is left out: the hard happens-before
(Trace.hb_hard) orders the chains, and the critical sections of each lock are scheduled in some
total order consistent with it and with the constraints of the alternative (_Sim). Premise P-T8
makes this sound: the shared state that decides a chain's calls is race-free with respect to the
hard happens-before, so a schedule that respects it and mutual exclusion gives every chain the
values it read in the run. An edge into or out of a critical section without a record (a
lock-protected flag, a condition-variable wait) stays hard, so such programs keep their order.

Three kinds of findings, none of which L1 makes:
  * receive and probe alternatives that the hard happens-before admits and a lock-feasible
    schedule realises (alternatives);
  * deadlocks in which a thread blocks in MPI inside a critical section that another thread
    needs to enter before it can send what the first waits for (lock_deadlocks; class
    2-lock-order);
  * two collectives of a process on one communicator ordered only by the order in which their
    threads entered critical sections of one lock, which another execution reverses so that the
    process's k-th collective pairs with another call on the other processes (lock_collectives;
    class 2-thread-collective).
"""
from __future__ import annotations
import copy
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .chains import CS
from .trace import Key, Trace

# closures a schedule search may compute per alternative before it gives up (incompleteness)
SEARCH_BUDGET = 400
# pairs of critical sections tried for lock-order deadlocks, per lock and per trace
PAIRS_PER_LOCK, PAIRS_PER_TRACE = 64, 512


def view(t: Trace) -> Trace:
    """The trace with the hard happens-before in place of the full one: every analysis of
    stretto.threads then treats two chains that only a soft lock edge ordered as concurrent."""
    v = copy.copy(t)
    v.hb = t.hb_hard
    return v


def applies(t: Trace) -> bool:
    return t.multi and t.hb_hard is not None and bool(t.locks)


def contains(cs: CS, idx: int) -> bool:
    """Record idx of cs's chain lies inside the critical section."""
    return cs.acq < idx <= cs.rel


# ------------------------------------------------------------------------- schedules

class _State:
    __slots__ = ("left", "done", "gpos", "fired", "holder", "chosen", "acquired", "order", "step", "ready")

    def copy(self) -> "_State":
        s = _State.__new__(_State)
        s.left, s.done, s.gpos, s.fired = dict(self.left), set(self.done), dict(self.gpos), set(self.fired)
        s.holder, s.chosen, s.acquired = dict(self.holder), set(self.chosen), list(self.acquired)
        s.order, s.step, s.ready = dict(self.order), self.step, list(self.ready)
        return s


class _Sim:
    """Schedules of a constraint graph over a horizon in which critical sections of the locks of
    t.locks are mutually exclusive. Between two records of a chain lies a gap node ("G", chain,
    i): after record i completes and before record i+1 is issued, the chain performs its
    synchronisation operations of that gap in the order of their ordinals: releases, acquires (an
    acquire waits while another critical section holds the lock), the release points of hard
    edges (signals) and the waits for them, each at the ordinal of its own operation, with every
    earlier point of the gap the trace keeps (an edge without ordinals waits at the start of its
    gap and signals at the end of its own). A critical section whose edge from the previous one is
    hard waits for that one's release. Every process of several chains has gaps, so its waits
    stop it where a schedule stops. Entering a critical section that contains a record, or one at
    the end of a chain's horizon, is a choice the search makes; entering one without a record is
    taken whenever the lock is free. open_end: the chains cut by rule 1, which perform nothing
    after their last record in the horizon (a lock they hold stays held)."""

    def __init__(self, t: Trace, g, cut: Dict[int, int], open_end: Iterable[int] = (),
                 issued: Optional[Dict[int, int]] = None, coll_order: bool = True):
        self.t = t
        issued = issued or {}
        open_end = set(open_end)
        succ: Dict[tuple, List[tuple]] = {}
        nodes = list(dict.fromkeys(list(g.nodes) + list(g.succ)))
        node_set = set(nodes)
        for a, bs in g.succ.items():
            succ.setdefault(a, [])
            for b in bs:
                if b not in node_set:
                    node_set.add(b)
                    nodes.append(b)
                succ[a].append(b)
        # a process issues its collectives on a communicator in the run's order: the k-th pairs with the
        # k-th of the others, so a schedule that issued them in another order would not have the
        # graph's collective edges (lock_collectives, which looks for exactly that, says so itself)
        if coll_order:
            by: Dict[tuple, list] = {}
            for c in t.colls:
                by.setdefault((c.proc, c.comm), []).append(c)
            for cs in by.values():
                cs.sort(key=lambda c: c.ordinal)
                for c1, c2 in zip(cs, cs[1:]):
                    if c1.issue_node() in node_set and c2.issue_node() in node_set:
                        succ.setdefault(c1.issue_node(), []).append(c2.issue_node())
        procs = set(t.locks)
        split = set(t.chains)              # every process of several chains has gaps (its waits stop it)
        self.events: Dict[tuple, list] = {}
        chains = [c for p in split for c in t.chains_of(p)]
        for ch in chains:
            c = cut.get(ch, len(t.ranks.get(ch, ())))
            for i in range(-1, c):
                gnode = ("G", ch, i)
                nodes.append(gnode)
                self.events[gnode] = []
                comp, nxt = (ch, i, "c"), (ch, i + 1, "i")
                if i >= 0:
                    lst = succ.setdefault(comp, [])
                    if nxt in lst:
                        lst.remove(nxt)
                    lst.append(gnode)
                if nxt in node_set:
                    succ.setdefault(gnode, []).append(nxt)
        self.trailing = {("G", ch, cut.get(ch, len(t.ranks.get(ch, ()))) - 1) for ch in chains
                         if issued.get(ch) != cut.get(ch, len(t.ranks.get(ch, ())))}
        # lock events
        for p in procs:
            for lid, css in t.locks[p].items():
                for cs in css:
                    for gap, k, kind in ((cs.acq, cs.ka, "a"), (cs.rel, cs.kr, "r")):
                        gnode = ("G", cs.chain, gap)
                        if gnode in self.events:
                            self.events[gnode].append((k, 3 if kind == "a" else 0, kind, cs))
        # hard edges: a signal at the release's ordinal, a wait at the acquire's (every pair the
        # trace keeps for the edge); without ordinals, at the end and at the start of the gaps
        for a, ia, b, ib in t.hb.edges:
            ga, gb = ("G", a, ia), ("G", b, ib - 1)
            if gb not in self.events or (b, ib, "i") not in node_set:
                continue
            for kc, ka in t.hb.kall.get((a, ia, b, ib)) or [(None, None)]:
                e = (a, ia, kc, b, ib, ka)
                self.events[gb].append((ka if ka is not None else -1, 2, "w", e))
                if ga in self.events:
                    self.events[ga].append((kc if kc is not None else 10 ** 12, 1, "s", e))
        # a critical section whose edge from the previous one is hard follows it (the edge is also
        # in the hard happens-before, unless the previous one was taken before its chain's first
        # record, which no clock records)
        for p in procs:
            for lid, css in t.locks[p].items():
                for prev, cs in zip(css, css[1:]):
                    if cs.soft:
                        continue
                    e = ("L", p, lid, cs.n)
                    ga, gb = ("G", prev.chain, prev.rel), ("G", cs.chain, cs.acq)
                    if gb in self.events:
                        self.events[gb].append((cs.ka, 2, "w", e))
                    if ga in self.events:
                        self.events[ga].append((prev.kr, 1, "s", e))
        for gnode, evs in self.events.items():
            if gnode[1] in open_end and gnode in self.trailing:
                evs.clear()                  # rule 1: what the chain does after the changed receive is not known
            evs.sort(key=lambda x: (x[0], x[1]))
        self.nodes, self.succ = nodes, succ
        self.goal = [n for n in g.nodes if n[0] != "G"]
        self.fpos = t.fpos

    def choice(self, gnode, cs: CS) -> bool:
        return not cs.empty or gnode in self.trailing

    def key(self, cs: CS):
        return (self.fpos.get((cs.chain, cs.acq + 1), 10 ** 9), cs.n)

    def start(self) -> _State:
        st = _State()
        st.left = {n: 0 for n in self.nodes}
        for a, bs in self.succ.items():
            for b in bs:
                st.left[b] = st.left.get(b, 0) + 1
        st.done, st.gpos, st.fired, st.holder = set(), {}, set(), {}
        st.chosen, st.acquired, st.order, st.step = set(), [], {}, 0
        st.ready = [n for n in self.nodes if st.left.get(n, 0) == 0]
        self.run(st)
        return st

    def _finish(self, st: _State, n) -> None:
        st.done.add(n)
        st.order[n] = st.step
        st.step += 1
        for b in self.succ.get(n, ()):
            st.left[b] -= 1
            if st.left[b] == 0:
                st.ready.append(b)

    def _gap(self, st: _State, gnode) -> bool:
        """Advance an entered gap as far as it goes; True if it moved."""
        evs = self.events[gnode]
        p0 = p = st.gpos.get(gnode, 0)
        while p < len(evs):
            _, _, kind, x = evs[p]
            if kind == "r":
                if st.holder.get((x.proc, x.lid)) is x:
                    st.holder[(x.proc, x.lid)] = None
            elif kind == "s":
                st.fired.add(x)
            elif kind == "w":
                if x not in st.fired:
                    break
            else:
                if st.holder.get((x.proc, x.lid)) is not None:
                    break
                if self.choice(gnode, x) and x not in st.chosen:
                    break
                st.holder[(x.proc, x.lid)] = x
                st.acquired.append(x)
            p += 1
        st.gpos[gnode] = p
        if p == len(evs) and gnode not in st.done:
            self._finish(st, gnode)
            return True
        return p != p0

    def run(self, st: _State) -> None:
        """Everything that can happen without a choice."""
        entered = [n for n in st.gpos if n not in st.done]
        while True:
            moved = False
            while st.ready:
                n = st.ready.pop()
                if n in st.done:
                    continue
                if n[0] == "G":
                    entered.append(n)
                    self._gap(st, n)
                else:
                    self._finish(st, n)
                moved = True
            for gnode in list(entered):
                if gnode in st.done:
                    entered.remove(gnode)
                elif self._gap(st, gnode):
                    moved = True
            if not moved:
                return

    def choices(self, st: _State, first: Optional[Tuple[CS, CS]] = None, pin: bool = True) -> List[CS]:
        out = []
        acquired = set(map(id, st.acquired))
        for gnode, p in st.gpos.items():
            if gnode in st.done:
                continue
            evs = self.events[gnode]
            if p >= len(evs) or evs[p][2] != "a":
                continue
            cs = evs[p][3]
            if st.holder.get((cs.proc, cs.lid)) is not None or cs in st.chosen:
                continue
            if first is not None and cs is first[1] and id(first[0]) not in acquired:
                continue
            out.append(cs)
        pinned = first[0] if first is not None and pin else None
        out.sort(key=lambda cs: (cs is not pinned, self.key(cs)))
        return out

    def choose(self, st: _State, cs: CS) -> None:
        st.chosen.add(cs)
        self.run(st)

    def complete(self, st: _State) -> bool:
        return all(n in st.done for n in self.goal)

    def search(self, budget: int = SEARCH_BUDGET, dead_ends: Optional[List[_State]] = None) -> Optional[_State]:
        """A schedule that executes every node of the graph: the run's order first, then other
        orders of the critical sections, depth first, until the budget is spent. dead_ends, if
        given, receives the states where no critical section can be entered and nodes are left."""
        st = self.start()
        stack = [st]
        seen = set()
        while stack and budget > 0:
            st = stack.pop()
            if self.complete(st):
                return st
            ch = self.choices(st)
            if not ch and dead_ends is not None and len(dead_ends) < 32:
                dead_ends.append(st)
            for cs in reversed(ch):
                key = frozenset(map(id, st.chosen | {cs}))
                if key in seen:
                    continue
                seen.add(key)
                nxt = st.copy()
                self.choose(nxt, cs)
                budget -= 1
                stack.append(nxt)
        return None

    def held_to_end(self, st: _State, cut: Dict[int, int]) -> Dict[int, int]:
        """The chains of a dead end waiting at an acquire whose lock a chain holds to the end of the
        horizon (its release lies beyond its cut, or after a rule-1 cut): chain -> the records that
        it keeps (those before the gap)."""
        out = {}
        for gnode, p in st.gpos.items():
            evs = self.events[gnode]
            if gnode in st.done or p >= len(evs) or evs[p][2] != "a":
                continue
            h = st.holder.get((evs[p][3].proc, evs[p][3].lid))
            if h is None:
                continue
            g = ("G", h.chain, h.rel)
            if h.rel >= cut.get(h.chain, 0) or (g in self.trailing and not self.events.get(g)):
                out[gnode[1]] = gnode[2] + 1
        return out

    def greedy(self, first: Optional[Tuple[CS, CS]] = None, pin: bool = True) -> _State:
        """As far as a schedule goes that enters first[0] before first[1] (as soon as it can, with pin)
        and every other critical
        section in the run's order where it can."""
        st = self.start()
        while True:
            ch = self.choices(st, first, pin)
            if not ch:
                return st
            self.choose(st, ch[0])

def _reordered(t: Trace, st: _State) -> List[Tuple[CS, CS]]:
    """Pairs (a, b) of critical sections of one lock that the schedule takes in the other order
    than the run: a entered before b, or entered while b, earlier in the run, is not."""
    out = []
    entered = {id(cs) for cs in st.acquired}
    seen: Dict[Tuple[int, int], List[CS]] = {}
    for cs in st.acquired:
        for o in seen.get((cs.proc, cs.lid), ()):
            if o.n > cs.n:
                out.append((o, cs))
        seen.setdefault((cs.proc, cs.lid), []).append(cs)
        css = t.locks.get(cs.proc, {}).get(cs.lid, ())
        out.extend((cs, o) for o in css[:cs.n - 1] if id(o) not in entered and o.chain != cs.chain)
    return out


def _reorder_note(t: Trace, pairs: List[Tuple[CS, CS]]) -> str:
    if not pairs:
        return "critical sections in the run's order: premise P-T8 is not needed"
    shown = "; ".join(f"rank {a.proc} lock {a.lid}: #{a.n} on chain {a.chain} before #{b.n} on chain {b.chain}" for a, b in pairs[:4])
    more = f" and {len(pairs) - 4} more" if len(pairs) > 4 else ""
    return f"critical sections entered in another order than in the run: {shown}{more}"


PREMISE = ("L1+L: lock order changed; sound under premise P-T8 (the state that decides a thread's calls is "
           "race-free without the edges between critical sections that both contain an MPI call; docs/threads.md 13)")


# ------------------------------------------------------------------- alternatives

def _schedule(tv: Trace, M, v, B, probe_alt, open_end, dead_ends=None) -> Optional[_State]:
    from .feasible import without
    from .matching import build_graph
    g = build_graph(tv, without(M, v.deprived), v.cut, B, probe_alt=probe_alt, issued=v.issued)
    sim = _Sim(tv, g, v.cut, open_end, v.issued)
    st = sim.search(dead_ends=dead_ends)
    if dead_ends is not None:
        dead_ends[:] = [(sim, x) for x in dead_ends]
    return st


# rounds of _judge: each cuts more chains before an acquire
JUDGE_ROUNDS = 4


def judge(tv: Trace, M, changed, B, open_end, **kw):
    """(schedule state or None, verdict): feasible.check over the hard happens-before and a
    schedule of its horizon. Where every schedule the search tried stops with chains waiting for
    a lock that a chain holds to the end of the horizon (one cut inside its critical section),
    those chains are cut before the acquire (horizon caps), which removes what follows them, and
    the alternative is judged again: a smaller horizon, closed under the same rules."""
    from .feasible import check
    caps: Dict[int, int] = {}
    v = None
    for _ in range(JUDGE_ROUNDS):
        v = check(tv, M, changed, B, "L1", caps=dict(caps), **kw)
        if not v.feasible:
            return None, v
        dead: list = []
        st = _schedule(tv, M, v, B, kw.get("probe_alt"), open_end, dead)
        if st is not None:
            return st, v
        new = {}
        for sim, d in dead:
            new = sim.held_to_end(d, v.cut)
            if new:
                break
        if not new or all(caps.get(ch, 10 ** 9) <= k for ch, k in new.items()):
            return None, v
        for ch, k in new.items():
            caps[ch] = min(caps.get(ch, 10 ** 9), k)
    return None, v

def alternatives(t: Trace, known: Set[tuple]) -> List[tuple]:
    """Receive and probe alternatives of the hard happens-before that are realisable with
    another order of critical sections, at B = 0 or B = inf, and not among known (the L1
    findings, as ("r", recv, send) and ("p", probe, send)): (kind, item, send, Bs, state, verdict)
    with kind "r" (item an Alternative) or "p" (item a Probe)."""
    from .report import probe_kw
    from .threads import OrderFilter, candidates, probe_candidates
    tv = view(t)
    order = OrderFilter(tv)
    out = []
    alts, _ = candidates(tv)
    for a in alts:
        if ("r", a.recv, a.send) in known or order.violation(a):
            continue
        open_end = {tv.recvs[k].rank for k in a.changed}
        held, last = [], None
        for B in (0, None):
            st, v = judge(tv, a.M, a.changed, B, open_end)
            if st is not None:
                held.append(B)
                last = (st, v)
        if held:
            out.append(("r", a, a.send, held, last[0], last[1]))
    for p, s in probe_candidates(tv):
        if ("p", p.key, s.key) in known or order.probe_violation(p, s):
            continue
        held, last = [], None
        for B in (0, None):
            st, v = judge(tv, tv.observed, set(), B, {p.rank}, **probe_kw(p, s))
            if st is not None:
                held.append(B)
                last = (st, v)
        if held:
            out.append(("p", p, s.key, held, last[0], last[1]))
    return out


# ----------------------------------------------------------------- lock-order deadlocks

def _pairs(t: Trace, tv: Trace) -> List[Tuple[CS, CS]]:
    """(y, x): x entered before y in the run, on another chain, both with a record, y's acquire
    not after x's release in the hard happens-before: candidates for y first."""
    out = []
    for p in sorted(t.locks):
        for lid, css in sorted(t.locks[p].items()):
            n = 0
            for j, y in enumerate(css):
                if y.empty:
                    continue
                for x in reversed(css[max(0, j - 4):j]):
                    if x.chain == y.chain or x.empty:
                        continue
                    if y.acq + 1 < len(tv.ranks[y.chain]) and \
                            tv.hb.last_before(y.chain, y.acq + 1, x.chain) >= x.rel:
                        continue
                    out.append((y, x))
                    n += 1
                    if n >= PAIRS_PER_LOCK:
                        break
                if n >= PAIRS_PER_LOCK:
                    break
    return out[:PAIRS_PER_TRACE]


def _stuck(tv: Trace, sim: _Sim, st: _State) -> Tuple[Dict[int, int], Dict[int, int], Dict[int, Tuple[str, Set[int]]]]:
    """The horizon a stuck schedule reached: per chain the records completed, the blocking
    sends issued at the cut, and the chains stopped in the gap after their last completed
    record: at the acquire of a lock another chain holds (partner: the holder), or at a wait for
    a point of another chain whose ordinal the trace gives (partner: that chain)."""
    cut, issued, stops = {}, {}, {}
    for ch, recs in tv.ranks.items():
        c = 0
        while c < len(recs) and (ch, c, "c") in st.done:
            c += 1
        cut[ch] = c
        gnode = ("G", ch, c - 1)
        evs = sim.events.get(gnode)
        # a chain that has passed the gap reaches its next call in every continuation, whether or not
        # the schedule's matching-order edges let it issue the call here: a send there is issued
        reached = gnode in st.done if evs is not None else c == 0 or (ch, c - 1, "c") in st.done
        if c < len(recs) and reached and recs[c].op == "send":
            issued[ch] = c
        if evs is None or gnode in st.done or gnode not in st.gpos:
            continue
        p = st.gpos[gnode]
        if p >= len(evs):
            continue
        _, _, kind, x = evs[p]
        if kind == "a":
            h = st.holder.get((x.proc, x.lid))
            if h is not None:
                stops[ch] = (f"the acquire of lock {x.lid} (its critical section #{x.n}), held by chain {h.chain}", {h.chain})
        elif kind == "w" and x[0] != "L" and x[2] is not None and x[5] is not None:
            stops[ch] = (f"a synchronisation (its operation {x[5]}) after operation {x[2]} of chain {x[0]}", {x[0]})
        elif kind == "w" and x[0] == "L":
            prev = tv.locks[x[1]][x[2]][x[3] - 2]
            stops[ch] = (f"the acquire of lock {x[2]} after its critical section #{prev.n}", {prev.chain})
    return cut, issued, stops


def lock_deadlocks(t: Trace) -> List[tuple]:
    """(y, x, D, Bs): with y entered before x, the run's matching and every other critical
    section in the run's order where it can, a schedule gets stuck with a deadlock D contained in
    what it reached (theorem T7 with lock waits: every chain of D stops at an operation only
    chains of D or terminated ones could complete, x's chain at the acquire y's chain holds)."""
    from .matching import build_graph
    from .threads import contained_deadlock
    tv = view(t)
    out = []
    graphs = {}
    for y, x in _pairs(t, tv):
        held, D_last = [], None
        for B in (0, None):
            if B not in graphs:
                graphs[B] = build_graph(tv, tv.observed, None, B)
            g = graphs[B]
            sim = _Sim(tv, g, {ch: len(r) for ch, r in tv.ranks.items()})
            st = sim.greedy((y, x))
            if sim.complete(st) or not any(cs is y for cs in st.acquired):
                continue
            cut, issued, stops = _stuck(tv, sim, st)
            if x.chain not in stops:
                continue
            # a thread that reached a collective the run's order holds back would issue it as its
            # process's next one, which pairs it otherwise than the run: no claim from there
            if any(c < len(tv.ranks[ch]) and tv.ranks[ch][c].op == "coll" and (ch, c, "i") not in st.done
                   and (("G", ch, c - 1) in st.done if ("G", ch, c - 1) in sim.events else c == 0 or (ch, c - 1, "c") in st.done)
                   for ch, c in cut.items()):
                continue
            D = contained_deadlock(tv, tv.observed, set(), cut, issued, B, {}, sync_stops=stops)
            if D and x.chain in D and y.chain in D:
                held.append(B)
                D_last = D
        if held:
            out.append((y, x, D_last, held))
    return out


# ------------------------------------------------------- collectives ordered by a lock

def _coll_sig(t: Trace, c) -> tuple:
    sig = c.sig or (None, None, None)
    return (c.name, c.root) + tuple(sig)

def _in_run_order(st: _State, groups, b) -> bool:
    """Every process issued its collectives on each communicator before b in the run's order (so
    their pairing is the run's), b aside. groups: per process and communicator, its collectives
    by ordinal."""
    upto = st.order[b.issue_node()]
    for group in groups:
        rest = [c for c in group if c is not b]
        before = sorted((c for c in rest if c.issue_node() in st.done and st.order[c.issue_node()] < upto),
                        key=lambda c: st.order[c.issue_node()])
        if before != rest[:len(before)]:
            return False
    return True


def lock_collectives(t: Trace) -> List[tuple]:
    """(a, b, cs_a, cs_b, other, k): collectives a before b of one process on one communicator,
    inside critical sections of one lock on two chains, ordered by the full happens-before but
    not by the hard one; a schedule that enters b's section first issues b before a, and a call
    of another process that pairs with a in the run has other arguments than b (other)."""
    from .matching import build_graph
    from .threads import before
    tv = view(t)
    out = []
    by: Dict[tuple, list] = {}
    for c in t.colls:
        if c.proc in t.locks:
            by.setdefault((c.proc, c.comm), []).append(c)
    sets: Dict[tuple, list] = {}
    groups: Dict[tuple, list] = {}
    for c in t.colls:
        sets.setdefault((c.comm, c.ordinal), []).append(c)
        groups.setdefault((c.proc, c.comm), []).append(c)
    for group in groups.values():
        group.sort(key=lambda c: c.ordinal)
    g = None
    for (p, comm), cs in sorted(by.items()):
        cs.sort(key=lambda c: c.ordinal)
        for i, a in enumerate(cs):
            for b in cs[i + 1:]:
                if a.rank == b.rank or not before(t, a, b) or before(tv, a, b) or before(tv, b, a):
                    continue
                pair = next(((ca, cb) for css in t.locks[p].values() for ca in css for cb in css
                             if ca.chain == a.rank and contains(ca, a.idx) and cb.chain == b.rank and contains(cb, b.idx)), None)
                if pair is None:
                    continue
                if g is None:
                    g = build_graph(tv, tv.observed, None, None)
                sim = _Sim(tv, g, {ch: len(r) for ch, r in tv.ranks.items()}, coll_order=False)
                st = sim.greedy((pair[1], pair[0]), pin=False)
                ib, ia = b.issue_node(), a.issue_node()
                if ib not in st.done or (ia in st.done and st.order[ia] < st.order[ib]):
                    continue
                if not _in_run_order(st, groups.values(), b):
                    continue
                # b's place among the process's collectives on comm in this schedule, and a call of
                # another process at that place in the run with other arguments
                k = sum(1 for c in cs if c is not b and c.issue_node() in st.done and st.order[c.issue_node()] < st.order[ib])
                sb = _coll_sig(t, b)
                other = next((o for o in sets.get((comm, k), ()) if o.proc != p and _coll_sig(t, o) != sb), None)
                if other is not None:
                    out.append((a, b, pair[0], pair[1], other, k))
                    break
    return out


# --------------------------------------------------------------------------- report

def report(t: Trace, rep, classify, classify_probe, fmt_B) -> None:
    """Add the L1+L findings of t to rep (stretto.report.analyze, after the L1 findings)."""
    from .report import Finding
    for rank, why in t.lock_limits:
        rep.notes.append(f"lock order of rank {rank} kept (no L1+L alternatives from its threads): {why}")
    if not applies(t):
        return
    known = set()
    for f in rep.findings:
        if f.level == "L1" and f.probe is not None and f.send is not None:
            known.add(("p", f.probe, f.send))
        elif f.level == "L1" and f.recv is not None and f.send is not None:
            known.add(("r", f.recv, f.send))

    for kind, item, sk, Bs, st, v in alternatives(t, known):
        sim_note = _reorder_note(t, _reordered(t, st)) if st is not None else ""
        Bs_txt = "B in " + ",".join(fmt_B(B) for B in Bs)
        if kind == "r":
            a = item
            r, s = t.recvs[a.recv], t.sends[a.send]
            cls, why = classify(r, s, t.observed_send(r))
            details = [why, PREMISE, f"L1: the continuation of chain {r.rank} after {r} is undetermined by the trace (horizon)"]
            if sim_note:
                details.append(sim_note)
            if a.kind == "swap":
                details.append(f"concurrent swap: {t.recvs[a.stolen_from]}, on another thread, takes {t.observed_send(r)}")
            elif a.stolen_from is not None:
                details.append(f"steal variant: {t.recvs[a.stolen_from]} is left without {s}")
            details.append("judged at " + Bs_txt)
            rep.findings.append(Finding("1", cls, "L1+L", a.describe(t), details, B=Bs_txt, recv=a.recv, send=a.send,
                                        error=("truncate" if why.startswith("count mismatch") else "type") if cls == "1a" else ""))
        else:
            p, s = item, t.sends[sk]
            seen = t.sends[p.seen]
            cls, why = classify_probe(p, s, seen)
            details = [why, PREMISE, f"L1: continuation of chain {p.rank} undetermined"] + ([sim_note] if sim_note else []) + ["judged at " + Bs_txt]
            rep.findings.append(Finding("probe", cls, "L1+L", f"{p} could observe {s} instead of {seen}", details,
                                        B=Bs_txt, probe=p.key, send=s.key))

    for y, x, D, Bs in lock_deadlocks(t):
        Bs_txt = "B in " + ",".join(fmt_B(B) for B in Bs)
        rep.findings.append(Finding(
            "2", "2-lock-order", "L1+L",
            f"deadlock when chain {y.chain} enters critical section #{y.n} of lock {y.lid} before chain {x.chain} enters #{x.n}",
            ["; ".join(f"chain {R} stops at {op}" for R, op in D.items()),
             "every chain that could complete these operations stops too or has terminated (theorem T7 with lock waits); "
             "the run's matching up to there",
             PREMISE, "judged at " + Bs_txt], B=Bs_txt, certainty="all"))
    for a, b, ca, cb, other, k in lock_collectives(t):
        rep.findings.append(Finding(
            "2", "2-thread-collective", "L1+L",
            f"rank {a.proc}: {a.name} (chain {a.rank}) and {b.name} (chain {b.rank}) on comm {a.comm} are ordered only by "
            f"the order in which their threads enter lock {ca.lid}",
            [f"another execution enters critical section #{cb.n} before #{ca.n}: the process's collective #{k} on comm "
             f"{a.comm} is then the {b.name} of chain {b.rank}, which pairs with rank {other.proc}'s {other.name} of other "
             f"arguments ({_coll_sig(t, b)[2:]} against {_coll_sig(t, other)[2:]})",
             "MPI 4.1 §12.6.2: concurrent threads must order collectives on one communicator by synchronisation; a lock "
             "serialises them but does not order them", PREMISE]))
