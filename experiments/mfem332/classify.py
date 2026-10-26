"""The kinds of MFEM 3.3.2's receive alternatives in GroupTopology::Create, for
experiments/history_predict.py --classify.

Create receives each group's row with MPI_Recv(MPI_ANY_SOURCE, 822) and ends with an MPI_Waitall
of the rows it sent; nothing collective separates one call (one ParMesh constructed) from the
next. A receive's call on its rank is therefore the number of waits issued before it from that
closing MPI_Waitall's call site: the site of the first wait after the rank's first receive with
the tag. An alternative is "cross" when the receive and the receive that took the alternative
message in the recorded run lie in different calls of the receiving rank (a row of the other
mesh's Create, the bug fixed by c52c6243b), and "same" when both lie in one call (another master's
row of the same mesh). Receives with another tag are not classified."""
import bisect

TAG = 822


def alternatives(t, rep):
    marks = {}
    for r, recs in t.ranks.items():
        first = next((x.key for x in t.recvs_of(r) if x.tag == TAG), None)
        site = None
        if first is not None:
            k = t.recvs[first].idx
            w = next((x for x in recs if x.idx > k and x.op == "wait"), None)
            site = t.site_label(r, w.idx) if w is not None else None
        marks[r] = [x.idx for x in recs if x.op == "wait" and site is not None and t.site_label(r, x.idx) == site]

    def call(rank, idx):
        return bisect.bisect_left(marks[rank], idx)

    took = {s: r for r, s in t.observed.items()}     # send -> the receive that took it in the run
    out = []
    for f in rep.findings:
        if f.level != "L1" or f.recv is None or f.send is None or f.probe is not None:
            continue
        r = t.recvs[f.recv]
        other = took.get(f.send)
        if r.tag != TAG or other is None:
            continue
        o = t.recvs[other]
        out.append((f, "cross" if call(r.rank, r.idx) != call(o.rank, o.idx) else "same"))
    return out
