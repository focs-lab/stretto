# SLATE leads of the configuration sweep: verdicts

The leads and the test for each were committed before the code was read
([LEADS.md](LEADS.md), commit dd1c6b6). SLATE 878439b (current master), Open MPI 4.1.6, the
sweep's build of the tester, one node of a-p13.

| lead | verdict | evidence |
|---|---|---|
| F `2-thread-finalize`, 103 cells | tool artefact | every finding named the chain into which the tracer puts every chain beyond its table of 256; traced again with a table of 4 096, 85 cells have a complete trace and none has the finding; the analysis fixed (43c4c09) reports nothing on all 103 of the sweep's own traces |
| C `2-thread-collective`, 17 cells | tool artefact | as F: the pair was always that chain and another |
| K `2-collective`, 10 cells | tool artefact | the order of a rank's collectives with that chain among them is not known; gone from every complete trace and from the fixed analysis |
| T `hetrf`, `hesv`, `hetrs` with 4 threads, 15 cells | **real bug** (a hang, not a race) | 0 of 140 untraced runs of 7 cells finish, 140 of 140 with the one-line fix; below |
| T `unmtr_hb2st` wide 4x1 | **real bug** (a deadlock of blocking broadcasts inside OpenMP tasks), reached by `heev` with eigenvectors | untraced at 4 threads: 13 of 400 runs hang (4x1), 6 of 100 (1x4), `heev` 4 of 50; with the broadcasts taken out of the tasks 0 of 300 and `heev` 0 of 50; below |
| T `tb2bd` (2 cells), `trtri` | no bug found | untraced 9 of 10 runs of each `tb2bd` cell finish on processors shared with the other series; 200 of 200 of `tb2bd` tall 2x2 on processors of its own; `trtri` 10 of 10 |

Files: [leads.csv](leads.csv) (the list), [retrace.csv](retrace.csv) (complete-chain traces,
`retrace.sh` with the tracer built `-DMAXCH=4096`), [reanalysed.csv](reanalysed.csv) (the
sweep's traces under the fixed analysis, `reanalyse.sh`), [plain.csv](plain.csv) and
[plain2.csv](plain2.csv) (untraced series, 120 s timeout, with and without
`OMP_MAX_ACTIVE_LEVELS=2`), [fixed.csv](fixed.csv) (untraced, the library with
[hetrf-levels.patch](hetrf-levels.patch)), [crash.csv](crash.csv) (the `map::at` aborts),
[unmtr-rate.csv](unmtr-rate.csv), [heev.csv](heev.csv) and [unmtr-traced.csv](unmtr-traced.csv)
(`unmtr_hb2st` and `heev`, plain and with [unmtr-hb2st-bcast.patch](unmtr-hb2st-bcast.patch),
lib `fixed2`), [stacks.sh](stacks.sh) and `hunt-*.txt` (runs until one hangs, then stack dumps).
The `/w/cells` copies of `unmtr_hb2st` and `heev` cells take the sweep cell's arguments with the
thread count of their name.
The first nine rows of `plain.csv` carry commit 37376a4 because the copy's COMMIT file lagged;
the script that wrote them is 8f5c626's.

## F, C, K: what the tracer's chain table did

SLATE's tester makes one chain per OpenMP task that calls MPI, 500 to 2 000 per run in these
routines. Past the tracer's table of 256 every later chain writes its records as tid -1. The
analysis took that merged chain as one more thread: none of its records is ordered by
happens-before against the other chains (so `MPI_Finalize` and the other chains' collectives
looked concurrent with it), the order of the rank's collectives on a communicator it used is
unknown (so two ranks seemed to disagree), and the `begin` of a blocking call there was paired
with whatever thread wrote next (so blocked calls and unreceived messages appeared, 2-blocked
and 1c, in runs that finished). The fix: no observed thread finding involves that chain, no
collective order is judged on its communicators, and its begins pair with a later record of
their call. Tracing with a table of 4 096 confirms it on the cells it can record:

- 85 of 103 cells: complete trace (503 to 1 977 chains), no finding of any class.
- 15 `matgen` cells: more than 4 096 chains, the trace still overflows (1.8 GB uncompressed),
  and the analysis was killed at its 16 GB limit; decided by the fixed analysis alone.
- 4 cells (`heev` 2x2, `svd` 2x2 and 4x1, `tb2bd` 4x1) ran past 300 s with the larger table on
  four shared processors; decided by the fixed analysis alone.

## T: `hetrf` hangs when its panel has more than one thread

**Observation.** Every cell of `hetrf`, `hesv` and `hetrs` with 4 threads per rank timed out in
the sweep (15 of 15) and none with 2. Untraced, the 4-thread cells hang too: 0 of 140 runs of
seven cells (five `hetrf` grids and shapes, `hesv` and `hetrs` 1x2) finish in 120 s.

**Where.** Stacks of a hung run: rank 0's only busy thread spins (99.7 % CPU) in
`slate::tile::getrf` under `internal::getrf_panel`, called from `impl::hetrf`; rank 1 waits in
`MPI_Wait` in `tileIbcastToSet` for the broadcast rank 0 never reaches.

**Cause.** The tester sets `Option::MaxPanelThreads` to `max(omp_get_max_threads() / 2, 1)`
(`test/test.cc:455`), 2 at 4 threads; `hesv`'s documentation gives the same default
(`src/hesv.cc:79`). `internal::getrf_panel` runs the panel as `#pragma omp parallel for
num_threads(thread_size)`, whose threads meet in a spinning `ThreadBarrier`
(`include/slate/internal/util.hh:153`). Inside `hetrf`'s own `omp parallel` region this nested
region is active only if OpenMP's max-active-levels allows it; `getrf.cc:79` and `gbtrf.cc`
raise it with `slate::OmpSetMaxActiveLevels set_active_levels( MinOmpActiveLevels )`, `hetrf.cc`
does not. With libgomp's default of one active level, the nested region gets one thread, which
runs iteration 0 and waits at the barrier for a thread that is never created.
`hetrf.cc:55` reads "Using > 1 thread leads to hang, reason unclear" above its own default of 1.

**Fix.** The same guard at the top of `impl::hetrf` ([hetrf-levels.patch](hetrf-levels.patch)):
140 of 140 runs of the seven cells finish (at most 1 s each). Setting `OMP_MAX_ACTIVE_LEVELS=2`
for the unpatched library also lets them finish (69 of 70), which confirms the cause; the one
other run aborted with `std::out_of_range` (`map::at`) in `hetrf` (below).

**Stretto's part.** None: the hang is a thread barrier, not a matching. The sweep's traced runs
surfaced it by timing out, the untraced series and the stacks located it.

## T: `unmtr_hb2st` deadlocks in its broadcasts

**Observation.** The traced `unmtr_hb2st` cell wide 4x1 (2 threads) timed out in the sweep; the
other 14 cells passed. Untraced, the cell hangs now and then: 1 of 100 runs with 2 threads per
rank, 13 of 400 with 4 (60 s timeout, runs that finish take 1 to 3 s); on a 1x4 grid 6 of 100
with 4 threads. `heev` with eigenvectors (`--jobz v --method-eig dc`, 2x2, 4 threads), which
back-transforms with `unmtr_hb2st` on a 1-by-p grid (`src/heev.cc:193-199`), hangs in 4 of 50
runs, every thread of every rank again in `tileBcast` under `internal::unmtr_hb2st`. Traced with the default
tracer, 1 of 10 runs hung and the 9 that finished give no alternative at L1 and no pending
same-envelope receive pair.

**Where.** Stacks of a hung run (2 threads): on all four ranks both threads are inside
`BaseMatrix::tileBcast`, called from the tasks of `internal::unmtr_hb2st`
(`src/internal/internal_unmtr_hb2st.cc:128-143`); the roots in `MPI_Waitall` on their sends of
160 000 bytes, the others in `MPI_Wait` in `tileIbcastToSet`. The trace of the hung traced run
shows three sends never received and every chain of every rank blocked in a wait.

**Cause.** Each task of the loop broadcasts V(0, r) to the ranks of row C(i, :) with a blocking
`tileBcast` and then computes. The tasks of independent rows run in whatever order each rank's
scheduler picks, and a thread waiting at a task scheduling point (`GOMP_taskgroup_end` in the
stacks) can start another such task under the one it waits for. A send of 160 000 bytes is
rendezvous, so a root blocks until its receiver posts the receive; when every thread of the
receiver is inside a different broadcast that waits on the first rank, no thread is left to
post it, and all ranks wait. Nothing about the matching changes, so Stretto, which assumes a
free thread for every chain (premise P-T7), has nothing to predict.

**Fix.** Broadcast every V(0, r) before the tasks, in the loop's own order, which is the same on
every rank ([unmtr-hb2st-bcast.patch](unmtr-hb2st-bcast.patch); the tasks keep their compute
part; the code's own comment at the broadcast reads "todo make async; put in different
task"): 0 of 300 runs hang at 4 threads, against 7 of the 300 plain runs interleaved with them
on the same processors; `heev` 0 of 50 against 4 of 50. Every patched run passes the tester's
checks.

## Open

- `hetrf` with two panel threads (the fixed library, or `OMP_MAX_ACTIVE_LEVELS=2`), square 2x1,
  4 threads: 1 of 60 and 2 of 50 runs abort with `std::out_of_range` (`map::at`) thrown in
  `BaseMatrix::tileBcast` from a task of `impl::hetrf`; the other six cells 0 of 20 each.
  Not examined further ([crash.csv](crash.csv)).
- Stretto's model assumes a free thread for every chain (P-T7), so a deadlock of blocking MPI
  calls inside a bounded pool of task threads, as in `unmtr_hb2st`, is outside what it predicts.
