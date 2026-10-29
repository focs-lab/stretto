# Uintah with hypre under the multithreaded Unified scheduler

Target 1 of the scout of 29 Oct 2026 ([docs/threads.md](../../docs/threads.md), sections 10 and
11): Uintah's schedulers and hypre share `MPI_COMM_WORLD`, and a comment in `KokkosScheduler.cc`
(lines 1042-1060) records crashes from hypre's `MPI_Iprobe` taking a Uintah message. `run.sh`
builds hypre 2.33.0 and Uintah's `sus` with the Examples component (no Kokkos: the Unified
scheduler, which `sus -nthreads N` selects, runs tasks on N threads under
`MPI_THREAD_MULTIPLE`), runs `StandAlone/inputs/Examples/solvertest1.ups` (the hypre CG/PFMG
solve, with 8 patches instead of 1 and 5 time steps) traced at 2 and 4 ranks with 2 threads, and
counts untraced outcomes. Uintah 493d2b5, Open MPI 4.1.6, a-p13, 30 Oct 2026. The build took
about 1.5 minutes (hypre) and 4.5 minutes (Uintah) with 2 jobs.

**What L1 predicts.** No receive alternative: every receive of Uintah and hypre names its
source and tag (0 wildcard receives). Probe alternatives (4 per trace at 2 ranks, 45 at 4): all
at hypre's own `Iprobe(ANY_SOURCE, tag)` of its data exchange (tags 1000, 3000, 4000), reporting
another thread's hypre message; none involves a Uintah message. Probe alternatives of several
threads are not replayed (section 11).

**What the traces show** (observed, no alternative needed): `2-thread-collective` in every
trace (2 per trace at 2 ranks, 4 at 4): on each rank, the `MPI_Allreduce` of Uintah's reduction
task (`OnDemandDataWarehouse::reduceMPI`, `libCCA_Components_Schedulers.so`) on one thread and
hypre's (`hypre_MPI_Allreduce`) on the other, on `MPI_COMM_WORLD`, with no recorded
synchronisation between them (MPI 4.1 §12.6.2 requires the user to order them). Where the two
are called in different orders on two ranks, each matches the other's.

**Outcomes** (untraced, two series of 10 runs each, the second by `run.sh`; the failure rate
varies between series):

| ranks, scheduler | passed | `MPI_ERR_TRUNCATE` in `MPI_Allreduce` |
|---|---|---|
| 2, `-nthreads 2` | 10 and 4 of 10 | 0 and 6 |
| 4, `-nthreads 2` | 4 and 0 of 10 | 6 and 10 |
| 4, `-nthreads 4` | 0 and 0 of 10 | 8 and 9 |
| 4, MPI scheduler (one thread) | 10 and 10 of 10 | 0 and 0 |

The failing runs also print a time step of 1.2e-321 before they abort: a reduction's result is
garbage. Traced, 3 of 3 runs at 4 ranks abort the same way (reported as observed `abort` and
`2-blocked` in an `MPI_Allreduce`), and 1 of 3 at 2 ranks. That the truncating `MPI_Allreduce`
is the reduction meeting hypre's is a hypothesis: the trace shows the two concurrent, and the
failures need threads, but which call fails was not checked. Uintah means to give each
reduction task a duplicate of the communicator when it runs threads (`TaskGraph.cc` 801-812,
`ProcessorGroup::getGlobalComm`); in these traces the concurrent reduction uses `MPI_COMM_WORLD`,
and why was not established. The scheduler warns at start that it is
experimental and "not all tasks are thread safe yet". Not reported upstream.
