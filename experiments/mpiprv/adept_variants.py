#!/usr/bin/env python3
"""Two variants of the Adept Kernel's double_stencil27_overlapped (MPI-PRV's example, stencil.c),
made by exact text edits inside that function only (each edit must match exactly once):

  fixed   the two halo sends of an iteration (req1, and req3 on the inner ranks) are completed
          with MPI_Wait right after the two halo receives. The original never completes them,
          and its copy of a1 back into a0 overwrites their buffers (planes 1 and local_n).
  mutant  the fixed variant with all four waits moved after the edge-plane updates, just before
          the copy back: the edge updates then read the halo planes while their receives may
          be pending. This is the error MPI-PRV's report describes, put in on purpose, as the
          positive control of experiments/mpiprv/adept.sh.

usage: adept_variants.py fixed|mutant <stencil.c> > <new stencil.c>"""
import sys

FN = "void double_stencil27_overlapped(unsigned int size){"
INNER = "    if ((world_rank > 0) && (world_rank < (world_size-1))) {\n"
RECV_WAITS = "    MPI_Wait(&req2, MPI_STATUS_IGNORE);\n" + INNER + "\tMPI_Wait(&req4, MPI_STATUS_IGNORE);\n    }\n"
SEND_WAITS = "    MPI_Wait(&req1, MPI_STATUS_IGNORE);\n" + INNER + "\tMPI_Wait(&req3, MPI_STATUS_IGNORE);\n    }\n"
COPY_BACK = "    for (i = 1; i < local_n+1; i++) {\n      for (j = 1; j < n+1; j++) {\n        for (k = 1; k < n+1; k++) {\n          a0[i*size*size+j*size+k] = a1[i*size*size+j*size+k];"


def once(text, old, new):
    if text.count(old) != 1:
        sys.exit(f"adept_variants: expected one occurrence of {old[:60]!r}, found {text.count(old)}")
    return text.replace(old, new)


def main():
    mode, path = sys.argv[1], sys.argv[2]
    src = open(path).read()
    a = src.index(FN)
    b = src.index("\nvoid ", a + len(FN))
    fn = src[a:b]
    fn = once(fn, RECV_WAITS, RECV_WAITS + SEND_WAITS)
    if mode == "mutant":
        fn = once(fn, RECV_WAITS + SEND_WAITS, "")
        fn = once(fn, COPY_BACK, RECV_WAITS + SEND_WAITS + COPY_BACK)
    elif mode != "fixed":
        sys.exit("usage: adept_variants.py fixed|mutant <stencil.c>")
    sys.stdout.write(src[:a] + fn + src[b:])


if __name__ == "__main__":
    main()
