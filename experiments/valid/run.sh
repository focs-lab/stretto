#!/usr/bin/env bash
# E8c: every replay behind a confirmation claim run again under the tracer, which judges it as a
# run of the program without its pins (stretto.confirm.run_checked, experiments/RUNBOOK.md,
# "Replays as runs of the program"). Inside the experiment image, after the runs it replays:
#   mbi      MBI's replays from the corpus run's program directories (run_mbi.py --replay-only
#            --replays-out; the table and its replays.csv are read, not rewritten)
#   reads    the same with read bounds, E3b's replays (after experiments/reads/run.sh)
#   npbmg    NPB MG 2.4.1 (the build of experiments/npbmg/run.sh): new traced runs, a sample of 60
#   dealii   deal.II 9.3.0-rc1 (the build of experiments/dealii/run.sh): the same
#   hypre    HYPRE 2.10.1 (the build of experiments/hypre/run.sh): the same
#   amg      AMG: the alternatives of its committed sample, on the trace of experiments/amg/run.sh
#   nekbone  Nekbone: the same (experiments/nekbone/run.sh; one replay's check takes about 17 GB)
# Writes experiments/results/valid/<step>.csv. The reruns of 22 Oct took about eight hours on 12
# processors, up to four containers at a time.
# usage: experiments/valid/run.sh [mbi|reads|npbmg|dealii|hypre|amg|nekbone ...]   JOBS (3)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
export PYTHONPATH="$R/analysis" OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_hwloc_base_binding_policy=none
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
V=experiments/results/valid; mkdir -p "$V"
D=third_party/apps/dealii

# the corpus programs with a replay that confirms, refutes or leaves inconclusive (the claims')
judged() {
    python3 - "$1" <<'PY'
import csv, sys
with open(sys.argv[1]) as fh:
    p = sorted({r["program"] for r in csv.DictReader(fh) if r["verdict"] in ("confirmed", "inconclusive", "not-confirmed")})
print("^(" + "|".join(p) + ")$" if p else "")
PY
}
corpus() {   # corpus <replays.csv> <out> [run_mbi options]: the judged replays of a corpus table again
    local rp=$1 out=$2 p; shift 2
    p=$(judged "$rp")
    [ -n "$p" ] || { echo "no judged replay in $rp" >&2; return 1; }
    python3 experiments/mbi/run_mbi.py --replay-only --select "$p" --work experiments/results/mbi \
        --replays-out "$out" --jobs "${JOBS:-3}" --timeout 60 --max-states 30000 --sp-max-events 600 "$@"
}
step_mbi() { corpus experiments/results/mbi/replays.csv "$V/mbi.csv" --out experiments/results/mbi/mbi.csv; }
step_reads() {
    corpus experiments/results/reads/replays.csv "$V/reads.csv" --out experiments/results/reads/mbi.csv \
        --reads experiments/results/reads/mbi
}
step_npbmg() {
    python3 experiments/npbmg/predict.py third_party/apps/npb-mg/NPB2.4.1/NPB2.4-MPI/bin --version 2.4.1 --runs 3 --np 16 \
        --sample 60 --timeout 120 --jobs 1 --out "$V/npbmg.csv"
}
step_dealii() {
    python3 experiments/dealii/predict.py "$D/drivers-9.3.0-rc1" --version 9.3.0-rc1 --runs 5 --np 4 --kinds cross --sample 60 \
        --expected "$R/$D/dealii-9.3.0-rc1/tests/remote_point_evaluation/mapping_01.mpirun={np}.output" \
        --normalize "$R/$D/dealii-9.3.0-rc1/cmake/scripts/normalize.pl" --timeout 120 --jobs 2 --out "$V/dealii.csv"
}
step_hypre() {
    local t=third_party/apps/hypre/hypre-2.10.1/src/test
    [ -d "$t" ] || t=third_party/apps/hypre/hypre-2.10.1/test
    python3 experiments/hypre/predict.py "$t" --runs 10 --np 4 8 --kinds cross --sample 60 --timeout 60 --jobs 2 --out "$V/hypre.csv"
}
step_amg() {
    python3 experiments/replay_sample.py --from-csv experiments/results/amg/replay_sample.csv --timeout 600 \
        --out "$V/amg.csv" experiments/results/amg/trace8 8 "$R/study/apps/AMG/test/amg" -P 2 2 2 -n 16 16 16 -problem 1
}
step_nekbone() {
    (cd study/apps/Nekbone/test/nek_comm && python3 "$R/experiments/replay_sample.py" \
        --from-csv "$R/experiments/results/nekbone/replay_sample.csv" --mpi mpich --timeout 600 \
        --out "$R/$V/nekbone.csv" "$R/experiments/results/nekbone/trace8" 8 ./nekbone data)
}
for step in "${@:-mbi reads npbmg dealii hypre amg nekbone}"; do
    for s in $step; do
        case "$s" in
            mbi|reads|npbmg|dealii|hypre|amg|nekbone) "step_$s";;
            *) echo "usage: $0 [mbi|reads|npbmg|dealii|hypre|amg|nekbone ...]" >&2; exit 2;;
        esac || exit 1
    done
done
