"""Kinds of the probe alternatives of NBX loops, for experiments/history_predict.py --classify.

Generic over the NBX family (Issend, then Iprobe(ANY_SOURCE, tag) + Recv until the sends are
done, then MPI_Ibarrier, probing until it completes). A rank's NBX calls on a communicator are
numbered by its Ibarriers there: the call of a record is the number of the rank's Ibarriers on
that communicator that had completed (their completing Test or Wait) before the record. A send
of call k is issued before call k's Ibarrier; a probe of call k lies before that Ibarrier's
completion. A probe alternative is "cross" when the probe and the send it would see lie in
different calls of their ranks, "same" otherwise; a successful probe that could return false
is "unflip". Only probes on tags in the environment variable NBX_TAGS (comma-separated; all tags
when unset) are classified.
"""
import bisect
import os

TAGS = {int(x) for x in os.environ.get("NBX_TAGS", "").split(",") if x.strip()}


def alternatives(t, rep):
    done = {}  # (rank, comm) -> sorted completion indices of its Ibarriers
    for c in t.colls:
        if c.name == "ibarrier":
            k = c.complete_idx if c.complete_idx is not None else c.idx
            done.setdefault((c.rank, c.comm), []).append(k)
    for v in done.values():
        v.sort()

    def call(rank, comm, idx):
        return bisect.bisect_left(done.get((rank, comm), []), idx)

    out = []
    for f in rep.findings:
        if f.level != "L1" or f.probe is None:
            continue
        p = t.probes[f.probe]
        if TAGS and p.tag not in TAGS:
            continue
        if f.send is None:
            out.append((f, "unflip"))
            continue
        s = t.sends[f.send]
        out.append((f, "cross" if call(p.rank, p.comm, p.idx) != call(s.rank, s.comm, s.idx) else "same"))
    return out
