#!/usr/bin/env bash
# Traced runs of reshape_check (plain library and self-tags.patch) in the cells of series.sh, one
# taskpool per run, and Stretto's report on each trace (milestone F, docs/threads.md section 15:
# the communication engine's receives cancelled at finalisation are analysed, no longer
# degraded threads-cancel).
# Run inside a container of the image built from Dockerfile here, after build.sh:
#   experiments/parsec/traced.sh BUILD WORK OUT.csv
# BUILD holds parsec-plain and parsec-fixed; the traces and reports stay under WORK. RUNS (traced
# runs per cell and variant, 5), JOBS (analyses at once, 4).
set -u
B=${1:?usage: traced.sh BUILD WORK OUT.csv}; W=${2:?}; OUT=${3:?}
RUNS=${RUNS:-5}; JOBS=${JOBS:-4}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONPATH=$REPO/analysis OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
T=$REPO/tracer/bin/openmpi/libstretto-trace.so
# name:ranks:cores:mt:packages (series.sh; packages 0: the flat default vpmap)
CELLS="vp2-mt:1:6:1:2 vp2-mt-4ranks:4:2:1:2 vp2-serialized:1:6:0:2 vp1-mt:1:6:1:0"
mkdir -p "$W"
for i in $(seq 1 "$RUNS"); do
  for c in $CELLS; do
    IFS=: read -r name np cores mt pk <<< "$c"
    for v in plain fixed; do
      d=$W/$name-$v-$i; rm -rf "$d"; mkdir -p "$d/trace"
      env=(); vpmap=()
      if [ "$pk" -gt 0 ]; then env=(-x HWLOC_SYNTHETIC="package:$pk core:$((cores / pk)) pu:1"); vpmap=(-- --mca runtime_vpmap hwloc); fi
      timeout 300 mpirun --bind-to none -np "$np" "${env[@]}" -x REPS=1 -x STRETTO_TRACE_DIR="$d/trace" -x LD_PRELOAD="$T" \
        "$B/parsec-$v/tests/collections/reshape/reshape_check" -N 120 -t 9 -c "$cores" -m "$mt" "${vpmap[@]}" > "$d/log" 2>&1
      rc=$?
      w=$(sed -n 's/^summary reps [0-9]* wrong \([0-9]*\)$/\1/p' "$d/log")
      res=crash; if [ -n "$w" ]; then if [ "$w" -gt 0 ]; then res=wrong; else res=ok; fi; elif [ $rc = 124 ]; then res=hang; fi
      echo "$res" > "$d/result"
    done
  done
done
ls -d "$W"/*/trace | xargs -P "$JOBS" -I{} sh -c 'timeout 3600 python3 -m stretto analyze --json "{}" > "{}/../report.json" 2> "{}/../report.err"'
python3 -I "$REPO/experiments/dlaf/traced_csv.py" "$W" > "$OUT"
