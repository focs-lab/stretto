#!/usr/bin/env bash
# Forced replays of the L1+L findings (docs/threads.md, sections 13 and 19): the programs whose
# MPI calls lie in critical sections that another execution enters in the other order. Idioms
# 22, 23, 27 and 43, MPI-CorrBench a05644b's two_collectives_8, two_collectives_9 and
# dependant/deadlock_probe (and the copy labelled correct, a real deadlock), and MUST-vc's own
# ThreadLevel_serialized and two_collectives_4 (labelled correct; both can deadlock). Each is
# built as stretto_bench.sh builds it, traced with the lock order (STRETTO_SYNC_ORDER=1) until
# RUNS runs have finished (at most TRIES attempts: ThreadLevel_serialized hangs in most runs),
# analysed, and every L1+L finding of each finished trace replayed REPEAT times with its control
# (experiments/replay_sample.py --threads --levels L1+L). Inside the experiment image with this
# repository built (make -C tracer, make -C idioms):
#   experiments/mustvc/lock_replays.sh CORRBENCH ARTIFACT_TESTS WORK OUT.csv
# CORRBENCH: MPI-CorrBench's micro-benches/0-level/openmp; ARTIFACT_TESTS: the MUST-vc
# artifact's tests/. Settings: RUNS (2), TRIES (10), REPEAT (2), TIMEOUT (30 s per run), ONLY (a
# regular expression on the program's name). Rows: program, run, then replay_sample.py's columns;
# "hits" counts the lines with "wrong" (the idioms' own checks).
set -u
CB=${1:?usage: lock_replays.sh CORRBENCH ARTIFACT_TESTS WORK OUT.csv}; AT=${2:?}; W=${3:?}; OUT=${4:?}
RUNS=${RUNS:-2}; TRIES=${TRIES:-10}; REPEAT=${REPEAT:-2}; TO=${TIMEOUT:-30}
R=$(cd "$(dirname "$0")/../.." && pwd)
LIB=$R/tracer/bin/openmpi/libstretto-trace.so
[ -f "$LIB" ] || { echo "no tracer at $LIB" >&2; exit 1; }
export PYTHONPATH=$R/analysis OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
# name:source:np:threads (an idiom's source is its directory, built by make -C idioms)
progs=(
  idiom22:idioms/22_threads_senders_mutex:3:0 idiom23:idioms/23_threads_probe_recv_critical:3:0
  idiom27:idioms/27_threads_lock_held_deadlock:3:0 idiom43:idioms/43_omp_critical_collectives:3:2
  cb-two_collectives_8:$CB/ordering/two_collectives_8.c:2:2 cb-two_collectives_9:$CB/ordering/two_collectives_9.c:2:2
  cb-deadlock_probe:$CB/ordering/dependant/deadlock_probe.c:2:2
  cb-correct-deadlock_probe:$CB/ordering/correct/dependant/deadlock_probe.c:2:2
  mvc-ThreadLevel_serialized:$AT/own_checks/ThreadLevel/correct/ThreadLevel_serialized.cpp:2:4
  mvc-two_collectives_4:$AT/MPI-Corrbench/ordering/correct/two_collectives_4.c:2:2
)
mkdir -p "$W/bin"
rm -f "$OUT"
for p in "${progs[@]}"; do
  IFS=: read -r name src np th <<< "$p"
  [[ $name =~ ${ONLY:-.} ]] || continue
  case $src in
    idioms/*) exe=$R/$src/bin/openmpi/main;;
    *.cpp) exe=$W/bin/$name; /usr/bin/mpicxx.openmpi -g -O0 -fopenmp -I"$(dirname "$src")" -I"$CB" "$src" -o "$exe" -lm > "$exe.log" 2>&1;;
    *) exe=$W/bin/$name; /usr/bin/mpicc.openmpi -g -O0 -fopenmp -I"$(dirname "$src")" -I"$CB" "$src" -o "$exe" -lm > "$exe.log" 2>&1;;
  esac
  [ -x "$exe" ] || { echo "$name: not built" >&2; continue; }
  envs=(); [ "$th" -gt 0 ] && envs=(OMP_NUM_THREADS="$th")
  done_runs=0
  for k in $(seq 1 "$TRIES"); do
    [ "$done_runs" -ge "$RUNS" ] && break
    d=$W/$name/$k; rm -rf "$d"; mkdir -p "$d/trace" "$d/run"
    (cd "$d/run" && env "${envs[@]}" timeout -k 5 "$TO" mpirun -np "$np" ${envs[@]+-x OMP_NUM_THREADS} -x LD_PRELOAD="$LIB" \
      -x STRETTO_TRACE_DIR="$d/trace" -x STRETTO_SYNC_ORDER=1 "$exe" > ../stdout.log 2> ../stderr.log < /dev/null)
    rc=$?
    echo "$name try $k: rc $rc"
    [ $rc = 0 ] || continue
    done_runs=$((done_runs + 1))
    (cd "$d/run" && env "${envs[@]}" python3 "$R/experiments/replay_sample.py" --threads --levels L1+L --sample 50 \
      --repeat "$REPEAT" --grep wrong --timeout "$TO" --keep-output "$d/out" ${envs[@]+--env "${envs[0]}"} --out "$d/replay.csv" \
      "$d/trace" "$np" "$exe" \
      > "$d/replay.log" 2>&1)
    [ -f "$d/replay.csv" ] || continue
    [ -f "$OUT" ] || { head -1 "$d/replay.csv" | sed 's/^/program,run,/' > "$OUT"; }
    tail -n +2 "$d/replay.csv" | sed "s/^/$name,$k,/" >> "$OUT"
    rm -rf "$d/trace"
  done
done
