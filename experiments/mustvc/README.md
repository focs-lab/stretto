# MUST-vc against Stretto on programs with threads

The closest competitor for several threads per process is MUST's vector-clock checks for
MPI+OpenMP (*Correctness Checking of MPI+OpenMP Applications Using Vector Clocks in MUST*, SC-W
2024; artifact RWTH-HPC/must-omp-clock-checks, here *MUST-vc*). Its clock-based analysis
(`ThreadSanity` over `OpenMPClock`, fed by MUST's OMPT adapter) keeps per thread and task a
vector clock of OpenMP synchronisation (regions, barriers, tasks and their dependences, locks,
critical sections) and reports, inside an OpenMP parallel region, two MPI calls that no clock
orders: two receives (`Recv`, `Irecv`, `Sendrecv`) with one source, tag and communicator (or a
wildcard tag), a receive and a probe likewise, two collectives (blocking ones only) on one
communicator, a call that uses a communicator another frees, and any two calls under
`MPI_THREAD_SERIALIZED`. Every cell below comes from running both tools (a-p13, CPUs 0-7,40-47,
1 Nov 2026; `MACHINE=a-p13`).

## Who finds what

Runs flagged out of runs made (MUST-vc: a `[MUST-REPORT]` error or warning; Stretto: a
finding, with its class and level). "as is" is the program unchanged; "compat" the program
with the change MUST-vc needs to work at all (below).

| case | MUST-vc | Stretto |
|---|---|---|
| **SLATE CALU, wide matrix** (OpenMP tasks, tags collide; plain 1000x2000, 4 ranks of 4 threads) | as is: crashes at start, 10 of 10 runs (plain and fixed; SLATE's static initialiser calls `omp_get_max_threads()` before `MPI_Init`); compat: the culprit pair (concurrent receives, tags 20-27 in the collision range) in 10 of 10 plain runs (2x2 and 4x1), nothing else, 0 of 10 fixed runs | L1 swaps or steals at `permuteRows` receives in every plain trace (42 of 42 on 16 processors, 7 of them from runs whose factor came out right), 0 in patched and control traces ([slate](../slate/README.md)); forced replay confirmed, narrowly |
| **DLA-Future `matrix::copy`** (pika threads, tags collide; 2x1, 4 threads) | artifact's MPICH 4.0.2: the program itself crashes in 20 of 20 runs (also with one pika thread: a segmentation fault in MPICH's datatype pack during a UCX rendezvous), and in 20 of 20 under MUST; rebuilt against Debian's Open MPI 4.1.4 (`mustvc:1.9-omp-openmpi`): natively 10 of 10 plain runs wrong and 10 of 10 fixed ones right, under MUST-vc 20 of 20 runs end in a segmentation fault that MUST's handler catches (not diagnosed), with no report. No check could run anyway: pika's threads are not OpenMP's | 15 of 15 plain traces of the colliding cells, 0 of 15 patched ([dlaf](../dlaf/README.md)) |
| **PaRSEC reshape** (PaRSEC's pthreads, one tag per thread number; 1 rank, 6 threads in 2 virtual processes) | 0 of 10 runs (plain and fixed: no OpenMP region, so no check runs); under MPICH every run is right; the 4-rank cell aborts in every run, with and without MUST, at PaRSEC's `MPI_Allreduce(MPI_BYTE, MPI_SUM)` (`parsec.c:2833`), which MPI does not define and MPICH rejects | 10 of 10 plain traces predict the swap from runs that were all right, 0 of 10 patched; 53 of 60 forced replays confirm it ([parsec](../parsec/README.md)) |
| **Uintah + hypre** (`std::thread` workers, two `MPI_Allreduce` on the world communicator) | 0 reports; 12 of 12 runs (4 ranks with 4 and with 2 threads, plain and fixed) stop before the first time step and are killed at 300 s. Without MUST in the same image, plain 0 of 6 pass (3 truncations, 1 failed `MPI_Waitall`, 2 hangs), fixed 4 of 6 (2 hang at exit after the last time step). No check could run anyway: Uintah's threads are not OpenMP's | observed `2-thread-collective` in every trace ([uintah](../uintah/README.md)) |
| MUST-vc's suite, 30 own programs and 20 adapted from CorrBench, 5 runs each | erroneous: 115 of 115 runs flagged; correct: 5 of 135 runs flagged (all `concurrent_communicator_transitive`); the counter layout 6 of 115 and 0 of 135; its `run_tests.sh` gives the artifact's table exactly | erroneous: 86 of 115 (error class: 71); correct: 39 of 135 flagged (error class: 29): 14 on three "correct" programs that deadlock (below), 10 on two task programs whose traces are degraded (chain table overflow), 10 probe warnings on the two Mprobe programs, 5 on `concurrent_communicator_transitive` |
| MPI-CorrBench a05644b, the 103 OpenMP programs, 3 runs each (59 erroneous, 44 correct) | as is: 0 programs; 300 of 309 runs warn that OpenMP started before `MPI_Init` (clang calls the runtime at the entry of a function with a `num_threads` clause), which MUST does not support; compat (the clause removed): 14 erroneous programs in every run, 1 correct one (`two_collectives_4`, erroneous as built) | 26 erroneous programs in every run and 4 in some; 5 correct ones (3 with an error class: `deadlock_probe`, a real lock-order deadlock, and `two_collectives_4` and `_5`, erroneous as built; 2 with probe warnings) |
| Stretto's idioms 15-47 with threads, 3 runs each (21 pthreads, 8 OpenMP) | pthreads: 0 of 21 idioms (no OpenMP); OpenMP as is: 0 of 8 (early start); compat: 3 of the 5 OpenMP idioms with an error (18, 25, 34; not 43, 45), none of the 3 negatives | 20 of 29 flagged in every run, each with the report its header expects (19 with the probe warnings it allows); the 9 unflagged are the 9 negatives |
| *special row:* **MPI calls under a lock** (critical sections whose order decides the run) | CorrBench `two_collectives_8`, `_9`, `dependant/deadlock_probe`: 0 of 9 runs (compat); idiom 43: 0 of 3 (compat); MUST-vc's own `ThreadLevel_serialized` and `two_collectives_4` (labelled correct; both can deadlock): 0 of 10 | L1+L: `two_collectives_8`, `_9` 6 of 6 (`2-thread-collective`), `deadlock_probe` 3 of 3 (`2-lock-order`), idiom 43 3 of 3, idioms 22, 23, 27 9 of 9; `ThreadLevel_serialized` 5 of 5 (`2-lock-order` in the 2 runs that finished, the hang observed in 3), `two_collectives_4` 5 of 5 |
| *special row:* **requests completed by another thread** | CorrBench `request_reuse` (two threads wait on one request): 0 of 6 (as is and compat); idiom 26: 0 of 3 (pthreads); DLA-Future (pika's polling worker completes other workers' requests): crashes under MUST-vc, 20 of 20 | `request_reuse` 3 of 3 (1c, observed), idiom 26 3 of 3 (`2-thread-wait`), DLA-Future's hand-offs to pika's polling worker modelled (P-T9 to P-T11): the 15 traces above |
| *special row:* **transitive synchronisation** through MPI (`concurrent_communicator_transitive`, labelled correct) | 5 of 5, the artifact's known false positive | 5 of 5 as well (`2-thread-collective`, observed): neither tool orders threads through messages. Thread 1's second barrier follows thread 0's first only in its start; thread 0's call need not have returned, so the two calls can overlap, which MPI 4.1 §12.6.2 forbids |
| *special row:* **predictions from runs where nothing went wrong** | the checks need no failure, but only report concurrency of one envelope: no consequence, and nothing when the threads are not OpenMP (PaRSEC: 0 of 10 right runs); idiom 45 (tasks under a nest lock, one tag): 0 of 3 | PaRSEC 10 of 10 right runs, then confirmed by replay; SLATE 7 right-factor runs; idioms 15, 20, 34, 44, 45, 47 from runs that went right, with the consequence (34, 44, 45: 1a truncation) |

Where Stretto is behind, measured: the concurrent receive and probe programs of MUST-vc's suite
(`concurrent_recv_probe_error`, `_any_tag_error`: 0 of 10; the alternative is a probe that
finds no message and hangs, which L1 does not predict), three `MPI_THREAD_SERIALIZED`
programs whose concurrent calls are `MPI_Comm_size` (0 of 15; the tracer records communication
calls only), task programs with more than 256 tasks (the tracer's chain table overflows:
`concurrent_communicator_task_nesting`, `_tasking_depend` and `_tasking_error` degraded, with
false observed classes on the correct ones), and the transitive row. Where MUST-vc is behind:
every program whose threads are not OpenMP's (the pthreads idioms, PaRSEC, Uintah,
DLA-Future), every program that starts OpenMP before `MPI_Init` (all of CorrBench as published,
SLATE), critical sections and locks whose order decides the run (it takes their order in the run
as happens-before), deadlocks and requests completed by another thread (no check), and
consequences (a concurrency warning, never a truncation, a wrong result or a hang).

## The programs MUST-vc needs changed

- **OpenMP started before `MPI_Init`.** MUST's OMPT tool initialises when the OpenMP runtime
  does; before `MPI_Init` its modules do not exist. SLATE's static initialisers
  (`src/auxiliary/Trace.cc:180-185`, `omp_get_max_threads()`) make MUST segfault in
  `OpenMPSynchPropagate::getInstance` before `main` (10 of 10 runs).
  [slate-trace-init.patch](slate-trace-init.patch) replaces the two calls by 1 (SLATE's own
  tracing, off in these runs, is the only user); the plain and fixed libraries are built again
  with it (`build-apps.sh slate-compat`). Clang calls `__kmpc_global_thread_num` at the entry of
  any function with a `num_threads` clause, so a `main` with `#pragma omp parallel
  num_threads(N)` starts the runtime before `MPI_Init` too: MUST then prints "The OpenMP runtime
  has been initialized before the call to MPI_Init ... Checks of threaded MPI usage might not
  work as expected" and reports nothing (`early` column). The artifact's copies of CorrBench
  programs drop the clause; `corrbench.sh` and `idioms.sh` with `COMPAT=1` do the same
  (OMP_NUM_THREADS=2 gives the same team).
- **Threads that are not OpenMP's.** The checks run only inside an OpenMP parallel region
  (`ThreadSanity.cpp`, `num_parallel_regions == 0` returns at once), so PaRSEC's, pika's and
  Uintah's threads are never checked. Nothing short of rewriting their schedulers in OpenMP
  changes that; they ran unchanged.

## What ran

Image `mustvc:1.9-omp` ([Dockerfile](Dockerfile), [build.sh](build.sh); 13 minutes with 8
jobs): the artifact's environment, Debian 12 (pinned digest), MPICH 4.0.2-3+b1, clang
15.0.6-4+b1 with libomp-15-dev, MUST v1.9-omp-checks from the artifact's tarball (sha256
37328e62...c2) built RelWithDebInfo, lit 14.0.0, Python 3.11.2; for the applications gcc 12.2,
OpenBLAS 0.3.21, Boost 1.81 (pika needs 1.77), hwloc 2.9.0. Layouts: the artifact's
`layout_clock.xml` (and `layout_counter.xml` for its suite) with the MPI level sized to the
process count; options as in its RUN lines (`--must:openmp --must:instant-logging warning`,
`MUST_OUTPUT=stdout`, `MUST_TIMEOUT=5`, which ends a program after 5 s; 0 for the applications).
Image IDs: `mustvc:1.9-omp` sha256:1059f9395c0c (every MUST-vc table but one), and
`mustvc:1.9-omp-openmpi` sha256:3aef055d75da (the same with Debian's Open MPI 4.1.4-3+b1 in
place of MPICH; `mustvc-apps-openmpi.csv`).
Stretto: the experiment image (`stretto-exp:24.04`: Open MPI 4.1.6, gcc 13.3 with libgomp,
Python 3.12.3), tracer and analysis of 549036d (the `commit` column), each traced run analysed
by `stretto analyze --json`.

| table | what | how |
|---|---|---|
| [mustvc-micro.csv](mustvc-micro.csv) | MUST-vc's suite, both layouts, 5 rounds, lit verdicts | `micro.sh` |
| [stretto-micro.csv](stretto-micro.csv), [stretto-micro-gcc.csv](stretto-micro-gcc.csv) | the same 50 programs under Stretto (built with gcc; `concurrent_communicator_tasking_depend` needs `comms.data()[tid]` for `comms[tid]` in its `depend` clause, which gcc rejects on a `std::vector`), 2 ranks, the RUN line's threads, 5 traced runs | `stretto_bench.sh artifact` |
| [mustvc-corrbench.csv](mustvc-corrbench.csv), [mustvc-corrbench-compat.csv](mustvc-corrbench-compat.csv) | CorrBench's 103 OpenMP programs as published and without `num_threads`, 2 ranks of 2 threads, one run without MUST and 3 with | `corrbench.sh`, `COMPAT=1` |
| [stretto-corrbench.csv](stretto-corrbench.csv) | the same under Stretto (the 65 programs outside `ordering` for the first time), 3 traced runs | `stretto_bench.sh corrbench` |
| [mustvc-idioms.csv](mustvc-idioms.csv), [mustvc-idioms-compat.csv](mustvc-idioms-compat.csv), [stretto-idioms.csv](stretto-idioms.csv) | idioms 15-47 with threads on their `idiom_np`, 3 runs each | `idioms.sh`, `stretto_bench.sh idioms` |
| [mustvc-apps.csv](mustvc-apps.csv), [mustvc-apps-openmpi.csv](mustvc-apps-openmpi.csv) | the four applications, plain and fixed, 5 rounds (Uintah 3) of one run without MUST and one with, alternating, after one unrecorded warm-up per cell; DLA-Future also on the Open MPI image | `build-apps.sh`, `apps.sh` |
| [hangs.csv](hangs.csv), [hangs-ompi.csv](hangs-ompi.csv) | the three suite programs labelled correct that can deadlock, 10 untraced runs per toolchain | `hangs.sh` |

`summary.py` prints the per-program and per-cell tables these numbers come from (`python3 experiments/mustvc/summary.py
experiments/mustvc`).

Commands (a-p13, from a copy of the repository at `~/mvc-work/repo`, work directory
`~/mvc-work` mounted at `/mvc`; SLATE, PaRSEC, DLA-Future and its dependencies from the trees of
the earlier hunts, mounted read-only under `/src`; Uintah 493d2b5 cloned there):

```bash
CPUS=0-7,40-47 MEMORY=24g JOBS=8 experiments/mustvc/build.sh
D="docker run --rm --cpuset-cpus 0-7,40-47 --memory 24g -e STRETTO_COMMIT=549036d -v $PWD:/w:ro -v ~/mvc-work:/mvc"
$D mustvc:1.9-omp bash -c 'REPS=5 /w/experiments/mustvc/micro.sh /mvc/results/mustvc-micro.csv /mvc/runs/micro'
$D -t mustvc:1.9-omp ./tests/run_tests.sh          # the artifact's own script, verbatim
$D mustvc:1.9-omp bash -c '[COMPAT=1] REPS=3 /w/experiments/mustvc/corrbench.sh /mvc/corrbench /mvc/results/mustvc-corrbench[-compat].csv /mvc/runs/cb'
$D mustvc:1.9-omp bash -c '[COMPAT=1 ONLY=_omp_] REPS=3 /w/experiments/mustvc/idioms.sh /mvc/results/mustvc-idioms[-compat].csv /mvc/runs/idioms'
$D --user 1000:1000 -e HOME=/tmp stretto-exp:24.04 bash -c 'make -C /w/tracer MPI=openmpi &&
  REPS=5 /w/experiments/mustvc/stretto_bench.sh artifact /mvc/artifact/tests /mvc/results/stretto-micro.csv /mvc/runs/s-micro &&
  REPS=3 /w/experiments/mustvc/stretto_bench.sh corrbench /mvc/corrbench /mvc/results/stretto-corrbench.csv /mvc/runs/s-cb &&
  REPS=3 /w/experiments/mustvc/stretto_bench.sh idioms /w/idioms /mvc/results/stretto-idioms.csv /mvc/runs/s-idioms'
# applications: build-apps.sh slate|slate-compat|uintah /mvc, parsec|dlaf /h (WORK/<app> at /h),
# then apps.sh <app> /mvc/results/mustvc-apps.csv /mvc/runs/apps-<app> in the same mounts
# (Uintah: RUNS=3 TIMEOUT=300 NTIMEOUT=120 WARMUP=0 CELLS="np4-t4 np4-t2", with its source at
# /src/uintah, where its build looks for ups_spec.xml; DLA-Future also with the Open MPI image,
# OMPI_MCA_rmaps_base_oversubscribe=1, into mustvc-apps-openmpi.csv)
```

## Times

Wall time per run, medians of finished runs (`summary.py times apps`), on 16 processors shared
with the other runs of this campaign, so the numbers are coarse. Small programs: MUST-vc 1.7 s
per CorrBench run against 0.12 s without it (start-up dominates), Stretto 0.47 s traced as
untraced (Open MPI's start dominates) and 0.10 s of analysis. SLATE (compat, fixed, so that
runs finish): 21 s under MUST-vc on 2x2 and 28 s on 4x1, against 0.6 and 0.8 s, about 30 times;
PaRSEC on 1 rank 5.0 s against 0.08 s. MUST's first run with a layout builds its infrastructure
(20 to 40 s more); `apps.sh` makes one unrecorded run per cell first. Stretto's cost on the
applications is measured in their own experiments: the tracer adds 0 to 15 % to traced runs of
threaded programs (docs/threads.md §9), and a plain SLATE trace takes 34 to 436 s to analyse,
a patched one 1 to 3 s (§12). MUST-vc reports while the program runs; Stretto's verdict comes
after it, offline.

## Notes

- MUST-vc keeps, per envelope and per communicator, the last call only, and clears its history at
  the end of the outermost region's barriers; its verdict on a pair is a warning (receives,
  probes) or an error (collectives, serialized), with no statement of what the other order would
  do.
- Three programs of MUST-vc's suite labelled correct can deadlock: `ThreadLevel_serialized`
  (each thread receives inside the one unnamed critical section, so a thread blocked in
  `MPI_Recv` holds the section the peer's sending thread needs), `two_collectives_4` (a barrier
  task and a broadcast task per thread, in critical sections whose order differs between ranks;
  its own comment says so) and `concurrent_communicator_tasking_locks` (tied tasks block their
  thread on a lock while its holder waits in a barrier for the other rank). Untraced, 10 runs
  each ([hangs.csv](hangs.csv), [hangs-ompi.csv](hangs-ompi.csv), `hangs.sh`; a run alive after
  30 s counts as hung): with MPICH 4.0.2, clang 15 and libomp, the artifact's own toolchain, 0, 7
  and 0 hang; with Open MPI 4.1.6, gcc 13 and libgomp 6, 0 and 8. Under MUST every run of them
  finished.
- The PaRSEC race does not show under MPICH 4.0.2 (0 of 10 runs wrong), where it shows in every
  run under Open MPI ([parsec](../parsec/README.md)): MPICH posts a `Sendrecv`'s receive and send
  in one hold of its global lock, so a thread's own send meets its own receive first.
