# StarPU-MPI: a communication thread, and workers that post their own sends

Target 5 of the third thread hunt (2 Nov 2026), a blind scan like those of Global Arrays,
Smilei, Athena++ and MADNESS ([experiments/madness](../madness/README.md)): the shipped tests are
traced and analysed first, the findings committed (the pre-registration), and only then is the
communication code read at the reported sites. The reason to look here (an audit, unverified
before the scan): StarPU-MPI's communication thread posts the requests of the task graph, and
with `STARPU_MPI_THREAD_MULTIPLE_SEND=1` worker threads post their sends themselves.

StarPU master e736b00 (27 Oct 2026), configured with its MPI layer and CPU workers only (no
CUDA, OpenCL, HIP, Fortran, Python, simgrid or FxT), compiled by clang 18 with
ThreadSanitizer's instrumentation of atomics and linked against the tracer's stub (StarPU's
workers synchronise through atomics and spin locks; docs/threads.md section 17), Open MPI 4.1.6
or MPICH 4.2.0 (`build.sh`, about 5 minutes per MPI with 8 jobs), MACHINE=a-p13, CPUs
0-7,40-47, in the image of experiments/atomics plus autotools (`Dockerfile`). What was read
before the pre-registration: `configure.ac` (options), `mpi/tests/Makefile.am` (the test list,
`XFAIL_TESTS`), `mpi/tests/helper.h` (how the tests initialise MPI), the documentation of the
MPI support and of the environment variables (`doc/doxygen/chapters/starpu_extensions/mpi_support.doxy`,
`starpu_installation/environment_variables.doxy`), the comment of `starpu_mpi_initialize_extended`
in `mpi/include/starpu_mpi.h`, and the function names and line numbers that `stretto sites`
prints.

## Thread levels and the three modes

Every shipped MPI test initialises MPI itself with `MPI_THREAD_SERIALIZED`
(`MPI_INIT_THREAD` of `mpi/tests/helper.h`), and the documentation says that
`STARPU_MPI_THREAD_MULTIPLE_SEND` needs `MPI_THREAD_MULTIPLE` (it is on by default with Open MPI
4 and MPICH). A first traced `ring` on 4 ranks had the main thread and one other thread call MPI
on every rank. The scan therefore runs each test in three modes: `ser`, as shipped; `1` and
`0`, with `mtinit.c` preloaded before the tracer (it asks `MPI_Init_thread` for
`MPI_THREAD_MULTIPLE` and forwards to the tracer, whose `init` record then says level 3) and
`STARPU_MPI_THREAD_MULTIPLE_SEND` 1 or 0.

## Blind scan (pre-registration)

Recorded before any of StarPU-MPI's communication code was read (`mpi/src`); what was read is
listed at the top.

**What ran.** `scan.sh` (scripts of bf6fbd8; tracer of 04d9f71, analysis of a14ee43), 2 Nov
17:36-18:52 +08, MACHINE=a-p13, CPUs 0-7,40-47: the 86 programs of `starpu_mpi_TESTS` (what
`make check` runs), 4 ranks of 2 CPU workers (`STARPU_NCPU=2`, `STARPU_MPI_NOBIND=1`,
`STARPU_WORKERS_NOBIND=1`), the three modes, 3 traced runs each, Open MPI and MPICH, 1 548
runs; each trace of at most 300 MB analysed at L1 (`stretto analyze --json --no-order`) and its
sites named (`stretto sites`). Tables: [scan-openmpi-runs.csv](scan-openmpi-runs.csv),
[scan-mpich-runs.csv](scan-mpich-runs.csv) (one row per run: the mode in `send`, result, trace
size, report status, findings by level and class, the analysis commit and the commit) and
`scan-*-sites.csv`. Then `targeted.sh` (census and a sample of 100 per trace, as for MADNESS)
on the 12 traces whose analysis stopped at its limit: [targeted-openmpi.csv](targeted-openmpi.csv),
[targeted-mpich.csv](targeted-mpich.csv). Deviations, all recorded in the tables or here:

- The analyses had 300 s each at the start; the 3 traces of `abstract_comms` as shipped (13 MB,
  1 500 candidates each) used them up, and both scans were stopped at 17:43 +08 and started again
  with `RESUME=1`, a 60 s limit and 3 analyses at once. At 18:29 +08 the MPICH scan, three times
  slower than Open MPI's, was split into two containers on disjoint halves of the remaining
  tests (same directory, `RESUME=1`; the run in progress at the stop was made again).
- `large_set` with MPICH: every run was killed (exit 137 or 15) when its ranks 0 and 1 grew past
  the container's 12 GB; untraced, a run grew to the cap of a 3 GB container in 7 s (1.6 GB per
  rank, exit 137). With Open MPI it passes in a second. Its traces end there.
- The three programs of `XFAIL_TESTS` (`policy_register_toomany`, `policy_unregister`,
  `starpu_redefine`) abort in every run as their makefile expects (exit 134; class `abort`).
  `abstract_comms` exits 77 (skipped) in the two modes with `MPI_THREAD_MULTIPLE`. `attr` calls
  no MPI function (no trace: rows "no report (rc 2)").
- Traces: at most 137 MB; none killed, none deleted unanalysed.

**What the traces hold.** With `ser` and `0` every point-to-point call of StarPU-MPI is made by
one thread per rank (its communication thread; the main thread makes the initial and final
collectives); with `1` the envelope and data sends of the task graph are posted by the CPU
workers and the main thread, and the receives stay on the communication thread (`early_request`
on 4 ranks: 397 of 400 sends of rank 0 by a worker, 3 by the main thread). Half of the receives
are posted with `MPI_ANY_SOURCE`: at `_starpu_mpi_comm_post_recv` (`mpi/src/mpi/starpu_mpi_comm.c:157`),
tag 42, 32 bytes (the envelopes); the others name their source.

**Findings, by site.** Both MPIs and all three modes alike unless said.

| # | Site | Class, level | Tests (traces with it) | Per trace |
|---|---|---|---|---|
| S1 | `_starpu_mpi_comm_post_recv` at `mpi/src/mpi/starpu_mpi_comm.c:157`: the communication thread's `Irecv(MPI_ANY_SOURCE, 42)` of 32-byte envelopes | 1f at L1, steals: a receive takes another rank's envelope than in the run, and the run's envelope is left for a later envelope receive | full analyses: 14 tests (`coop_acknowledgement`, `coop_chained_sends`, `coop_many`, `early_request`, `gather`, `gather2`, `insert_task_seq`, `load_balancer`, `mpi_data_cpy`, `mpi_reduction`, `mpi_redux`, `mpi_scatter_gather`, `partition`, `policy_selection2`), 121 traces with Open MPI and 125 with MPICH, in every mode; census of the 12 stopped analyses (`abstract_comms` as shipped, `coop_many`, `early_request`): 10 016 candidates, of the 1 200 judged all accepted (1f) | 1-347 (full analyses); 186-1 493 (census) |
| O1 | (observed) | `1c`: four sends of the test's own `dump` (`mpi/tests/large_set.c:58-59`, `MPI_Send` from ranks 2 and 3 to rank 0, tags 2 and 3, 8 and 23 bytes) are never received; `2-buffering` below 23 bytes | `large_set`, all 18 traces (9 with MPICH, of runs killed by memory) | 4 |
| O2 | (observed) | `2-buffering`: the observed matching deadlocks below 4, 23, 32 or 262 144 buffered bytes (26, 18, 26 and 9 traces) | 79 traces of `early_request`, `insert_task`, `insert_task_seq`, `large_set`, `load_balancer`, `mpi_scatter_gather`, `coop_many` and `matrix2` (MPICH), both MPIs | 1 |
| O3 | (observed) | `abort` | the `XFAIL_TESTS` (27 traces per MPI) and MPICH's killed `large_set` runs | 2-4 |

No finding of any class in the other analysed traces: 595 of 757 analysed traces with Open MPI
and 595 of 761 with MPICH have none.

**Questions for the reading** (step 3): S1, does the communication thread's handling of an
envelope depend on which envelope receive takes it, or on the order of envelopes of different
ranks (the envelope names the data's tag and size, and a named receive follows)? With workers
posting sends (`1`), can two envelopes of one rank to one destination leave in another order
than their data? O1, is rank 0 meant to receive `dump`'s messages? O2, does each cycle run
through the envelope receives? And `large_set` with MPICH: what grows?

## Verdicts (after the pre-registration, from reading the code at the sites)

Read from 18:56 +08: `mpi/src/mpi/starpu_mpi_comm.c` (the envelope receive), `mpi/src/mpi/starpu_mpi_mpi.c`
(the envelope and data sends, the receive side of the progress loop, early data, the sync
mode, request termination), and the test `mpi/tests/large_set.c`. Line numbers are those of
e736b00.

| # | Verdict | Why |
|---|---|---|
| S1 | harmless by design | the communication thread keeps one `Irecv(MPI_ANY_SOURCE, _STARPU_MPI_TAG_ENVELOPE)` posted per communicator (`_starpu_mpi_comm_post_recv`, `starpu_mpi_comm.c:145-163`) and, for each envelope it tests complete (`_starpu_mpi_comm_test_recv`, `:165-213`), looks up the application's request by the envelope's data tag and the status's source, or creates early data for it, and posts the data receive from that source by name before it tests for another envelope (`starpu_mpi_mpi.c:1502-1590`; for early data "to make sure the mpi_irecv is posted before receiving an other envelope", `:1300-1310`). A sender posts an envelope and its data under one mutex (`send_mutex`, `_starpu_mpi_isend_envelope_func` and `_starpu_mpi_mpi_backend_isend`, `:478-548`), whichever thread posts them, so one rank's envelopes and data reach a destination in the same order, with workers posting sends (`1`) as with the communication thread alone; a synchronous send's data waits for the receiver's ready envelope, which the receiver sends just before it posts the data receive (`:566-584`), on another MPI tag. Which rank's envelope is taken first therefore changes only the order in which requests of different ranks are served, each by its own named receive |
| O1 | **real bug** in the test program, deterministic with an MPI library that lacks `MPI_Type_vector_c` (Open MPI 4.1 here), surfaced by the observed classes of the scan (1c and 2-buffering), not by a prediction | ranks above 1 skip the test and go to `end` (`large_set.c:595-596`), where `dump()` sends their output to rank 0 with two `MPI_Send` (`:56-59`). Without `MPI_Type_vector_c` (`#ifndef STARPU_HAVE_MPI_TYPE_VECTOR_C`, `:598-614`), ranks 0 and 1 first check `MPI_Type_create_struct` on large types (`_starpu_test_datatype`), and when it fails (as with Open MPI 4.1.6) they shut down and `return 0` without going to `end`: rank 0 never receives the other ranks' messages. They are 8 and 23 bytes, so the sends complete eagerly and the test passes; with the eager limit at its minimum every run hangs in those `MPI_Send` (`eager.sh`, [eager-openmpi.csv](eager-openmpi.csv): 10 of 10 runs at 0 eager payload bytes hang, as shipped and with the workers' own sends, one of them recorded as "fail 137" at 620 s because its stuck `mpirun` was killed by hand; 10 of 10 pass at the defaults), and the messages are left unmatched at `MPI_Finalize` in every run. The program relies on buffering that MPI does not promise. Fix: `goto end` on that path (`large-set-dump.patch`, 2 lines), so that ranks 0 and 1 dump too and rank 0 receives every rank's output. With MPICH (which has `MPI_Type_vector_c`) the check is skipped and ranks 0 and 1 run the large data sets, which need more memory than the 12 GB container (the deviation above), not this bug |
| O2 | **tool false alarm** (a limit of the observed 2-buffering class) | the cycle of `insert_task` (checked record by record; those of `early_request` and `load_balancer` were checked only for wildcard receives, of which `load_balancer`'s has two) runs through completions that are successful `MPI_Test`s of the communication thread's polling loop (`_starpu_mpi_test_detached_requests`, `starpu_mpi_mpi.c:1125-1150`) on the data sends; without buffering the test returns false and the thread goes on to serve envelopes and post the data receives the other rank waits for: the observed sequence of calls is not the only one the program makes at that threshold. The only blocking wait on the cycle, the `MPI_Wait` on the envelope of a send whose data is already complete (`:944-951`), cannot block: the data's receive is posted only after its envelope is received. Untraced runs at 0 eager bytes: `insert_task`, `insert_task_seq`, `early_request`, `load_balancer`, `mpi_scatter_gather`, `coop_many`, 60 of 60 passed at 0 eager bytes (30 per mode) and 60 of 60 at the defaults, none hung |
| O3 | as expected | the `XFAIL_TESTS` abort by design; MPICH's `large_set` runs ran out of the container's memory |

**Forced replays of S1** (`replay.sh`, [replay-openmpi.csv](replay-openmpi.csv), Open MPI, as
shipped): 2 new traced runs each of `gather`, `partition` and `mpi_redux` with the lock order
recorded, up to 4 sampled 1f alternatives per trace, each replayed once with its control: all
17 inconclusive (`replay_verdicts.py`): every replay and every control ended on a misapplied lock
pin (exit 86) within StarPU's initialisation (lock attempt 12 of a main thread), whose lock order
depends on the schedule of its threads (premise P-T5, as for MADNESS and DLA-Future). The
harmlessness of S1 rests on the reading.

## The large_set messages (O1): series, fix, report

`large-set-dump.patch` (2 lines in `main`): `goto end` instead of the early return, so that
ranks 0 and 1 dump their output too and rank 0 receives every rank's. `series.sh`
([series-openmpi.csv](series-openmpi.csv)), untraced, 4 ranks of 2 workers, Open MPI 4.1.6, 30 s
a run, 20 runs per cell, plain and patched builds (`build.sh` with `VARIANT=dump`) interleaved:

| mode | eager limit | plain | patched |
|---|---|---|---|
| as shipped (`MPI_THREAD_SERIALIZED`) | minimal (TCP, no payload byte buffered) | 19 of 20 hang (the 20th was killed by hand at 20 s while it hung: exit 137) | 0 of 20: 20 pass (0.6-0.7 s) |
| as shipped | defaults | 0 of 20: 20 pass | 0 of 20: 20 pass |
| `MPI_THREAD_MULTIPLE`, `STARPU_MPI_THREAD_MULTIPLE_SEND=1` | minimal | 20 of 20 hang | 0 of 20: 20 pass |
| `MPI_THREAD_MULTIPLE`, `STARPU_MPI_THREAD_MULTIPLE_SEND=1` | defaults | 0 of 20: 20 pass | 0 of 20: 20 pass |

Rows of `series-openmpi.csv` whose commit is `490410c-dirty` (from the 11th round on) ran
`eager.sh` as committed in 4e4d984 (`timeout -k 10`: after `timeout` stopped a hung run,
`mpirun` itself hung with its ranks gone, and the 11th plain run and one run of `eager.sh` were
killed by hand; `eager-openmpi.csv` has that one as "fail 137" at 620 s).

The report's reproduction block (`clean-room.sh`: a fresh clone, StarPU's default configuration
with MPI and gcc, Ubuntu's Open MPI, 4 ranks, eager limit at its minimum) run in an empty
directory: 3 of 3 plain runs hung (exit 124), 3 of 3 runs with the patch applied passed (exit 0), 2 Nov 19:21-19:40 +08 (a first attempt with plain `timeout` had one plain run killed by hand, as above: 124, 137, 124 and 0, 0, 0). The lines of the type check and its early return date from October 2024 to April 2025
(blame of the file); StarPU's issue tracker was not searched (no access from here). Report drafted, not
sent.

## What the tool did here

No race in StarPU-MPI's shipped tests, with or without workers posting their own sends. Every
predicted alternative is a steal at the communication thread's one wildcard envelope receive,
on 14 of the 86 tests (1 to 347 per trace, 10 016 more candidates in the traces cut short), and
the runtime is built for it: each envelope is answered by a data receive that names its
source, posted before the next envelope is taken, and a sender posts envelope and data under one
mutex whichever thread sends (GA's S1 and MADNESS's S1 again). The real bug of the scan is in
a test program, and the scan's observed classes surfaced it rather than a prediction: four
sends of `large_set` that nothing receives (1c), and the observed matching's deadlock without
buffering (2-buffering), which an untraced run at the minimal eager limit confirms as a hang in
every run. The other observed 2-buffering reports are false alarms of the class at a polling
loop, whose successful tests the class takes for waits.

## Files

`Dockerfile`, `build.sh` (StarPU with its MPI layer, clang with the atomics instrumented, both
MPIs, `VARIANT`/`PATCH` for patched trees), `mtinit.c` (the `MPI_THREAD_MULTIPLE` shim),
`scan.sh` and `scan_csv.py` (the scan and its tables `scan-{openmpi,mpich}-{runs,sites}.csv`),
`summary.py`, `targeted.py`, `targeted.sh` and `targeted_summary.py`
(`targeted-{openmpi,mpich}.csv`), `eager.sh` (`eager-openmpi.csv`), `replay.sh`
(`replay-openmpi.csv`), `large-set-dump.patch`, `series.sh` (`series-openmpi.csv`),
`clean-room.sh`.
