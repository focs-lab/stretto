#!/usr/bin/env bash
# Run the experiments left open after the 15 Sep stop, inside the experiment container
# (Ubuntu 24.04 with Open MPI 4.1.6, MPICH 4.2.0 and Python 3.12; see experiments/container/).
# Every step writes under experiments/results/ and logs to experiments/results/run-remaining.log.
#
# usage: experiments/run_remaining.sh [step ...]     steps: build tests mbb e2 e6 amg e4 e3
#        (no step: all of them, in that order)
set -uo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
cd "$R"
export PYTHONPATH="$R/analysis" OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_hwloc_base_binding_policy=none
JOBS="${JOBS:-6}"
log() { echo "== $(date '+%F %T') $*"; }

step_build() {
    for d in tracer replay idioms; do make -s -C "$d" MPI=openmpi || return 1; done
}
step_tests() {
    (cd analysis && python3 -m unittest discover -s tests 2>&1 | tail -3)
}
step_mbb() {
    # MPI-BugBench level 2: point-to-point and collective call-ordering, missing-call and
    # local-concurrency programs, and every correct program (for false positives).
    python3 experiments/mbi/run_mbi.py --corpus mbb \
        --select '^(P2P|COLL)/(CallOrdering|MissingCall|LocalConcurrency|Correct)' \
        --jobs "$JOBS" --timeout 40 --confirm --max-states 20000 --sp-max-events 600
}
step_e2() {
    python3 experiments/e2_buffering.py --max-events 3000
}
step_e6() {
    python3 experiments/e6_checker_vs_explorer.py --max-events 2000 --max-states 100000
}
step_amg() {
    local d=experiments/results/amg
    /usr/bin/time -f 'analysis %e s, %M KB' python3 -m rematch analyze --no-sp "$d/trace8" \
        > "$d/report8.txt" 2> "$d/analyze8.err"
    tail -1 "$d/analyze8.err"
}
step_e4() {
    # Refresh the analysis-time column of the overhead table with the current analysis code.
    python3 experiments/e4_reanalyze.py
}
step_e3() {
    python3 experiments/e3_levels.py > experiments/results/e3.md
}

steps=("$@")
[ ${#steps[@]} -eq 0 ] && steps=(build tests mbb e2 e6 amg e4 e3)
for s in "${steps[@]}"; do
    log "start $s"
    "step_$s"
    log "end $s rc=$?"
done
log "ALL DONE"
