"""The kinds of the probe alternatives at deal.II's ConsensusAlgorithm_NBX request tag, for
experiments/history_predict.py --classify (experiments/dealii8929/run.sh).

In the deal.II of issue 8929 a call of ConsensusAlgorithm_NBX::run() Isends its requests with
tag 12 (tag_request), answers others' requests by MPI_Iprobe(MPI_ANY_SOURCE, 12) until its own
answers are in, calls MPI_Ibarrier, and goes on probing until the barrier completes; nothing
else separates one call from the next on the communicator. A call on a rank therefore begins
with its request sends: the call of any record of a rank is the number of the rank's
Ibarriers on the communicator before the last tag-12 send it issued up to that record (0 for
the first call). A probe alternative is "cross" when the probe and the send it would see lie
in different calls of their ranks (the probe of one call taking a request of the next, the
race of 8929), and "same" otherwise: a flag flip that sees a request of its own call earlier,
or a successful probe that sees another rank's request of the same call. A successful probe
that could return false (an unflip) is "unflip". Findings at other tags or at receives are
not classified."""
import bisect

TAG = 12


def alternatives(t, rep):
    starts = {}            # (rank, comm) -> record indices of the tag-12 sends
    barriers = {}          # (rank, comm) -> record indices of the Ibarriers
    for s in t.sends.values():
        if s.tag == TAG:
            starts.setdefault((s.rank, s.comm), []).append(s.idx)
    for c in t.colls:
        if c.name == "ibarrier":
            barriers.setdefault((c.rank, c.comm), []).append(c.idx)
    for v in list(starts.values()) + list(barriers.values()):
        v.sort()

    def call(rank, comm, idx):
        sends = starts.get((rank, comm), [])
        k = bisect.bisect_right(sends, idx)
        if k == 0:
            return 0
        return bisect.bisect_left(barriers.get((rank, comm), []), sends[k - 1])

    out = []
    for f in rep.findings:
        if f.level != "L1" or f.probe is None:
            continue
        p = t.probes[f.probe]
        if p.tag != TAG:
            continue
        if f.send is None:
            out.append((f, "unflip"))
            continue
        s = t.sends[f.send]
        out.append((f, "cross" if call(p.rank, p.comm, p.idx) != call(s.rank, s.comm, s.idx) else "same"))
    return out
