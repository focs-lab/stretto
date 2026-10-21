# Stretto

(The project was called Rematch until 21 Oct 2026; results committed before then use that name.)

Sound prediction of alternative message matchings from one MPI run. WP0 prototype for the
proposal in `docs/proposal_EN.md`; the critique that shaped it is `docs/analysis.md`, the
working formal core is `docs/model.md`.

Pipeline: record one run with a PMPI tracer → reconstruct the observed matching → enumerate
single-change alternatives at wildcard receives → keep the ones that are feasible (horizon
closure + matching order + acyclicity, polynomial) → classify them (count mismatch,
cross-purpose, orphan, deadlock, benign) → confirm by forced replay.

## Layout

```
docs/       proposal, analysis, model, related-work checklist, WP0 gate report
idioms/     thirteen small MPI programs with the expected prediction in each README
tracer/     PMPI tracer -> traces/<mpi>/<idiom>/rank-*.jsonl
analysis/   Python package `stretto` (stdlib only) + tests
replay/     pin library: force wildcard sources, inject delays
study/      WP0a measurement: proxy-app wildcard sites and their dependence class
scripts/    run_idiom.sh, run_all.sh, replay_idiom.sh, must_run.sh, heavy.sh
experiments/ corpus harness (MBI, MPI-BugBench), E2-E7 scripts, the applications, container, results
```

## Requirements

System toolchain only: `/usr/bin/mpicc.openmpi` or `/usr/bin/mpicc.mpich` (gcc underneath),
Python 3.10+. No pip packages. Select the implementation with `MPI=openmpi` (default) or
`MPI=mpich`.

## Run

```bash
scripts/run_all.sh                       # trace + analyse all idioms
scripts/run_idiom.sh 05                  # one idiom
MPI=mpich scripts/run_idiom.sh 05        # same on MPICH
scripts/replay_idiom.sh 05 0:0:2         # pin rank 0's first wildcard to source 2
PYTHONPATH=analysis python3 -m stretto analyze -v traces/openmpi/09_two_wildcards_chain
PYTHONPATH=analysis python3 -m stretto confirm traces/openmpi/05_count_mismatch idioms/05_count_mismatch/bin/openmpi/main
PYTHONPATH=analysis python3 -m stretto oracle traces/openmpi/04_orphan_send
make -C tracer && make -C replay                                # the tracer and the replay library (system MPI)
cd analysis && python3 -m unittest discover -s tests          # the suite incl. the differential tests (STRETTO_REQUIRE_TRACES=1 after run_all.sh)
```

Experiments (see `docs/results.md`, `docs/gate.md`, `docs/mvp.md`; the two-machine procedure
and the shared-host rules are in `experiments/RUNBOOK.md`):

```bash
python3 experiments/mbi/run_mbi.py --corpus mbi --select 'MessageRace_|ParamMatching_Tag|CallOrdering_Probe|Buffering' --confirm
python3 experiments/mbi/run_must.py experiments/results/mbi/mbi.csv      # MUST 1.9.2 in the MPI-BugBench container
python3 experiments/mbi/score.py experiments/results/mbi/mbi.csv
python3 experiments/e2_buffering.py; python3 experiments/e3_levels.py; python3 experiments/e6_checker_vs_explorer.py
experiments/e4_overhead.sh 4 16                                          # NPB 3.4.3 (MPICH build)
python3 experiments/tables.py                                            # regenerate docs/results.md
```

Third-party pieces (git-ignored, fetched by scripts): MUST (1.11.2 built by `third_party/build_must.sh`
hangs under this host's Open MPI, so every result uses MUST 1.9.2 in the MPI-BugBench container
built from `third_party/mpi-bugbench/Dockerfile.must`), MPI-BugBench, NPB 3.4.3, AMG, MBI, MPI-CorrBench.

Pin syntax: `STRETTO_PIN="rank:idx:src[:tag],..."` pins the idx-th wildcard receive of a rank;
`STRETTO_PIN_PROBE` does the same for wildcard probes. `STRETTO_PIN=@file` reads the list from a
file, for horizons too long for one environment string (`run_pinned` does this past 16 KiB).
`STRETTO_SEND_DELAY_MS="rank:ms"` and `STRETTO_RECV_DELAY_MS` skew timing.

## Report classes

| class | meaning | level |
|---|---|---|
| 1a | alternative message does not fit the receive (count/type) | L1, trace only |
| 1b | alternative message is from another logical channel (tag) | L1, trace only |
| 1c | a send is left unmatched | L1+SP (single-path assumption) |
| 1c | a send was never received in the observed run | observed |
| 1d | deadlock under the alternative | L1+SP |
| 1e | the twin's continuation diverges from the trace, or the twin aborts on the program's own check | L2 (hand-written twin) |
| 1f | same shape as observed; benign unless the continuation depends on it | L1 |
| 2-buffering | the observed run itself deadlocks below an eager threshold | observed |
| waitany-order | another request could have completed first | L1 |
| probe-alt | a wildcard probe could have observed another message | L1 |
| 2-collective | ranks disagree on a collective (name or participation) | observed |
| 2-blocked | the run ended with a rank blocked in a call or spinning in a polling loop | observed |

"L1+SP" findings assume the recorded call sequence does not change: unsound on
metadata-dependent code (idioms 01 and 06 show it), which is what L2 is for.

## License

MIT; see `LICENSE`.
