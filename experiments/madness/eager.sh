#!/usr/bin/env bash
# Untraced runs of the tests whose traces have an observed 2-buffering (the scan's O1) with Open
# MPI's eager limit forced down to 0 payload bytes (TCP, as experiments/ga/eager.sh) and at the
# defaults. A run that does not end within TIMEOUT seconds is a hang. Run inside a container of
# the image built from Dockerfile here:
#   experiments/madness/eager.sh OUT.csv
# Settings: TESTS (test:ranks, under the tree's src/madness), EAGER (default 0), RUNS (5),
# TIMEOUT (120), TREE (mad-openmpi-plain), THREADS (2).
set -u
OUT=${1:?usage: eager.sh OUT.csv}
TESTS=${TESTS:-world/test_tree:2 world/test_hashdc:4 world/test_dc:2 mra/test_cloud:2}
EAGER=${EAGER:-default 0}; RUNS=${RUNS:-5}; TIMEOUT=${TIMEOUT:-120}; TREE=${TREE:-mad-openmpi-plain}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=$(python3 "$REPO/experiments/provenance.py")
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
[ -f "$OUT" ] || echo "test,np,eager,run,result,rc,seconds,commit" > "$OUT"
log=$(mktemp); cwd=$(mktemp -d)
for i in $(seq 1 "$RUNS"); do
  for x in $TESTS; do
    t=${x%:*} np=${x#*:}
    for e in $EAGER; do
      mca=()
      [ "$e" != default ] && mca=(--mca btl self,tcp --mca btl_tcp_if_include lo
                                  --mca btl_tcp_eager_limit $((e + 56)) --mca btl_self_eager_limit $((e + 56)))
      t0=$(date +%s.%N)
      (cd "$cwd" && timeout "$TIMEOUT" mpirun --bind-to none -np "$np" "${mca[@]}" -x MAD_NUM_THREADS="${THREADS:-2}" \
         -x MAD_CHECK_BINDING=OFF "/h/build/$TREE/src/madness/$t") > "$log" 2>&1; rc=$?
      s=$(echo "$(date +%s.%N) - $t0" | bc)
      res=fail; [ $rc = 0 ] && res=pass; [ $rc = 124 ] && res=hang
      echo "$(basename "$t"),$np,$e,$i,$res,$rc,$(printf %.1f "$s"),$COMMIT" >> "$OUT"
    done
  done
done
rm -rf "$log" "$cwd"
