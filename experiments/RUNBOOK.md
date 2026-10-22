# Experiment runbook

## Inputs

`third_party/fetch.sh all` clones MBI, MPI-BugBench, AMG, MACSio and Nekbone at the commits
the results refer to, generates the MBI programs and the MPI-BugBench level-2 programs, builds
AMG with the system Open MPI wrapper, downloads NPB 3.4.3 and builds its kernels twice (Open
MPI for the plain reference, MPICH for the traced run, since Open MPI's Fortran bindings bypass
C interposition), and builds the MUST container image from MPI-BugBench's Dockerfile. Each part
can be fetched alone (`corpora`, `apps`, `npb`, `must`). Everything it writes is git-ignored.
Every part but `must`, which needs docker, also runs inside the experiment image
(`artifact/reproduce.sh inputs`). The image has the tools the generators need, among them the
clang-format that formatted the paper's MPI-BugBench programs.

## Steps

`experiments/run_remaining.sh [step ...]` runs, inside the container, `build tests mbi mbb e2 e6
amg e4 e3`: the tracer and replay libraries for both MPI implementations and the idioms; the
unit tests with `STRETTO_REQUIRE_TRACES=1` after tracing the idioms; the two corpora with
replays (`experiments/mbi/run_mbi.py --confirm`, one row per finding in `replays.csv`); the
eager-threshold measurement and the buffering ablation with its forced-rendezvous replays
(`experiments/b_impl.py`, `experiments/e2_buffering.py --replay`); the checker-versus-explorer
comparison on the corpus traces and on synthetic ones (`experiments/e6_checker_vs_explorer.py
--synthetic`); AMG traced, analysed and replayed (`experiments/amg/run.sh`); the E4 analysis
times; the level table. MUST runs on the host, where docker is: `experiments/mbi/run_must.py
<corpus>.csv --timeout <same as the run>` for the corpora and `experiments/amg/must.sh` for
AMG. `experiments/tables.py` then writes `docs/results.md`.

The buffer guard (local concurrency, class 3-local) is opt-in and outside that sequence:
`experiments/mbi/run_mbi.py --guard` (or `experiments/run_remaining.sh guard`) traces a corpus
with it into `experiments/results/guard/<corpus>.csv`, and `experiments/guard/run.sh` checks it on the real stack (cases with known
answers, MPI-BugBench's LocalConcurrency programs, NPB LU, HYPRE and AMG with the guard off and
on), once in the default container and once with `DOCKER_ARGS="--cap-add SYS_PTRACE"`, which
lets the MPI libraries use single-copy transfers. Its tables are in `experiments/results/guard/`;
`experiments/tables.py` adds the corpus rows with the guard to E1 and the checks to E8.

Read bounds (rule H1', `docs/firstread.md`, E3b) are outside that sequence too. The image of
`experiments/container/Dockerfile.firstread` (the experiment image plus clang 18 from the same
archive snapshot) runs `experiments/mbi/reads_maps.py experiments/results/mbi/mbi.csv`, which
writes the first-read map of every MBI program to `experiments/results/reads/mbi/`. The
experiment image then reanalyses the corpus run with them (`experiments/mbi/reanalyze.py
experiments/results/mbi/mbi.csv --reads experiments/results/reads/mbi --out
experiments/results/reads/mbi.csv`), replays the programs whose levels change with the witness
pins (`run_mbi.py --replay-only --reads ... --out <a copy of that table>`, whose `replays.csv`
becomes `experiments/results/reads/replays.csv`), and compares rule H1' with the explorer
(`run_remaining.sh e6reads`, `experiments/results/e6_reads_{never,random}.csv`).

Variables the scripts read (all optional):

| variable | script | meaning (default) |
|---|---|---|
| `JOBS` | `run_remaining.sh` | parallel programs (6) |
| `TIMEOUT` | `run_remaining.sh`; `scripts/env.sh` | seconds per run, replay and MUST run (60 in the corpus harness; 15 for a bare `mpi_run`) |
| `MPI` | `scripts/*.sh`, Makefiles | `openmpi` or `mpich`: which wrapper, library and idiom binaries |
| `STRETTO_TRACE_DIR` | tracer | where the ranks write `rank-N.jsonl` (`./traces`) |
| `STRETTO_PAYLOAD` | tracer; `scripts/run_idiom.sh`, `experiments/guard/run.sh` | record message contents up to this many packed bytes per buffer (off); level L2 by re-execution needs them |
| `STRETTO_GUARD` | tracer; `scripts/run_idiom.sh` | `1`: the buffer guard, local-concurrency findings (class 3-local); `experiments/mbi/run_mbi.py --guard` sets it (off) |
| `STRETTO_PIN`, `STRETTO_PIN_PROBE` | replay library | `rank:ordinal:world-src[:tag],...` for wildcard receives and probes (`replay/pin.h`) |
| `STRETTO_SEND_DELAY_MS`, `STRETTO_RECV_DELAY_MS`, `STRETTO_DELAY_AT` | replay library | sleeps before sends/receives, per rank or at the k-th one (`replay/pin.h`) |
| `STRETTO_REQUIRE_TRACES` | tests | `1`: idiom tests fail instead of skipping without traces (Open MPI idioms fall back to `analysis/tests/golden`) |
| `STRETTO_COMMIT` | experiment scripts | the code commit written into every CSV row; unset, the `COMMIT` file at the root is read, else git is asked (`experiments/provenance.py`) |
| `MUST_IMAGE`, `MUST_CPUS`, `MUST_KEEP` | `scripts/must_run.sh`, `experiments/amg/must.sh` | image (`mbb-must:latest`), cpuset, keep the work directory |
| `MUST_ARGS` | `experiments/amg/must.sh` | MUST layout (default `--must:distributed`; empty for the centralised one) |
| `STRETTO_MACHINE_LOCK`, `STRETTO_CPUS` | `scripts/heavy.sh` | the shared-host lock file and the processors a heavy job may use |
| `IMAGE`, `CPUS`, `MEMORY`, `NAME`, `MACHINE`, `DETACH` | `experiments/container/run.sh` | container image, cpuset (`0-11,40-51`), memory (`24g`), name, the host name the summaries print (the host's), `0` to run in the foreground (detached) |
| `DOCKER_ARGS` | `experiments/container/run.sh` | more `docker run` options; `--cap-add SYS_PTRACE` lets the MPI libraries use single-copy transfers (`experiments/guard/run.sh`) |
| `CPUS`, `MEMORY`, `JOBS`, `NEKBONE` | `artifact/reproduce.sh` | as above, but `CPUS` defaults to every processor; `NEKBONE=0` leaves Nekbone out |
| `BENCHMARKS`, `CLASSES`, `CSV` | `experiments/e4_overhead.sh` | which NPB rows to measure and which table to write |
| `DOCKER_BUILD_ARGS` | `third_party/fetch.sh must` | e.g. `--cgroup-parent=docker.slice` on the development host |

## Where experiments run

The development host is shared and reserved for another project's benchmark legs, so the
experiments that execute MPI programs at scale run on a second machine inside a container
with the development host's userland (`experiments/container/Dockerfile`: Ubuntu 24.04,
Open MPI 4.1.6, MPICH 4.2.0, gcc 13, Python 3.12). Traces, replays and analysis are therefore
comparable across the two. The second machine is shared as well: use it for runs and
analysis, and for timing claims only inside an announced timed leg (below).

The paper's runs used another image with the same packages. Since 21 Oct 2026 the Dockerfile
pins its base by digest and its packages by an Ubuntu archive snapshot, and
`experiments/container/check.sh` compares a built image with `versions.txt`. The package
versions it shares with that image are identical. `artifact/reproduce.sh` drives the whole
evaluation through this image (`artifact/README.md`).

The copy goes to `$DIR` on the second machine, a directory on disk. A tmpfs such as
`/dev/shm` is faster but not durable there: logind's `RemoveIPC=yes` (the default) deletes
the files a user owns in `/dev/shm` when that user's last login session ends, so a copy made
over ssh on a freshly booted machine is gone before the next ssh command.

```bash
# on the development host: copy the repository and the inputs to $DIR on the second machine
# (a relative DIR, for example stretto, is taken from the remote home directory)
git ls-files > /tmp/files.txt
rsync -a --files-from=/tmp/files.txt . $REMOTE:$DIR/
git rev-parse --short HEAD | ssh $REMOTE "cat > $DIR/COMMIT"   # the commit every CSV row records
rsync -a --relative --exclude bin/ study/apps/MBI/gencodes third_party/mpi-bugbench/scripts/gencodes $REMOTE:$DIR/
rsync -a --relative study/apps/AMG/test/amg third_party/apps/NPB-mpich/bin tracer/bin replay/bin $REMOTE:$DIR/
rsync -a --relative study/apps/Nekbone $REMOTE:$DIR/      # sources: nekbone/run.sh build runs inside the container
rsync -a --relative experiments/results/e4/e4.csv experiments/results/e4/trace.* $REMOTE:$DIR/

# on the second machine: build, test, then every remaining step, detached
cd $DIR
IMAGE=stretto-exp:24.04 experiments/container/run.sh bash -c \
  "experiments/run_remaining.sh > experiments/results/run-remaining.log 2>&1"

# MUST baseline, on the second machine's host (its own container, MUST 1.9.2 + MPICH), the
# run's timeout; and MUST on AMG
for c in mbi mbb; do MUST_CPUS=0-11,40-51 python3 experiments/mbi/run_must.py experiments/results/$c/$c.csv --jobs 3 --timeout 60; done
MUST_CPUS=0-11,40-51 experiments/amg/must.sh 1800
# MUST's distributed layout (the default, as on the corpora) crashed inside its wait-state
# module on AMG; MUST_ARGS= (empty) runs the centralised layout instead
MUST_ARGS= MUST_CPUS=0-11,40-51 experiments/amg/must.sh 1800

# back on the development host: fetch the tables and regenerate docs/results.md
rsync -a $REMOTE:$DIR/experiments/results/ experiments/results/
python3 experiments/tables.py
```

The MUST image is MPI-BugBench's (`third_party/mpi-bugbench/Dockerfile.must`, built by
`third_party/fetch.sh must`); it can be copied between machines with `docker save
mbb-must:latest | gzip -1 | ssh $REMOTE 'gunzip | docker load'`. Any `docker build` on the
development host needs `--cgroup-parent=docker.slice` (`DOCKER_BUILD_ARGS`).

## Timed leg

`experiments/timing_leg.sh`, inside the container on the second machine, measures the E4
plain, traced and analysis times (`e4_overhead.sh`, `REPS` repetitions of the whole set,
default 5, with NPB's own timer next to the wall time) and the AMG analysis (`amg/run.sh
analyse`, tracing AMG first if its trace is missing), then runs the AMG analysis twice more
for the log and marks the summary as written by the leg. The leg of 20 Oct (UTC) took 25
minutes on `CPUS=0-11,40-51`. The whole host is idle for it: the
leg is announced to the other lanes with a START and an END message, nothing of theirs runs
in between, and anything that did run is written into `experiments/results/timing-leg.log`
next to the rows it may have touched (which are then rerun inside the window). `MACHINE=<name>`
on `container/run.sh` sets the host name the summaries print.

## Second application: Nekbone

`experiments/nekbone/run.sh [build|trace|analyse|summary|sample|all]` builds Nekbone's
communication suite (`test/nek_comm`, `PPLIST=NEKCOMM`, the build that reaches the
`MPI_ANY_SOURCE` wrappers of `comm_mpi.f`) with the MPICH wrappers, traces it at 8 ranks,
analyses the trace and replays a sample of 30 alternatives with controls
(`experiments/replay_sample.py`, which analyses the trace again);
`experiments/results/nekbone/{summary8.txt,replay_sample.csv}` are committed. `amg/run.sh
sample` does the same for AMG in a few minutes. The trace is
10 million records (1.1 GB) and the analysis needs about 33 GB and an hour in one process:
run it in a container with a memory cap of at least 40 GB (`MEMORY=48g` on
`container/run.sh`). A `docker run
--rm` container killed by its cap leaves empty outputs and exit code 137 with nothing in
`docker events` beyond create and start; sample `docker stats` into a file to see the peak.
MACSio's one wildcard site lies in `src-stuff/`, outside the built program, so MACSio is not
traced.

## Real bug: HYPRE

`experiments/hypre/run.sh` fetches HYPRE 2.10.1 and 2.11.0 (sha256-checked) and builds both
with MPICH; `HYPRE_DIR` points the scripts at that directory (on the second machine
`~/rematch-hypre`, mounted as `/h`). `experiments/hypre/campaign.sh [predict|stress|all]`
traces `RUNS` runs of `test/ij -solver 8 -pout 0` at 4 and 8 ranks, replays every
request-probe alternative with its horizon pins and control (`JOBS` at a time; the 2.10.1
campaign took about four hours at `JOBS=4`, most of it in the 60 s timeouts of hanging
replays), and runs the untraced stress baseline; `HYPRE_VERSION=2.11.0` runs the same on the
fix (`predict-2.11.0.csv`). Replays or controls that neither finish nor time out leave their
output under `experiments/results/hypre/failed/`. Keep a few processors of the node outside a
long campaign's `--cpuset-cpus` for short jobs (tables, LaTeX): two bursts of runs that exited
with status 15 coincided with such jobs on the campaign's own processors.

## Third documented bug: deal.II

`experiments/dealii/run.sh [fetch|build|predict|stress|all]` fetches deal.II 9.3.0-rc1 (the
race in `Utilities::MPI::RemotePointEvaluation`) and 9.3.0 (the fix), p4est 2.2 and CMake
3.28.3, which the image lacks, each checked by sha256. The deal.II archives leave `tests/` out,
so the tests of RemotePointEvaluation and their expected outputs come one by one from the two
tagged commits. The build step builds p4est and both deal.II versions with the MPICH wrappers
in Release, `-j8`, about 25 minutes each in a 24 GB container, then the tests as programs;
`predict` traces `RUNS` runs of `mapping_01` per version at 4 ranks and replays every probe
alternative on RemotePointEvaluation's tag with its horizon pins and control, `JOBS` at a
time; `stress` runs the untraced baseline and `changes` locates the values that changed. A
test's log is compared with its expected output after deal.II's own
`cmake/scripts/normalize.pl`, which removes the line naming host and date. `DEALII_DIR`
(default `third_party/apps/dealii`) is where the sources and builds go. Each run of an MPICH
program under Open MPI's `mpirun` spends about 4 s idle in start-up in the image (an MPICH
hello world takes 4.4 s), so the campaign of 21 Oct ran `RUNS=20 JOBS=8 predict` (about 3.5
minutes per trace of 9.3.0-rc1, 2 per trace of 9.3.0) and `RUNS=50 stress`, two hours in all.

## Happens-before selection against the check (E8b)

`experiments/e8_hb.py [trace|compare|replay|all]`, inside the container. `trace` traces the
applications it compares into `experiments/results/e8/` with the binaries the application
scripts build (AMG at 8 ranks; HYPRE 2.10.1 at 4 and 8 ranks under `HYPRE_DIR`; NPB MG 2.4.1
class S at 16 and 32 ranks under `NPB_MG_DIR`). `compare` judges every pair (wildcard receive
or probe, send) by the two happens-before rules of `analysis/stretto/hb.py` and by the check,
on the corpus traces of the latest run (`experiments/results/{mbi,mbb}/<program>/trace`), the
idioms (`traces/openmpi`) and those applications, and with `--synthetic N` on random programs
that the explorer decides; it writes `e8_hb_counts.csv` (pairs per program, kind of receive and
verdicts), `e8_hb.csv` (one row per disagreement) and `e8_hb_traces.csv`. MG at 32 ranks takes
about 20 minutes of it, in the generic check of nonblocking alternatives. `replay` replays up
to `--sample` disagreements per program and class with the tracer preloaded: it applies the
pins and records each replay, whose trace tells a run of the program from one that only the
pins make possible. A replay that hangs waits out `--timeout` (corpus programs) or
`--app-timeout` (applications). The committed tables come from `e8_hb.py all --synthetic 100
--max-states 100000 --jobs 2` in a container on 12 processors; at the explorer's default cap
the generator keeps larger programs but takes a minute or more for each.

## Baselines: NINJA and MUST 1.11.2

`experiments/ninja/build.sh` (inside the container) clones NINJA v1.0.2 into `third_party/ninja`
and builds its library for both MPI implementations, as shipped and with the two repairs the
runs use (random noise, Fortran programs; the script says why), and NINJA's ParaSails replica.
`experiments/ninja/campaign.sh [calibrate|hypre|mbi|mg]` then runs the replica, HYPRE (mount the
HYPRE builds and set `HYPRE_DIR`, as for the HYPRE campaign), the MBI corpus (its binaries under
`experiments/results/mbi/*/main`) and NPB MG 2.4.1 (`NPB_MG_DIR`) under each mode, one run at a
time except the corpus (`JOBS`), into `experiments/results/ninja/`. The campaign of 21 Oct
2026 ran on `CPUS=20-25,60-65` (borrowed until 21:19 UTC) at commit 3b8e846: `RUNS=10
campaign.sh calibrate`, then `hypre` for 2.10.1 at 4 ranks; the rest was cut to fit the window
and made the same `run.py` calls `campaign.sh` makes, without random noise of 1.2 s: HYPRE 2.10.1
at 8 ranks and 2.11.0 at 4 and 8, MG at 16 ranks, then the MBI corpus in the application-centric,
random (50 ms) and system-centric modes. One 8-rank run of 2.10.1 that the first invocation
recorded before it was stopped (it finished) is not in the table, so that each configuration
has 40 runs.

`experiments/must/build.sh` builds the MUST 1.11.2 image (`stretto-must:1.11.2`) from MUST's
release archive in `third_party/must/`, checked by sha256. The two runs per corpus, on the host:

```bash
export MUST_IMAGE=stretto-must:1.11.2 MUST_PREFIX=/MBB-builds/MUST1112 MUST_CACHE=$HOME/must-cache MUST_CPUS=...
for c in mbi mbb; do
  python3 experiments/mbi/run_must.py --jobs 3 --timeout 60 --column must1112 experiments/results/$c/$c.csv
  MUST_TSAN=1 python3 experiments/mbi/run_must.py --jobs 3 --timeout 60 --column must1112tsan experiments/results/$c/$c.csv
done
```

MUST builds the ThreadSanitizer tool layouts at run time into `MUST_CACHE`; run one program
per process count with `MUST_TSAN=1` first, so that parallel runs do not build the same layout.
The four runs of 21 Oct 2026 (UTC) took 12 minutes (MBI, plain), 46 (MPI-BugBench, plain), 16
(MBI with ThreadSanitizer) and 80 (MPI-BugBench with ThreadSanitizer) on `CPUS=20-25,60-65`, from
the copy deployed at commit 3b8e846; their tables `<corpus>/must1112*.csv` were then merged
(`run_must.py --merge-only --column ...`) into the corpus tables of the current commit.

## Shared-host protocol on the development host

Jobs that may exceed 20 GiB or use 8 sustained cores run one at a time through
`scripts/heavy.sh <cap> <cmd>`, which takes the machine lock and a MemoryMax scope (never
MemoryHigh: the host has no swap) and pins to the processors outside the benchmark and
operating-system sets. Nothing runs during an announced timed benchmark leg.

## Notes

- The NPB Fortran kernels are traced only when built with the MPICH wrappers: Open MPI's
  Fortran bindings call `PMPI_*` directly. A tree first built against Open MPI must be
  cleaned completely (`make veryclean` also removes `config/make.def`, which then has to be
  rewritten).
- Busy-polling loops (`MPI_Iprobe`, `MPI_Test` until a flag is set) are summarised by the
  tracer; before that, single MPI-BugBench programs produced 3 GB traces.
- Open MPI's `mpirun` merges the ranks' stderr in chunks, so a pin's report line can be split
  by another rank's line, and a replay then counts a pin as not taken that was. Of the deal.II
  replays of ten further traces made with their stderr kept (21 Oct), five had pins counted
  as not taken; all nine of those reports were in the output, cut apart by another rank's
  line.
