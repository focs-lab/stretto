#!/usr/bin/env bash
# Blind scan: traced runs of GA's and ComEx's shipped tests built with the MPI progress-thread
# port (build.sh), on 4 ranks of one machine with nodes emulated by libfakehost.so (fakehost.c:
# FAKEHOST_PPN ranks per node), and Stretto's report and call sites of each trace.
# Run inside a container of the image built from Dockerfile here, after build.sh, with a
# /dev/shm larger than Docker's 64 MB (ComEx maps its segments there; with the default the
# ComEx tests die of SIGBUS):
#   docker run --shm-size 4g ... ga-exp:24.04 /h/repo/experiments/ga/scan.sh WORK
# Settings: MPI (openmpi), VARIANT (plain), TESTS (paths under the build tree), PPNS (1 2),
# RUNS (3), NP (4), JOBS (analyses at once, 4), TIMEOUT (seconds per run, 300), ATIMEOUT
# (seconds per analysis, 3600), MAXMB (traces larger than this many MB are not analysed; their
# report.rc says "size"). Each run leaves WORK/<test>-<mpi>-ppn<p>-<i>/ with log, result,
# report.json, report.rc, report.err, sites.txt and the trace.
set -u
W=${1:?usage: scan.sh WORK}
MPI=${MPI:-openmpi}; VARIANT=${VARIANT:-plain}; PPNS=${PPNS:-1 2}; RUNS=${RUNS:-3}; NP=${NP:-4}
JOBS=${JOBS:-4}; TIMEOUT=${TIMEOUT:-300}; ATIMEOUT=${ATIMEOUT:-3600}; MAXMB=${MAXMB:-60}
# The default: GA's C tests but the benchmarks (perf2, sprs_perf, big, gemmtest, ...) and
# ComEx's tests for more than two processes.
g=global/testing
TESTS=${TESTS:-$g/testc.x $g/ntestc.x $g/mtest.x $g/nbtestc.x $g/lock.x $g/read_only.x $g/packc.x
  $g/unpackc.x $g/patch_enumc.x $g/scan_addc.x $g/scan_copyc.x $g/threadsafec.x
  $g/thread_perf_contig.x $g/thread_perf_strided.x $g/elempatch.x $g/getmem.x $g/normc.x
  $g/matrixc.x $g/ntestfc.x $g/print.x $g/testmult.x $g/testmatmultc.x $g/testmultrect.x
  $g/ga-mpi.x $g/comm_init.x $g/simple_groups_commc.x $g/sprs_test.x $g/cache_test.x $g/gatscat.x
  comex/testing/test comex/testing/shift comex/testing/perf_amo}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
B=/h/build/ga-$MPI-$VARIANT
export PYTHONPATH=$REPO/analysis OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
T=$REPO/tracer/bin/$MPI/libstretto-trace.so F=/h/build/libfakehost.so
[ -f $F ] || gcc -O2 -Wall -shared -fPIC -o $F "$REPO/experiments/ga/fakehost.c" -ldl
launch() {  # launch DIR PPN EXE: one traced run (Open MPI's mpirun starts MPICH's programs too, RUNBOOK)
  timeout "$TIMEOUT" mpirun --bind-to none -np "$NP" -x FAKEHOST_PPN="$2" -x STRETTO_TRACE_DIR="$1/trace" \
    -x STRETTO_SYNC_ORDER=1 -x LD_PRELOAD="$F:$T" "$3"
}
mkdir -p "$W"
for t in $TESTS; do
  n=$(basename "$t" .x)
  for p in $PPNS; do
    for i in $(seq 1 "$RUNS"); do
      d=$W/$n-$MPI-ppn$p-$i; rm -rf "$d"; mkdir -p "$d/trace"
      launch "$d" "$p" "$B/$t" > "$d/log" 2>&1; rc=$?
      res=pass; [ $rc = 124 ] && res=hang; [ $rc != 0 ] && [ $rc != 124 ] && res=fail
      grep -v 'Error=0\.0*$' "$d/log" | grep -qiE 'fail|error|wrong' && [ $res = pass ] && res=suspect
      echo "$res $rc" > "$d/result"
    done
  done
done
ls -d "$W"/*/trace | xargs -P "$JOBS" -I{} sh -c '
  d=$(dirname {}); [ -f "$d/report.json" ] && exit 0
  if [ $(du -sm {} | cut -f1) -gt '"$MAXMB"' ]; then echo size > "$d/report.rc"; exit 0; fi
  timeout '"$ATIMEOUT"' python3 -m stretto analyze --json {} > "$d/report.json" 2> "$d/report.err"; echo $? > "$d/report.rc"
  python3 -m stretto sites {} > "$d/sites.txt" 2>> "$d/report.err"'
