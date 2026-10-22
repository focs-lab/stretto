#!/usr/bin/env bash
# Scale: trace HYPRE's ij driver (ParaSails, 2.10.1), AMG and NPB LU beyond eight processes,
# analyse each trace under /usr/bin/time, and replay a sample of its alternatives with the
# pins of their horizon and with controls (experiments/replay_sample.py). Inside the
# experiment container, after third_party/fetch.sh apps npb and experiments/hypre/run.sh build:
#   experiments/scale/run.sh [hypre|amg|lu|all]
#   NPS (default "16 32 64"), SAMPLE (replays per trace, default 10), TIMEOUT (replay, 120 s)
# Appends to experiments/results/scale/scale.csv, one row per application and process count:
# the trace's size, its alternatives by class, the analysis time and peak memory, and the
# replay sample's outcome; the sample's rows go to samples/<app>-<np>.csv. Process counts
# above the container's processors oversubscribe: these runs measure size and cost of the
# analysis, which runs in one process, not the speed of the traced program.
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
OUT="$R/experiments/results/scale"; mkdir -p "$OUT/samples"
NPS="${NPS:-16 32 64}"; SAMPLE="${SAMPLE:-10}"; TIMEOUT="${TIMEOUT:-120}"
TMP="${TMPDIR:-/tmp}/stretto-scale"; mkdir -p "$TMP"
COMMIT="$(cat "$R/COMMIT" 2>/dev/null || git -C "$R" rev-parse --short HEAD 2>/dev/null || echo unknown)"
export PYTHONPATH="$R/analysis" STRETTO_COMMIT="$COMMIT"
source "$R/experiments/harness.sh"
[ -f "$OUT/scale.csv" ] || echo "app,np,mpi,traced_run,records,wildcard_receives,probes,findings,analysis_s,peak_kb,sample,matched,finished,controls_finished,commit" > "$OUT/scale.csv"

one() {   # one <app> <np> <mpi> <dir> <exe> [args ...]: trace, analyse, replay a sample, append a row
    local app=$1 np=$2 mpi=$3 dir=$4; shift 4
    local td="$TMP/$app-$np" log="$OUT/samples/$app-$np"
    need_file "$R/tracer/bin/$mpi/libstretto-trace.so" "$R/replay/bin/$mpi/libstretto-pin.so" || return 1
    rm -rf "$td"; mkdir -p "$td"
    (cd "$dir" && /usr/bin/timeout -k 5 1800 /usr/bin/mpirun.openmpi --oversubscribe -np "$np" -x OMP_NUM_THREADS=1 \
        -x STRETTO_TRACE_DIR="$td" -x LD_PRELOAD="$R/tracer/bin/$mpi/libstretto-trace.so" "$@" < /dev/null > "$log.run" 2>&1)
    local rc=$? run=FINISHED; [ $rc = 0 ] || run="rc=$rc"
    /usr/bin/time -f '%e %M' -o "$log.time" python3 -m stretto analyze --no-sp "$td" > "$log.report" 2> "$log.err"
    local t; t=$(tail -1 "$log.time")
    local head; head=$(head -1 "$log.report")   # "trace: N ranks, S sends, R receives, W wildcard receives, P probes, ..."
    local recs; recs=$(cat "$td"/rank-*.jsonl | wc -l)
    local wild probes finds
    wild=$(echo "$head" | sed -n 's/.* \([0-9]*\) wildcard receives.*/\1/p')
    probes=$(echo "$head" | sed -n 's/.* \([0-9]*\) probes.*/\1/p')
    finds=$(grep -o '^\[ *[0-9a-z-]* *\]' "$log.report" | tr -d '[] ' | sort | uniq -c | awk '{printf "%s %s;", $2, $1}')
    (cd "$dir" && python3 "$R/experiments/replay_sample.py" --out "$log.csv" --sample "$SAMPLE" --timeout "$TIMEOUT" --mpi "$mpi" \
        "$td" "$np" "$@" > "$log.sample" 2>&1)
    local s m f c
    read -r s m f c < <(python3 - "$log.csv" <<'PY'
import csv, sys, os
rows = list(csv.DictReader(open(sys.argv[1]))) if os.path.exists(sys.argv[1]) else []
r = [x for x in rows if x.get("replay") not in (None, "", "no horizon")]
print(len(r), sum(x["matched"] == "True" for x in r), sum(x["replay"] == "FINISHED" for x in r),
      sum(x["control"] == "FINISHED" for x in r))
PY
)
    echo "$app,$np,$mpi,$run,$recs,${wild:-},${probes:-},${finds%;},${t% *},${t#* },$s,$m,$f,$c,$COMMIT" | tee -a "$OUT/scale.csv"
    rm -rf "$td"
}

hypre() {   # ParaSails in ij, the E5c driver, on HYPRE 2.10.1 built with MPICH
    local src="${HYPRE_DIR:-$R/third_party/apps/hypre}/hypre-2.10.1"; [ -f "$src/configure" ] || src="$src/src"
    for np in $NPS; do one hypre "$np" mpich "$src/test" ./ij -solver 8 -pout 0; done
}
amg() {     # AMG with 16^3 points per process, the E5 problem, on a 3-D process grid
    for np in $NPS; do
        case $np in 8) p="2 2 2";; 16) p="4 2 2";; 32) p="4 4 2";; 64) p="4 4 4";; 128) p="8 4 4";; *) echo "no grid for $np"; continue;; esac
        one amg "$np" openmpi "$R/study/apps/AMG/test" ./amg -P $p -n 16 16 16 -problem 1
    done
}
lu() {      # NPB LU class A (MPICH build): the E4 cost benchmark
    for np in $NPS; do one lu "$np" mpich "$R/third_party/apps/NPB-mpich/NPB3.4-MPI/bin" ./lu.A.x; done
}
for step in "${@:-all}"; do
    case "$step" in
        hypre) hypre;; amg) amg;; lu) lu;; all) hypre; amg; lu;;
        *) echo "usage: $0 [hypre|amg|lu|all]" >&2; exit 2;;
    esac
done
