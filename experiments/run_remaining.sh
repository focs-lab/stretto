#!/usr/bin/env bash
# The corpus run, inside the experiment container (Ubuntu 24.04 with Open MPI 4.1.6,
# MPICH 4.2.0 and Python 3.12; see experiments/container/ and RUNBOOK.md). Every step writes
# under experiments/results/ and logs to experiments/results/run-remaining.log. MUST runs are
# separate (experiments/mbi/run_must.py, experiments/amg/must.sh): they need docker on the host.
#
# usage: experiments/run_remaining.sh [step ...]     steps: build tests mbi mbb stress e2 e6 amg e4 e3 guard reps
#        (no step: all but guard and reps, in that order; guard traces the corpora again with the
#        buffer guard, reps traces MBI REPS more times)
#   JOBS     parallel programs in the corpus steps (default 6)
#   TIMEOUT  per program run and per replay, seconds (default 60); MUST gets the same
set -uo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
cd "$R"
export PYTHONPATH="$R/analysis" OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_hwloc_base_binding_policy=none
# the commit every CSV row records (experiments/provenance.py); the copy here may have no .git,
# so the sync writes it to COMMIT (RUNBOOK.md)
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || echo unknown)}"
JOBS="${JOBS:-6}"
TIMEOUT="${TIMEOUT:-60}"
log() { echo "== $(date '+%F %T') $*"; }

step_build() {
    for d in tracer replay idioms; do for mpi in openmpi mpich; do make -s -C "$d" MPI=$mpi || return 1; done; done
}
step_tests() {
    scripts/run_all.sh > experiments/results/run-all.log 2>&1
    (cd analysis && STRETTO_REQUIRE_TRACES=1 python3 -m unittest discover -s tests 2>&1 | tail -3)
}
# MBI: every wildcard, probe, tag-matching and buffering program (115). MPI-BugBench level 2:
# point-to-point and collective call-ordering, missing-call and local-concurrency programs, and
# every correct program (for false positives).
MBI_SELECT='MessageRace|ANY|Probe|Correct|ParamMatching_Tag|Buffering'
MBB_SELECT='^(P2P|COLL)/(CallOrdering|MissingCall|LocalConcurrency|Correct)'
step_mbi() {
    python3 experiments/mbi/run_mbi.py --corpus mbi --select "$MBI_SELECT" \
        --jobs "$JOBS" --timeout "$TIMEOUT" --confirm --max-states 30000 --sp-max-events 600
}
step_mbb() {
    python3 experiments/mbi/run_mbi.py --corpus mbb --select "$MBB_SELECT" \
        --jobs "$JOBS" --timeout "$TIMEOUT" --confirm --max-states 30000 --sp-max-events 600
}
step_guard() {
    # both corpora traced again with the buffer guard (into experiments/results/guard/), then
    # the guard's own checks on the real stack (experiments/guard/run.sh)
    python3 experiments/mbi/run_mbi.py --corpus mbi --guard --select "$MBI_SELECT" \
        --jobs "$JOBS" --timeout "$TIMEOUT" --max-states 30000 --sp-max-events 600 &&
    python3 experiments/mbi/run_mbi.py --corpus mbb --guard --select "$MBB_SELECT" \
        --jobs "$JOBS" --timeout "$TIMEOUT" --max-states 30000 --sp-max-events 600 &&
    REPS=3 experiments/guard/run.sh all
}
step_reps() {
    # MBI traced again REPS times (default 10), without replays: how often one traced run of
    # each program shows or predicts its error. Tables in experiments/results/reps/mbi-<k>.csv.
    for k in $(seq 1 "${REPS:-10}"); do
        python3 experiments/mbi/run_mbi.py --corpus mbi --select "$MBI_SELECT" --jobs "$JOBS" --timeout "$TIMEOUT" \
            --max-states 30000 --sp-max-events 600 \
            --out "experiments/results/reps/mbi-$k.csv" --work "experiments/results/reps/mbi-$k" || return 1
    done
}
step_stress() {
    # the baseline: up to 30 runs per program with random delays, until one does not finish
    python3 experiments/stress_baseline.py experiments/results/mbi/mbi.csv --runs 30 --jobs "$JOBS" --timeout "$TIMEOUT"
    python3 experiments/stress_baseline.py experiments/results/mbb/mbb.csv --runs 30 --jobs "$JOBS" --timeout "$TIMEOUT" \
        --select '^(P2P|COLL)/(CallOrdering|Correct)'
}
step_e2() {
    python3 experiments/b_impl.py
    python3 experiments/e2_buffering.py --max-events 3000 --replay --timeout "$TIMEOUT"
}
step_e6() {
    python3 experiments/e6_checker_vs_explorer.py --max-events 2000 --max-states 100000 --synthetic 200
}
step_amg() {
    experiments/amg/run.sh all
}
step_e4() {
    # Refresh the analysis-time column of the overhead table with the current analysis code.
    python3 experiments/e4_reanalyze.py
}
step_e3() {
    PYTHONPATH="$R/analysis:$R/analysis/tests" python3 experiments/e3_levels.py > experiments/results/e3.md
    python3 experiments/e7_mbi_twins.py > /dev/null
}

steps=("$@")
[ ${#steps[@]} -eq 0 ] && steps=(build tests mbi mbb stress e2 e6 amg e4 e3)
for s in "${steps[@]}"; do
    log "start $s"
    "step_$s"
    log "end $s rc=$?"
done
log "ALL DONE"
