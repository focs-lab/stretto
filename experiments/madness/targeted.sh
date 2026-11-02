#!/usr/bin/env bash
# The scan's traces whose analysis stopped at its time limit (report.rc 124) and that were kept
# (at most KEEPMB of scan.sh), judged by targeted.py: the census of their candidates by site and
# a random sample of SAMPLE of them. Run inside a container of the image built from Dockerfile
# here, after scan.sh:
#   experiments/madness/targeted.sh SCANDIR OUT.csv
# Settings: SAMPLE (60), SEED (1), JOBS (traces at once, 2).
set -u
S=${1:?usage: targeted.sh SCANDIR OUT.csv}; OUT=${2:?}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONPATH=$REPO/analysis SAMPLE=${SAMPLE:-60} SEED=${SEED:-1}
tmp=$(mktemp -d)
for d in "$S"/*/; do
  [ "$(cat "$d/report.rc" 2>/dev/null)" = 124 ] && [ -d "$d/trace" ] && echo "${d%/}/trace"
done | xargs -r -P "${JOBS:-2}" -I{} sh -c 'python3 '"$REPO"'/experiments/madness/targeted.py {} > '"$tmp"'/$(basename $(dirname {})).csv 2>> '"$tmp"'/err.txt'
first=1
for f in "$tmp"/*.csv; do
  [ -f "$f" ] || continue
  if [ $first = 1 ]; then cat "$f"; first=0; else tail -n +2 "$f"; fi
done > "$OUT"
cat "$tmp/err.txt" >&2
rm -rf "$tmp"
