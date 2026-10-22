#!/usr/bin/env bash
# How often does one trace expose the ParaSails race, and how often does testing hit it?
# After experiments/hypre/run.sh (fetch, build), inside the experiment container:
#   predict  RUNS traced runs of HYPRE 2.10.1 per rank count (4 and 8), each analysed; every
#            probe alternative of the request exchanges (tag 222), whether it takes a request
#            of the other exchange or of the same one, is replayed with the pins of its whole
#            horizon and with its control, JOBS replays at a time (experiments/hypre/predict.py)
#   stress   RUNS untraced runs per rank count with random send and receive delays from the
#            replay library (no pin), and RUNS plain runs, each with a 60 s timeout
# Writes experiments/results/hypre/{predict.csv,stress.csv}. With HYPRE_VERSION=2.11.0, the fix,
# whose two exchanges send their requests with tags 221 and 222: predict-2.11.0.csv and
# stress-2.11.0.csv, the same steps on the fixed code.
# usage: experiments/hypre/campaign.sh [predict|stress|all]
#        RUNS (default 20), JOBS (default 1), HYPRE_VERSION (default 2.10.1)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
H="${HYPRE_DIR:-$R/third_party/apps/hypre}"
OUT=experiments/results/hypre; mkdir -p "$OUT"
RUNS="${RUNS:-20}"; TIMEOUT=60; ARGS=(-solver 8 -pout 0)
TRACER="$R/tracer/bin/mpich/libstretto-trace.so"; PINLIB="$R/replay/bin/mpich/libstretto-pin.so"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || git -C "$R" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
V="${HYPRE_VERSION:-2.10.1}"
SRC="$H/hypre-$V"; [ -f "$SRC/configure" ] || SRC="$SRC/src"
case "$V" in 2.10.1) SUF=; TAGS=222;; *) SUF="-$V"; TAGS="221 222";; esac
TMP="${TMPDIR:-/tmp}/hypre-campaign"; mkdir -p "$TMP"

run() {   # run <np> <logfile> [-x VAR=value ...]: one run of ij, prints FINISHED | TIMEOUT | rc=N
    local np=$1 log=$2; shift 2
    (cd "$SRC/test" && /usr/bin/timeout -k 5 $TIMEOUT mpirun.openmpi --oversubscribe -np "$np" "$@" ./ij "${ARGS[@]}" < /dev/null > "$log" 2>&1)
    local rc=$?; [ $rc = 0 ] && echo FINISHED || { [ $rc = 124 ] && echo TIMEOUT || echo "rc=$rc"; }
}
step_predict() {
    python3 experiments/hypre/predict.py "$SRC/test" --runs "$RUNS" --np 4 8 --timeout $TIMEOUT --jobs "${JOBS:-1}" \
        --tags $TAGS --out "$OUT/predict$SUF.csv"
}
step_stress() {
    need_file "$PINLIB" || return 1
    echo "np,run,setting,result,commit" > "$OUT/stress$SUF.csv"
    for np in 4 8; do
        for i in $(seq 1 "$RUNS"); do
            r=$(run "$np" "$TMP/plain-$np-$i.log")
            echo "$np,$i,plain,$r,$STRETTO_COMMIT" >> "$OUT/stress$SUF.csv"
            ds=$(python3 -c "import random; r=random.Random($i*100+$np); print(','.join(f'{k}:{r.choice([0,0,0,1,5,20,50])}' for k in range($np)))")
            dr=$(python3 -c "import random; r=random.Random($i*100+$np+7); print(','.join(f'{k}:{r.choice([0,0,0,1,5,20,50])}' for k in range($np)))")
            r=$(run "$np" "$TMP/delay-$np-$i.log" -x LD_PRELOAD="$PINLIB" -x STRETTO_SEND_DELAY_MS=$ds -x STRETTO_RECV_DELAY_MS=$dr)
            preload_ok "$TMP/delay-$np-$i.log" || { harness_error "$PINLIB did not load (np $np, run $i)"; return 1; }
            echo "$np,$i,delays,$r,$STRETTO_COMMIT" | tee -a "$OUT/stress$SUF.csv"
        done
    done
}
for step in "${@:-all}"; do
    case "$step" in
        predict) step_predict;; stress) step_stress;; all) step_predict && step_stress;;
        *) echo "usage: $0 [predict|stress|all]" >&2; exit 2;;
    esac || exit 1
done
