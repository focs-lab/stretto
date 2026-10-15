"""A tiny abstract-MPI language for idiom twins and for traces under the single-path
assumption. Programs are {rank: [ops]}. Metadata (source, tag, bytes of a received
message, index completed by Waitany) is bound to named variables; `If` branches on
them. Payload never exists in this model: that is the L2 skeleton by construction.

Sends and receives are identified by (rank, ordinal) exactly as in trace.py.
"""
from __future__ import annotations
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


@dataclass(frozen=True)
class Waitany:
    reqs: Tuple[str, ...]
    into: Optional[str] = None      # index variable


@dataclass(frozen=True)
class Coll:
    name: str
    comm: int = 0
    req: Optional[str] = None       # set => nonblocking collective, completed by Wait/Waitany


@dataclass(frozen=True)
class If:
    cond: Callable[[dict], bool]
    then: Tuple[object, ...]
    els: Tuple[object, ...] = ()


@dataclass(frozen=True)
class Meta:
    """Metadata of a completed receive."""
    src: int
    tag: int
    bytes: int


Op = Union[Send, Recv, Wait, Waitany, Coll, If]
Program = Dict[int, List[Op]]


def Isend(dst, tag, bytes=4, req="s", comm=0):
    return Send(dst, tag, bytes, comm, req)


def Irecv(src, tag, cap=4, req="r", comm=0, into=None):
    return Recv(src, tag, cap, comm, req, into)


def program_from_trace(t) -> Program:
    """Straight-line DSL program from a trace (single-path assumption)."""
    from . import trace as T
    prog: Program = {}
    for rank, recs in t.ranks.items():
        ops: List[Op] = []
        for rec in recs:
            raw = rec.raw
            if rec.op == "send":
                nb = raw["mode"] in ("isend", "issend", "ibsend", "irsend")
                mode = {"ssend": "ssend", "issend": "ssend", "bsend": "bsend", "ibsend": "bsend",
                        "rsend": "rsend", "irsend": "rsend"}.get(raw["mode"], "std")
                ops.append(Send(raw["dst"], raw["tag"], raw["count"] * raw["tsize"], raw["comm"],
                                f"q{raw['req']}" if nb else None, mode))
            elif rec.op == "recv":
                ops.append(Recv(raw["src"], raw["tag"], raw["count"] * raw["tsize"], raw["comm"]))
            elif rec.op == "irecv":
                ops.append(Recv(raw["src"], raw["tag"], raw["count"] * raw["tsize"], raw["comm"], f"q{raw['req']}"))
            elif rec.op == "wait":
                kind = raw.get("kind")
                if kind in ("waitany", "testany"):
                    # A Test* that succeeded is modelled as a wait: in the observed run it was
                    # complete at this point; an alternative where it is not is a metadata-
                    # dependent (flag) continuation, i.e. beyond L1.
                    reqs = tuple(f"q{q}" for q in raw.get("reqs", []) if q is not None and q >= 0)
                    ops.append(Waitany(reqs))
                else:
                    for d in raw.get("done", []):
                        if d.get("req") is not None and d["req"] >= 0:
                            ops.append(Wait(f"q{d['req']}"))
            elif rec.op == "coll":
                ops.append(Coll(raw["name"], raw["comm"], f"q{raw['req']}" if raw.get("req") is not None else None))
        prog[rank] = ops
    return prog
