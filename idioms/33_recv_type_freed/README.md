# 33 a receive's datatype freed while it is pending (tracer regression)

Ranks: 3 (the third only finalizes). Rank 0 twice commits `MPI_Type_vector(4, 4, 8,
MPI_DOUBLE)`, posts `MPI_Irecv` with it from rank 1 and frees the type at once, as SLATE's
`Tile::irecv` does for a strided tile; the first receive is completed by `MPI_Wait`, the second
by `MPI_Waitall`. Rank 1 sends 16 doubles twice. MPI allows the free: "Any communication that is
currently using this datatype will complete normally" (MPI 4.1, `MPI_TYPE_FREE`).

The library releases the type when the request completes, before the tracer writes the
completion. The tracer before 30 Oct 2026 called `MPI_Get_count` with the handle there and read
freed memory: traced SLATE `gelqf` and `geqrf` runs aborted with `MPI_ERR_TYPE` (17 and 11 of 20).
In a single-threaded run the freed memory is usually intact, so the program runs itself again
with glibc's thread cache off and freed memory overwritten (`GLIBC_TUNABLES`,
`MALLOC_PERTURB_`); under that tracer Open MPI records the first count as unknown and aborts at
the second, MPICH records both as unknown.

Expected: the program prints `ok`, both completions record `mcount` 1, no finding.
