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

from .trace import Key, Recv, Send, Trace
from .types import compatible


def _cidx(x) -> int:
    return x.complete_idx if x.complete_idx is not None else x.idx


def erroneous(r: Recv, s: Send) -> Optional[str]:
    """Is receive r taking the message of send s an MPI error, and which: "truncate" when the
    message is longer than the receive (MPI_ERR_TRUNCATE), "type" when the type signatures
    do not match (as report.classify decides 1a); None when it is not."""
    if s.bytes > r.capacity:
        return "truncate"
    if compatible(s.tsig, s.count, r.tsig, r.count) is False:
        return "type"
    return None


class ReadBounds:
    """Rule H1: every result may be read at once."""

    def limit(self, t: Trace, rank: int, comp: int, post: Optional[int]) -> int:
        """The bound of the result that record comp delivers, for the receive posted at record
        post (None: the record's own results, such as a Waitany's index)."""
        return comp + 1

    def recv(self, t: Trace, r: Recv, sk: Optional[Key]) -> int:
        """The bound of receive r when it takes the message of send sk (None: not known). Rule
        H1 for a receive that never completed in the trace, whose result a later test of its
        request could deliver anywhere, and for a message that r cannot take without an MPI
        error (erroneous): the call that completes r then returns an error code, which the
        program may test at once, and with the default error handler it aborts there."""
        c = _cidx(r)
        s = t.sends.get(sk) if sk is not None else None
        if not r.completed or s is None or erroneous(r, s):
            return c + 1
        return max(c + 1, self.limit(t, r.rank, c, r.idx))

    def rule1(self, t: Trace, changed: Iterable[Key], M) -> Dict[int, int]:
        """Rule 1 of the horizon under these bounds, for the matching M (receive -> send):
        per rank, the least bound of its changed receives."""
        out: Dict[int, int] = {}
        for rk in changed:
            r = t.recvs[rk]
            q = self.recv(t, r, M.get(rk))
            out[r.rank] = min(out.get(r.rank, q), q)
        return out

    def extends(self, t: Trace, changed: Iterable[Key], M) -> bool:
        """Does some changed receive keep records after its completion under the matching M?"""
        return any(self.recv(t, t.recvs[rk], M.get(rk)) > _cidx(t.recvs[rk]) + 1 for rk in changed)


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


class MapMismatch(ValueError):
    """The first-read map was not made from the sources the traced program was built from."""


def sha256_of(path: str) -> str:
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def check_sources(rmap: dict, t: Trace, objdir: Optional[str] = None) -> int:
    """Refuse (MapMismatch) a first-read map that does not belong to the traced program. The map
    names its sites by function, file name and line, so a map made from edited sources, or from
    another tree, would put a site's bounds on another call and widen the horizon unsoundly.
    The map records the SHA-256 of every source its sites come from (sources); every source the
    trace's call sites lie in (addr2line on the traced objects, as the bounds are found) that
    the map has must hash the same, at the path the program names or, failing that, at the
    path the map recorded. A map without hashes is refused, and so is one that no call site of
    the trace lies in. Returns how many sources were checked."""
    from .sites import symbolize
    srcs = rmap.get("sources")
    if not srcs:
        raise MapMismatch("the map records no hashes of its sources (it predates them): remake it with python3 -m "
                          "stretto.firstread, or accept it unchecked")
    by_name: Dict[str, List[Tuple[str, str]]] = {}
    for path, digest in srcs.items():
        by_name.setdefault(os.path.basename(path), []).append((path, digest))
    checked: Dict[str, bool] = {}
    # one call of addr2line per site, not per rank: the same call has another address in each
    # process, but the same offset in its object
    distinct = {}
    for s in t.sites.values():
        distinct.setdefault((s.get("obj", ""), s.get("addr", 0) - s.get("base", 0)), (s.get("addr", 0), s.get("base", 0)))
    for (obj, _), (addr, base) in sorted(distinct.items()):
        if not os.path.exists(obj) and objdir is not None and os.path.exists(os.path.join(objdir, os.path.basename(obj))):
            obj = os.path.join(objdir, os.path.basename(obj))
        where = symbolize(obj, addr, base)
        if " at " not in where:
            continue
        path = where.rsplit(" at ", 1)[1].rpartition(":")[0]
        cands = by_name.get(os.path.basename(path))
        if not cands or path in checked:
            continue
        f = path if os.path.isfile(path) else next((p for p, _ in cands if os.path.isfile(p)), None)
        if f is None:
            raise MapMismatch(f"cannot read {path}, the source of traced call sites, to check the map against it")
        if sha256_of(f) not in {d for _, d in cands}:
            raise MapMismatch(f"{path}: the source the traced program was built from differs from the one the map was made from")
        checked[path] = True
    if not checked:
        raise MapMismatch("no call site of the trace lies in a source the map was made from: a map of another program "
                          "(or objects without debug information)")
    return len(checked)


class SiteMap(ReadBounds):
    """Bounds from a first-read map: a record after k is certified if its call site matches
    static sites (same function, file name and line, a call of its kind) and every one of them
    is free for every pair of static sites the posting and the delivering records can be.
    SiteMap.load checks, given the trace, that the map belongs to the traced program
    (check_sources)."""

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
    def load(cls, path: str, resolve: Optional[Resolver] = None, t: Optional[Trace] = None,
             objdir: Optional[str] = None, unchecked: bool = False) -> "SiteMap":
        """The map in file path, for the trace t: refused (MapMismatch) unless its sources are
        those of the traced program (check_sources), or unchecked is given (a map made before
        maps recorded their sources, accepted knowingly)."""
        with open(path) as fh:
            rmap = json.load(fh)
        if not unchecked:
            if t is None:
                raise MapMismatch("a first-read map is checked against the trace it is used with: give the trace")
            check_sources(rmap, t, objdir)
        return cls(rmap, resolve)

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


# ------------------------------------------------------------------ single-path witnesses

_TESTS = ("test", "testall", "testany", "testsome", "getstatus")


def _outside(rec) -> Optional[str]:
    """Why a record lies outside what the single-path explorer models (its result could
    differ in another execution, or it could block or synchronise there), or None."""
    op, raw = rec.op, rec.raw or {}
    if op == "blocked":
        op = raw.get("call")
    if op == "probe":
        return "a probe"
    if op == "wait":
        kind = raw.get("kind")
        if kind in _TESTS:
            return f"a {kind}"
        if kind in ("waitany", "waitsome"):
            return f"a {kind}"
        return None
    if op == "comm":
        return None if raw.get("name") in ("world", "self", "free") else "the creation of a communicator"
    if op in ("cancel", "abort", "unmodelled", "overflow"):
        return f"a record '{op}'"
    return None


def witness_respects(t: Trace, o, positions: Dict[int, List[int]], bounds: ReadBounds,
                     claim: Optional[str] = None) -> Optional[str]:
    """None if the explorer's execution o of dsl.program_from_trace(t, positions) is an
    execution of every program consistent with the trace that reads no result before its
    bound, as far as claim needs it; otherwise why it may not be (docs/firstread.md). For
    every rank, every record it issues in o, the one it stops at included, must precede the
    bound of each earlier receive whose message in o differs from the trace's: up to there the
    program has read only results equal to the trace's, so it issues the trace's calls, with
    the trace's payloads. A rank that finishes must end with MPI_Finalize, and no rank may pass
    a record the explorer does not model (_outside).

    The explorer goes on after an MPI error; a program need not: with the default error
    handler it aborts in the call that returns the error, and with MPI_ERRORS_RETURN it may
    test the error code right there. claim None stands for the end of o (a deadlock, orphan
    messages), which comes after every error of o, so o must have none: no error the explorer
    records, and no receive that takes, instead of the trace's message, one it errs on
    (erroneous: a truncation, a type mismatch). A claim "truncate" or "rsend-no-receive"
    stands for o up to its first error, which must be of that kind: o may have no error of
    another kind, and a rank is judged only up to the completion of its first receive that
    errs, which it has not passed when the first error happens."""
    if t.missing:
        return f"ranks without a trace: {t.missing}"
    if not o.stops:
        return "the witness has no positions"
    matched = dict(o.matching)
    errs: Dict[Key, str] = {}           # receives that take another message than the trace's and err on it
    for rk, sk in matched.items():
        r, s = t.recvs.get(rk), t.sends.get(sk)
        if r is not None and s is not None and sk != t.observed.get(rk):
            e = erroneous(r, s)
            if e is not None:
                errs[rk] = e
    # the explorer's own errors; its truncations are those of errs and those of receives that
    # take the trace's message, which the traced run went past already
    kinds = {k for k in o.error_kinds if k != "truncate"} | set(errs.values())
    if claim is None and kinds:
        return f"the witness meets an MPI error ({', '.join(sorted(kinds))}), where the program may abort or branch"
    if claim is not None and kinds - {claim}:
        return f"the witness meets an MPI error other than {claim} ({', '.join(sorted(kinds - {claim}))})"
    caps: Dict[int, int] = {}
    for rk in errs:
        r = t.recvs[rk]
        if r.completed:
            caps[r.rank] = min(caps.get(r.rank, _cidx(r)), _cidx(r))
    for rank, pos, blocked in o.stops:
        where = positions.get(rank, [])
        recs = t.ranks[rank]
        finished = pos >= len(where) and not blocked
        if finished:
            last = len(recs) - 1
        elif blocked in ("send", "recv"):
            last = where[pos - 1]                  # issued, the process parked in it
        else:
            last = where[pos]                      # parked at a collective, or a wait that cannot complete
        if rank in caps and caps[rank] < last:
            last = caps[rank]                      # as far as the rank can be when the first error happens
        elif finished and (not recs or recs[-1].op != "finalize"):
            return f"rank {rank} finishes its recorded calls without MPI_Finalize"
        for rec in recs[:last + 1]:
            why = _outside(rec)
            if why:
                return f"rank {rank} passes {why} (record #{rec.idx})"
        for r in t.recvs_of(rank):
            if not r.completed or _cidx(r) >= last:
                continue                           # its result is not delivered before the rank stops
            sk = matched.get(r.key)
            if sk is None:
                return f"{r} completes without a match in the witness"
            if sk == t.observed.get(r.key):
                continue
            q = bounds.recv(t, r, sk)
            if last >= q:
                return f"rank {rank} issues record #{last}, at or after record #{q}, where it may read the result of {r}, which differs"
    return None
