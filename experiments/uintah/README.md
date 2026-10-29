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
`2-blocked` in an `MPI_Allreduce`), and 1 of 3 at 2 ranks. The scheduler warns at start that it
is experimental and "not all tasks are thread safe yet".

## Cause and fix (30 Oct 2026)

**The defect** (line numbers of Uintah 493d2b5, which is also master on 30 Oct, and hypre
2.33.0). Two tasks of one time step call `MPI_Allreduce` on `MPI_COMM_WORLD` from two threads,
and nothing orders them:

- the hypre solve (`Task::Hypre`): `HypreStencil7::solve` creates every hypre object with
  `pg->getComm()` (`Solvers/HypreSolver.cc` 243, 435, 546, ...), the world communicator
  (`Core/Parallel/Parallel.cc:570`, 588);
- `ApplicationCommon::reduceSystemVars`, a `OncePerProc` task with `usesMPI(true)`
  (`ApplicationCommon.cc:388-392`), reduces delT with `reduceMPI(..., -1)` (line 499; the
  application reduction variables likewise, `ApplicationReductionVariable.h:185, 207, 228`).
  `getGlobalComm(-1)` is the world communicator (`ProcessorGroup.h:90-96`): the duplicates of
  `setGlobalComm` go only to tasks of type `Task::Reduction` (`TaskGraph.cc:801-812`), and
  solvertest1 has none.

Both tasks are in phase 0 (`SCI_DEBUG=TaskGraphPhases:+`); the Unified scheduler starts a
phase's `usesMPI` task once every other task of the phase has been *consumed*, that is taken by
a thread, not finished (`UnifiedScheduler.cc:746-751`, `markTaskConsumed` line 709). The two
depend on different tasks (`computeStableTimeStep`, `timeAdvance`), so ranks call them in
different orders. With `--mca mpi_abort_print_stack 1` the call that reports `MPI_ERR_TRUNCATE`
is hypre's `MPI_Allreduce` in `hypre_StructGridAssemble` (`struct_grid.c:377`: one `MPI_INT`,
the local cell count), on a worker thread or the main thread; the delT reduction is one
`MPI_DOUBLE`. The garbage delT is that int read as a double: 250 × 2^-1074 = 1.23516e-321, 250
being the cells of one rank (2 patches of 125). So the trace's concurrent pair is the failing
pair. The same conflict was fixed for the KokkosOpenMP scheduler in Aug 2021 (Uintah fefdd7d,
a duplicated communicator for hypre; d5a107b) and reverted on 14 Jan 2022 (5e4360e, ae3d4b6,
"some error on GPU"); no GitHub issue mentions it.

**The fix** ([hypre-comm.patch](hypre-comm.patch), 32 lines): `HypreSolver2`'s constructor,
which runs on the main thread before any task, duplicates the world communicator, and
`HypreStencil7` uses the duplicate in place of `pg->getComm()`. `build.sh` builds hypre and the
plain and fixed `sus` for Open MPI or MPICH; `series.sh` alternates their runs. Ubuntu's MPICH
needs Open MPI's `mpirun` as launcher (its hydra starts singletons, RUNBOOK). Both report
`Provided: 3` (`MPI_THREAD_MULTIPLE`) in every threaded run. Results in
[series.csv](series.csv), 40 runs per cell, CPUs 16-19,56-59 of a-p13, 30 Oct:

| ranks, scheduler | Open MPI 4.1.6 plain | fixed | MPICH 4.2.0 plain | fixed |
|---|---|---|---|---|
| 4, `-nthreads 4` | 0 pass (35 truncate, 2 abort, 3 hang) | 40 | 0 pass (40 truncate) | 40 |
| 4, `-nthreads 2` | 14 pass (26 truncate) | 40 | 7 pass (32 truncate, 1 hang) | 40 |
| 2, `-nthreads 2` | 39 pass (1 truncate) | 40 | 35 pass (5 truncate) | 40 |
| 4, MPI scheduler | 40 | 40 | 40 | 40 |

The 2 aborts lost Open MPI's error text in ORTE's `show_help` but printed the garbage delT. A
run takes under a second with Open MPI and about 4.5 s with MPICH (UCX start-up); a hang is a
run killed after 30 s. Three fixed runs at 4 ranks with 4 threads print the same iterations and
residuals in every time step as the MPI scheduler. The shipped solvertest1 (1 patch) cannot run
on more than one rank ("Number of patches (1) must >= the number of processes"), hence the 8
patches. Uintah's nightly suite runs hypre under the Unified scheduler on 2 ranks
(`R_Tester/ARCHES.py`, `multibox_rmcrt_coal_*_threaded`, `-nthreads 8`); ARCHES was not built.
Upstream report drafted 30 Oct, not sent.
