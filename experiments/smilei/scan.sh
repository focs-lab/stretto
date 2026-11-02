#!/usr/bin/env bash
# Blind scan: traced runs of Smilei's shipped benchmarks (benchmarks/tst*.py, each with its
# validation script and reference in validation/), NP ranks of OMP OpenMP threads, each run
# checked against its reference by Smilei's own validation (validate.py here), and Stretto's
# report and call sites of each trace. Run inside a container of the image built from
# Dockerfile here, after build.sh:
#   docker run ... smi-exp:24.04 /h/repo/experiments/smilei/scan.sh WORK
# Settings: MPI (openmpi), VARIANT (plain), BENCHES (names under benchmarks/, default every
# one with a validation script), RUNS (3), NP (4), OMP (2), TIMEOUT (seconds per run, 600),
# KILLMB (a run whose trace passes this many MB is killed, 1500), MAXMB (a trace larger than
# this is not analysed, 400), KEEPMB (a trace larger than this is deleted after its analysis,
# 60), JOBS (analyses at once, beside the runs, 4), ANAMB (MB of traces under analysis at once,
# 1400: an analysis takes about 15 times its trace in memory), PENDMB (the runs wait while the
# traces on disk pass this many MB, 6000), ATIMEOUT (seconds per analysis, 3600), PAR (runs at
# once, 2), ANALYSE (1; 0 runs only). The tracer runs with STRETTO_OMP_CHAINS=thread and
# STRETTO_MPIIO=coll (docs/threads.md, section 18). Each run leaves WORK/<bench>-<mpi>-<i>/
# with log (Smilei's output), result ("pass|fail|hang|killed|invalid rc"), valid.txt (the
# validation's output), report.json, report.rc, report.err, sites.txt, trace.mb and the trace
# (until deleted).
set -u
W=${1:?usage: scan.sh WORK}
MPI=${MPI:-openmpi}; VARIANT=${VARIANT:-plain}; RUNS=${RUNS:-3}; NP=${NP:-4}; OMP=${OMP:-2}
TIMEOUT=${TIMEOUT:-600}; KILLMB=${KILLMB:-1500}; MAXMB=${MAXMB:-400}; KEEPMB=${KEEPMB:-60}
JOBS=${JOBS:-4}; ATIMEOUT=${ATIMEOUT:-3600}; PAR=${PAR:-2}; ANALYSE=${ANALYSE:-1}
ANAMB=${ANAMB:-1400}; PENDMB=${PENDMB:-6000}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
B=/h/build/smilei-$MPI-$VARIANT
BENCHES=${BENCHES:-$(cd $B/validation/analyses && ls validate_tst*.py | sed 's/^validate_//')}
T=$REPO/tracer/bin/$MPI/libstretto-trace.so
export REPO B T NP OMP TIMEOUT KILLMB MAXMB MPI W ATIMEOUT KEEPMB
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
one() {  # one BENCH RUN: a traced run, its validation, the trace's size
  n=${1%.py}; d=$W/$n-$MPI-$2; rm -rf "$d"; mkdir -p "$d/trace" "$d/sim/restart000"
  ( cd "$d/sim/restart000" && exec timeout "$TIMEOUT" mpirun --bind-to none -np "$NP" \
      -x OMP_NUM_THREADS="$OMP" -x OMP_SCHEDULE=dynamic -x STRETTO_TRACE_DIR="$d/trace" \
      -x STRETTO_SYNC_ORDER=1 -x STRETTO_OMP_CHAINS=thread -x STRETTO_MPIIO=coll -x LD_PRELOAD="$T" \
      "$B/smilei" "$B/benchmarks/$1" ) > "$d/log" 2>&1 &
  pid=$!; killed=0
  while kill -0 $pid 2>/dev/null; do
    sleep 5
    if [ "$(du -sm "$d/trace" | cut -f1)" -gt "$KILLMB" ]; then killed=1; kill $pid; sleep 5; kill -9 $pid 2>/dev/null; pkill -9 -f "$d/trace"; fi
  done
  wait $pid; rc=$?
  res=pass; [ $rc = 124 ] && res=hang; [ $rc != 0 ] && [ $rc != 124 ] && res=fail; [ $killed = 1 ] && res=killed
  if [ $res = pass ]; then
    (cd $B/validation && PYTHONPATH=$B timeout 600 python3 "$REPO/experiments/smilei/validate.py" "$1" "$d/sim") > "$d/valid.txt" 2>&1
    v=$?; [ $v = 0 ] || res=invalid
  fi
  du -sm "$d/trace" | cut -f1 > "$d/trace.mb"
  rm -rf "$d/sim"                                   # Smilei's output is checked, not kept
  m=$(cat "$d/trace.mb")
  if [ "$m" -gt "$MAXMB" ] || [ $res = killed ]; then echo size > "$d/report.rc"; rm -rf "$d/trace"; fi
  echo "$res $rc" > "$d/result"                     # last: the analyser takes the trace from here
}
ana() {  # ana TRACE: Stretto's report and call sites; a trace above KEEPMB is deleted after
  d=$(dirname "$1")
  PYTHONPATH=$REPO/analysis timeout "$ATIMEOUT" python3 -m stretto analyze --json "$1" > "$d/report.json" 2> "$d/report.err"
  echo $? > "$d/report.rc"
  PYTHONPATH=$REPO/analysis python3 -m stretto sites "$1" > "$d/sites.txt" 2>> "$d/report.err"
  [ "$(cat "$d/trace.mb")" -gt "$KEEPMB" ] && rm -rf "$1"
  return 0
}
export -f one ana
# The analyses run beside the runs, smallest trace first, at most JOBS at once and at most
# ANAMB MB of traces at once; the runs wait while the traces on disk pass PENDMB.
analyser() {
  local busy=$W/.busy-$MPI f t d m n used next nm
  mkdir -p "$busy"
  while :; do
    for f in "$busy"/*; do [ -e "$f" ] || continue; kill -0 "$(basename "$f")" 2>/dev/null || rm -f "$f"; done
    n=0; used=0
    for f in "$busy"/*; do [ -e "$f" ] || continue; n=$((n + 1)); used=$((used + $(cat "$f"))); done
    next=""; nm=0
    for t in "$W"/*-"$MPI"-*/trace; do
      d=$(dirname "$t")
      [ -d "$t" ] && [ -f "$d/result" ] && [ ! -e "$d/report.rc" ] && [ ! -e "$d/.taken" ] || continue
      m=$(cat "$d/trace.mb")
      if [ -z "$next" ] || [ "$m" -lt "$nm" ]; then next=$t; nm=$m; fi
    done
    if [ -n "$next" ] && [ "$n" -lt "$JOBS" ] && { [ "$n" = 0 ] || [ $((used + nm)) -le "$ANAMB" ]; }; then
      touch "$(dirname "$next")/.taken"; ana "$next" & echo "$nm" > "$busy/$!"; continue
    fi
    [ -z "$next" ] && [ "$n" = 0 ] && [ -e "$W/.runs-done-$MPI" ] && break
    sleep 5
  done
  wait
}
mkdir -p "$W"; rm -f "$W/.runs-done-$MPI"
[ "$ANALYSE" = 1 ] && { analyser & apid=$!; }
pending() {  # MB of this MPI's traces still to be analysed
  local t s=0
  for t in "$W"/*-"$MPI"-*/trace; do [ -d "$t" ] && [ ! -e "$(dirname "$t")/report.rc" ] && s=$((s + $(du -sm "$t" | cut -f1))); done
  echo $s
}
for b in $BENCHES; do
  while [ "$(pending)" -gt "$PENDMB" ]; do sleep 10; done
  for i in $(seq 1 "$RUNS"); do echo "$b $i"; done | xargs -P "$PAR" -n 2 bash -c 'one "$0" "$1"'
done
touch "$W/.runs-done-$MPI"
[ "$ANALYSE" = 1 ] && wait $apid
exit 0
