#!/usr/bin/env bash
# MUST on the five applications whose races Stretto predicts, at the configurations of their
# traced campaigns, on a host with docker (as experiments/amg/must.sh and scripts/must_run.sh):
#   HYPRE 2.10.1, test/ij -solver 8 -pout 0, 4 and 8 ranks (experiments/hypre/)
#   HYPRE 3.2.0, ex5_levels -solver 8 -sai_levels 2 -n 33, 4 and 8 ranks (experiments/hypre3/)
#   PETSc 3.26.0 with HYPRE 3.2.0, ex2 with ParaSails at two levels, 4 and 8 ranks
#     (experiments/petsc/)
#   NPB MG 2.4.1 class S, 16 ranks (experiments/npbmg/)
#   deal.II 9.3.0-rc1, tests/remote_point_evaluation/mapping_01, 4 ranks (experiments/dealii/)
# MUST's images carry MPICH 4.0.2 and clang 14 on Debian 12 (experiments/must/Dockerfile, and
# MPI-BugBench's image of MUST 1.9.2, the same base and toolchain), so the applications are
# built there, once, by each campaign's own build step with MPICH's wrappers; the builds run
# under either MUST release. Steps:
#   fetch   each campaign's fetch step, in the experiment image (EXP_IMAGE), into MUST_APPS_DIR
#   build   each campaign's build step, in BUILD_IMAGE (default stretto-must:1.11.2)
#   run     RUNS runs per configuration (CONFIGS) under mustrun, MUST's distributed layout and
#           JSON output as on the corpora (MUST_ARGS), TIMEOUT seconds each (default: the
#           configuration's own, below), one container per run, JOBS runs at a time. MUST's
#           release is chosen as for scripts/must_run.sh: MUST_IMAGE and MUST_PREFIX, recorded
#           as MUST_LABEL (default 1.11.2). NPB MG is Fortran: MUST built without its Fortran
#           interface, as for the corpora, reports MUST_ERROR_DATATYPE_UNKNOWN on its calls and
#           does not end; MG's runs use the image built with FORTRAN=ON (experiments/must/build.sh,
#           MUST_IMAGE=stretto-must:1.11.2-fortran, MUST_LABEL=1.11.2-fortran) and
#           MUST_ARGS="--must:distributed --must:language fortran"
# experiments/must/once.sh makes one run inside the image. Each run's row: how the run ended
# (FINISHED: mustrun printed its closing line; CRASHED: MPICH's launcher reported a process that
# ended badly, MUST's tool processes included; TIMEOUT: the harness's timeout; KILLED: a SIGKILL
# before the timeout; rc=N otherwise), mustrun's exit status (MUST's choice for its findings, not
# the program's status), MUST's report as its messages' classes verbatim, error_id(call)*count for
# its errors and warnings ("no-report": MUST wrote no readable report), whether MUST reported a
# deadlock, the value the campaign's own check reads (residual, iterations, norm, or the number
# of output lines that differ from the expected output; empty when the program did not get that
# far) and whether that check fails (wrong_result: True, False, or empty for the checks without a
# reference, HYPRE's and PETSc's, whose bug is a hang, and for deal.II's output when the run did
# not finish). The output of every run is kept under experiments/results/must/runs/.
# Writes experiments/results/must/apps.csv (rows appended; a new table gets the header).
# usage: experiments/must/apps.sh [fetch|build|run ...]
#        RUNS (20), FIRST (1: the number of the first run, to add runs to a configuration),
#        JOBS (1), CPUS (cpuset of every container), MEMORY (24g), CONFIGS (all),
#        MUST_APPS_DIR (third_party/apps/must), TIMEOUT (per configuration)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
M="${MUST_APPS_DIR:-$R/third_party/apps/must}"; mkdir -p "$M"
case "$M" in "$R"/*) ;; *) echo "MUST_APPS_DIR must lie inside the repository (it is mounted with it)" >&2; exit 2;; esac
MW="/w/${M#"$R"/}"                       # the same directory inside the containers
OUT="$R/experiments/results/must"; mkdir -p "$OUT/runs"
TABLE="$OUT/apps.csv"
RUNS="${RUNS:-20}"; FIRST="${FIRST:-1}"; JOBS="${JOBS:-1}"; MEMORY="${MEMORY:-24g}"
EXP_IMAGE="${EXP_IMAGE:-stretto-exp:24.04}"; BUILD_IMAGE="${BUILD_IMAGE:-stretto-must:1.11.2}"
MUST_IMAGE="${MUST_IMAGE:-stretto-must:1.11.2}"; MUST_PREFIX="${MUST_PREFIX:-/MBB-builds/MUST1112}"
MUST_LABEL="${MUST_LABEL:-1.11.2}"; MUST_ARGS="${MUST_ARGS---must:distributed}"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
cpus=(); [ -n "${CPUS:-}" ] && cpus=(--cpuset-cpus "$CPUS")

# name|app|version|np|timeout|check|program and arguments (container paths). The timeouts are the
# campaigns' own (TIMEOUT overrides them); check names the campaign's validity check (once.sh).
PS="-m 48 -n 48 -pc_type hypre -pc_hypre_type parasails -pc_hypre_parasails_nlevels 2 -pc_hypre_parasails_thresh 0.01"
ALL="hypre-4|hypre|2.10.1|4|60|residual|$MW/hypre/hypre-2.10.1/test/ij -solver 8 -pout 0
hypre-8|hypre|2.10.1|8|60|residual|$MW/hypre/hypre-2.10.1/test/ij -solver 8 -pout 0
hypre3-ex5-4|hypre3|3.2.0|4|30|residual|$MW/hypre3/hypre-3.2.0/src/examples/ex5_levels -solver 8 -sai_levels 2 -n 33
hypre3-ex5-8|hypre3|3.2.0|8|30|residual|$MW/hypre3/hypre-3.2.0/src/examples/ex5_levels -solver 8 -sai_levels 2 -n 33
petsc-ex2-4|petsc|3.26.0|4|30|iterations|$MW/petsc/petsc-3.26.0/src/ksp/ksp/tutorials/ex2 $PS
petsc-ex2-8|petsc|3.26.0|8|30|iterations|$MW/petsc/petsc-3.26.0/src/ksp/ksp/tutorials/ex2 $PS
npbmg-16|npbmg|2.4.1|16|120|mgnorm|$MW/npb-mg/NPB2.4.1/NPB2.4-MPI/bin/mg.S.16
dealii-4|dealii|9.3.0-rc1|4|120|dealii|$MW/dealii/drivers-9.3.0-rc1/mapping_01"

step_fetch() {
    docker run --rm --name stretto-honest-fetch "${cpus[@]}" --memory "$MEMORY" --user "$(id -u):$(id -g)" -e HOME=/tmp \
        -e STRETTO_COMMIT -v "$R:/w" -w /w "$EXP_IMAGE" bash -c "
      HYPRE_DIR=$MW/hypre experiments/hypre/run.sh fetch && HYPRE3_DIR=$MW/hypre3 experiments/hypre3/run.sh fetch &&
      PETSC_SRC_DIR=$MW/petsc experiments/petsc/run.sh fetch && NPB_MG_DIR=$MW/npb-mg experiments/npbmg/run.sh fetch &&
      DEALII_DIR=$MW/dealii experiments/dealii/run.sh fetch"
}
step_build() {
    # MPICH's wrappers call clang 14 (MPICH_CC, MPICH_CXX in the image); the Fortran of MG is gfortran's
    local j="${MAKE_JOBS:-8}"
    docker run --rm --name stretto-honest-build "${cpus[@]}" --memory "$MEMORY" --memory-swap "$MEMORY" \
        --user "$(id -u):$(id -g)" -e HOME=/tmp -e STRETTO_COMMIT -v "$R:/w" -w /w "$BUILD_IMAGE" bash -c "
      set -e
      HYPRE_DIR=$MW/hypre experiments/hypre/run.sh build
      HYPRE3_DIR=$MW/hypre3 MPI_CC=/usr/bin/mpicc.mpich MPI_CXX=/usr/bin/mpicxx.mpich MAKE_JOBS=$j experiments/hypre3/run.sh build
      PETSC_SRC_DIR=$MW/petsc PETSC_ARCH=arch-mpich-opt MPI_CC=/usr/bin/mpicc.mpich MPI_CXX=/usr/bin/mpicxx.mpich MAKE_JOBS=$j \
        experiments/petsc/run.sh build
      NPB_MG_DIR=$MW/npb-mg NPS=16 experiments/npbmg/run.sh build
      DEALII_DIR=$MW/dealii experiments/dealii/run.sh build"
}
one_run() {   # one_run <name> <app> <version> <np> <timeout> <check> <i> <program and args>
    local name=$1 app=$2 ver=$3 np=$4 to=$5 check=$6 i=$7 cmd=$8
    local d="experiments/results/must/runs/$MUST_LABEL/$name/$i"; rm -rf "$R/$d"; mkdir -p "$R/$d"
    local line
    line=$(docker run --rm --name "stretto-honest-must-$MUST_LABEL-$name-$i" "${cpus[@]}" --memory "$MEMORY" \
        --memory-swap "$MEMORY" --shm-size 4g --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$R:/w" -w /w \
        -e STRETTO_MUST_DIR="$MUST_PREFIX" -e STRETTO_MUST_ARGS="$MUST_ARGS" \
        -e STRETTO_DEALII_NORMALIZE="$MW/dealii/dealii-9.3.0-rc1/cmake/scripts/normalize.pl" \
        -e STRETTO_DEALII_EXPECTED="$MW/dealii/dealii-9.3.0-rc1/tests/remote_point_evaluation/mapping_01.mpirun=4.output" \
        "$MUST_IMAGE" bash -c "/w/experiments/must/once.sh $np $to $check /w/$d $cmd" 2> "$R/$d/docker.err" | tail -1)
    [ -n "$line" ] || line="HARNESS-ERROR,,,,,,,"
    echo "$app,$ver,$name,$np,$MUST_LABEL,$i,$to,$line,$STRETTO_COMMIT"
}
step_run() {
    [ -f "$TABLE" ] || echo "app,version,config,np,must,run,timeout,result,mustrun_rc,seconds,must_errors,must_warnings,must_deadlock,value,wrong_result,commit" > "$TABLE"
    local name app ver np to check cmd i
    while IFS='|' read -r name app ver np to check cmd; do
        case " ${CONFIGS:-all} " in *" all "*|*" $name "*) ;; *) continue;; esac
        to="${TIMEOUT:-$to}"
        for i in $(seq "$FIRST" $((FIRST + RUNS - 1))); do
            one_run "$name" "$app" "$ver" "$np" "$to" "$check" "$i" "$cmd" >> "$OUT/rows-$MUST_LABEL-$name-$i.tmp" &
            while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do wait -n; done
        done
        wait
        for i in $(seq "$FIRST" $((FIRST + RUNS - 1))); do cat "$OUT/rows-$MUST_LABEL-$name-$i.tmp" | tee -a "$TABLE"; rm -f "$OUT/rows-$MUST_LABEL-$name-$i.tmp"; done
    done <<< "$ALL"
}
for step in "${@:-run}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; run) step_run;;
        *) echo "usage: $0 [fetch|build|run ...]" >&2; exit 2;;
    esac || exit 1
done
