# SLATE: OpenMP tasks issuing MPI under `MPI_THREAD_MULTIPLE`

Target of the thread hunt of 30 Oct 2026 (the ledger's read-only candidate "DLA-Future and SLATE
(tags reused by concurrent tasks)", [docs/findings.md](../../docs/findings.md)). SLATE
(icl-utk-edu/slate, master 878439b, two build-system commits past release 2025.05.28) runs every
distributed routine as a graph of OpenMP tasks with `depend` clauses, and the tasks call MPI
themselves; the tester and the library require `MPI_THREAD_MULTIPLE` (`test/test.cc:654-660`).
Messages between tasks are told apart by tags only: one communicator (the matrix's) carries
every tile broadcast and row swap, and a comment states the scheme ("Communication of the jth
tile column uses the MPI tag j. So, the data dependencies protect the corresponding MPI tags",
`src/getrf.cc:68-69`). A race needs two tasks that the dependences leave unordered and that use
one tag between the same two ranks.

CPU only (no GPU backend), Ubuntu's OpenBLAS (`Dockerfile`), Open MPI 4.1.6 and MPICH 4.2.0 of
the experiment image, gcc 13, 4 ranks of 4 OpenMP threads on CPUs 8-15,48-55 of a-p13.
`build.sh` builds the tester (about 25 minutes with 8 jobs), `build-lib.sh` the library alone,
plain and patched, for one MPI, and the driver `wide_lu.cc`.

## Finding: `getrf` with tournament pivoting on a wide matrix (confirmed)

**The defect** (line numbers of 878439b; the code is the same in releases 2024.05.31 to
2025.05.28; it came with 91df11d, "Fix bug and performance of getrf_tntpiv", 27 Nov 2023).
`impl::getrf_tntpiv` (`src/getrf_tntpiv.cc`, `slate::getrf` with `MethodLU::CALU`) gives each
group of tasks a tag range for `internal::permuteRows`, which swaps rows in tile column j with
tag `tag_base + j` (`src/internal/internal_swap.cc:228`):

- columns to the right of panel k: lookahead tasks `tag_j = j + A_mt` (line 250), the trailing
  update `tag_kl1 = k+1+lookahead + A_mt` as base (line 294, so `j + A_mt` for column j), and the
  trailing broadcast of A(k, j) with `j + A_mt` too (line 320);
- columns to the left (the "pivot to the left" task, lines 342-358): base `A_mt * 2`, so
  `2 A_mt + j` for column j <= k-1.

For a square matrix the two ranges are [A_mt+1, 2 A_mt-1] and [2 A_mt, 3 A_mt-2]. For a wide one
(A_nt > A_mt) the right columns reach `A_nt-1 + A_mt >= 2 A_mt`: left column j and right column
j + A_mt use one tag. The tasks are not ordered: the left one depends on `diag[k]`, `column[0]`
and `column[k-1]` (lines 342-344), the trailing ones on `diag[k]` or `column[k]` and
`column[k+1+lookahead]`, `column[A_nt-1]` (lines 289-291, 311-313), so the left swap of step k
runs beside the trailing updates of steps k-1, k, k+1. Both apply the same panel's pivots or
neighbouring ones, and when the grid's column count q divides A_mt the two columns live on the
same process column, so the same ranks exchange messages of the same size on one tag (the
non-root ranks' `MPI_Send`/`MPI_Recv` of the pivot rows, the root's `MPI_Irecv`/`MPI_Isend`,
`internal_swap.cc:305-363, 405-408`). MPI may match them across the two tasks (MPI 4.1 §4.5,
§12.6.1): rows of column j are swapped with rows of column j + A_mt, a silently wrong factor; or
the trailing broadcast's tile (80 000 bytes) meets a row receive (36 000): `MPI_ERR_TRUNCATE`.

**How it was found.** By reading the tag scheme for pairs of unordered tasks with overlapping
tag ranges (sites shown safe below), then a run. The tester checks `getrf` only through
`getrs`, which needs a square matrix (`--dim 1000x2000 getrf_tntpiv --check y` fails with
"Error check 'orig.mt() == orig.nt()'"), so a wide factorisation has no oracle there.
`wide_lu.cc` fills A from its global indices, factors it, gathers the factors and prints
`|| P A - L U ||_max / (|| A ||_max m)`.

**Series** (`series.sh`, [series.csv](series.csv), Open MPI, 40 runs per cell;
[series-mpich.csv](series-mpich.csv), MPICH launched by Open MPI's `mpirun` as in the RUNBOOK,
20 runs per cell): 4 ranks, 4 threads, nb 100, lookahead 1, the plain library and the one with
[tntpiv-tags.patch](tntpiv-tags.patch) in turn; a run is wrong when the residual exceeds 1e-12
(correct runs give 5.4e-17 to 5.9e-17, wrong ones 0.33 to 6.5 with Open MPI, 0.60 to 5.6 with
MPICH).

| cell | Open MPI plain | patched | MPICH plain | patched |
|---|---|---|---|---|
| 1000x2000 (mt 10, nt 20), grid 2x2 | 40 of 40 wrong | 0 of 40 | 20 of 20 wrong | 0 of 20 |
| 1000x2000, grid 4x1 | 28 of 40 wrong | 0 of 40 | 20 of 20 wrong | 0 of 20 |
| 1000x1000, grid 2x2 (square) | 0 of 40 | 0 of 40 | 0 of 20 | 0 of 20 |
| 1000x2000, grid 2x2, 1 thread | 0 of 40 | | 0 of 20 | |

Both MPIs grant `MPI_THREAD_MULTIPLE` (the driver prints `provided 3`). The wrong runs exit
normally; no untraced run crashed. Further controls, 10 runs each with the plain library (Open
MPI, 4 threads, 2x2): 900x2000 (A_mt = 9, q = 2 does not divide it, so the colliding columns
live on different ranks) 0 wrong, against 10 of 10 at 1000x2000; partial pivoting
(`MethodLU::PartialPiv`, whose tags are the global column index and whose left swaps are chained
by `column[0]`) at 1000x2000, 0 wrong; CALU on a tall 2000x1000, 0 wrong. (The series ran a
first version of `wide_lu.cc` whose residual read out of bounds for m > n; it was corrected
before the tall control, and the arithmetic for m <= n is the same.)

**The fix** ([tntpiv-tags.patch](tntpiv-tags.patch), one line): start the left swaps' tags at
`A_mt + A_nt`, beyond the right columns' range. For a square matrix the tags are unchanged
(`A_mt + A_nt = 2 A_mt`).

**Traces** (`traced.sh`, [traced.csv](traced.csv), 5 traced runs of each library at 1000x2000 on
2x2). Stretto reports nothing: every trace is degraded `threads-tasks` (MPI inside explicit
OpenMP tasks, which are not chains; docs/threads.md §9), so only the thread-level check runs.
`overlap.py` scans a trace for operations of one envelope (direction, communicator, peer, tag)
on two threads of a rank whose [start, completion] intervals overlap, leaving out operations
completed by another thread or freed. Every operation `permuteRows` starts it completes in the
same call with no synchronisation in between, so for these an overlap means the two calls are
concurrent whatever the task graph says. Plain: 16 to 33 such pairs per trace (in the trace
read in detail, 26 pairs, all in `permuteRows`, tags 20 to 25, inside the collision range
[2 A_mt, A_mt + A_nt - 1] = [20, 29], with equal counts), 4 runs wrong and 1 aborted
(`MPI_ERR_TRUNCATE` in `MPI_Wait`; the report shows a row receive of 36 000 bytes with tag 20
next to an unconsumed 80 000-byte send with tag 20). Patched: 0 pairs, 5 of 5 correct. A run
of tags 21-29 on ranks 2 and 3 can only be the trailing update of step 9 (columns 11, 13, ...,
19), and it overlaps a run of tags 21-27 (left columns 1 to 7), which is the pair the fix
separates. Of all 19 traced plain runs (these 5, 5 of an earlier version of the scan, 8 with
their logs kept, 1 read in detail), 3 aborted with `MPI_ERR_TRUNCATE` and 16 gave wrong
factors; untraced none aborted (the tracer's issue lock changes the interleavings).

**Upstream.** Not found in the issue tracker (searched 30 Oct for tntpiv, CALU, getrf, tag,
hang): the only CALU issue, #70 (accuracy on GPUs, square matrix, closed Jan 2024), is another
defect. Report drafted, not sent.

## Sites shown safe (by reading, and by the sweep below)

- `getrf` with partial pivoting: every message of tile column j has tag j (panel `listBcast`,
  lookahead `permuteRows` and `tileBcast`, trailing `permuteRows` and `listBcast`, left
  `permuteRows` with base 0, `getrf.cc:90-188`), and every task that touches column j depends on
  `column[j]` or follows one that does; the left swaps are chained by `column[0]`. Holds for
  wide matrices too.
- `potrf`, `herk`, `gemmC`, `hemmC`, `work_trsm`: the broadcasting tasks are chained
  (`bcast[k]` after `bcast[k-1]`, panels by `column[k]`/`row[k]`), so tag 0 reused across steps
  is ordered on every rank. `gemmA`: broadcast of step k+la (tag k+la) and reduction of step k
  (tag k) run together with different tags.
- `geqrf`, `gelqf`, `unmqr` and the triangle-triangle kernels: `ttmqr`'s tags are tag+k for
  column k of C, the global column index (`internal_ttmqr.cc:155, 189, 225, 277`); concurrent
  calls cover disjoint column ranges (lookahead `j <= k+la`, trailing from `k+1+la`), the panel
  uses tag 0 and no `ttmqr` touches column 0.
- `getrf_nopiv`: the lookahead `tileBcast(k, j)` (tag j) and the panel's row broadcast (tags
  i = k+1, ...) share tags and are unordered, but one runs inside process column j mod q and the
  other inside process row i mod p; on SLATE's 2D block-cyclic grids the two sets meet in one
  rank, which receives the two tiles from different sources. A custom distribution
  (`tileRank` lambdas) could break this; not tried.
- `he2hb`: the partial-sum exchanges of rows i and i+1 run as concurrent tasks (lines 433-445)
  with tags i and i+1 chosen by rank order, distinct per row and direction.
- Collectives on the matrix communicator from tasks: the pivot `MPI_Bcast` of `getrf` and
  `getrf_tntpiv` (`getrf.cc:115`, `getrf_tntpiv.cc:184`) and `MPI_Comm_create_group` of the
  panel communicators (`internal_comm.cc:37`, with tag k) are in panel tasks, which are chained.
- Not examined: the build option `SLATE_HAVE_MT_BCAST` (`INSTALL.md`; not on by default), which
  turns `listBcastMT` into a `taskloop`; in `he2hb` it would broadcast one tile (i0, k) to several
  sets concurrently (`he2hb.cc:262-268`). `ttmqr`'s `MPI_Isend` + `MPI_Request_free` followed by
  an `MPI_Irecv` into the same tile (`internal_ttmqr.cc:155-189`) is a buffer reuse, outside the
  thread analysis.

**Sweep** (`sweep.sh`, [sweep.csv](sweep.csv)): 39 tester routines (BLAS 3, Cholesky, LU in
three variants, QR/LQ, least squares, Hermitian indefinite and eigenvalue, SVD, band), square
800 with nb 100 on 2x2, 4 threads. Untraced, all pass their checks. Traced: 0 overlapping pairs
in every trace once operations completed by another thread (the `ttmqr` tasks; 49 in `geqrf`,
131 in `ge2tb`) or freed (76 and 159) are left out; with them, 3 to 366 pairs in 9 routines.
Stretto: 31 traces degraded `threads-tasks` (7 of them also `threads-handoff`), 0 findings
except in the two aborted traces below; 8 traces (`hetrf`, `hesv`, `genorm`, the band routines)
have no point-to-point message at all and are analysed completely, 0 findings. The tester
binary of the sweep was linked against the patched library, which for square matrices uses the
same tags.

## What the tool did and did not do on these traces

- **Gap: MPI in OpenMP tasks.** Every SLATE trace with messages is degraded `threads-tasks`
  (`analysis/stretto/chains.py:116`, `report.py:242`): no alternative is predicted and the
  observed thread classes are skipped (`threads.observed_findings` with `tasks=True`), even on
  traces in which the race happened. The physical overlap that `overlap.py` reads needs no
  happens-before and found it in every plain trace; an observed class from it would cover such
  programs where the overlapping operations are posted and completed within one call.
- **Defect, fixed 30 Oct: the tracer aborted traced `gelqf` and `geqrf` runs.** Traced `gelqf`
  and `geqrf` runs (tester, square 800, nb 100, 2x2, 4 threads, Open MPI) ended in
  `MPI_ERR_TYPE: invalid datatype` "in MPI_Get_count" (17 and 11 of 20 runs), untraced never.
  SLATE frees a strided tile's datatype right after posting the receive (`Tile.hh:1104-1110`),
  which MPI allows; the library keeps the type until the request completes and releases it
  there, and the tracer's `status_fields` then asked `MPI_Get_count` about the freed handle.
  Checked by marking, in a diagnostic build, receives whose type the program freed while they
  were pending: in each of 3 aborted runs the aborting process's last such completion entered
  `MPI_Get_count` and never returned. A single-threaded program ([typefree.c](typefree.c))
  usually reads intact freed memory; with glibc's thread cache off and `MALLOC_PERTURB_` it
  records an unknown count, and the program's own `MPI_Get_count` on the freed handle fails.
  The tracer now keeps the type's size from the posting and computes the count from the bytes
  the status reports; with it 20 of 20 traced runs of each routine pass. Regression idiom 33.
- **Chain names.** The OpenMP threads that run SLATE's tasks appear as chains named `u` of kind
  `pthread` (untracked), not as `0/p.j`: libgomp runs deferred tasks in the region's closing
  barrier, after the outlined function has returned and the tracer's `omp_tramp` has restored
  the pool thread's chain (`tracer/mpitrace.c:2830-2850`; a reading of the code, not checked
  further). Their synchronisation is then not recorded.

## Files

`Dockerfile` (CMake, OpenBLAS, gdb on top of stretto-exp:24.04), `build.sh` (tester),
`build-lib.sh` (library, plain and patched, and the driver, per MPI), `wide_lu.cc` (driver and
oracle), `series.sh`, `traced.sh`, `sweep.sh`, `overlap.py`, `sites.sh` (call sites by gdb's
`info symbol`), `typefree.c`, `tntpiv-tags.patch` (against 878439b, `git apply` or `patch -p1`).
