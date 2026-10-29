#!/usr/bin/env bash
# SLATE's tester over its distributed routines, 4 ranks (2x2) with 4 OpenMP threads each: one
# untraced run (the tester's own check) and one traced run per routine, with Stretto's report
# status and the overlap scan (overlap.py) of the trace.
#   experiments/slate/sweep.sh TESTER WORK OUT.csv
# Settings: DIM (default 800), NB (100). Traces are deleted after the scan; the scan's output is
# kept as WORK/sweep-ROUTINE.overlap when it found a pair.
set -u
T=${1:?usage: sweep.sh TESTER WORK OUT.csv}; W=${2:?}; OUT=${3:?}
DIM=${DIM:-800}; NB=${NB:-100}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export OPENBLAS_NUM_THREADS=1 OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none OMP_NUM_THREADS=4
ROUTINES=${ROUTINES:-"gemm gemmA hemm symm herk syrk her2k syr2k trsm trmm potrf posv potri trtri getrf gesv getrf_tntpiv gesv_tntpiv getrf_nopiv gesv_nopiv getri geqrf gelqf gels unmqr hetrf hesv he2hb heev hegst hegv ge2tb svd pbtrf pbsv gbtrf gbsv tbsm genorm"}
echo "routine,dim,nb,untraced_rc,untraced_status,traced_rc,overlap_pairs,stretto_status,stretto_findings" > "$OUT"
for r in $ROUTINES; do
  args=(--type d --dim "$DIM" --nb "$NB" --grid 2x2 --check y --ref n)
  case $r in gbtrf|gbsv) args+=(--kl 200 --ku 200);; pbtrf|pbsv|tbsm) args+=(--kd 200);; esac
  log=$(timeout 300 mpirun -x OMP_NUM_THREADS -np 4 "$T" "${args[@]}" "$r" 2>&1); rc=$?
  st=$(echo "$log" | grep -oE '(All tests passed|tests FAILED|tests? skipped|Unknown routine|unknown routine)' | head -1)
  d=$W/sweep-$r; rm -rf "$d"; mkdir -p "$d/trace"
  timeout 600 mpirun -x OMP_NUM_THREADS -x STRETTO_TRACE_DIR="$d/trace" \
    -x LD_PRELOAD="$REPO/tracer/bin/openmpi/libstretto-trace.so" -np 4 "$T" "${args[@]}" "$r" > "$d/out.log" 2>&1
  trc=$?
  "$REPO/experiments/slate/sites.sh" "$d/trace" > "$d/sites.txt"
  STEP=2 python3 -I "$REPO/experiments/slate/overlap.py" "$d/trace" "$d/sites.txt" > "$d/overlap.txt" 2>&1
  pairs=$(sed -n 's/^overlapping .*: //p' "$d/overlap.txt")
  timeout 900 env PYTHONPATH="$REPO/analysis" python3 -m stretto analyze "$d/trace" > "$d/report.txt" 2>&1
  rst=$(sed -n 's/^status: \([^;]*\).*/\1/p' "$d/report.txt" | tr , +); nf=$(grep -c '^\[' "$d/report.txt")
  [ "${pairs:-0}" != 0 ] && cp "$d/overlap.txt" "$W/sweep-$r.overlap"
  echo "$r,$DIM,$NB,$rc,${st:-?},$trc,${pairs:-?},${rst:-?},$nf" | tee -a "$OUT"
  rm -rf "$d/trace"
done
