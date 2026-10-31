# PaRSEC: local reshapes on several threads under `MPI_THREAD_MULTIPLE`

Target 2 of the second thread hunt of 30 Oct 2026. PaRSEC (ICLDisco/parsec, master 6c49997 of
28 Oct 2026) runs a communication thread beside its computation threads. When MPI grants
`MPI_THREAD_MULTIPLE` and the MCA parameter `runtime_comm_thread_multiple` is left at -1 (auto),
`remote_dep_mpi_init` sets `PARSEC_CONTEXT_FLAG_COMM_MT` (`parsec/remote_dep_mpi.c:381-383`), and
then computation threads call MPI themselves: they send activations (`remote_dep_mpi.c:649-652`,
`MPI_Send` in `parsec_mpi_funnelled.c:1264`) and perform local reshapes (`remote_dep_mpi.c:667,
810-822`). Built with MPI and its reshape tests (`tests/collections/reshape`) plus the driver
`reshape_check` (`build.sh`, both variants in about 10 minutes with 6 jobs, not timed exactly),
Open MPI 4.1.6, gcc 13, a-p13 CPUs 0-5,40-45. DPLASMA was not built: the defect below is in
PaRSEC and its own tests reach it.

## Finding: reshape tags repeat across virtual processes (confirmed)

**The defect** (`parsec/parsec_mpi_funnelled.c:485-509`, `parsec_mpi_sendrecv`, the engine's
`reshape`). A local reshape copies a tile between two datatypes with

    MPI_Sendrecv(src, count_src, layout_src, 0, es->th_id,
                 dst, count_dst, layout_dst, 0, es->th_id, parsec_ce_mpi_self_comm, ...)

on one duplicate of `MPI_COMM_SELF` for the whole process, with the calling execution stream's
`th_id` as tag. Under `COMM_MT` every computation thread reshapes inline, so the tag has to tell
concurrent callers apart, and `th_id` does not: it numbers the threads of one virtual process
(`parsec_vp_init`, `parsec/parsec.c:396`: `startup[t].th_id = t`; `execution_stream.h:37`, "A
thread belongs to a vp"; `PARSEC_THREAD_IS_MASTER` tests `th_id == 0 && vp_id == 0`), and the
communication thread's stream is set to `th_id = 0` ("Pretend to be the master thread",
`remote_dep_mpi.c:538`). With two virtual processes, thread t of each uses tag t; two such
`MPI_Sendrecv`s at once can match each other's sends, and each copy receives the other's tile.
With equal sizes the data are silently exchanged; with different datatypes the receive can
truncate.

**When.** Several virtual processes per MPI process come from `runtime_vpmap`: `hwloc` makes
one per socket or NUMA node (`vpmap.c:222-235`), the usual choice on multi-socket nodes. a-p13
grants us one NUMA node, so `series.sh` emulates two sockets with a synthetic hwloc topology
(`HWLOC_SYNTHETIC="package:2 core:3 pu:1"`), under which hwloc does not bind threads. The other
two maps do not work: `rr:` reaches `assert(0); /* TODO: finish this */` (`vpmap.c:384`,
segmentation fault in a release build), and `file:` crashes on both line formats (tested,
`vpmap.c:308-327`: the `NULL == local_vpmap` test is inverted and `rest_of_line` is not set for
lines that start with ':'). The communication thread's tag 0 could collide with thread 0's in
one virtual process, but in the traces of the shipped `reshape` test (4 ranks, `-m 1`) the
communication thread made no reshape (all self-communicator `MPI_Sendrecv`s came from the
computation threads, tags 0 and 1); it reshapes before sending only with
`PARSEC_RESHAPE_BEFORE_SEND_TO_REMOTE`, which no build file defines.

**Driver** (`reshape_check.jdf`, `testing_reshape_check.c`): `local_read_reshape.jdf` with an
oracle. Every tile holds values that encode its indices; `READ_A` reads it with `[type =
LOWER_TILE]` (a reshape), `CHECK_A` compares the reshaped copy's lower triangle with the tile's
values, `WRITE_A` writes it back (another reshape), and the program compares the matrix with its
initial values; `REPS` taskpools per run.

**Series** (`series.sh`, [series.csv](series.csv), 20 runs per cell and variant, plain and
patched interleaved; 120 x 120 integers in 9 x 9 tiles; a run of `reshape_check` is wrong when
any of its 5 taskpools is; a run of the shipped `reshape` test when any of its 7 tests fails):

| cell | plain | patched |
|---|---|---|
| `reshape_check`, 1 rank, 6 threads in 2 virtual processes, `-m 1` (`MPI_THREAD_MULTIPLE`) | 20 of 20 wrong (97 of 100 taskpools) | 0 of 20 |
| the same on 4 ranks, 2 threads each in 2 virtual processes | 15 of 20 (39 of 100) | 0 of 20 |
| shipped `reshape` test, 1 rank, 6 threads in 2 virtual processes, `-m 1` | 20 of 20 (104 of 140 tests failed) | 0 of 20 |
| `reshape_check`, 2 virtual processes, `-m 0` (`MPI_THREAD_SERIALIZED`: reshapes on the communication thread) | 0 of 20 | 0 of 20 |
| `reshape_check`, 1 virtual process (flat), `-m 1` | 0 of 20 | 0 of 20 |

`MPI_Query_thread` gives 3 with `-m 1`, and the driver prints `vps 2 comm_mt 1`. No run crashed
or hung. In 5 more runs of the shipped test per mode, `local_read_reshape`, `local_input_reshape`
and `remote_read_reshape` failed in 5 of 5 with `-m 1`, `local_input_LU_LL` and
`remote_no_re_reshape` in 4, `local_output_reshape` in 3, `local_no_reshape` (no reshape) in 0;
with `-m 0` all passed. In the first plain run the wrong taskpools had 90 to 630 wrong
elements, multiples of 45, the lower triangle of a 9 x 9 tile: whole tiles exchanged.

**The fix** ([self-tags.patch](self-tags.patch), 11 lines): number the computation threads
across all virtual processes from 1 (`th_id` plus the sizes of the earlier virtual processes)
and leave tag 0 to the communication thread.

**Upstream.** The line is the same in master 6c49997 and in the branch of the open PR #782
("Communication engine as a component", `parsec/mca/comm/mpi/comm_mpi_funnelled.c`, read 30 Oct).
Not found in the issue tracker (searched 30 Oct for reshape, 23 results, and vpmap, 6; #180 is a
hang with `-V rr:1:10:10` in 2018). Report drafted, not sent. Its reproduction block
(`clean-room.sh`: a fresh clone, the shipped `reshape` test with two virtual processes, then the
patch applied by `git apply`) run in an empty directory: 3 of 3 plain runs fail 6 of the 7
tests, 3 of 3 patched runs pass all 7. (A first attempt stopped at `git apply`: the patch's hunk
header counted one line too many, which GNU `patch`, used by `build.sh`, tolerates; fixed.)

## Sites shown safe (by reading)

- Activations from computation threads (`remote_dep_nothread_send` under `COMM_MT`,
  `MPI_Send` on the per-tag duplicate `parsec_ce_mpi_am_comm[tag]`) are received by the
  communication thread's persistent `MPI_Recv_init(ANY_SOURCE, tag)` requests
  (`parsec_mpi_funnelled.c:901-906`) and handled by their contents; their order between
  threads does not matter, and only the communication thread receives.
- Data messages (PUT/GET) are posted by the communication thread only, with tags from
  `next_tag` (`parsec_mpi_funnelled.c:72-95`), a compare-and-swap counter under `COMM_MT`. A tag
  is reused after `MAX_MPI_TAG`, 2^31 - 1 with Open MPI (`MPI_TAG_UB`), only after rollover.
- With one virtual process the computation threads have distinct `th_id`s: the 1-VP cell above.

## What the tool did on these traces

**Since milestone F (30 Oct, docs/threads.md section 15)** a receive withdrawn by a cancel on
another thread is modelled (posted until its cancel, ordered against concurrent receives and
probes), and the trace is analysed. `traced.sh` ([traced.csv](traced.csv); a-p13, 16
processors; the CSV was written by the same commands from a scratch copy of the script), 5
traced runs per cell and variant, one taskpool each; every run came out right:

| cell | plain: traces with L1 receive alternatives | patched |
|---|---|---|
| 1 rank, 6 threads in 2 virtual processes, `-m 1` | 5 of 5 (15 to 17 each, tags 0, 1 and 2) | 0 of 5 |
| 4 ranks, 2 threads each in 2 virtual processes | 5 of 5 (20 each, tag 0) | 0 of 5 |
| 2 virtual processes, `-m 0` (`MPI_THREAD_SERIALIZED`) | 0 of 5 | 0 of 5 |
| 1 virtual process, `-m 1` | 0 of 5 | 0 of 5 |

Every alternative is a concurrent swap or steal (1f, the copies have one size) of two threads'
`MPI_Sendrecv` receives on a rank's duplicate of `MPI_COMM_SELF` with one tag, the threads `t`
of the two virtual processes: the race of the finding, predicted from runs in which it did not
happen. No other finding in any of the 40 traces. Before the fix of 15.2, 6 of the 10 earlier
plain traces (below) also had an observed `2-buffering` and 5 instead of 15 alternatives; with
it, those 10 traces give 15 or 17 alternatives each, the 5 patched ones none.

**Forced replays (30 Oct, [docs/threads.md](../../docs/threads.md) section 16; `replay.sh`).** 5
new traced runs per cell with the lock order recorded (`STRETTO_SYNC_ORDER=1`), all right; per
trace 3 of its L1 alternatives, each replayed twice with the chain pins, order gates and lock
gates of its horizon and twice as its control; the oracle is the driver's own check. The swap
needs the other thread's send between one `Sendrecv`'s two postings, which the tracer posted
under one hold of its issue lock: the replays became possible only once a gate may hold a
`Sendrecv`'s receive back (section 16). Tables `replay-1rank.csv`, `replay-4ranks.csv` (their
traced runs `replay-traced-1rank.csv`, `replay-traced-4ranks.csv`); verdicts by
`experiments/replay_verdicts.py`:

| cell | traces (alternatives each) | replays | every pin taken, check failed, control right with its pins | inconclusive |
|---|---|---|---|---|
| 1 rank, 6 threads in 2 virtual processes | 5 (15 or 17) | 30 (15 alternatives) | 23 (12 alternatives) | 7: 6 replays at a gate timeout (4 with 5 threads waiting at gates, 2 with none), 1 control timeout |
| 4 ranks, 2 threads each in 2 virtual processes | 5 (20) | 30 (15 alternatives) | 30 (15) | 0 |

Every control finished with a right result, its pins taken and its synchronisation the trace's,
but one that timed out (59 controls: 0 wrong elements); no replay or control diverged or
misapplied a pin. Each confirmed replay failed the check with 45 or 90 wrong elements, the lower
triangles of one or two tiles (two 4-rank replays exited with status 0 although the driver's
summary line counted the wrong taskpool; not examined). So the
race is confirmed from runs in which it did not happen: forcing the predicted interleaving of
two threads' reshapes makes the same program, unpatched, compute a wrong result while its
control does not. The rows' commit column was 82c88b0, a commit later amended with an unused
option only (`--cls`); it is written as 35d94ee here.

Before milestone F: traced `reshape_check` runs (1 rank, 2 virtual processes, `-m 1`, one taskpool): 10 plain runs
and 5 patched, all with correct results (the tracer's issue lock serialises the postings).
Stretto: every trace degraded `threads-cancel` ("a receive withdrawn by a successful cancel:
not modelled across threads"): PaRSEC cancels the communication engine's 24 persistent wildcard
receives at finalisation, so no alternative is predicted, and the reports have no finding.
`experiments/slate/overlap.py`, which needs no happens-before and applies here because each
`MPI_Sendrecv` posts and completes its operations in one call: 8 of 10 plain traces have 2 to 22
same-envelope pairs pending on two threads at once, on the self communicator with tags 0 and 2
(threads 0 and 2 of the two virtual processes); 0 in the 5 patched traces.

## Files

`Dockerfile`, `build.sh`, `reshape_check.jdf`, `testing_reshape_check.c`, `self-tags.patch`
(against 6c49997, `patch -p1`), `series.sh`, `traced.sh` (traced runs and Stretto's reports; its
CSV by `../dlaf/traced_csv.py`), `replay.sh` (forced replays), `clean-room.sh` (the upstream
report's reproduction block, run verbatim in an empty directory); results `series.csv`,
`traced.csv`, `replay-1rank.csv`, `replay-4ranks.csv`, `replay-traced-1rank.csv`,
`replay-traced-4ranks.csv`.
