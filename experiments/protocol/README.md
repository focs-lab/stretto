# The uniform campaign: harness

[RULE.md](RULE.md) fixes the protocol, with its amendments of 24 and 25 Nov 2026 (Sections 10 to 13); this
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

**The surviving builds, checked against their campaigns (amendment 18; a-p13, 24 Nov 2026, inside
a container with the work directories mounted read-only).** Each build's log names the sources it
was built from, and its patched trees were compared with the repository's patches:

| build (a-p13) | campaign and what it records | found | check |
|---|---|---|---|
| DLA-Future, `DLAF_BUILD` | experiments/dlaf, "with pika's atomics recorded" (1 Nov): DLA-Future 09fe442, pika 947cf61, `copy-tags.patch` | `build-tsan.log`: DLA-Future 09fe442, pika 947cf61; `bin/copy_check{,_fixed}` of 1 Nov; `bin/dlaf-fixed-include` is the headers with `copy-tags.patch` applied (it reverses cleanly) | matches |
| PaRSEC, `PARSEC_BUILD` | experiments/parsec, the same section and the replays of 2 Nov (commit column c466813): PaRSEC 6c49997, `self-tags.patch` | `build-tsan.log`: PaRSEC 6c49997, clang 18; `reshape_check` of 1 Nov; the fixed tree has `self-tags.patch` applied (reverses cleanly), the plain tree has not (applies cleanly) | matches |
| Uintah for MPICH, `UINTAH_MPICH_MOUNTS` | experiments/uintah, the series of 30 Oct (no commit column): Uintah 493d2b5, hypre 2.33.0, MPICH 4.2.0, `hypre-comm.patch` | the clone at 493d2b5; `mpich/{plain,fix}/StandAlone/sus` of 29 Oct; the work directory's `uintah-hypre-comm.patch` and `hypre-comm.patch` are the repository's byte for byte; its `build.sh` is an earlier version of experiments/uintah/build.sh (before the ThreadSanitizer, component and symbolic-link options, which these Examples builds do not use); the patched source tree it built from (`src-fix`, a link to another checkout) is gone | matches, the patched tree not rechecked |
| MUST-vc, `MUSTVC_WORK`, image `mustvc:1.9-omp` | experiments/mustvc (commit column 549036d): the artifact at e1b187e, its tarball's sha256 in `build.sh`, image sha256:1059f9395c0c; the applications at PaRSEC 6c49997, DLA-Future 09fe442, pika 947cf61, SLATE 878439b, Uintah 493d2b5 | the artifact clone at e1b187e; the tarball's sha256 is build.sh's; the image is sha256:1059f9395c0c (`image-id.txt` holds an earlier build of the same morning, superseded by `build-image2.log`'s); the repository copy's `COMMIT` is 549036d; the build logs name the same five source commits | matches |

DLA-Future's and PaRSEC's builds link the tracer's stub runtime (`libstretto-tsan.so`) from the
path they were built in, which is gone; the campaign gives them the frozen commit's
(`*_LD_LIBRARY_PATH=/w/tracer/bin`).

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
| [calibrate.py](calibrate.py) | Section 2.1 and amendment 5: `derive` turns the calibration runs into `results/<subject>/timeouts.csv`; `derive-suites` the suites' into `results/<suite>/suite-timeouts.csv` (amendment 15); `derive-threads` the thread campaigns' into `results/thread-timeouts.csv`, one T per block and cell (amendment 20) |
| [../thread_timeouts.sh](../thread_timeouts.sh) | what the thread campaigns' scripts take from the harness: a T per cell (`TIMEOUTS`, a calibration's `CAP`), the cells and variants to run (`ONLY`, `VARIANTS`), each run's seconds (`SECONDS_OUT`), `ANALYSE=0`; without them a script runs as committed |
| [suites.py](suites.py) | Section 5: a suite's build for the tools that run its binaries, MUST 1.11.2 over a build (`--tsan`: with ThreadSanitizer, amendment 16), the selection of a suite's programs (`--correct`: those labelled correct) |
| [threadreplays.py](threadreplays.py) | Section 6 for SLATE, PaRSEC and DLA-Future (amendment 17): traced runs of the replay cell, the distinct alternatives over their traces (a seeded sample of 40), each replayed twice with its control per gate mode |
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
  counts the alternatives of the bug's kind, whose pins run.py keeps for the replays (a sidecar
  for every analysis, empty when it found none; the replays take only these, amendment 26).
- Runs that end early or leave nothing are rows with outcome tool failure, never rerun
  (amendments 25 and 29): a run killed before T by the job's own memory limit (run.py's `KILLED`
  when the job's cgroup counted the kill, `memory.events`; a container Docker records as
  `State.OOMKilled` gets its row from the scheduler: a replay's with a verdict, inconclusive, a
  delays run's with its seed, a calibration run's with the seconds it lasted, left out of T as a
  capped run), a traced run whose trace is empty or unreadable, an analysis killed before its
  budget ("analysis killed", not "analysis timeout"). Any other kill, and any job that leaves no
  row, is a harness failure (Section 1.8): the job runs again, at most twice in an invocation
  (then its block stops), and every rerun is listed in [`results/reruns.tsv`](results/) (time,
  block, job, reason). A deferred analysis gets the memory its traced run's job had.
- F for MUST: an error (not a warning, amendment 4) of MUST's report whose call, or one of whose
  referenced calls, is a racing call with a stack at the racing site. A run MUST stops at a
  deadlock shows no symptom: at the racing calls "predicted", elsewhere a tool failure
  (amendment 3). MUST runs with `--must:fanin 4`, and the centralised layout where that needs
  more than the 36 run threads (32 ranks, amendment 1); one uncounted warm-up per configuration
  precedes the calibration (amendment 6). The centralised layout is mustrun's without
  `--must:distributed` (MUST 1.11.2 has no option that names it): run.py records the layout in the
  row and stops with a harness error if mustrun does not say "centralized checks". MUST's layouts
  are cached per MPI library (`STATE/must-cache/<mpi>`, the suites' cache too), and every warm-up,
  the applications' and the suites', takes the cache's lock, so that one layout is built at a time.
  No MUST run of a configuration, calibration included, starts before its warm-up has ended
  (amendment 31), and the warm-up checks that mustrun started one process per thread of the job
  (the levels of `must_temp/src/layout.xml`: 21 at 16 ranks, 33 at 32; a harness error otherwise).
- F = S for plain runs, random delays and NINJA.
- The suites: the committed scripts' verdicts, by adapters.py (`stress`, `corpus`, `corrbench`,
  `bench`, `must`): S what the run itself showed, F a report of an error class (Stretto, MUST) or
  S (delays, NINJA). A run of a program labelled erroneous that does not finish within T shows
  the symptom (amendment 28); on a program labelled correct a timeout counts as the committed
  `tab:rq2` scripts count it: a failing run for the delays and NINJA (experiments/tables.py
  `stress`, any `first_failure`), no report for MUST on MBI and MPI-BugBench (experiments/mbi/
  score.py `must_verdict`: TIMEOUT is not ERROR), a run without a verdict, a tool failure, for
  MUST on CorrBench (experiments/corrbench/summary.py), Stretto's report for Stretto. Against the
  committed MBI and MPI-BugBench tables (stress.csv, the corpus tables' must1112 and Stretto
  columns) and CorrBench's (must.csv, stretto-openmpi.csv) the adapters give the committed
  reveals per tool and label (a unit test); CorrBench's "failed run only" (an error finding
  observed on a run that did not finish) is observed-reported under Section 3. MUST's report
  counts when it holds an error, a data race alone only on a program labelled a data race
  (amendment 22; must_once.sh says "data race:" when the race is all MUST reported; suites.py
  `labels`). Stretto's suite rows carry `cell` (amendment 23): `stretto` for the traced
  runs, `plain` for corrbench/run.py's native runs and stretto_bench.sh's untraced run 0,
  `stretto-guard` for the buffer guard's runs (appendix). The random delays keep the committed
  per-program seeding, `Random("<seed>:<program>")`, the pass's seed in the rows.
- The thread campaigns: Uintah's plain build has every run that does not pass as its symptom,
  the garbage reduction's abort and its hang alike (amendment 27); a series run of Open MPI that
  exited with status 15 is MPI_ERR_TRUNCATE whose message was lost (SLATE's 1a collision), the
  symptom.

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
| `stretto-traced` | 40 traced runs, each analysed, its trace kept; NPB MG at 32 ranks: the run alone, its analysis a job of its own on one thread (`traced-analysis`, amendment 14) | main |
| `replay` | the enumeration (run 0) and up to 40 distinct alternatives, each with its control | main |
| `mpiprv` | one attempt, experiments/mpiprv/stretto_apps.sh in MPI-PRV's image | main |

Configurations marked supplementary in Section 4 (Cabana `-repeat2`, LAMMPS eager limit 0,
SuperLU_DIST g20) are rows of the same blocks with `table` = supplementary.

Per suite and MPI library (`<suite>/<mpi>-<tool>-<setting>`; MBI, MPI-BugBench under Open MPI,
CorrBench and its OpenMP programs under both):

| block | jobs |
|---|---|
| `build` | the suite compiled for the MPI library (suites.py build), for the tools that run its binaries |
| `calibrate` | per tool setting, every program the suite labels correct once, at most 300 s each; then `calibrate.py derive-suites`: one T per suite and tool, max(30 s, 10 x the slowest run of any of the tool's settings under either MPI), a run past the cap a tool failure left out (amendments 5 and 15) |
| `stretto-traced` | 10 passes: run_mbi.py (MBI, MPI-BugBench), corrbench/run.py, mustvc/stretto_bench.sh (OpenMP, Open MPI only) |
| `stretto-L2` | MBI only, 10 passes of run_mbi.py `--rerun` (L2 by re-execution, a row of its own) |
| `delays-D50` | 10 passes of stress_baseline.py, one run per program, seed 20261124 + pass |
| `ninja-random`, `-system`, `-app` | 10 passes of ninja/run.py corpus each |
| `must-1.11.2` | 10 passes of suites.py must |
| `must-1.11.2-tsan` | MBI and CorrBench's OpenMP programs: 10 passes of suites.py must `--tsan`, the column `must1112tsan` (amendment 16) |
| `<tool>-budget` | the budget view: one job, up to 30 runs per program, stopping at the first failure (delays and each NINJA mode) |

Per thread campaign (external.py; one job is one run of every cell of the block, the committed
script with `RUNS=1` and the job's number as the run, each cell at its own T, amendment 20):
`<subject>/calibrate-<block>` (and `mustvc/calibrate-<app>`) first, 10 jobs of one run of each
cell's fixed variant (a control cell's plain one; MUST-vc's with and without MUST) at the 1 800 s
cap, then `calibrate.py derive-threads`; `<subject>/plain-serial`, 40 plain runs of the first cell
(SLATE wide-2x2, DLA-Future 2x1-offset, PaRSEC vp2-mt, Uintah 4 ranks with 2 threads) alone on
the machine, the concurrency check of Section 1.9; `slate/series-openmpi`, `series-mpich`,
`traced-openmpi`; `dlaf/series-openmpi`, `traced-openmpi`; `parsec/series-openmpi`,
`traced-openmpi`; `uintah/series-openmpi`, `series-mpich` (every committed cell, amendment 30:
4 ranks with 2 and with 4 threads, 2 ranks with 2, and the MPI scheduler's control at 4 ranks),
40 jobs each; the replays of
threadreplays.py (amendment 17): `<subject>/replay-traces` (the replay cell traced with its lock
order: SLATE 16 runs, of which those with a right factor are kept, as the committed campaign of
2 Nov; PaRSEC and DLA-Future 5), `<subject>/replay-alternatives` (the distinct alternatives over
those traces, a sample of 40 drawn with `random.Random("20261124:<subject>")` if there are more),
then `slate/replay-<mode>` and `parsec/replay-<mode>` for the gate modes after (main), before,
yield and both (appendix), one job per alternative, each replayed twice with its control; and
`dlaf/replay-after`, DLA-Future in its one mode (its replay.sh has no gate modes; its table says
so in the notes); `mustvc/<app>` for the four bugs, 40 jobs, each cell once without MUST-vc and
once with; `locks/replay`, one job, every L1+L finding replayed twice at CorrBench OpenMP's
Stretto T. The replays' traced runs and replays take the replay cell's T (SLATE wide-4x1, PaRSEC
vp2-mt, DLA-Future 2x1-offset-jitter of the traced block). The enumeration waits for every
counted traced run in its table (whatever its outcome) and takes the alternatives of their
analyses only, made in the traced-run job within the 3 600 s budget; an enumeration whose set of
traced runs changed later is superseded with its replays and runs again (amendment 26; the same
for the applications' `replay`). SLATE's replays start from the traces whose factor came out
right only, since a wrong-factor trace's control cannot come out right: a restriction of the
replays, not of the counted traced runs (amendment 24). Each replay row has its verdict
(replay_verdicts.py) and the alternative's in its mode: confirmed if one of its two replays is,
else realised if one is, else inconclusive, beside the share of confirmed replays
(`alternative_verdict`, `confirmed_share`). No row carries a timeout that is not a number: a
thread row's is its cell's T, a build's and an enumeration's 0 (they run nothing).

## Running, resuming

```bash
export MACHINE=a-p13 STRETTO_COMMIT=<frozen commit>
python3 experiments/protocol/schedule.py --blocks '/(ninja-learn|must-warmup|calibrate|[a-z]+-build|[a-z]+-calibrate|calibrate-.*)$'
python3 experiments/protocol/calibrate.py derive
python3 experiments/protocol/calibrate.py derive-suites
python3 experiments/protocol/calibrate.py derive-threads
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
says which runs were started before and had no row; they run again, listed in
`results/reruns.tsv`. SIGINT or SIGTERM stops starting jobs and lets the running ones finish; a
second signal kills them (their runs have no row and run again). A job that ends without a row
(a library that did not load, a trace that could not be kept, a script that wrote no table, a
kill that is not its memory limit's) runs again, twice at most, each rerun in
`results/reruns.tsv`, then stops its block for that invocation; a missing build stops it at once.
A run killed by the job's own memory limit is a tool-failure row instead, never rerun (amendments
25 and 29). Containers are kept until the scheduler has read Docker's verdict on them and then
removed; they carry their job and whether it runs alone as labels, so that containers adopted
from an earlier invocation count in the jobs column and an adopted job that runs alone, selected
or not, keeps every other job from starting until it ends. schedule.py refuses to start without
`MACHINE`. summary.py shows a suite block per cell and label, with the share of its reveals that
are timeouts (amendment 28), and a thread replay block per alternative and mode as well as per
replay (amendment 24).

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
traced cells, and replay.sh's replays in every mode, which threadreplays.py has replaced since),
Uintah under both MPIs (series: the plain builds truncate, the fixed ones pass, as committed),
MUST-vc on the four bugs, the L1+L lock replays (104 replays: 66 confirmed, 7 realised, 31
inconclusive, by replay_verdicts.py) and the MPI-PRV attempts on the twelve subjects (each
recorded: instrumenter crashes, Fortran, no events woven) all ran.

**What changed with Section 11, smoked again (24 Nov 2026, a-p13).**

- *A lone job's analysis apart (amendment 14).* NPB MG at 32 ranks, one traced run of the bug and
  one of the fix, beside NPB MG at 16 ranks: each 32-rank run held the machine alone (6 s), wrote
  its row without the analysis, and its analysis then ran as a one-thread job beside the 16-rank
  runs (the bug's trace: 740 s on one thread, predicted; the fix's 2 s, no false alarm; the rows
  keep the 32-rank runs' threads). The first invocation started the bug's analysis beside the
  fix's 32-rank run, on the four threads that run left: nothing now starts while a job that runs
  alone runs (checked with HYPRE's `plain-serial` beside its `plain`).
- *The suites' timeouts (amendment 15).* MBI and CorrBench's OpenMP programs (4 programs, both
  MPIs for the OpenMP ones): every tool setting's calibration on the programs labelled correct,
  `derive-suites`, then a pass of each tool at its T. T came out at 30 s for every tool on MBI's
  four, 44 s (delays), 45 s (NINJA), 77 s (MUST, its ThreadSanitizer runs included) and 30 s
  (Stretto) on the OpenMP ones; MBI's two erroneous programs hang until T in every tool's pass, as
  committed.
- *MUST with ThreadSanitizer (amendment 16).* MUST's ThreadSanitizer runs segfaulted in
  `MPI_Init_thread` (PMIx's shared-memory store, `pmix_gds_shmem_fetch`), fixed with PMIx's hash
  store (`PMIX_MCA_gds=hash`); under Open MPI its runs then hung at random over the
  shared-memory transport (a correct two-rank MBI program: 5 of 7 runs past 60 s, MPICH 6 of 6
  in 7 s), and over TCP (`OMPI_MCA_btl=self,tcp`) 12 of 12 ended in 2 s. In the smoke pass one of
  the four OpenMP programs under Open MPI still timed out (a correct one, `data_race_ibcast`), none
  under MPICH; MUST with ThreadSanitizer reported neither erroneous program's race
  (`data_race_bcast`, `data_race_ibcast`), and in a probe it reported `data_race_isend`'s, in
  its correct version too. The first run of a process count builds MUST's layout (about 20 s
  with ThreadSanitizer): suites.py makes that run once per pass, uncounted, before the timed
  runs, one pass at a time.
- *Thread replays (amendment 17)*, below.
- *MPI-PRV on NPB MG and QE (amendment 19).* `wacc` on `MG/mg.f` with the build's flags and on
  `PHonon/PH/elphon.f90` (as `elphon.F90`, `-D__MPI`, the QE build's modules beside it): the
  instrumenter weaves its aspect only into units named `.c`, `.cpp`, `.cc`, `.cxx` or `.c++`
  (`wac/aspect/compilers.py` of its image) and handed each Fortran unit to clang as it was;
  clang drives gfortran without the build's flags (`-fallow-argument-mismatch` rejected,
  include paths dropped), so neither compiled, and neither has a monitor call: unsupported, with
  what the instrumenter ran in the cell.

**Why every thread replay of the earlier smoke was inconclusive (amendment 17).** The harness,
not the tool:

| subject | earlier smoke | the committed campaign that confirmed | cause |
|---|---|---|---|
| PaRSEC | every replay and control `diverged` (synchronised otherwise than the trace) | `replay-modes-1rank.csv` (2 Nov, c466813): 28 of 30 per mode, on the uninstrumented build of the second thread hunt, whose build trees are gone | the only PaRSEC build left is experiments/atomics' (PaRSEC 6c49997 with its atomics instrumented), and the smoke recorded its atomics (the default). The committed run on that build (`replay-atomics.csv`, 2 Nov) is the same: 30 of 30 inconclusive by `sync_divergence` with the recording on, 30 of 30 confirmed with `STRETTO_ATOMICS=0` (`replay-atomics-off.csv`) |
| SLATE | 6 of 6 inconclusive | `replay-modes-4x1.csv` (2 Nov): 7 of 14 in mode after | the smoke ran replay.sh's defaults: grid 2x2, whose traced runs all end with a wrong factor (milestone E: 9 of 9), so a replay's wrong factor shows nothing; the confirmations came from grid 4x1, right-factor traces only, 4 threads, classes 1a and 1f in the collision range |
| DLA-Future | every replay and control ended with a misapplied lock pin (exit 86) | none: `replay-sync.csv` (30 Oct, 06286e5) is the same, 0 of 24 ran their course (pika hands tasks to its workers through lock-free queues; README of experiments/dlaf) | none to fix; the replays stay inconclusive by construction, which the table says |

threadreplays.py now traces and replays with the committed confirming campaigns' settings
(SLATE's 4x1 cell, right factors, 4 threads, classes 1a and 1f; PaRSEC's 1-rank cell; DLA-Future's
2x1 cell, 1f), and with `STRETTO_ATOMICS=0` on the atomics builds. The tracer and the replay
library of this commit, with no other change, reproduce the confirmations, so there is no
regression of the tool to report: PaRSEC through the scheduler, 2 traced runs, 2 of its distinct
alternatives in modes after and before, 8 of 8 replays confirmed (a probe beforehand: of 4 other
alternatives of one trace, one confirmed 2 of 2, two realised 2 of 2 (pins taken, the result
right: harmless in that run), one inconclusive (pins not taken)); SLATE, 8 traced runs, 2 with a
right factor, 76 distinct alternatives, 40 sampled, 3 of them replayed in mode after: every replay
took its pins and computed a wrong factor beside a right control, and 1 of the 6 is confirmed, the
other 5 inconclusive because the replay's synchronisation diverged after a gate gave way (3 to 6
of 10 per mode in the committed campaign); DLA-Future, 2 traced runs, 129 distinct alternatives,
2 replayed: misapplied lock pins, as committed.

**What changed with Section 12, smoked again (24 Nov 2026, a-p13; one or two runs a block).**

- *Thread campaigns' T (amendment 20).* The calibrations of SLATE's series, PaRSEC's series and
  traced cells, Uintah's MPICH series and MUST-vc on PaRSEC (one run of each cell's fixed variant;
  `derive-threads --runs 1`) gave T = 30 s for every SLATE and PaRSEC cell, 44 and 45 s for
  Uintah's two, 44 and 318 s for MUST-vc's two PaRSEC cells. Then SLATE's `plain-serial`, PaRSEC's
  series, traced cells and replays, MUST-vc on PaRSEC and Uintah under MPICH ran at those T, every
  row with its own cell's T. SLATE's concurrency check: one wrong factor and one run that Open MPI
  aborted with status 15 (MPI_ERR_TRUNCATE, its message lost), the collision's other symptom,
  which the series judge now counts (it was a tool failure).
- *Replays from the counted analyses (amendments 24 and 26).* PaRSEC: 2 traced runs of the replay
  cell, each analysed in its job (15 alternatives each), the enumeration from their alternatives
  only (30 distinct), 2 alternatives replayed: one confirmed in 1 of its 2 replays (so confirmed,
  share 1/2), one inconclusive. HYPRE 2.10.1 at 4 ranks: an enumeration over 1 traced run was
  superseded, with its replays, once the configuration had 3 (the scheduler logs "superseded"),
  and ran again; both of its 2 alternatives confirmed. A traced run whose analysis found nothing
  has its empty sidecar.
- *Suites (amendments 21 to 23).* MBI's erroneous programs that hang until T, labelled deadlocks,
  are observed-reported under the delays; MUST with ThreadSanitizer on six of CorrBench's OpenMP
  programs reported the data races of the erroneous `data_race_isend` and `data_race_send` under
  Open MPI (predicted; the data-race label lets them count) and none under MPICH; Stretto's pass under MPICH ran
  (built with MPICH's wrapper, traced with the MPICH tracer), its rows as under Open MPI; every
  Stretto row of the OpenMP pass has its cell (`plain` for run 0, `stretto` for the traced run).
- *MUST at 32 ranks (review item 12).* NPB MG's warm-up and a run of each version took mustrun's
  centralised layout, which it announced ("centralized checks"), and the warm-up took the cache's
  lock. The suites' MUST warm-up had taken a pass's first program of the process count, on MBI an
  erroneous one that hangs, and waited for the warm-up's 1 800 s cap: it now takes a correct
  program where there is one (MBI's two erroneous programs then hung until T = 60 s,
  observed-not-reported, as committed).

**What changed with Section 13, smoked again (25 Nov 2026, a-p13; one run a block).**

- *Timeouts on the suites (amendment 28).* MBI's and MPI-BugBench's first four programs under the
  delays, MUST and Stretto at T = 30 s: the erroneous programs that hang until T (MBI's two
  CallMatching programs, MPI-BugBench's four CallOrdering ones) are revealed by the delays and by
  Stretto (observed-reported, "timeouts 2/2" and "4/4" in summary.py) and are observed-not-reported
  under MUST on MBI, which reports nothing there, as in `mbi.csv`; MUST predicts MPI-BugBench's
  four; the correct programs are missed by every tool. With the rule of Section 12 these hangs were
  tool failures.
- *MUST's warm-up first, and its processes (amendment 31, item 6 of the review).* NPB MG at 32 and
  16 ranks: each warm-up started 33 and 21 processes, its job's threads, and the MUST calibration
  runs of each configuration started after it.
- *Uintah's cells (amendment 30).* The MPICH calibration ran the four committed cells (T = 44 or
  45 s each), then a series run of each, 8 rows: the plain build at 4 ranks with 2 and with 4
  threads did not pass (the symptom, observed-reported), the plain build at 2 ranks and with the
  MPI scheduler passed, as did every run of the fixed build.
- Amendment 29's kills and the adopted containers' labels are pinned by unit tests; no container
  was killed on purpose.

## Wall time

From the smoke test's job times, perfect packing on the 36 run threads (lower bounds):

| part | hours | dominated by |
|---|---|---|
| applications, without NPB MG at 32 ranks | 37 | SuperLU_DIST's delayed runs hanging until T (10 h), Cabana's L1+SP analyses (7 h) |
| NPB MG at 32 ranks, alone | about 12 | delays (a run of the fix more than 8 minutes, 80 runs: 11 h); its 40 analyses (12 minutes each) are one-thread jobs beside the others now (amendment 14) |
| MBI | about 4 | 39 of its 115 programs hang until T in every pass of every tool: T was 60 s, the calibration gives 30 s on the smoke's programs (amendment 15); MUST with ThreadSanitizer adds 10 passes |
| MPI-BugBench | about 12 to 24 | 171 of its 388 programs hang until T, 10 passes x 6 tools, and the budget view: 24 h at 60 s, half at 30 s |
| MPI-CorrBench, both MPIs | about 15 | 77 of 513 programs hang; Stretto's passes run native, traced and guard runs (its T was 30 s already) |
| CorrBench's OpenMP programs | about 4 | the budget views under MPICH, MUST with ThreadSanitizer under both MPIs |
| thread campaigns, MUST-vc, lock replays | about 12 | SLATE's replays (6 minutes an alternative and mode on 16 threads, 160 jobs: 7 h), the traced cells with their analyses, the lock replays (34 min); PaRSEC's and DLA-Future's replays take seconds each; the calibrations and concurrency checks of amendment 20 under 1 h, except MUST-vc on Uintah (below); Uintah's series with all four committed cells (amendment 30) about 1 h for both MPIs (5 s a run) |
| MUST-vc on Uintah | about 45 | every MUST-vc run of Uintah hangs (the committed campaign killed them at its 300 s): its calibration's 20 runs reach the 1 800 s cap, so T is the cap (amendment 5), and its 40 jobs of 4 MUST-vc runs each wait 1 800 s apiece on 18 threads (7 h at the committed 300 s) |
| MPI-PRV attempts | under 1 | deal.II's instrumentation (9 min) |
| **whole campaign** | **about 145 to 155** | with packing below perfect, 6 to 8 days of the machine; the suites' share follows their calibrated T, and MUST-vc on Uintah is a third of it |

## Disk

Kept traces are small: the applications' at 40 runs per configuration and version 0.2 GB (NPB MG
included), the thread campaigns' traced cells about 0.5 GB, the suites' traced passes under 2 GB,
the alternatives' pins, MUST's reports and the outputs of failed runs well under 1 GB. Proposal:
keep every trace of every traced run, about 3 GB. The builds take about 11 GB (`third_party/apps`
10 GB, the suites 60 MB, SLATE and Uintah under `~/protocol/threads` about 1 GB), the images 2.8 GB
(`stretto-must-u`) and 0.1 GB (`stretto-uintah`).

## Open points

Sections 11 and 12 settled those of the earlier versions. Left open:

1. **MUST-vc on Uintah** under amendment 20 takes about 45 of the campaign's hours: its MUST-vc
   runs all hang, so its calibrated T is the 1 800 s cap. A decision for the rule: keep it, or
   give that cell fewer runs or a smaller cap.
2. **MUST with ThreadSanitizer under Open MPI** still timed out on one of four correct OpenMP
   programs in the earlier smoke pass over TCP; under MPICH none did. Its hangs are tool failures
   of the cells they fall in (amendment 22: a timeout on a program not labelled a deadlock).
3. **SLATE's replays diverge often** (5 of 6 in the earlier smoke, after a gate gave way): the
   counted replays will say how often; the committed campaign had 3 to 6 of 10 per mode.
4. **MUST 1.11.2 has no option that names its centralised layout** (review item 12): run.py
   takes the layout of a run without `--must:distributed` and checks mustrun's own announcement
   of it, which is as explicit as MUST allows.
