#!/usr/bin/env bash
# The corpus run, inside the experiment container (Ubuntu 24.04 with Open MPI 4.1.6,
# MPICH 4.2.0 and Python 3.12; see experiments/container/ and RUNBOOK.md). Every step writes
# under experiments/results/ and logs to experiments/results/run-remaining.log. MUST runs are
# separate (experiments/mbi/run_must.py, experiments/amg/must.sh): they need docker on the host.
#
# usage: experiments/run_remaining.sh [step ...]     steps: build tests mbi mbb stress e2 e6 amg e4 e3 guard reps e6reads stresseager stressreps
#        (no step: all but guard, reps, e6reads, stresseager and stressreps, in that order; guard
#        traces the corpora again with the buffer guard, reps traces MBI REPS more times, e6reads
#        runs E6 with read bounds, stresseager runs the stress baseline with the eager threshold
#        forced down, stressreps runs the stress baseline on MBI REPS more times)
# Exit status: 0 if every step succeeded, 1 if one failed (the others still run, but a failed
# build stops the script), 2 for an unknown step.
#   JOBS     parallel programs in the corpus steps (default 6)
#   TIMEOUT  per program run and per replay, seconds (default 60); MUST gets the same
#   REPS     passes of the reps and stressreps steps (default 10); REPS_FIRST, stressreps's first
#            pass (default 1), lets two copies of that step share the passes
set -uo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
cd "$R"
export PYTHONPATH="$R/analysis" OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_hwloc_base_binding_policy=none
# the commit every CSV row records (experiments/provenance.py: git, or the COMMIT and
# COMMIT.files the sync writes into a copy without .git; "-dirty" when the code differs)
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
JOBS="${JOBS:-6}"
TIMEOUT="${TIMEOUT:-60}"
log() { echo "== $(date '+%F %T') $*"; }

step_build() {
    for d in tracer replay idioms; do for mpi in openmpi mpich; do make -s -C "$d" MPI=$mpi || return 1; done; done
}
step_tests() {
    # the idioms traced (their traces are inputs of the tests), then the unit tests; the status is
    # the tests' own, and a failed idiom fails the step too
    local rc=0
    scripts/run_all.sh > experiments/results/run-all.log 2>&1 || { log "tracing the idioms failed (experiments/results/run-all.log)"; rc=1; }
    (cd analysis && STRETTO_REQUIRE_TRACES=1 python3 -m unittest discover -s tests > ../experiments/results/unit-tests.log 2>&1)
    local t=$?
    tail -3 experiments/results/unit-tests.log
    [ $t = 0 ] || { log "unit tests failed (experiments/results/unit-tests.log)"; rc=1; }
    return $rc
}
# The corpus steps select each corpus's programs by run_mbi.py's default, the campaign's
# selection (experiments/selection.py): MBI's wildcard, probe, tag-matching and buffering
# programs (115); MPI-BugBench level 2's point-to-point and collective call-ordering,
# missing-call and local-concurrency programs, and every correct program (for false positives).
step_mbi() {
    python3 experiments/mbi/run_mbi.py --corpus mbi \
        --jobs "$JOBS" --timeout "$TIMEOUT" --confirm --max-states 30000 --sp-max-events 600
}
step_mbb() {
    python3 experiments/mbi/run_mbi.py --corpus mbb \
        --jobs "$JOBS" --timeout "$TIMEOUT" --confirm --max-states 30000 --sp-max-events 600
}
step_guard() {
    # both corpora traced again with the buffer guard (into experiments/results/guard/), then
    # the guard's own checks on the real stack (experiments/guard/run.sh)
    python3 experiments/mbi/run_mbi.py --corpus mbi --guard \
        --jobs "$JOBS" --timeout "$TIMEOUT" --max-states 30000 --sp-max-events 600 &&
    python3 experiments/mbi/run_mbi.py --corpus mbb --guard \
        --jobs "$JOBS" --timeout "$TIMEOUT" --max-states 30000 --sp-max-events 600 &&
    REPS=3 experiments/guard/run.sh all
}
step_reps() {
    # MBI traced again REPS times (default 10), without replays: how often one traced run of
    # each program shows or predicts its error. Tables in experiments/results/reps/mbi-<k>.csv.
    for k in $(seq 1 "${REPS:-10}"); do
        python3 experiments/mbi/run_mbi.py --corpus mbi --jobs "$JOBS" --timeout "$TIMEOUT" \
            --max-states 30000 --sp-max-events 600 \
            --out "experiments/results/reps/mbi-$k.csv" --work "experiments/results/reps/mbi-$k" || return 1
    done
}
# the stress baseline's MPI-BugBench programs: the call-ordering and correct ones (a pattern
# selects by table name or path alike, experiments/selection.py)
MBB_STRESS_SELECT='^(P2P|COLL)/(CallOrdering|Correct)'
step_stress() {
    # the baseline: up to 30 runs per program with random delays, until one does not finish
    python3 experiments/stress_baseline.py experiments/results/mbi/mbi.csv --runs 30 --jobs "$JOBS" --timeout "$TIMEOUT" &&
    python3 experiments/stress_baseline.py experiments/results/mbb/mbb.csv --runs 30 --jobs "$JOBS" --timeout "$TIMEOUT" \
        --select "$MBB_STRESS_SELECT"
}
step_stressreps() {
    # the stress baseline on MBI again REPS times, at the paper's settings (30 runs, the default
    # limits, the same timeout and programs), each pass with delays from its own seed (the
    # single pass, stress.csv, has stress_baseline.py's default, 20261020): a program whose
    # outcome under stress depends on the schedule makes one pass a single sample. Tables in
    # experiments/results/reps/stress-mbi-<k>.csv; the MBI binaries come from the mbi step.
    for k in $(seq "${REPS_FIRST:-1}" "${REPS:-10}"); do
        python3 experiments/stress_baseline.py experiments/results/mbi/mbi.csv --runs 30 --jobs "$JOBS" --timeout "$TIMEOUT" \
            --seed $((20261020 + k)) --out "experiments/results/reps/stress-mbi-$k.csv" || return 1
    done
}
step_stresseager() {
    # the baseline again with no payload byte buffered (stress_baseline.py --eager 0: E2's TCP
    # setting at the least eager limit Open MPI accepts), the same programs, runs, delays and timeout
    python3 experiments/stress_baseline.py experiments/results/mbi/mbi.csv --runs 30 --jobs "$JOBS" --timeout "$TIMEOUT" --eager 0 &&
    python3 experiments/stress_baseline.py experiments/results/mbb/mbb.csv --runs 30 --jobs "$JOBS" --timeout "$TIMEOUT" --eager 0 \
        --select "$MBB_STRESS_SELECT"
}
step_e2() {
    python3 experiments/b_impl.py &&
    python3 experiments/e2_buffering.py --max-events 3000 --replay --timeout "$TIMEOUT"
}
step_e6() {
    python3 experiments/e6_checker_vs_explorer.py --max-events 2000 --max-states 100000 --synthetic 200
}
step_e6reads() {
    # E6 with read bounds (rule H1', docs/firstread.md): no result ever read, and a random bound
    # per receive; a straight-line program reads nothing, so both are sound for it
    python3 experiments/e6_checker_vs_explorer.py --max-events 2000 --max-states 100000 --synthetic 200 --reads never &&
    python3 experiments/e6_checker_vs_explorer.py --max-events 2000 --max-states 100000 --synthetic 200 --reads random
}
step_amg() {
    experiments/amg/run.sh all
}
step_e4() {
    # Refresh the analysis-time column of the overhead table with the current analysis code.
    python3 experiments/e4_reanalyze.py
}
step_e3() {
    PYTHONPATH="$R/analysis:$R/analysis/tests" python3 experiments/e3_levels.py > experiments/results/e3.md &&
    python3 experiments/e7_mbi_twins.py > /dev/null
}

steps=("$@")
[ ${#steps[@]} -eq 0 ] && steps=(build tests mbi mbb stress e2 e6 amg e4 e3)
# A failed step is logged and the next one runs, but the script then exits with status 1; a
# failed build stops it at once, since every later step would run stale libraries.
failed=()
for s in "${steps[@]}"; do
    declare -F "step_$s" > /dev/null || { log "unknown step: $s"; exit 2; }
    log "start $s"
    rc=0; "step_$s" || rc=$?
    log "end $s rc=$rc"
    if [ "$rc" != 0 ]; then
        failed+=("$s")
        [ "$s" = build ] && { log "the build failed: stopping"; exit 1; }
    fi
done
[ ${#failed[@]} -eq 0 ] || { log "FAILED: ${failed[*]}"; exit 1; }
log "ALL DONE"
