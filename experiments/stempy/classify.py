"""The kinds of stempy's receive alternatives in gatherEvents, for experiments/history_predict.py
--classify.

Every worker sends rank 0 its map's size (one 8-byte MPI_UINT64_T) and then the serialized map,
both with tag 0, and rank 0 takes each with a receive from MPI_ANY_SOURCE. An alternative is
"cross" when the receive would take a message of the other kind than the one it took in the
recorded run (a size where it took a map, or a map where it took a size): the bug of 3.3.5,
issue #292. It is "same" when it would take another worker's message of the same kind (a
reordering of the workers, which by itself leaves every size before its map)."""

SIZE = 8    # bytes of one MPI_UINT64_T: the size message


def alternatives(t, rep):
    out = []
    for f in rep.findings:
        if f.level != "L1" or f.recv is None or f.send is None or f.probe is not None:
            continue
        seen = t.observed_send(t.recvs[f.recv])
        if seen is None:
            continue
        out.append((f, "cross" if (t.sends[f.send].bytes == SIZE) != (seen.bytes == SIZE) else "same"))
    return out
