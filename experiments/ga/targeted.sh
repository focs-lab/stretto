#!/usr/bin/env bash
# targeted.py at the main threads' reply receives (nb_recv, nb_recv_packed, server_recv) of
# every threadsafec trace of the scan, the larger traces split into shards. Run inside a
# container of the image built from Dockerfile here:
#   experiments/ga/targeted.sh OUTDIR    (writes OUTDIR/<run>-<k>of<n>.csv)
# JOBS (14) processes at once.
set -u
OUT=${1:?usage: targeted.sh OUTDIR}; JOBS=${JOBS:-14}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONPATH=$REPO/analysis
mkdir -p "$OUT"
jobs=()
for t in /h/scan-*/threadsafec-*/trace; do
  run=$(basename "$(dirname "$t")")
  case $run in
    threadsafec-mpich-ppn1-3) n=4 ;;
    threadsafec-mpich-ppn1-1|threadsafec-mpich-ppn2-1) n=3 ;;
    *) n=1 ;;
  esac
  for k in $(seq 0 $((n - 1))); do jobs+=("$t $run $k $n"); done
done
printf '%s\n' "${jobs[@]}" | xargs -P "$JOBS" -L 1 sh -c \
  'SHARD=$3/$4 python3 '"$REPO"'/experiments/ga/targeted.py "nb_recv|server_recv" "$1" > '"$OUT"'/$2-$3of$4.csv 2> '"$OUT"'/$2-$3of$4.err' sh
