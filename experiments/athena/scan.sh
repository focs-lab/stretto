#!/usr/bin/env bash
# Blind scan: Athena++'s shipped regression tests that run MPI (tst/regression/scripts/tests),
# built with -mpi -omp and run by Athena++'s own run_tests.py, every mpirun call traced (the
# mpirun of this directory, first in PATH: a trace directory per call) and analysed by Stretto.
# The tests that run one thread set their runs to OMP threads (run_tests.py --run
# mesh/num_threads=OMP; the inputs without that parameter get it, set to 1, in their <mesh>
# block); hybrid/hybrid_linwave sets its own (1 and 2). Run inside a container
# of the image built from Dockerfile here:
#   docker run ... ath-exp:24.04 /h/repo/experiments/athena/scan.sh WORK
# Settings: MPI (openmpi), TESTS (test names of run_tests.py), RUNS (3), OMP (2), TIMEOUT
# (seconds per mpirun call, 600), KILLMB (a call whose trace passes it is killed, 1500), MAXMB (traces above this are not analysed, 400), KEEPMB
# (traces above this are deleted after their analysis, 60), ATIMEOUT (seconds per analysis,
# 3600), JOBS (analyses at once, 3), VARIANT (plain) and PATCH (a patch for another variant).
# Each run leaves WORK/<test>-<mpi>-<i>/ with tests.log (run_tests.py's output), result
# (pass|fail and the exit status), and calls/<n>/ per mpirun call with cmd, the trace,
# report.json, report.rc, report.err, sites.txt, trace.mb.
set -u
W=${1:?usage: scan.sh WORK}
MPI=${MPI:-openmpi}; RUNS=${RUNS:-3}; OMP=${OMP:-2}; TIMEOUT=${TIMEOUT:-600}; MAXMB=${MAXMB:-400}
KEEPMB=${KEEPMB:-60}; ATIMEOUT=${ATIMEOUT:-3600}; JOBS=${JOBS:-3}; VARIANT=${VARIANT:-plain}
TESTS=${TESTS:-hybrid/hybrid_linwave mpi/mpi_linwave grav/jeans_3d turb/turb_3d fft/fft}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
S=/h/src/athena B=/h/build/athena-$MPI-$VARIANT
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none TIMEOUT
export PATH=$REPO/experiments/athena:$PATH PYTHONPATH=$REPO/analysis
make -s -C "$REPO/tracer" MPI="$MPI" > /dev/null && make -s -C "$REPO/replay" MPI="$MPI" > /dev/null
rm -rf "$B" && cp -r "$S" "$B" && rm -rf "$B/.git"
if [ "$VARIANT" != plain ]; then patch -s -d "$B" -p1 < "${PATCH:?PATCH names the patch of the variant}"; fi
# Athena++ takes a parameter on the command line only if the input file has it: the inputs
# without mesh/num_threads get "num_threads = 1" (the default) in their <mesh> block
for f in "$B"/inputs/*/athinput.*; do
  grep -q num_threads "$f" || sed -i '/^<mesh>/a num_threads = 1   # added by the scan: the command line sets it' "$f"
done
cfg=(--config=-omp --config=--cflag=-g)
[ "$MPI" = mpich ] && cfg+=(--config=--mpiccmd=mpicxx.mpich)
mkdir -p "$W"
for t in $TESTS; do
  for i in $(seq 1 "$RUNS"); do
    d=$W/${t//\//_}-$MPI-$i; rm -rf "$d"; mkdir -p "$d/calls"
    run=(); [ "$t" != hybrid/hybrid_linwave ] && run=(--run=mesh/num_threads=$OMP)
    (cd "$B/tst/regression" && TRACES=$d/calls TRACER=$REPO/tracer/bin/$MPI/libstretto-trace.so \
       python3 run_tests.py "$t" "${cfg[@]}" "${run[@]}" > "$d/tests.log" 2>&1); rc=$?
    res=pass; [ $rc != 0 ] && res=fail
    echo "$res $rc" > "$d/result"
    for c in "$d"/calls/*/; do
      m=$(du -sm "$c" | cut -f1); echo "$m" > "$c/trace.mb"
      [ -e "$c/killed" ] && { echo killed > "$c/report.rc"; continue; }
      ls "$c"/rank-*.jsonl > /dev/null 2>&1 || { echo none > "$c/report.rc"; continue; }
      [ "$m" -gt "$MAXMB" ] && { echo size > "$c/report.rc"; rm -f "$c"/rank-*.jsonl "$c"/sync-*.txt; }
    done
    # the run's analyses before the next run, smallest trace first
    for c in "$d"/calls/*/; do [ -e "$c/report.rc" ] || echo "$(cat "$c/trace.mb") $c"; done | sort -n |
      awk '{print $2}' | xargs -r -P "$JOBS" -I{} sh -c '
        c={}; timeout '"$ATIMEOUT"' python3 -m stretto analyze --json "$c" > "$c/report.json" 2> "$c/report.err"; echo $? > "$c/report.rc"
        python3 -m stretto sites "$c" > "$c/sites.txt" 2>> "$c/report.err"
        [ "$(cat "$c/trace.mb")" -gt '"$KEEPMB"' ] && rm -f "$c"/rank-*.jsonl "$c"/sync-*.txt; exit 0'
  done
done
