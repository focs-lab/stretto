"""Trace model.

A trace is one JSONL file per rank (see tracer/mpitrace.c). Every record becomes one
`Rec` with two graph nodes, issue `(rank, idx, 'i')` and complete `(rank, idx, 'c')`.
Blocking operations complete at their own record; nonblocking ones complete at the
wait/test record that completed their request, or at an earlier MPI_Request_get_status
that found the request complete. An operation that never completed in
the observed run (a blocked call in a deadlocked run, an unwaited request) has
`completed == False` and no complete node takes part in delivery or rendezvous edges.

Identity of sends, receives and probes is by (rank, ordinal): the k-th send / receive /
probe issued by that rank. This is the numbering the DSL twins use. `widx` is the
wildcard ordinal used by the replay pin.

Communicators: the tracer records, at creation, the world ranks of the new communicator
in comm-rank order and the creation ordinal of that group ("gord"). The loader maps every
per-rank local id to a global id derived from (group, gord), which is consistent across
ranks by construction (creation is collective over the group). Ranks in send/recv records
are comm-local and are translated to world ranks at load time. Old traces (no `ranks`
field) keep their local ids and are assumed to use MPI_COMM_WORLD only.

Encoding of wildcards: new traces carry `anysrc`/`anytag`/`procnull` flags; old traces
encode ANY as -1.

Payloads: a trace made with STRETTO_PAYLOAD carries the packed contents of every send
(`Send.data`) and of what each rank gives to and gets from a collective (`Coll.din`,
`Coll.dout`), in hexadecimal, so that a rank can be rerun alone on the recorded messages.

Guard records (the tracer's STRETTO_GUARD=1) are kept as they are in `Trace.guards`: each
names the request of a pending operation whose buffer was misused, and the report turns them
into observed findings of family 3. They are side records, not events.
"""
from __future__ import annotations
import json
import os
from array import array
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ANY = -1
WORLD = 0

Key = Tuple[int, int]  # (rank, ordinal)


@dataclass(slots=True)
class Rec:
    rank: int
    idx: int
    op: str
    raw: Optional[dict]   # the record's fields; None once the loader has taken what it needs

    @property
    def issue(self):
        return (self.rank, self.idx, "i")

    @property
    def complete(self):
        return (self.rank, self.idx, "c")


@dataclass(slots=True)
class Send:
    key: Key
    rank: int
    idx: int
    dst: int
    tag: int
    comm: int
    count: int
    tsize: int
    blocking: bool
    mode: str
    req: Optional[int] = None
    complete_idx: Optional[int] = None
    completed: bool = True
    tsig: Optional[str] = None
    cancelled: bool = False          # a cancel record named its request: the message is withdrawn
    reserved_idx: Optional[int] = None   # idx of the Mprobe/Improbe at dst that removed it from the queue
    data: Optional[str] = None       # the packed message in hexadecimal, if the tracer recorded it (STRETTO_PAYLOAD)

    @property
    def bytes(self) -> int:
        return self.count * self.tsize

    def available_to(self, r: "Recv") -> bool:
        """Can r take s: s accepted, not cancelled, and not already removed from the queue by
        a matched probe that precedes r on r's rank."""
        return (r.accepts(self) and not self.cancelled
                and (self.reserved_idx is None or self.reserved_idx >= r.idx))

    @property
    def rendezvous_always(self) -> bool:
        return self.mode in ("ssend", "issend")

    @property
    def never_blocks(self) -> bool:
        return self.mode in ("bsend", "ibsend")

    @property
    def ready_mode(self) -> bool:
        return self.mode in ("rsend", "irsend")

    def issue_node(self):
        return (self.rank, self.idx, "i")

    def complete_node(self):
        if not self.completed:
            return None
        return (self.rank, self.complete_idx if self.complete_idx is not None else self.idx, "c")

    def __str__(self):
        return f"send{self.key}->{self.dst} tag={self.tag} {self.bytes}B"


@dataclass(slots=True)
class Recv:
    key: Key
    rank: int
    idx: int
    src: int  # ANY or a world rank
    tag: int  # ANY or a tag
    comm: int
    count: int
    tsize: int
    blocking: bool
    req: Optional[int] = None
    complete_idx: Optional[int] = None
    msrc: Optional[int] = None  # observed matched source (world rank), None if never completed
    mtag: Optional[int] = None
    mcount: Optional[int] = None
    widx: Optional[int] = None
    completed: bool = True
    tsig: Optional[str] = None
    cancelled: bool = False
    mrecv: bool = False              # Mrecv/Imrecv: takes the message its Mprobe removed from the queue
    freed: bool = False              # MPI_Request_free'd while pending: completes unobserved

    @property
    def capacity(self) -> int:
        return self.count * self.tsize

    @property
    def wildcard(self) -> bool:
        return self.src == ANY or self.tag == ANY

    def accepts(self, s: "Send") -> bool:
        return (s.dst == self.rank and s.comm == self.comm
                and (self.src == ANY or self.src == s.rank)
                and (self.tag == ANY or self.tag == s.tag))

    def issue_node(self):
        return (self.rank, self.idx, "i")

    def complete_node(self):
        if not self.completed:
            return None
        return (self.rank, self.complete_idx if self.complete_idx is not None else self.idx, "c")

    def __str__(self):
        src = "ANY" if self.src == ANY else str(self.src)
        tag = "ANY" if self.tag == ANY else str(self.tag)
        return f"recv{self.key} src={src} tag={tag} cap={self.capacity}B"


@dataclass(slots=True)
class Probe:
    """A probe is a non-consuming receive: it observes a message without taking it."""
    key: Key
    rank: int
    idx: int
    src: int
    tag: int
    comm: int
    kind: str          # probe | iprobe | mprobe | improbe
    flag: bool
    msrc: Optional[int] = None
    mtag: Optional[int] = None
    completed: bool = True
    seen: Optional[Key] = None   # the send it observed, per FIFO reconstruction
    pidx: Optional[int] = None   # wildcard probe ordinal (replay pin)

    @property
    def wildcard(self) -> bool:
        return self.src == ANY or self.tag == ANY

    def accepts(self, s: Send) -> bool:
        return (s.dst == self.rank and s.comm == self.comm
                and (self.src == ANY or self.src == s.rank)
                and (self.tag == ANY or self.tag == s.tag))

    def issue_node(self):
        return (self.rank, self.idx, "i")

    def complete_node(self):
        return (self.rank, self.idx, "c") if self.completed else None

    def __str__(self):
        src = "ANY" if self.src == ANY else str(self.src)
        tag = "ANY" if self.tag == ANY else str(self.tag)
        return f"{self.kind}{self.key} src={src} tag={tag}"


@dataclass(slots=True)
class Coll:
    rank: int
    idx: int
    name: str
    comm: int
    ordinal: int  # k-th collective on this comm at this rank
    root: Optional[int] = None   # world rank of the root, if any
    req: Optional[int] = None
    complete_idx: Optional[int] = None
    completed: bool = True
    din: Optional[str] = None    # what this rank contributed, packed, in hexadecimal (STRETTO_PAYLOAD)
    dout: Optional[str] = None   # what it received

    def issue_node(self):
        return (self.rank, self.idx, "i")

    def complete_node(self):
        if not self.completed:
            return None
        return (self.rank, self.complete_idx if self.complete_idx is not None else self.idx, "c")


@dataclass
class Trace:
    ranks: Dict[int, List[Rec]] = field(default_factory=dict)
    sends: Dict[Key, Send] = field(default_factory=dict)
    recvs: Dict[Key, Recv] = field(default_factory=dict)
    probes: Dict[Key, Probe] = field(default_factory=dict)
    colls: List[Coll] = field(default_factory=list)
    observed: Dict[Key, Key] = field(default_factory=dict)  # recv key -> send key
    waitany: List[Rec] = field(default_factory=list)   # Waitany/Testany/Waitsome/Testsome records
    np: int = 0
    thread_level: int = 0                               # MPI_THREAD_* level the run was given (3 = MULTIPLE)
    threads: Dict[int, int] = field(default_factory=dict)   # rank -> number of threads that made MPI calls
    comm_ranks: Dict[int, List[int]] = field(default_factory=dict)   # global comm id -> world ranks, in comm-rank order (the remote group of an intercommunicator)
    comm_members: Dict[int, List[int]] = field(default_factory=dict) # global comm id -> every rank that takes part in its collectives
    comm_ranks_by_rank: Dict[Tuple[int, int], List[int]] = field(default_factory=dict)  # (rank, comm id) -> peer ranks of an intercommunicator seen from that rank
    truncated: List[int] = field(default_factory=list)   # ranks without a finalize record
    missing: List[int] = field(default_factory=list)     # ranks without a trace file
    bad_lines: int = 0
    warnings: List[str] = field(default_factory=list)
    unmodelled: List[Tuple[int, str]] = field(default_factory=list)   # (rank, call) outside the modelled fragment
    guards: List[Rec] = field(default_factory=list)      # the buffer guard's records (misused buffers of pending operations)
    guard: Dict[int, int] = field(default_factory=dict)  # rank -> 1 guard on, 0 asked for but off (absent: not asked for)
    sites: Dict[Tuple[int, int], dict] = field(default_factory=dict)    # (rank, site id) -> the tracer's site record
    _site_ids: Dict[int, "array"] = field(default_factory=dict, repr=False)   # rank -> site id per record, -1 if none

    def site_of(self, rank: int, idx: int) -> Optional[Tuple[str, int]]:
        """The call site of record idx of rank as (object path, offset in the object), the
        same in every process of the run; None if the tracer recorded none."""
        ids = self._site_ids.get(rank)
        sid = ids[idx] if ids is not None and idx < len(ids) else -1
        s = self.sites.get((rank, sid)) if sid >= 0 else None
        return None if s is None else (s.get("obj", "?"), s.get("addr", 0) - s.get("base", 0))

    def site_record(self, rank: int, idx: int) -> Optional[dict]:
        """The tracer's site record (object, return address, load address) of record idx."""
        ids = self._site_ids.get(rank)
        sid = ids[idx] if ids is not None and idx < len(ids) else -1
        return self.sites.get((rank, sid)) if sid >= 0 else None

    def site_label(self, rank: int, idx: int) -> Optional[str]:
        s = self.site_of(rank, idx)
        return None if s is None else f"{os.path.basename(s[0])}+{s[1]:#x}"

    # ---------------------------------------------------------------- helpers
    def recv_by_req(self, rank: int, req: int) -> Optional[Recv]:
        return self._by_req.get(("r", rank, req))

    def recvs_of(self, rank: int) -> List[Recv]:
        return self._recvs_of.get(rank, [])

    def sends_of(self, rank: int) -> List[Send]:
        return self._sends_of.get(rank, [])

    def wild_recvs_of(self, rank: int) -> List[Recv]:
        """Wildcard-source receives of a rank in posting order: the only earlier receives
        that can take a message from a *different* source than their own match."""
        return self._wild_recvs_of.get(rank, [])

    def sends_to(self, src: int, dst: int, comm: int) -> List[Send]:
        """The (src -> dst, comm) queue in issue order."""
        return self._queues.get((src, dst, comm), [])

    def sources_to(self, dst: int, comm: int) -> List[int]:
        """Ranks with at least one send to dst on comm, ascending."""
        return self._sources_to.get((dst, comm), [])

    def earlier_competitors(self, r: Recv, s: Send) -> Tuple[Recv, ...]:
        """Receiver side of matching order (docs/model.md §2, condition 3'): for each envelope
        class that accepts s, the latest receive of r's rank posted before r. Every earlier
        receive that accepts s lies in one of the four classes (source s.rank or ANY, tag
        s.tag or ANY), and inside a class each receive accepts whatever a later one does, so
        the order reaches the rest of the class through the class members' own pairs. r may
        also be a probe. Kept per pair: every constraint graph of the trace asks again, one per
        threshold, for every pair of the observed matching."""
        k = (r.key, s.key, type(r) is Recv)
        out = self._competitors.get(k)
        if out is None:
            out = self._competitors[k] = tuple(self._earlier_competitors(r, s))
        return out

    def _earlier_competitors(self, r, s: Send) -> List[Recv]:
        out = []
        for src in (s.rank, ANY):
            for tag in (s.tag, ANY):
                key = (r.rank, r.comm, src, tag)
                idx = self._recv_class_idx.get(key)
                if idx:
                    j = bisect_left(idx, r.idx)
                    if j:
                        out.append(self._recv_class[key][j - 1])
        return out

    def earlier_sends(self, r: Recv, s: Send) -> Tuple[Send, ...]:
        """Sender side of matching order (condition 2): for each tag r accepts, the latest
        send of s's channel issued before s that r could take. A receive that accepts one of
        a tag's sends accepts all of them, so the order chains through that send's own pair.
        r may also be a probe. Kept per pair, as earlier_competitors."""
        k = (r.key, s.key, type(r) is Recv)
        out = self._earlier.get(k)
        if out is None:
            out = self._earlier[k] = tuple(self._earlier_sends(r, s))
        return out

    def _earlier_sends(self, r, s: Send) -> List[Send]:
        q = (s.rank, s.dst, s.comm)
        tags = (r.tag,) if r.tag != ANY else self._tags_on.get(q, ())
        out = []
        for tag in tags:
            key = q + (tag,)
            idx = self._send_tag_idx.get(key)
            if not idx:
                continue
            j = bisect_left(idx, s.idx) - 1
            sends = self._send_tag[key]
            while j >= 0 and not sends[j].available_to(r):   # removed by an earlier matched probe
                j -= 1
            if j >= 0:
                out.append(sends[j])
        return out

    def observed_send(self, r: Recv) -> Optional[Send]:
        k = self.observed.get(r.key)
        return self.sends[k] if k is not None else None

    def coll_sets(self) -> List[List[Coll]]:
        """Group collectives into match sets: the k-th collective on a comm across ranks."""
        groups: Dict[Tuple[int, int], List[Coll]] = {}
        for c in self.colls:
            groups.setdefault((c.comm, c.ordinal), []).append(c)
        return [sorted(v, key=lambda c: c.rank) for _, v in sorted(groups.items())]

    def message_sizes(self) -> List[int]:
        return sorted({s.bytes for s in self.sends.values()})

    def blocked_calls(self) -> List[Rec]:
        return [rec for recs in self.ranks.values() for rec in recs if rec.op == "blocked"]

    def fast_indexes(self):
        """Threshold-independent indexes for the fast checks (stretto.fast), built once."""
        if getattr(self, "_fast", None) is None:
            from .fast import Indexes
            self._fast = Indexes(self)
        return self._fast

    def index(self) -> None:
        self._fast = None
        self._competitors: Dict = {}         # earlier_competitors and earlier_sends, per pair
        self._earlier: Dict = {}
        self._by_req = {}
        for s in self.sends.values():
            if s.req is not None:
                self._by_req[("s", s.rank, s.req)] = s
        for r in self.recvs.values():
            if r.req is not None:
                self._by_req[("r", r.rank, r.req)] = r
        self._recvs_of = {}
        self._wild_recvs_of = {}
        for r in sorted(self.recvs.values(), key=lambda r: r.idx):
            self._recvs_of.setdefault(r.rank, []).append(r)
            if r.src == ANY:
                self._wild_recvs_of.setdefault(r.rank, []).append(r)
        self._sends_of = {}
        self._queues = {}
        for s in sorted(self.sends.values(), key=lambda s: s.idx):
            self._sends_of.setdefault(s.rank, []).append(s)
            self._queues.setdefault((s.rank, s.dst, s.comm), []).append(s)
        self._sources_to = {}
        for src, dst, comm in sorted(self._queues):
            self._sources_to.setdefault((dst, comm), []).append(src)
        # matching-order indexes: receives by (rank, comm, source or ANY, tag or ANY) in
        # posting order (cancelled ones never match and an Mrecv takes the message its
        # probe removed, so neither competes), and sends by (channel, tag) in issue order
        self._recv_class = {}
        for r in sorted(self.recvs.values(), key=lambda r: r.idx):
            if not r.cancelled and not r.mrecv:
                self._recv_class.setdefault((r.rank, r.comm, r.src, r.tag), []).append(r)
        self._recv_class_idx = {k: [r.idx for r in v] for k, v in self._recv_class.items()}
        self._send_tag = {}
        for s in sorted(self.sends.values(), key=lambda s: s.idx):
            if not s.cancelled:
                self._send_tag.setdefault((s.rank, s.dst, s.comm, s.tag), []).append(s)
        self._send_tag_idx = {k: [s.idx for s in v] for k, v in self._send_tag.items()}
        self._tags_on = {}
        for src, dst, comm, tag in sorted(self._send_tag):
            self._tags_on.setdefault((src, dst, comm), []).append(tag)


# ---------------------------------------------------------------------- loading

def load(tracedir: str) -> Trace:
    t = Trace()
    files = sorted(f for f in os.listdir(tracedir) if f.startswith("rank-") and f.endswith(".jsonl"))
    if not files:
        raise FileNotFoundError(f"no rank-*.jsonl in {tracedir}")
    raw_ranks: Dict[int, List[dict]] = {}
    for f in files:
        rank = int(f[len("rank-"):-len(".jsonl")])
        recs = []
        with open(os.path.join(tracedir, f)) as fh:
            lines = fh.read().split("\n")
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                if i == len(lines) - 1 or (i == len(lines) - 2 and not lines[-1].strip()):
                    t.warnings.append(f"rank {rank}: discarded a partial trailing record")
                else:
                    # a record lost in the middle shifts every later ordinal of the rank:
                    # findings from such a trace would name the wrong operations
                    raise ValueError(f"{f}: unparseable record at line {i + 1}")
        check_records(f, rank, recs)
        raw_ranks[rank] = recs
    nps = {r: next((x.get("np") for x in recs if x.get("op") == "init"), None) for r, recs in raw_ranks.items()}
    if len({v for v in nps.values() if v is not None}) > 1:
        raise ValueError(f"ranks disagree on the number of processes {nps}: files from different runs?")
    _build(t, raw_ranks)
    return t


def check_records(fname: str, rank: int, recs: List[dict]) -> None:
    """Every record names its file's rank and the sequence numbers run without a gap (the
    tracer numbers each record of a rank, failed polls summarised by pollrepeat included)."""
    prev = None
    for k, r in enumerate(recs):
        if "r" in r and r["r"] != rank:
            raise ValueError(f"{fname}: record {k} belongs to rank {r['r']}")
        q = r.get("seq")
        if q is None:
            continue
        if prev is not None and q != prev + 1:
            raise ValueError(f"{fname}: records missing between sequence numbers {prev} and {q}")
        prev = q


SIDE_RECORDS = ("comm", "overflow", "guard")


def _fold_begins(raw: List[dict]) -> List[dict]:
    """Drop `begin` records that are followed by their completing record, the next record
    of the same thread when records carry `tid`; an unpaired `begin` (the rank was killed
    inside the call) becomes an `op: blocked` record. `pollrepeat` records (a failed poll
    repeated n times) carry no event of their own and are dropped: the failed poll they
    repeat is already in the trace once."""
    raw = [r for r in raw if r.get("op") != "pollrepeat"]
    # the next record of each thread, found in one backward pass (a scan per begin is quadratic).
    # Side records the tracer writes while a call is in progress (the communicator an Idup
    # completion registers, a table overflow) are not the call's completion and are skipped.
    following: List[Optional[dict]] = [None] * len(raw)
    last: Dict[Optional[int], dict] = {}
    for i in range(len(raw) - 1, -1, -1):
        following[i] = last.get(raw[i].get("tid"))
        if raw[i].get("op") not in SIDE_RECORDS:
            last[raw[i].get("tid")] = raw[i]
    out = []
    for i, r in enumerate(raw):
        if r.get("op") != "begin":
            out.append(r)
            continue
        nxt = following[i]
        if nxt is not None and nxt.get("op") == r.get("call") and nxt.get("op") != "begin":
            continue
        b = dict(r)
        b["op"] = "blocked"
        out.append(b)
    return out


def _build(t: Trace, raw_ranks: Dict[int, List[dict]]) -> None:
    # pass 0: call-site records are a table, not events; fold begin/end, np, communicators
    for rank, recs in raw_ranks.items():
        for r in recs:
            if r.get("op") == "site":
                t.sites[(rank, r.get("id"))] = r
    folded = {rank: _fold_begins([r for r in recs if r.get("op") != "site"]) for rank, recs in raw_ranks.items()}
    for rank, recs in folded.items():
        t.ranks[rank] = [Rec(rank, i, r["op"], r) for i, r in enumerate(recs)]
        t.threads[rank] = len({r.get("tid") for r in recs} - {None}) or 1
        for r in recs:
            if r["op"] == "init":
                t.np = max(t.np, r.get("np", 0))
                t.thread_level = max(t.thread_level, r.get("thread", 0))
                if "guard" in r:
                    t.guard[rank] = r["guard"]
            elif r["op"] == "overflow":
                t.warnings.append(f"rank {rank}: the tracer's {r.get('what')} overflowed; later records of that kind may be incomplete")
            elif r["op"] == "unmodelled":
                t.unmodelled.append((rank, r.get("call", "?")))
        t.guards.extend(rec for rec in t.ranks[rank] if rec.op == "guard")
        if not any(r["op"] == "finalize" for r in recs):
            t.truncated.append(rank)
    if t.np:
        t.missing = [r for r in range(t.np) if r not in t.ranks]
        if t.missing:
            t.warnings.append(f"missing trace files for ranks {t.missing}")
    all_ranks = list(range(t.np)) if t.np else sorted(t.ranks)
    comm_map = _comm_ids(t, folded, all_ranks)   # (rank, local id) -> global id

    def gid(rank: int, local: int) -> int:
        return comm_map.get((rank, local), local)

    def worldg(comm_gid: int, r: Optional[int], rank: Optional[int] = None) -> Optional[int]:
        """Translate a comm-local rank to a world rank (on an intercommunicator the peer
        ranks name the remote group, which differs per side)."""
        if r is None or r < 0:
            return r
        members = t.comm_ranks_by_rank.get((rank, comm_gid)) if rank is not None else None
        if members is None:
            members = t.comm_ranks.get(comm_gid)
        if members is None or r >= len(members):
            return r
        return members[r]

    def world(rank: int, comm_local_id: int, r: Optional[int]) -> Optional[int]:
        return worldg(gid(rank, comm_local_id), r, rank)

    def src_of(raw: dict) -> int:
        if raw.get("anysrc"):
            return ANY
        return raw["src"]

    def tag_of(raw: dict) -> int:
        if raw.get("anytag"):
            return ANY
        return raw["tag"]

    # pass 1: operations
    for rank, recs in t.ranks.items():
        nsend = nrecv = nprobe = 0
        coll_ord: Dict[int, int] = {}
        pending_req: Dict[int, object] = {}
        cancelled_ops: List[object] = []       # the operations a cancel record names
        status_at: Dict[int, dict] = {}        # request -> the first MPI_Request_get_status that found it complete
        for rec in recs:
            raw = rec.raw
            op = rec.op
            blocked = op == "blocked"
            call = raw.get("call") if blocked else op
            if call == "send":
                if raw.get("procnull"):
                    continue
                mode = raw.get("mode", "std")
                nb = mode in ("isend", "issend", "ibsend", "irsend")
                comm = gid(rank, raw["comm"])
                s = Send((rank, nsend), rank, rec.idx, world(rank, raw["comm"], raw["dst"]), raw["tag"], comm,
                         raw["count"], raw["tsize"], not nb, mode, raw.get("req"), tsig=raw.get("tsig"),
                         data=raw.get("data"))
                nsend += 1
                if blocked:
                    s.completed = False
                t.sends[s.key] = s
                if nb:
                    s.completed = False      # until a wait/test completes the request
                    if raw.get("req") is not None:
                        pending_req[raw["req"]] = s
            elif call in ("recv", "irecv"):
                if raw.get("procnull"):
                    continue
                nb = call == "irecv"
                comm = gid(rank, raw["comm"])
                r = Recv((rank, nrecv), rank, rec.idx, world(rank, raw["comm"], src_of(raw)), tag_of(raw), comm,
                         raw["count"], raw["tsize"], not nb, raw.get("req"),
                         widx=raw.get("widx"), tsig=raw.get("tsig"), mrecv=bool(raw.get("mrecv")))
                nrecv += 1
                if blocked:
                    r.completed = False
                elif not nb:
                    r.msrc = world(rank, raw["comm"], raw.get("msrc"))
                    r.mtag, r.mcount = raw.get("mtag"), raw.get("mcount")
                    r.cancelled = bool(raw.get("cancelled"))
                    if r.msrc is None or r.cancelled:
                        r.completed = r.completed and not r.cancelled
                else:
                    r.completed = False   # until a wait completes it
                    if raw.get("req") is not None:
                        pending_req[raw["req"]] = r
                t.recvs[r.key] = r
            elif call == "wait":
                if raw.get("kind") == "getstatus":
                    # MPI_Request_get_status leaves the request to a later wait or test, but a
                    # true flag is a result, as a successful test's is: the operation completed
                    # there, so it is its completion node (a false flag, like a failed test,
                    # says nothing, since the model has no progress guarantee)
                    if not blocked and raw.get("flag"):
                        for q in raw.get("reqs", []):
                            if q is not None and q >= 0 and q in pending_req:
                                status_at.setdefault(q, {**raw, "idx": rec.idx})
                    continue
                if raw.get("kind") in ("waitany", "testany", "waitsome", "testsome"):
                    t.waitany.append(rec)
                if blocked:
                    continue
                for d in raw.get("done", []):
                    q = d.get("req")
                    if q is None or q < 0:
                        continue
                    obj = pending_req.pop(q, None)
                    if obj is None:
                        continue
                    st = status_at.pop(q, None)
                    obj.complete_idx = rec.idx if st is None else st["idx"]
                    obj.completed = True
                    if isinstance(obj, Send) and "cancelled" in d:
                        obj.cancelled = bool(d["cancelled"])       # the completion status is authoritative
                    if isinstance(obj, Recv):
                        obj.msrc = worldg(obj.comm, d.get("msrc"), rank)
                        obj.mtag, obj.mcount = d.get("mtag"), d.get("mcount")
                        obj.cancelled = bool(d.get("cancelled"))
                        if obj.msrc is None or obj.cancelled:
                            obj.completed = False
            elif call == "cancel":
                obj = pending_req.get(raw.get("req"))
                if isinstance(obj, (Send, Recv)):
                    cancelled_ops.append(obj)
                if isinstance(obj, Send):
                    obj.cancelled = True     # provisional; the completion status (if any) decides
            elif call == "reqfree":
                obj = pending_req.pop(raw.get("req"), None)
                st = status_at.pop(raw.get("req"), None)
                if obj is not None and st is not None:
                    _complete_at_status(obj, st, worldg, rank)
                elif isinstance(obj, Recv):
                    obj.freed = True         # the receive completes unobserved: its message is not an orphan
            elif call == "probe":
                if raw.get("procnull"):
                    continue                   # a probe of MPI_PROC_NULL returns at once and observes nothing
                comm = gid(rank, raw["comm"])
                p = Probe((rank, nprobe), rank, rec.idx, world(rank, raw["comm"], src_of(raw)), tag_of(raw), comm,
                          raw.get("kind", "probe"), bool(raw.get("flag", 1)),
                          world(rank, raw["comm"], raw.get("msrc")), raw.get("mtag"), pidx=raw.get("pidx"))
                nprobe += 1
                if blocked:
                    p.completed = False
                t.probes[p.key] = p
            elif call == "coll":
                comm = gid(rank, raw["comm"])
                k = coll_ord.get(comm, 0)
                coll_ord[comm] = k + 1
                root = raw.get("root")
                root = None if root is None or root < 0 else world(rank, raw["comm"], root)
                c = Coll(rank, rec.idx, raw["name"], comm, k, root, raw.get("req"), din=raw.get("din"), dout=raw.get("dout"))
                if blocked:
                    c.completed = False
                elif c.req is not None:
                    c.completed = False
                    pending_req[c.req] = c
                t.colls.append(c)
        for q, st in status_at.items():        # found complete, and no later call returned it
            obj = pending_req.get(q)
            if obj is not None:
                _complete_at_status(obj, st, worldg, rank)
        # the model has no cancellation: a cancel that succeeded withdrew an operation at a
        # point no matching rule describes, and one whose outcome the trace does not have may
        # have; a cancel that failed changed nothing (an implementation may always let it fail)
        for obj in cancelled_ops:
            returned = obj.complete_idx is not None      # a completion call returned the request
            if returned and obj.cancelled:
                t.unmodelled.append((rank, "Cancel (succeeded)"))
            elif not returned:
                t.unmodelled.append((rank, "Cancel (outcome not recorded)"))
    t.index()
    _reconstruct_observed(t)
    # call sites, 4 bytes per record, before the dictionaries that name them go
    if t.sites:
        for rank, recs in t.ranks.items():
            ids = array("i", [-1]) * len(recs)
            for rec in recs:
                sid = rec.raw.get("site") if rec.raw is not None else None
                if sid is not None:
                    ids[rec.idx] = sid
            t._site_ids[rank] = ids
    # the bulk of a trace is point-to-point and collective records whose fields now live in
    # Send/Recv/Coll objects: drop their dictionaries (a third of the memory on large traces).
    # Blocked calls, waits, probes and the rest keep theirs; the report reads them.
    for recs in t.ranks.values():
        for rec in recs:
            if rec.op in ("send", "recv", "irecv", "coll"):
                rec.raw = None


def _complete_at_status(obj, st: dict, worldg, rank: int) -> None:
    """A request that an MPI_Request_get_status record st found complete and that no later wait
    or test returned (freed, or never completed): it completed at st. A receive's status is in
    st when the tracer wrote it there (since 22 Oct 2026); without it the receive's message is
    unknown and the receive counts as freed, which the analysis treats as unmatched."""
    obj.complete_idx = st["idx"]
    obj.completed = True
    if isinstance(obj, Recv):
        obj.msrc = worldg(obj.comm, st.get("msrc"), rank)
        obj.mtag, obj.mcount = st.get("mtag"), st.get("mcount")
        obj.cancelled = bool(st.get("cancelled"))
        if obj.msrc is None or obj.cancelled:
            obj.completed = False
            obj.freed = not obj.cancelled


def _comm_ids(t: Trace, folded: Dict[int, List[dict]], all_ranks: List[int]) -> Dict[Tuple[int, int], int]:
    """Assign global communicator ids. Key = (tuple of world ranks, gord)."""
    keys: Dict[Tuple[Tuple[int, ...], int], int] = {}
    world_key = (tuple(all_ranks), 0)
    keys[world_key] = WORLD
    t.comm_ranks[WORLD] = list(all_ranks)
    t.comm_members[WORLD] = list(all_ranks)
    mapping: Dict[Tuple[int, int], int] = {}
    for rank in t.ranks:
        mapping[(rank, 0)] = WORLD
        self_key = ((rank,), 0)
        if self_key not in keys:
            keys[self_key] = len(keys)
            t.comm_ranks[keys[self_key]] = [rank]
            t.comm_members[keys[self_key]] = [rank]
        mapping[(rank, 1)] = keys[self_key]
    for rank, recs in folded.items():
        for r in recs:
            if r.get("op") != "comm" or r.get("id", -1) is None or r.get("id", -1) < 0:
                continue
            if r.get("name") == "free":
                continue
            if "ranks" in r:
                members = r.get("members", r["ranks"])   # an intercommunicator: both groups
                key = (tuple(members), r.get("gord", 0))
                if key not in keys:
                    keys[key] = len(keys)
                    t.comm_members[keys[key]] = list(members)
                g = keys[key]
                if g not in t.comm_ranks or not r.get("inter"):
                    t.comm_ranks[g] = list(r["ranks"])
                if r.get("inter"):
                    # src/dst on this rank's side name the remote group: per-rank translation
                    t.comm_ranks_by_rank[(rank, g)] = list(r["ranks"])
                mapping[(rank, r["id"])] = g
            else:
                mapping[(rank, r["id"])] = r["id"]   # old traces: keep the local id
                t.comm_ranks.setdefault(r["id"], list(all_ranks))
                t.comm_members.setdefault(r["id"], list(all_ranks))
    return mapping


def _reconstruct_observed(t: Trace) -> None:
    """Pair every completed receive with its send, using non-overtaking: receives are
    processed in posting order per rank; each takes the earliest unconsumed send from the
    (msrc -> rank, comm, mtag) queue. Probes see the earliest send not consumed by
    receives posted before them. A send whose cancellation succeeded never reached its
    destination, so it is in no queue. Linear: one pointer per queue."""
    queues: Dict[Tuple[int, int, int, int], List[Send]] = {}
    for s in sorted(t.sends.values(), key=lambda s: (s.rank, s.idx)):
        if not s.cancelled:
            queues.setdefault((s.rank, s.dst, s.comm, s.tag), []).append(s)
    ptr: Dict[Tuple[int, int, int, int], int] = {}
    for rank in sorted(t.ranks):
        events = sorted(list(t.recvs_of(rank)) + [p for p in t.probes.values() if p.rank == rank],
                        key=lambda x: x.idx)
        reserved: Dict[Tuple[int, int, int, int], List[Send]] = {}   # removed by Mprobe, awaiting Mrecv
        for e in events:
            if isinstance(e, Probe):
                if not e.flag or e.msrc is None:
                    continue
                key = (e.msrc, rank, e.comm, e.mtag)
                q = queues.get(key)
                i = ptr.get(key, 0)
                if q and i < len(q):
                    e.seen = q[i].key
                    if e.kind in ("mprobe", "improbe"):   # consuming: the message leaves the queue here
                        q[i].reserved_idx = e.idx
                        reserved.setdefault(key, []).append(q[i])
                        ptr[key] = i + 1
                continue
            if not e.completed or e.msrc is None:
                continue
            key = (e.msrc, rank, e.comm, e.mtag)
            if e.mrecv and reserved.get(key):
                t.observed[e.key] = reserved[key].pop(0).key
                continue
            q = queues.get(key)
            i = ptr.get(key, 0)
            if not q or i >= len(q):
                t.warnings.append(f"{e}: completed from {e.msrc} tag {e.mtag} but no such send in the trace")
                continue
            t.observed[e.key] = q[i].key
            ptr[key] = i + 1
