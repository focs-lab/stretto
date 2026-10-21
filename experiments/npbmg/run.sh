#!/usr/bin/env bash
# A second documented bug: NPB MG 2.4.1's halo exchanges post their receives with
# MPI_ANY_SOURCE and a tag that does not depend on the grid level, so a receive can take a
# message meant for another exchange (NPB3.3.1/Changes.log: "could cause MPI_Wait return a
# message meant for the wrong k"); 3.2.1 fixed it by naming the neighbour. Inside the
# experiment container:
#   fetch    download NPB 2.4.1 and 3.3.1, check the archives' sha256
#   build    MG class CLASS for each process count of NPS with the MPICH wrappers (the Fortran
#            calls are traced only through MPICH), both versions
#   predict  RUNS traced runs per version and process count, each analysed, every alternative
#            replayed with its horizon pins and its control, JOBS at a time
#            (experiments/npbmg/predict.py)
#   stress   RUNS untraced runs of 2.4.1 per process count with random send and receive delays
#            from the replay library, and RUNS plain runs; each prints MG's residual norm
#   all
# Writes experiments/results/npbmg/{predict-2.4.1,predict-3.3.1,stress}.csv.
# usage: experiments/npbmg/run.sh [fetch|build|predict|stress|all]
#        NPS (default "16 32"), CLASS (S), RUNS (5), JOBS (1), NPB_MG_DIR (third_party/apps/npb-mg)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${NPB_MG_DIR:-$R/third_party/apps/npb-mg}"
OUT="$R/experiments/results/npbmg"; mkdir -p "$OUT"
NPS="${NPS:-16 32}"; CLASS="${CLASS:-S}"; RUNS="${RUNS:-5}"; TIMEOUT=120
PINLIB="$R/replay/bin/mpich/libstretto-pin.so"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || git -C "$R" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
URL=https://www.nas.nasa.gov/assets/nas/npb
declare -A SHA=([2.4.1]=59448aeee0641c1e08b8bb211c6d38e8e2cee29b17a2678e2f81b05997d2c137
                [3.3.1]=4a8ea679b1df69f583c544c47198b3c26a50ec2bb6f8f69aef66c04c9a747d2d)
bindir() { echo "$D/NPB$1/NPB${1%.*}-MPI/bin"; }

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    for v in 2.4.1 3.3.1; do
        [ -f "NPB$v.tar.gz" ] || wget -q "$URL/NPB$v.tar.gz" || return 1
        echo "${SHA[$v]}  NPB$v.tar.gz" | sha256sum -c - || return 1
        [ -d "NPB$v" ] || tar xzf "NPB$v.tar.gz"
    done
    cd "$R"
}
step_build() {
    for v in 2.4.1 3.3.1; do
        local src="$D/NPB$v/NPB${v%.*}-MPI"
        cat > "$src/config/make.def" <<'DEF'
MPIF77 = /usr/bin/mpifort.mpich
FLINK = $(MPIF77)
FMPI_LIB =
FMPI_INC =
FFLAGS = -O2 -fallow-argument-mismatch -std=legacy
FLINKFLAGS = -O2
MPICC = /usr/bin/mpicc.mpich
CLINK = $(MPICC)
CMPI_LIB =
CMPI_INC =
CFLAGS = -O2
CLINKFLAGS = -O2
UCC = /usr/bin/gcc
CC = /usr/bin/gcc
BINDIR = ../bin
RAND = randi8
WTIME = wtime.c
DEF
        for np in $NPS; do
            [ -x "$(bindir $v)/mg.$CLASS.$np" ] && continue
            (cd "$src" && make MG NPROCS=$np CLASS=$CLASS > "$OUT/build-$v-$CLASS-$np.log" 2>&1) \
                || { echo "build of $v np=$np failed" >&2; return 1; }
        done
    done
}
step_predict() {
    for v in 2.4.1 3.3.1; do
        python3 experiments/npbmg/predict.py "$(bindir $v)" --version $v --class "$CLASS" --runs "$RUNS" --np $NPS \
            --timeout $TIMEOUT --jobs "${JOBS:-1}" --out "$OUT/predict-$v.csv" || return 1
    done
}
step_stress() {
    local b; b="$(bindir 2.4.1)"
    echo "version,class,np,run,setting,result,norm,commit" > "$OUT/stress.csv"
    for np in $NPS; do
        for i in $(seq 1 "$RUNS"); do
            for setting in plain delays; do
                local env=()
                if [ $setting = delays ]; then
                    ds=$(python3 -c "import random; r=random.Random($i*100+$np); print(','.join(f'{k}:{r.choice([0,0,0,1,5,20])}' for k in range($np)))")
                    dr=$(python3 -c "import random; r=random.Random($i*100+$np+7); print(','.join(f'{k}:{r.choice([0,0,0,1,5,20])}' for k in range($np)))")
                    env=(-x LD_PRELOAD="$PINLIB" -x STRETTO_SEND_DELAY_MS=$ds -x STRETTO_RECV_DELAY_MS=$dr)
                fi
                out=$(cd "$b" && /usr/bin/timeout -k 5 $TIMEOUT mpirun.openmpi --oversubscribe -np $np "${env[@]}" ./mg.$CLASS.$np < /dev/null 2>&1)
                rc=$?; r=FINISHED; [ $rc = 0 ] || { [ $rc = 124 ] && r=TIMEOUT || r="rc=$rc"; }
                n=$(echo "$out" | sed -n 's/.*L2 Norm is *\([^ ]*\).*/\1/p' | head -1)
                echo "2.4.1,$CLASS,$np,$i,$setting,$r,$n,$STRETTO_COMMIT" | tee -a "$OUT/stress.csv"
            done
        done
    done
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) step_predict;; stress) step_stress;;
        all) step_fetch && step_build && step_predict && step_stress;;
        *) echo "usage: $0 [fetch|build|predict|stress|all]" >&2; exit 2;;
    esac || exit 1
done
