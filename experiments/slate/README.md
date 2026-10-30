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
2x2; the tracer before 30 Oct). Stretto reports nothing: every trace is degraded `threads-tasks`
(MPI inside explicit OpenMP tasks, which were not chains; docs/threads.md §9), so only the
thread-level check runs.
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

**Traces since the tracer follows tasks** (docs/threads.md §12; `traced_cells.sh`,
[traced-cells.csv](traced-cells.csv), 5 traced runs per cell and 8 more of the plain 2x2 cell,
Stretto's report and `finding_sites.py`, which names the function of each finding's receive).
Each task is a chain ordered by its creation, dependences, taskwaits and barriers, and the race
is an L1 concurrent swap or steal of two tasks' receives. Remeasured 30 Oct with the tracer that
records lock events and the analysis with level L1+L and the L1 fix of the same day (threads.md
§13; main 5fdd932; CPUs 0-5,40-45 of a-p13, so 16 threads on 12 processors):

| cell (4 ranks, 4 threads, nb 100) | runs (factor) | traces with findings | L1 findings per trace | L1+L | in `permuteRows` |
|---|---|---|---|---|---|
| 1000x2000, 2x2, plain (13 runs) | 11 wrong, 2 aborted | 12 of 13 | 1 to 73 (1a in 5) | 0 | all but one, tags 20-27 |
| 1000x2000, 4x1, plain | 5 wrong | 4 of 5 | 40 to 85 | 0 | all |
| 1000x2000, 2x2 and 4x1, patched | 10 right | 0 of 10 | 0 | 0 | |
| 1000x1000 (square), 900x2000, partial pivoting, plain | 15 right | 0 of 15 | 0 | 0 | |

No trace has an L1+L finding: the tasks' critical sections (`LockGuard`, `critical(slate_mpi)`)
are recorded, and reordering them adds no alternative. The 1a findings are the trailing
broadcast's 80 000-byte tile taken by a 36 000-byte row receive, the `MPI_ERR_TRUNCATE` of the
aborted runs. The one L1 finding outside `permuteRows` is in an aborted run, at the tile receive
of that broadcast (`Tile::irecv`, tag 27), the other end of the same collision. Traces without a
finding: one aborted 2x2 run, degraded `reconstruction` (its last receive's size disagrees with
the reconstructed message), and one wrong 4x1 run, degraded `threads-handoff`, as is one square
control. Those handoffs are a tracer defect (below), not SLATE's: `permuteRows` completes each
request in the task that posted it. 4 wrong 2x2 traces have an observed `2-buffering` (the
observed matching judged infeasible; one of the 5 earlier 2x2 traces) and fewer L1 findings (1
to 22); in another such trace, of a diagnostic run, the cycle runs through four tasks on ranks 2
and 3 that exchange rows (35 200 bytes, tags 21 and 23) with each other. Not diagnosed.

Before these changes (the tracer and analysis of 30 Oct morning, CPUs 8-15,48-55) the same cells
gave: 2x2 plain 4 wrong and 1 aborted, findings in 4 of 5 (4 to 86); 4x1 plain 3 wrong and 2
right, 5 of 5 (7 to 105, two 1a); 8 more 2x2 runs, all with a right factor, 37 to 90 findings
each; 0 in the 25 patched and control traces. What changed: no traced run now has a right
factor (0 of 18 plain, against 10 of 18), so the claim that Stretto predicts the race from runs
in which it did not happen rests on the earlier traces; plain traces with findings 16 of 18
(17 of 18). The traced share of wrong runs varies between sessions (above 7 of 10, later 0 of
52, now 16 of 18 with 2 aborted; untraced 10 of 10 wrong). Replay of the 1a alternative of a
right run ([replay.csv](replay.csv), 3 replays, 3 controls): not confirmed; one replay ended in
`MPI_ERR_TRUNCATE` with its pins unmatched and its synchronisation diverged, two at a gate
timeout, and every control gave a wrong factor on its own. The tasks take locks
(`LockGuard` on tile nodes, `critical(slate_mpi)`) whose order varies between runs.

**Replays in the trace's lock order** (milestone E, [docs/threads.md](../../docs/threads.md)
section 14; `replay.sh`; tracer and analysis of main 5fdd932). The traced runs record their lock
order, and each replay and control gates it; with the trace's 4 threads per rank the tasks waiting
at gates took every thread and the control starved (by hand: 4 of 4 threads at gates), so the
replays and controls run with 8 (`REPLAY_THREADS=8`; a task's name does not depend on the team).
All 9 traced runs of this session ([traced-e.csv](traced-e.csv), 2x2, 1000x2000) ended with a
wrong factor (5) or `MPI_ERR_TRUNCATE` (4): every recorded run already shows the race.

| sample | replays | every pin taken, no divergence | gate timeout | controls reproducing the run (pins, no divergence, wrong factor) | both |
|---|---|---|---|---|---|
| 6 L1 1f alternatives of 3 traces, 3 times each ([replay-e.csv](replay-e.csv)) | 18 | 15 | 3 (8 of 8 threads at gates) | 13 (2 timed out, 3 `MPI_ERR_TRUNCATE`) | 11 |
| 2 L1 1a alternatives of one trace, 3 times each ([replay-1a.csv](replay-1a.csv)) | 6 | 0 | 6 | 6 | 0 |

The 1f alternatives are realised: the two tasks' row receives take each other's messages, and in
11 runs the control reproduced the recorded run as well. Every replay ended with a wrong factor,
as the recorded runs and their controls did, so no consequence tells the alternative from the run.
The 1a replays (the 80 000-byte tile into a 36 000-byte row receive) all stopped at a lock gate of
a task waiting for a task that never started, with one thread at a gate (not a starved team; not
diagnosed). Not confirmed; the race itself is confirmed by the series and the fix above.

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
  an `MPI_Irecv` into the same tile (`internal_ttmqr.cc:155-189`) is a buffer reuse; examined 30
  Oct with buffer ranges (`STRETTO_BUFS=1`, docs/model.md section 7): in a traced `geqrf` run the 84
  pairs of a freed tile send and the receive of that tile are not ordered, and harmless at every
  eager threshold, since the peer sends the tile back only after receiving it.

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

With the tracer following tasks ([sweep-d.csv](sweep-d.csv), remeasured 30 Oct with lock events,
level L1+L and the L1 fix, main 5fdd932): all 39 routines pass traced and untraced (`gelqf` and
`geqrf` included), 31 traces are analysed completely and 8 are degraded `threads-handoff`
(`gesv`, `geqrf`, `gelqf`, `gels`, `unmqr`, `he2hb`, `ge2tb`, `svd`), 0 findings in every trace,
none at L1+L, 0 overlapping pairs. The earlier run (before lock events) had 30 complete and 9
degraded: `gesv_tntpiv` was degraded then and is complete now. Whether these
handoffs are SLATE's (`ttmqr`) or the tracer defect below was not checked; in `gesv` the pattern
is the defect's (two tasks, each named as completing the other's request).

## What the tool did and did not do on these traces

- **Gap: MPI in OpenMP tasks, closed 30 Oct.** Until then every SLATE trace with messages was
  degraded `threads-tasks` (`analysis/stretto/chains.py`, `report.py`): no alternative was
  predicted and the observed thread classes were skipped, even on traces in which the race
  happened; `overlap.py`, which needs no happens-before, located it. Since the tracer follows
  tasks as chains (docs/threads.md §12) the race is predicted at L1 from clean traces (above).
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
- **Chain names, fixed 30 Oct.** The OpenMP threads that ran SLATE's tasks appeared as chains
  named `u` of kind `pthread` (untracked): libgomp runs deferred tasks in the region's closing
  barrier, after the outlined function has returned and the tracer's `omp_tramp` has restored
  the pool thread's chain. Each task now runs on a chain of its own, `0~k`.
- **Defect, open: request handles reused across threads.** In a degraded square control of 30 Oct
  (`threads-handoff`, "a request of chain 1.10 completed by chain 1.11") task 10 posts an
  `MPI_Isend` (request id 24) and enters `MPI_Waitall` on it; task 11 then posts an `MPI_Isend`
  (id 27) and its `MPI_Waitall` is recorded as completing id 24, and task 10's wait ends with an
  unknown request (`null`). Open MPI completed and freed task 10's request inside its wait and
  gave the same handle to task 11 before the tracer's wrapper in task 10 had dropped its entry;
  `req_find_locked` (`tracer/mpitrace.c`), finding no entry for that handle with task 11's slot,
  returns the oldest one with the handle, task 10's. The mechanism is read from the records and
  the code, not checked in a debugger. 1 of 5 diagnostic square runs shows it; 2 of the 35
  traces of the cells were degraded with the same pattern (two chains, each named as completing
  the other's request; their traces were deleted before this was read).

## Files

`Dockerfile` (CMake, OpenBLAS, gdb on top of stretto-exp:24.04), `build.sh` (tester),
`build-lib.sh` (library, plain and patched, and the driver, per MPI), `wide_lu.cc` (driver and
oracle), `series.sh`, `traced.sh`, `traced_cells.sh` (traced cells with Stretto's report),
`finding_sites.py` (functions of the findings' receives), `sweep.sh`, `overlap.py`, `sites.sh`
(call sites by gdb's `info symbol`), `typefree.c`, `tntpiv-tags.patch` (against 878439b, `git
apply` or `patch -p1`);
`replay.sh` (replays in the lock order); results: `series*.csv`, `traced.csv`, `traced-cells.csv`,
`sweep.csv`, `sweep-d.csv`, `replay.csv`, `traced-e.csv`, `replay-e.csv`, `replay-1a.csv`.
