#!/usr/bin/env bash
# Traced runs of the wide CALU factorisation, plain and patched in turn: Stretto's report and the
# overlap scan (overlap.py) of each trace.
#   experiments/slate/traced.sh LIBDIR DRIVER WORK OUT.csv
# Settings: RUNS (traced runs per variant, 5). Traces are deleted after the scan.
set -u
L=${1:?usage: traced.sh LIBDIR DRIVER WORK OUT.csv}; D=${2:?}; W=${3:?}; OUT=${4:?}
RUNS=${RUNS:-5}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export OPENBLAS_NUM_THREADS=1 OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none OMP_NUM_THREADS=4
echo "variant,run,result,overlap_pairs,stretto_status,stretto_findings" > "$OUT"
for i in $(seq 1 "$RUNS"); do
  for v in plain fixed; do
    d=$W/traced-$v-$i; rm -rf "$d"; mkdir -p "$d/trace"
    res=$(timeout 300 mpirun -x OMP_NUM_THREADS -x LD_LIBRARY_PATH="$L/$v" -x STRETTO_TRACE_DIR="$d/trace" \
          -x LD_PRELOAD="$REPO/tracer/bin/openmpi/libstretto-trace.so" -np 4 "$D" 1000 2000 100 2 2 C 1 1 2>&1 |
          sed -n 's/^rep 0 .* \(ok\|WRONG\)$/\1/p')
    pairs=$(STEP=2 python3 -I "$REPO/experiments/slate/overlap.py" "$d/trace" | sed -n 's/^overlapping .*: //p')
    PYTHONPATH=$REPO/analysis python3 -m stretto analyze "$d/trace" > "$d/report.txt" 2>&1
    st=$(sed -n 's/^status: \([^;]*\).*/\1/p' "$d/report.txt" | tr , +)
    nf=$(grep -c '^\[' "$d/report.txt")
    echo "$v,$i,${res:-crash},$pairs,$st,$nf" >> "$OUT"
    rm -rf "$d/trace"
  done
done
cat "$OUT"
