#!/usr/bin/env bash
# Replay the CALU tag collision that L1 predicts (docs/threads.md, section 14): traced runs of
# wide_lu with the lock order recorded (STRETTO_SYNC_ORDER=1), and for each run that finished
# (its factor right or wrong) up to SAMPLE alternatives at the receives of the collision range
# (tags 20 to 29), each forced REPEAT times with the chain pins, order gates and lock gates of its
# horizon and REPEAT times as its control (experiments/replay_sample.py --threads). Run inside a
# container of the image of Dockerfile here, with the libraries of build-lib.sh:
#   experiments/slate/replay.sh LIBDIR DRIVER WORK
# Settings: RUNS (traced runs, 5), FIRST (the first run's number, 1), SAMPLE (2), REPEAT (3),
# TIMEOUT (120 s per run), GRID (p q, "2 2"), THREADS (OpenMP threads per rank in the traced runs,
# 4), REPLAY_THREADS (in the replays and controls, THREADS by default): a task that waits at a
# gate holds its thread, and with the trace's 4 every thread of a rank ends up waiting for a task
# no thread is free to start (P-T7, docs/threads.md section 14). RIGHT_ONLY=1 analyses and replays
# only the runs whose factor came out right, the others recorded with their result alone.
# CLASSES (for example "1a 1f") samples SAMPLE alternatives of each class in turn, and not
# SAMPLE of all.
# Writes WORK/traced.csv (run, factor, Stretto's findings) and WORK/replay.csv (replay_sample.py's
# columns with the run in front; "hits" counts the lines reporting a wrong factor); each replay's
# and control's output is kept under WORK/run<i>/out. A trace is deleted once its run is
# replayed, or at once if the run aborted. ONLY (a regular expression of the alternatives'
# titles, by default the collision range's tags) narrows the sample, OUT names the table
# (replay.csv).
set -u
L=${1:?usage: replay.sh LIBDIR DRIVER WORK}; D=${2:?}; W=${3:?}
RUNS=${RUNS:-5}; FIRST=${FIRST:-1}; SAMPLE=${SAMPLE:-2}; REPEAT=${REPEAT:-3}; TIMEOUT=${TIMEOUT:-120}
read -r P Q <<< "${GRID:-2 2}"
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONPATH=$REPO/analysis LD_LIBRARY_PATH="$L/plain"
export OPENBLAS_NUM_THREADS=1 OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
export OMP_NUM_THREADS=${THREADS:-4}
mkdir -p "$W"
[ -f "$W/traced.csv" ] || echo "run,result,stretto_rc,l1_collision,l1_collision_1a" > "$W/traced.csv"
out=$W/${OUT:-replay.csv}; [ "$FIRST" = 1 ] && rm -f "$out"
for i in $(seq "$FIRST" $((FIRST + RUNS - 1))); do
  d=$W/run$i; rm -rf "$d"; mkdir -p "$d/trace"
  log=$(timeout 300 mpirun -x OMP_NUM_THREADS -x LD_LIBRARY_PATH -x STRETTO_TRACE_DIR="$d/trace" -x STRETTO_SYNC_ORDER=1 \
        -x LD_PRELOAD="$REPO/tracer/bin/openmpi/libstretto-trace.so" -np 4 "$D" 1000 2000 100 "$P" "$Q" C 1 1 2>&1)
  res=$(echo "$log" | sed -n 's/^rep 0 .* \(ok\|WRONG\)$/\1/p')
  [ -z "$res" ] && res=crash:$(echo "$log" | grep -oE 'MPI_ERR_[A-Z_]+' | head -1)
  if [ "${RIGHT_ONLY:-0}" = 1 ] && [ "$res" != ok ]; then
    echo "$i,$res,,," >> "$W/traced.csv"; rm -rf "$d/trace"; continue
  fi
  timeout 3600 python3 -m stretto analyze "$d/trace" > "$d/report.txt" 2> "$d/report.err"; rc=$?
  l1=$(grep '^\[' "$d/report.txt" | grep ' L1 ' | grep -cE 'tag=2[0-9] ')
  l1a=$(grep '^\[' "$d/report.txt" | grep ' L1 ' | grep -E 'tag=2[0-9] ' | grep -c '^\[ *1a\]')
  echo "$i,$res,$rc,$l1,$l1a" >> "$W/traced.csv"
  if [ "${res#crash}" = "$res" ] && [ "$l1" -gt 0 ]; then
    for c in ${CLASSES:-all}; do
      cls=(); [ "$c" = all ] || cls=(--cls "$c")
      OMP_NUM_THREADS=${REPLAY_THREADS:-$OMP_NUM_THREADS} \
        python3 "$REPO/experiments/replay_sample.py" --threads --repeat "$REPEAT" --sample "$SAMPLE" ${SEED:+--seed "$SEED"} \
        --only "${ONLY:-tag=2[0-9] }" "${cls[@]}" --grep 'WRONG' --timeout "$TIMEOUT" --keep-output "$d/out/$c" \
        --out "$d/replay-$c.csv" "$d/trace" 4 "$D" 1000 2000 100 "$P" "$Q" C 1 1 > "$d/replay-$c.log" 2>&1
      [ -f "$out" ] || { head -1 "$d/replay-$c.csv" | sed 's/^/run,/' > "$out"; }
      tail -n +2 "$d/replay-$c.csv" | sed "s/^/$i,/" >> "$out"
    done
  fi
  rm -rf "$d/trace"
done
cat "$W/traced.csv"
