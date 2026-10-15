#!/usr/bin/env bash
# Resume the held heavy jobs, one at a time, under the shared-machine protocol, pinned away
# from the benchmark core set 4-27,60-83 (allowed: 0-3,28-59,84-111).
set -uo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"; cd "$R"
export PYTHONPATH="$R/analysis" OMPI_MCA_hwloc_base_binding_policy=none OMPI_MCA_rmaps_base_oversubscribe=1
CPUS="0-3,28-59,84-111"
log="$R/experiments/results/resume-heavy.log"
run() { echo "== $(date '+%F %T') start: $*" >> "$log"; taskset -c "$CPUS" scripts/heavy.sh "$@" >> "$log" 2>&1; echo "== $(date '+%F %T') end rc=$? : $*" >> "$log"; }
# 1. parallel job first (16 ranks): NPB overhead rows at np=16
run 8G experiments/e4_overhead.sh 16
# 2. MPI-BugBench rerun with replay confirmation (2 workers x <=4 ranks)
run 16G python3 experiments/mbi/run_mbi.py --corpus mbb --select '^P2P/(CallOrdering|MissingCall|LocalConcurrency)' --jobs 2 --timeout 40 --confirm --max-states 20000
run 8G python3 experiments/mbi/run_must.py experiments/results/mbb/mbb.csv --timeout 240
# 3. single-process analyses
run 8G python3 experiments/e2_buffering.py
run 8G python3 experiments/e6_checker_vs_explorer.py
run 12G bash -c "/usr/bin/time -f 'analysis %e s, %M KB' python3 -m rematch analyze --no-sp experiments/results/amg/trace8 > experiments/results/amg/report8.txt 2> experiments/results/amg/analyze8.err; echo done >> experiments/results/amg/analyze8.err"
echo "== $(date '+%F %T') ALL DONE" >> "$log"
