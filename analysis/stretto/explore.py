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
  * a collective completes for a member once the members its shape requires have issued
    their k-th collective on that communicator (matching.coll_shape: barrier-like ones need
    everyone, bcast-like ones the root, reduce-like ones everyone for the root and nobody for
    the others, scan the members before it); blocking members are parked at it, nonblocking
    members have moved on. A collective whose root is unknown synchronises everyone.
    Different names once everyone has issued => 'collective-mismatch';
  * count overflow at match time => 'truncate'; a ready-mode send that is not matched to a
    receive posted before its issue => 'rsend-no-receive' (both errors on the path; see
    _Ctx.local_step for when the error is recorded); an Abort op ends its rank and makes the
    outcome 'abort' (the program's own check failed). A program with a ready-mode send is
    explored without the reduction, since receive posting then no longer commutes with the
    sender's steps;
  * a Lazy op is replaced by the ops its function computes from the rank's variables (a
    local step, like a branch); an Unknown op ends its rank and makes the outcome 'unknown'
    unless some rank aborted: the rank's continuation is not determined, so the execution is
    neither finished nor deadlocked. A message carries the payload of its Send, which the
    receive binds with its metadata.

Exploration is exhaustive over interleavings with memoisation. With reduce=True a
persistent-set reduction is applied: branch evaluation, send issue, receive posting,
enabled waits, collective firing, and the match of a specific-source receive (unique by
non-overtaking) commute with every other enabled transition and are taken alone; only
wildcard-source matches and Waitany choices branch. reduce=False enumerates every
enabled transition and is used as the reference in the differential test.

Outcomes: done | deadlock | abort | unknown | force-blocked, with the errors hit on the path
(truncate, collective-mismatch, rsend-no-receive, whose sends ready_errors() names), the
matching in match order (one witness execution), the orphan sends (issued, never matched),
the leaked receives (posted, never matched), every send issued with its destination and tag,
the collectives issued per rank, which is what a skeleton comparison needs, and the ranks that
stopped at an Unknown op.

A program whose ops are computed during the exploration (Lazy) cannot be scanned for
ready-mode sends beforehand; it may provide dynamic_rsend(), true once one was computed, and
is then explored again without the reduction.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple, Union

from .dsl import ANY, Abort, Coll, If, Lazy, Meta, Op, Probe, Program, Recv, Send, Test, Unknown, Wait, Waitany
from .matching import coll_shape

Key = Tuple[int, int]


@dataclass(frozen=True)
class Msg:
    key: Key
    src: int
    dst: int
    tag: int
    bytes: int
    comm: int
    data: Optional[str] = None


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
    root: Optional[int] = None


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
    nprobe: int = 0


@dataclass(frozen=True)
class GState:
    procs: Tuple[PState, ...]
    pending: Tuple[Msg, ...]                       # issued, unmatched sends
    posted: Tuple[Post, ...]                       # posted, unmatched receives
    matched: Tuple[Tuple[Key, Key], ...]           # (recv key, send key) in match order
    meta: Tuple[Tuple[Key, str, Meta], ...]        # (recv key, var, metadata) matched, not yet completed
    completed_recv: FrozenSet[Key]
    completed_send: FrozenSet[Key]                 # send *requests* that completed
    colls: Tuple[CollIssue, ...]                   # issued collectives of groups not yet complete
    completed_coll: FrozenSet[Tuple[int, int, int]]   # (rank, comm, ordinal) completed for that member
    errors: Tuple[str, ...] = ()                   # MPI errors hit on this path (sorted, unique)
    sent: Tuple[Tuple[Key, int, int, int], ...] = ()   # every send issued: (key, dst, tag, comm)
    aborted: Tuple[int, ...] = ()                  # ranks that aborted
    unknown: Tuple[Tuple[int, str], ...] = ()      # (rank, why) of the ranks stopped at an Unknown op
    # pending ready-mode sends issued while a receive that accepts them was posted, each with
    # those receives: the only ones that may take it without error (sorted by send key)
    ready: Tuple[Tuple[Key, FrozenSet[Key]], ...] = ()
    # results of the probes whose result is recorded (forced ones, or all with record_probes):
    # (probe key, send key or None for a false flag), and the marks of marked waits, in order
    probed: Tuple[Tuple[Key, Optional[Key]], ...] = ()
    marks: Tuple[str, ...] = ()


@dataclass
class Outcome:
    kind: str
    matching: FrozenSet[Tuple[Key, Key]]
    orphans: FrozenSet[Key]
    detail: str = ""
    leaked: FrozenSet[Key] = frozenset()
    errors: Tuple[str, ...] = ()        # e.g. ('truncate: 32B into 16B at r(0, 0)',)
    order: Tuple[Tuple[Key, Key], ...] = ()   # the matching in match order: one witness execution
    sent: Tuple[Tuple[Key, int, int, int], ...] = ()   # sends issued on this execution: (key, dst, tag, comm)
    ncoll: Tuple[Tuple[int, int], ...] = ()   # (rank, collectives issued) per rank
    unknown: Tuple[Tuple[int, str], ...] = ()   # (rank, why): ranks whose continuation is not determined
    # (rank, position, blocked) per rank in the witness's final state: the index of its next op
    # in the program and the kind of op it is parked at ('send', 'recv', 'coll', or ''); not
    # part of sig, so it is the first witness's of the outcome (stretto.reads reads it)
    stops: Tuple[Tuple[int, int, str], ...] = ()
    probes: Tuple[Tuple[Key, Optional[Key]], ...] = ()   # recorded probe results (GState.probed)
    marks: Tuple[str, ...] = ()                         # marks of the marked waits that returned

    def sig(self):
        return (self.kind, self.matching, self.orphans, self.leaked, self.errors, self.unknown, self.probes, self.marks)

    @property
    def error_kinds(self):
        return {e.split(":")[0] for e in self.errors}

    def __str__(self):
        m = ", ".join(f"r{r}<-s{s}" for r, s in sorted(self.matching))
        o = ", ".join(f"s{k}" for k in sorted(self.orphans))
        l = ", ".join(f"r{k}" for k in sorted(self.leaked))
        u = "; ".join(f"rank {r}: {why}" for r, why in self.unknown)
        return (f"{self.kind}: [{m}]" + (f" orphans=[{o}]" if o else "")
                + (f" leaked=[{l}]" if l else "") + (f" {self.detail}" if self.detail else "")
                + (f" undetermined: {u}" if u else ""))


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


_READY_ERROR = re.compile(r"^rsend-no-receive: s\((-?\d+), (-?\d+)\)")


def ready_errors(o: Outcome) -> FrozenSet[Key]:
    """The ready-mode sends that an execution did not match to a receive posted before their
    issue: the sends its rsend-no-receive errors name."""
    out = set()
    for e in o.errors:
        m = _READY_ERROR.match(e)
        if m:
            out.add((int(m.group(1)), int(m.group(2))))
    return frozenset(out)


class ExploreError(Exception):
    pass


class StateCapReached(RuntimeError):
    """The exploration stopped at max_states: not an error of the program or of the explorer.
    A subclass of RuntimeError, which callers caught before this class existed."""


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


def _ready_error(key: Key) -> str:
    """The error of a ready-mode send that no receive posted before its issue takes. One text
    wherever it is detected, so that executions alike in their matching and their errors are one
    outcome."""
    return f"rsend-no-receive: s{key} is not taken by a receive posted before its issue"


# ---------------------------------------------------------------- the model

def explore(prog: Program, B: Optional[int] = None, force: Optional[Dict[Key, Key]] = None,
            max_states: int = 2_000_000, comms: Optional[Dict[int, List[int]]] = None,
            reduce: bool = True, force_probes: Optional[Dict[Key, Optional[Key]]] = None,
            record_probes: bool = False) -> Result:
    """force: {recv key: send key} restricts match transitions for those receives.
    comms: communicator id -> member ranks (default: comm 0 = all ranks; other ids must
    be given). force_probes: {probe key: send key, or None for a false flag} fixes what those
    probes return: the probe waits until the message can be seen (for ever if it never can),
    and a false flag is always possible for an Iprobe; their results are recorded
    (Outcome.probes). record_probes also branches on, and records, what every other probe
    returns; otherwise a probe whose result is bound to no variable is one step: an Iprobe
    constrains nothing (false is always possible) and a blocking probe waits for a message."""
    force = force or {}
    ranks = sorted(prog)
    comms = dict(comms or {})
    comms.setdefault(0, list(ranks))
    dynamic_rsend = getattr(prog, "dynamic_rsend", None)
    if reduce and (_has_rsend(prog) or (dynamic_rsend is not None and dynamic_rsend())):
        # whether a ready-mode send finds its receive posted depends on the order of the
        # receiver's posting and the sender's issue, so posting no longer commutes with
        # every other step and the persistent-set reduction is unsound: explore unreduced
        reduce = False
    ctx = _Ctx(prog, B, force, ranks, comms, reduce, force_probes or {}, record_probes)
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
            raise StateCapReached("state space too large")
        succs, outs = ctx.successors(st)
        res.outcomes.extend(outs)
        if succs:
            stack.extend(succs)
        elif not outs:
            res.outcomes.append(ctx.terminal(st))
    if reduce and dynamic_rsend is not None and dynamic_rsend():
        # a ready-mode send was computed during the exploration: the reduction did not apply
        return explore(prog, B, force, max_states, comms, reduce=False, force_probes=force_probes,
                       record_probes=record_probes)
    uniq, out = set(), []
    for o in res.outcomes:
        if o.sig() not in uniq:
            uniq.add(o.sig())
            out.append(o)
    res.outcomes = out
    return res


def _has_rsend(prog: Program) -> bool:
    def walk(ops) -> bool:
        for op in ops:
            if isinstance(op, Send) and op.mode == "rsend":
                return True
            if isinstance(op, If) and (walk(op.then) or walk(op.els)):
                return True
        return False
    return any(walk(ops) for ops in prog.values())


class _Ctx:
    def __init__(self, prog, B, force, ranks, comms, reduce, force_probes=None, record_probes=False):
        self.prog, self.B, self.force, self.ranks, self.comms, self.reduce = prog, B, force, ranks, comms, reduce
        self.force_probes, self.record_probes = force_probes or {}, record_probes
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
        # an abort stands whatever an undetermined rank would do later (its later steps can be
        # scheduled after the abort); finished and deadlocked need every rank determined
        kind = "abort" if st.aborted else "unknown" if st.unknown else "done" if done else "deadlock"
        if kind == "deadlock" and self.force and self._force_starved(st):
            kind = "force-blocked"
        ncoll = tuple((self.ranks[i], sum(n for _, n in p.ncoll)) for i, p in enumerate(st.procs))
        stops = tuple((self.ranks[i], p.pos, "send" if p.blocked_send is not None else "recv" if p.blocked_recv is not None
                       else "coll" if p.blocked_coll is not None else "") for i, p in enumerate(st.procs))
        errors = st.errors
        for key, _ in st.ready:
            # a ready send that no receive took has no matching receive at all
            errors = _add_error(errors, _ready_error(key))
        return Outcome(kind, frozenset(st.matched), frozenset(m.key for m in st.pending), "; ".join(errors),
                       frozenset(r.key for r in st.posted), errors, st.matched, st.sent, ncoll, st.unknown, stops,
                       probes=st.probed, marks=st.marks)

    # ---- probes
    def visible(self, st: GState, rank: int, op: Probe) -> List[Msg]:
        """The messages a probe of rank could return now: pending, accepted, the earliest
        pending one of its source that the probe accepts (non-overtaking), and taken by no
        receive the rank has posted (MPI 4.1 §3.8.1: a probe returns what a receive posted at
        that point would match, and a posted receive that accepts the message comes first)."""
        out = []
        for m in st.pending:
            if not (m.dst == rank and m.comm == op.comm and (op.src == ANY or op.src == m.src) and (op.tag == ANY or op.tag == m.tag)):
                continue
            if any(x.src == m.src and x.dst == rank and x.comm == m.comm and x.key < m.key and (op.tag == ANY or op.tag == x.tag)
                   for x in st.pending):
                continue
            if any(r.rank == rank and _accepts(r, m) for r in st.posted):
                continue
            out.append(m)
        return out

    def probe_results(self, st: GState, i: int) -> Tuple[List[Tuple[Optional[Msg], bool]], bool]:
        """What the probe that is process i's next op can return now, each with whether it is
        recorded, and whether the step can be taken alone (persistent-set reduction): a
        forced probe, or one whose result nothing records, has at most one result, which no
        other transition disables (only a receive of the probe's rank could take a message it
        sees, and the rank is at the probe), so it commutes with every other step."""
        p = st.procs[i]
        op = self.next_op(i, p)
        rank = self.ranks[i]
        key = (rank, p.nprobe)
        if key in self.force_probes:
            want = self.force_probes[key]
            if want is None:
                return ([] if op.blocking else [(None, True)]), True
            return [(m, True) for m in self.visible(st, rank, op) if m.key == want], True
        vis = self.visible(st, rank, op)
        if not self.record_probes and op.into is None:
            if op.blocking:
                return ([(vis[0], False)] if vis else []), True
            return [(None, False)], True
        res = [(m, True) for m in vis] + ([] if op.blocking else [(None, True)])
        return res, False

    def probe_step(self, st: GState, i: int, m: Optional[Msg], recorded: bool) -> GState:
        p = st.procs[i]
        op = self.next_op(i, p)
        key = (self.ranks[i], p.nprobe)
        env = p.env
        if op.into is not None:
            e = _env(p)
            e[op.into] = Meta(m.src, m.tag, m.bytes, m.data) if m is not None else None
            env = tuple(sorted(e.items()))
        np_ = self.advance(p, nprobe=p.nprobe + 1, env=env)
        probed = st.probed + ((key, m.key if m is not None else None),) if recorded else st.probed
        return _gs(st, procs=_procs(st, i, np_), probed=probed)

    def probe_steps(self, st: GState, i: int, alone: bool) -> List[GState]:
        """The steps of process i at a probe: those that can be taken alone (alone=True) or the
        branching ones (alone=False)."""
        p = st.procs[i]
        if p.blocked_send is not None or p.blocked_recv is not None or p.blocked_coll is not None or self.done_p(i, p):
            return []
        if not isinstance(self.next_op(i, p), Probe):
            return []
        res, single = self.probe_results(st, i)
        if single != alone:
            return []
        return [self.probe_step(st, i, m, rec) for m, rec in res]

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
        if st.ready:
            # a ready send is correct only if the receive that takes it was posted before its
            # issue; another receive that accepted it then does not make it so. A pending ready
            # send whose receives of that kind are all matched elsewhere is erroneous whatever
            # happens next, and its entry goes (fewer distinct states)
            errors, ready = st.errors, []
            for key, posted in st.ready:
                if key == m.key:
                    if r.key not in posted:
                        errors = _add_error(errors, _ready_error(key))
                    continue
                if r.key in posted:
                    posted = posted - {r.key}
                    if not posted:
                        errors = _add_error(errors, _ready_error(key))
                        continue
                ready.append((key, posted))
            st = _gs(st, errors=errors, ready=tuple(ready))
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
                env[r.var] = Meta(m.src, m.tag, m.bytes, m.data)
            q = _with(q, blocked_recv=None, env=tuple(sorted(env.items())))
            crecv = crecv | {r.key}
        elif r.var:
            meta = meta + ((r.key, r.var, Meta(m.src, m.tag, m.bytes, m.data)),)
        procs[pj] = q
        return _gs(st, procs=tuple(procs), pending=tuple(x for x in st.pending if x.key != m.key),
                   posted=tuple(x for x in st.posted if x.key != r.key),
                   matched=st.matched + ((r.key, m.key),), meta=meta, completed_recv=crecv, completed_send=csend)

    # ---- collectives
    def coll_ready(self, st: GState):
        """(comm, ordinal, members that can complete now); None if none. A member completes
        once the members its shape requires have issued (see the module docstring)."""
        groups: Dict[Tuple[int, int], List[CollIssue]] = {}
        for c in st.colls:
            groups.setdefault((c.comm, c.ordinal), []).append(c)
        for (comm, k), members in sorted(groups.items()):
            need = self.comms.get(comm, [])
            if not need:
                raise ExploreError(f"unknown communicator {comm}: pass comms=")
            issued = {c.rank for c in members}
            pos = {r: i for i, r in enumerate(need)}
            ready = []
            for c in members:
                if (c.rank, comm, k) in st.completed_coll:
                    continue
                shape, _ = coll_shape(c.name)
                if c.root is None and shape != "prefix":
                    shape = "all"
                ok = (issued == set(need) if shape == "all" else
                      c.rank == c.root or c.root in issued if shape == "root-out" else
                      c.rank != c.root or issued == set(need) if shape == "root-in" else
                      all(r in issued for r in need if pos.get(r, 0) < pos.get(c.rank, 0)))
                if ok:
                    ready.append(c)
            if ready:
                return (comm, k, ready)
        return None

    def coll_fire(self, st: GState, comm: int, k: int, ready: List[CollIssue]) -> GState:
        """Complete the given members of collective k on comm. Once every member has issued,
        a name mismatch is recorded as an error (the collective still completes: same
        commutativity argument as for truncation) and the group leaves the state."""
        group = [c for c in st.colls if c.comm == comm and c.ordinal == k]
        procs = list(st.procs)
        ccoll = st.completed_coll
        for c in ready:
            i = self.ranks.index(c.rank)
            if c.req is None:
                procs[i] = self.advance(procs[i], blocked_coll=None)
            ccoll = ccoll | {(c.rank, comm, k)}
        errors = st.errors
        colls = st.colls
        if {c.rank for c in group} == set(self.comms.get(comm, [])):
            names = {c.name for c in group}
            if len(names) > 1:
                errors = _add_error(errors, f"collective-mismatch: comm {comm} #{k}: {sorted(names)}")
            if all((c.rank, comm, k) in ccoll for c in group):
                colls = tuple(c for c in st.colls if not (c.comm == comm and c.ordinal == k))
        return _gs(st, procs=tuple(procs), colls=colls, completed_coll=ccoll, errors=errors)

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
        if isinstance(op, Lazy):
            q = self.advance(p)
            return _gs(st, procs=_procs(st, i, _with(q, inj=tuple(op.fn(_env(p))) + q.inj)))
        if isinstance(op, Abort):
            # the rank stops here; the state is terminal for it (others may still run)
            np_ = _with(p, pos=len(self.progs[i]), inj=(), blocked_send=None, blocked_recv=None, blocked_coll=None)
            return _gs(st, procs=_procs(st, i, np_), aborted=tuple(sorted(set(st.aborted) | {rank})),
                       errors=_add_error(st.errors, f"abort: rank {rank} ({op.why})"))
        if isinstance(op, Unknown):
            np_ = _with(p, pos=len(self.progs[i]), inj=(), blocked_send=None, blocked_recv=None, blocked_coll=None)
            return _gs(st, procs=_procs(st, i, np_), unknown=tuple(sorted(set(st.unknown) | {(rank, op.why)})))
        if isinstance(op, Send):
            key = (rank, p.nsend)
            dst = op.dst
            if callable(dst):
                dst = dst(_env(p))
            elif isinstance(dst, str):
                v = _env(p)[dst]
                dst = v.src if isinstance(v, Meta) else int(v)
            m = Msg(key, rank, dst, op.tag, op.bytes, op.comm, op.data)
            mode = getattr(op, "mode", "std")
            rendez = mode == "ssend" or (mode != "bsend" and B is not None and op.bytes > B)
            reqs = p.reqs + ((op.req, ("send", key)),) if op.req else p.reqs
            blocked = key if (op.req is None and rendez) else None
            csend = st.completed_send if rendez else st.completed_send | {key}
            np_ = self.advance(p, nsend=p.nsend + 1, blocked_send=blocked, reqs=reqs)
            errors, ready = st.errors, st.ready
            if mode == "rsend":
                # MPI 4.1 §3.4: a ready send may start only if its matching receive is already
                # posted. Which receive takes it is decided later, so the receives posted now that
                # accept it are kept with it: the error is recorded here if there is none, at the
                # match if another receive takes it, once all of them have taken other messages,
                # or at the end of the path if nothing takes it (docs/model.md §5). The model
                # says the same of the receive the send is matched to: the edge
                # issue(r) -> issue(s) and horizon rule 7
                posted = frozenset(r.key for r in st.posted if _accepts(r, m))
                if posted:
                    ready = tuple(sorted(ready + ((key, posted),)))
                else:
                    errors = _add_error(errors, _ready_error(key))
            return _gs(st, procs=_procs(st, i, np_), pending=st.pending + (m,), completed_send=csend, errors=errors,
                       sent=st.sent + ((key, dst, op.tag, op.comm),), ready=ready)
        if isinstance(op, Recv):
            key = (rank, p.nrecv)
            post = Post(key, rank, op.src, op.tag, op.cap, op.comm, op.req is None, op.into)
            reqs = p.reqs + ((op.req, ("recv", key)),) if op.req else p.reqs
            np_ = self.advance(p, nrecv=p.nrecv + 1, blocked_recv=key if op.req is None else None, reqs=reqs)
            return _gs(st, procs=_procs(st, i, np_), posted=st.posted + (post,))
        if isinstance(op, Test):
            # a test that fails, always possible (no progress guarantee): a step of its rank alone
            return _gs(st, procs=_procs(st, i, self.advance(p)))
        if isinstance(op, Wait):
            live = dict(p.reqs)
            if op.req not in live:      # MPI_REQUEST_NULL (already completed): returns at once
                return _gs(st, procs=_procs(st, i, self.advance(p)))
            kind, key = live[op.req]
            if not self._req_complete(st, p, kind, key):
                return None
            nst = self._finish_request(st, i, p, op.req, kind, key, None, None)
            return _gs(nst, marks=nst.marks + (op.mark,)) if getattr(op, "mark", None) else nst
        if isinstance(op, Coll):
            comm = op.comm
            k = _ncoll(p, comm)
            c = CollIssue(rank, comm, k, op.name, op.req, op.root)
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
                alone = self.probe_steps(st, i, True)
                if alone:
                    return alone, outs
            for r, m in matches:
                succs.append(self.match_step(st, r, m))
            for i in range(len(st.procs)):
                succs.extend(self.waitany_steps(st, i))
                succs.extend(self.probe_steps(st, i, False))
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
            succs.extend(self.probe_steps(st, i, True))
            succs.extend(self.probe_steps(st, i, False))
        return succs, outs
