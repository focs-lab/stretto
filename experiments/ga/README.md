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
  committed ones change (`census.csv` got its column, 37643dc, the analysis code it ran on). The `testmult` analyses that the restart cut were run again with
  37643dc; their rows name it as the analysis commit.
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
