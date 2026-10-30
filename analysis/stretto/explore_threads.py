"""Exhaustive explorer over small programs of several threads with locks: ground truth for the
analysis of several threads at L1 and L1+L (docs/threads.md, sections 3 and 13).

A program has processes; each process a main chain and worker chains. The main chain creates
the workers in order, runs its own operations, joins the workers in order and finalises. An
operation is a blocking send (dst, tag, bytes), a blocking receive (src or ANY, tag or ANY,
capacity), or a lock or unlock of one of the process's locks (critical sections are properly
nested and not recursive). Programs are straight-line, so every premise about shared state
(P-T1, P-T8) holds for them.

Semantics (B = eager threshold in bytes, None = infinite): a send is issued when its chain
reaches it and blocks its chain until matched if bytes > B; a receive is posted when its chain
reaches it and blocks its chain until matched. Matching follows the physical order of the
postings (premise P-T4): match(s, r) is enabled when s is pending, r posted and unmatched, they
are compatible, s is the earliest pending send of its process to r's process that r accepts (in
issue order, every chain of the sender together), and no receive of r's process posted earlier
and still unmatched accepts s. A lock is taken when free. The explorer enumerates every
interleaving (with memoisation).

simulate() runs one execution chosen by a random number generator and writes its trace as the
tracer does (tracer/mpitrace.c, threads section): records with tid, seq, iseq, after, hafter
and lock events, the vector clocks of chains and objects included, so that stretto.trace loads
it as a trace of format version 3.
"""
from __future__ import annotations
import random
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

ANY = -1
OpId = Tuple[int, int, int]           # (process, chain, index of the operation in the chain)


@dataclass(frozen=True)
class TOp:
    kind: str                          # send | recv | lock | unlock
    a: int = 0                         # dst | src | lock
    b: int = 0                         # tag
    c: int = 1                         # bytes | capacity


@dataclass
class TProgram:
    procs: List[List[List[TOp]]]       # procs[p][c]: chain c of process p (0 the main chain)

    def __str__(self) -> str:
        out = []
        for p, chains in enumerate(self.procs):
            for c, ops in enumerate(chains):
                out.append(f"p{p}.{c}: " + " ".join(_op_str(o) for o in ops))
        return "\n".join(out)


def _op_str(o: TOp) -> str:
    if o.kind == "send":
        return f"S({o.a},{o.b},{o.c})"
    if o.kind == "recv":
        return f"R({'*' if o.a == ANY else o.a},{'*' if o.b == ANY else o.b},{o.c})"
    return f"{'L' if o.kind == 'lock' else 'U'}{o.a}"


# ----------------------------------------------------------------------------- semantics

# a step of chain (p, c): its position counts the main chain's creates first and joins last
def _steps(prog: TProgram, p: int, c: int) -> int:
    n = len(prog.procs[p][c])
    if c == 0:
        return 2 * (len(prog.procs[p]) - 1) + n
    return n


def _step(prog: TProgram, p: int, c: int, i: int):
    """("create", k) | ("join", k) | ("op", index, TOp) of position i of chain (p, c)."""
    k = len(prog.procs[p]) - 1
    ops = prog.procs[p][c]
    if c == 0:
        if i < k:
            return ("create", i + 1)
        if i < k + len(ops):
            return ("op", i - k, ops[i - k])
        return ("join", i - k - len(ops) + 1)
    return ("op", i, ops[i])


@dataclass(frozen=True)
class _St:
    pos: Tuple[Tuple[int, ...], ...]               # per process, per chain: position
    started: Tuple[Tuple[bool, ...], ...]
    wait: Tuple[Tuple[Optional[OpId], ...], ...]   # per chain: the blocking operation it waits on
    pending: Tuple[Tuple[OpId, int, int, int, int], ...]   # (send, src proc, dst proc, tag, bytes), in issue order
    posted: Tuple[Tuple[OpId, int, int, int, int], ...]    # (recv, proc, src, tag, cap), in posting order
    matched: FrozenSet[Tuple[OpId, OpId]]
    held: Tuple[Tuple[int, ...], ...]              # per process, per lock: holding chain or -1


def _initial(prog: TProgram, nlocks: List[int]) -> _St:
    return _St(tuple(tuple(0 for _ in ch) for ch in prog.procs),
               tuple(tuple(c == 0 for c in range(len(ch))) for ch in prog.procs),
               tuple(tuple(None for _ in ch) for ch in prog.procs),
               (), (), frozenset(), tuple(tuple(-1 for _ in range(n)) for n in nlocks))


def _nlocks(prog: TProgram) -> List[int]:
    return [1 + max([o.a for ch in chains for o in ch if o.kind in ("lock", "unlock")], default=-1) for chains in prog.procs]


def _set(tt, i, j, v):
    row = list(tt[i])
    row[j] = v
    out = list(tt)
    out[i] = tuple(row)
    return tuple(out)


def _accepts(r, m) -> bool:
    _, rp, src, tag, _ = r
    _, sp, dp, mtag, _ = m
    return dp == rp and (src == ANY or src == sp) and (tag == ANY or tag == mtag)


def _transitions(prog: TProgram, st: _St, B: Optional[int]):
    """(label, next state) of every enabled transition. Labels: ("step", p, c, what) for a chain
    step, ("match", recv, send)."""
    out = []
    for p, chains in enumerate(prog.procs):
        for c in range(len(chains)):
            if not st.started[p][c] or st.wait[p][c] is not None:
                continue
            i = st.pos[p][c]
            if i >= _steps(prog, p, c):
                continue
            s = _step(prog, p, c, i)
            nxt = _set(st.pos, p, c, i + 1)
            if s[0] == "create":
                out.append((("step", p, c, s), _St(nxt, _set(st.started, p, s[1], True), st.wait, st.pending, st.posted,
                                                   st.matched, st.held)))
                continue
            if s[0] == "join":
                k = s[1]
                if st.pos[p][k] >= _steps(prog, p, k) and st.wait[p][k] is None:
                    out.append((("step", p, c, s), _St(nxt, st.started, st.wait, st.pending, st.posted, st.matched, st.held)))
                continue
            _, j, o = s
            oid = (p, c, j)
            if o.kind == "lock":
                if st.held[p][o.a] == -1:
                    out.append((("step", p, c, s), _St(nxt, st.started, st.wait, st.pending, st.posted, st.matched,
                                                       _set(st.held, p, o.a, c))))
            elif o.kind == "unlock":
                out.append((("step", p, c, s), _St(nxt, st.started, st.wait, st.pending, st.posted, st.matched,
                                                   _set(st.held, p, o.a, -1))))
            elif o.kind == "send":
                blocks = B is not None and o.c > B
                out.append((("step", p, c, s), _St(nxt, st.started, _set(st.wait, p, c, oid) if blocks else st.wait,
                                                   st.pending + ((oid, p, o.a, o.b, o.c),), st.posted, st.matched, st.held)))
            else:
                out.append((("step", p, c, s), _St(nxt, st.started, _set(st.wait, p, c, oid), st.pending,
                                                   st.posted + ((oid, p, o.a, o.b, o.c),), st.matched, st.held)))
    for ri, r in enumerate(st.posted):
        for mi, m in enumerate(st.pending):
            if not _accepts(r, m):
                continue
            if any(m2[1] == m[1] and m2[2] == m[2] and _accepts(r, m2) for m2 in st.pending[:mi]):
                continue
            if any(_accepts(r2, m) and r2[1] == r[1] for r2 in st.posted[:ri]):
                continue
            wait = st.wait
            for (q, ch_, j), who in ((r[0], "r"), (m[0], "s")):
                if wait[q][ch_] == (q, ch_, j):
                    wait = _set(wait, q, ch_, None)
            out.append((("match", r[0], m[0]), _St(st.pos, st.started, wait, st.pending[:mi] + st.pending[mi + 1:],
                                                    st.posted[:ri] + st.posted[ri + 1:], st.matched | {(r[0], m[0])},
                                                    st.held)))
    return out


def _done(prog: TProgram, st: _St) -> bool:
    return all(st.pos[p][c] >= _steps(prog, p, c) and st.wait[p][c] is None
               for p in range(len(prog.procs)) for c in range(len(prog.procs[p])))


class StateCap(RuntimeError):
    pass


def exists(prog: TProgram, B: Optional[int], pairs: Set[Tuple[OpId, OpId]], deadlock_of: Optional[Set[Tuple[int, int]]] = None,
           max_states: int = 200_000) -> bool:
    """Is there an execution at B whose matching contains every pair (receive, send) of pairs and,
    with deadlock_of, that ends in a deadlock in which every chain (process, chain) of
    deadlock_of has not finished?"""
    want_r = {r: s for r, s in pairs}
    st0 = _initial(prog, _nlocks(prog))
    seen = {st0}
    stack = [st0]
    while stack:
        st = stack.pop()
        if any(want_r.get(r, s) != s for r, s in st.matched):
            continue                          # a wanted receive took another message
        got = all(pr in st.matched for pr in pairs)
        nxt = _transitions(prog, st, B)
        if deadlock_of is None:
            if got:
                return True
        elif not nxt and got and not _done(prog, st):
            unfinished = {(p, c) for p in range(len(prog.procs)) for c in range(len(prog.procs[p]))
                          if st.pos[p][c] < _steps(prog, p, c) or st.wait[p][c] is not None}
            if deadlock_of <= unfinished:
                return True
        for _, s2 in nxt:
            if s2 not in seen:
                if len(seen) >= max_states:
                    raise StateCap(f"more than {max_states} states")
                seen.add(s2)
                stack.append(s2)
    return False


def deadlocks(prog: TProgram, B: Optional[int], max_states: int = 200_000) -> List[_St]:
    """Every reachable deadlocked state."""
    st0 = _initial(prog, _nlocks(prog))
    seen = {st0}
    stack = [st0]
    out = []
    while stack:
        st = stack.pop()
        nxt = _transitions(prog, st, B)
        if not nxt and not _done(prog, st):
            out.append(st)
        for _, s2 in nxt:
            if s2 not in seen:
                if len(seen) >= max_states:
                    raise StateCap(f"more than {max_states} states")
                seen.add(s2)
                stack.append(s2)
    return out


def lock_waits(prog: TProgram, st: _St) -> Set[Tuple[int, int]]:
    """Chains of a stuck state waiting at a lock."""
    out = set()
    for p, chains in enumerate(prog.procs):
        for c in range(len(chains)):
            if not st.started[p][c] or st.wait[p][c] is not None:
                continue
            i = st.pos[p][c]
            if i < _steps(prog, p, c):
                s = _step(prog, p, c, i)
                if s[0] == "op" and s[2].kind == "lock":
                    out.add((p, c))
    return out


# --------------------------------------------------------------------- one traced run

class _Chain:
    def __init__(self, name: str):
        self.name, self.id, self.k, self.nrec = name, -1, 0, 0
        self.vc: Dict[int, Tuple[int, int]] = {}
        self.hvc: Dict[int, Tuple[int, int]] = {}
        self.pend: Dict[int, Tuple[int, int, int]] = {}
        self.hpend: Dict[int, Tuple[int, int, int]] = {}
        self.hx: List[Tuple[int, int, int, int]] = []
        self.lk: List[list] = []
        self.deferred: List["_Lock"] = []
        self.lkseen = 0
        self.fin = self.hfin = None


class _Lock:
    def __init__(self):
        self.lid = 0
        self.ncs = 0
        self.vc: Dict[int, Tuple[int, int]] = {}
        self.hvc: Dict[int, Tuple[int, int]] = {}
        self.last_rec = False
        self.last_holder = None
        self.hold_nrec = self.hold_ka = 0
        self.hold_ev = -1
        self.dv = None


def _above(a, b) -> bool:
    return a[0] > b[0] or (a[0] == b[0] and a[1] > b[1])


_NONE = (-1, 0)


class _Tracer:
    """The tracer's clocks for one process (tracer/mpitrace.c, threads section), at the
    granularity of the explorer's steps."""

    def __init__(self, rank: int, np: int):
        self.rank, self.recs, self.seq, self.nch, self.nlock, self.iseq = rank, [], 0, 0, 0, 0
        self.main = _Chain("0")
        self.locks: Dict[int, _Lock] = {}

    # clocks
    def _pend(self, c: _Chain, hard: bool, i: int, v: Tuple[int, int], ka: int) -> None:
        if v[0] < 0 or i == c.id:
            return
        pend = c.hpend if hard else c.pend
        old = pend.get(i)
        if old is None or _above(v, old[:2]):
            if hard and old is not None and old[2] != ka:
                c.hx.append((i, old[0], old[1], old[2]))
            pend[i] = (v[0], v[1], ka)

    def _acq(self, c: _Chain, src: Dict[int, Tuple[int, int]], ka: int, hard: bool) -> None:
        dst = c.hvc if hard else c.vc
        for i, v in sorted(src.items()):
            if _above(v, dst.get(i, _NONE)):
                dst[i] = v
                self._pend(c, hard, i, v, ka)

    @staticmethod
    def _join(dst: Dict[int, Tuple[int, int]], src: Dict[int, Tuple[int, int]]) -> None:
        for i, v in src.items():
            if _above(v, dst.get(i, _NONE)):
                dst[i] = v

    def _flush_hard(self, c: _Chain) -> None:
        for s in c.deferred:
            self._acq(c, s.dv, s.hold_ka, True)
            s.dv = None
            if s.hold_ev >= 0:
                c.lk[s.hold_ev][3] = 0
        c.deferred = []

    def sync_op(self, c: _Chain, lockop: bool = False) -> int:
        if c.deferred:
            self._flush_hard(c)
        if lockop:
            c.lkseen = max(c.lkseen, 1)
        elif c.lkseen == 1 and c.id < 0:
            c.lkseen = 2
            c.lk.append([-2, 0, 0, 0])
        c.k += 1
        if c.id >= 0:
            c.vc[c.id] = (c.vc.get(c.id, _NONE)[0], c.k)
            c.hvc[c.id] = (c.hvc.get(c.id, _NONE)[0], c.k)
        return c.k

    # synchronisation
    def create(self, parent: _Chain, child: _Chain) -> None:
        self.sync_op(parent)
        child.vc, child.hvc = dict(parent.vc), dict(parent.hvc)
        for i, v in sorted(parent.vc.items()):
            self._pend(child, False, i, v, 0)
        for i, v in sorted(parent.hvc.items()):
            self._pend(child, True, i, v, 0)

    def end(self, c: _Chain) -> None:
        self.sync_op(c)
        if c.deferred:
            self._flush_hard(c)
        if c.lk and c.id >= 0:
            self.recs.append({"r": self.rank, "seq": self._seq(), **({"tid": c.id} if c.id else {}),
                              "op": "locks", "locks": [list(e) for e in c.lk]})
        c.lk = []
        c.fin, c.hfin = dict(c.vc), dict(c.hvc)

    def join(self, c: _Chain, child: _Chain) -> None:
        ka = self.sync_op(c)
        self._acq(c, child.fin, ka, False)
        self._acq(c, child.hfin, ka, True)

    def lock(self, c: _Chain, key: int) -> None:
        ka = self.sync_op(c, True)
        s = self.locks.setdefault(key, _Lock())
        if not s.lid:
            self.nlock += 1
            s.lid = self.nlock
        s.ncs += 1
        defer = s.last_rec and s.last_holder is not c and len(c.deferred) < 8
        self._acq(c, s.vc, ka, False)
        if defer:
            s.dv = dict(s.hvc)
        else:
            self._acq(c, s.hvc, ka, True)
        s.hold_nrec, s.hold_ka = c.nrec, ka
        c.lk.append([s.lid, s.ncs, ka, 1 if defer else 0])
        if defer:
            s.hold_ev = len(c.lk) - 1
            c.deferred.append(s)

    def unlock(self, c: _Chain, key: int) -> None:
        k = self.sync_op(c, True)
        s = self.locks[key]
        s.last_rec = c.nrec > s.hold_nrec
        s.last_holder = c
        self._join(s.vc, c.vc)
        s.hvc = dict(c.hvc)
        c.lk.append([s.lid, s.ncs, k, 2])

    # records
    def _seq(self) -> int:
        q = self.seq
        self.seq += 1
        return q

    def emit(self, c: _Chain, body: dict, iseq: Optional[int] = None) -> None:
        if c.id < 0:
            c.id = self.nch
            self.nch += 1
            if c.id:
                self.recs.append({"r": self.rank, "seq": self._seq(), "op": "chain", "id": c.id, "name": c.name,
                                  "parent": 0, "kind": "pthread"})
        seq = self._seq()
        rec = {"r": self.rank, "seq": seq}
        if c.id:
            rec["tid"] = c.id
        for s in c.deferred:
            s.dv = None
        c.deferred = []
        after = [[i, v[0], v[1], v[2]] for i, v in sorted(c.pend.items()) if i != c.id]
        hafter = [list(x) for x in c.hx] + [[i, v[0], v[1], v[2]] for i, v in sorted(c.hpend.items()) if i != c.id]
        if after:
            rec["after"] = after
        if hafter != after:
            rec["hafter"] = hafter
        if c.lk:
            rec["locks"] = [list(e) for e in c.lk]
        c.pend, c.hpend, c.hx, c.lk = {}, {}, [], []
        c.nrec += 1
        c.vc[c.id] = (seq, c.k)
        c.hvc[c.id] = (seq, c.k)
        rec.update(body)
        if iseq is not None:
            rec["iseq"] = iseq
        self.recs.append(rec)


def simulate(prog: TProgram, B: Optional[int], rng: random.Random, max_steps: int = 10_000):
    """One execution at B, chosen by rng, written as the tracer would: (raw records per rank, the
    operation of every record as (rank, record position in its chain's records) -> OpId, the
    matching (recv OpId -> send OpId)); None if the execution deadlocks or a message is longer than its receive."""
    np_ = len(prog.procs)
    tr = [_Tracer(p, np_) for p in range(np_)]
    chains = [[tr[p].main] + [_Chain(f"0.{k}") for k in range(len(prog.procs[p]) - 1)] for p in range(np_)]
    nrec = [[0] * len(prog.procs[p]) for p in range(np_)]
    where: Dict[Tuple[int, int, int], OpId] = {}           # (process, chain, its record ordinal) -> op
    posted_iseq: Dict[OpId, int] = {}
    for p in range(np_):
        tr[p].emit(chains[p][0], {"op": "init", "np": np_, "version": 3, "thread": 3, "issue": 1, "lockorder": 1})
        nrec[p][0] += 1
    st = _initial(prog, _nlocks(prog))
    matching: Dict[OpId, OpId] = {}

    def record(oid: OpId) -> None:
        p, c, j = oid
        o = prog.procs[p][c][j]
        if o.kind == "send":
            body = {"op": "send", "mode": "std", "dst": o.a, "tag": o.b, "comm": 0, "count": o.c, "tsize": 1, "tsig": "MPI_BYTE"}
        else:
            s = matching[oid]
            so = prog.procs[s[0]][s[1]][s[2]]
            body = {"op": "recv", "src": max(o.a, 0), "tag": max(o.b, 0), "comm": 0, "count": o.c, "tsize": 1,
                    "tsig": "MPI_BYTE", "msrc": s[0], "mtag": so.b, "mcount": min(so.c, o.c)}
            if o.a == ANY:
                body["anysrc"] = 1
            if o.b == ANY:
                body["anytag"] = 1
        tr[p].emit(chains[p][c], body, posted_iseq[oid])
        where[(p, c, nrec[p][c])] = oid
        nrec[p][c] += 1

    for _ in range(max_steps):
        nxt = _transitions(prog, st, B)
        if not nxt:
            break
        label, st2 = rng.choice(nxt)
        if label[0] == "match":
            _, r, s = label
            if prog.procs[s[0]][s[1]][s[2]].c > prog.procs[r[0]][r[1]][r[2]].c:
                return None                                  # MPI_ERR_TRUNCATE: the run does not finish
            matching[r] = s
            record(r)                                        # the receive returns
            sp, sc, sj = s
            if B is not None and prog.procs[sp][sc][sj].c > B:
                record(s)                                    # a rendezvous send returns at its match
        else:
            _, p, c, what = label
            t, ch = tr[p], chains[p][c]
            if what[0] == "create":
                t.create(ch, chains[p][what[1]])
            elif what[0] == "join":
                t.join(ch, chains[p][what[1]])
            else:
                _, j, o = what
                if o.kind == "lock":
                    t.lock(ch, o.a)
                elif o.kind == "unlock":
                    t.unlock(ch, o.a)
                else:
                    posted_iseq[(p, c, j)] = t.iseq
                    t.iseq += 1
                    if o.kind == "send" and not (B is not None and o.c > B):
                        record((p, c, j))                    # an eager send returns at once
            # a worker that has made its last step ends (its end is a release, which the join takes)
            if c > 0 and st2.pos[p][c] >= _steps(prog, p, c) and st2.wait[p][c] is None:
                t.end(ch)
        st = st2
        # a worker whose last operation completed in a match ends too
        for p in range(np_):
            for c in range(1, len(prog.procs[p])):
                ch = chains[p][c]
                if ch.fin is None and st.pos[p][c] >= _steps(prog, p, c) and st.wait[p][c] is None and st.started[p][c]:
                    tr[p].end(ch)
    if not _done(prog, st):
        return None
    for p in range(np_):
        tr[p].emit(chains[p][0], {"op": "finalize"})
    return {p: tr[p].recs for p in range(np_)}, where, matching


# ------------------------------------------------------------------------ random programs

def random_program(rng: random.Random, nprocs: int = 2, max_workers: int = 2, max_msgs: int = 4,
                   p_lock: float = 0.6, p_any: float = 0.5) -> TProgram:
    """Messages between processes, each send and its receive placed on a random chain of their
    process in random order, then random runs of a worker's operations wrapped in a critical
    section of the process's lock 0 (receives with ANY source or tag with probability p_any)."""
    workers = [rng.randint(0, max_workers) for _ in range(nprocs)]
    ops: List[List[List[TOp]]] = [[[] for _ in range(1 + w)] for w in workers]
    n = rng.randint(2, max_msgs)
    for _ in range(n):
        s, d = rng.sample(range(nprocs), 2) if nprocs > 1 else (0, 0)
        tag = rng.randint(0, 1)
        size = rng.choice((1, 1, 8))
        cap = rng.choice((1, 8, 8))
        so = TOp("send", d, tag, size)
        ro = TOp("recv", ANY if rng.random() < p_any else s, ANY if rng.random() < p_any / 2 else tag, cap)
        for p, o in ((s, so), (d, ro)):
            chains = ops[p]
            c = rng.randrange(len(chains)) if len(chains) == 1 else rng.randrange(1, len(chains)) if rng.random() < 0.8 else 0
            lst = chains[c]
            lst.insert(rng.randint(0, len(lst)), o)
    for p, chains in enumerate(ops):
        for c in range(1, len(chains)):
            lst = chains[c]
            if lst and rng.random() < p_lock:
                i = rng.randrange(len(lst))
                j = rng.randint(i + 1, len(lst))
                chains[c] = lst[:i] + [TOp("lock", 0)] + lst[i:j] + [TOp("unlock", 0)] + lst[j:]
    return TProgram(ops)


def trace_of(prog: TProgram, B: Optional[int], rng: random.Random, tries: int = 20):
    """A finishing run's trace (stretto.trace.Trace), with where and the matching (simulate), or
    None if no run of the tries finishes."""
    from . import trace as T
    for _ in range(tries):
        run = simulate(prog, B, rng)
        if run is None:
            continue
        raw, where, matching = run
        t = T.Trace()
        T._build(t, {p: [dict(r) for r in recs] for p, recs in raw.items()})
        return t, where, matching
    return None


# --------------------------------------------------------------- checker against explorer

def _op_of(t, where, x) -> Optional[OpId]:
    proc = t.proc.get(x.rank, x.rank)
    c = 0 if x.rank == proc else int(t.chain_name[x.rank].split(".")[-1]) + 1
    return where.get((proc, c, x.idx))


def _chain_of(t, ch) -> Tuple[int, int]:
    proc = t.proc.get(ch, ch)
    return (proc, 0 if ch == proc else int(t.chain_name[ch].split(".")[-1]) + 1)


def compare(prog: TProgram, t, where, max_states: int = 200_000) -> List[dict]:
    """Every claim the analysis of trace t makes about prog, at B = 0 and inf, against the
    explorer: L1 receive alternatives of several threads (feasible.check), L1+L alternatives
    (stretto.locks: a feasible horizon of the hard happens-before with a schedule of its critical
    sections) and lock-order deadlocks. A claim is sound if some execution realises the judged
    pairs of its matching (for a deadlock: ends in a deadlock with the claimed chains stopped).
    Rows: level, item, B, claim, explorer, kind (agree | UNSOUND | incomplete | skip)."""
    from . import locks as L
    from .feasible import check, without
    from .threads import candidates
    rows = []

    def judged_pairs(tt, M, v):
        out = set()
        for rk, r in tt.recvs.items():
            if r.idx >= v.cut.get(r.rank, 10 ** 9) or rk in v.deprived:
                continue
            sk = M.get(rk)
            if sk is None or tt.sends[sk].idx >= v.cut.get(sk[0], 10 ** 9) and v.issued.get(sk[0]) != tt.sends[sk].idx:
                continue
            ro, so = _op_of(tt, where, r), _op_of(tt, where, tt.sends[sk])
            if ro is not None and so is not None:
                out.add((ro, so))
        return out

    def explorer(B, pairs, dead=None):
        try:
            return exists(prog, B, pairs, dead, max_states=max_states)
        except StateCap:
            return None

    tv = L.view(t) if L.applies(t) else None
    for level, tt in (("L1", t), ("L1+L", tv)):
        if tt is None:
            continue
        alts, _ = candidates(tt)
        for a in alts:
            for B in (0, None):
                if level == "L1+L":
                    st, v = L.judge(tt, a.M, a.changed, B, {tt.recvs[k].rank for k in a.changed})
                    claim = st is not None
                else:
                    v = check(tt, a.M, a.changed, B, "L1")
                    claim = v.feasible
                pairs = judged_pairs(tt, without(a.M, v.deprived), v) if v.cut is not None else set()
                pairs.add((_op_of(t, where, t.recvs[a.recv]), _op_of(t, where, t.sends[a.send])))
                ex = explorer(B, pairs) if claim else explorer(B, {pr for pr in pairs if pr[0] == _op_of(t, where, t.recvs[a.recv])})
                kind = "skip" if ex is None else "UNSOUND" if claim and not ex else "incomplete" if not claim and ex else "agree"
                rows.append({"level": level, "item": f"{a.kind} {a.describe(t)}", "B": "inf" if B is None else B,
                             "claim": claim, "explorer": ex, "kind": kind})
    if tv is not None:
        claimed = set()
        for y, x, D, Bs in L.lock_deadlocks(t):
            for B in Bs:
                dead = {_chain_of(t, ch) for ch in D}
                ex = explorer(B, set(), dead)
                claimed.add((B, frozenset(dead)))
                rows.append({"level": "L1+L", "item": f"deadlock {y} before {x}: {sorted(dead)}", "B": "inf" if B is None else B,
                             "claim": True, "explorer": ex, "kind": "skip" if ex is None else "agree" if ex else "UNSOUND"})
    # deadlocks the explorer finds with a thread waiting at a lock, which no claim names
    for B in (0, None):
        try:
            dls = deadlocks(prog, B, max_states=max_states)
        except StateCap:
            continue
        if any(lock_waits(prog, st) for st in dls) and not any(b == B for b, _ in (claimed if tv is not None else ())):
            rows.append({"level": "L1+L", "item": "a deadlock with a lock wait", "B": "inf" if B is None else B,
                         "claim": False, "explorer": True, "kind": "incomplete"})
    return rows
