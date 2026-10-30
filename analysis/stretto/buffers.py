"""Buffers of pending operations: when a touch changes data (docs/model.md, section 7).

An operation owns its buffer from its call to its completion, but MPI reads a send buffer and
writes a receive buffer only inside a transfer window of that interval:

- a send whose message the library copies in the call (premise P-B1: a buffered send always,
  a standard or ready send of at most B bytes) reads the buffer during the call;
- any other send reads it after its match and before the completion of its receive or of the
  send itself (P-B2: rendezvous);
- a receive writes it after its match and before its completion (P-B3).

A program access to the buffer, or the transfer window of another operation on overlapping
memory, is harmless when the window ends before it in every execution, changes the data in
every execution when it precedes the window's start in every execution, and in some executions
otherwise. Every claim is about the executions that have the observed matching. Happens-before
is the constraint graph of the observed matching at threshold B (stretto.matching.build_graph):
its guaranteed edges for a claim about every execution, and all its edges, unknown collectives
as barriers, for a claim that an order is possible, as for the alternatives the checker accepts.
The verdicts are judged at the breakpoints of the trace, where the blocking of sends changes, and
the graph only loses edges as B grows, so each is a run of thresholds, found by bisection.

The guard's records place an access in the program order of its rank: a record of a faulting
access is written at the rank's next MPI call, so it stands between the calls that bracket the
access; a send buffer found changed at its completion was written somewhere between the call
and that completion; an overlap names the call whose buffer overlaps a pending one.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .matching import build_graph, match_node
from .trace import Recv, Send, Trace

Node = Tuple[int, int, str]

# verdicts, from harmless to certain
HARMLESS, ORDERED, SOME, EVERY, COPIED = "harmless", "ordered", "some", "every", "copied"

def copied(s: Send, B: Optional[int]) -> bool:
    """P-B1: the library copies the message out of the buffer in the call: a buffered send
    always, a standard or ready one whose message fits the threshold. A synchronous send is
    never taken to be copied: MPICH over UCX read a pending 40-byte Issend's buffer after the
    call with its rendezvous threshold above the message (experiments/results/guard/thresholds.csv)."""
    return s.never_blocks or (not s.rendezvous_always and (B is None or s.bytes <= B))

@dataclass
class Window:
    """Where MPI accesses an operation's buffer: after one of starts in program order or
    happens-before, and before every one of ends (an empty ends: not bounded in the trace)."""
    starts: List[Node]
    ends: List[Node]
    writes: bool
    what: str = ""

@dataclass
class Access:
    """A program access after every node of lo and before every node of hi, in program order
    and happens-before (the guard record's own nodes for a faulting access, with the
    synchronisation of its chain since its last record; the call and the completion for a
    changed send buffer)."""
    lo: List[Node]
    hi: List[Node]
    writes: bool

class Orders:
    """Reachability in the observed constraint graph per threshold, guaranteed edges only
    (strong) or all of them, built on first use and cached with its reach sets."""

    def __init__(self, t: Trace):
        self.t = t
        self._graphs: Dict[Tuple[Optional[int], bool], object] = {}
        self._reach: Dict[Tuple[Optional[int], bool, Node], set] = {}

    def reaches(self, B: Optional[int], strong: bool, a: Node, b: Node) -> bool:
        if a == b:
            return True
        key = (B, strong, a)
        r = self._reach.get(key)
        if r is None:
            g = self._graphs.get((B, strong))
            if g is None:
                g = self._graphs[(B, strong)] = build_graph(self.t, self.t.observed, cut=None, B=B,
                                                            strong_only=strong, thread_orient=False)
            r = self._reach[key] = g.reach_set(a)
        return b in r

def completion(x) -> Optional[Node]:
    """The completion node of x, on the chain that completed it (a request may be completed by
    another thread than the one that started it)."""
    c = x.complete_node()
    if c is not None and x.complete_chain is not None:
        return (x.complete_chain, x.complete_idx, "c")
    return c

def receive_of(t: Trace, s: Send) -> Optional[Recv]:
    rk = next((rk for rk, sk in t.observed.items() if sk == s.key), None)
    return t.recvs.get(rk) if rk is not None else None

def window(t: Trace, x, B: Optional[int]) -> Window:
    """The transfer window of operation x (a Send or a Recv) at threshold B."""
    if isinstance(x, Send):
        if copied(x, B):
            return Window([(x.rank, x.idx, "i")], [(x.rank, x.idx, "c")], False, "copied in the call")
        r = receive_of(t, x)
        ends = [n for n in (completion(x), completion(r) if r is not None else None) if n is not None]
        if r is None:
            return Window([(x.rank, x.idx, "i")], ends, False, "read by rendezvous (no receive took it)")
        return Window([match_node(r)], ends, False, "read by rendezvous after the match")
    s_key = t.observed.get(x.key) if isinstance(x, Recv) else None
    ends = [n for n in (completion(x),) if n is not None]
    if isinstance(x, Recv) and s_key is not None:
        return Window([match_node(x)], ends, True, "written after the match")
    return Window([(x.rank, x.idx, "i")], ends, True, "written before the completion")

def _before(o: Orders, B, strong: bool, ends: List[Node], starts: List[Node]) -> bool:
    return any(o.reaches(B, strong, e, s) for e in ends for s in starts)

def verdict(o: Orders, B: Optional[int], w: Window, a) -> str:
    """How access or window a relates to window w at B: HARMLESS (w ends before a in every
    execution), EVERY (a ends before w starts in every execution, so a's data or a's change is
    what w moves, or w overwrites what a wrote), SOME (either can come first), ORDERED (one comes
    first unless collectives of unknown shape do not synchronise: no claim)."""
    a_starts, a_ends = (a.lo, a.hi) if isinstance(a, Access) else (a.starts, a.ends)
    if w.ends and _before(o, B, True, w.ends, a_starts):
        return HARMLESS
    if a_ends and _before(o, B, True, a_ends, w.starts):
        return EVERY
    if (w.ends and _before(o, B, False, w.ends, a_starts)) or (a_ends and _before(o, B, False, a_ends, w.starts)):
        return ORDERED
    return SOME

def profile(t: Trace, o: Orders, judge: Callable[[Optional[int]], str]) -> List[Tuple[Optional[int], str]]:
    """judge at every breakpoint ({0} U message sizes U {inf}), as (B, verdict). The verdict
    is constant between breakpoints; judge is called at the two ends of each run of the
    breakpoints on which it agrees, found by bisection, since every predicate it is built from
    is monotone in B (the graph loses rendezvous edges as B grows, copied() turns true once)."""
    bps: List[Optional[int]] = [0] + t.message_sizes() + [None]
    out: Dict[int, str] = {}

    def at(i: int) -> str:
        if i not in out:
            out[i] = judge(bps[i])
        return out[i]

    def fill(lo: int, hi: int) -> None:       # bps[lo..hi], both judged
        if at(lo) == at(hi):
            for i in range(lo + 1, hi):
                out[i] = out[lo]
            return
        if hi - lo <= 1:
            return
        mid = (lo + hi) // 2
        at(mid)
        fill(lo, mid)
        fill(mid, hi)
    fill(0, len(bps) - 1)
    return [(bps[i], out[i]) for i in range(len(bps))]

def ranges(prof: List[Tuple[Optional[int], str]]) -> List[Tuple[str, str]]:
    """Runs of one verdict as (range text, verdict): the verdict at breakpoint b holds for B
    in [b, next breakpoint)."""
    out: List[Tuple[str, str]] = []
    i = 0
    while i < len(prof):
        j = i
        while j + 1 < len(prof) and prof[j + 1][1] == prof[i][1]:
            j += 1
        lo, hi = prof[i][0], prof[j + 1][0] if j + 1 < len(prof) else "end"
        if lo == 0 and hi == "end":
            text = "any B"
        elif lo is None:
            text = "B = inf"
        elif lo == 0:
            text = f"B < {hi}" if hi is not None else "finite B"
        elif hi == "end":
            text = f"B >= {lo}"
        elif hi is None:
            text = f"B >= {lo} (finite)"
        else:
            text = f"{lo} <= B < {hi}"
        out.append((text, prof[i][1]))
        i = j + 1
    return out

@dataclass
class Consequence:
    B: str                                   # the thresholds at which data can change ("" if at none)
    profile: List[Tuple[Optional[int], str]] = field(default_factory=list)   # (breakpoint, verdict)
    detail: List[str] = field(default_factory=list)

SEND_TEXT = {COPIED: "harmless: the message is copied in the call (eager), before the write",
             HARMLESS: "harmless: the library has read the message before the write",
             EVERY: "the message carries the write in every execution with the observed matching",
             SOME: "the message carries the write in some executions with the observed matching",
             ORDERED: "the message is read before the write unless collectives of unknown shape do not synchronise"}
RECV_TEXT = {HARMLESS: "harmless: the message was in place before the access",
             EVERY: "the message arrives after the access in every execution with the observed matching: "
                    "it overwrites a write, a read sees the earlier content",
             SOME: "the message may arrive before or after the access (some executions each way)",
             ORDERED: "the order of the message and the access depends on collectives of unknown shape"}

def _summary(prof, texts) -> Consequence:
    harm = [B for B, v in prof if v in (EVERY, SOME)]
    rs = ranges(prof)
    detail = [f"at {r}: {texts[v]}" for r, v in rs]
    B = ", ".join(r for r, v in rs if v in (EVERY, SOME)) if harm else ""
    return Consequence(B, prof, detail)

def access_consequence(t: Trace, o: Orders, x, a: Access) -> Optional[Consequence]:
    """What a program access a to the buffer of the pending operation x does to the data, by
    threshold. None if x is neither a send nor a receive of the model (a collective)."""
    if isinstance(x, Send):
        if not a.writes:
            return None                       # reading a pending send buffer is allowed (MPI 3.0 on)

        def judge(B):
            return COPIED if copied(x, B) else verdict(o, B, window(t, x, B), a)
        prof = profile(t, o, judge)
        cons = _summary(prof, SEND_TEXT)
        if x.never_blocks:
            how = "a buffered send copies its message in the call at every B (P-B1)"
        elif x.rendezvous_always:
            how = "a synchronous send's message may be read after its match at every B (P-B1 does not cover it, P-B2)"
        else:
            how = (f"the {x.bytes}-byte message is read from the buffer in the call at B >= {x.bytes} "
                   f"(copied, premise P-B1) and after the match below (rendezvous, P-B2)")
        cons.detail.insert(0, how)
        return cons
    if isinstance(x, Recv):
        prof = profile(t, o, lambda B: verdict(o, B, window(t, x, B), a))
        cons = _summary(prof, RECV_TEXT)
        cons.detail.insert(0, "a receive writes its buffer between its match and its completion whatever the "
                              "protocol (P-B3), so the threshold does not make the access harmless")
        return cons
    return None

def overlap_consequence(t: Trace, o: Orders, x, y) -> Optional[Consequence]:
    """Two operations on overlapping memory, x pending when y was called: what y's transfer and
    x's do to each other, by threshold. None unless both are sends or receives of the model."""
    if not isinstance(x, (Send, Recv)) or not isinstance(y, (Send, Recv)):
        return None
    if isinstance(x, Send) and isinstance(y, Send):
        return None

    def judge(B):
        wx, wy = window(t, x, B), window(t, y, B)
        v = verdict(o, B, wx, wy)            # HARMLESS: x's transfer ends before y's starts
        return v
    prof = profile(t, o, judge)
    texts = {HARMLESS: "harmless: the pending operation's transfer ends before the new one's starts",
             EVERY: "the new operation's transfer ends before the pending one's starts in every execution "
                    "with the observed matching, so the pending one moves what the new one wrote or the "
                    "new one overwrites what the pending one will write",
             SOME: "the two transfers can interleave (some executions each way)",
             ORDERED: "the order of the two transfers depends on collectives of unknown shape"}
    return _summary(prof, texts)

def guard_position(t: Trace, rec) -> Optional[Tuple[List[Node], List[Node]]]:
    """(lo, hi) of the access of a guard access record: the record itself stands in its chain's
    program order between the calls around the access (the tracer writes it before the chain's
    next record, and in that chain's sequence when it names the chain). With several chains the
    record's at:[s, k] adds the synchronisation of the chain between its last record and the
    fault: an acquire with an ordinal of at most k, which the chain's next record carries, came
    before the access, and a release after the last record with an ordinal above k after it.
    None when several chains share the rank and the record does not say where its chain was."""
    lo, hi = [(rec.rank, rec.idx, "i")], [(rec.rank, rec.idx, "c")]
    if not t.multi:
        return lo, hi
    at = rec.raw.get("at")
    if not at or len(at) < 2:
        return None
    k = at[1]
    recs = t.ranks.get(rec.rank, [])
    events = [r.idx for r in recs if r.op not in ("guard", "overflow", "comm")]
    prev = max((i for i in events if i < rec.idx), default=None)
    nxt = min((i for i in events if i > rec.idx), default=None)
    for (a, ia, b, ib), (kc, ka) in t.hb.kinfo.items():
        if b == rec.rank and ib == nxt and ka <= k:
            lo.append((a, ia, "c"))
        if a == rec.rank and ia == prev and kc > k:
            hi.append((b, ib, "i"))
    return lo, hi

def next_call(t: Trace, rec):
    """The send or receive whose call follows guard record rec on its rank (the blocking call
    whose buffer an overlap record names), or None."""
    recs = t.ranks.get(rec.rank, [])
    nxt = next((r for r in recs[rec.idx + 1:] if r.op not in ("guard", "overflow", "comm")), None)
    if nxt is None:
        return None
    for x in list(t.sends.values()) + list(t.recvs.values()):
        if x.rank == rec.rank and x.idx == nxt.idx:
            return x
    return None

