# MADNESS: a task pool and a communication thread that receives with wildcards

Target 4 of the third thread hunt (2 Nov 2026), a blind scan like those of Global Arrays
([experiments/ga](../ga/README.md)), Smilei ([experiments/smilei](../smilei/README.md)) and
Athena++ ([experiments/athena](../athena/README.md)): the shipped tests are traced and analysed
first, the findings committed (the pre-registration), and only then is the communication code
read at the reported sites. The reason to look here (an audit, unverified before the scan): a
pthreads task pool plus a communication (RMI) thread that receives with wildcards, the shape of
DLA-Future's bug.

MADNESS master 64eaf80 (29 Oct 2026), configured by its CMake with MPI (`MPI_THREAD_MULTIPLE`,
its default), the pthreads task backend (its default), static libraries, reference LAPACK and
BLAS, and without its optional packages (libxc, IntegratorXX, DFT-D3, LIBXSMM, PCM), MACHINE=a-p13,
CPUs 0-7,40-47, in the experiment image plus clang 18, cmake, LAPACK, nlohmann-json and
googletest (`Dockerfile`, on the image of experiments/atomics). `build.sh` builds the runtime
and the numerical library with the 27 unit tests this scan runs: the 22 of the runtime
(`src/madness/world`) and the 5 MPI tests of the numerical library (`src/madness/mra`, which
`add_mpi_tests` runs on 2 ranks), about 6 minutes with 8 jobs. What was read before the
pre-registration: `CMakeLists.txt` and `cmake/modules/AddMPITests.cmake` (options, the thread
level, the test lists, `MAD_NUM_THREADS` and `MAD_CHECK_BINDING`), `INSTALL.md`, the names of
the files in `src/madness/world`, the names of those among them that mention `std::atomic`,
`__atomic` or `__sync_` (a grep, file names only: `atomicint.h`, `future.h`,
`dependency_interface.h`, `thread.h`, `worldmutex.h`, `worldrmi.*` and others), the macro lines
of `atomicint.h` that choose its implementation (`MADATOMIC_USE_CXX`, ...), and the function
names and line numbers that `stretto sites` prints.

## What the tracer records here

MADNESS synchronises its pool through atomics (futures, dependency counters and the task queue
use `madness::AtomicInt`, which is `std::atomic`, `MADATOMIC_USE_CXX`), so the scan traced the
build compiled with clang 18 and ThreadSanitizer's instrumentation of atomics, linked against
the tracer's stub (`build.sh` with `TSAN=1`, the trees `mad-<mpi>-plain-tsan`; docs/threads.md
section 17): every trace's `init` record says `atomics:1` and `MPI_THREAD_MULTIPLE` with the
issue lock on (a traced `test_dc` on 2 ranks: 2.8 million acquires and 276 000 releases on rank
0). Each rank has two chains that call MPI, its main thread and one thread it created
(named `0.1` in the reports), and in `test_world` a third (`0.0`); the runtime's preposted
receives are posted by the main thread and completed by the other (premise P-T9, up to 557
requests per trace). Two tool changes were needed before any of this, both off the scan's
critical path for the protocol (no MADNESS code read for them): the tracer kept a clock of
8 KiB per address ever used for a lock, and a traced `test_world` was killed by a 12 GB cap
(04d9f71, docs/threads.md section 20); and three analysis costs on these traces (a14ee43: the
double-wait check, the completion-order queries, rule 6's recursion, which crashed Python with
SIGSEGV), with an option `--no-order` that counts the completion-order candidates instead of
judging them (418 459 in a 217 MB `test_dc` trace, none of them at a receive).

## Blind scan (pre-registration)

Recorded before any of MADNESS's communication code was read (`src/madness/world/worldrmi.*`,
`worldgop.*`, `worldam.*`, `safempi.*`, `world_object.h`, `worlddc.h`); what was read is listed
at the top, and, for naming the sites, the function names and line numbers of gdb backtraces
at `MPI_Irecv` and at SafeMPI's `Irecv`/`Isend` wrappers in untraced runs of `test_hashdc` and
`test_worldptr` (4 ranks, the gcc build), with the arguments of each `MPI_Irecv`.

**What ran.** `scan.sh` (scripts of 4256eda; tracer of 04d9f71, analysis of a14ee43), 2 Nov
13:32-16:40 +08, MACHINE=a-p13, CPUs 0-7,40-47, two containers (Open MPI 4.1.6 and MPICH 4.2.0)
side by side: the 27 unit tests of `build.sh`, 2 and 4 ranks, a pool of `MAD_NUM_THREADS=2`
threads per rank, `MAD_CHECK_BINDING=OFF`, 3 traced runs each, 324 runs; each trace of at most
300 MB analysed at L1 (`stretto analyze --json --no-order`) and its sites named (`stretto
sites`). Tables: [scan-openmpi-runs.csv](scan-openmpi-runs.csv),
[scan-mpich-runs.csv](scan-mpich-runs.csv) (one row per run: result, trace size, report status,
findings by level and class, the analysis commit and the commit) and `scan-*-sites.csv` (one row
per run and site with a finding). Then `targeted.sh` (scripts of 7fdfcb7), 16:51-17:05 +08: for
the 30 traces whose analysis stopped at the time limit, the census of all their candidates by
site and chain and a random sample of 100 per trace judged as the report judges them (the order
filter, then the generic check at B = 0 and B = inf): [targeted-openmpi.csv](targeted-openmpi.csv),
[targeted-mpich.csv](targeted-mpich.csv). Deviations, all recorded in the tables or here:

- The analyses had an hour each at the start; the 4-rank traces of `test_future2` (19 MB) took
  more than 15 minutes each, and both scans were stopped at 14:10 and at 14:19 +08 and started
  again with `RESUME=1` (the runs with a result kept, the analyses without a report made again)
  and limits of 900 and then 300 s; rows "analysis stopped at its time limit" are those of the
  300 s limit, and their traces of at most 60 MB are the ones `targeted.sh` judged. The resume
  option was added to `scan.sh` for this (squashed into 4256eda).
- Size limits: a run whose trace passed 600 MB was killed (rows "run killed", 21 with Open MPI,
  12 with MPICH: `test_eval`, `test_vectormacrotask` and `test_world` on 2 ranks with Open MPI, and
  the 4-rank runs of `test_halo` and `test_mul_sparse` with Open MPI), and traces of 300-600 MB were
  deleted unanalysed (rows "not analysed": `test_dc` on 4 ranks, `test_halo` and `test_mul_sparse`
  on 2 with both MPIs and on 4 with MPICH, `test_world` on 2 with MPICH). `big.sh` traces one run of
  some of them again (up to 700 MB) for `targeted.py`; it was running when this was committed, and
  its tables are added when it ends.
- Every 4-rank run of `test_world` was killed by the container's memory cap (12 GB, exit 137; the
  untraced 2-rank run peaks at 2.2 GB per rank): its traces end there, and their observed classes
  (`abort`, `2-blocked`, `1c`) are the kill's.
- `test_hashthreaded` ran past the 300 s limit in every traced run but MPICH's 2-rank ones (rows
  "hang"); untraced it passes in 47 to 59 s (1, 2 and 4 ranks, both builds, Open MPI). A slow test
  under the tracer, not a hang.
- `test_stack` and `test_vector` make no MPI call (googletest only): no trace (rows "no report (rc
  2)").
- The containers' memory caps were 12 GB each until 15:05 +08, then 11 GB, and for 30 seconds at
  16:44 +08 three caps summed to 30 GB (a slip: the third container was started before the second
  had ended; lowered at once to 8 GB each).

**Findings, by site.** Both MPIs alike unless said. The site of every predicted alternative is
one source line, SafeMPI's `Intracomm::Irecv` wrapper (`world/safempi.h:751`); the gdb
backtraces name its callers, and every receive at the site with an alternative is one of the
runtime's preposted ones: `MPI_ANY_SOURCE`, tag 1023, 1 572 864 bytes, posted by
`RMI::RmiTask::post_recv_buf` (`worldrmi.cc:203`), 128 per rank from `RmiTask`'s constructor in
`madness::initialize` (`worldrmi.cc:318`, `world.cc:222`; chain `0`, the main thread) and again,
as each completes, from `RmiTask::process_some` (`worldrmi.cc:115`; chain `0.1`, the server thread). The other
receives at that line (fences' `WorldGopInterface::fence_impl` and `broadcast`, `worldgop.cc:71`,
`:203`) name their source and use tags 1024-1029, and have no alternative.

| # | Site | Class, level | Tests (traces with it) | Per trace |
|---|---|---|---|---|
| S1 | the runtime's preposted wildcard receives, tag 1023 (`post_recv_buf`, posted on both chains) | 1f at L1, steals: a receive takes another rank's message than in the run, and the run's message is left for a later preposted receive | full analyses: `test_future3`, `test_hashdc`, `test_worldptr` on 4 ranks (all 18 traces), `test_worldref` on 4 ranks with MPICH (3 of 3); none on 2 ranks (one other rank: no other source). Census of the 30 stopped analyses (`test_binsorter`, `test_future2`, `test_memory_measurement`, `test_replication`, `test_tree`, 4 ranks): 253 206 candidates, 16 580 of chain `0` and 236 626 of chain `0.1`; of the 3 000 judged, 1 274 accepted (all 1f: 467 of 1 500 with Open MPI, 807 of 1 500 with MPICH) and 1 726 rejected | 2-65 (full analyses); 116-21 141 candidates (census) |
| O1 | (observed) | `2-buffering`: the observed matching deadlocks below 8, 16 or 200 buffered bytes (6, 20 and 3 traces) | 29 traces: `test_cloud`, `test_dc`, `test_tree` on 2 ranks (both MPIs), `test_hashdc` (2 ranks with Open MPI, 4 with both), `test_hashthreaded` (MPICH, 4 ranks, 1 trace), `test_world` (Open MPI, 4 ranks) | 1 |
| O2 | `SafeMPI::Request::Test_got_lock_already()` at `world/safempi.h:423` (an `MPI_Test`) | `2-thread-wait` (observed): two chains test one request and neither precedes the other | MPICH only: `test_tree` on 2 ranks (1 of 3 traces: chains `0` and `0.1`), `test_world` on 4 ranks (3 of 3, traces of runs killed by memory: chains `0` and `0.0`) | 1-2 |
| O3 | (observed) | `abort`, `2-blocked`, `1c` | the killed 4-rank `test_world` runs (memory) and the `test_hashthreaded` runs stopped at 300 s (slow) | - |

No finding of any class in the other analysed traces (`test_ar`, `test_atomicint`,
`test_future`, `test_future2` on 2 ranks, `test_hello`, `test_prof`, `test_replication` on 2,
`test_thread_specific`, `test_worldprofile`, `test_future3` and `test_worldptr` on 2 ranks,
`test_worldref` (but MPICH's 4-rank runs), `test_binsorter` and `test_memory_measurement` on 2):
their reports are `complete` or degraded only by the counted completion order.

**Questions for the reading** (step 3): S1, does the runtime's handling of an active message
depend on which preposted buffer it lands in, or on the order in which messages of different
ranks are processed (a per-source order the runtime keeps, a counter, a fence that counts
messages)? And messages of one rank: can a preposted receive posted by the main thread and one
reposted by the communication thread take one rank's messages in another order than they were
sent? O1, does the cycle run through the wildcard receives (GA's O2 shape)? O2, what lock does
`Test_got_lock_already` assume, and does the tracer see it? Why no alternative at 2 ranks and in
the fences?

**The large traces** (`big.sh`, scripts of 7fdfcb7, 16:51-17:30 +08, finished after the
pre-registration and before any verdict below was written; [big-openmpi.csv](big-openmpi.csv),
[big-mpich.csv](big-mpich.csv)): one run each of `test_cloud` and `test_dc` on 4 ranks and of
`test_halo` and `test_mul_sparse` on 2, both MPIs, traced up to 700 MB (97-492 MB) and judged by
`targeted.py` (census and a sample of 100): steals at the same preposted receives only, 1 455 and
1 537 candidates in `test_cloud` (the reposting thread is `0.16` there), 275 and 589 in `test_dc`,
of the 400 judged 184 accepted (all 1f) and 216 rejected; none in the 2-rank traces. MPICH's
2-rank `test_world` was left out (its traced run needs more than the 9 GB the container had: a
first attempt was killed by memory and its core dump briefly brought the disk to 10 GB free),
and `test_eval` and `test_vectormacrotask` (above 700 MB) were not traced again.

## Verdicts (after the pre-registration, from reading the code at the sites)

Read from 17:10 +08: `src/madness/world/worldrmi.cc` and `worldrmi.h` (the server's loop, its
receive buffers, `isend` and the huge-message protocol), `worldam.h` (the managed send buffers),
`worldgop.cc` (`fence_impl`), `world.h` and `thread.h` (`World::await`), `safempi.h` (`Request`)
and `worldmutex.h` (`Spinlock`); and, for O2, gdb at `MPI_Test` in untraced runs of `test_tree`
with MPICH (thread and request handle of each call). Line numbers are those of 64eaf80.

| # | Verdict | Why |
|---|---|---|
| S1 | harmless by design | the server preposts 128 identical buffers (`post_recv_buf`, `worldrmi.cc:201-213`: `MPI_ANY_SOURCE`, `RMI_TAG` 1023) and dispatches each arrival on its header (`process_some`, `:60-180`). An active message is unordered (tasks and remote method calls, `world_task_queue.h:436`, `world_object.h:772`, the reduction trees of `worldgop.h`) or ordered (the default of `WorldAmInterface::send`, `worldam.h:279`); an ordered one carries a per-destination counter stamped under the RMI mutex that encloses the send (`isend`, `worldrmi.cc:456-470`), and the server runs it only when its counter is the source's next, queueing the others and sorting the queue by source and counter (`:105-165`). Which buffer takes which rank's message, or which of two arrivals is processed first, therefore changes nothing that the runtime promises: ordered messages run in send order per source whatever the buffers, unordered ones carry no order. The queue cannot overflow (`maxq_` is the number of buffers plus one, and each queued message holds its buffer until it runs). Huge messages are announced by an unordered message and received from the named source with a unique tag (`:419-442`, `:183-199`). Fences count sent and received messages (`fence_impl`, `worldgop.cc:55-150`), not their order. None at 2 ranks because a rank's only other source keeps MPI's per-source order |
| O1 | **tool false alarm** (a limit of the observed 2-buffering class) | the cycle runs through the server's wildcard receives (checked in the reports of `test_tree` on 2 ranks and `test_hashdc` on 4, Open MPI: an `irecv` with `MPI_ANY_SOURCE` and tag 1023 on both chains of the cycle); with rendezvous sends the server takes another message first, and no sender blocks on a receive that is not preposted. GA's O2 shape. Untraced runs of `test_tree` and `test_dc` (2 ranks), `test_hashdc` (4) and `test_cloud` (2), Open MPI, 5 each at the defaults and 5 over TCP at 0 eager payload bytes (`eager.sh`, [eager-openmpi.csv](eager-openmpi.csv)): 40 of 40 passed, none hung |
| O2 | **tool false alarm** (tracer) | the two `MPI_Test`s are of two requests with one handle value: gdb shows the main thread (in `World::await` and `free_managed_buffers`) and the server thread (`clear_send_req`) testing handle `0x6c000001`, which is MPICH's builtin handle for a send request that completed at once (a lightweight request; `HANDLE_KIND_BUILTIN`), shared by every such request. The tracer names requests by handle value, so two distinct completed sends became one request tested by two chains. Open MPI returns distinct handles, hence MPICH only. Not fixed here (the tracer would have to treat MPICH's builtin request handles as completed at creation); recorded in docs/findings.md. The managed send buffers themselves are tested under their own spin lock (`SendReq`, `worldam.h:218-236`, `:354-363`), which the tracer records |
| O3 | not a bug of the program here | runs killed by the container's memory cap (the 4-rank `test_world`) or by the 300 s limit of a test that is slow under the tracer (`test_hashthreaded`, 47-59 s untraced) |

**Forced replays of S1** (`replay.sh`, [replay-openmpi.csv](replay-openmpi.csv), Open MPI, the
instrumented build): 2 new traced runs each of `test_hashdc`, `test_worldptr` and `test_future3`
on 4 ranks with the lock order recorded (`STRETTO_SYNC_ORDER=1`), 4 sampled 1f alternatives per
trace, each replayed once with its control: all 24 inconclusive (`replay_verdicts.py`). 23 replays
and their 23 controls ended on a misapplied lock pin (exit 86: a chain's attempt at a given
ordinal took another lock than in the trace), and one alternative had no horizon to pin. MADNESS's pool takes tasks from its
own queues and its threads take their locks in an order that depends on that schedule, so a
replay cannot follow the trace's lock order (premise P-T5 fails for the pool, as for DLA-Future's
pika, docs/threads.md section 16; and the lock gates do not order atomics, section 17.3). The
harmlessness of S1 rests on the reading above, not on replays.

## What the tool did here

No bug found, by the tool or by reading. The tool's predictions are all of one kind, steals at
the active-message server's 128 preposted `MPI_ANY_SOURCE` receives on 4 ranks (2 to 65 per
trace in full analyses, 116 to 21 141 candidates per trace where the analysis was cut short), and
the runtime is built for them: ordered messages carry a per-source counter and are run in that
order whatever the buffer, unordered ones promise no order. This is GA's S1 again, with the
order kept by counters instead of by named follow-up receives. The tool's observed classes were
false alarms twice: an observed `2-buffering` whose cycle runs through those wildcard receives
(GA's O2), and a `2-thread-wait` that MPICH's shared handle for requests completed at creation
made out of two distinct requests. MADNESS needed two tool changes to be traced at all (a lock's
clock freed with the lock, and three analysis costs) and is beyond the analysis at its own test
sizes: traces of 4 ranks have up to 21 000 candidates each, judged here by sample, and its
largest tests (`test_eval`, `test_vectormacrotask`, `test_world`) write more than 600 MB.

## Files

`Dockerfile`, `build.sh` (MADNESS with both MPIs, gcc or, with `TSAN=1`, clang with the atomics
instrumented), `scan.sh` and `scan_csv.py` (the scan and its tables
`scan-{openmpi,mpich}-{runs,sites}.csv`), `summary.py` (findings by site and class),
`targeted.py`, `targeted.sh`, `big.sh` and `targeted_summary.py` (the census and the sampled
judgement: `targeted-{openmpi,mpich}.csv`, and `big-{openmpi,mpich}.csv` for the large traces),
`eager.sh` (`eager-openmpi.csv`), `replay.sh` (`replay-openmpi.csv`).
