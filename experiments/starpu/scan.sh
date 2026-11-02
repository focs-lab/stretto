#!/usr/bin/env bash
# Blind scan: traced runs of StarPU's shipped MPI tests (build.sh) on NP ranks of one machine,
# each rank with NCPU CPU workers besides StarPU-MPI's communication thread, and Stretto's report
# and call sites of each trace. Three modes (SENDS): "ser", the tests as shipped (they initialise
# MPI with MPI_THREAD_SERIALIZED, mpi/tests/helper.h); "1" and "0", MPI_THREAD_MULTIPLE asked for
# instead (the shim mtinit.c, preloaded before the tracer) with STARPU_MPI_THREAD_MULTIPLE_SEND
# 1 (worker threads post their own sends; StarPU's default with Open MPI 4 and MPICH, which needs
# that level) or 0. Run inside a container of the image built from Dockerfile here, after
# build.sh:
#   docker run ... spu-exp:24.04 /h/repo/experiments/starpu/scan.sh WORK
# Settings: MPI (openmpi), TREE (the build tree's name, spu-$MPI-plain), TESTS (programs of the
# tree's mpi/tests; default: every one that make check would run), SENDS (ser 1 0), NPS (4), NCPU
# (2), RUNS (3), TIMEOUT (seconds per run, 120), KILLMB (a run whose trace passes this many MB
# is killed and its trace deleted, 600), MAXMB (larger traces are deleted after their run, not
# analysed, 300), KEEPMB (traces above this are deleted after their analysis, 60), ATIMEOUT
# (seconds per analysis, 300), JOBS (analyses at once, 2), AOPTS (options of stretto analyze,
# --no-order), SYNC (1: record the lock order for replays), RESUME (1: keep the runs of an
# earlier start that have a result). Each run leaves WORK/<test>-ms<send>-<mpi>-np<n>-<i>/
# with log, result (pass|fail|hang|skip|killed and the exit status; 77 is StarPU's "skipped"),
# trace.mb, report.json, report.rc ("size" or "killed" when not analysed), report.err,
# sites.txt and the trace; the test runs in the run's directory cwd/.
set -u
W=${1:?usage: scan.sh WORK}
MPI=${MPI:-openmpi}; TREE=${TREE:-spu-$MPI-plain}; SENDS=${SENDS:-ser 1 0}; NPS=${NPS:-4}; NCPU=${NCPU:-2}
RUNS=${RUNS:-3}; TIMEOUT=${TIMEOUT:-120}; KILLMB=${KILLMB:-600}; MAXMB=${MAXMB:-300}; KEEPMB=${KEEPMB:-60}
ATIMEOUT=${ATIMEOUT:-300}; JOBS=${JOBS:-2}; SYNC=${SYNC:-0}; AOPTS=${AOPTS:---no-order}; RESUME=${RESUME:-0}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
B=/h/build/$TREE
# the programs of starpu_mpi_TESTS as the tree's Makefile lists them (make check's list)
TESTS=${TESTS:-$(make -s -C "$B/mpi/tests" -f Makefile -f - print-tests <<< 'print-tests: ; @echo $(starpu_mpi_TESTS)' 2>/dev/null)}
export PYTHONPATH=$REPO/analysis OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
T=$REPO/tracer/bin/$MPI/libstretto-trace.so M=/h/build/libmtinit-$MPI.so
[ -f "$M" ] || /usr/bin/mpicc.$MPI -O2 -Wall -shared -fPIC -o "$M" "$REPO/experiments/starpu/mtinit.c" -ldl
L=$B/src/.libs:$B/mpi/src/.libs
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
for n in $TESTS; do
  exe=$B/mpi/tests/.libs/$n; [ -x "$exe" ] || exe=$B/mpi/tests/$n
  [ -x "$exe" ] || { echo "no $n" >&2; continue; }
  for ms in $SENDS; do
    for np in $NPS; do
      for i in $(seq 1 "$RUNS"); do
        d=$W/$n-ms$ms-$MPI-np$np-$i
        [ "$RESUME" = 1 ] && [ -e "$d/result" ] && continue
        rm -rf "$d"; mkdir -p "$d/trace" "$d/cwd"
        mode=(-x LD_PRELOAD="$T"); [ "$ms" != ser ] && mode=(-x LD_PRELOAD="$M:$T" -x STARPU_MPI_THREAD_MULTIPLE_SEND="$ms")
        (cd "$d/cwd" && exec timeout "$TIMEOUT" mpirun --bind-to none -np "$np" -x LD_LIBRARY_PATH="$L" \
           -x STARPU_NCPU="$NCPU" -x STARPU_MPI_NOBIND=1 -x STARPU_WORKERS_NOBIND=1 -x STARPU_SILENT=1 \
           -x STARPU_HOME="$d/cwd" -x STRETTO_TRACE_DIR="$d/trace" -x STRETTO_SYNC_ORDER="$SYNC" "${mode[@]}" \
           "$exe") > "$d/log" 2>&1 &
        pid=$!; killed=0
        while kill -0 $pid 2>/dev/null; do
          sleep 1
          if [ "$(du -sm "$d/trace" | cut -f1)" -gt "$KILLMB" ]; then
            killed=1; pkill -f "STRETTO_TRACE_DIR=$d/trace"; sleep 2; pkill -9 -f "STRETTO_TRACE_DIR=$d/trace"
            pkill -9 -f "$exe"; rm -rf "$d/trace"; break
          fi
        done
        wait $pid; rc=$?
        res=pass; [ $rc = 124 ] && res=hang; [ $rc = 77 ] && res=skip
        [ $rc != 0 ] && [ $rc != 124 ] && [ $rc != 77 ] && res=fail; [ $killed = 1 ] && res=killed
        echo "$res $rc" > "$d/result"
        if [ $killed = 1 ]; then echo "$KILLMB" > "$d/trace.mb"; echo killed > "$d/report.rc"; continue; fi
        mb=$(du -sm "$d/trace" | cut -f1); echo "$mb" > "$d/trace.mb"
        if [ "$mb" -gt "$MAXMB" ]; then echo size > "$d/report.rc"; rm -rf "$d/trace"; fi
      done
    done
  done
  # the test's analyses before the next test, smallest trace first, so traces do not pile up
  for d in "$W"/$n-ms*-$MPI-np*-*/; do [ -e "$d/report.rc" ] || echo "$(cat "$d/trace.mb") ${d%/}"; done | sort -n |
    awk '{print $2}' | xargs -r -P "$JOBS" -I{} bash -c 'analyse {}'
done
