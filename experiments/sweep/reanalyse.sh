#!/usr/bin/env bash
# The sweep's kept traces (run.sh: trace.tar.xz per cell) analysed again by another tree of the
# analysis, into rows of the same form as run.sh's.
#   experiments/sweep/reanalyse.sh PROJECT WORK OUT.csv TREE LABEL
# TREE is a checkout whose analysis/ and experiments/slate/finding_sites.py are used; LABEL (its
# commit) goes into the rows' commit column and names the directory WORK/<cell>/re-LABEL that
# takes the new report.json, sites.txt and pairs.txt. JOBS (analyses at once, 6). Cells without
# a kept trace get no row. Run in the image of the project's campaign (finding_sites.py needs
# gdb and the traced objects at their paths).
set -u
P=${1:?usage: reanalyse.sh PROJECT WORK OUT.csv TREE LABEL}; W=${2:?}; OUT=${3:?}; TREE=${4:?}; L=${5:?}
JOBS=${JOBS:-6}
HERE=$(cd "$(dirname "$0")" && pwd)
[ -e "$OUT" ] || python3 -I "$HERE/cell_row.py" --header > "$OUT"
ls -d "$W"/*/ | while read -r d; do
  d=${d%/}
  [ -f "$d/trace.tar.xz" ] || continue
  grep -q "^$P,$(head -1 "$d/cell" | cut -d, -f1 | sed 's/[.[\*^$]/\\&/g')," "$OUT" && continue
  echo "$d"
done | xargs -r -P "$JOBS" -I{} sh -c '
  d={}; r=$d/re-$2; rm -rf "$r"; mkdir -p "$r"
  for f in cell run.rc log trace.kb rerun; do [ -e "$d/$f" ] && cp "$d/$f" "$r/"; done
  xz -dc "$d/trace.tar.xz" | tar -C "$r" -xf -
  s=$(date +%s); PYTHONPATH=$1/analysis timeout 3600 python3 -m stretto analyze --json "$r/trace" > "$r/report.json" 2> "$r/report.err"
  echo $? $(($(date +%s) - s)) > "$r/report.rc"
  python3 -I "$1/experiments/slate/finding_sites.py" "$r/trace" "$r/report.json" > "$r/sites.txt" 2> "$r/sites.err"
  python3 -I "$1/experiments/dlaf/pending_pairs.py" "$r/trace" > "$r/pairs.txt" 2> "$r/pairs.err"
  rm -rf "$r/trace"' sh "$TREE" "$L"
for r in "$W"/*/re-"$L"; do
  [ -f "$r/report.rc" ] || continue
  grep -q "^$P,$(head -1 "$r/cell" | cut -d, -f1 | sed 's/[.[\*^$]/\\&/g')," "$OUT" && continue
  python3 -I "$HERE/cell_row.py" "$P" "$r" "$L" >> "$OUT"
done
