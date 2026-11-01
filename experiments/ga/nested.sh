#!/usr/bin/env bash
# nested_lock.c against the plain build and the build with lock-index.patch (build.sh with
# VARIANT=lockfix), Open MPI, 2 and 4 ranks, one emulated node per rank (FAKEHOST_PPN=1) or the
# machine as one node (4); RUNS runs each, TIMEOUT seconds a run. Run inside a container of the
# image built from Dockerfile here, with --shm-size 4g:
#   experiments/ga/nested.sh WORK OUT.csv
set -u
W=${1:?usage: nested.sh WORK OUT.csv}; OUT=${2:?}
RUNS=${RUNS:-5}; TIMEOUT=${TIMEOUT:-15}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=$(python3 "$REPO/experiments/provenance.py")
F=/h/build/libfakehost.so
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
mkdir -p "$W"
for v in plain lockfix; do
  B=/h/build/ga-openmpi-$v S=/h/build/ga-openmpi-$v-src
  mpicc.openmpi -O1 -I$B -I$B/gaf2c -I$B/global/src -I$B/ma -I$S/gaf2c -I$S/global/src -I$S/ma \
    -o "$W/nested_lock-$v" "$REPO/experiments/ga/nested_lock.c" $B/.libs/libga.a $B/comex/.libs/libarmci.a \
    $B/comex/.libs/libcomex.a -lm -lpthread
done
echo "variant,np,ppn,run,result,rc,commit" > "$OUT"
for i in $(seq 1 "$RUNS"); do
  for v in plain lockfix; do
    for np in 2 4; do
      for p in 1 4; do
        timeout "$TIMEOUT" mpirun --bind-to none -np $np -x FAKEHOST_PPN=$p -x LD_PRELOAD=$F "$W/nested_lock-$v" > "$W/log" 2>&1
        rc=$?; r=fail; [ $rc = 124 ] && r=hang; [ $rc = 0 ] && grep -q "nested ok" "$W/log" && r=pass
        echo "$v,$np,$p,$i,$r,$rc,$COMMIT" >> "$OUT"
      done
    done
  done
done
