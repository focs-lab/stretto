#!/usr/bin/env bash
# The scan's tests whose traces were too large for it (deleted unanalysed, or the run killed at
# its size limit): one traced run per test and rank count, up to KILLMB, judged by targeted.py
# (census and sample) instead of the full analysis, one test at a time, each trace deleted
# after it. Run inside a container of the image built from Dockerfile here:
#   experiments/madness/big.sh WORK OUT.csv "world/test_dc:4 mra/test_halo:2 ..."
# Settings as scan.sh (MPI, TREE, THREADS, TIMEOUT) and KILLMB (1000), SAMPLE (60), SEED (1).
set -u
W=${1:?usage: big.sh WORK OUT.csv TESTS}; OUT=${2:?}; LIST=${3:?}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONPATH=$REPO/analysis SAMPLE=${SAMPLE:-60} SEED=${SEED:-1}
mkdir -p "$W"
for x in $LIST; do
  t=${x%:*} np=${x#*:}
  # the full analysis is skipped (ATIMEOUT=1) and the trace kept for targeted.py
  TESTS=$t NPS=$np RUNS=1 KILLMB=${KILLMB:-1000} MAXMB=100000 KEEPMB=100000 ATIMEOUT=1 \
    "$REPO/experiments/madness/scan.sh" "$W"
  d=$W/$(basename "$t")-${MPI:-openmpi}-np$np-1
  if [ -d "$d/trace" ]; then
    python3 "$REPO/experiments/madness/targeted.py" "$d/trace" > "$d/targeted.csv" 2> "$d/targeted.err"
    rm -rf "$d/trace"
  fi
done
first=1
for f in "$W"/*/targeted.csv; do
  [ -f "$f" ] || continue
  if [ $first = 1 ]; then cat "$f"; first=0; else tail -n +2 "$f"; fi
done > "$OUT"
