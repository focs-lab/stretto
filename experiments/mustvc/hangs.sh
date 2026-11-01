#!/usr/bin/env bash
# Untraced runs of the three programs of MUST-vc's suite that are labelled correct but can
# deadlock (README, Notes), to count how often they hang. In the mustvc image (MPICH 4.0.2,
# clang 15 and libomp) or in the experiment image (Open MPI 4.1.6, gcc 13 and libgomp):
#   experiments/mustvc/hangs.sh TESTS OUT.csv WORK
# TESTS is the artifact's tests/ directory. Each program runs on 2 processes with the threads of
# its RUN line. Settings: RUNS (10), TIMEOUT (30 s; a run killed then counts as a hang), LABEL
# (the toolchain column, default from the MPI found).
set -u
T=${1:?usage: hangs.sh TESTS OUT.csv WORK}; OUT=${2:?}; W=${3:?}
RUNS=${RUNS:-10}; TO=${TIMEOUT:-30}
R=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || echo unknown)}
if [ ! -x /usr/bin/mpicxx.openmpi ]; then CXX=mpicxx; RUN=(mpiexec -n 2); L=${LABEL:-mpich-4.0.2+clang-15+libomp}
else CXX=/usr/bin/mpicxx.openmpi; RUN=(mpirun --oversubscribe -np 2 -x OMP_NUM_THREADS); L=${LABEL:-openmpi-4.1.6+gcc-13+libgomp}; fi
mkdir -p "$W"
[ -e "$OUT" ] || echo "program,toolchain,threads,run,result,seconds,commit" > "$OUT"
for p in own_checks/ThreadLevel/correct/ThreadLevel_serialized.cpp MPI-Corrbench/ordering/correct/two_collectives_4.c \
         own_checks/ConComm/correct/concurrent_communicator_tasking_locks.cpp; do
  n=$(basename "${p%.*}"); th=$(sed -n 's/.*OMP_NUM_THREADS=\([0-9]*\).*/\1/p' "$T/$p" | head -1)
  case $p in *.cpp) cc=$CXX;; *) cc=${CXX/mpicxx/mpicc};; esac
  $cc -g -O0 -fopenmp -I"$(dirname "$T/$p")" "$T/$p" -o "$W/$n" || continue
  for i in $(seq 1 "$RUNS"); do
    t0=$(date +%s.%N)
    OMP_NUM_THREADS=${th:-2} /usr/bin/timeout -k 5 "$TO" "${RUN[@]}" "$W/$n" > "$W/$n.$i.log" 2>&1 < /dev/null
    rc=$?; t1=$(date +%s.%N)
    case $rc in 0) r=finished;; 124|137) r=hang;; *) r=rc=$rc;; esac
    echo "$p,$L,${th:-2},$i,$r,$(echo "$t1 - $t0" | bc | awk '{printf "%.2f", $1}'),$COMMIT" >> "$OUT"
  done
done
