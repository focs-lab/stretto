#!/usr/bin/env bash
# Replay the reshape tag collision that L1 predicts (docs/threads.md, sections 14 and 15): traced
# runs of reshape_check (plain library) with the lock order recorded (STRETTO_SYNC_ORDER=1), and
# for each run that finished up to SAMPLE of its L1 alternatives, each forced REPEAT times with
# the chain pins, order gates and lock gates of its horizon and REPEAT times as its control
# (experiments/replay_sample.py --threads). The oracle is the driver's own check: a run is wrong
# when its summary line counts a wrong taskpool. Run inside a container of the image built from
# Dockerfile here, after build.sh:
#   experiments/parsec/replay.sh BUILD WORK
# Settings: CELL (name:ranks:cores:mt:packages as in traced.sh, vp2-mt:1:6:1:2), RUNS (traced
# runs, 5), SAMPLE (3), REPEAT (2), TIMEOUT (60 s per run). Writes WORK/traced.csv (run, result,
# Stretto's L1 findings) and WORK/replay.csv (replay_sample.py's columns with the run in front;
# "hits" is 1 for a run whose check failed); each run's output is kept under WORK/run<i>/out. A
# trace is deleted once its run is replayed.
set -u
B=${1:?usage: replay.sh BUILD WORK}; W=${2:?}
CELL=${CELL:-vp2-mt:1:6:1:2}; RUNS=${RUNS:-5}; SAMPLE=${SAMPLE:-3}; REPEAT=${REPEAT:-2}; TIMEOUT=${TIMEOUT:-60}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONPATH=$REPO/analysis OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
export OMPI_MCA_hwloc_base_binding_policy=none
T=$REPO/tracer/bin/openmpi/libstretto-trace.so
IFS=: read -r name np cores mt pk <<< "$CELL"
exe=$B/parsec-plain/tests/collections/reshape/reshape_check
args=(-N 120 -t 9 -c "$cores" -m "$mt"); envs=(REPS=1)
if [ "$pk" -gt 0 ]; then envs+=("HWLOC_SYNTHETIC=package:$pk core:$((cores / pk)) pu:1"); args+=(-- --mca runtime_vpmap hwloc); fi
xs=(); es=(); for e in "${envs[@]}"; do xs+=(-x "$e"); es+=(--env "$e"); done
mkdir -p "$W"
echo "run,result,stretto_rc,l1" > "$W/traced.csv"
out=$W/replay.csv; rm -f "$out"
for i in $(seq 1 "$RUNS"); do
  d=$W/run$i; rm -rf "$d"; mkdir -p "$d/trace"
  timeout 300 mpirun --bind-to none -np "$np" "${xs[@]}" -x STRETTO_TRACE_DIR="$d/trace" -x STRETTO_SYNC_ORDER=1 \
    -x LD_PRELOAD="$T" "$exe" "${args[@]}" > "$d/log" 2>&1
  w=$(sed -n 's/^summary reps [0-9]* wrong \([0-9]*\)$/\1/p' "$d/log")
  res=crash; if [ -n "$w" ]; then if [ "$w" -gt 0 ]; then res=wrong; else res=ok; fi; fi
  timeout 3600 python3 -m stretto analyze "$d/trace" > "$d/report.txt" 2> "$d/report.err"; rc=$?
  l1=$(grep '^\[' "$d/report.txt" | grep -c ' L1 ')
  echo "$i,$res,$rc,$l1" >> "$W/traced.csv"
  if [ "$res" != crash ] && [ "$l1" -gt 0 ]; then
    python3 "$REPO/experiments/replay_sample.py" --threads --repeat "$REPEAT" --sample "$SAMPLE" ${SEED:+--seed "$SEED"} \
      --grep 'summary reps [0-9]+ wrong [1-9]' --timeout "$TIMEOUT" --keep-output "$d/out" "${es[@]}" --out "$d/replay.csv" \
      "$d/trace" "$np" "$exe" "${args[@]}" > "$d/replay.log" 2>&1
    [ -f "$out" ] || { head -1 "$d/replay.csv" | sed 's/^/run,/' > "$out"; }
    tail -n +2 "$d/replay.csv" | sed "s/^/$i,/" >> "$out"
  fi
  rm -rf "$d/trace"
done
cat "$W/traced.csv"
