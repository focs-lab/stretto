# Findings

One ledger of everything the bug hunts found: real bugs, latent bugs, sites shown safe, and
defects of the tool itself. Each entry says where its evidence lives. Measured numbers of the
cases that are experiments of this repository are in [results.md](results.md) (generated).
Updated with every finding.

## Real bugs, predicted from clean runs and confirmed by forced replay

| Bug | Class, consequence | How found | Evidence | Upstream |
|---|---|---|---|---|
| HYPRE 2.10.1 ParaSails, two exchanges share tag 222 | probe takes another exchange's request; hang | documented (fixed in 2.11.0) | E5c, `experiments/hypre` | fixed |
| NPB MG 2.4.1, message of another grid level | wildcard receive; wrong numbers | documented (fixed in 3.2.1) | E5d, `experiments/npbmg` | fixed |
| deal.II 9.3.0-rc1, probe takes the next call's message | probe; wrong values | documented | E5e, `experiments/dealii` | fixed |
| deal.II NBX, issue 8929 (pre-fix 2c1b27fb) | failed `Iprobe` of call k could see call k+1's message; wrong answers | documented; reached by flag flips | E5g, E5i, `experiments/dealii8929` | fixed (f9ea3396) |
| Cabana, before PR 835 | hang | documented | E5g, `experiments/cabana` | fixed |
| stempy 3.3.5 | deserialisation exception | documented | E5g, `experiments/stempy` | fixed |
| MFEM 3.3.2, group rows | abort (truncation) or wrong groups | documented | E5g, `experiments/mfem332` | fixed |
| LAMMPS, before March 2017 | deadlock without eager buffering | documented | E5g, `experiments/lammps` | fixed |
| deal.II 9.3.0 `FETools::extrapolate`, every `Isend` of a round from one buffer | buffer guard: writes to pending send buffers (40-248 B), predicted to change messages below their size; with the eager limit forced below, 10 of 10 runs of the shipped test fail its check, 0 of 10 at it and 0 with the fix | documented (PR 13206), latent at default eager limits | `experiments/dealii13206` | fixed (b3b2b97) |
| **HYPRE 3.2.0 ParaSails, all pattern levels share tag 221** | probe takes the next level's request; hang | new, Oct 2026 | E5f, `experiments/hypre3` | report drafted |
| **SuperLU_DIST 9.3.1, 3D solve, barrier commented out (79644321)** | L-solve takes a U-solve message on reducible matrices; hang or wrong x | new, Oct 2026 | E5h, `experiments/superlu3d` | report drafted |
| **Quantum ESPRESSO develop `elph_prt`, tags independent of spin** | ionode's receives take the other spin's data (LSDA, npool >= 3); wrong printed table | new, Oct 2026 | E5h, `experiments/qeelph` | report drafted |
| PETSc 3.26, `-pc_hypre_parasails_nlevels` ignored without `_thresh` | option handling (not a race); found while reproducing HYPRE 3.2.0 | by reading | none needed (deterministic) | report drafted |

The four drafted reports were reproduced on 29 Oct 2026 from their text alone, on the then
current heads and without this tool. QE shows in plain runs (23 and 30 of 40 wrong tables, with
MPICH and with Open MPI). SuperLU_DIST and HYPRE show only with a demonstration patch that
delays one message (20 of 20 hang in every setting, 0 of 20 with the fix). Plain runs of both
never failed on one node (0 of 80 and 0 of 160).

## Latent bugs: real, but reached only by a driver over the public API

No shipped caller reaches them, so they count apart from the bugs above. Drivers, fixes, scripts
and compact result tables: [experiments/latent](../experiments/latent/README.md).

| Bug | Evidence (driver written for the hunt; `experiments/latent`) | Why no shipped caller hits it |
|---|---|---|
| OpenFOAM ESI develop 68a81857c, `Pstream::exchange(Map)`: NBX with a constant default tag; two calls in a row mix their messages | 39 of 39 clean traces predict it; 434 of 434 replays print wrong data, 429 of them with a correct control; plain and delayed stress 1 of 320; `nbx.tuning=1` removes it; 100 calls a run, 4 ranks: 12 of 20 plain runs wrong, then abort, 0 of 20 with `nbx.tuning=1` or with it the default (30 Oct) | the only in-tree caller (Test-parallel-chunks, with `nbx.min=2`) checks nothing; snappyHexMesh tutorials traced with `pbufs.tuning=1` have no cross-call alternative. The 2024 issue 3238 was the same class at call sites; report drafted 30 Oct, not sent |
| libsc 6928417 `sc_notify_payload_nbx`: fixed tag, nothing orders calls | 22 of 22 clean traces predict it; 372 of 372 replays wrong; plain stress 22-24 of 40 wrong; 100 calls a run: 20 of 20 plain runs wrong, 0 of 20 with a barrier (30 Oct) | NBX is opt-in; the default type PEX is safe; report (one for the three libsc rows) drafted 30 Oct, not sent |
| libsc `sc_notify_recursive_nary`: per-level tags reused by the next call | 20 of 20 clean traces; 80 of 80 replays wrong, then abort or SEGV; 100 calls a run: 19 of 20 plain runs wrong, 0 of 20 with a barrier | opt-in; p4est callers put a collective between calls; report drafted 30 Oct, not sent |
| libsc `sc_notify_payloadv_nbx`: sends tag NBXV, probes tag NBX | deterministic hang, 20 of 20 (not a race; found by reading); 0 of 20 with the right tag | opt-in, `sorted=0` only; report drafted 30 Oct, not sent |
| Swift/T ADLB 4b3882d `xlb_poll`: `Iprobe(ANY, ANY)` can take SYNC_SUB, which has no handler | 20 of 20 clean traces; 40 of 40 replays abort the server; stress 0 of 290 | STC-compiled Swift uses short subscripts; only the C API or hand-written Tcl rules reach it |

## Sites shown safe

| Site | Why safe | How shown |
|---|---|---|
| hypre 3.2.0 `hypre_LocateAssumedPartition` (MGR) | `MPI_Scan` then `Bcast` from the last rank order consecutive calls | traced: 31 alternatives, all within one call |
| dune-common `IndicesSyncer::sync` | two `MPI_Allreduce` (dune-istl matrixhierarchy.hh) separate the syncs | traced `pamgtest`: 460 alternatives, 0 across syncs, 30 replays correct |
| PETSc 3.26 own wildcard sites | - | 480 replays benign |
| MFEM 4.10 | - | 160 replays: 150 benign, 10 inconclusive (ex15p hex) |
| deal.II 9.8.0 | - | 8 419 alternatives, all within one exchange; 120 replays benign |
| openfpm_vcluster, code_saturne, KaHIP | GPU-only clash; NBX never selected; not an NBX between calls | reading |
| MFEM master d259866 `ParFiniteElementSpace::OrderPropagation` (pfespace.cpp:3704-3751): a real race, benign | the barrier orders the Isends, not their delivery, so the `Iprobe(ANY)` drain can miss an order message, which the second drain drops; the fixpoint loop (fespace.cpp:3249) resends it | shipped miniapp `phpref -dim 2 -n 100`, np 4: ~900 flag-flip alternatives per trace; 46 of 46 replays and 10 of 10 at np 8 (3D) correct; the race also occurred unforced in 2 controls |
| SLATE 878439b `ttmqr` (internal_ttmqr.cc:155-189): a freed tile `Isend`, then an `Irecv` into the same tile | the two are not ordered (the program cannot know when a freed send ends), but the peer sends the tile back only after receiving it, so the first transfer ends before the second starts at every eager threshold | `geqrf` tester traced with buffer ranges (`STRETTO_BUFS=1`, 2x2, 4 threads): 84 such pairs, all harmless (docs/model.md section 7) |

The 26 Oct screen of 70 codes, and the 29 Oct screen of ~85 more (deal.II master, PETSc main,
MOOSE, runtimes, climate, CFD, MD and electronic-structure codes), with their safe lists: kept
with the hunt notes, outside this repository.

## Defects and limits of the tool found by the hunts

- Failed polls were out of reach (NBX races sit at an `Iprobe` that returned no message): flag
  flips since 27 Oct 2026. Flips were not forced in replays and were named by a timing-dependent
  segment: fixed 28 Oct (d582a71, 14b6221).
- A trace whose receives are cancelled at shutdown and never completed (ADLB) is reported
  degraded and predicts nothing; the hunt stripped the trailing cancel records. Fixed 29 Oct:
  the tracer records the cancel's outcome at a free and at `MPI_Finalize`, and a receive whose
  cancel succeeded is modelled, posted until its cancel (docs/model.md, cancellation). Swift/T
  traces, unfiltered: 8 of 8 complete, 20 syncsub alternatives each, 8 of 8 replays abort the
  server, their controls correct.
- A flag flip cannot be pinned when one rank has two wildcard-probe sites with different tags
  under one call-site identity: the pin library counts the rank's wildcard probes together, the
  two tags interleave differently in the replay, and the pin lands on the other probe (VTK 9.7
  `vtkPLagrangianParticleTracker`, tags 622 and 624: 40 of 40 replays misapplied). Fixed 29 Oct:
  a probe pin names its envelope's ordinal, a flip pin counts its envelope's probes.
- `horizon_pins` returned no pins for unflips and test flips, so the hunts replayed them with a
  pin of their own. Fixed 29 Oct (`replay_sample.py --flips` samples them too).
- Analysis time: a SuperLU_DIST ABglobal trace of 7 403 events took 1 072 s, 95% of it in the
  generic check of 431 steals at nonblocking receives at each of 57 breakpoints. Fixed 29 Oct:
  such profiles share their closures and graph searches between thresholds
  (`feasible.check_profile`); 24 s, the same report in 3 033 comparisons (NPB MG at 32 processes,
  under load, 2 203 s to 823 s).
- Alternatives that need two changed matches are not predicted (HDF5 ph5diff 1.8.9). Assessed 29
  Oct, not pursued: the ph5diff hang needs a flip and then a probe on the same rank beyond its
  horizon, which no two-change search over the trace's calls can express, and no other known
  case needs two changes.
- Treating every collective as a barrier changes no finding (0 of 115 MBI, 0 of 396 MBB): the
  minimal synchronisation of collectives is not an advantage on these corpora.
- Several threads (milestone A, [threads.md](threads.md) §9): the L1 check did not finish on
  the 1 024-iteration MPICH `mt_*` traces (`mt_isendirecv`, 24 702 records, over 50 min): a
  quadratic enumeration and 2 * 10^7 orientation edges per graph, for 32 700 alternatives. Fixed
  for receive alternatives 29 Oct (§10: 286 s, 159 s and 55 s for the three receive tests); the
  probe tests' 16 400 probe alternatives per trace still go to the generic check at 45 s each,
  so those four traces do not finish. Open. The tracer's issue lock hides
  a crash: untraced, `mt_probe` and `mt_iprobe` abort in their racy phase (MPI_ERR_COUNT, 3 of 3
  runs), traced never; the report names the race, not the crash. The tracer can crash in
  `PMPI_Comm_group` on a freed communicator (CorrBench `task_comm_free`, 1 of 2 runs; older than
  the thread work). Open.
- Replays of several threads (milestone C, [threads.md](threads.md) §11): chain pins name a
  receive by its thread's ordinal, which a pool of worker threads does not keep from run to run
  (HDF5 Subfiling: every replay and control diverged), so such alternatives stay unconfirmed. Open.
  Gating every concurrent pair instead of the reordered ones timed out on the same pools; fixed
  30 Oct. The reduced orientation edges shared one cache between receive and send views and ended
  the analysis of a Uintah trace; fixed 30 Oct (84 multithreaded reports unchanged).
  Records of chains other than a rank's first had no call site (`Trace.site_of` looked the
  process's site ids up under the chain), so `stretto sites` and the report's grouping missed
  worker threads' records; found on Uintah's ARCHES traces, fixed 30 Oct.
- MPI inside OpenMP tasks (SLATE, 30 Oct): every trace with messages was degraded
  `threads-tasks`, and with it the observed thread classes were skipped, so the traces of runs
  in which SLATE's tag race happened reported nothing; `experiments/slate/overlap.py` located the
  race without happens-before. Fixed 30 Oct (milestone D, [threads.md](threads.md) §12): explicit
  tasks are chains ordered by creation, dependences, taskwait, taskgroup and team barriers
  (libgomp's entry points: its runtime has no OMPT), and the race is an L1 concurrent swap or
  steal; still degraded: `taskloop` and detached tasks, and requests completed by another task
  (`threads-handoff`, 9 of SLATE's 39 tester routines). A premise is added (P-T7): the tasks an
  alternative keeps in progress at once must fit the team's threads. The tracer aborted traced
  SLATE `gelqf` and `geqrf` runs (17 and 11 of 20) with `MPI_ERR_TYPE` in its own
  `MPI_Get_count`: SLATE frees a pending receive's datatype (allowed), the library releases it
  when the receive completes, and the tracer then asked about the freed handle (confirmed with a
  diagnostic build). Fixed 30 Oct: the count comes from the type's size at the posting; 20 of 20
  traced runs each pass (idiom 33). SLATE's task threads appeared as untracked chains named `u`;
  fixed with the task chains. Open: replays of task alternatives diverge (SLATE's tasks take
  locks whose order varies; a gated task holds its thread), and one wrong traced run's observed
  matching was judged infeasible (`2-buffering`, observed), not diagnosed.
- Task runtimes other than OpenMP (30 Oct, DLA-Future on pika, PaRSEC): not followed. pika runs
  tasks as user-level threads on worker pthreads (a chain is a worker, not a task), synchronises
  them by atomics and spinlocks, and its MPI polling completes requests on whichever worker polls,
  so every DLA-Future trace is degraded `threads-handoff` and its observed `2-thread-collective`
  (an `Ibarrier` against a `Comm_free`, 7 and 13 per trace) and `2-thread-wait` (2) are false
  alarms of unrecorded synchronisation (P-T2). PaRSEC traces are degraded `threads-cancel` (its
  communication engine cancels its persistent wildcard receives at finalisation). Neither tool
  path predicted the two tag races below; scans of same-envelope operations pending on two threads
  located them (`experiments/dlaf/pending_pairs.py`, `experiments/slate/overlap.py`). Open.
- Buffer-guard findings said nothing of their consequence (30 Oct): a write to a pending send
  buffer is harmless while the library copies the message in the call. The report now gives each
  3-local finding the thresholds at which data changes (docs/model.md section 7), confirmed by
  runs with the eager limit forced just below and at the message
  (`experiments/results/guard/thresholds.csv`). Two premises found false there: MPICH over UCX
  read a pending 40-byte synchronous send's buffer after the call with its rendezvous threshold
  above the message (so synchronous sends are never taken as copied), and Open MPI's TCP
  transport read an eager 4 000-byte message, the first to its peer, after the call (the
  confirmations use shared memory).

## Multithreaded benchmarks (L1 only)

MPICH fb003aa `test/mpi/threads/pt2pt` `mt_*` (16 iterations): every finding is in the racy
phase (one communicator and tag), none in the two control phases. MPI-CorrBench a05644b OpenMP
ordering: 8 of 23 erroneous programs found (4 deadlocks observed, 2 concurrent collectives,
request reuse, Probe-then-Recv as 1f); 2 need lock reordering (L1+SP-T, not built); task
programs were degraded; 0 of 15 correct programs get an error class. With tasks followed (30 Oct,
[threads.md](threads.md) §12): 5 more found (`two_collectives_3` to `_7`), `_8` needs lock
reordering, `nondeterministic_deadlock` overflows the tracer's 256-chain table; 2 programs
labelled correct now get an error class, and both are erroneous as built (concurrent barriers;
4 broadcasts on one rank against 2).

At 1 024 iterations (milestone B, [threads.md](threads.md) §10) the same holds for the three
receive tests: 5, 39 and 49 L1 1f findings, all on the racy phase's threads.

Thread idioms 15, 16, 17 and 20 (Open MPI): each L1 alternative at a receive forced by chain pins
and order gates, twice, with the predicted consequence (wrong header, truncation, hang, wrong order)
and a control that finishes without it ([threads.md](threads.md) §11, `test_replay_e2e.Threads`).

HDF5 Subfiling VFD 9975eaeb2, the wrong-worker race fixed by ffc254983 (the unique data tag that
27bb358 later squashed into develop): 5 of 5 clean traces of `t_subfiling_vfd` (4 ranks) predict at
L1 that two worker threads of the I/O concentrator take each other's write data (4-6 per trace,
1a truncations in an earlier run); 0 of 5 traces of the fix. Untraced, 20 of 20 runs passed. Replayed
30 Oct with chain pins and order gates ([threads.md](threads.md) §11): 0 of 40 replays of 20
alternatives took their pinned messages, every control diverged in its threads' synchronisation (the
concentrator's worker pool hands requests to other workers in each run); not confirmed. Evidence:
[experiments/subfiling](../experiments/subfiling/README.md) (`replay.csv`).

**Uintah 493d2b5 (master on 30 Oct) + hypre 2.33.0, Unified scheduler** (`sus -nthreads`,
Examples/solvertest1, 8 patches), a real bug, observed: every trace shows the `MPI_Allreduce` of
`ApplicationCommon::reduceSystemVars` (`reduceMPI(..., -1)`, ApplicationCommon.cc:499, so
`MPI_COMM_WORLD`) and hypre's (`hypre_StructGridAssemble`, the communicator of
HypreSolver.cc's `pg->getComm()`) called concurrently by two threads (`2-thread-collective`).
The scheduler starts the `usesMPI` task once the other tasks of its phase have been taken, not
finished (UnifiedScheduler.cc:746, 709). Abort stacks put `MPI_ERR_TRUNCATE` in hypre's call,
and the garbage delT 1.23516e-321 is hypre's int 250 read as a double. Untraced, 4 ranks, 40
runs each: Open MPI 14 and 0 pass with 2 and 4 threads, MPICH 7 and 0; with hypre on a
duplicated communicator (a 32-line patch) 40 of 40 in every setting, in both MPIs. The 2021 fix
of the same conflict (fefdd7d) was reverted in Jan 2022; no issue mentions it. L1 predicts no
receive alternative and probe alternatives between hypre messages only. Uintah's nightly
threaded tests (ARCHES `multibox_rmcrt_coal_{1L,2L,DO}_threaded`, 2 ranks, 8 threads) do not
hit it: 180 of 180 runs pass, plain and fixed alike (60 of 60 at 4 ranks, 3 time steps),
since there delT needs the velocities the pressure solve produces, which orders the two
reductions on each rank. Their traces (plain and fixed) show no other conflicting collective:
every observed pair is a worker's delT reduction against the main thread's statistics after
`execute()`, ordered by a spin flag the tracer does not record. Report drafted, not sent.
Evidence:
[experiments/uintah](../experiments/uintah/README.md) (`series.csv`, `nightly.csv`).

**SLATE 878439b (master on 30 Oct; the code is that of releases 2024.05.31 to 2025.05.28),
`getrf` with `MethodLU::CALU` on a wide matrix**, a real bug, found by reading the tag ranges of
unordered tasks: the pivot-to-the-left task swaps rows of column j with tag `2 A_mt + j`
(getrf_tntpiv.cc:347), the trailing updates of column j with `j + A_mt` (lines 250, 294, 320),
so for n > m left column j and right column j + A_mt share a tag, and their task dependences
(lines 289-291, 342-344) leave them concurrent. Untraced, 4 ranks of 4 threads, 1000x2000, nb
100, residual `||PA - LU||` 0.33 to 6.5 instead of 6e-17: 2x2 grid 40 of 40 wrong, 4x1 28 of 40
(Open MPI), 20 of 20 and 20 of 20 (MPICH); with the left tags started at `A_mt + A_nt` (one
line) 0 wrong in every cell; square, one thread, 900x2000 on 2x2 (q does not divide A_mt) 0
wrong. Traced, also `MPI_ERR_TRUNCATE` (3 of 19 runs). Until 30 Oct Stretto degraded every
SLATE trace (`threads-tasks`), and same-envelope `permuteRows` exchanges pending on two threads
at once (16 to 33 per plain trace, 0 per patched one) located it. Since the tracer follows tasks
(milestone D), Stretto predicts it at L1 from clean traces: 9 of 10 plain wide traces (2x2 and
4x1) report 4 to 105 concurrent swaps and steals, every one at a receive in `permuteRows` with a
tag of the collision range (two of them 1a, the 80 000-byte tile into a 36 000-byte row
receive), also in runs whose factor came out right (2 of 10, and 8 more right runs with 37 to 90
each, one 1a); the tenth aborted (`reconstruction`); 0 findings in 10 patched and 15 control traces
(square, partial pivoting, 900x2000) and in the 39-routine sweep. Not confirmed by replay (task
locks and gates diverge). The tester checks `getrf` only on square matrices.
Shown safe by reading: partial-pivoting `getrf` (tag = column, chained by `column[j]`),
`potrf`, `gemmA`/`gemmC`/`herk` (chained broadcasts), `geqrf` and `ttmqr` (disjoint column
ranges), `getrf_nopiv` (shared tags, but row and column process sets meet in one rank), `he2hb`,
the pivot `MPI_Bcast`s in chained panel tasks; 39 tester routines (square 800, 2x2, 4 threads)
pass untraced, with no such overlap in their traces. Report drafted, not sent. Evidence:
[experiments/slate](../experiments/slate/README.md) (`series.csv`, `series-mpich.csv`,
`traced-cells.csv`).

**DLA-Future 09fe442 (master on 30 Oct; since release 0.2.0), `matrix::copy` between two
distributions**, a real bug, found by reading the tags of operations posted as independent pika
tasks through `CommunicatorPipeline::shared()`: the tag (copy.h:215-226) is meant to be a local
tile index, but `ij.row() + ij.col() * ceil(mt / P)` uses the global row, so tiles (i + ld, j)
and (i, j + 1) share a tag when the grid has P > 1 rows, and when both go between the same two
ranks MPI matches them in the order the tasks found their tiles ready. Untraced (`copy_check.cpp`,
an oracle on every element), 20 runs per cell, 2 to 6 ranks: 20 of 20 wrong on 2x1 (source rank
shifted, or block size halved), 4x1 and 3x2; 0 of 20 on 2x2 (no colliding pair), with one pika
thread, and with the global linear tile index as tag (one line) in every cell. The unit test
`CopyReshuffling` has no colliding pair in its configurations. Stretto predicts nothing (pika:
`threads-handoff`, above); `pending_pairs.py` finds 24 pending same-envelope receive pairs in a
plain trace, 0 in a patched one. Shown safe by reading: every collective (`exclusive()`), the
fixed-tag `exclusive()` sends, `bt_band_to_tridiag`'s `shared()` sums, `rot.h`'s serialised
column exchanges, the permutation's tag 0 (one message per pair and call), `band_to_tridiag`'s
four tag families. Report drafted, not sent; its reproduction block, run in an empty directory,
gives 10 of 10 wrong copies and 10 of 10 right ones patched. Evidence:
[experiments/dlaf](../experiments/dlaf/README.md) (`series.csv`, `series-1thread.csv`).

**PaRSEC 6c49997 (master on 30 Oct), local reshapes under `MPI_THREAD_MULTIPLE`**, a real bug,
found by reading which threads call MPI: with `COMM_MT` every computation thread reshapes
inline by `MPI_Sendrecv` on one duplicate of `MPI_COMM_SELF` with tag `es->th_id`
(parsec_mpi_funnelled.c:485-509), and `th_id` numbers the threads of one virtual process
(parsec.c:396), so with two virtual processes (`runtime_vpmap hwloc` on a two-socket node,
emulated by a synthetic hwloc topology) threads t of both share a tag and receive each other's
tiles. Untraced, 20 runs per cell: the shipped `reshape` test fails 3 to 6 of its 7 tests in 20
of 20 runs, a checking variant 20 of 20 (1 rank) and 15 of 20 (4 ranks); 0 with one virtual
process, with `MPI_THREAD_SERIALIZED`, or with tags numbered across virtual processes (an
11-line patch). Traced runs come out right (0 of 10) and are degraded `threads-cancel`;
`overlap.py` finds 2 to 22 same-tag `Sendrecv` pairs pending on two threads in 8 of 10 plain
traces, 0 in 5 patched. Shown safe by reading: activations (received by the communication
thread only), data messages (communication thread, compare-and-swap tags). Side defects, not
races: the `rr:` and `file:` vpmaps crash (vpmap.c:384, 308-327). Report drafted, not sent; its
reproduction block, run in an empty directory, fails 6 of 7 tests in 3 of 3 runs, 0 patched.
Evidence: [experiments/parsec](../experiments/parsec/README.md) (`series.csv`).

Not built in this hunt, by cost (estimated, not measured): HPX's MPI parcelport (read only:
headers on tag 0 with `ANY_SOURCE` carry the sender's tag from an atomic counter, so data
messages are told apart until the counter wraps; HPX's build looks beyond the 3 GB and 6-job
budget), ESMF (Fortran and C++, about an hour's build), Legion/Realm (needs its own interop
driver).

## Not yet traced

PFLOTRAN (perturbation inversion, needs three or more process groups) and SPECFEM3D (HDF5 I/O
server): read only.

VTK 9.7 c5d8bc83 `vtkPLagrangianParticleTracker` (Filters/ParallelFlowPaths, :434-545): rank 0
decides global termination from statuses it drains with `Iprobe(ANY_SOURCE, 622)`; a status
still in flight after a particle hand-over could let it terminate while a particle is live. The
shipped test `TestPLagrangianParticleTracker` (2 ranks) predicts the stale read (status-unflip,
4 per trace). With the envelope pins of 29 Oct, 40 of 40 replays of it (10 traces) applied their
pins and finished with the regression image exact (ImageError 0), and so did their controls:
in this test the stale status is harmless. At 3 or 4 ranks the test's image differs from its
baseline even without the tracer (ImageError 124 and 72), so no larger shipped setting has an
oracle. Not confirmed; inconclusive beyond the 2-rank test.

dolfinx a7b1840 `nbx_consensus_rounds` (MPI.cpp:182-290), two readings that disagree: an
`Irecv(ANY_SOURCE)` listener still posted when the call ends, and cancelled after its Ibarrier,
may match the next call's Issend. Against it: IndexMap runs the exchange on a communicator of its
own, and every other caller creates a communicator right after it, which synchronises in both
MPIs although the standard does not require it. Unverified; a trace with the cancelled listener is
analysable since the fix for cancelled receives above.

Thread-level candidates for the multithreaded extension (read only; [threads.md](threads.md)): Uintah + hypre (a code comment in KokkosScheduler.cc documents
crashes from hypre's `Iprobe` taking a Uintah message on the shared communicator, and the
reverse hazard it leaves); Legion/Realm's MPI network in interop mode (wildcard `Irecv` with tag 1
on `MPI_COMM_WORLD`); DLA-Future (tags reused by concurrent tasks; built and confirmed 30 Oct,
above). For Uintah a build of
Core, Schedulers, Solvers and Examples with hypre (driver `Examples/solvertest1.ups`, `sus
-nthreads`) built in 6 minutes with 2 jobs on 30 Oct (above, [experiments/uintah](../experiments/uintah/README.md)). By reading, not a race: Realm's
`generate_payload_tag` (am_mpi.cc:225) uses the attribute key `MPI_TAG_UB` as the tag bound.
