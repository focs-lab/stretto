# The uniform campaign: harness

[RULE.md](RULE.md) fixes the protocol, with its amendments of 24 Nov 2026 (Section 10); this
directory is the harness that runs it. Nothing here is a result yet: `results/` is written by the
campaign itself.

Frozen commit: (set before the first run)

Declared deviations: none yet. A change to a tool during the campaign is listed here with its
date and reason, and every block it can affect is run again (RULE.md, Section 1.7). A block
replaced for a harness failure is named here, and its rows stay in its table with `superseded`
set (Section 1.8; `rows.py supersede`).

## What is built

| what | how | where |
|---|---|---|
| experiment image | `experiments/container/Dockerfile` | `stretto-exp:24.04` (Open MPI 4.1.6, MPICH 4.2.0, gcc 13) |
| MUST 1.11.2 against both MPI libraries, Fortran on | [must/build.sh](must/build.sh) (`FROM stretto-exp:24.04`, clang 18 of the same archive snapshot) | `stretto-must-u:24.04`, `/opt/must/mpich`, `/opt/must/openmpi` |
| NINJA with every repair of the earlier campaigns | `experiments/ninja/build.sh build` | `third_party/ninja/<mpi>/libninja-fixed-all.so` |
| the tracer, its stub runtime and the replay library | `make -C tracer`, `make -C replay` (both MPIs) | `tracer/bin/`, `replay/bin/<mpi>/` |
| every application subject, bug and fix | [build_subjects.sh](build_subjects.sh), each subject's own `fetch` and `build` steps | `third_party/apps/<subject>/` |
| the suites' programs | `build_subjects.sh suites`: `third_party/fetch.sh corpora`, MPI-CorrBench at a05644b | `study/apps/MBI`, `third_party/mpi-bugbench`, `third_party/corrbench` |
| SLATE (both MPIs), Uintah for Open MPI | [build_threads.sh](build_threads.sh) `slate`, `uintah openmpi` | the machine's `SLATE_HOME`, `UINTAH_MOUNTS` |
| Uintah's image | `docker build -t stretto-uintah:24.04 experiments/uintah` | |

The other builds of the thread campaigns, MUST-vc's and the L1+L replays' are the committed
campaigns' own, where [machine-a-p13.env](machine-a-p13.env) says they are (DLA-Future and
PaRSEC: experiments/atomics' builds; Uintah for MPICH: experiments/uintah's; MUST-vc's work
directory). A block whose build is not there logs `missing-build` and runs nothing.

`libninja-fixed-all.so` is `libninja-fixed-some-testall.so` (random noise, Fortran,
Testsome/Waitsome, Testall/Waitall) with the Waitany repair of `libninja-fixed-any.so`; the build
checks that it differs from NINJA as shipped in exactly the lines of the five repairs. One build
of each application serves every tool: MUST runs in `stretto-must-u:24.04`, the experiment image
with MUST added. PETSc's `ex2` gets a fixed version (`VARIANT=fix experiments/petsc/run.sh build`,
HYPRE 3.2.0 under `experiments/hypre3/`'s fix).

```bash
# on the experiment machine, in a copy of the repository (experiments/RUNBOOK.md, "Where experiments run")
CPUS=0-17,40-57 MEMORY=40g JOBS=16 experiments/protocol/must/build.sh
docker run --rm --ulimit core=0 --cpuset-cpus 0-17,40-57 --memory 48g --user $(id -u):$(id -g) -e HOME=/tmp \
  -v $PWD:/w -w /w stretto-exp:24.04 bash -c "MAKE_JOBS=16 experiments/protocol/build_subjects.sh"
docker build -t stretto-uintah:24.04 experiments/uintah
docker run --rm --ulimit core=0 --cpuset-cpus 0-17,40-57 --user $(id -u):$(id -g) -e HOME=/tmp -e JOBS=16 \
  -v $PWD:/w -v $HOME/protocol/threads/slate:/h -w /w hunt-slate:24.04 experiments/protocol/build_threads.sh slate
docker run --rm --ulimit core=0 --cpuset-cpus 0-17,40-57 --user $(id -u):$(id -g) -e HOME=/tmp -e JOBS=16 \
  -v $PWD:/w -v $HOME/protocol/threads/uintah-openmpi:/h -v <Uintah 493d2b5>:/usrc:ro -w /w stretto-uintah:24.04 \
  experiments/protocol/build_threads.sh uintah openmpi
```

## The parts

| file | what it does |
|---|---|
| [plan.py](plan.py) -> [plan.csv](plan.csv) | every subject x configuration x version x tool x setting with its N, from RULE.md Sections 1.9, 2, 4-6 and 10 (`plan.py --check`) |
| [subjects.py](subjects.py) | the application subjects of Section 4: programs, arguments, MPI, judges, the bug's symptom, Stretto's classifier, MUST's racing call and site |
| [run.py](run.py) | one run of one tool on one application configuration, inside its container; writes the row |
| [replays.py](replays.py) | Section 6 for the applications: the distinct alternatives of a configuration's kept traces (a seeded sample of 40 if more), each with its control |
| [calibrate.py](calibrate.py) | Section 2.1 and amendment 5: `derive` turns the calibration runs into `results/<subject>/timeouts.csv` |
| [suites.py](suites.py) | Section 5: a suite's build for the tools that run its binaries, MUST 1.11.2 over a build, the selection of a suite's programs |
| [external.py](external.py) | the jobs of the committed scripts: suites, thread campaigns, MUST-vc, the L1+L lock replays, the MPI-PRV attempts |
| [machine-a-p13.env](machine-a-p13.env) | where those scripts' builds are on a-p13 |
| [schedule.py](schedule.py) | places every job of plan.csv on disjoint hardware threads of 0-17,40-57, resumable; writes `results/<block>.csv` and `results/journal.log` |
| [rows.py](rows.py) | the common columns, the outcome of Section 3, Wilson intervals, superseding |
| [adapters.py](adapters.py) | the common columns and the outcome for the committed scripts' tables |
| [summary.py](summary.py) | per block of run.py: N, outcomes, revealed per run with its interval, symptom rate, failures, seconds; `--estimate` |
| [smoke.sh](smoke.sh), [build_subjects.sh](build_subjects.sh), [build_threads.sh](build_threads.sh) | the smoke test of the applications; the builds |

Every row of `results/` has, besides its own columns, `host, cpus, jobs, timeout, seed, commit,
superseded, outcome`. `outcome` is one of `predicted`, `observed-reported`,
`observed-not-reported`, `missed`, `tool-failure`; on a fixed version or a correct program the
same categories are written, and there F is a false alarm. A replay row's outcome is its verdict
(`confirmed`, `not-confirmed`, `inconclusive`; the thread replays also `realised`, by
`experiments/replay_verdicts.py`). A committed script's own `outcome` column stays as
`script_outcome`; its rows carry the job's key in `protocol_job`.

## How a run is judged

- S, the bug's own symptom, from how the run ended and the subject's committed judge
  (subjects.py): a timeout is the symptom only of the hang bugs (HYPRE 2.10.1, HYPRE 3.2.0,
  PETSc, Cabana, LAMMPS, SuperLU_DIST), a tool failure on the others (Section 2.1); a finished
  run with a wrong output is the symptom of the wrong-result bugs; an abort is the symptom only
  when it carries the bug's own message (stempy's cereal exception; MFEM's "integer set not
  found" and MPI_ERR_TRUNCATE, amendment 12); every other end is a tool failure. Open MPI aborts
  with the error's class as the exit status and can lose the message when mpirun takes the other
  ranks down first: an exit status of 15 with no output is MPI_ERR_TRUNCATE (run.py
  `abort_class`; MFEM under NINJA and in replay controls).
- F for Stretto: the traced run's trace (kept, `KEEP_TRACES`) is analysed, struck runs included,
  for at most 3 600 s, on the job's own threads (amendment 9); the subject's committed classifier
  counts the alternatives of the bug's kind, whose pins run.py keeps for the replays.
- F for MUST: an error (not a warning, amendment 4) of MUST's report whose call, or one of whose
  referenced calls, is a racing call with a stack at the racing site. A run MUST stops at a
  deadlock shows no symptom: at the racing calls "predicted", elsewhere a tool failure
  (amendment 3). MUST runs with `--must:fanin 4`, and the centralised layout where that needs
  more than the 36 run threads (32 ranks, amendment 1); one uncounted warm-up per configuration
  precedes the calibration (amendment 6).
- F = S for plain runs, random delays and NINJA.
- The suites: the committed scripts' verdicts, by adapters.py (`stress`, `corpus`, `corrbench`,
  `bench`, `must`): S what the run itself showed, F a report of an error class (Stretto, MUST) or
  S (delays, NINJA).

Every rank is bound to its own hardware thread of the job's cpuset (amendment 8; Open MPI's
launcher for both libraries, amendment 2), the suites' programs too, except the OpenMP programs
and the thread campaigns, whose threads stay unbound on the job's threads.

## Blocks

A block is one table, `results/<subject>/<block>.csv`. A job is one container on its own threads.

Per application subject (run.py, replays.py):

| block | jobs | table |
|---|---|---|
| `ninja-learn` | NINJA's system-centric learning run per configuration and version (run 0, up to 3 tries, at the 1 800 s cap) | prerequisite |
| `must-warmup` | one uncounted MUST run of the fix per configuration | prerequisite |
| `calibrate` | 10 runs of the fix per configuration and tool setting, at most 1 800 s each; then `calibrate.py derive` | prerequisite |
| `plain` | 40 untraced runs, nothing loaded | main |
| `plain-serial` | 40 plain runs of the first configuration's bug alone on the machine (Section 1.9) | check |
| `delays-D50` | 40 runs with the replay library's D50 delays, seed Random(i*100+np) | main |
| `ninja-random` | 40 runs, 10% of the sends by 50 ms | main |
| `ninja-system`, `ninja-app` | 40 runs each | appendix |
| `ninja-random-1.2s` | HYPRE 2.10.1 only, 40 runs | appendix |
| `ninja-nonoise` | 10 runs, the library loaded and no send delayed | check |
| `must-1.11.2` | 40 runs under mustrun | main |
| `stretto-traced` | 40 traced runs, each analysed, its trace kept | main |
| `replay` | the enumeration (run 0) and up to 40 distinct alternatives, each with its control | main |
| `mpiprv` | one attempt, experiments/mpiprv/stretto_apps.sh in MPI-PRV's image | main |

Configurations marked supplementary in Section 4 (Cabana `-repeat2`, LAMMPS eager limit 0,
SuperLU_DIST g20) are rows of the same blocks with `table` = supplementary.

Per suite and MPI library (`<suite>/<mpi>-<tool>-<setting>`; MBI, MPI-BugBench under Open MPI,
CorrBench and its OpenMP programs under both):

| block | jobs |
|---|---|
| `build` | the suite compiled for the MPI library (suites.py build), for the tools that run its binaries |
| `stretto-traced` | 10 passes: run_mbi.py (MBI, MPI-BugBench), corrbench/run.py, mustvc/stretto_bench.sh (OpenMP, Open MPI only) |
| `stretto-L2` | MBI only, 10 passes of run_mbi.py `--rerun` (L2 by re-execution, a row of its own) |
| `delays-D50` | 10 passes of stress_baseline.py, one run per program, seed 20261124 + pass |
| `ninja-random`, `-system`, `-app` | 10 passes of ninja/run.py corpus each |
| `must-1.11.2` | 10 passes of suites.py must |
| `<tool>-budget` | the budget view: one job, up to 30 runs per program, stopping at the first failure (delays and each NINJA mode) |

Per thread campaign (external.py; one job is one run of every cell of the block, the committed
script with `RUNS=1` and the job's number as the run): `slate/series-openmpi`, `series-mpich`,
`traced-openmpi`; `dlaf/series-openmpi`, `traced-openmpi`; `parsec/series-openmpi`,
`traced-openmpi`; `uintah/series-openmpi`, `series-mpich`, 40 jobs each; the replays
`slate/replay-<mode>` and `parsec/replay-<mode>` for the gate modes after (main), before, yield
and both (appendix), `dlaf/replay-after` (DLA-Future's replay.sh has the default only), 20 jobs
each, one traced run and its sampled alternatives per job; `mustvc/<app>` for the four bugs, 40
jobs, each cell once without MUST-vc and once with; `locks/replay`, one job, every L1+L finding
replayed twice.

## Running, resuming

```bash
export MACHINE=a-p13 STRETTO_COMMIT=<frozen commit>
python3 experiments/protocol/schedule.py --blocks '/(ninja-learn|must-warmup|calibrate)$'
python3 experiments/protocol/calibrate.py derive
python3 experiments/protocol/schedule.py            # every other block of plan.csv
python3 experiments/protocol/summary.py --blocks '^hypre/'
```

`--blocks`, `--configs` and `--tools` (tool:setting) are regular expressions that select jobs;
`--dry-run` lists what would start; `--suite-select` and `--suite-limit` narrow the suites'
programs (the smoke test). The scheduler's state (row files, NINJA's learned files, MUST's layout
cache, kept traces, the alternatives' pins, the suites' builds and passes, the output of every
run that did not end correctly) is under `--state` (default `~/protocol/state`, `/state` in each
container); the tables and the journal under `--results`.

To resume after an interruption, run the same command again. A job whose row is in its table is
done; a row file not yet in its table is appended first; containers of an earlier invocation
keep their threads until they end and are collected then; every other job runs, and the journal
says which runs were started before and had no row ("restarted"). SIGINT or SIGTERM stops
starting jobs and lets the running ones finish; a second signal kills them (their runs have no row
and run again). A job that ends without a row (a library that did not load, a run killed before
its timeout, a trace that could not be kept, a script that wrote no table) stops its block for
that invocation; so does a missing build.

## Smoke test

Every path ran with 2 runs (2 passes, a suite's first 4 programs) on a-p13 on 24 Nov 2026, into
scratch directories since discarded; no job of the final code ended in a harness error.

Applications (`smoke.sh`, every application subject, every run.py and replays.py block):

| subject | Stretto (bug / fix) | MUST 1.11.2 | NINJA | plain, delays |
|---|---|---|---|---|
| HYPRE 2.10.1 | predicted 4/4 / no false alarm; replays confirmed | missed (8 ranks end since fan-in 4) | random hangs 1/2 at 4 ranks, app 2/2 at 8 | delays no hang |
| HYPRE 3.2.0 ex5 | predicted 4/4 / none | missed | random, system, app hang at times | none |
| PETSc ex2 | predicted 4/4 / none | tool failure 8/8: a deadlock report in PetscSF, fix too | app: APP-ERROR 59 in every run | none |
| NPB MG, 16 ranks | predicted 2/2 / none; replays 1 of 2 confirmed | missed | none | none |
| NPB MG, 32 ranks (plain, traced, MUST) | predicted 2/2 / none (analysis 729 s) | missed, centralised layout, 6.4 s a run | not smoked | plain 4.8 s; delays not smoked |
| deal.II rc1 | predicted 2/2 / none | missed (warnings only) | wrong logs in random, system, app | delays 2/2 wrong |
| deal.II NBX | predicted 4/4 / none | missed | none | none |
| Cabana | predicted 7/8, the struck run observed-reported; replays confirmed | missed | app: SIGNAL 11 in every run | delays hang 6/8 |
| stempy | predicted 4/4 / none | observed-reported 4/4, fix clean | app on the fix: SIGNAL 11 and hangs | delays 2/4 |
| MFEM 3.3.2 | predicted 2/2 at 4, observed-reported 2/2 at 8 | crash in every run, fix too | random, system, app reveal at 8 ranks | plain 2/2 at 8 ranks |
| LAMMPS | predicted 4/4 / none; eager 0: hangs, observed-not-reported | predicted 4/4 (send cycle), fix clean | eager 0: every run hangs, learning runs too | eager 0: every run hangs |
| SuperLU_DIST | predicted 16/16 / none | missed | app: SIGNAL 11 in every run | delays hang or wrong x in every run |
| QE elph_prt | predicted 2/2 / none | the fix beyond 600 s (amendment 5) | wrong tables at times | plain 1/2 wrong |

Suites (4 programs, 2 passes, both views): every block ran. MBI, MPI-BugBench and CorrBench
behave as their committed tables (MBI's first programs: Stretto observed-reported on the two
erroneous ones, MUST TIMEOUT on them as in `mbi.csv`; CorrBench's ArgError programs reported by
MUST); MUST 1.11.2 without its ThreadSanitizer layer reports none of the OpenMP data races
(missed 8/8); the OpenMP programs' delays and NINJA runs miss them too.

Thread campaigns: SLATE (series under both MPIs, traced cells), DLA-Future and PaRSEC (series,
traced cells, replays in every mode), Uintah under both MPIs (series: the plain builds truncate,
the fixed ones pass, as committed),
MUST-vc on the four bugs, the L1+L lock replays (104 replays: 66 confirmed, 7 realised, 31
inconclusive, by replay_verdicts.py) and the MPI-PRV attempts on the twelve subjects (each
recorded: instrumenter crashes, Fortran, no events woven) all ran.
SLATE's replays ran in mode after only (one job: 26 minutes, its 6 replays inconclusive); the
other modes are the same script with another `MODES`, smoked on PaRSEC.

## Wall time

From the smoke test's job times, perfect packing on the 36 run threads (lower bounds):

| part | hours | dominated by |
|---|---|---|
| applications, without NPB MG at 32 ranks | 37 | SuperLU_DIST's delayed runs hanging until T (10 h), Cabana's L1+SP analyses (7 h) |
| NPB MG at 32 ranks, alone | about 20 | delays (a run of the fix more than 8 minutes, 80 runs: 11 h), traced runs (a bug trace's analysis 729 s: 40 x 12 min = 8 h) |
| MBI | about 7 | 39 of its 115 programs hang until the 60 s timeout in every pass of every tool |
| MPI-BugBench | about 24 | 171 of its 388 programs hang until the timeout, 10 passes x 6 tools, and the budget view |
| MPI-CorrBench, both MPIs | about 15 | 77 of 513 programs hang; Stretto's passes run native, traced and guard runs |
| CorrBench's OpenMP programs | about 3 | the budget views under MPICH |
| thread campaigns, MUST-vc, lock replays | about 20 | SLATE's replays (26 minutes a job on 16 threads, 80 jobs: 15 h), the traced cells with their analyses, PaRSEC's replays, the lock replays (34 min) |
| MPI-PRV attempts | under 1 | deal.II's instrumentation (9 min) |
| **whole campaign** | **about 125** | with packing below perfect, 5 to 7 days of the machine |

## Disk

Kept traces are small: the applications' at 40 runs per configuration and version 0.2 GB (NPB MG
included), the thread campaigns' traced cells about 0.5 GB, the suites' traced passes under 2 GB,
the alternatives' pins, MUST's reports and the outputs of failed runs well under 1 GB. Proposal:
keep every trace of every traced run, about 3 GB. The builds take about 11 GB (`third_party/apps`
10 GB, the suites 60 MB, SLATE and Uintah under `~/protocol/threads` about 1 GB), the images 2.8 GB
(`stretto-must-u`) and 0.1 GB (`stretto-uintah`).

## Open points

1. **NPB MG at 32 ranks** holds the machine alone, also while its analyses run (729 s each on the
   bug's traces, one thread of 32 busy): 8 of its about 20 hours. Proposal: run an alone job's
   analysis as a job of its own on one thread (an exception to amendment 9).
2. **Replays of the thread campaigns** are the committed `replay.sh`'s: a sample of 2 or 3
   alternatives per traced run, 20 traced runs per mode here, not Section 6's distinct
   alternatives over the configuration's traces; their verdicts are `replay_verdicts.py`'s.
   DLA-Future's `replay.sh` has no gate modes, so only the default (after) runs. In the smoke test
   every thread replay of SLATE, PaRSEC and DLA-Future was inconclusive (the replays' or controls'
   synchronisation diverged from the trace's, a lock pin fell elsewhere), which the committed
   campaigns' replays mostly were not: to be looked at before the counted replays.
3. **Timeouts of the committed scripts.** The suites keep their 60 s per program run (CorrBench's
   run.py 30 s), the thread scripts theirs; Section 2.1's calibration is applied to the
   application subjects only.
4. **MUST on the OpenMP programs** runs without ThreadSanitizer (`must1112`, not `must1112tsan`),
   so it cannot report their data races; the committed MBI table has both columns.
5. **Builds of the committed thread campaigns** are partly gone from a-p13: SLATE's and Uintah's
   for Open MPI are rebuilt here; DLA-Future and PaRSEC run the atomics campaign's builds, Uintah
   for MPICH and MUST-vc their own campaigns'.
6. **MPI-PRV on NPB MG and Quantum ESPRESSO** is recorded as unsupported (Fortran) by
   stretto_apps.sh without running the instrumenter on a Fortran file (Section 2.6 asks for the
   attempt).
