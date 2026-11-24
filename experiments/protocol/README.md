# The uniform campaign: harness

[RULE.md](RULE.md) fixes the protocol; this directory is the harness that runs it. Nothing here
is a result yet: `results/` is written by the campaign itself.

Frozen commit: (set before the first run)

Declared deviations: none yet. A change to a tool during the campaign is listed here with its
date and reason, and every block it can affect is run again (RULE.md, Section 1.7). A block
replaced for a harness failure is named here, and its rows stay in its table with `superseded`
set (Section 1.8; `rows.py supersede`).

## What is built

| what | how | where |
|---|---|---|
| experiment image | `experiments/container/Dockerfile` | `stretto-exp:24.04` (Open MPI 4.1.6, MPICH 4.2.0, gcc 13) |
| MUST 1.11.2 against both MPI libraries, Fortran on | [must/build.sh](must/build.sh) (`must/Dockerfile`, `FROM stretto-exp:24.04`, clang 18 of the same archive snapshot) | `stretto-must-u:24.04`, `/opt/must/mpich`, `/opt/must/openmpi` |
| NINJA with every repair of the earlier campaigns | `experiments/ninja/build.sh build` | `third_party/ninja/<mpi>/libninja-fixed-all.so` |
| the tracer and the replay library, both MPIs | `make -C tracer`, `make -C replay` | `tracer/bin/<mpi>/`, `replay/bin/<mpi>/` |
| every subject at its version with the bug and its fix | [build_subjects.sh](build_subjects.sh), each subject's own `fetch` and `build` steps | `third_party/apps/<subject>/` |

`libninja-fixed-all.so` is `libninja-fixed-some-testall.so` (random noise, Fortran,
Testsome/Waitsome, Testall/Waitall) with the Waitany repair of `libninja-fixed-any.so`; the build
checks that it differs from NINJA as shipped in exactly the lines of the five repairs.

One build of each program serves every tool: MUST runs in `stretto-must-u:24.04`, which is the
experiment image with MUST added, so MUST runs the same binary under the same MPI library as
Stretto, the delays and NINJA (Section 1.6). PETSc's `ex2` gets a fixed version here for the
first time: `VARIANT=fix experiments/petsc/run.sh build` configures PETSc with HYPRE 3.2.0 under
`experiments/hypre3/`'s fix.

```bash
# on the experiment machine, in a copy of the repository (experiments/RUNBOOK.md, "Where experiments run")
CPUS=0-17,40-57 MEMORY=40g JOBS=16 experiments/protocol/must/build.sh
docker run --rm --ulimit core=0 --cpuset-cpus 0-17,40-57 --memory 48g --user $(id -u):$(id -g) -e HOME=/tmp \
  -v $PWD:/w -w /w stretto-exp:24.04 bash -c "MAKE_JOBS=16 experiments/protocol/build_subjects.sh"
```

## The parts

| file | what it does |
|---|---|
| [plan.py](plan.py) -> [plan.csv](plan.csv) | every subject x configuration x version x tool x setting with its N, from RULE.md Sections 1.9, 2, 4-6 (`plan.py --check` says whether plan.csv is current) |
| [subjects.py](subjects.py) | the application subjects of Section 4: programs, arguments, MPI, judges, the bug's symptom, Stretto's classifier, MUST's racing call and site |
| [run.py](run.py) | one run of one tool on one configuration, inside its container; writes the row |
| [replays.py](replays.py) | Section 6: the distinct alternatives of a configuration's kept traces (a seeded sample of 40 if more), each replayed with its control |
| [calibrate.py](calibrate.py) | Section 2.1: `derive` turns the calibration runs into `results/<subject>/timeouts.csv` |
| [schedule.py](schedule.py) | places the jobs on disjoint hardware threads of 0-17,40-57, resumable; writes `results/<block>.csv` and `results/journal.log` |
| [rows.py](rows.py) | the common columns, the outcome of Section 3, Wilson intervals, superseding |
| [adapters.py](adapters.py) | the common columns and the outcome for the tables of the scripts that keep their own (suites, thread campaigns) |
| [summary.py](summary.py) | per block: N, outcomes, revealed per run with its interval, symptom rate, failures, seconds |
| [smoke.sh](smoke.sh) | the smoke test: every path, 2 runs each, into a scratch directory |

Every row of `results/` has, besides its own columns, `host, cpus, jobs, timeout, seed, commit,
superseded, outcome` (rows.py). `outcome` is one of `predicted`, `observed-reported`,
`observed-not-reported`, `missed`, `tool-failure`; on the fixed version the same categories are
written, and there F is a false alarm. A replay row's outcome is its verdict (`confirmed`,
`not-confirmed`, `inconclusive`).

## How a run is judged

- S, the bug's own symptom, from how the run ended and the subject's committed judge
  (subjects.py): a timeout is the symptom only of the hang bugs (HYPRE 2.10.1, HYPRE 3.2.0,
  PETSc, Cabana, LAMMPS, SuperLU_DIST), and a tool failure on the others (Section 2.1); a
  finished run with a wrong output is the symptom of the wrong-result bugs; an abort is the
  symptom only when its output carries the bug's own message (stempy's cereal exception; MFEM's
  "integer set not found" and MPI_ERR_TRUNCATE, a receive taking the next mesh's longer row);
  every other end is a tool failure (NINJA's own crashes on Cabana, PETSc and SuperLU_DIST in its
  application-centric mode among them).
- F for Stretto: the traced run's trace (kept, `KEEP_TRACES`) is analysed, struck runs included,
  for at most 3 600 s, and the subject's committed classifier counts the alternatives of the bug's
  kind; a run whose analysis exceeds 3 600 s is a tool failure ("analysis timeout").
- F for MUST: an error of MUST's JSON report whose call, or one of whose referenced calls, is a
  racing call (`must_calls`) with a stack at the racing site (`must_site`); warnings do not
  count. MUST stopping a program at a deadlock it reports (`MUST-DEADLOCK`) shows no symptom: at
  the racing calls the run is "predicted", at other calls a tool failure. MUST crashing (a
  rank's fatal signal, which mustrun reports and still prints its closing line after) is a tool
  failure.
- F = S for plain runs, random delays and NINJA.

Every rank is bound to its own hardware thread of the job's cpuset (Open MPI's launcher for both
libraries, `--use-hwthread-cpus`, map and bind by hardware thread, set by environment so that
mustrun's launch binds the same way). MUST's distributed layout adds two tool processes (three at
32 ranks, measured), which get threads of their own (plan.csv `cpus`).

## Blocks

A block is one table, `results/<subject>/<tool>[-<setting>].csv`, holding both versions and every
configuration of the subject for one tool setting. Per application subject:

| block | runs | table |
|---|---|---|
| `ninja-learn` | NINJA's system-centric learning run per configuration and version (run 0, retried up to 3 times, at the calibration cap) | prerequisite |
| `calibrate` | 10 runs of the fix per configuration and tool setting, at most 1 800 s each; then `calibrate.py derive` | prerequisite |
| `plain` | 40 untraced runs, nothing loaded | main |
| `plain-serial` | 40 plain runs of the first configuration's bug with no other job running (Section 1.9) | check |
| `delays-D50` | 40 runs with the replay library's D50 delays, seed Random(i*100+np) | main |
| `ninja-random` | 40 runs, 10% of the sends by 50 ms | main |
| `ninja-system`, `ninja-app` | 40 runs each | appendix |
| `ninja-random-1.2s` | HYPRE 2.10.1 only, 40 runs | appendix |
| `ninja-nonoise` | 10 runs with the library loaded and no send delayed | check |
| `must-1.11.2` | 40 runs under mustrun | main |
| `stretto-traced` | 40 traced runs, each analysed, its trace kept | main |
| `replay` | the enumeration (run 0) and up to 40 distinct alternatives, each with its control | main |
| `mpiprv` | one attempt (experiments/mpiprv/apps.py) | main |

Configurations marked supplementary in RULE.md Section 4 (Cabana `-repeat2`, LAMMPS eager limit 0,
SuperLU_DIST g20) are rows of the same blocks with `table` = supplementary in plan.csv.

The suites (MBI, MPI-BugBench, MPI-CorrBench and its OpenMP programs) and the thread campaigns
(Uintah, SLATE, DLA-Future, PaRSEC, MUST's vector-clock checks, the L1+L lock replays) are rows of
plan.csv whose `runner` is the committed script; their tables get the common columns and the
outcome through `adapters.py annotate --kind stress|must|corpus|series|traced` as each pass or
cell lands in its block's table. schedule.py does not start them (see "Not wired yet" below).

## Running a block, resuming

```bash
MACHINE=a-p13 STRETTO_COMMIT=<frozen commit> python3 experiments/protocol/schedule.py --blocks '^hypre/(ninja-learn|calibrate)$'
python3 experiments/protocol/calibrate.py derive --subjects hypre
MACHINE=a-p13 STRETTO_COMMIT=<frozen commit> python3 experiments/protocol/schedule.py --blocks '^hypre/'
python3 experiments/protocol/summary.py --blocks '^hypre/'
```

`--blocks` is a regular expression over block names; `--dry-run` lists what would start. The
scheduler's state (row files, NINJA's learned files, MUST's layout cache, kept traces, the output
of every run that did not end correctly) is under `--state` (default `~/protocol/state`, mounted
at `/state` in each container); the tables and the journal under `--results`.

To resume after an interruption, run the same command again. A job whose row is in its table is
done; a row file not yet in its table is appended first; containers of the earlier invocation
(`proto-*`) are waited for; every other job runs, and the journal says which runs were started
before and had no row ("restarted"). SIGINT or SIGTERM stops starting jobs and lets the running
ones finish; a second signal kills them (their runs have no row and run again). A job that ends
without a row (run.py's HARNESS-ERROR: a library that did not load, a run killed before its
timeout, a trace that could not be kept) stops its block for that invocation.

## Smoke test

`experiments/protocol/smoke.sh <subject> ...` runs every path of a subject with 2 runs per row,
the calibration with 2 runs per setting, into `~/protocol/scratch/smoke` (scratch, not results,
not committed). The smoke test of 24 Nov 2026 (a-p13, 2 runs per row) ran every application
subject through every block run.py and replays.py serve, except NPB MG at 32 ranks, of which only
NINJA's learning runs, MUST's and NINJA's random calibration ran before it was stopped (the
delayed runs alone would have held the machine for an hour), and Cabana's replay enumeration at
4 ranks with `-repeat2`, stopped after an hour of analysing its traces again (run.py now writes
each trace's alternatives when it analyses it, which the enumeration reads). No job ended in a
harness error. What it showed on the known cases:

| subject | Stretto (bug / fix) | MUST 1.11.2 | NINJA | plain, delays |
|---|---|---|---|---|
| HYPRE 2.10.1 | predicted 4/4 / no false alarm; replays confirmed | missed (np 8 runs end since fan-in 4) | random hangs 1/2 at 4 ranks, app 2/2 at 8 | delays no hang |
| HYPRE 3.2.0 ex5 | predicted 4/4 / none | missed | random, system, app hang at times | none |
| PETSc ex2 | predicted 4/4 / none | tool failure 8/8: a deadlock report in PetscSF, fix too | app: APP-ERROR 59 in every run (tool failure) | none |
| NPB MG (16) | predicted 2/2 / none; replays 1 of 2 confirmed | missed | none | none |
| deal.II rc1 | predicted 2/2 / none | missed (warnings only) | wrong logs in random, system, app | delays 2/2 wrong |
| deal.II NBX | predicted 4/4 / none | missed | none | none |
| Cabana | predicted 7/8, the struck run observed-reported; replays confirmed | missed | app: SIGNAL 11 in every run (tool failure) | delays hang 6/8 |
| stempy | predicted 4/4 / none | observed-reported 4/4 (struck under MUST), fix clean | app on the fix: SIGNAL 11 and hangs (T undefined, item 5) | delays 2/4 |
| MFEM 3.3.2 | predicted 2/2 at 4, observed-reported 2/2 at 8 | crash in every run, fix too (tool failure) | random, system, app reveal at 8 ranks | plain 2/2 at 8 ranks |
| LAMMPS | predicted 4/4 (2-buffering) / none; eager 0: hangs, observed-not-reported | predicted 4/4 (send cycle), fix clean | eager 0: every run hangs, learning runs too | eager 0: every run hangs |
| SuperLU_DIST | predicted 16/16 on the four matrices / none | missed | app: SIGNAL 11 in every run (tool failure) | delays hang or wrong x in every run |
| QE elph_prt | predicted 2/2 / none | fix beyond 600 s (T undefined, item 5) | wrong tables at times | plain 1/2 wrong |

## Wall time

From the smoke test's mean job seconds (summary.py `--estimate`: a block's thread-seconds over
the 36 run threads, an alone job's seconds as they are; perfect packing, so read these as lower
bounds, and the means of 2 runs are coarse where a bug strikes in some runs):

| subject | hours | dominated by |
|---|---|---|
| SuperLU_DIST | 9.9 | delayed runs on NOS2 and mhd3200b hanging until T (up to 2 800 s, 10 x a delayed run of the fix) |
| Cabana | 6.8 | the L1+SP analyses at 4 ranks (400 to 1 500 s each, with `-repeat2` too) |
| LAMMPS | 1.6 | eager limit 0: every run of the bug hangs until T |
| HYPRE 2.10.1 | 1.3 | NINJA's 1.2 s noise, delays |
| NPB MG at 16 ranks | 1.6 | delays (50 s a run) |
| NPB MG at 32 ranks | about 12, alone | delays: a delayed run of the fix takes more than 8 minutes, so 80 runs alone take about 11 h, more if T (10 x) is reached |
| PETSc, MFEM, QE, stempy, HYPRE 3.2.0, deal.II rc1, deal.II NBX | 0.3 to 0.8 each | MUST, delays |
| all application subjects | about 37 | plus the calibration (1.4) and NINJA's learning runs (0.1), included above |

By block over the application subjects (without NPB MG at 32 ranks): delays 9.7 h, Stretto's
traced runs 7.3 h, NINJA's settings 2.8 h, MUST 1.7 h, the calibration 1.4 h, the replays 1.1 h,
the serial plain runs 0.6 h, plain runs 0.5 h. The suites and the thread campaigns are not
smoked; the audit's estimate for them (MBI, MPI-BugBench and CorrBench with every tool over 10
passes, 8 to 10 h; the thread campaigns at 40 per cell, 10 to 15 h) stands. With packing below
perfect, the whole campaign is of the order of 60 to 70 hours of the machine.

## Disk

Kept traces are small. The smoke test's archives (xz, stretto.keep) at their largest per
configuration, times 40 runs and both versions: 0.18 GB for every application configuration but
NPB MG (SuperLU_DIST's mhd3200b 21 MB per configuration and version, QE 9 MB, the rest 0.1 to
2 MB), and about 0.04 GB for NPB MG (0.2 MB a trace, frozen rerun). The thread campaigns' traced
cells (25 cells x 40) at the configuration sweep's 0.5 MB a trace are about 0.5 GB, and the
suites' traced passes (about 1 100 programs x 10 passes, a few KB to 1 MB each) under 2 GB. The
pins of every alternative (STATE/alternatives/, JSON) and MUST's reports of the runs kept under
outputs/ add well under 1 GB.

Proposal: keep every trace of every traced run, struck and clean, bug and fix (about 3 GB in
all), and the replays' traces too (`run_checked`'s `keep_as`, not set yet: of the same order).
The builds take 10 GB under `third_party/apps` and the images 2.8 GB (`stretto-must-u:24.04`);
with 28 GB free on a-p13 on 24 Nov 2026 the 12 GB floor leaves about 13 GB, four times what the
campaign keeps.

## Where the harness had to choose, and where RULE.md cannot be applied as written

Each needs a decision before the frozen commit; the harness implements the proposal.

1. **MUST's layout.** MUST's distributed layout as mustrun chooses it reports, at 8 ranks, a
   collective root mismatch the program does not have (`MPI_Bcast` in `HYPRE_IJMatrixCreate`, on
   HYPRE 3.2.0's fix as on the bug, under both MPI libraries) and then hangs; a trivial program
   hangs there too. With fan-in 4 (and 2, and the centralised layout) the same runs end normally.
   Proposal: `--must:distributed --must:fanin 4` (run.py `must_layout`); the tool processes get
   threads of their own (2 to 5). Open at 32 ranks (NPB MG): fan-in 4 needs 43 processes, more
   than the 36 run threads, and fan-in 16, which run.py gives it now, crashed in the smoke test
   (UCX "connection reset by remote peer" in MUST's sends at ranks 25-27, on the fix). The
   centralised layout (33 processes) fits and ended on a small program at 8 ranks; it is to be
   tried at 32 ranks, else MUST's tool processes oversubscribe or MUST's NPB MG cell at 32 ranks
   is "cannot run" with that reason.
2. **MUST's launcher.** The image's MPICH needs PMIx, which its Hydra launcher does not provide
   (an MPICH program under `mpiexec.hydra` starts as a singleton); every tool, MUST included,
   launches with Open MPI's `mpirun`, as the committed harness does for MPICH programs.
3. **MUST stopping a program at a deadlock.** MUST aborts a program at a deadlock it predicts,
   also for standard sends that would have been buffered (LAMMPS), so the run shows no symptom:
   S is taken as not observed, the report at the racing calls is "predicted", and a deadlock
   report at other calls (PETSc's `PetscSF` waits on the fix) is a tool failure. The symptom rate
   S/N under MUST is therefore a lower bound.
4. **What MUST "reports".** Only MUST's errors count. Its warnings (`MUST_WARNING_INTEGER_ZERO`
   and the like) fall at every call site, the racing ones included, and would make every run of
   deal.II's fix a report.
5. **A calibration that does not end.** Section 2.1 needs the slowest of 10 runs of the fix; when a
   fix's run under a tool does not end (stempy under NINJA's application-centric mode: segfaults and
   hangs; Quantum ESPRESSO under MUST beyond 600 s in the smoke test), T is not defined and the
   block cannot start. Proposal: such a run is a tool failure, recorded, and T is taken from the
   runs that ended; if none ended the cell is "cannot run" with that reason.
6. **MUST's first run per layout builds the layout** (about 15 to 20 s, MUST's cache), which
   enters the calibration's slowest run. Proposal: one uncounted mustrun per process count and
   MPI library before the calibration.
7. **NINJA's learning run** has no T of its own in RULE.md; it runs before the calibration at the
   calibration's cap (the application-centric runs of the calibration start from its files) and is
   retried up to 3 times, as committed. On MFEM 3.3.2 it counts once the first mesh is done
   (`--learn-mark`, as committed); a configuration whose learning runs all fail gets tool failures
   in its application-centric block.
8. **Binding.** RULE.md Section 1.5 asks for each rank on its own hardware thread; the committed
   scripts oversubscribed without binding. The harness binds every rank (and MUST's tool processes)
   to one thread of the job's cpuset.
9. **Stretto's analysis runs on the job's threads**, inside the job, not on 18-19,58-59
   (Section 1.5 reserves those for analysis): it keeps the analysis of a run beside its run and
   holds no other job's threads. An analysis of Cabana at 4 ranks with `--gtest_repeat=2` takes
   minutes and holds 4 threads meanwhile.
10. **PETSc's fix** did not exist; it is built here from HYPRE 3.2.0's fix (Section 4 names one).
11. **Replays' outcome** is their verdict (`confirmed`, `not-confirmed`, `inconclusive`): the
    categories of Section 3 describe runs of a tool, not replays.
12. **MUST's vector-clock checks** run in their own artifact image (`mustvc:1.9-omp`), not MUST
    1.11.2; Section 2.3 ("only release 1.11.2") is read as applying to MUST itself.
13. **NPB MG at 32 ranks with random delays** runs for minutes (more than 8 minutes for one run of
    the fix at D50 in the smoke test), so T is of the order of an hour and its 80 runs, alone on the
    machine, take a day or more (see Wall time).
14. **The suites and the thread campaigns** keep their committed scripts (below); they are planned,
    their outcome rules are in adapters.py, but schedule.py does not start them and they are not
    smoked.

## Not wired yet

These rows of plan.csv have a `runner` that schedule.py does not start; they need their own
invocation before the campaign counts them:

- The suites (Section 5): per pass, the committed corpus scripts with one run per program
  (`stress_baseline.py --runs 1 --out`, `ninja/run.py corpus --runs 1`, the traced corpus step,
  `mbi/run_must.py` with `MUST_IMAGE=stretto-must-u:24.04 MUST_PREFIX=/opt/must/openmpi`), and
  for the budget view `--runs 30`; each pass's table then goes through `adapters.py annotate`.
  Not smoked.
- The thread campaigns (Uintah, SLATE, DLA-Future, PaRSEC): their committed `series.sh`,
  `traced.sh` and `traced_cells.sh` in their own images with `RUNS=40` per cell, then
  `adapters.py annotate --kind series|traced`; the replays by `experiments/replay_modes.sh` in
  gate mode `after` (main) and `before`, `yield`, `both` (appendix); MUST's vector-clock cells by
  `experiments/mustvc/apps.sh`; the L1+L lock replays by `experiments/mustvc/lock_replays.sh`.
  Not smoked.
- MPI-PRV's attempt per subject (`experiments/mpiprv/apps.py`, its own image). Not smoked.
- `tables.py` (Section 7, third item): the proportions from these tables with Wilson macros, the
  NINJA "best" macros dropped, the hand-typed numbers generated. Not started.
