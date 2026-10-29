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
THREAD_SIDE = ("chain", "task", "overlap", "unordered")


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
            elif op == "task":
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
        hb = t.hb
        for ch in cmap.values():
            vc: Dict[int, int] = {}
            lastk: Dict[int, int] = {}       # per predecessor chain, the ordinal of its latest release seen
            rows: Dict[int, array] = {}
            n = len(t.ranks[ch])
            for rec in t.ranks[ch]:
                for entry in rec.raw.get("after", ()) or ():
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
                    hb.edges.append((a, ia, ch, rec.idx))
                    if k is not None:
                        hb.kinfo[(a, ia, ch, rec.idx)] = k
                        lastk[a] = k[0]
                    hb.out.setdefault(a, []).append((ia, ch, rec.idx))
                for a, row in rows.items():
                    row[rec.idx] = vc[a]
            for a, row in rows.items():
                hb.pos[(ch, a)] = row
        for a in list(hb.out):
            hb.out[a].sort()
    for rank in proc_recs:
        t.proc.setdefault(rank, rank)
    return proc_recs


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
