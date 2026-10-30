#!/usr/bin/env bash
# Interleaved series: SLATE's getrf with tournament pivoting (MethodLU::CALU) on 4 ranks, with
# the plain library and with tntpiv-tags.patch applied, one run of each in turn, checked by the
# residual of wide_lu (a run counts as wrong when || P A - L U ||_max / (|| A ||_max m) > 1e-12).
# Run inside a container of the image built from Dockerfile here, after build.sh:
#   experiments/slate/series.sh LIBDIR DRIVER OUT.csv
# LIBDIR holds plain/libslate.so.2 and fixed/libslate.so.2; DRIVER is the built wide_lu.
# Settings: RUNS (runs per cell, 40), THREADS (OpenMP threads per rank, 4).
set -u
L=${1:?usage: series.sh LIBDIR DRIVER OUT.csv}; D=${2:?}; OUT=${3:?}
RUNS=${RUNS:-40}; THREADS=${THREADS:-4}
export OPENBLAS_NUM_THREADS=1 OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
echo "cell,variant,run,m,n,nb,grid,threads,rc,residual,result" > "$OUT"
one() {   # cell variant run m n grid threads
  local cell=$1 v=$2 i=$3 m=$4 n=$5 g=$6 t=$7 log
  log=$(OMP_NUM_THREADS=$t timeout 120 mpirun -x OMP_NUM_THREADS -x LD_LIBRARY_PATH="$L/$v" -np 4 \
        "$D" "$m" "$n" 100 "${g%x*}" "${g#*x}" C 1 1 2>&1)
  local rc=$? res
  res=$(echo "$log" | sed -n 's/^rep 0 residual \([^ ]*\) .*/\1/p')
  local verdict=crash
  if echo "$log" | grep -q '^rep 0 .* ok$'; then verdict=ok; elif echo "$log" | grep -q WRONG; then verdict=wrong
  elif [ $rc = 124 ]; then verdict=hang; fi
  echo "$cell,$v,$i,$m,$n,100,$g,$t,$rc,${res:-},$verdict" >> "$OUT"
}
for i in $(seq 1 "$RUNS"); do
  for v in plain fixed; do
    one wide-2x2 "$v" "$i" 1000 2000 2x2 "$THREADS"
    one wide-4x1 "$v" "$i" 1000 2000 4x1 "$THREADS"
    one square-2x2 "$v" "$i" 1000 1000 2x2 "$THREADS"
  done
  one wide-2x2-1thread plain "$i" 1000 2000 2x2 1
done
awk -F, 'NR > 1 { n[$1 "," $2]++; c[$1 "," $2 "," $11]++ }
         END { for (k in n) { s = k ": " n[k] " runs"; for (r in c) if (index(r, k ",") == 1) s = s ", " substr(r, length(k) + 2) " " c[r]; print s } }' "$OUT" | sort
