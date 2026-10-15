"""Trace model.

A trace is one JSONL file per rank (see tracer/mpitrace.c). Every record becomes one
`Rec` with two graph nodes, issue `(rank, idx, 'i')` and complete `(rank, idx, 'c')`.
Blocking operations complete at their own record; nonblocking ones complete at the
wait/test record that completed their request. An operation that never completed in
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
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ANY = -1
WORLD = 0

Key = Tuple[int, int]  # (rank, ordinal)


@dataclass
class Rec:
    rank: int
    idx: int
    op: str
    raw: dict

    @property
    def issue(self):
        return (self.rank, self.idx, "i")

    @property
    def complete(self):
        return (self.rank, self.idx, "c")


@dataclass
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

    @property
    def bytes(self) -> int:
        return self.count * self.tsize

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


@dataclass
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
    pinned: Optional[dict] = None
    widx: Optional[int] = None
    completed: bool = True
    tsig: Optional[str] = None
    cancelled: bool = False

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


@dataclass
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


@dataclass
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
    waitany: List[Rec] = field(default_factory=list)
    np: int = 0
    comm_ranks: Dict[int, List[int]] = field(default_factory=dict)   # global comm id -> world ranks
    truncated: List[int] = field(default_factory=list)   # ranks without a finalize record
    missing: List[int] = field(default_factory=list)     # ranks without a trace file
    bad_lines: int = 0
    warnings: List[str] = field(default_factory=list)

    # ---------------------------------------------------------------- helpers
    def rank_len(self, rank: int) -> int:
        return len(self.ranks[rank])

    def send_by_req(self, rank: int, req: int) -> Optional[Send]:
        return self._by_req.get(("s", rank, req))

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

    def rs_edges_observed(self, rk: Key):
        """Receiver-side ordering edges for an observed pair (cached)."""
        if rk in self._rs_cache:
            return self._rs_cache[rk]
        r = self.recvs[rk]
        s = self.sends[self.observed[rk]]
        rc = r.complete_node()
        edges = []
        if rc is not None:
            for r2 in self.wild_recvs_of(r.rank):     # a specific-source r2 is matched to s's own source: no edge
                if r2.idx >= r.idx:
                    break
                if r2.accepts(s) and r2.key in self.observed:
                    s2 = self.sends[self.observed[r2.key]]
                    if s2.rank != s.rank:
                        edges.append((s2.issue_node(), rc))
        self._rs_cache[rk] = edges
        return edges

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

    def index(self) -> None:
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
        self._rs_cache = {}


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
                    t.bad_lines += 1
        raw_ranks[rank] = recs
    _build(t, raw_ranks)
    return t


BLOCKING_CALLS = {"recv", "wait", "probe", "coll", "send"}


def _fold_begins(raw: List[dict]) -> List[dict]:
    """Drop `begin` records that are followed by their completing record; an unpaired
    `begin` (the rank was killed inside the call) becomes an `op: blocked` record.
    `pollrepeat` records (a failed poll repeated n times) carry no event of their own and
    are dropped: the failed poll they repeat is already in the trace once."""
    raw = [r for r in raw if r.get("op") != "pollrepeat"]
    out = []
    i = 0
    while i < len(raw):
        r = raw[i]
        if r.get("op") == "begin":
            nxt = raw[i + 1] if i + 1 < len(raw) else None
            if nxt is not None and nxt.get("op") == r.get("call") and nxt.get("op") != "begin":
                i += 1
                continue
            b = dict(r)
            b["op"] = "blocked"
            out.append(b)
            i += 1
            continue
        out.append(r)
        i += 1
    return out


def _build(t: Trace, raw_ranks: Dict[int, List[dict]]) -> None:
    # pass 0: fold begin/end, np, communicators
    folded = {rank: _fold_begins(recs) for rank, recs in raw_ranks.items()}
    for rank, recs in folded.items():
        t.ranks[rank] = [Rec(rank, i, r["op"], r) for i, r in enumerate(recs)]
        for r in recs:
            if r["op"] == "init":
                t.np = max(t.np, r.get("np", 0))
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

    def worldg(comm_gid: int, r: Optional[int]) -> Optional[int]:
        """Translate a comm-local rank to a world rank."""
        if r is None or r < 0:
            return r
        members = t.comm_ranks.get(comm_gid)
        if members is None or r >= len(members):
            return r
        return members[r]

    def world(rank: int, comm_local_id: int, r: Optional[int]) -> Optional[int]:
        return worldg(gid(rank, comm_local_id), r)

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
                         raw["count"], raw["tsize"], not nb, mode, raw.get("req"), tsig=raw.get("tsig"))
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
                         raw["count"], raw["tsize"], not nb, raw.get("req"), pinned=raw.get("pinned"),
                         widx=raw.get("widx"), tsig=raw.get("tsig"))
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
                if raw.get("kind") in ("waitany", "testany"):
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
                    obj.complete_idx = rec.idx
                    obj.completed = True
                    if isinstance(obj, Recv):
                        obj.msrc = worldg(obj.comm, d.get("msrc"))
                        obj.mtag, obj.mcount = d.get("mtag"), d.get("mcount")
                        obj.cancelled = bool(d.get("cancelled"))
                        if obj.msrc is None or obj.cancelled:
                            obj.completed = False
            elif call == "probe":
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
                c = Coll(rank, rec.idx, raw["name"], comm, k, root, raw.get("req"))
                if blocked:
                    c.completed = False
                elif c.req is not None:
                    c.completed = False
                    pending_req[c.req] = c
                t.colls.append(c)
    t.index()
    _reconstruct_observed(t)


def _comm_ids(t: Trace, folded: Dict[int, List[dict]], all_ranks: List[int]) -> Dict[Tuple[int, int], int]:
    """Assign global communicator ids. Key = (tuple of world ranks, gord)."""
    keys: Dict[Tuple[Tuple[int, ...], int], int] = {}
    world_key = (tuple(all_ranks), 0)
    keys[world_key] = WORLD
    t.comm_ranks[WORLD] = list(all_ranks)
    mapping: Dict[Tuple[int, int], int] = {}
    for rank in t.ranks:
        mapping[(rank, 0)] = WORLD
        self_key = ((rank,), 0)
        if self_key not in keys:
            keys[self_key] = len(keys)
            t.comm_ranks[keys[self_key]] = [rank]
        mapping[(rank, 1)] = keys[self_key]
    for rank, recs in folded.items():
        for r in recs:
            if r.get("op") != "comm" or r.get("id", -1) is None or r.get("id", -1) < 0:
                continue
            if r.get("name") == "free":
                continue
            if "ranks" in r:
                key = (tuple(r["ranks"]), r.get("gord", 0))
                if key not in keys:
                    keys[key] = len(keys)
                    t.comm_ranks[keys[key]] = list(r["ranks"])
                mapping[(rank, r["id"])] = keys[key]
            else:
                mapping[(rank, r["id"])] = r["id"]   # old traces: keep the local id
                t.comm_ranks.setdefault(r["id"], list(all_ranks))
    return mapping


def _reconstruct_observed(t: Trace) -> None:
    """Pair every completed receive with its send, using non-overtaking: receives are
    processed in posting order per rank; each takes the earliest unconsumed send from the
    (msrc -> rank, comm, mtag) queue. Probes see the earliest send not consumed by
    receives posted before them. Linear: one pointer per queue."""
    queues: Dict[Tuple[int, int, int, int], List[Send]] = {}
    for s in sorted(t.sends.values(), key=lambda s: (s.rank, s.idx)):
        queues.setdefault((s.rank, s.dst, s.comm, s.tag), []).append(s)
    ptr: Dict[Tuple[int, int, int, int], int] = {}
    for rank in sorted(t.ranks):
        events = sorted(list(t.recvs_of(rank)) + [p for p in t.probes.values() if p.rank == rank],
                        key=lambda x: x.idx)
        for e in events:
            if isinstance(e, Probe):
                if not e.flag or e.msrc is None:
                    continue
                q = queues.get((e.msrc, rank, e.comm, e.mtag))
                i = ptr.get((e.msrc, rank, e.comm, e.mtag), 0)
                if q and i < len(q):
                    e.seen = q[i].key
                continue
            if not e.completed or e.msrc is None:
                continue
            key = (e.msrc, rank, e.comm, e.mtag)
            q = queues.get(key)
            i = ptr.get(key, 0)
            if not q or i >= len(q):
                t.warnings.append(f"{e}: completed from {e.msrc} tag {e.mtag} but no such send in the trace")
                continue
            t.observed[e.key] = q[i].key
            ptr[key] = i + 1
