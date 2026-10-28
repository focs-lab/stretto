"""The kinds of SuperLU_DIST's receive alternatives in the 3D triangular solve, for
experiments/history_predict.py --classify.

The L-solve and U-solve receive loops of the default 3D solve (dForwardSolve3d_newsolve_reusepdgstrs
and dBackSolve3d_newsolve_reusepdgstrs in SRC/double/pdgstrs3d.c) both receive with
MPI_ANY_SOURCE and MPI_ANY_TAG on the 2D grid's communicator, and the L-solve's messages carry the
tags BC_L = 1 and RD_L = 2, the U-solve's BC_U = 3 and RD_U = 4 (SRC/include/superlu_defs.h). An
alternative is classified by the tag of the message the receive took in the recorded run and of
the one it would take:
  cross  one solve's tag and the other's: a receive of one loop would take a message of the other
         solve, which the loop counts and drops (the bug; in every trace so far the L-solve loop
         taking a U-solve message);
  tag    the other tag of the same solve (a broadcast where it took a reduction, or the reverse);
  same   the same tag (another sender's message of the same kind);
  other  a tag outside 1 to 4."""

L, U = {1, 2}, {3, 4}


def kind(seen: int, alt: int) -> str:
    if seen == alt:
        return "same"
    if (seen in L and alt in U) or (seen in U and alt in L):
        return "cross"
    if (seen in L and alt in L) or (seen in U and alt in U):
        return "tag"
    return "other"


def alternatives(t, rep):
    out = []
    for f in rep.findings:
        if f.level != "L1" or f.recv is None or f.send is None or f.probe is not None:
            continue
        seen = t.observed_send(t.recvs[f.recv])
        if seen is None:
            continue
        out.append((f, kind(seen.tag, t.sends[f.send].tag)))
    return out
