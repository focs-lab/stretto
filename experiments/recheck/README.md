# Recheck after the November fixes

Five changes of November can move verdicts that committed results were built from:

| change | commit | acts on |
|---|---|---|
| orientation against a concurrent receive the sender order cannot decide | 1544c79 | ranks with several chains |
| every threshold judged where B-monotonicity fails | 56b8a47 | ranks with several chains |
| no observed finding from the chain beyond the tracer's chain table | 43c4c09 | traces whose chain table overflowed |
| payloads of 2 GiB or more sized in `MPI_Count` | 1496d9b | the tracer with `STRETTO_PAYLOAD` |
| a freed pending receive takes the head of its channel | 385f919 | receives freed while pending |

Every kept trace was analysed by the analysis before them (6fd7a06, `base`) and after
(`recheck-nov`, `new`), with [reanalyse.py](reanalyse.py); campaigns whose traces were not kept
were traced again with the new code, keeping their traces, and those traces were analysed by both
versions too, so that a changed verdict can be told from a different run. A trace without a
rank of several chains and without a freed receive cannot change (every change above acts on
one of the two); such a trace was skipped once loaded, except for one in 50 analysed anyway.
[summary.py](summary.py) prints the per-campaign counts.

| traces | where | compared | reports that differ | cause |
|---|---|---|---|---|
| 16 939 kept by the frozen rerun (MBI 213, MBB 388, deal.II 10 598, NPB MG 4 820, AMG, Nekbone, the replays of E8c and the read bounds, E8b, idioms) | [frozen.csv](results/frozen.csv), [frozen-large.csv](results/frozen-large.csv) | 1 093, all of MBI and MBB among them; none has several chains or a freed receive | 0 | - |
| MUST-vc's suite and Stretto's idioms with threads, traced again (`stretto_bench.sh`, 418 traces) | [mustvc-suite.csv](results/mustvc-suite.csv) | 418 | 12, all traces whose chain table overflowed | 43c4c09 |
| MPI-CorrBench's 103 OpenMP programs, traced again (412 traces) | [mustvc-corrbench.csv](results/mustvc-corrbench.csv) | 412 | 3, `nondeterministic_deadlock`, whose chain table overflows | 43c4c09 |
| PaRSEC's atomics campaign, traced again (20 plain traces) | [parsec-atomics.csv](results/parsec-atomics.csv) | 20 | 0 | - |
| DLA-Future's atomics campaign, traced again (50 traces) | [dlaf-atomics.csv](results/dlaf-atomics.csv) | 50 | 0 | - |
| 1 285 thread traces kept in the work directories of the thread milestones (gates, idioms, CorrBench's ordering programs, MPICH's `mt_*`), the blind scans of GA, Smilei and Athena++, the HDF5 and Uintah runs, the PaRSEC and DLA-Future hunts and the SLATE leads | [threads.csv](results/threads.csv) | 635 (610 can change) | 0 among those both versions finish; 16 not compared, both versions exceeding 3 hours (GA's 12 `threadsafec` and MPICH's 4 `mt_*` traces at 1 024 iterations) | - |
| GA's 12 `testmult` traces again with 6 hours per analysis | [testmult.csv](results/testmult.csv) | 12 | 0; the new analysis takes 3.1 to 4.2 hours against 24 to 34 minutes | 56b8a47 judges every threshold of a trace with several chains (cost only) |

Left out: the configuration sweep's 2 131 kept traces, re-analysed with the thread fixes when
the sweep was merged (experiments/sweep, `sweep-fixed.csv`), and the SLATE leads' archives traced
with 4 096 chains, re-analysed with 43c4c09 there (experiments/slate-leads), whose 1.8 GB each
the disk could not hold here; the campaigns whose traces were deleted and not traced again
(Uintah, MADNESS, StarPU-MPI, the SLATE cells of section 15.4), whose findings are observed
(Uintah) or predicted on ranks with several chains (MADNESS, StarPU-MPI) and stay to be
checked.

The campaigns traced again give new tables beside the committed ones
(`experiments/mustvc/stretto-*-recheck.csv`, `experiments/parsec/traced-atomics-recheck.csv`,
`experiments/dlaf/traced-atomics-recheck.csv`); where their counts differ from the committed
ones without a differing report on the same trace, the runs differ, not the analysis.

Commands (a-p13, a copy of this branch at `~/recheck/repo` with its commit in `COMMIT`, the
analysis of 6fd7a06 at `~/recheck/base-pre/analysis`):

```
D="docker run --rm --ulimit core=0 --user $(id -u):$(id -g) -e HOME=/tmp -e STRETTO_COMMIT=$(cat ~/recheck/repo/COMMIT) -v ~/recheck:/rc -w /rc/repo"
$D -v ~/stretto-freeze/repo/artifact/out/traces:/traces:ro stretto-exp:24.04 python3 experiments/recheck/reanalyse.py \
  --manifest /traces/MANIFEST.tsv --base /rc/base-pre/analysis --out /rc/out/frozen --jobs 12 \
  --skip-names '^trace8$' --unaffected-only-sampled 50 --scratch /rc/scratch --timeout 7200
# the same with --campaigns amg,nekbone --skip-names 'replay|control' --jobs 1 for the two large traces;
# kept trace directories: --list FILE --root DIR in place of --manifest
```
