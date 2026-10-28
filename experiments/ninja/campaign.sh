#!/usr/bin/env bash
# NINJA against the bugs Stretto predicts, with the run budget and timeout of the stress
# baseline (experiments/hypre/campaign.sh, experiments/stress_baseline.py,
# experiments/npbmg/run.sh). Inside the experiment container, after experiments/ninja/build.sh:
#   calibrate  NINJA's own replica of the ParaSails race (build.sh's parasails_anysize, LOOPS
#              iterations of its two exchanges with its one-second pause between them) and its
#              fixed variant at 4 and 8 ranks: RUNS runs per mode, plain runs among them, and on
#              the replica random noise as shipped, which delays nothing below a ratio of 100%
#   hypre      HYPRE's ij -solver 8 -pout 0 (HYPRE_DIR as for experiments/hypre/, 2.10.1 and the
#              fix 2.11.0) at 4 and 8 ranks, RUNS runs per mode, 60 s each, one at a time; a run
#              records how far it got (setup: the ParaSails setup, where the race hangs,
#              completed; solved: the residual was printed)
#   mbi        the MBI programs of the stress baseline, up to 30 runs per program and mode,
#              60 s each, stopping at the first that does not finish, JOBS programs at a time
#   mg         NPB MG 2.4.1 class S (NPB_MG_DIR as for experiments/npbmg/) at each NPS, RUNS
#              runs per mode, plain runs among them, 120 s each, recording MG's residual norm
#   superlu    SuperLU_DIST 9.3.1's 3D solve and its fix, from the builds of the verification
#              (SLU_DIR, SLU_MATS), at 3 and 6 ranks, RUNS runs per mode, 60 s each
#   qe         Quantum ESPRESSO's elph_prt and its fix, from the MPICH builds of the verification
#              (QE_DIR, QE_REF), at 3 ranks in 3 pools, RUNS runs per mode, 120 s each
# Modes: MODES (default random system app; mg adds plain). Random noise delays RAND_RATIO
# percent of the sends by each of RAND_DELAYS microseconds in turn: 50 ms, the stress
# baseline's largest delay, and 1.2 s, longer than the replica's pause (superlu and qe: 50 ms,
# as the steps of the four historical bugs in their run.sh).
# Writes experiments/results/ninja/{calibrate,hypre,mbi,mg,superlu,qe}.csv, each step its table afresh
# (run.py appends, and a rerun appended to the last campaign would be counted with it;
# NINJA_APPEND=1 keeps the table, to add for example a HYPRE version); the output of runs that
# did not finish goes to experiments/results/ninja/logs/ (NINJA_OUT: another directory).
# usage: experiments/ninja/campaign.sh [calibrate|hypre|mbi|mg|superlu|qe ...]
#        RUNS (40; calibrate and mg 20), JOBS (3), LOOPS (5), NPS (16 32), RAND_RATIO (10),
#        RAND_DELAYS (50000 1200000)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
OUT="${NINJA_OUT:-experiments/results/ninja}"; mkdir -p "$OUT"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
RUN="python3 experiments/ninja/run.py"
H="${HYPRE_DIR:-$R/third_party/apps/hypre}"
MG="${NPB_MG_DIR:-$R/third_party/apps/npb-mg}/NPB2.4.1/NPB2.4-MPI/bin"
NINJA="${NINJA_DIR:-$R/third_party/ninja}"

fresh() {  # fresh <table>: start a step's table afresh unless NINJA_APPEND=1
    [ "${NINJA_APPEND:-0}" = 1 ] || rm -f "$OUT/$1.csv"
}
ninja() {  # one run.py call; a harness error (status 3: no NINJA, a run killed, nothing selected) stops the step
    $RUN "$@"
    [ $? != 3 ] || { echo "HARNESS-ERROR: experiments/ninja/run.py $1 stopped; the step's table is incomplete" >&2; return 1; }
}
each() {   # each <modes> -- <run.py arguments>: one run.py call per mode, per delay for random
    local modes="$1" m d; shift 2
    for m in $modes; do
        if [ "$m" = random ]; then
            for d in ${RAND_DELAYS:-50000 1200000}; do
                ninja "$1" --mode random --rand-ratio "${RAND_RATIO:-10}" --rand-delay "$d" "${@:2}" || return 1
            done
        else
            ninja "$1" --mode "$m" "${@:2}" || return 1
        fi
    done
}
step_calibrate() {
    fresh calibrate
    local label exe np
    for label in replica replica-fixed; do
        exe="$NINJA/mpich/parasails/parasails_anysize"; [ $label = replica-fixed ] && exe="${exe}_fixed"
        for np in 4 8; do
            local a=(--np $np --runs "${RUNS:-20}" --label $label --out "$OUT/calibrate.csv" --keep "$OUT/logs" --value "loop ([0-9]+)")
            each "plain random system app" -- runs "${a[@]}" -- "$exe" "${LOOPS:-5}" || return 1
            if [ $label = replica ]; then ninja runs --mode random --shipped --rand-ratio "${RAND_RATIO:-10}" --rand-delay 1200000 \
                "${a[@]}" -- "$exe" "${LOOPS:-5}" || return 1; fi
        done
    done
}
step_hypre() {
    fresh hypre
    local v src np
    for v in ${HYPRE_VERSIONS:-2.10.1 2.11.0}; do
        src="$H/hypre-$v"; [ -f "$src/configure" ] || src="$src/src"
        for np in 4 8; do
            each "${MODES:-random system app}" -- runs --np $np --runs "${RUNS:-40}" --label "hypre-$v" --out "$OUT/hypre.csv" \
                --line-buffered --marks "setup=Setup phase times,solved=Final Relative Residual Norm" \
                --value "Final Relative Residual Norm = (\S+)" --keep "$OUT/logs" -- "$src/test/ij" -solver 8 -pout 0 || return 1
        done
    done
}
step_mbi() {
    fresh mbi
    each "${MODES:-random system app}" -- corpus experiments/results/mbi/mbi.csv --runs 30 --timeout 60 --jobs "${JOBS:-3}" \
        --out "$OUT/mbi.csv" || return 1
}
step_superlu() {
    # SuperLU_DIST 9.3.1's pddrive3d from the verification's builds (SLU_DIR: build-openmpi-orig,
    # and build-openmpi-fix with the barrier between the L- and U-solve), blk12x6 on grid 1x3x1
    # and blk8x10 on 2x3x1 (SLU_MATS), RUNS runs per mode, random noise at 50 ms only as on the
    # four historical bugs, 60 s each; the value is the x error pddrive3d prints
    fresh superlu
    local RAND_DELAYS="${RAND_DELAYS:-50000}" v s m r c d
    export OMPI_MCA_btl_vader_single_copy_mechanism=none
    for v in orig fix; do
        for s in blk12x6:1:3:1 blk8x10:2:3:1; do
            IFS=: read -r m r c d <<< "$s"
            each "${MODES:-random system app}" -- runs --np $((r * c * d)) --runs "${RUNS:-40}" --timeout 60 --mpi openmpi \
                --label "pddrive3d-$m-$v" --out "$OUT/superlu.csv" --value '\|\|X - Xtrue\|\| / \|\|X\|\| = (\S+)' \
                --keep "$OUT/logs" -- "${SLU_DIR:?}/build-openmpi-$v/EXAMPLE/pddrive3d" -r "$r" -c "$c" -d "$d" "${SLU_MATS:?}/$m.mtx" \
                || return 1
        done
    done
}
step_qe() {
    # Quantum ESPRESSO's ph.x from the verification's MPICH builds (QE_DIR: build-mpich-dev, and
    # build-mpich-fix with a tag per spin), the Ni input at X with electron_phonon='prt' and the
    # k point of the verification (QE_DIR/run/k39, copied into each run's directory), 3 ranks in
    # 3 pools, RUNS runs per mode, random noise at 50 ms only, 120 s each; the value is the number
    # of elph_prt rows that differ from the one-process reference QE_REF (experiments/elph_rows.py)
    fresh qe
    local RAND_DELAYS="${RAND_DELAYS:-50000}" v
    for v in dev fix; do
        each "${MODES:-random system app}" -- runs --np 3 --runs "${RUNS:-40}" --timeout 120 --mpi mpich --label "ph.x-$v" \
            --out "$OUT/qe.csv" --setup "${QE_DIR:?}/run/k39" --check "python3 $R/experiments/elph_rows.py run.out ${QE_REF:?}" \
            --keep "$OUT/logs" -- "$QE_DIR/build-mpich-$v/bin/ph.x" -nk 3 -in ph.in || return 1
    done
}
step_mg() {
    fresh mg
    local np
    for np in ${NPS:-16 32}; do
        each "${MODES:-plain random system app}" -- runs --np $np --runs "${RUNS:-20}" --timeout 120 --label mg-2.4.1-S \
            --out "$OUT/mg.csv" --value "L2 Norm is\s+(\S+)" --keep "$OUT/logs" -- "$MG/mg.S.$np" || return 1
    done
}
for step in "$@"; do
    case "$step" in
        calibrate|hypre|mbi|mg|superlu|qe) "step_$step" || exit 1;;
        *) echo "usage: $0 [calibrate|hypre|mbi|mg|superlu|qe ...]" >&2; exit 2;;
    esac
done
