"""A tiny abstract-MPI language for idiom twins and for traces under the single-path
assumption. Programs are {rank: [ops]}. Metadata (source, tag, bytes of a received
message, index completed by Waitany) is bound to named variables; `If` branches on
them. A hand-written twin has no payload: that is the L2 skeleton by construction. A
message may carry one (`Send.data`, bound as `Meta.data`), which the re-executed ranks of
stretto.rerun read; `Lazy` computes the rest of a rank's program from its variables when
the rank gets there, and `Unknown` marks a point beyond which it is not determined.

Sends and receives are identified by (rank, ordinal) exactly as in trace.py.
"""
from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

ANY = -1


@dataclass(frozen=True)
class Send:
    dst: object                     # rank, or a metadata variable name, or callable(env) -> rank
    tag: int
    bytes: int = 4
    comm: int = 0
    req: Optional[str] = None       # set => nonblocking (Isend)
    mode: str = "std"               # std | ssend (always rendezvous) | bsend (never blocks) | rsend
    data: Optional[str] = None      # the payload, packed, in hexadecimal; None: not modelled


@dataclass(frozen=True)
class Recv:
    src: int
    tag: int
    cap: int = 4
    comm: int = 0
    req: Optional[str] = None       # set => nonblocking (Irecv)
    into: Optional[str] = None      # metadata variable bound at completion


@dataclass(frozen=True)
class Wait:
    req: str
    mark: Optional[str] = None      # recorded in the outcome when the wait returns (explore.Outcome.marks)


@dataclass(frozen=True)
class Probe:
    """MPI_Probe (blocking: waits until a message it accepts can be received) or MPI_Iprobe
    (may return false whatever is pending, since nothing guarantees progress, or true with a
    message it accepts that a receive posted at this point would take). Non-consuming. into is
    bound to the message's Meta, or to None for a false flag."""
    src: int
    tag: int
    comm: int = 0
    blocking: bool = False
    into: Optional[str] = None


def Iprobe(src, tag, comm=0, into=None):
    return Probe(src, tag, comm, False, into)


@dataclass(frozen=True)
class Test:
    """MPI_Test of a request that returns false: always a possible result, since nothing
    guarantees progress, and the only one the explorer gives (a test that succeeds is a Wait).
    A trace written from an execution records it as a failed test (trace_from_execution), the
    record whose flip to true docs/model.md §3 predicts."""
    req: str


@dataclass(frozen=True)
class Waitany:
    reqs: Tuple[str, ...]
    into: Optional[str] = None      # index variable


@dataclass(frozen=True)
class Coll:
    name: str
    comm: int = 0
    req: Optional[str] = None       # set => nonblocking collective, completed by Wait/Waitany
    root: Optional[int] = None      # root rank of bcast/scatter/reduce/gather; None: synchronise all


@dataclass(frozen=True)
class If:
    cond: Callable[[dict], bool]
    then: Tuple[object, ...]
    els: Tuple[object, ...] = ()


@dataclass(frozen=True)
class Abort:
    """The program aborts (MPI_Abort, abort(), exit on its own check): a terminal error."""
    why: str = "abort"


@dataclass(frozen=True)
class Lazy:
    """The rest of the rank's program, computed when the rank reaches this point: fn maps the
    rank's variables to the ops that follow (which may end with another Lazy). key names the
    continuation: two Lazy ops with equal keys give the same ops for the same variables, so
    explorer states that differ only in the function object are the same state."""
    key: object
    fn: Callable[[dict], Sequence[object]] = field(compare=False, repr=False)


@dataclass(frozen=True)
class Unknown:
    """What the rank does from here on is not determined. The rank stops; an execution that
    reaches this point is neither finished nor deadlocked (explorer outcome 'unknown'), while
    what happened before it, an abort or a call that departs from the trace, stands."""
    why: str = "undetermined"


@dataclass(frozen=True)
class Meta:
    """Metadata of a completed receive, and the payload when the message carries one."""
    src: int
    tag: int
    bytes: int
    data: Optional[str] = None


Op = Union[Send, Recv, Wait, Waitany, Coll, Probe, Test, If, Abort, Lazy, Unknown]
Program = Dict[int, List[Op]]


def Isend(dst, tag, bytes=4, req="s", comm=0):
    return Send(dst, tag, bytes, comm, req)


def Irecv(src, tag, cap=4, req="r", comm=0, into=None):
    return Recv(src, tag, cap, comm, req, into)


_MODE = {"ssend": "ssend", "issend": "ssend", "bsend": "bsend", "ibsend": "bsend", "rsend": "rsend", "irsend": "rsend"}


def program_from_trace(t, positions: Optional[Dict[int, List[int]]] = None, probes: bool = False,
                       mark_test: Optional[Tuple[int, int]] = None) -> Program:
    """Straight-line DSL program from a trace (single-path assumption), built from the
    loaded Send/Recv/Coll objects: ranks are world ranks, communicator ids are the global
    ones (explore(..., comms=t.comm_members)), ANY is the loaded ANY, and the ordinals are
    trace.py's by construction, since both skip MPI_PROC_NULL operations. A call the rank
    was blocked in when the run ended is part of the program: the explorer finds the hang.
    positions, if given, receives per rank the trace record of each op of the program.
    probes=True also issues the probes: Probe and Iprobe ops (whose results the program
    ignores, so that an Iprobe constrains nothing unless explore forces its result; a blocking
    probe waits for a message), with the ordinals of trace.py; a matched probe (Mprobe,
    Improbe) is not modelled and raises ValueError. mark_test, (rank, record) of a failed test
    of a receive's request, makes that test a wait for the request marked "flip" (a test flip,
    explore.Outcome.marks); the later wait of the request then returns at once."""
    prog: Program = {}
    colls = {}
    for c in t.colls:
        colls[(c.rank, c.idx)] = c
    probe_at = {(p.rank, p.idx): p for p in t.probes.values()} if probes else {}
    for rank, recs in t.ranks.items():
        sends = {s.idx: s for s in t.sends_of(rank)}
        recvs = {r.idx: r for r in t.recvs_of(rank)}
        ops: List[Op] = []
        where: List[int] = []
        for rec in recs:
            raw = rec.raw
            call = raw.get("call") if rec.op == "blocked" else rec.op
            if mark_test == (rank, rec.idx):
                ops.extend(Wait(f"q{q}", "flip") for q in raw.get("reqs", []) if q is not None and q >= 0)
            elif call == "probe" and probes:
                p = probe_at.get((rank, rec.idx))
                if p is not None:          # None: a probe of MPI_PROC_NULL
                    if p.kind in ("mprobe", "improbe"):
                        raise ValueError("program_from_trace: matched probes are not modelled")
                    ops.append(Probe(p.src, p.tag, p.comm, p.kind == "probe"))
            elif call == "send":
                s = sends.get(rec.idx)
                if s is not None:          # None: a send to MPI_PROC_NULL
                    ops.append(Send(s.dst, s.tag, s.bytes, s.comm, None if s.blocking else f"q{s.req}",
                                    _MODE.get(s.mode, "std")))
            elif call in ("recv", "irecv"):
                r = recvs.get(rec.idx)
                if r is not None:
                    ops.append(Recv(r.src, r.tag, r.capacity, r.comm, None if r.blocking else f"q{r.req}"))
            elif call == "wait":
                kind = raw.get("kind")
                if kind == "getstatus":
                    # a true MPI_Request_get_status found its request complete: a wait there, as for
                    # a successful test (the later wait or test that returns it then returns at once)
                    if rec.op != "blocked" and raw.get("flag"):
                        ops.extend(Wait(f"q{q}") for q in raw.get("reqs", []) if q is not None and q >= 0)
                elif rec.op == "blocked":
                    reqs = tuple(f"q{q}" for q in raw.get("reqs", []) if q is not None and q >= 0)
                    if kind in ("waitany", "testany"):
                        ops.append(Waitany(reqs))
                    else:
                        ops.extend(Wait(q) for q in reqs)
                elif kind in ("waitany", "testany"):
                    # A Test* that succeeded is modelled as a wait: in the observed run it was
                    # complete at this point; an alternative where it is not is a metadata-
                    # dependent (flag) continuation, i.e. beyond L1.
                    reqs = tuple(f"q{q}" for q in raw.get("reqs", []) if q is not None and q >= 0)
                    ops.append(Waitany(reqs))
                else:
                    for d in raw.get("done", []):
                        if d.get("req") is not None and d["req"] >= 0:
                            ops.append(Wait(f"q{d['req']}"))
            elif call == "coll" or (call == "comm" and (rank, rec.idx) in colls):
                # comm: a communicator's creation or release, a collective of the communicator it is
                # collective over (trace.ON_PARENT)
                c = colls[(rank, rec.idx)]
                ops.append(Coll(c.name, c.comm, f"q{c.req}" if c.req is not None else None, c.root))
            where.extend([rec.idx] * (len(ops) - len(where)))
        prog[rank] = ops
        if positions is not None:
            positions[rank] = where
    return prog


# ------------------------------------------------------------ random programs

COLLS = ["barrier", "allreduce", "bcast", "scatter", "reduce", "gather", "scan"]


def random_program(rng: random.Random, nranks: int, branches: bool = True, waitany: bool = True,
                   ops_per_rank: Tuple[int, int] = (1, 5)) -> Tuple[Program, Dict[int, List[int]]]:
    """A random program over comm 0 (everyone) and comm 1 (the first two ranks): every send
    mode, wildcard receives, nonblocking operations with Wait/Waitany, collectives of every
    shape with a root, and a branch on the source of a received message. branches=False
    and waitany=False give a straight-line program whose executions can be written back as
    traces (trace_from_execution). Returns (program, comms)."""
    prog: Program = {}
    comms = {0: list(range(nranks)), 1: [0, 1]}
    kinds = ["send", "send", "recv", "recv", "irecv", "isend", "coll"] + (["if"] if branches else [])
    for r in range(nranks):
        ops: List[Op] = []
        n = rng.randint(*ops_per_rank)
        pending_reqs: List[str] = []
        bound = None
        for k in range(n):
            kind = rng.choice(kinds)
            comm = 1 if r in comms[1] and rng.random() < 0.3 else 0
            others = [x for x in comms[comm] if x != r]
            if not others:
                comm, others = 0, [x for x in range(nranks) if x != r]
            if kind == "send":
                ops.append(Send(rng.choice(others), rng.choice([1, 2]), rng.choice([4, 64]), comm,
                                mode=rng.choice(["std", "std", "ssend", "bsend", "rsend"])))
            elif kind == "isend":
                name = f"s{k}"
                ops.append(Isend(rng.choice(others), rng.choice([1, 2]), rng.choice([4, 64]), req=name, comm=comm))
                pending_reqs.append(name)
            elif kind == "recv":
                into = f"m{k}" if branches and rng.random() < 0.5 else None
                ops.append(Recv(rng.choice(others + [ANY]), rng.choice([1, 2, ANY]), 64, comm, into=into))
                bound = into or bound
            elif kind == "irecv":
                name = f"r{k}"
                ops.append(Irecv(rng.choice(others + [ANY]), rng.choice([1, 2, ANY]), 64, req=name, comm=comm))
                pending_reqs.append(name)
            elif kind == "if" and bound is not None:
                dst = rng.choice(others)
                ops.append(If(lambda e, v=bound: v in e and e[v].src == 0, (Send(dst, 3, 4, comm),), (Send(dst, 4, 4, comm),)))
            else:
                ops.append(Coll(rng.choice(COLLS), comm, root=rng.choice(comms[comm])))
            if pending_reqs and rng.random() < 0.5:
                if waitany and len(pending_reqs) >= 2 and rng.random() < 0.5:
                    ops.append(Waitany(tuple(pending_reqs)))
                    pending_reqs.pop(rng.randrange(len(pending_reqs)))
                else:
                    ops.append(Wait(pending_reqs.pop()))
        for name in pending_reqs:
            ops.append(Wait(name))
        prog[r] = ops
    return prog, comms


_RAW_MODE = {"std": ("send", "std"), "ssend": ("send", "ssend"), "bsend": ("send", "bsend"), "rsend": ("send", "rsend")}
_RAW_IMODE = {"std": "isend", "ssend": "issend", "bsend": "ibsend", "rsend": "irsend"}


def trace_from_execution(prog: Program, matching: Dict[Tuple[int, int], Tuple[int, int]],
                         comms: Dict[int, List[int]],
                         probes: Optional[Dict[Tuple[int, int], Optional[Tuple[int, int]]]] = None) -> Dict[int, List[dict]]:
    """Raw tracer records ({rank: [record]}) of a run of a straight-line program (no If, no
    Waitany) whose receives matched as `matching` (recv key -> send key, every receive
    matched) and whose probes returned what `probes` says (probe key -> the send it saw, None
    or absent for a false flag; explore.Outcome.probes). trace._build loads the result like a
    real trace: ranks are written comm-local, requests get ids, wildcard receives their
    ordinal, sends with a payload their data."""
    probes = probes or {}
    sends_by_key = {}
    for r, ops in prog.items():
        n = 0
        for op in ops:
            if isinstance(op, Send):
                sends_by_key[(r, n)] = op
                n += 1
    out: Dict[int, List[dict]] = {}
    for r, ops in prog.items():
        # each sub-communicator comes from a split of the world that every rank calls, those
        # outside it obtaining MPI_COMM_NULL (trace version 2 records that)
        recs: List[dict] = [{"op": "init", "np": len(prog), "version": 2}]
        for cid, members in comms.items():
            if cid != 0 and r in members:
                recs.append({"op": "comm", "name": "split", "parent": 0, "id": cid, "ranks": list(members), "gord": 0})
            elif cid != 0:
                recs.append({"op": "comm", "name": "split", "parent": 0, "id": -1})
        reqid: Dict[str, int] = {}
        reqkind: Dict[str, Tuple[str, Tuple[int, int]]] = {}
        nreq = nsend = nrecv = widx = nprobe = 0

        def local(comm: int, world: int) -> int:
            return comms[comm].index(world)

        for op in ops:
            if isinstance(op, Send):
                rec = {"op": "send", "mode": _RAW_IMODE[op.mode] if op.req else op.mode, "dst": local(op.comm, op.dst) if isinstance(op.dst, int) else -1,
                       "tag": op.tag, "comm": op.comm, "count": op.bytes // 4, "tsize": 4, "tsig": "MPI_INT"}
                if op.req:
                    rec["req"] = reqid[op.req] = nreq
                    reqkind[op.req] = ("send", (r, nsend))
                    nreq += 1
                if op.data is not None:
                    rec["data"] = op.data
                recs.append(rec)
                nsend += 1
            elif isinstance(op, Recv):
                key = (r, nrecv)
                sk = matching.get(key)
                rec = {"op": "irecv" if op.req else "recv", "src": -1 if op.src == ANY else local(op.comm, op.src), "tag": op.tag,
                       "comm": op.comm, "count": op.cap // 4, "tsize": 4, "tsig": "MPI_INT"}
                if op.src == ANY:
                    rec["anysrc"] = 1
                if op.tag == ANY:
                    rec["anytag"] = 1
                if op.src == ANY or op.tag == ANY:
                    rec["widx"] = widx
                    widx += 1
                st = {"msrc": None, "mtag": None, "mcount": None}
                if sk is not None:
                    s = sends_by_key[sk]
                    st = {"msrc": local(op.comm, sk[0]), "mtag": s.tag, "mcount": s.bytes // 4}
                if op.req:
                    rec["req"] = reqid[op.req] = nreq
                    reqkind[op.req] = ("recv", st)
                    nreq += 1
                else:
                    rec.update(st)
                recs.append(rec)
                nrecv += 1
            elif isinstance(op, Wait):
                q = reqid.get(op.req)
                done = {"req": q}
                kind = reqkind.get(op.req)
                if kind and kind[0] == "recv":
                    done.update(kind[1])
                recs.append({"op": "wait", "kind": "wait", "reqs": [q], "done": [done]})
            elif isinstance(op, Test):
                recs.append({"op": "wait", "kind": "test", "flag": 0, "reqs": [reqid.get(op.req)], "done": []})
            elif isinstance(op, Probe):
                sk = probes.get((r, nprobe))
                rec = {"op": "probe", "kind": "probe" if op.blocking else "iprobe",
                       "src": -1 if op.src == ANY else local(op.comm, op.src), "tag": op.tag, "comm": op.comm,
                       "flag": 1 if sk is not None else 0}
                if op.src == ANY:
                    rec["anysrc"] = 1
                if op.tag == ANY:
                    rec["anytag"] = 1
                if sk is not None:
                    s = sends_by_key[sk]
                    rec.update({"msrc": local(op.comm, sk[0]), "mtag": s.tag, "mbytes": s.bytes})
                elif op.blocking:
                    raise ValueError("trace_from_execution: a blocking probe without a message")
                recs.append(rec)
                nprobe += 1
            elif isinstance(op, Coll):
                rec = {"op": "coll", "name": op.name, "comm": op.comm, "root": local(op.comm, op.root) if op.root is not None else -1,
                       "count": 1, "tsize": 4, "tsig": "MPI_INT"}
                if op.req:
                    rec["req"] = reqid[op.req] = nreq
                    reqkind[op.req] = ("coll", ())
                    nreq += 1
                recs.append(rec)
            else:
                raise ValueError(f"trace_from_execution: {type(op).__name__} is not straight-line")
        recs.append({"op": "finalize"})
        out[r] = recs
    return out


def random_matched_program(rng: random.Random, nranks: int, rounds: Tuple[int, int] = (2, 4),
                           msgs_per_round: Tuple[int, int] = (2, 5), wildcard: float = 0.6,
                           mixed: bool = False, probes: float = 0.0) -> Tuple[Program, Dict[int, List[int]]]:
    """A straight-line program with races: in every round each rank issues its sends
    (standard or buffered, blocking or not) and posts its receives (blocking, or nonblocking
    and waited later in the round), one per message addressed to it, most of them
    wildcards; a round may end with a collective of some shape on comm 0 or on comm 1 (the
    first two ranks). Every message has a receiver.

    mixed=False issues each rank's sends before its receives and waits at the end of the
    round, so an execution that ends with every receive matched exists under infinite
    buffering. mixed=True also uses synchronous and ready sends, interleaves each rank's
    sends and receives in a random order and places each wait anywhere after its request:
    a nonblocking wildcard may then stay pending while later receives complete and the rank
    sends again, which is the shape a re-matched pending receive constrains (docs/model.md
    §2, match nodes). Such a program can deadlock or fail; the caller keeps the ones whose
    explorer finds a clean execution.

    probes > 0 puts, with that probability, a polling loop before a receive, unrolled as a
    straight line: one to three Iprobes of the receive's envelope (the iterations a trace
    records, folded, as failed polls and perhaps a success), or, before a blocking receive,
    sometimes a blocking Probe. Their results, which the program ignores, are what the trace of
    an execution records (explore(record_probes=True)), and the flag flips of docs/model.md §3
    are the alternatives at them."""
    prog: Program = {r: [] for r in range(nranks)}
    comms = {0: list(range(nranks)), 1: [0, 1]}
    nreq = 0
    modes = ["std", "bsend", "isend", "isend"] + (["ssend", "rsend", "isend"] if mixed else [])
    for _ in range(rng.randint(*rounds)):
        msgs = []
        for _ in range(rng.randint(*msgs_per_round)):
            comm = 1 if nranks > 2 and rng.random() < 0.25 else 0
            src, dst = rng.sample(comms[comm], 2)
            msgs.append((src, dst, rng.choice([1, 1, 2]), rng.choice([4, 64, 4096]), comm))
        sends: Dict[int, List[tuple]] = {r: [] for r in range(nranks)}    # (op, request or None)
        recvs: Dict[int, List[tuple]] = {r: [] for r in range(nranks)}
        for src, dst, tag, size, comm in msgs:
            mode = rng.choice(modes)
            if mode == "isend":
                name = f"q{nreq}"; nreq += 1
                sends[src].append((Isend(dst, tag, size, req=name, comm=comm), name))
            else:
                sends[src].append((Send(dst, tag, size, comm, mode=mode), None))
        by_dst: Dict[int, List] = {}
        for m in msgs:
            by_dst.setdefault(m[1], []).append(m)
        for dst, lst in by_dst.items():
            rng.shuffle(lst)
            for src, _, tag, size, comm in lst:
                wsrc = ANY if rng.random() < wildcard else src
                wtag = ANY if rng.random() < wildcard / 3 else tag
                nonblocking = rng.random() < (0.5 if mixed else 0.4)
                if probes and rng.random() < probes:
                    if not nonblocking and rng.random() < 0.25:
                        recvs[dst].append((Probe(wsrc, wtag, comm, True), None))
                    else:
                        recvs[dst].extend((Iprobe(wsrc, wtag, comm), None) for _ in range(rng.randint(1, 3)))
                if nonblocking:
                    name = f"q{nreq}"; nreq += 1
                    recvs[dst].append((Irecv(wsrc, wtag, 4096, req=name, comm=comm), name))
                else:
                    recvs[dst].append((Recv(wsrc, wtag, 4096, comm), None))
        recv_reqs = {name for lst in recvs.values() for _, name in lst if name}

        def wait_for(name: str) -> List[Op]:
            """The wait of a request, after a test of it that fails, with probes, sometimes (a
            receive's polling test)."""
            if probes and name in recv_reqs and rng.random() < probes:
                return [Test(name), Wait(name)]
            return [Wait(name)]

        for r in range(nranks):
            if not mixed:
                ops = sends[r] + recvs[r]
                prog[r].extend(op for op, _ in ops)
                for _, name in ops:
                    if name:
                        prog[r].extend(wait_for(name))
                continue
            # a random merge of the rank's sends and receives, each list in its own order, with
            # each wait at a random later position
            seq, i, j = [], 0, 0
            while i < len(sends[r]) or j < len(recvs[r]):
                if j >= len(recvs[r]) or (i < len(sends[r]) and rng.random() < 0.5):
                    seq.append(sends[r][i]); i += 1
                else:
                    seq.append(recvs[r][j]); j += 1
            body: List[Op] = []
            pending: List[str] = []
            for op, name in seq:
                body.append(op)
                if name:
                    pending.append(name)
                if pending and rng.random() < 0.3:
                    body.extend(wait_for(pending.pop(rng.randrange(len(pending)))))
            for name in pending:
                body.extend(wait_for(name))
            prog[r].extend(body)
        if rng.random() < 0.5:
            comm = 1 if nranks > 2 and rng.random() < 0.3 else 0
            name = rng.choice(COLLS)
            root = rng.choice(comms[comm])
            for r in comms[comm]:
                prog[r].append(Coll(name, comm, root=root))
    return prog, comms
