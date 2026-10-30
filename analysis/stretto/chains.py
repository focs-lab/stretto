"""Chains: the threads of control of a process (docs/threads.md).

A trace of format version 3 made with the tracer's thread support (the init record without
"threads": 0) names the calling thread of every record of a thread other than the initialising
one ("tid", the tracer's chain id, 0 for the initialising thread) and carries happens-before
between the threads of a process as vector-clock deltas: a record's "after" lists [chain, seq]
pairs, each saying that a synchronisation point of that chain located after its record seq
precedes this record's call. Under MPI_THREAD_MULTIPLE (init "issue": 1) the tracer also
serialises the posting of point-to-point operations and numbers it ("iseq"), so that the
observed matching can be reconstructed in the order in which MPI saw the calls.

A rank with records of several chains is split here: each chain becomes a sequence of records
of its own, whose id plays the role a rank plays in program-order structures (Trace.ranks, cuts,
record indexes), while the process (Trace.proc) keeps the envelope role. The main chain of a
rank keeps the rank's id, so a rank with one chain is today's rank in every structure, and a
trace in which every rank has one chain loads exactly as before.
"""
from __future__ import annotations
from array import array
from bisect import bisect_left, bisect_right
from typing import Dict, List, Optional, Tuple

# side records of the tracer's thread support: no calls of the program
THREAD_SIDE = ("chain", "task", "overlap", "unordered", "locks")


class Chain(int):
    """The id of a chain other than a rank's main one: an int for every structure, printed as
    rank.chain (the tracer's chain id) in reports."""


    def __new__(cls, value: int, label: str):
        o = int.__new__(cls, value)
        o.label = label
        return o

    def __repr__(self) -> str:
        return self.label

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return self.label if not spec else int.__format__(int(self), spec)

    def __reduce__(self):
        return (Chain, (int(self), self.label))


class HB:
    """Happens-before between the records of the chains of a process: record a precedes
    record b if a's call has returned before b's call starts, by program order or through
    recorded synchronisation. pos[(B, A)][ib] is the last record of chain A that precedes
    record ib of chain B (-1 if none); edges are the recorded synchronisation edges
    complete(A, ia) -> issue(B, ib), and out[A] the same by source, sorted."""

    def __init__(self):
        self.pos: Dict[Tuple[int, int], array] = {}
        self.edges: List[Tuple[int, int, int, int]] = []
        self.out: Dict[int, List[Tuple[int, int, int]]] = {}
        # (A, ia, B, ib) -> (kc, ka): the ordinals, among their chains' synchronisation calls, of the
        # release on A and the acquire on B (trace entries of four elements), to order the
        # synchronisation points between two records of a chain
        self.kinfo: Dict[Tuple[int, int, int, int], Tuple[int, int]] = {}
        # the same, every pair: the hard happens-before (stretto.locks) keeps for one edge the earlier
        # points of the gap with their own ordinals
        self.kall: Dict[Tuple[int, int, int, int], List[Tuple[int, int]]] = {}

    def last_before(self, b_chain: int, b_idx: int, a_chain: int) -> int:
        if a_chain == b_chain:
            return b_idx - 1
        arr = self.pos.get((b_chain, a_chain))
        if arr is None or b_idx >= len(arr):
            return -1 if arr is None else (arr[-1] if len(arr) else -1)
        return arr[b_idx]

    def before(self, a_chain: int, a_idx: int, b_chain: int, b_idx: int) -> bool:
        """Does record a return before record b is called?"""
        if a_chain == b_chain:
            return a_idx < b_idx
        return self.last_before(b_chain, b_idx, a_chain) >= a_idx

    def first_after(self, a_chain: int, a_idx: int, b_chain: int, n: int) -> int:
        """The first record of chain b_chain (n records) that record a precedes; n if none."""
        if a_chain == b_chain:
            return a_idx + 1
        arr = self.pos.get((b_chain, a_chain))
        if arr is None:
            return n
        return bisect_left(arr, a_idx)


def split(t, folded: Dict[int, List[dict]], make_rec) -> Dict[int, list]:
    """Fill t.ranks from the folded records of each rank, one sequence per chain, and return
    each rank's records in file order. make_rec(chain, idx, raw) builds a record."""
    nps = [r.get("np", 0) for recs in folded.values() for r in recs if r.get("op") == "init"]
    next_id = max([max(nps, default=0)] + [rank + 1 for rank in folded])
    proc_recs: Dict[int, list] = {}
    for rank, recs in folded.items():
        side = [r for r in recs if r.get("op") in THREAD_SIDE]
        if side:
            recs = [r for r in recs if r.get("op") not in THREAD_SIDE]
            folded[rank] = recs
        init = next((r for r in recs if r.get("op") == "init"), {})
        hb_on = init.get("version", 1) >= 3 and init.get("threads", 1) != 0
        if not hb_on:
            t.ranks[rank] = proc_recs[rank] = [make_rec(rank, i, r) for i, r in enumerate(recs)]
            # under MPI_THREAD_MULTIPLE every record names its thread (a site table overflow aside);
            # below it (since 22 Oct 2026) those of a thread other than the initialising one do, and
            # a record without a thread is the initialising thread's
            tids = {r.get("tid") for r in recs if r["op"] != "overflow"}
            if max((r.get("thread", 0) for r in recs if r["op"] == "init"), default=0) >= 3:
                tids.discard(None)
            t.threads[rank] = len(tids) or 1
            continue
        for r in side:
            op = r.get("op")
            if op == "chain":
                t.chain_info[(rank, r.get("id"))] = r
            elif op == "task" and r.get("what") == "detach":
                t.thread_limits.append(("threads-tasks", f"rank {rank} created a detached OpenMP task, whose "
                                        "completion the tracer does not follow"))
            elif op == "task":
                # since 30 Oct 2026 explicit tasks are chains (kind "task"); the record names a
                # call in a task the tracer does not follow (a taskloop's), or any task before
                t.thread_limits.append(("threads-tasks", f"rank {rank} called MPI inside an explicit OpenMP task: "
                                        "its calls are not attributed to a chain of their own"))
            elif op == "overlap":
                t.thread_overlap.append(rank)
            elif op == "unordered":
                t.thread_limits.append(("threads-unordered", f"rank {rank} called {r.get('call', '?')}, whose posting "
                                        "the tracer does not order"))
        ids = sorted({r.get("tid") or 0 for r in recs if r["op"] != "overflow"})
        t.threads[rank] = len(ids) or 1
        if len(ids) <= 1:
            t.ranks[rank] = proc_recs[rank] = [make_rec(rank, i, r) for i, r in enumerate(recs)]
            continue
        t.multi = True
        if t.hb is None:
            t.hb = HB()
        cmap: Dict[int, int] = {}
        for tid in ids:
            if tid == 0:
                cmap[0] = rank
            else:
                cmap[tid] = Chain(next_id, f"{rank}.{tid}")
                next_id += 1
        t.chains[rank] = [cmap[tid] for tid in ids]
        for tid, ch in cmap.items():
            t.proc[ch] = rank
            info = t.chain_info.get((rank, tid))
            t.chain_name[ch] = info.get("name", str(tid)) if info else ("0" if tid == 0 else str(tid))
            t.ranks[ch] = []
        out = []
        seqs: Dict[int, List[int]] = {ch: [] for ch in cmap.values()}
        for r in recs:
            ch = cmap.get(r.get("tid") or 0, rank)
            rec = make_rec(ch, len(t.ranks[ch]), r)
            t.ranks[ch].append(rec)
            out.append(rec)
            q = r.get("seq")
            if q is not None:
                seqs[ch].append(q)
            else:
                seqs[ch].append(seqs[ch][-1] if seqs[ch] else -1)
        proc_recs[rank] = out
        # happens-before from the deltas
        _clocks(t.hb, t, cmap, seqs, lambda raw: raw.get("after", ()))
        if init.get("lockorder"):
            # the hard happens-before: hafter where a record has it (stretto.trace folds a begin's)
            if t.hb_hard is None:
                t.hb_hard = HB()
            why = _critical_sections(t, rank, cmap, side)
            if why is None:
                _clocks(t.hb_hard, t, cmap, seqs, lambda raw: raw.get("hafter", raw.get("after", ())))
            else:
                t.lock_limits.append((rank, why))
                t.locks.pop(rank, None)
                _clocks(t.hb_hard, t, cmap, seqs, lambda raw: raw.get("after", ()))
    for hb in (t.hb, t.hb_hard):
        if hb is not None:
            for a in list(hb.out):
                hb.out[a].sort()
    for rank in proc_recs:
        t.proc.setdefault(rank, rank)
    return proc_recs


def _clocks(hb: HB, t, cmap: Dict[int, int], seqs: Dict[int, List[int]], entries) -> None:
    """Add the happens-before of one split rank to hb, from the deltas entries(raw) of each of
    its records."""
    for ch in cmap.values():
        vc: Dict[int, int] = {}
        lastk: Dict[int, int] = {}       # per predecessor chain, the ordinal of its latest release seen
        rows: Dict[int, array] = {}
        n = len(t.ranks[ch])
        for rec in t.ranks[ch]:
            for entry in entries(rec.raw) or ():
                try:
                    c, q = int(entry[0]), int(entry[1])
                    k = (int(entry[2]), int(entry[3])) if len(entry) >= 4 else None
                except (TypeError, ValueError, IndexError):
                    continue
                a = cmap.get(c)
                if a is None or a == ch:
                    continue
                ia = bisect_right(seqs[a], q) - 1
                later_k = k is not None and ia == vc.get(a, -1) and k[0] > lastk.get(a, -1)
                if ia < 0 or (ia <= vc.get(a, -1) and not later_k):
                    continue                 # nothing new (a later synchronisation point in the same gap is new)
                vc[a] = ia
                if a not in rows:
                    rows[a] = array("i", [-1]) * n
                key = (a, ia, ch, rec.idx)
                if key not in hb.kall:
                    hb.edges.append(key)
                    hb.out.setdefault(a, []).append((ia, ch, rec.idx))
                    hb.kall[key] = []
                if k is not None:
                    hb.kinfo[key] = k
                    hb.kall[key].append(k)
                    lastk[a] = k[0]
            for a, row in rows.items():
                row[rec.idx] = vc[a]
        for a, row in rows.items():
            hb.pos[(ch, a)] = row


def add_edges(t, rank: int, recs: list, edges) -> None:
    """Add to the happens-before of split rank `rank`, and to its hard half, the edges
    complete(a, ia) -> issue(b, ib) of `edges` that it does not imply yet, closed transitively:
    a request completed, tested or cancelled by another chain than the one that posted it
    (stretto.trace, premise P-T9 of docs/threads.md section 15). recs: the rank's records in
    file order, a linear extension of happens-before and of these edges (a record is written
    when its call returns, and a call names a request only after the call that created it has
    returned it)."""
    chains = t.chains[rank]
    for hb in (t.hb, t.hb_hard):
        if hb is None:
            continue
        new = sorted({e for e in edges if not hb.before(*e)})
        if not new:
            continue
        into: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
        for a, ia, b, ib in new:
            into.setdefault((b, ib), []).append((a, ia))
            key = (a, ia, b, ib)
            if key not in hb.kall:
                hb.edges.append(key)
                hb.out.setdefault(a, []).append((ia, b, ib))
                hb.kall[key] = []
        for a in {e[0] for e in new}:
            hb.out[a].sort()
        n = {ch: len(t.ranks[ch]) for ch in chains}
        old = {ch: [(a, hb.pos[(ch, a)]) for a in chains if (ch, a) in hb.pos] for ch in chains}
        rows = {(b, a): array("i", [-1]) * n[b] for b in chains for a in chains if a != b}
        vc = {ch: {a: -1 for a in chains if a != ch} for ch in chains}
        for rec in recs:
            ch, i = rec.rank, rec.idx
            if ch not in vc:
                continue
            v = vc[ch]
            srcs = [(a, arr[i]) for a, arr in old[ch] if i < len(arr) and arr[i] > v[a]]
            srcs += [(a, ia) for a, ia in into.get((ch, i), ()) if ia > v[a]]
            for a, ia in srcs:
                if ia <= v[a]:
                    continue
                v[a] = ia
                for c in chains:                 # what precedes (a, ia) precedes this record
                    if c != ch and c != a:
                        x = rows[(a, c)][ia]
                        if x > v[c]:
                            v[c] = x
            for a, x in v.items():
                rows[(ch, a)][i] = x
        for (b, a), row in rows.items():
            if any(x >= 0 for x in row):
                hb.pos[(b, a)] = row
            else:
                hb.pos.pop((b, a), None)


class CS:
    """A critical section of a mutual-exclusion lock of one process (docs/threads.md, section
    13): the n-th entered of lock lid, on chain. Its acquire and its release lie in gaps of the
    chain (acq, rel: the index of the chain's record before it, -1 for its start), at ordinals
    ka and kr among the chain's synchronisation operations. soft: the edge from critical section
    n-1 is not in the hard happens-before (both contain a record of their chain); empty: the
    section contains no record (acquire and release in one gap)."""
    __slots__ = ("proc", "lid", "n", "chain", "acq", "ka", "rel", "kr", "soft")

    def __init__(self, proc: int, lid: int, n: int):
        self.proc, self.lid, self.n = proc, lid, n
        self.chain = self.acq = self.ka = self.rel = self.kr = None
        self.soft = False

    @property
    def empty(self) -> bool:
        return self.rel == self.acq

    def __repr__(self) -> str:
        return f"cs{self.lid}.{self.n}@{self.chain}[{self.acq}:{self.ka}..{self.rel}:{self.kr}]{'s' if self.soft else ''}"


def _critical_sections(t, rank: int, cmap: Dict[int, int], side: List[dict]) -> Optional[str]:
    """Read the lock events of a split rank into t.locks[rank] (the locks with a soft edge);
    the reason the rank's lock order must be kept instead (its hard happens-before is then the
    full one), or None. A rank without a soft edge keeps nothing and loses nothing."""
    by: Dict[int, Dict[int, CS]] = {}
    bad: Dict[int, str] = {}             # per lock, what makes its critical sections unknown
    limits: List[str] = []               # what makes the rank's lock order unknown

    def event(ch: int, gap: int, ev) -> None:
        try:
            lid, n, k, typ = (int(x) for x in ev[:4])
        except (TypeError, ValueError):
            limits.append("a malformed lock event")
            return
        if lid == -2:
            limits.append("a thread synchronised with others after a lock operation and before its first record, "
                          "where the tracer records no edge from it")
            return
        if lid < 0:
            limits.append("a thread made more lock operations between two records than the tracer keeps")
            return
        cs = by.setdefault(lid, {}).setdefault(n, CS(rank, lid, n))
        if typ == 2:
            if cs.rel is not None or (cs.chain is not None and cs.chain != ch):
                bad.setdefault(lid, f"lock {lid}: critical section {n} released twice or by another thread")
            cs.chain, cs.rel, cs.kr = ch, gap, k
        else:
            if cs.acq is not None or (cs.chain is not None and cs.chain != ch):
                bad.setdefault(lid, f"lock {lid}: critical section {n} entered twice or by another thread")
            cs.chain, cs.acq, cs.ka, cs.soft = ch, gap, k, typ == 1

    for tid, ch in cmap.items():
        for rec in t.ranks[ch]:
            for ev in rec.raw.get("locks", ()) or ():
                event(ch, rec.idx - 1, ev)
    for r in side:
        if r.get("op") == "locks":
            ch = cmap.get(r.get("tid") or 0)
            if ch is None:
                limits.append("lock events of a thread without records")
                continue
            for ev in r.get("locks", ()) or ():
                event(ch, len(t.ranks[ch]) - 1, ev)
    out: Dict[int, list] = {}
    for lid, css in by.items():
        if not any(cs.soft for cs in css.values()):
            continue                         # every edge of the lock is in the hard happens-before
        if lid in bad:
            return bad[lid]
        n = max(css)
        for i in range(1, n + 1):
            cs = css.get(i)
            if cs is None or cs.acq is None or cs.rel is None:
                return (f"lock {lid}: critical section {i} is not recorded in full (a thread without records "
                        "took the lock, or it was still held at the end of the trace)")
            if (cs.rel, cs.kr) < (cs.acq, cs.ka):
                return f"lock {lid}: critical section {i} is released before it is entered"
            prev = css.get(i - 1)
            if prev is not None and prev.chain == cs.chain and (cs.acq, cs.ka) < (prev.rel, prev.kr):
                return f"lock {lid}: critical sections {i - 1} and {i} are nested (a recursive lock)"
        lst = [css[i] for i in range(1, n + 1)]
        # a critical section its chain enters right after leaving an empty one of the same lock (no
        # record between: a loop that polls lock-protected state until another thread changes it)
        # keeps its edge: what it does may depend on what it read (stretto.locks orders it)
        empty_at = {(cs.chain, cs.acq) for cs in lst if cs.empty}
        for cs in lst:
            if cs.soft and (cs.chain, cs.acq) in empty_at:
                cs.soft = False
        out[lid] = lst
    if out and limits:
        return limits[0]
    if out:
        t.locks[rank] = out
    return None


def physical_key(t, x) -> Tuple[int, int]:
    """The order in which MPI saw a point-to-point operation or probe of a split rank: its
    issue number under MPI_THREAD_MULTIPLE, else its place in the rank's file (below
    MULTIPLE calls never overlap, so the file order is the order of the calls)."""
    if x.iseq is not None:
        return (0, x.iseq)
    return (1, t.fpos.get((x.rank, x.idx), x.idx))


def concurrent(t, a_chain: int, a_idx: int, b_chain: int, b_idx: int) -> bool:
    """Two records of one process that neither precedes."""
    return a_chain != b_chain and not t.hb.before(a_chain, a_idx, b_chain, b_idx) \
        and not t.hb.before(b_chain, b_idx, a_chain, a_idx)
