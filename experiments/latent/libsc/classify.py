"""Kinds of the probe alternatives of libsc's sc_notify (notify2 driver), for
experiments/history_predict.py --classify.

Tags (sc_mpi.h): SC_TAG_FIRST = 's' + 'c' = 214, so SC_TAG_NOTIFY_NBX = 220,
SC_TAG_NOTIFY_NBXV = 221, SC_TAG_NOTIFY_NARY = 260 (+ level, up to 291).

NBX (sc_notify_payload_nbx): a call on a rank ends when its MPI_Ibarrier completes (the
MPI_Test that returns true ends the loop), so the call of a record is the number of the
rank's Ibarriers completed before it.

NARY (sc_notify_recursive_nary): each call runs its levels from the deepest (depth - 1)
up to 0, tag 260 + level; a rank's call therefore advances whenever the level of its next
NARY record (send or probe) is larger than that of the one before.

A probe alternative is "cross" when the probe and the send it would see lie in different
calls (the race between consecutive calls), "same" otherwise, "unflip" for a successful
probe that could have returned false, "other" at any other tag."""
import bisect

NBX, NBXV, NARY = 220, 221, 260


def alternatives(t, rep):
    done = {}
    for c in t.colls:
        if c.name == "ibarrier" and c.completed:
            done.setdefault(c.rank, []).append(c.complete_idx if c.complete_idx is not None else c.idx)
    for v in done.values():
        v.sort()

    nary = {}
    per = {}
    for s in t.sends.values():
        if NARY <= s.tag < NARY + 32:
            per.setdefault(s.rank, []).append((s.idx, s.tag - NARY))
    for p in t.probes.values():
        if NARY <= p.tag < NARY + 32:
            per.setdefault(p.rank, []).append((p.idx, p.tag - NARY))
    for rank, lst in per.items():
        lst.sort()
        call, prev = 0, None
        for idx, lev in lst:
            if prev is not None and lev > prev:
                call += 1
            nary[(rank, idx)] = call
            prev = lev

    def call_nbx(rank, idx):
        return bisect.bisect_left(done.get(rank, []), idx)

    out = []
    for f in rep.findings:
        if f.level != "L1" or f.probe is None:
            continue
        p = t.probes[f.probe]
        if f.send is None:
            out.append((f, "unflip"))
            continue
        s = t.sends[f.send]
        if p.tag in (NBX, NBXV) or (p.tag == -1 and s.tag in (NBX, NBXV)):
            k = "cross" if call_nbx(p.rank, p.idx) != call_nbx(s.rank, s.idx) else "same"
        elif NARY <= s.tag < NARY + 32 and (p.rank, p.idx) in nary and (s.rank, s.idx) in nary:
            k = "cross" if nary[(p.rank, p.idx)] != nary[(s.rank, s.idx)] else "same"
        else:
            k = "other"
        out.append((f, k))
    return out
