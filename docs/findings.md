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
| GA bd1149f ComEx `mpi-pt` progress thread, `MPI_Recv(ANY_SOURCE)` of request headers (comex.c:2445) and the put handler's named data receive (:2564) | each handler receives the rest of a request from its sender by name, replies go on another communicator, a fence is acknowledged after the sender's earlier requests; requests of different ranks are unordered in GA's model | blind scan: steals in every test with remote traffic; read after the pre-registration; 74 of 74 forced replays pass like their controls |
| GA bd1149f `threadsafec`, the main threads' reply receives (comex.c:4027, 4102, 3937) | GA's own mutex holds each request with its preposted reply receive, so replies from a node arrive in the order of the receives | no candidate at L1; at L1+L 12 800 candidates in 12 traces, all rejected (`targeted.csv`) |
| Smilei 61aa917 patch exchanges of fields and particles (`Patch::initExchange`, `initSumField`, `exchNbrOfParticles`, Patch.cpp; OpenMP threads post and complete them) | every tag is the sender patch's Hilbert index, a direction digit and a field digit in decimal (`buildtag`, Patch.h:485-502), distinct among the exchanges in flight between two ranks (fields sharing a digit go one after another); receives name source and tag; the phases are separated by the barriers of their `omp for` loops | blind scan: 408 complete traces of 82 benchmarks (Open MPI) and 54 (MPICH), 4 ranks of 2 threads: no alternative at L1, no wildcard or probe |
| Athena++ 053bc73 boundary and AMR exchanges and the FFT remap (`remap_3d.cpp:109`, `MPI_Waitany`) | tags pack the destination block's local id with the buffer and physics (`CreateBvalsMPITag`) or the child offsets (`CreateAMRMPITag`), distinct among messages in flight; every remap receive has its own buffer and output offset, so the `Waitany` order is free; the `MPI_Iprobe(ANY, ANY)` calls only push progress | blind scan, 2 threads per rank: no finding but 408 `waitany-order` per 4-rank `fft` trace; read after the pre-registration |
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
  (HDF5 Subfiling: every replay and control diverged). Fixed 30 Oct (milestone E, §14): the
  tracer records the lock order (`STRETTO_SYNC_ORDER=1`) and the replay gates it, so a chain's
  ordinals name the trace's work; on idioms 44 and 45 (a worker pool, tasks taking roles under
  a lock) 10 of 10 replays confirmed against 0 of 10 without lock gates, whose replays
  included 3 wrong refutations. Alternatives across HDF5's pools of two files (an atomic flag
  orders them; over-predictions) closed 1 Nov by recording atomics (§17). SLATE replays that
  needed more threads than the trace's team (P-T7; on 31 Oct teams of 8 and of 16 still starved in
  8 of 28 SLATE replays, §16): since 2 Nov (§19) a gate that stays closed while nothing on its rank
  moves gives way, tasks start in the trace's order, and the control follows the run beyond the
  horizon; at the trace's 4 threads 0 of 14 replays starve (13 of 14 before), 14 of 14 controls
  come out right (9 of 14 without the part beyond the horizon), and 7 of 14 replays are confirmed
  (0 before). Still open: the tasks of tasks get no priority (libgomp 14.2 crashes in
  `GOMP_taskwait` when an explicit task's children have different priorities), so gates still
  give way there, and a replay whose gate gave way may synchronise otherwise than its trace.
  Gating every concurrent pair instead of the reordered ones timed out on the same pools; fixed
  30 Oct. The reduced orientation edges shared one cache between receive and send views and ended
  the analysis of a Uintah trace; fixed 30 Oct (84 multithreaded reports unchanged).
  Records of chains other than a rank's first had no call site (`Trace.site_of` looked the
  process's site ids up under the chain), so `stretto sites` and the report's grouping missed
  worker threads' records; found on Uintah's ARCHES traces, fixed 30 Oct.
- Replay harness on the races of milestone F (30-31 Oct, [threads.md](threads.md) §16): a
  handed-off receive was judged by its posting chain's cut (fixed, `test_confirm.Handoffs`);
  the tracer posted a `Sendrecv`'s send and receive under one hold of its issue lock, so no
  replay could swap two threads' `Sendrecv`s (PaRSEC; fixed: a gate that holds the receive back
  splits them, idiom 47); a replay that aborted on a misapplied pin hung at a lock gate inside
  Open MPI's abort and read as a gate timeout (DLA-Future; fixed).
- Replay harness, 2 Nov ([threads.md](threads.md) §19): a lock taken before a send that the
  horizon issues without completing it lay outside the horizon, so idiom 22's mutex was not gated
  (fixed); a gate that gave way inside a predicted lock-order deadlock released a chain the
  deadlock keeps waiting (24 of the 27 `ThreadLevel_serialized` replays that finished; fixed: a
  replay of a predicted hang keeps its gates); L1+L findings could not be replayed at all (built:
  the schedule's lock order, 71 of 108 replays confirmed); replays of an instrumented program are
  judged diverged by the order of its atomics (PaRSEC: 60 of 60 replays and controls; open, no
  cheap remedy).
- MPI inside OpenMP tasks (SLATE, 30 Oct): every trace with messages was degraded
  `threads-tasks`, and with it the observed thread classes were skipped, so the traces of runs
  in which SLATE's tag race happened reported nothing; `experiments/slate/overlap.py` located the
  race without happens-before. Fixed 30 Oct (milestone D, [threads.md](threads.md) §12): explicit
  tasks are chains ordered by creation, dependences, taskwait, taskgroup and team barriers
  (libgomp's entry points: its runtime has no OMPT), and the race is an L1 concurrent swap or
  steal; still degraded: `taskloop` and detached tasks, and requests completed by another task
  (`threads-handoff`, 8 of SLATE's 39 tester routines in the run with lock events, 9 before;
  some of them may be the handle-reuse defect below). A premise is added (P-T7): the tasks an
  alternative keeps in progress at once must fit the team's threads. The tracer aborted traced
  SLATE `gelqf` and `geqrf` runs (17 and 11 of 20) with `MPI_ERR_TYPE` in its own
  `MPI_Get_count`: SLATE frees a pending receive's datatype (allowed), the library releases it
  when the receive completes, and the tracer then asked about the freed handle (confirmed with a
  diagnostic build). Fixed 30 Oct: the count comes from the type's size at the posting; 20 of 20
  traced runs each pass (idiom 33). SLATE's task threads appeared as untracked chains named `u`;
  fixed with the task chains. Replays of task alternatives diverged (SLATE's tasks take locks
  whose order varies; a gated task holds its thread); with the lock order replayed and a team of
  8 (§14) 15 of 18 replays took their pins. Open: the observed matching of wrong traced 2x2 runs
  is judged infeasible (`2-buffering`, observed; 4 of 13 in the remeasurement of 30 Oct, 1 of 5
  before), not diagnosed. Open, found in that remeasurement: the tracer misnames a request whose
  handle MPI freed in one thread's wait and gave to another thread's new request before the
  first wrapper dropped it (`req_find_locked` returns the oldest entry of the handle); the trace
  is then degraded `threads-handoff` (2 of 35 SLATE cell traces, 1 of 5 diagnostic runs;
  experiments/slate/README.md).
- Lock order (milestone A, [threads.md](threads.md) §9): an alternative that needs two critical
  sections of one lock in the other order was never predicted (CorrBench `deadlock_probe`,
  `two_collectives_8`, `_9`). Fixed 30 Oct (level L1+L, §13, under premise P-T8); checked against
  a new exhaustive explorer of threaded programs with locks (E6 for threads: 6 000 random programs,
  no unsound claim). Found on the way: L1 of several threads accepted a receive taking a message
  while an earlier one of its channel waited for a receive beyond its thread's cut (1 random
  program of 3 000); fixed, which narrows 22 findings of three MPICH `mt_*` traces to B = 0. The
  tracer hung every OpenMP idiom in `MPI_Init_thread` under MPICH once its chain structure grew:
  UCX's memory hooks take a lock whose wrapper allocated the chain again inside the allocation;
  fixed. Main's analysis reads the new `locks` side records as calls (spurious
  `2-thread-finalize`): traces with lock events need this analysis.
- Task runtimes other than OpenMP (30 Oct, DLA-Future on pika, PaRSEC): not followed. pika runs
  tasks as user-level threads on worker pthreads (a chain is a worker, not a task), synchronises
  them by atomics and spinlocks, and its MPI polling completes requests on whichever worker polls,
  so every DLA-Future trace is degraded `threads-handoff` and its observed `2-thread-collective`
  (an `Ibarrier` against a `Comm_free`, 7 and 13 per trace) and `2-thread-wait` (2) are false
  alarms of unrecorded synchronisation (P-T2). PaRSEC traces are degraded `threads-cancel` (its
  communication engine cancels its persistent wildcard receives at finalisation). Neither tool
  path predicted the two tag races below; scans of same-envelope operations pending on two threads
  located them (`experiments/dlaf/pending_pairs.py`, `experiments/slate/overlap.py`). Closed 30
  Oct (milestone F, [threads.md](threads.md) §15): a request completed by another thread is
  modelled (premises P-T9, P-T10), and so is a receive cancelled by another thread (P-T11); both
  races are predicted at L1 (below). The false alarms of pika's unrecorded synchronisation
  stayed until pika was compiled for the tracer's atomics (1 Nov, next entry).
- Synchronisation through atomics (1 Nov, [threads.md](threads.md) §17): spin flags, atomic
  counters and pika's lock-free queues were invisible (P-T1, P-T2, P-T10 failed). The tracer now
  records the atomics, fences and volatile accesses of code compiled with clang's
  ThreadSanitizer instrumentation and linked against a stub runtime of its own
  (`tracer/tsan_rt.c`), as clocks per location, so the edges reach `after` like a mutex's; an
  annotation API (`tracer/stretto_sync.h`) covers the rest. On the real cases
  ([experiments/atomics](../experiments/atomics/README.md)): HDF5 Subfiling's cross-pool
  alternatives 56 of 60 to 0 of 5 (the 5 left are the real race); DLA-Future's observed
  `2-thread-collective` and `2-thread-wait` 0 in all 50 traces (up to 15 and 6 unrecorded); Uintah
  ARCHES with its scheduler instrumented: concurrent collective pairs 12 per trace to 0 (its two
  `2-thread-wait`s stay: a lock-free pool that claims requests with relaxed atomics, which order
  nothing). The confirmed races stay: DLA-Future 15 of 15 plain traces, PaRSEC 10 of 10, SLATE
  (not instrumented) 10 of 10 re-traced, 0 on the patched and control traces. `waitany-order`
  warnings stay (Testsome completion order, which no synchronisation decides).
- Request handles reused across threads (30 Oct, SLATE): the tracer credited one thread's
  completion with another thread's request when the library shared a handle between them (a
  send completed at once) or freed one inside a thread's wait and handed it to another thread's
  next request; traces were degraded `threads-handoff` (idiom 46: 40 of 40 runs). Fixed: entries
  remember their thread and are claimed before the completion call ([threads.md](threads.md)
  §15.1).
- Observed `2-buffering` on threads (30 Oct, SLATE 4 of 13 wide traces; PaRSEC 6 of 10 once its
  cancels are modelled, milestone F): the reduced orientation edges cached physical order by
  operation key, which a send and a receive of one thread share, and oriented sends against the
  run; the observed matching then looked infeasible and few alternatives were judged. Fixed
  ([threads.md](threads.md) §15.2).
- Buffer-guard findings said nothing of their consequence (30 Oct): a write to a pending send
  buffer is harmless while the library copies the message in the call. The report now gives each
  3-local finding the thresholds at which data changes (docs/model.md section 7), confirmed by
  runs with the eager limit forced just below and at the message
  (`experiments/results/guard/thresholds.csv`). Two premises found false there: MPICH over UCX
  read a pending 40-byte synchronous send's buffer after the call with its rendezvous threshold
  above the message (so synchronous sends are never taken as copied), and Open MPI's TCP
  transport read an eager 4 000-byte message, the first to its peer, after the call (the
  confirmations use shared memory).

- An observed `2-buffering` on a cycle through a wildcard receive is not a deadlock of the
  program (1 Nov, GA's progress thread, [experiments/ga](../experiments/ga/README.md)): the
  class says the observed matching is infeasible below B, and with `ANY_SOURCE` on the cycle a
  rendezvous run takes another message first. 3 of 36 traces of GA's `testc`, `ga-mpi` and
  `mtest` reported it (below 400 bytes, and 9 608 with MPICH); 0 of 60 plain runs at 0
  buffered bytes hung. Not changed (the class is scored on the corpora); a report could demote
  it to a warning when a wildcard receive on the cycle has another candidate.
- Two analysis costs on GA's traces (1 Nov): a signature of one basic type was flattened copy by
  copy (2 MB `MPI_CHAR` receives: 280 s to 16 s on a 2 700-record trace once merged at once,
  5fec36b), and the check for two threads waiting on one request compared every pair of a
  rank's waits (4 600 per rank in `threadsafec`, over an hour; grouped by request, 6a36b6f).
  Both give the same reports. Still beyond reach: traces above 60 MB, and `threadsafec`'s 20 000
  candidates per trace at the progress thread's receive for the generic check.
- A code that enters an OpenMP parallel region per time step overflowed the tracer's 256-chain
  table at once (each region's thread j > 0 was a chain), and a program that writes through
  MPI-IO (parallel HDF5) got observed findings only (1 Nov, Smilei,
  [experiments/smilei](../experiments/smilei/README.md)). Two tracer settings, off by default:
  `STRETTO_OMP_CHAINS=thread` (one chain per OpenMP thread across a chain's regions, 57cad0c)
  and `STRETTO_MPIIO=coll` (MPI-IO's collective calls as barrier-shaped collectives on a
  duplicate of the file's communicator, 85fe6cd); [threads.md](threads.md) section 18.
- A receive still pending when a hung run is stopped, with the one pending send of its envelope,
  is reported as an L1 `1f` alternative: the receive's own message, which MPI matches as soon as
  both are posted, but neither completion is in the trace (2 Nov, Smilei: 31 per trace in 12 of
  12 hung traces, all of this kind; GA's S2 had the same shape). Not changed; a report could
  drop a pair when the send is the only pending one the receive accepts.
- Analysis memory is about 15 times the trace (a 634 MB Smilei trace: 9.2 GB, 9 min), which
  bounded the Smilei scan to traces of at most 400 MB: 85 of its 173 benchmarks.

## Multithreaded benchmarks (L1, and L1+L since 30 Oct)

MPICH fb003aa `test/mpi/threads/pt2pt` `mt_*` (16 iterations): every finding is in the racy
phase (one communicator and tag), none in the two control phases. MPI-CorrBench a05644b OpenMP
ordering: 8 of 23 erroneous programs found (4 deadlocks observed, 2 concurrent collectives,
request reuse, Probe-then-Recv as 1f); 2 need lock reordering (built 30 Oct, below); task
programs were degraded; 0 of 15 correct programs get an error class. With tasks followed (30 Oct,
[threads.md](threads.md) §12): 5 more found (`two_collectives_3` to `_7`), `_8` needs lock
reordering, `nondeterministic_deadlock` overflows the tracer's 256-chain table; 2 programs
labelled correct now get an error class, and both are erroneous as built (concurrent barriers;
4 broadcasts on one rank against 2). With lock reordering (level L1+L, 30 Oct,
[threads.md](threads.md) §13; all 38 retraced): 16 of 23 found, the 3 new ones at L1+L
(`dependant/deadlock_probe` 2-lock-order, `two_collectives_8` and `_9` 2-thread-collective); 3 of
15 correct programs get an error class, the new one `correct/dependant/deadlock_probe`
(2-lock-order: the Mprobe fix keeps the mutex held across the probe, and its source expects the
deadlock under the other thread order); the 7 `mt_*` reports are unchanged. Forced by replay
(2 Nov, [threads.md](threads.md) §19.2, `experiments/mustvc/lock-replays.csv`): the replay enters
the critical sections in the analysis's order; `two_collectives_8`, `_9`, idiom 43 and MUST-vc's
`two_collectives_4` 48 of 48 replays take the reordering, idioms 22 and 27 8 of 8 show their
consequence, `deadlock_probe`'s deadlock hangs 6 of 8 replays (3 confirmed: one copy's controls
hung on its probe race), `ThreadLevel_serialized` 12 of 40.

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
concentrator's worker pool hands requests to other workers in each run). Replayed again in the
trace's lock order (§14, 13 new traces per version, the analysis of 5fdd932): 58 of 65
alternatives put a worker of one file's pool on another file's data, over-predictions of an atomic
flag the tracer does not see (0 of 32 replays took their pins; 32 of 32 controls clean); of the 7
within one pool, those of a run that passed (a 1264-byte write into a 96-byte receive) were
realised in 4 of 6 replays, the truncation in the replay's trace, with 6 of 6 controls
reproducing the run: confirmed with the truncation as the outcome (HDF5 returns the error and goes
on, so the run itself finishes); those of a run that crashed could not be replayed (15 of 15
timed out). Evidence:
[experiments/subfiling](../experiments/subfiling/README.md) (`replay.csv`, `results-e.csv`,
`replay-e.csv`, `replay-same.csv`). With HDF5 compiled for the tracer's atomics (1 Nov, §17,
`results-atomics.csv`): 5 alternatives in 2 of 13 `pre` traces, all within one pool, against 60
(56 across pools) for the gcc build in the same session; 0 in 13 `fix` traces.

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
`execute()`, ordered by a spin flag the tracer did not record (with the scheduler compiled for
the tracer's atomics, 1 Nov, 0 such pairs: [threads.md](threads.md) §17). Report drafted, not sent.
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
(milestone D), Stretto predicts it at L1 from clean traces. Remeasured 30 Oct with lock events,
level L1+L and the L1 fix (main 5fdd932): 16 of 18 plain wide traces (13 on 2x2, 5 on 4x1)
report 1 to 85 concurrent swaps and steals, all at receives of the collision's tags 20-27, all
in `permuteRows` but one at the colliding tile broadcast's receive (1a in 5 traces, the
80 000-byte tile into a 36 000-byte row receive); the other two are degraded (an aborted run,
`reconstruction`; a tracer handle-reuse defect, `threads-handoff`); none at L1+L; 0 findings in
10 patched and 15 control traces (square, partial pivoting, 900x2000) and in the 39-routine
sweep. No traced run of the remeasurement had a right factor (16 wrong, 2 aborted); before it,
17 of 18 plain traces had findings (4 to 105), 10 of them from runs whose factor came out right
(37 to 90 each), so the prediction from runs without the error rests on the earlier traces.
Remeasured again with milestone F's fixes on 16 processors (30 Oct, `traced-cells-16.csv`): all
42 plain wide traces (15 on 2x2, 27 on 4x1) are analysed completely and report 15 to 109
concurrent swaps and steals at `permuteRows` receives (1a in 7), none degraded, none with an
observed 2-buffering; 7 of these runs had a right factor (1 on 2x2, 6 on 4x1) and each of their
traces predicts the race (36 to 108 alternatives); 0 findings in 25 patched and control traces.
Replayed in the trace's lock order with 8 threads (§14): 15 of 18 replays of six 1f alternatives
took their pins, with a control reproducing the run in 11, but the traced runs of that session
all had the race already, so no consequence tells them apart; two 1a alternatives stopped at a
gate (6 of 6, not diagnosed). Forced replays of right-factor runs (31 Oct, [threads.md](threads.md)
§16): of 5 right 4x1 traces, 16 of 20 1f replays took every pin and gave a wrong factor, 3 of
them beside a right control (confirmed); the other controls went wrong too (the run beyond the
horizon is free) or starved (P-T7); the 1a truncation was forced from a right run in 2 of 2
replays with right controls, and on 2x2 from a wrong run in 1 of 6 with the truncation shown at
the changed receive. The milestone E stall was not seen again once a replay's abort opened its
gates. The tester checks `getrf` only on square matrices.
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
`CopyReshuffling` has no colliding pair in its configurations. Until milestone F Stretto predicted
nothing (pika: `threads-handoff`, above); `pending_pairs.py` finds 24 pending same-envelope receive
pairs in a plain trace, 0 in a patched one. Since then (30 Oct, 5 traced runs per cell and
variant): 15 of 15 plain traces of the three colliding cells (2x1, 4x1, 3x2) predict L1 swaps or
steals of two receives with one colliding tag (34 to 206 per trace), 0 of 15 patched ones and 0
of 20 controls (2x2 without a colliding pair, one pika thread), plain or patched; every trace
with several workers keeps the false alarms of pika's unrecorded synchronisation (none once pika is
compiled for the tracer's atomics, 1 Nov, §17, where 15 of 15 still predict the race), and every one
`waitany-order` warnings of pika's polling (`experiments/dlaf/traced.csv`). All plain traced runs
of the colliding cells were wrong. Not confirmable by forced replay (30 Oct, [threads.md](threads.md)
§16): 0 of 24 replays and 0 of 24 controls of 12 alternatives ran their course, all ending on a
misapplied lock or chain pin, since pika's workers take other tasks in every run (lock-free
queues, work stealing; P-T5 fails for workers). Shown safe by reading: every collective (`exclusive()`), the
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
11-line patch). Traced runs come out right (0 of 10) and were degraded `threads-cancel`;
`overlap.py` finds 2 to 22 same-tag `Sendrecv` pairs pending on two threads in 8 of 10 plain
traces, 0 in 5 patched. Since milestone F (30 Oct) Stretto predicts the race at L1 from these
runs without it: 10 of 10 new plain traces with two virtual processes (1 rank and 4 ranks) have
15 to 20 swaps or steals of two threads' reshape receives on a self duplicate, at the shared
tag; 0 of 10 patched and 0 of 20 controls (`MPI_THREAD_SERIALIZED`, one virtual process); all 40
runs right (`experiments/parsec/traced.csv`). The 10 earlier plain traces give 15 or 17 each.
Confirmed by forced replay (30 Oct, [threads.md](threads.md) §16): of 10 new right runs, 3
alternatives each, twice, 53 of 60 replays (27 of 30 alternatives) took every pin and failed the
check (one or two tiles exchanged) while their controls came out right with every pin taken; 7
inconclusive (gate and control timeouts). It needed the tracer to let a gate come between a
`Sendrecv`'s two postings (idiom 47).
Shown safe by reading: activations (received by the communication
thread only), data messages (communication thread, compare-and-swap tags). Side defects, not
races: the `rr:` and `file:` vpmaps crash (vpmap.c:384, 308-327). Report drafted, not sent; its
reproduction block, run in an empty directory, fails 6 of 7 tests in 3 of 3 runs, 0 patched.
With PaRSEC compiled for the tracer's atomics (1 Nov, [threads.md](threads.md) §17): 10 of 10 plain
traces still predict it (486 to 764 alternatives: fewer pairs are concurrent, so fewer are
rejected by orientation cycles), 0 of 30 patched and controls.
Evidence: [experiments/parsec](../experiments/parsec/README.md) (`series.csv`, `traced-atomics.csv`).

**MUST-vc against Stretto (1 Nov 2026, [experiments/mustvc](../experiments/mustvc/README.md)).**
MUST's vector-clock checks for MPI+OpenMP (SC-W 2024 artifact, MUST v1.9-omp-checks, its Debian
12 / MPICH 4.0.2 / clang 15 environment) run on everything above. Its own suite reproduces its
table (clock layout: 115 of 115 erroneous runs flagged, 5 of 135 correct runs, all
`concurrent_communicator_transitive`). Of the four thread bugs it reports one: SLATE's CALU
collision, the culprit receives in 10 of 10 plain runs and nothing in 10 fixed ones, but only
after a 2-line SLATE change (its static initialisers start OpenMP before `MPI_Init`, and
MUST-vc then crashes in 10 of 10 runs). PaRSEC, Uintah and DLA-Future run threads of their own,
and its checks run only inside OpenMP regions: PaRSEC 0 reports in 10 runs; Uintah 0 reports,
each of 12 runs stalling before the first time step; DLA-Future crashes natively under MPICH
4.0.2 and, rebuilt for Open MPI, under MUST-vc in 20 of 20 runs. On MPI-CorrBench's 103 OpenMP programs as published it flags none: clang starts the
OpenMP runtime at the entry of `main` when it has a `num_threads` clause, which MUST does not
support (300 of 309 runs warn); without the clause 14 erroneous programs. Stretto flags 26 in
every run and 4 in some, and alone finds the critical-section and lock-order cases
(`two_collectives_8`, `_9`, `deadlock_probe`, idiom 43), the request completed by another thread
(`request_reuse`, idiom 26), and all pthreads idioms. Three programs MUST-vc's suite labels
correct deadlock natively (`two_collectives_4` 7 of 10 runs with MPICH and libomp,
`ThreadLevel_serialized` 6 of 10 and `concurrent_communicator_tasking_locks` 8 of 10 with Open MPI
and libgomp); Stretto reports them (`2-lock-order`, `2-thread-collective` at L1+L, observed
hangs), MUST-vc does not. Where Stretto is behind: a probe that a concurrent receive leaves
without a message (MUST-vc's two `RecvProbe` programs, 0 of 10), `MPI_THREAD_SERIALIZED`
violations by local calls such as `MPI_Comm_size` (not traced; 3 programs, 0 of 15), task
programs beyond the tracer's 256 chains (degraded, false observed classes on 2 correct
programs), and the transitive program, which both tools flag. Side defect found on the way:
PaRSEC's `MPI_Allreduce(MPI_BYTE, MPI_SUM)` (parsec.c:2833) is undefined in MPI, and MPICH
rejects it, so PaRSEC on several ranks aborts at start under MPICH.

**Global Arrays bd1149f (master on 1 Nov), ComEx's progress-thread port (`--with-mpi-pt`)**,
the first blind scan: the shipped tests traced and analysed, the findings committed (d68b241,
1 Nov 18:39 +08) before ComEx's communication code was read. 359 traced runs of 30 C tests, 4
ranks, one or two ranks per node (nodes emulated by answering `gethostname`/`gethostid` to
ComEx only), Open MPI and MPICH. No new bug that the tool found first. Its predictions: steals
at the progress thread's `MPI_Recv(ANY_SOURCE)` of request headers (comex.c:2445; 2 to 1 397
per trace, every test with remote traffic) and once at the put handler's data receive; no
alternative at any receive of a main thread at L1, and at L1+L in `threadsafec` (OpenMP threads
calling GA) none of 12 800 candidates accepted. Read after: harmless by design (each handler
receives the rest of a request from its sender by name, replies travel on another
communicator, a fence is acknowledged after the sender's earlier requests; GA's own mutex holds
each request with its preposted reply receive), and 74 of 74 forced replays of the steals (six
tests, both MPIs) took every pin and passed like their controls. A real bug, deterministic,
that the scan's observed classes surfaced rather than predicted: `nbtestc` hangs in every run
(rank 0 outside MPI, its puts never received), because `comex_test` (comex.c:1346-1374) never
retires a completed request, so `NGA_NbTest` on a pending operation loops forever. Untraced,
20 runs per cell, Open MPI and MPICH, one node and emulated nodes: 120 of 120 plain runs hang,
0 of 120 with a 15-line patch (control `testc`: 2 of 240 runs past the 30 s limit, one per
variant, MPICH, slow); GA's CI builds no `mpi-pt`; report drafted, not sent, its reproduction
block 3 of 3 hangs and 3 of 3 passes patched. Tool false alarm: an observed `2-buffering` in 3
of 36 traces of three tests (tool limits, above). Also by reading, not by the tool: `pnga_lock`
derives the ComEx mutex from `num_mutexes` instead of its argument, so all GA mutexes are one,
and a process holding one that asks for another waits for itself (a driver: 20 of 20 hang, 20
of 20 pass with a 6-line fix). Test defects: `read_only` and `cache_test` call `MPI_Init`,
which this port rejects. Evidence: [experiments/ga](../experiments/ga/README.md).

**Smilei 61aa917 (master on 1 Nov)**, a particle-in-cell code whose OpenMP threads post their
own patch exchanges under `MPI_THREAD_MULTIPLE`, the second blind scan: 426 traced runs of 85
shipped benchmarks (of 173 with a validation script; the rest above 400 MB of trace, or not
runnable here), 4 ranks of 2 threads, 3 runs each with Open MPI and, for the 57 with traces of
at most 150 MB, MPICH; each run checked by Smilei's validation; findings committed (2aa26c9, 2
Nov 06:58 +08) before the communication code was read. The tracer needed two new settings first
(one chain per OpenMP thread across regions, MPI-IO as collectives; tool limits, above). No
trace has a wildcard receive or a probe, and no trace but the 12 of hung runs has an alternative
at L1: the tags are unique among the exchanges in flight (sites shown safe). A real bug,
deterministic, that the scan's observed classes surfaced rather than predicted: the two 3D
benchmarks that inject an envelope laser from xmin hang in every run (12 of 12 traced, rank 0
outside MPI and every other thread waiting on it), because with Python 3.12 the master thread
holds the GIL through the `omp for` of `VectorPatch::injectEnvelopeFromXminIfNeeded`, and a
thread with an xmin patch waits in `PyGILState_Ensure` for it (the loop lacks the
`SMILEI_PY_SAVE_MASTER_THREAD` pair Smilei's other such loops have). Untraced, 20 runs per cell:
80 of 80 plain runs hang and 80 of 80 patched runs pass their validation (Open MPI and MPICH, 4
ranks of 2 threads; 6 of 6 and 6 of 6 with 4 threads; 1 thread passes unpatched, 20 of 20);
report drafted, not sent, its reproduction block 3 of 3 hangs and 3 of 3 passes patched from a
fresh clone. Not a race: no forced replay applies. Tool false alarm: the 31 L1 alternatives per
hung trace are pending receives with their own pending messages (tool limits, above). The issue
307 the audit cited is an invalid tag with Intel MPI (tags about 100 times the patch count), not
a race. Evidence: [experiments/smilei](../experiments/smilei/README.md).

**Athena++ 053bc73 (master on 2 Nov)**, hybrid MPI+OpenMP, the third blind scan: its MPI regression
tests (`hybrid_linwave`, `mpi_linwave`, `jeans_3d`, `turb_3d`, `fft`) through its own `run_tests.py`
and two shipped AMR inputs, built `-mpi -omp`, 2 threads per rank, 3 runs each, Open MPI and (the
linear-wave tests and the examples) MPICH; findings committed (7199084, 2 Nov 11:44 +08) before
the communication code was read. 51 traces analysed; the 4-rank linear-wave calls and the DMR
example (above 1 GB) and `jeans_3d`'s 2-rank call (above 1.5 GB) were not. One class: 408
`waitany-order` per 4-rank `fft` trace at the remap's `MPI_Waitany`, harmless (each receive has its
own buffer and output place). No other finding, and none by reading: tags are unique among
messages in flight, receives name their source, and the wildcard `MPI_Iprobe` calls only push
progress. The audit's lead, tags packed without a check against `MPI_TAG_UB`, is real (a `\todo`
in the code) but needs 2^17 blocks per rank with MPICH. No bug found. Evidence:
[experiments/athena](../experiments/athena/README.md).

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
