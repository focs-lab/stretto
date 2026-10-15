"""Exhaustive explorer over DSL programs: ground truth for tiny instances.

Semantics (parameter B = eager threshold in bytes; None = infinite):
  * a send is issued when its process reaches it. The *request* completes when the
    message is matched if bytes > B (rendezvous) or when the send mode is 'ssend',
    otherwise at issue ('bsend' never blocks). A blocking send additionally blocks the
    *process* until its request completes;
  * a receive is posted when reached; a blocking receive blocks the process until it
    is matched. Its metadata (source, tag, bytes) is bound to `into` when the receive
    *completes* (at the match for a blocking receive, at Wait/Waitany otherwise);
  * match(s, r) is a transition enabled when s is pending, r is posted and unmatched,
    they are compatible, s is the earliest unmatched send from its source to r's rank
    that r accepts, and no earlier-posted unmatched receive at r's rank accepts s;
  * Wait completes a request that is complete; Waitany completes any not-yet-consumed
    request that is complete (nondeterministic) and binds the index;
  * a collective fires when every member of the communicator has issued its k-th
    collective on that communicator (blocking members are parked at it, nonblocking
    members have moved on); different names => 'collective-mismatch';
  * count overflow at match time => 'truncate'.

Exploration is exhaustive over interleavings with memoisation. With reduce=True a
persistent-set reduction is applied: branch evaluation, send issue, receive posting,
enabled waits, collective firing, and the match of a specific-source receive (unique by
non-overtaking) commute with every other enabled transition and are taken alone; only
wildcard-source matches and Waitany choices branch. reduce=False enumerates every
enabled transition and is used as the reference in the differential test.

Outcomes: done | deadlock | force-blocked | truncate | collective-mismatch, each with the
matching, the orphan sends (issued, never matched) and leaked receives (posted, never
matched).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple, Union

from .dsl import ANY, Coll, If, Meta, Op, Program, Recv, Send, Wait, Waitany

Key = Tuple[int, int]


@dataclass(frozen=True)
class Msg:
    key: Key
    src: int
    dst: int
    tag: int
    bytes: int
    comm: int


@dataclass(frozen=True)
class Post:
    key: Key
    rank: int
    src: int
    tag: int
    cap: int
    comm: int
    blocking: bool
    var: Optional[str]


@dataclass(frozen=True)
class CollIssue:
    rank: int
    comm: int
    ordinal: int
    name: str
    req: Optional[str]      # None: blocking, the process is parked at it


@dataclass(frozen=True)
class PState:
    pos: int                                         # index of the next op in the rank's program
    inj: Tuple[Op, ...]                              # ops spliced in by If, executed before pos
    env: Tuple[Tuple[str, object], ...]
    nsend: int
    nrecv: int
    ncoll: Tuple[Tuple[int, int], ...] = ()          # (comm, count)
    blocked_send: Optional[Key] = None               # process parked until this send completes
    blocked_recv: Optional[Key] = None               # process parked until this receive matches
    blocked_coll: Optional[Tuple[int, int]] = None   # process parked at (comm, ordinal)
    reqs: Tuple[Tuple[str, Tuple[str, object]], ...] = ()   # name -> (kind, key), live requests only


@dataclass(frozen=True)
class GState:
    procs: Tuple[PState, ...]
    pending: Tuple[Msg, ...]                       # issued, unmatched sends
    posted: Tuple[Post, ...]                       # posted, unmatched receives
    matched: Tuple[Tuple[Key, Key], ...]           # (recv key, send key) in match order
    meta: Tuple[Tuple[Key, str, Meta], ...]        # (recv key, var, metadata) matched, not yet completed
    completed_recv: FrozenSet[Key]
    completed_send: FrozenSet[Key]                 # send *requests* that completed
    colls: Tuple[CollIssue, ...]                   # issued collectives not yet fired
    completed_coll: FrozenSet[Tuple[int, int, int]]   # (rank, comm, ordinal) fired nonblocking
    errors: Tuple[str, ...] = ()                   # MPI errors hit on this path (sorted, unique)


@dataclass
class Outcome:
    kind: str
    matching: FrozenSet[Tuple[Key, Key]]
    orphans: FrozenSet[Key]
    detail: str = ""
    leaked: FrozenSet[Key] = frozenset()
    errors: Tuple[str, ...] = ()        # e.g. ('truncate: 32B into 16B at r(0, 0)',)

    def sig(self):
        return (self.kind, self.matching, self.orphans, self.leaked, self.errors)

    @property
    def error_kinds(self):
        return {e.split(":")[0] for e in self.errors}

    def __str__(self):
        m = ", ".join(f"r{r}<-s{s}" for r, s in sorted(self.matching))
        o = ", ".join(f"s{k}" for k in sorted(self.orphans))
        l = ", ".join(f"r{k}" for k in sorted(self.leaked))
        return (f"{self.kind}: [{m}]" + (f" orphans=[{o}]" if o else "")
                + (f" leaked=[{l}]" if l else "") + (f" {self.detail}" if self.detail else ""))


@dataclass
class Result:
    outcomes: List[Outcome] = field(default_factory=list)
    states: int = 0

    @property
    def matches(self) -> Set[Tuple[Key, Key]]:
        return {p for o in self.outcomes for p in o.matching}

    def outcomes_with(self, rkey: Key, skey: Key) -> List[Outcome]:
        return [o for o in self.outcomes if (rkey, skey) in o.matching]

    def kinds(self) -> Set[str]:
        """Terminal kinds plus the kinds of MPI errors hit on the way (truncate, collective-mismatch)."""
        out = {o.kind for o in self.outcomes}
        for o in self.outcomes:
            out |= o.error_kinds
        return out

    def sigs(self) -> Set[tuple]:
        return {o.sig() for o in self.outcomes}


class ExploreError(Exception):
    pass


# ------------------------------------------------------------------ helpers

def _env(p: PState) -> dict:
    return dict(p.env)


def _with(p: PState, **kw) -> PState:
    d = p.__dict__.copy()
    d.update(kw)
    return PState(**d)


def _gs(st: GState, **kw) -> GState:
    d = st.__dict__.copy()
    d.update(kw)
    return GState(**d)


def _procs(st: GState, i: int, p: PState) -> Tuple[PState, ...]:
    procs = list(st.procs)
    procs[i] = p
    return tuple(procs)


def _accepts(r: Post, m: Msg) -> bool:
    return (m.dst == r.rank and m.comm == r.comm and (r.src == ANY or r.src == m.src)
            and (r.tag == ANY or r.tag == m.tag))


def _ncoll(p: PState, comm: int) -> int:
    return dict(p.ncoll).get(comm, 0)


def _bump_ncoll(p: PState, comm: int) -> Tuple[Tuple[int, int], ...]:
    d = dict(p.ncoll)
    d[comm] = d.get(comm, 0) + 1
    return tuple(sorted(d.items()))


def _reqs_del(reqs, name):
    return tuple(x for x in reqs if x[0] != name)


def _add_error(errors: Tuple[str, ...], e: str) -> Tuple[str, ...]:
    return errors if e in errors else tuple(sorted(errors + (e,)))


# ---------------------------------------------------------------- the model

def explore(prog: Program, B: Optional[int] = None, force: Optional[Dict[Key, Key]] = None,
            max_states: int = 2_000_000, comms: Optional[Dict[int, List[int]]] = None,
            reduce: bool = True) -> Result:
    """force: {recv key: send key} restricts match transitions for those receives.
    comms: communicator id -> member ranks (default: comm 0 = all ranks; other ids must
    be given)."""
    force = force or {}
    ranks = sorted(prog)
    comms = dict(comms or {})
    comms.setdefault(0, list(ranks))
    ctx = _Ctx(prog, B, force, ranks, comms, reduce)
    init = GState(tuple(PState(0, (), (), 0, 0) for r in ranks),
                  (), (), (), (), frozenset(), frozenset(), (), frozenset())
    res = Result()
    seen: Set[GState] = set()
    stack = [init]
    while stack:
        st = stack.pop()
        if st in seen:
            continue
        seen.add(st)
        res.states += 1
        if res.states > max_states:
            raise RuntimeError("state space too large")
        succs, outs = ctx.successors(st)
        res.outcomes.extend(outs)
        if succs:
            stack.extend(succs)
        elif not outs:
            res.outcomes.append(ctx.terminal(st))
    uniq, out = set(), []
    for o in res.outcomes:
        if o.sig() not in uniq:
            uniq.add(o.sig())
            out.append(o)
    res.outcomes = out
    return res


class _Ctx:
    def __init__(self, prog, B, force, ranks, comms, reduce):
        self.prog, self.B, self.force, self.ranks, self.comms, self.reduce = prog, B, force, ranks, comms, reduce
        self.progs = [tuple(prog[r]) for r in ranks]

    def done_p(self, i: int, p: PState) -> bool:
        return not p.inj and p.pos >= len(self.progs[i])

    def next_op(self, i: int, p: PState):
        if p.inj:
            return p.inj[0]
        return self.progs[i][p.pos] if p.pos < len(self.progs[i]) else None

    @staticmethod
    def advance(p: PState, **kw) -> PState:
        if p.inj:
            return _with(p, inj=p.inj[1:], **kw)
        return _with(p, pos=p.pos + 1, **kw)

    # ---- terminal classification
    def terminal(self, st: GState) -> Outcome:
        """Classify a state with no successors."""
        done = all(self.done_p(i, p) and p.blocked_send is None and p.blocked_recv is None and p.blocked_coll is None
                   for i, p in enumerate(st.procs))
        kind = "done" if done else "deadlock"
        if not done and self.force and self._force_starved(st):
            kind = "force-blocked"
        return Outcome(kind, frozenset(st.matched), frozenset(m.key for m in st.pending), "; ".join(st.errors),
                       frozenset(r.key for r in st.posted), st.errors)

    def _force_starved(self, st: GState) -> bool:
        """Some posted forced receive could match a pending send if the force were lifted."""
        for r in st.posted:
            if r.key not in self.force:
                continue
            for m in st.pending:
                if _accepts(r, m) and m.key != self.force[r.key]:
                    return True
        return False

    # ---- enabled matches
    def enabled_matches(self, st: GState):
        out = []
        for r in st.posted:
            for m in st.pending:
                if not _accepts(r, m):
                    continue
                if r.key in self.force and self.force[r.key] != m.key:
                    continue
                if any(x.src == m.src and x.dst == m.dst and x.comm == m.comm and x.key < m.key and _accepts(r, x)
                       for x in st.pending):
                    continue
                if any(x.rank == r.rank and x.key < r.key and _accepts(x, m) for x in st.posted):
                    continue
                out.append((r, m))
        return out

    def match_step(self, st: GState, r: Post, m: Msg) -> GState:
        """Apply match(m, r). A count overflow is recorded as an error on the path (as under
        MPI_ERRORS_RETURN) and the match still happens, so that exploration stays
        commutative; the error surfaces in the terminal outcome."""
        if m.bytes > r.cap:
            st = _gs(st, errors=_add_error(st.errors, f"truncate: {m.bytes}B into {r.cap}B at r{r.key}"))
        procs = list(st.procs)
        pi = self.ranks.index(m.src)
        p = procs[pi]
        if p.blocked_send == m.key:
            procs[pi] = _with(p, blocked_send=None)
        csend = st.completed_send | {m.key}
        pj = self.ranks.index(r.rank)
        q = procs[pj]
        meta = st.meta
        crecv = st.completed_recv
        if r.blocking:
            env = _env(q)
            if r.var:
                env[r.var] = Meta(m.src, m.tag, m.bytes)
            q = _with(q, blocked_recv=None, env=tuple(sorted(env.items())))
            crecv = crecv | {r.key}
        elif r.var:
            meta = meta + ((r.key, r.var, Meta(m.src, m.tag, m.bytes)),)
        procs[pj] = q
        return _gs(st, procs=tuple(procs), pending=tuple(x for x in st.pending if x.key != m.key),
                   posted=tuple(x for x in st.posted if x.key != r.key),
                   matched=st.matched + ((r.key, m.key),), meta=meta, completed_recv=crecv, completed_send=csend)

    # ---- collectives
    def coll_ready(self, st: GState):
        """(comm, ordinal) whose every member has issued; None if none."""
        issued: Dict[Tuple[int, int], List[CollIssue]] = {}
        for c in st.colls:
            issued.setdefault((c.comm, c.ordinal), []).append(c)
        for (comm, k), members in sorted(issued.items()):
            need = set(self.comms.get(comm, []))
            if not need:
                raise ExploreError(f"unknown communicator {comm}: pass comms=")
            if {c.rank for c in members} == need:
                return (comm, k, members)
        return None

    def coll_fire(self, st: GState, comm: int, k: int, members: List[CollIssue]) -> GState:
        """Fire collective k on comm. A name mismatch is recorded as an error and the
        collective still completes (same commutativity argument as for truncation)."""
        names = {c.name for c in members}
        if len(names) > 1:
            st = _gs(st, errors=_add_error(st.errors, f"collective-mismatch: comm {comm} #{k}: {sorted(names)}"))
        procs = list(st.procs)
        ccoll = st.completed_coll
        for c in members:
            i = self.ranks.index(c.rank)
            p = procs[i]
            if c.req is None:
                procs[i] = self.advance(p, blocked_coll=None)
            else:
                ccoll = ccoll | {(c.rank, comm, k)}
        return _gs(st, procs=tuple(procs), colls=tuple(c for c in st.colls if not (c.comm == comm and c.ordinal == k)),
                   completed_coll=ccoll)

    # ---- process steps
    def _req_complete(self, st: GState, p: PState, kind: str, key) -> bool:
        if kind == "recv":
            return any(rk == key for rk, _ in st.matched)
        if kind == "send":
            return key in st.completed_send
        if kind == "coll":
            return key in st.completed_coll
        return False

    def local_step(self, st: GState, i: int) -> Optional[GState]:
        """The unique deterministic step of process i, or None."""
        p = st.procs[i]
        if p.blocked_send is not None or p.blocked_recv is not None or p.blocked_coll is not None or self.done_p(i, p):
            return None
        rank = self.ranks[i]
        op = self.next_op(i, p)
        B = self.B
        if isinstance(op, If):
            branch = op.then if op.cond(_env(p)) else op.els
            q = self.advance(p)
            return _gs(st, procs=_procs(st, i, _with(q, inj=tuple(branch) + q.inj)))
        if isinstance(op, Send):
            key = (rank, p.nsend)
            dst = op.dst
            if callable(dst):
                dst = dst(_env(p))
            elif isinstance(dst, str):
                v = _env(p)[dst]
                dst = v.src if isinstance(v, Meta) else int(v)
            m = Msg(key, rank, dst, op.tag, op.bytes, op.comm)
            mode = getattr(op, "mode", "std")
            rendez = mode == "ssend" or (mode != "bsend" and B is not None and op.bytes > B)
            reqs = p.reqs + ((op.req, ("send", key)),) if op.req else p.reqs
            blocked = key if (op.req is None and rendez) else None
            csend = st.completed_send if rendez else st.completed_send | {key}
            np_ = self.advance(p, nsend=p.nsend + 1, blocked_send=blocked, reqs=reqs)
            return _gs(st, procs=_procs(st, i, np_), pending=st.pending + (m,), completed_send=csend)
        if isinstance(op, Recv):
            key = (rank, p.nrecv)
            post = Post(key, rank, op.src, op.tag, op.cap, op.comm, op.req is None, op.into)
            reqs = p.reqs + ((op.req, ("recv", key)),) if op.req else p.reqs
            np_ = self.advance(p, nrecv=p.nrecv + 1, blocked_recv=key if op.req is None else None, reqs=reqs)
            return _gs(st, procs=_procs(st, i, np_), posted=st.posted + (post,))
        if isinstance(op, Wait):
            live = dict(p.reqs)
            if op.req not in live:      # MPI_REQUEST_NULL (already completed): returns at once
                return _gs(st, procs=_procs(st, i, self.advance(p)))
            kind, key = live[op.req]
            if not self._req_complete(st, p, kind, key):
                return None
            return self._finish_request(st, i, p, op.req, kind, key, None, None)
        if isinstance(op, Coll):
            comm = op.comm
            k = _ncoll(p, comm)
            c = CollIssue(rank, comm, k, op.name, op.req)
            if op.req is None:
                np_ = _with(p, ncoll=_bump_ncoll(p, comm), blocked_coll=(comm, k))
            else:
                np_ = self.advance(p, ncoll=_bump_ncoll(p, comm), reqs=p.reqs + ((op.req, ("coll", (rank, comm, k))),))
            return _gs(st, procs=_procs(st, i, np_), colls=st.colls + (c,))
        return None   # Waitany: never local

    def _finish_request(self, st, i, p, name, kind, key, idx_var, idx):
        env = _env(p)
        meta = st.meta
        crecv = st.completed_recv
        if kind == "recv":
            crecv = crecv | {key}
            for rk, var, mt in st.meta:
                if rk == key:
                    env[var] = mt
            meta = tuple(x for x in st.meta if x[0] != key)
        if idx_var is not None:
            env[idx_var] = idx
        np_ = self.advance(p, env=tuple(sorted(env.items())), reqs=_reqs_del(p.reqs, name))
        return _gs(st, procs=_procs(st, i, np_), meta=meta, completed_recv=crecv)

    def waitany_steps(self, st: GState, i: int) -> List[GState]:
        p = st.procs[i]
        if p.blocked_send is not None or p.blocked_recv is not None or p.blocked_coll is not None or self.done_p(i, p):
            return []
        op = self.next_op(i, p)
        if not isinstance(op, Waitany):
            return []
        out = []
        live = dict(p.reqs)
        for idx, name in enumerate(op.reqs):
            if name not in live:
                continue                       # already consumed
            kind, key = live[name]
            if self._req_complete(st, p, kind, key):
                out.append(self._finish_request(st, i, p, name, kind, key, op.into, idx))
        return out

    # ---- successor enumeration
    def successors(self, st: GState):
        outs: List[Outcome] = []
        succs: List[GState] = []
        matches = self.enabled_matches(st)
        if self.reduce:
            for r, m in matches:
                if r.src != ANY:
                    return [self.match_step(st, r, m)], outs
            ready = self.coll_ready(st)
            if ready is not None:
                return [self.coll_fire(st, *ready)], outs
            for i in range(len(st.procs)):
                res = self.local_step(st, i)
                if res is not None:
                    return [res], outs
            for r, m in matches:
                succs.append(self.match_step(st, r, m))
            for i in range(len(st.procs)):
                succs.extend(self.waitany_steps(st, i))
            return succs, outs
        # unreduced: every enabled transition
        for r, m in matches:
            succs.append(self.match_step(st, r, m))
        ready = self.coll_ready(st)
        if ready is not None:
            succs.append(self.coll_fire(st, *ready))
        for i in range(len(st.procs)):
            res = self.local_step(st, i)
            if res is not None:
                succs.append(res)
            succs.extend(self.waitany_steps(st, i))
        return succs, outs
