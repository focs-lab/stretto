"""Read bounds on a trace (docs/firstread.md).

For a record k of a rank that delivers the result of a receive (the receive itself if it
blocks, the wait or test that completed it otherwise), the bound is the index of the first
later record of the rank that the program may issue after reading that result: every record
from k + 1 up to the bound is issued whatever the result was. Rule H1 of the horizon is the
bound k + 1, which is what ReadBounds gives; SiteMap computes bounds from the first-read map
of stretto.firstread and the call sites the tracer recorded; NeverReads is a program that
reads no result at all, such as the explorer's straight-line programs."""
from __future__ import annotations
import json
import os
from typing import Callable, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from .trace import Key, Recv, Trace


def _cidx(x) -> int:
    return x.complete_idx if x.complete_idx is not None else x.idx


class ReadBounds:
    """Rule H1: every result may be read at once."""

    def limit(self, t: Trace, rank: int, comp: int, post: Optional[int]) -> int:
        """The bound of the result that record comp delivers, for the receive posted at record
        post (None: the record's own results, such as a Waitany's index)."""
        return comp + 1

    def recv(self, t: Trace, r: Recv) -> int:
        """The bound of receive r: rule H1 for a receive that never completed in the trace,
        whose result a later test of its request could deliver anywhere."""
        c = _cidx(r)
        if not r.completed:
            return c + 1
        return max(c + 1, self.limit(t, r.rank, c, r.idx))

    def rule1(self, t: Trace, changed: Iterable[Key]) -> Dict[int, int]:
        """Rule 1 of the horizon under these bounds: per rank, the least bound of its changed
        receives."""
        out: Dict[int, int] = {}
        for rk in changed:
            r = t.recvs[rk]
            q = self.recv(t, r)
            out[r.rank] = min(out.get(r.rank, q), q)
        return out

    def extends(self, t: Trace, changed: Iterable[Key]) -> bool:
        """Does some changed receive keep records after its completion?"""
        return any(self.recv(t, t.recvs[rk]) > _cidx(t.recvs[rk]) + 1 for rk in changed)


class NeverReads(ReadBounds):
    """A program that reads no result: every record of the rank is before the bound."""

    def limit(self, t: Trace, rank: int, comp: int, post: Optional[int]) -> int:
        return len(t.ranks[rank])


class Table(ReadBounds):
    """Bounds given per (rank, record that delivers the result); H1 elsewhere."""

    def __init__(self, bounds: Dict[Tuple[int, int], int]):
        self.bounds = dict(bounds)

    def limit(self, t: Trace, rank: int, comp: int, post: Optional[int]) -> int:
        return self.bounds.get((rank, comp), comp + 1)


# the MPI calls that can produce a record of each kind (tracer/mpitrace.c)
_SENDS = {"MPI_Send", "MPI_Ssend", "MPI_Bsend", "MPI_Rsend", "MPI_Isend", "MPI_Issend", "MPI_Ibsend", "MPI_Irsend",
          "MPI_Start", "MPI_Startall", "MPI_Sendrecv", "MPI_Sendrecv_replace"}
_WAITS = {"MPI_Wait", "MPI_Waitall", "MPI_Waitany", "MPI_Waitsome", "MPI_Test", "MPI_Testall", "MPI_Testany",
          "MPI_Testsome", "MPI_Request_get_status", "MPI_Sendrecv", "MPI_Sendrecv_replace"}
_COLLS = {"MPI_" + n for n in ("Barrier", "Bcast", "Reduce", "Allreduce", "Gather", "Gatherv", "Scatter", "Scatterv",
                               "Allgather", "Allgatherv", "Alltoall", "Alltoallv", "Alltoallw", "Reduce_scatter",
                               "Reduce_scatter_block", "Scan", "Exscan")}
_COLLS |= {"MPI_I" + n[4:].lower() for n in _COLLS}
FAMILIES: Dict[str, FrozenSet[str]] = {k: frozenset(v) for k, v in {
    "send": _SENDS,
    "recv": {"MPI_Recv", "MPI_Mrecv", "MPI_Sendrecv", "MPI_Sendrecv_replace"},
    "irecv": {"MPI_Irecv", "MPI_Imrecv", "MPI_Start", "MPI_Startall"},
    "wait": _WAITS,
    "probe": {"MPI_Probe", "MPI_Iprobe", "MPI_Mprobe", "MPI_Improbe"},
    "coll": _COLLS,
    "finalize": {"MPI_Finalize"},
    "init": {"MPI_Init", "MPI_Init_thread"},
    "abort": {"MPI_Abort"},
    "cancel": {"MPI_Cancel"},
    "reqfree": {"MPI_Request_free"},
    "bufattach": {"MPI_Buffer_attach"},
    "bufdetach": {"MPI_Buffer_detach"},
}.items()}


def family(rec) -> Optional[FrozenSet[str]]:
    """The MPI calls that can have written record rec; None: any."""
    op = rec.op
    if op == "blocked":
        op = (rec.raw or {}).get("call")
    return FAMILIES.get(op)


Resolver = Callable[[Trace, int, int], Optional[Tuple[str, str, int]]]


def addr2line_resolver(objdir: Optional[str] = None) -> Resolver:
    """(function, file name, line) of a record's call site with addr2line (stretto.sites).
    objdir: where to look for an object whose recorded path no longer exists (a run made in a
    container under another mount point), by its file name."""
    from .sites import symbolize

    def resolve(t: Trace, rank: int, idx: int) -> Optional[Tuple[str, str, int]]:
        s = t.site_record(rank, idx)
        if s is None:
            return None
        obj = s.get("obj", "")
        if not os.path.exists(obj) and objdir is not None and os.path.exists(os.path.join(objdir, os.path.basename(obj))):
            obj = os.path.join(objdir, os.path.basename(obj))
        where = symbolize(obj, s.get("addr", 0), s.get("base", 0))
        if " at " not in where:
            return None
        fn, loc = where.rsplit(" at ", 1)
        path, _, line = loc.rpartition(":")
        try:
            return fn, os.path.basename(path), int(line)
        except ValueError:
            return None
    return resolve


class SiteMap(ReadBounds):
    """Bounds from a first-read map: a record after k is certified if its call site matches
    static sites (same function, file name and line, a call of its kind) and every one of them
    is free for every pair of static sites the posting and the delivering records can be."""

    def __init__(self, rmap: dict, resolve: Optional[Resolver] = None):
        if not str(rmap.get("format", "")).startswith("stretto-firstread"):
            raise ValueError("not a first-read map (stretto.firstread)")
        self.resolve = resolve or addr2line_resolver()
        self.call: Dict[int, str] = {}
        self.at: Dict[Tuple[str, str, int], List[int]] = {}
        for s in rmap["sites"]:
            self.call[s["id"]] = s["call"]
            self.at.setdefault((s["function"], os.path.basename(s["file"]), s["line"]), []).append(s["id"])
        self.free: Dict[Tuple[Optional[int], int], FrozenSet[int]] = {
            (r["post"], r["complete"]): frozenset(r["free"]) for r in rmap["results"]}
        self._cands: Dict[Tuple[int, int, Optional[FrozenSet[str]]], Tuple[int, ...]] = {}
        self._limits: Dict[Tuple[int, int, Optional[int]], int] = {}

    @classmethod
    def load(cls, path: str, resolve: Optional[Resolver] = None) -> "SiteMap":
        with open(path) as fh:
            return cls(json.load(fh), resolve)

    def candidates(self, t: Trace, rank: int, idx: int, fam: Optional[FrozenSet[str]] = None) -> Tuple[int, ...]:
        """The static sites record idx of rank may come from (of the calls fam, or of the
        record's own kind)."""
        if fam is None:
            fam = family(t.ranks[rank][idx])
        key = (rank, idx, fam)
        c = self._cands.get(key)
        if c is None:
            where = self.resolve(t, rank, idx)
            ids = self.at.get(where, []) if where is not None else []
            c = self._cands[key] = tuple(i for i in ids if fam is None or self.call[i] in fam)
        return c

    def limit(self, t: Trace, rank: int, comp: int, post: Optional[int]) -> int:
        key = (rank, comp, post)
        q = self._limits.get(key)
        if q is None:
            q = self._limits[key] = self._limit(t, rank, comp, post)
        return q

    def _limit(self, t: Trace, rank: int, comp: int, post: Optional[int]) -> int:
        cs = self.candidates(t, rank, comp)
        if post is None:
            ps: Tuple[Optional[int], ...] = (None,)
        elif post == comp:
            ps = ()
        else:
            ps = self.candidates(t, rank, post)
            if not ps:
                return comp + 1
        if not cs:
            return comp + 1
        free: Optional[FrozenSet[int]] = None
        for c in cs:
            for p in (ps if post != comp else (c,)):
                f = self.free.get((p, c))
                if f is None:
                    return comp + 1
                free = f if free is None else free & f
        recs = t.ranks[rank]
        for e in range(comp + 1, len(recs)):
            es = self.candidates(t, rank, e)
            if not es or not free.issuperset(es):
                return e
        return len(recs)
