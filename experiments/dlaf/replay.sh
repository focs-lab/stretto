#!/usr/bin/env bash
# Replay the matrix::copy tag collision that L1 predicts (docs/threads.md, sections 14 to 16):
# traced runs of copy_check (plain headers) with the lock order recorded (STRETTO_SYNC_ORDER=1)
# and every wrong tile reported (COPY_CHECK_TILES=1), and for each run that finished up to
# SAMPLE of its L1 alternatives, each forced REPEAT times with the chain pins, order gates and lock
# gates of its horizon and REPEAT times as its control (experiments/replay_sample.py --threads),
# with each run's output kept: the tiles a replay moved, against its control's and the traced
# run's, are the oracle of a copy that was wrong already. Run inside a container of the image
# built from Dockerfile here, with copy_check built (build.sh driver; DRIVER_OUT and
# VARIANTS=plain for a copy elsewhere):
#   experiments/dlaf/replay.sh COPY_CHECK WORK
# Settings: CELL (name:ranks:threads:M:N:mb_src:nb_src:mb_dst:nb_dst:P:Q:src_r:src_c:dst_r:dst_c:
# jitter as in traced.sh, the 2x1 cell by default), RUNS (traced runs, 5), SAMPLE (3), REPEAT (2),
# TIMEOUT (60 s per run), NO_SYNC (1: trace without the lock order, so that the replays gate only
# the pairs the alternative reorders, as in milestone C). Writes WORK/traced.csv (run, result,
# Stretto's L1 findings) and WORK/replay.csv (replay_sample.py's columns with the run in front),
# and keeps each traced run's log in WORK/run<i>/log and each replay's and control's output under
# WORK/run<i>/out. A trace is deleted once its run is replayed.
set -u
X=${1:?usage: replay.sh COPY_CHECK WORK}; W=${2:?}
CELL=${CELL:-2x1-offset-jitter:2:4:512:512:64:64:64:64:2:1:0:0:1:0:200}
RUNS=${RUNS:-5}; SAMPLE=${SAMPLE:-3}; REPEAT=${REPEAT:-2}; TIMEOUT=${TIMEOUT:-60}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONPATH=$REPO/analysis OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none OPENBLAS_NUM_THREADS=1
export OMPI_MCA_hwloc_base_binding_policy=none
T=$REPO/tracer/bin/openmpi/libstretto-trace.so
IFS=: read -r name np t m n mbs nbs mbd nbd p q sr sc dr dc jit <<< "$CELL"
args=("$m" "$n" "$mbs" "$nbs" "$mbd" "$nbd" "$p" "$q" "$sr" "$sc" "$dr" "$dc" 1 "$jit" --pika:threads="$t" --pika:bind=none)
mkdir -p "$W"
echo "run,result,stretto_rc,l1" > "$W/traced.csv"
out=$W/replay.csv; rm -f "$out"
for i in $(seq 1 "$RUNS"); do
  d=$W/run$i; rm -rf "$d"; mkdir -p "$d/trace"
  timeout 300 mpirun --bind-to none -np "$np" -x COPY_CHECK_TILES=1 -x STRETTO_TRACE_DIR="$d/trace" -x STRETTO_SYNC_ORDER=$((1 - ${NO_SYNC:-0})) \
    -x LD_PRELOAD="$T" "$X" "${args[@]}" > "$d/log" 2>&1
  w=$(sed -n 's/^summary reps [0-9]* wrong \([0-9]*\)$/\1/p' "$d/log")
  res=crash; if [ -n "$w" ]; then if [ "$w" -gt 0 ]; then res=wrong; else res=ok; fi; fi
  timeout 3600 python3 -m stretto analyze "$d/trace" > "$d/report.txt" 2> "$d/report.err"; rc=$?
  l1=$(grep '^\[' "$d/report.txt" | grep -c ' L1 ')
  echo "$i,$res,$rc,$l1" >> "$W/traced.csv"
  if [ "$res" != crash ] && [ "$l1" -gt 0 ]; then
    python3 "$REPO/experiments/replay_sample.py" --threads --repeat "$REPEAT" --sample "$SAMPLE" ${SEED:+--seed "$SEED"} \
      --cls 1f --grep 'WRONG' --timeout "$TIMEOUT" --keep-output "$d/out" --env COPY_CHECK_TILES=1 --out "$d/replay.csv" \
      "$d/trace" "$np" "$X" "${args[@]}" > "$d/replay.log" 2>&1
    [ -f "$out" ] || { head -1 "$d/replay.csv" | sed 's/^/run,/' > "$out"; }
    tail -n +2 "$d/replay.csv" | sed "s/^/$i,/" >> "$out"
  fi
  rm -rf "$d/trace"
done
cat "$W/traced.csv"
