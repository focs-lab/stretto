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
