# Global Arrays / ComEx with the MPI progress thread (`--with-mpi-pt`)

Target 1 of the third thread hunt (1 Nov 2026), the first in which the tool is to lead. In the
four earlier thread bugs (Uintah, SLATE, DLA-Future, PaRSEC) the defect was found by reading
the code and predicted afterwards. Here the shipped tests are traced and analysed first, the
findings committed (the pre-registration), and only then is the communication code read at the
reported sites.

GA master bd1149f (23 Oct 2026), configured `--with-mpi-pt --disable-f77` (C only, GA's own
BLAS) with gcc 13 and Open MPI 4.1.6 or MPICH 4.2.0 (`build.sh`, about a minute per MPI with 8
jobs), MACHINE=a-p13, CPUs 8-15,48-55, in the experiment image plus autotools (`Dockerfile`).
`MPI_Init_thread` asks for `MPI_THREAD_MULTIPLE` and gets it with both libraries.

## Emulated nodes

ComEx serves requests within a node through shared memory and between nodes through MPI. On
one machine the progress thread of a traced 4-rank `testc` received a single message, the
termination request at the end. `fakehost.c` (LD_PRELOAD) answers `gethostname` and
`gethostid`, the two node-identity calls libcomex imports (`nm -u`; the code was not read for
this), with a node of its own for every `FAKEHOST_PPN` consecutive ranks, to callers in the
program's executable only (GA is linked statically), so that MPI itself still sees one host.
With `FAKEHOST_PPN=1` every rank of a 4-rank run has a second chain, the progress thread, and
`testc`'s trace carries 364 point-to-point messages; with 2, ranks 1 and 3 have one. Docker's
64 MB `/dev/shm` is too small for ComEx's segments (the ComEx tests died of SIGBUS in the
first pilot run); the scans ran with `--shm-size 4g`.

## Blind scan (pre-registration)

Recorded before any of ComEx's communication code (`comex/src-mpi-pt/comex.c`, `groups.c`) was
read; what was read: GA's build files, the test programs' sources where a test failed, and the
function names and line numbers that `stretto sites` prints for the call sites of the traces.

**What ran.** `scan.sh` at commit 48f1816 (analysis code of 8a05eff), 1 Nov 2026 14:40-16:24
+08, two containers on CPUs 8-15,48-55: 30 shipped tests (GA's C tests without the benchmarks,
ComEx's `test`, `shift`, `perf_amo`; two more left out, below), 4 ranks, `FAKEHOST_PPN` 1
(four nodes of one rank) and 2 (two nodes of two), 3 traced runs each, with Open MPI and with
MPICH, 359 runs in all; each trace analysed at L1
(`stretto analyze --json`, one hour each) and its sites named (`stretto sites`). Tables:
[scan-openmpi-runs.csv](scan-openmpi-runs.csv), [scan-mpich-runs.csv](scan-mpich-runs.csv) (one
row per run: result, report status, findings by level and class, the analysis commit) and
`scan-*-sites.csv` (one row per run and site of a changed receive or probe: its source line in
GA's tree and its counts). Deviations, all recorded in the tables or here:

- A pilot before it (one Open MPI run per test, one rank per node, Docker's default
  `/dev/shm`) gave the same classes at the same sites; it is not tabulated.
- `thread_perf_contig` and `thread_perf_strided` are not in the tables: the first traced run of
  `thread_perf_contig` wrote 5 GB in about three minutes and was stopped, and both were left out
  (their traces are beyond any analysis here).
- Traces above 60 MB were deleted after their run and not analysed (`packc`, `unpackc`,
  `scan_addc`, `scan_copyc`: 75-184 MB; `sprs_test` 278-322 MB; ComEx's `test` 124 MB and
  `perf_amo` 306 MB): status "not analysed" with the size. During the scan a side loop did the
  deleting; `scan.sh` now does it itself.
- MPICH's runs paused for 5 minutes when the machine's disk dipped below 10 GB (other lanes),
  and `nbtestc` has 5 MPICH runs instead of 6 (the sixth was cut by that stop).
- `threadsafec` (OpenMP threads calling GA at once; about 68 000 records and 102 or 104 chains
  per trace) did not finish: the check for two threads waiting on one request compared every
  pair of a rank's waits (made linear in commit 37643dc, the same result), and after it each
  trace has 17 800 to 22 300 L1 candidates for the generic check, beyond hours. The analyses
  were stopped after 54 minutes (rows "analysis stopped"). Instead `census.py`
  ([census.csv](census.csv)) counts the candidates per site over the happens-before of L1 and
  over the hard one of L1+L, and `targeted.sh` / `targeted.py` judge, at L1+L as
  `stretto.locks.alternatives` does, every candidate at the main threads' reply receives
  (started 18:01 +08; its table, `targeted.csv`, is added when it ends, its shards merged).
  The two scripts as they ran wrote no commit column and CRLF line ends; that is all the
  committed ones change (`census.csv` got its column, 37643dc, the analysis code it ran on).
  The `testmult` analyses that the restart cut were run again with 37643dc; their rows name
  it as the analysis commit.
- Seven `perf_amo` analyses (four of Open MPI, three of MPICH) found their trace already
  deleted (the side loop ran first); they are size-skipped like the others.

**Findings, by site.** Counts per trace, both MPIs alike unless said. Sites as `stretto
sites` names them in GA's tree.

| # | Site | Class, level | Tests (traces with it) | Per trace |
|---|---|---|---|---|
| S1 | `_progress_server` at `comex/src-mpi-pt/comex.c:2445`: the progress thread's receive from `ANY_SOURCE` with tag 27624, 2 MB, on a duplicate of the world communicator | 1f at L1, steals: the receive takes another rank's 32-byte request header than in the run, and the run's sender's header is left for a later receive | `testc`, `ntestc`, `ntestfc`, `mtest`, `ga-mpi`, `gatscat`, `matrixc`, `patch_enumc`, `testmatmultc`, `testmult`, `testmultrect` (all 12 traces each); `shift` and `lock` with two ranks per node only (6 of 12) | 2 (`lock`) to 1 397 (`testmult`) |
| S2 | `_put_handler` at `comex.c:2564`: the progress thread's receive of a put's data (16 KB, named source) | 1f at L1 | `nbtestc`, Open MPI, two ranks per node (3 of 3; 0 of 2 with MPICH): the data of a put that the hung run never delivered | 1 |
| O1 | (observed) | `2-blocked`, `abort` ("rank 0 stopped outside MPI before MPI_Finalize"), `1c` (Open MPI, two per node: 3 sends never received) | `nbtestc`: every run hangs (300 s), traced or not, both MPIs and layouts, untraced on one real node too (200 s); rank 0 loops outside MPI while the others wait in a collective and the progress threads in S1's receive | 11 of 11 traced runs |
| O2 | (observed) | `2-buffering`: the observed matching deadlocks below 400 buffered bytes (Open MPI) or 9 608 (MPICH); the cycle runs through two main threads and two progress threads | `testc` and `ga-mpi` (Open MPI, one rank per node), `mtest` (MPICH, two per node) | 1 trace of 6 each |
| O3 | (observed) | `abort` | `read_only`, `cache_test`: the test calls `MPI_Init` (`read_only.c:105`, `cache_test.c:105`), and ComEx asserts `MPI_THREAD_MULTIPLE` (`comex.c:304`, from the message) | 12 of 12 each |
| T | the main threads' reply receives of `threadsafec`: `nb_recv` at `comex.c:4102`, `nb_recv_packed` at `:4027`, `server_recv` at `:3937` | no candidate at L1; at L1+L (critical sections of lock 1 reordered) 106 to 4 503 candidates per trace; when this was committed (18:38 +08) `targeted.sh` had judged 4 881 of the 12 806, every one rejected, and 5 of the 12 traces were done | `threadsafec` (12 traces) | none accepted so far |

No finding at any receive of a main thread outside `threadsafec` either: in the traces the
replies a main thread waits for come on another duplicate of the world communicator than the
requests the progress thread takes (communicators 3 and 2), from named sources. Tests without
any finding: `comm_init`, `elempatch`, `getmem`, `normc`, `print`, `simple_groups_commc`, and
`shift` and `lock` with one rank per node.

**Checks made before reading the code** (no code read for them). Forced eager limits for O2
(`eager.sh`, [eager-openmpi.csv](eager-openmpi.csv), untraced): `testc`, `ga-mpi` and `mtest`,
both layouts, 10 runs each with the TCP transport at 0 and at 400 buffered bytes and at the
defaults: 180 of 180 passed, none hung. A trial of three forced replays of S1 on one `testc`
trace (two ranks per node): every pin taken, the test passed in replay and control.

**Questions for the reading** (step 3): S1, does the server's handling depend on the order of
requests from different ranks (two requests on one location, a lock and an unlock, a fence)?
S2, can the put handler's data receive take another request's data? O1, why does rank 0 of
`nbtestc` never leave its loop over `NGA_NbTest`? O2, is the cycle a real dependence on eager
sends between progress threads, or does something the trace does not show break it? T, what
orders the reply receives of a rank's threads, and is it recorded?

## Verdicts (after the pre-registration, from reading the code at the sites)

Read from 18:40 +08: `comex/src-mpi-pt/comex.c` around every site of the table, the client
calls that send to the progress thread, `global/src/onesided.c` and `base.c` for GA's own
locking. Line numbers are those of bd1149f.

| # | Verdict | Why |
|---|---|---|
| S1 | harmless by design | `_progress_server` (`comex.c:2414-2540`) takes one request header from any rank and dispatches on its `operation`; every handler then receives the rest of that request from the same rank by name (`server_recv`, `:3931-3948`, or `MPI_Recv(proc, ...)` in `_put_handler` and `_acc_handler`). A requester posts a request's header and payload in one call (`nb_send_header`, `nb_send_buffer`, `:3950-3991`) and replies come back on another communicator (`server_send` on `group_list->comm`, `:3915-3928`), so per-source FIFO keeps each request whole. Requests of different ranks are unordered in GA's model unless a fence orders them, and a fence is acknowledged only after the server has handled the requester's earlier requests (`_fence_handler`, `:3190-3208`). Locks queue in arrival order (`_lock_handler`, `:3343-3383`); another order is another fair schedule. Forced replays (`replay.sh`, [replay-openmpi.csv](replay-openmpi.csv), [replay-mpich.csv](replay-mpich.csv)): 74 alternatives of 21 traces of six tests, both layouts (42 with Open MPI, 32 with MPICH), each once with its control: every pin taken, the test finished with the output of its control, no divergence, no misapplied pin |
| S2 | harmless | the put handler's data receive (`:2564`) names the requester, and the requester's data follows its header on the same channel; in the hung `nbtestc` runs the alternative is the run's own data, which rank 0 never let through (O1) |
| O1 | **real bug**, deterministic, found by the observed classes of the scan, not by a prediction | `comex_test` (`:1346-1374`) only looks whether a handle's lists of requests are empty, and only the blocking `nb_wait_for_send1` / `nb_wait_for_recv1` (`MPI_Wait`, `:4166-4300`) empty them, so a handle with a pending operation is never reported complete. `NGA_NbTest` loops forever on them, and `nbtestc` polls such handles in both of its put phases. The progress-rank port fixed its `comex_test` in 2019 (`nb_test_for_all`, b5cc6175); the progress-thread and multithreaded ports kept the 2015 code (`src-mpi-mt/comex.c` has the same function; not built here). Series and patch below |
| O2 | **tool false alarm** (a limit of the observed 2-buffering class) | the cycle closes through the progress threads' wildcard receive: in the run, rank 3's server took rank 1's request before rank 0's, and at B = 0 that order needs rank 0's request to wait on a reply that follows rank 1's. With rendezvous sends the server would simply take another request first (S1); nothing in ComEx blocks on an unbuffered send to a receiver that has not posted (replies go to receives preposted by the requester). The class reads "the observed run deadlocks below B" as "the program deadlocks"; with a wildcard receive on the cycle that does not follow. Plain runs at 0 buffered bytes: 0 of 60 hung (`eager-openmpi.csv`). Not fixed here (the class is part of the corpus scores); recorded in docs/findings.md |
| O3 | test defect, not a race | `read_only.c:105` and `cache_test.c:105` call `MPI_Init`, not the suite's `MP_INIT`, which asks for `MPI_THREAD_MULTIPLE` under this port (`mp3.h:15-21`); ComEx then asserts the thread level (`comex.c:304`) |
| T | no race | GA's own mutex (`GA_Internal_Threadsafe_Lock`, `global/src/thread-safe.c`) is held around each put, get, accumulate and read-and-increment (`onesided.c`); within one critical section ComEx preposts the reply receive and sends the request (`nb_recv` then `nb_send_header`, for example `:4415-4416`), so replies from one server arrive in the order of the requests and of the receives, in whatever order the threads take the mutex. The tool agrees: [targeted.csv](targeted.csv) has all 12 800 L1+L candidates of the 12 traces rejected, 6 320 by the order filter and 6 480 by the schedule search of `locks.judge` (the pre-registration's total of 12 806 was a sum error; finished 20:37 +08, 2.6 hours on 14 processes) |

## The NbTest hang (O1): series, fix, report

`nbtest-progress.patch` (15 lines in `comex_test`): before looking at the lists, retire the
requests that have completed, in order, with `MPI_Request_get_status` and the existing
`nb_wait_for_send1` / `nb_wait_for_recv1`. `series.sh` ([series-openmpi.csv](series-openmpi.csv),
[series-mpich.csv](series-mpich.csv)), untraced, 4 ranks, 30 s a run, 20 runs per cell, plain
and patched interleaved; `testc`, which polls no handle, is the control:

| test, MPI | layout | plain | patched |
|---|---|---|---|
| `nbtestc`, Open MPI 4.1.6 | one rank per node (4 emulated nodes) | 20 of 20 hang | 0 of 20 (all pass, 1.5 s on average) |
| | two ranks per node | 20 of 20 hang | 0 of 20 |
| | one node (the machine) | 20 of 20 hang | 0 of 20 |
| `nbtestc`, MPICH 4.2.0 | one rank per node | 20 of 20 hang | 0 of 20 (11 s on average, at most 28 s) |
| | two ranks per node | 20 of 20 hang | 0 of 20 |
| | one node | 20 of 20 hang | 0 of 20 |
| `testc` (control), Open MPI | all three | 0 of 60 | 0 of 60 |
| `testc` (control), MPICH | all three | 1 of 60 past 30 s | 1 of 60 past 30 s |

With one rank per node or two, the plain runs stop in the first phase (puts to every owner,
polled until done); on one node that phase is local copies and passes, and the second hangs.
MPICH's `testc` took 10 to 16 s on average per cell on this busy machine, and two runs, one per
variant, one rank per node, ran past the limit; they are slow runs, not the bug.

The report's reproduction block (`clean-room.sh`: fresh clone, `autoreconf -fi`, 4 ranks of one
machine with Open MPI and no emulated nodes) run in an empty directory on 1 Nov: 3 of 3 plain
runs print the first phase's `OK` and hang (exit 124), 3 of 3 runs with the patch applied by
`git apply` pass all four phases. Not in GA's issue tracker (searched 1 Nov: "mpi-pt" 4 issues,
"nbtest" 1, "progress thread" 1; #349 is the exhaustion of `COMEX_MAX_NB_OUTSTANDING`, whose
reply says the progress-thread port still runs the old non-blocking code). GA's CI builds
`mpi-ts`, `mpi-pr` and `mpi3`, not `mpi-pt`. Report drafted, not sent.

## GA mutexes are one mutex (side finding, by reading)

`pnga_lock` and `pnga_unlock` (`global/src/base.c:5121-5150`) compute the ComEx mutex from
`num_mutexes`, not from their argument (present since at least 2001), so every GA mutex is the
same ComEx mutex: unrelated locks exclude each other, and a process that holds one GA mutex
and asks for another waits for itself. `nested_lock.c` (rank 0 takes mutex 0, then mutex 1;
`nested.sh`, [nested-openmpi.csv](nested-openmpi.csv), Open MPI, 2 and 4 ranks, one node or
one emulated node per rank, 5 runs each, 15 s a run): 20 of 20 plain runs hang after "lock 0
taken", 20 of 20 pass with `lock-index.patch` (the mutex's owner and index from the argument,
as `pnga_create_mutexes` lays them out); the shipped `lock.x` passes with and without it. Found
while reading for T, not by the tool, and no shipped test nests locks. Not reported yet.

Also by reading, not tested: `pnga_lock` takes no `GA_Internal_Threadsafe_Lock`, while ComEx's
progress-thread port keeps its handle table and its request lists without a lock
(`nb_wait_for_handle`, `comex.c:4124-4150`), so a thread in `GA_Lock` concurrent with another
thread's put or get shares them; and when the lock is held elsewhere, its preposted 4-byte
acknowledgement (`comex_lock`, `:1813-1847`) stays the earliest receive from that node, and the
reply to a later get of another thread, sent first, would land in it. No shipped test calls
`GA_Lock` from several threads.

## What the tool did here

No new bug that the tool found first. The scan's question was answered by the tool, and
negatively: no alternative at any receive of a main thread at L1, and at L1+L (`threadsafec`)
none of 12 800 candidates accepted; reading confirms why (GA's mutex around each request and
its reply receive). The tool's own predictions (S1, S2) are races that the protocol tolerates
by design, and 74 of 74 forced replays realised them with the tests passing as their controls.
The real bug the scan surfaced (O1) is a hang that any plain run of the shipped test shows; the
analysis named where each thread stood when it ended (rank 0 outside MPI, its 16 KB put never
received), which pointed at `comex_test`. The observed 2-buffering (O2) is a false alarm of
the class with a wildcard receive on the cycle. Two analysis costs found on the way are fixed
(8a05eff: one-type signatures flattened at once, 280 s to 16 s on `testc`; 37643dc: the
double-wait check grouped by request).

## Files

`Dockerfile`, `build.sh` (GA with `--with-mpi-pt`, both MPIs, `VARIANT`/`PATCH` for patched
trees), `fakehost.c` (emulated nodes), `scan.sh` and `scan_csv.py` (the blind scan and its
tables `scan-{openmpi,mpich}-{runs,sites}.csv`), `census.py` ([census.csv](census.csv)),
`targeted.sh`, `targeted.py` (`targeted.csv`), `eager.sh` ([eager-openmpi.csv](eager-openmpi.csv)),
`replay.sh` (`replay-*.csv`), `nbtest-progress.patch`, `series.sh` (`series-*.csv`),
`clean-room.sh`, `lock-index.patch`, `nested_lock.c`, `nested.sh` (`nested-openmpi.csv`).
