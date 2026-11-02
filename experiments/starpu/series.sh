#!/usr/bin/env bash
# Untraced series of large_set, the plain build against the build with large-set-dump.patch
# (build.sh with VARIANT=dump), runs of the two interleaved, on 4 ranks with Open MPI's eager
# limit at 0 payload bytes (TCP) and at the defaults, as eager.sh runs them. A run that does
# not end within TIMEOUT seconds is a hang. Run inside a container of the image built from
# Dockerfile here:
#   experiments/starpu/series.sh OUT.csv
# Settings: RUNS (20), TIMEOUT (30), MODES (ser 1), EAGER (default 0), VARIANTS (plain dump).
set -u
OUT=${1:?usage: series.sh OUT.csv}
RUNS=${RUNS:-20}; VARIANTS=${VARIANTS:-plain dump}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
for i in $(seq 1 "$RUNS"); do
  for v in $VARIANTS; do
    TESTS=large_set RUNS=1 TIMEOUT=${TIMEOUT:-30} MODES=${MODES:-ser 1} EAGER=${EAGER:-default 0} \
      TREE=spu-openmpi-$v VARIANT=$v "$REPO/experiments/starpu/eager.sh" "$OUT.part"
    tail -n +2 "$OUT.part" | sed "s/^large_set,$v,\([^,]*\),\([^,]*\),1,/large_set,$v,\1,\2,$i,/" >> "$OUT.rows"
    rm -f "$OUT.part"
  done
done
{ echo "test,variant,mode,eager,run,result,rc,seconds,commit"; cat "$OUT.rows"; } > "$OUT"
rm -f "$OUT.rows"
