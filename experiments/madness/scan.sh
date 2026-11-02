#!/usr/bin/env bash
# Blind scan: traced runs of MADNESS's shipped unit tests (build.sh) on NP ranks of one machine,
# each rank with a pool of MAD_NUM_THREADS threads besides its main thread and the runtime's
# communication thread, and Stretto's report and call sites of each trace. Run inside a
# container of the image built from Dockerfile here, after build.sh:
#   docker run ... mad-exp:24.04 /h/repo/experiments/madness/scan.sh WORK
# Settings: MPI (openmpi), TREE (the build tree's name, mad-$MPI-plain; mad-$MPI-plain-tsan
# for the build whose atomics the tracer records), TESTS (executables under the tree's
# src/madness), NPS (2 4), THREADS (MAD_NUM_THREADS, 2), RUNS (3), TIMEOUT (seconds per run,
# 300), KILLMB (a run whose trace passes this many MB is killed and its trace deleted, 800),
# MAXMB (larger traces are deleted after their run, not analysed, 300), KEEPMB (traces above
# this are deleted after their analysis, 60), ATIMEOUT (seconds per analysis, 3600), JOBS
# (analyses at once, 2), AOPTS (options of stretto analyze, --no-order: the communication
# thread's Testsome records have hundreds of thousands of completion-order candidates, which
# are counted, not judged), SYNC (1: record the lock order for replays, STRETTO_SYNC_ORDER; 0
# by default, since MADNESS's locks make it the larger part of a trace), RESUME (1: keep the
# runs of an earlier start that have a result, and analyse only the traces without a report).
# Each run leaves WORK/<test>-<mpi>-np<n>-<i>/
# with log, result (pass|fail|hang|killed and the exit status), trace.mb, report.json,
# report.rc ("size" or "killed" when not analysed), report.err, sites.txt and the trace; the
# test runs in the run's directory cwd/ (some tests write files).
set -u
W=${1:?usage: scan.sh WORK}
MPI=${MPI:-openmpi}; TREE=${TREE:-mad-$MPI-plain}; NPS=${NPS:-2 4}; THREADS=${THREADS:-2}; RUNS=${RUNS:-3}
TIMEOUT=${TIMEOUT:-300}; KILLMB=${KILLMB:-800}; MAXMB=${MAXMB:-300}; KEEPMB=${KEEPMB:-60}
ATIMEOUT=${ATIMEOUT:-3600}; JOBS=${JOBS:-2}; SYNC=${SYNC:-0}; AOPTS=${AOPTS:---no-order}; RESUME=${RESUME:-0}
w=world/test_ m=mra/test_
TESTS=${TESTS:-${w}world ${w}dc ${w}hashdc ${w}hello ${w}future ${w}future2 ${w}future3 ${w}hashthreaded
  ${w}queue ${w}replication ${w}worldref ${w}worldptr ${w}tree ${w}ar ${w}atomicint ${w}stack
  ${w}vector ${w}binsorter ${w}thread_specific ${w}prof ${w}worldprofile ${m}cloud ${m}eval ${m}halo
  ${m}vectormacrotask ${m}mul_sparse ${m}memory_measurement}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
B=/h/build/$TREE
export PYTHONPATH=$REPO/analysis OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
T=$REPO/tracer/bin/$MPI/libstretto-trace.so
analyse() {  # analyse RUNDIR: report and sites of a kept trace, the trace deleted above KEEPMB
  local d=$1
  timeout "$ATIMEOUT" python3 -m stretto analyze --json $AOPTS "$d/trace" > "$d/report.json" 2> "$d/report.err"
  echo $? > "$d/report.rc"
  python3 -m stretto sites "$d/trace" > "$d/sites.txt" 2>> "$d/report.err"
  [ "$(cat "$d/trace.mb")" -gt "$KEEPMB" ] && rm -rf "$d/trace"
  return 0
}
export -f analyse; export ATIMEOUT KEEPMB AOPTS
mkdir -p "$W"
for t in $TESTS; do
  n=$(basename "$t"); exe=$B/src/madness/$t
  [ -x "$exe" ] || { echo "no $exe" >&2; continue; }
  for np in $NPS; do
    for i in $(seq 1 "$RUNS"); do
      d=$W/$n-$MPI-np$np-$i
      [ "$RESUME" = 1 ] && [ -e "$d/result" ] && continue      # a run of an earlier start
      rm -rf "$d"; mkdir -p "$d/trace" "$d/cwd"
      (cd "$d/cwd" && exec timeout "$TIMEOUT" mpirun --bind-to none -np "$np" -x MAD_NUM_THREADS="$THREADS" \
         -x MAD_CHECK_BINDING=OFF -x STRETTO_TRACE_DIR="$d/trace" -x STRETTO_SYNC_ORDER="$SYNC" \
         -x LD_PRELOAD="$T" "$exe") > "$d/log" 2>&1 &
      pid=$!; killed=0
      while kill -0 $pid 2>/dev/null; do
        sleep 2
        if [ "$(du -sm "$d/trace" | cut -f1)" -gt "$KILLMB" ]; then
          killed=1; pkill -f "STRETTO_TRACE_DIR=$d/trace"; sleep 2; pkill -9 -f "STRETTO_TRACE_DIR=$d/trace"
          pkill -9 -f "$exe"; rm -rf "$d/trace"; break
        fi
      done
      wait $pid; rc=$?
      res=pass; [ $rc = 124 ] && res=hang; [ $rc != 0 ] && [ $rc != 124 ] && res=fail; [ $killed = 1 ] && res=killed
      echo "$res $rc" > "$d/result"
      if [ $killed = 1 ]; then echo "$KILLMB" > "$d/trace.mb"; echo killed > "$d/report.rc"; continue; fi
      mb=$(du -sm "$d/trace" | cut -f1); echo "$mb" > "$d/trace.mb"
      if [ "$mb" -gt "$MAXMB" ]; then echo size > "$d/report.rc"; rm -rf "$d/trace"; fi
    done
  done
  # the test's analyses before the next test, smallest trace first, so traces do not pile up
  for d in "$W"/$n-$MPI-np*-*/; do [ -e "$d/report.rc" ] || echo "$(cat "$d/trace.mb") ${d%/}"; done | sort -n |
    awk '{print $2}' | xargs -r -P "$JOBS" -I{} bash -c 'analyse {}'
done
