#!/usr/bin/env bash
# Untraced series of GA tests, plain build against the build with nbtest-progress.patch
# (build.sh with VARIANT=fixed), runs of the two interleaved, on 4 ranks with emulated nodes
# (fakehost.c; FAKEHOST_PPN=4 is one node, the machine as it is). A run that does not end
# within TIMEOUT seconds is a hang. Run inside a container of the image built from Dockerfile
# here, with --shm-size 4g:
#   experiments/ga/series.sh OUT.csv
# Settings: MPI (openmpi), TESTS (nbtestc), PPNS (1 2 4), VARIANTS (plain fixed), RUNS (20),
# TIMEOUT (30).
set -u
OUT=${1:?usage: series.sh OUT.csv}
MPI=${MPI:-openmpi}; TESTS=${TESTS:-nbtestc}; PPNS=${PPNS:-1 2 4}; VARIANTS=${VARIANTS:-plain fixed}
RUNS=${RUNS:-20}; TIMEOUT=${TIMEOUT:-30}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=$(python3 "$REPO/experiments/provenance.py")
F=/h/build/libfakehost.so
[ -f $F ] || gcc -O2 -Wall -shared -fPIC -o $F "$REPO/experiments/ga/fakehost.c" -ldl
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
[ -f "$OUT" ] || echo "test,mpi,variant,ppn,run,result,rc,ok_lines,seconds,commit" > "$OUT"
log=$(mktemp)
for i in $(seq 1 "$RUNS"); do
  for t in $TESTS; do
    for p in $PPNS; do
      for v in $VARIANTS; do
        exe=/h/build/ga-$MPI-$v/global/testing/$t.x
        [ -x "$exe" ] || exe=/h/build/ga-$MPI-$v/comex/testing/$t
        t0=$(date +%s.%N)
        timeout "$TIMEOUT" mpirun --bind-to none -np 4 -x FAKEHOST_PPN="$p" -x LD_PRELOAD="$F" "$exe" > "$log" 2>&1
        rc=$?
        s=$(echo "$(date +%s.%N) - $t0" | bc)
        res=fail; [ $rc = 0 ] && res=pass; [ $rc = 124 ] && res=hang
        echo "$t,$MPI,$v,$p,$i,$res,$rc,$(grep -ci 'is OK\|successful' "$log"),$(printf %.1f "$s"),$COMMIT" >> "$OUT"
      done
    done
  done
done
rm -f "$log"
