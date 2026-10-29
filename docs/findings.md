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
| OpenFOAM ESI develop 68a81857c, `Pstream::exchange(Map)`: NBX with a constant default tag; two calls in a row mix their messages | 39 of 39 clean traces predict it; 434 of 434 replays print wrong data, 429 of them with a correct control; plain and delayed stress 1 of 320; `nbx.tuning=1` removes it | the only in-tree caller (Test-parallel-chunks, with `nbx.min=2`) checks nothing; snappyHexMesh tutorials traced with `pbufs.tuning=1` have no cross-call alternative. The 2024 issue 3238 was the same class at call sites |
| libsc 6928417 `sc_notify_payload_nbx`: fixed tag, nothing orders calls | 22 of 22 clean traces predict it; 372 of 372 replays wrong; plain stress 22-24 of 40 wrong; a barrier removes it | NBX is opt-in; the default type PEX is safe |
| libsc `sc_notify_recursive_nary`: per-level tags reused by the next call | 20 of 20 clean traces; 80 of 80 replays wrong, then abort or SEGV | opt-in; p4est callers put a collective between calls |
| libsc `sc_notify_payloadv_nbx`: sends tag NBXV, probes tag NBX | deterministic hang, 20 of 20 (not a race; found by reading) | opt-in, `sorted=0` only |
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
- Analysis time: a SuperLU_DIST ABglobal trace of 7 403 events took 1 072 s. Under study.
- Alternatives that need two changed matches are not predicted (HDF5 ph5diff 1.8.9). Under study.
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

## Multithreaded benchmarks (L1 only)

MPICH fb003aa `test/mpi/threads/pt2pt` `mt_*` (16 iterations): every finding is in the racy
phase (one communicator and tag), none in the two control phases. MPI-CorrBench a05644b OpenMP
ordering: 8 of 23 erroneous programs found (4 deadlocks observed, 2 concurrent collectives,
request reuse, Probe-then-Recv as 1f); 2 need lock reordering (L1+SP-T, not built); task
programs are degraded; 0 of 15 correct programs get an error class.

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

Uintah 493d2b5 + hypre 2.33.0, Unified scheduler (`sus -nthreads`, Examples/solvertest1): every
trace shows Uintah's reduction and hypre's `MPI_Allreduce` called concurrently by two threads on
`MPI_COMM_WORLD` (observed `2-thread-collective`); untraced, 4 of 20 runs at 4 ranks with 2 threads
pass and 0 of 20 with 4 (`MPI_ERR_TRUNCATE` in `MPI_Allreduce`), 20 of 20 with the MPI scheduler.
That the pair is the cause is a hypothesis; L1 predicts no receive alternative and probe
alternatives between hypre messages only. Not reported upstream. Evidence:
[experiments/uintah](../experiments/uintah/README.md).

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
on `MPI_COMM_WORLD`); DLA-Future and SLATE (tags reused by concurrent tasks). For Uintah a build of
Core, Schedulers, Solvers and Examples with hypre (driver `Examples/solvertest1.ups`, `sus
-nthreads`) built in 6 minutes with 2 jobs on 30 Oct (above, [experiments/uintah](../experiments/uintah/README.md)). By reading, not a race: Realm's
`generate_payload_tag` (am_mpi.cc:225) uses the attribute key `MPI_TAG_UB` as the tag bound.
