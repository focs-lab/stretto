"""The kinds of ph.x's receive alternatives in elph_prt, for experiments/history_predict.py
--classify.

With LSDA, elph_prt (PHonon/PH/elphon.f90) has the root of each pool that holds spin ispn of the
requested k point send the ionode el_ph_mat with tag 4*nproc_image and the two eigenvalue
vectors with tag 6*nproc_image, and the ionode receives each spin's three messages from
MPI_ANY_SOURCE with those tags (with the fix, tags 4*nproc_image + ispn - 1 and
6*nproc_image + ispn - 1). An alternative is "cross" when a receive of elph_prt would take the
message of another pool than the one it took it from in the recorded run: with one pool per
spin, the other spin's data (the bug). It is "same" when it would take another message of the
same pool, and "other" when the receive is not elph_prt's (none in the traces so far). The
image is the whole of MPI_COMM_WORLD here (one image), so nproc_image is the number of ranks."""


def alternatives(t, rep):
    n = len(t.ranks)
    tags = {4 * n, 4 * n + 1, 6 * n, 6 * n + 1}
    out = []
    for f in rep.findings:
        if f.level != "L1" or f.recv is None or f.send is None or f.probe is not None:
            continue
        seen = t.observed_send(t.recvs[f.recv])
        if seen is None:
            continue
        if seen.tag not in tags:
            out.append((f, "other"))
        else:
            out.append((f, "cross" if t.sends[f.send].rank != seen.rank else "same"))
    return out
