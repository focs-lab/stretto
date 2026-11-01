#!/usr/bin/env bash
# Untraced runs of GA tests (build.sh's Open MPI tree) on 4 ranks with emulated nodes
# (fakehost.c), at Open MPI's default transports and with the eager threshold forced down to B
# payload bytes: the TCP transport over loopback with eager limit B + 56 (56 bytes are its
# header, experiments/results/b_impl.csv) and the self transport's alike, the setting of
# experiments/b_impl.py. A run that does not end within TIMEOUT seconds is a hang.
# Run inside a container of the image built from Dockerfile here (with --shm-size 4g):
#   experiments/ga/eager.sh OUT.csv
# Settings: TESTS (testc ga-mpi mtest), PPNS (1 2), EAGER (default 0 400: "default" is the
# library's own transports, a number B the forced setting), RUNS (10), TIMEOUT (60),
# VARIANT (plain).
set -u
OUT=${1:?usage: eager.sh OUT.csv}
TESTS=${TESTS:-testc ga-mpi mtest}; PPNS=${PPNS:-1 2}; EAGER=${EAGER:-default 0 400}; RUNS=${RUNS:-10}
TIMEOUT=${TIMEOUT:-60}; VARIANT=${VARIANT:-plain}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=$(python3 "$REPO/experiments/provenance.py")
B=/h/build/ga-openmpi-$VARIANT F=/h/build/libfakehost.so
[ -f $F ] || gcc -O2 -Wall -shared -fPIC -o $F "$REPO/experiments/ga/fakehost.c" -ldl
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
[ -f "$OUT" ] || echo "test,variant,ppn,eager,run,result,rc,seconds,commit" > "$OUT"
for i in $(seq 1 "$RUNS"); do
  for t in $TESTS; do
    for p in $PPNS; do
      for e in $EAGER; do
        mca=()
        if [ "$e" != default ]; then
          mca=(--mca btl self,tcp --mca btl_tcp_if_include lo --mca btl_tcp_eager_limit $((e + 56))
               --mca btl_self_eager_limit $((e + 56)))
        fi
        t0=$(date +%s.%N)
        timeout "$TIMEOUT" mpirun --bind-to none -np 4 "${mca[@]}" -x FAKEHOST_PPN="$p" -x LD_PRELOAD="$F" \
          "$B/global/testing/$t.x" > /tmp/eager.log 2>&1; rc=$?
        s=$(echo "$(date +%s.%N) - $t0" | bc)
        res=fail; [ $rc = 0 ] && res=pass; [ $rc = 124 ] && res=hang
        [ $res = pass ] && ! grep -qi 'success\|passed\|complete\|OK' /tmp/eager.log && res=suspect
        echo "$t,$VARIANT,$p,$e,$i,$res,$rc,$(printf %.1f "$s"),$COMMIT" >> "$OUT"
      done
    done
  done
done
