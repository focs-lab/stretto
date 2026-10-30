# DLA-Future: pika tasks issuing MPI under `MPI_THREAD_MULTIPLE`

Target 1 of the second thread hunt of 30 Oct 2026 (the ledger's read-only candidate
"DLA-Future (tags reused by concurrent tasks)", [docs/findings.md](../../docs/findings.md)).
DLA-Future (eth-cscs/DLA-Future, master 09fe442 of 19 Oct 2026, after release 0.10.0) runs every
distributed algorithm as pika tasks; the library requires `MPI_THREAD_MULTIPLE`
(`src/init.cpp:393`). CPU only: pika 947cf61 (0.34.0) with MPI, Umpire v2024.07.0, blaspp and
lapackpp v2024.10.26, Ubuntu's OpenBLAS, Boost, hwloc, fmt and spdlog (`Dockerfile`), Open MPI
4.1.6, gcc 13, a-p13 CPUs 0-5,40-45. `build.sh` builds the dependencies, DLA-Future's library,
its matrix unit test and the redistribution miniapp, and the driver `copy_check.cpp` twice
(plain headers and with `copy-tags.patch`; the fix is header-only). Not built against MPICH (it
would need pika and DLA-Future again).

## How DLA-Future orders its MPI calls

Every MPI call goes through a `CommunicatorPipeline` (`include/dlaf/communication/
communicator_pipeline.h`): `exclusive()` grants the communicator to one task at a time in
submission order, `shared()` to any number of tasks at once. The access is released as soon as
the call that posts the operation returns (`MPICallHelper`, `include/dlaf/sender/
transform_mpi.h`), so exclusive accesses order the postings, and shared ones leave them in
whatever order the tasks become ready. Each `CommunicatorGrid` holds round-robin pipelines on
cloned communicators (`src/communication/communicator_grid.cpp:64-75`), and an algorithm takes
a sub-pipeline, which is sequenced against earlier and later users of its parent. So two
algorithms never share a communicator concurrently, collectives (all through `exclusive()`) are
ordered, and a race needs two `shared()` operations of one algorithm with one tag between the
same two ranks.

## Finding: `matrix::copy` between distributions (confirmed)

**The defect** (`include/dlaf/matrix/copy.h:215-226`, since PR #933, "Matrix Data Reshuffling",
26 Jul 2023, release 0.2.0; unchanged in 0.10.0 and master). `copy(src, dst, grid)` retiles both
matrices to the smaller tile size and sends every tile whose owner changes with
`schedule_send(mpi_chain.shared(), dst_rank, tag(ij), ...)`, receiving with
`schedule_recv(mpi_chain.shared(), src_rank, tag(ij), ...)`, one task per tile, none depending
on another. The comment says the tag is "a function of local tile index"; the code computes the
local index `source_ij_lc` and does not use it:

    const LocalTileIndex source_ij_lc{ij.row() / size.rows(), ij.col() / size.cols()};
    const SizeType ld = dlaf::util::ceilDiv(dist.nrTiles().rows(), to_SizeType(size.rows()));
    return to_int(ij.row() + ij.col() * ld);

With global row indices up to `nrTiles().rows() - 1` and `ld = ceil(mt / P)`, tiles (i + d ld,
j) and (i, j + d) get the same tag whenever the grid has P > 1 rows. They are sent between the
same two ranks when both distributions place them so (for example a P x 1 grid with `ld` a
multiple of P, or a 3 x 2 grid with mt = 9); then the two sends and the two receives are
pending together and MPI matches them in posting order (MPI 4.1 §4.5), which is the order in
which the tasks found their tiles ready, independently on the two ranks. A tile receives
another tile's data, silently when the sizes agree.

**Driver** (`copy_check.cpp`): sets the source with `matrix::util::set` (one task per tile),
optionally keeps each source tile busy for a random 0-200 µs (the computation that would have
produced it), copies, and compares every destination element with its value (`row * 100000 +
col`); it also counts the colliding pairs from the two distributions and copy.h's formula. The
first wrong element of a run names the global index whose value it holds: in the first test run
on 2x1, element (320, 0), of tile (5, 0), held (64, 64), the value of tile (1, 1): ld = 4, so
tiles (5, 0) and (1, 1) share tag 5.

**Series** (`series.sh`, [series.csv](series.csv), 20 runs per cell and variant, 5 copies per
run, plain and patched interleaved; [series-1thread.csv](series-1thread.csv), 10 runs). A run
is wrong when any copy has a wrong element. Both builds print `provided 3`.

| cell (matrix, tiles src -> dst, grid, ranks x pika threads) | colliding pairs | plain | patched |
|---|---|---|---|
| 512, 64 -> 64, 2x1, source rank 0 -> 1, 2 x 4 | 28 | 20 of 20 wrong (100 of 100 copies) | 0 of 20 |
| the same, 0-200 µs per source tile | 28 | 20 of 20 (100 of 100) | 0 of 20 |
| 512, 128 -> 64, 2x1, same source rank (the miniapp's kind), jitter | 14 | 20 of 20 (100 of 100) | 0 of 20 |
| 1024, 64 -> 64, 4x1, 0 -> 1, 4 x 2, jitter | 344 | 20 of 20 (100 of 100) | 0 of 20 |
| 576, 64 -> 64, 3x2, (0,0) -> (1,0), 6 x 2, jitter | 21 | 20 of 20 (100 of 100) | 0 of 20 |
| 512, 64 -> 64, 2x2, (0,0) -> (1,1), 4 x 2, jitter (control) | 0 | 0 of 20 | 0 of 20 |
| 512, 2x1 as the first row, 1 pika thread, with and without jitter | 28 | 0 of 20 | 0 of 20 |

No run crashed or hung. With one worker thread the postings agree, most likely because the tasks
then run one at a time in creation order on both ranks (a guess, not checked in pika). The shipped test (`test_matrix`, `MatrixTest/*.
CopyReshuffling`, 6 ranks, grids 3x2 and 2x3, 3 configurations) passes (3 of 3 runs); the driver
counts 0 colliding pairs in each of its 6 grid and configuration combinations, so the test cannot
see the defect. The redistribution miniapp (`miniapp/miniapp_redistribution.cpp`) times copies
between block sizes and has no check.

**The fix** ([copy-tags.patch](copy-tags.patch)): the global linear tile index `ij.row() +
ij.col() * nrTiles().rows()`, unique. It raises the largest tag from about mt nt / P to mt nt; a
smaller unique tag is the source rank's local index (`util::matrix::local_tile_from_global_tile`
for the tile's source rank), which needs the distribution's private tile offsets.

**Upstream.** Not found (searched 30 Oct): GitHub issues for "copy tag", "tag" (9 issues;
#857 is the hard-coded tag 0 of the distributed permutation, open and judged harmless, #700 the
tag convention closed by #993), "redistribution" (5, none a bug report); PRs on `copy` (#1355
"Fix assertion in copy", #1361 local copies with different tile sizes) do not touch the tag.
Report drafted, not sent. Its reproduction block (`clean-room.sh`: fresh clones, the dependencies
and DLA-Future installed to a prefix, the driver built with `CMakeLists.txt`) run in an empty
directory: 10 of 10 copies wrong, then 10 of 10 right with the patch applied and DLA-Future
reinstalled (after adding `LD_LIBRARY_PATH`, which the first run lacked: the dependencies
install without RPATH).

## Sites shown safe (by reading)

- Every collective (broadcasts, reductions, all-reduces, barriers; `broadcast_panel.h`,
  `reduction_to_band/impl.h`, `tridiag_solver`, `triangular/impl.h`, `norm/mc.h`, `t_factor_impl.h`)
  goes through `exclusive()`, so the calls on one communicator are posted in program order on
  every rank.
- Point-to-point through `exclusive()` with a fixed tag (`bt_band_to_tridiag/impl.h:921-925`,
  tag 0): postings in submission order on both sides, so non-overtaking matches them in order.
- `bt_band_to_tridiag/impl.h:1005`, `schedule_sum_p2p` through `shared()` with tag `j_e + i_b *
  ncols_local`: distinct within an epoch; epochs k and k-2 can reuse i_b, but the second
  exchange reads E tiles that the first one's update writes, so on each rank it is posted after
  the first completes.
- `tridiag_solver/rot.h:328-330`: the column exchanges of one rotation are waited for
  (`sync_wait`) before the next rotation's are posted, as the comment there explains.
- `permutations/general/impl.h:302-314`: `MPI_Isend`/`MPI_Irecv` with tag 0 through `shared()`,
  one message per partner and direction in each call, and each call on a sub-pipeline
  sequenced after the previous one (the hard-coded tag of #857).
- `band_to_tridiag/mc.h:1100-1500`: four tag families (copy `2j + {0,1}`, then the v tags, then
  the column and worker tags) in disjoint ranges; the column and worker tags may coincide for
  more than 2 ranks but flow in opposite directions round the ring (different sources), and the
  worker tag repeats every `workers_per_tile` sweeps, the reuse being ordered by the worker's
  pipeline. Read, not checked exhaustively.

## What the tool did on these traces

**Since milestone F (30 Oct, docs/threads.md section 15)** a request completed by another
thread is analysed: its completion lies on the polling worker, which the posting precedes
(premise P-T9). `traced.sh` ([traced.csv](traced.csv); a-p13, 16 processors; the CSV was
written by the same commands from a scratch copy of the script), 5 traced runs per cell and
variant, one copy each:

| cell (as series.sh) | plain: wrong copies, traces with L1 receive alternatives | patched |
|---|---|---|
| 2x1, 4 pika threads, 28 colliding pairs | 5 of 5 wrong, 5 of 5 (52 to 64 each) | 0 of 5, 0 of 5 |
| 4x1, 2 threads, 344 pairs | 5 of 5, 5 of 5 (113 to 206) | 0 of 5, 0 of 5 |
| 3x2, 2 threads, 21 pairs | 5 of 5, 5 of 5 (34 to 48) | 0 of 5, 0 of 5 |
| 2x2, 2 threads, 0 pairs (control) | 0 of 5, 0 of 5 | 0 of 5, 0 of 5 |
| 2x1, 1 pika thread (control) | 0 of 5, 0 of 5 | 0 of 5, 0 of 5 |

Every alternative is a concurrent swap or steal (1f) of two receives with one source and tag,
which exist only where tags collide, so all are at colliding tags (4 to 71). The patched traces
and the controls have none. What remains on every trace with several workers is the false alarms
above, from pika's synchronisation the tracer does not see (0 to 13 `2-thread-collective`, 2 to
6 `2-thread-wait` per trace), and `waitany-order` warnings (45 to 2 443 per trace) from pika's
`MPI_Testsome` polls, whose completion order can vary; neither is a receive alternative. The
claim rests on P-T10 (a result is read only after its completion in the recorded
happens-before), which pika's continuations, scheduled through atomics, do not give the trace.
The two traces of the paragraph below, analysed again, give the same picture (60 alternatives,
none patched).

Before milestone F: traced copies (2x1, 4 threads, one copy; the plain run gave a wrong copy,
the patched one a right one): both traces are degraded `threads-handoff`, so Stretto predicts
nothing. pika's MPI
polling (`libs/pika/async_mpi/src/mpi_polling.cpp:415, 466, 499`) tests the requests of all
tasks with `MPI_Testsome`/`MPI_Testany` on whichever worker thread polls, so nearly every
request is completed by another thread than the one that posted it. The observed classes are
false alarms from synchronisation the tracer does not record (premise P-T2):
`2-thread-collective` (7 and 13 per trace) pairs an `MPI_Ibarrier` of
`CommunicatorGrid::wait_all_communicators` with the `MPI_Comm_free` that the last release of the
communicator's reference most likely performs on another worker (a guess from the code:
ordered by the pipeline's and the reference count's atomics), and `2-thread-wait` (2 per
trace) two workers testing one request, serialised by pika's `polling_vector_mtx_` (a
`pika::detail::spinlock`, atomics). pika runs tasks
as user-level threads on its worker pthreads, so a chain is a worker, not a task: records of
unrelated tasks follow each other in a chain's program order, and a task may resume on another
worker. `pending_pairs.py`, which needs no happens-before, finds 10 and 14 pending same-envelope
receive pairs on the two ranks of the plain trace (tags 4 to 31, all posted on two threads) and
0 in the patched one; whether a pair's postings were unordered comes from the code, not the
trace.

## Files

`Dockerfile`, `build.sh`, `copy_check.cpp` (driver and oracle), `copy-tags.patch` (against
09fe442, `patch -p1`), `series.sh`, `traced.sh` and `traced_csv.py` (traced runs and Stretto's
reports), `pending_pairs.py`, `clean-room.sh` and `CMakeLists.txt` (the upstream report's
reproduction block, run verbatim in an empty directory); results `series.csv`,
`series-1thread.csv`, `traced.csv`.
