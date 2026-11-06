#!/usr/bin/env bash
# One traced run per cell of a grid (RULE.md rule 9) and Stretto's report on each trace.
# Run inside the project's image, with the repository at REPO (default: this script's), as
#   experiments/sweep/run.sh slate|dlaf|parsec GRID.csv WORK OUT.csv
# Per project: slate needs the tester at BIN (default /b/build/test/tester); dlaf the miniapps in
# BIN (default /h/build/dlaf/miniapp); parsec the test build at BIN (default
# /h/build-tsan/parsec-plain/tests), whose relative program paths are run from there.
# Settings: BATCH (cells traced before their analysis starts, 20; the analysis of one batch runs
# while the next is traced), JOBS (analyses at once, 6), ONLY (a grep pattern on the cell name),
# MINFREE (GiB that must stay free on WORK's file system, 12; the sweep stops below it).
# Every cell's directory keeps its log, result, report.json, sites.txt and pairs.txt; the trace is
# deleted once its row is written. OUT gets one row per cell (cell_row.py), appended; cells whose
# row is in OUT already are skipped, so an interrupted sweep resumes.
set -u
P=${1:?usage: run.sh slate|dlaf|parsec GRID.csv WORK OUT.csv}; G=${2:?}; W=${3:?}; OUT=${4:?}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
BATCH=${BATCH:-20}; JOBS=${JOBS:-6}; MINFREE=${MINFREE:-12}
export PYTHONPATH=$REPO/analysis OPENBLAS_NUM_THREADS=1
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
T=$REPO/tracer/bin/openmpi/libstretto-trace.so
case $P in
  slate) BIN=${BIN:-/b/build/test/tester} ;;
  dlaf) BIN=${BIN:-/h/build/dlaf/miniapp} ;;
  parsec) BIN=${BIN:-/h/build-tsan/parsec-plain/tests} ;;
esac
mkdir -p "$W"
[ -e "$OUT" ] || python3 -I "$REPO/experiments/sweep/cell_row.py" --header > "$OUT"

trace_cell() {   # the cell named by the loop's cell, np, threads, env and args
  local d=$W/$(echo "$cell" | tr '/:#' '__+') exe envs=() e a rc t0 res
  rm -rf "$d"; mkdir -p "$d/trace"
  printf '%s\n' "$cell" > "$d/cell"
  IFS=';' read -r -a envs <<< "$env"
  local xs=(-x STRETTO_TRACE_DIR="$d/trace" -x LD_PRELOAD="$T" -x STRETTO_ATOMICS=1)
  for e in "${envs[@]}"; do
    [ -n "$e" ] || continue
    e=${e//@VPMAP@/$d/vpmap}
    xs+=(-x "$e")
  done
  eval "a=($args)"
  case $P in
    slate) exe=$BIN; a=("${a[@]:1}"); xs+=(-x OMP_NUM_THREADS="$threads") ;;
    dlaf) exe=$BIN/${a[0]}; a=("${a[@]:1}") ;;
    parsec) exe=$BIN/${a[0]}; a=("${a[@]:1}")
      h=$((threads / 2)); [ "$h" -lt 1 ] && h=1
      printf ':%s:0-%s\n:%s:%s-%s\n' "$h" $((h - 1)) "$h" "$h" $((2 * h - 1)) > "$d/vpmap" ;;
  esac
  printf '%q ' "${a[@]}" > "$d/args"
  t0=$(date +%s)
  (cd "$([ "$P" = parsec ] && echo "$BIN" || echo "$d")" && timeout 300 mpirun --bind-to none -np "$np" "${xs[@]}" "$exe" "${a[@]}" < /dev/null) > "$d/log" 2>&1
  rc=$?
  # Rule 2: a SLATE cell refused by its oracle runs once more with the oracle off.
  if [ "$P" = slate ] && grep -q "Error check" "$d/log" && ! grep -q -- "--check n" "$d/args"; then
    mv "$d/log" "$d/log.check-on"; rm -rf "$d/trace"; mkdir -p "$d/trace"
    a=("${a[@]:0:${#a[@]}-1}" --check n "${a[@]: -1}")
    printf '%q ' "${a[@]}" > "$d/args"; echo check-off > "$d/rerun"
    timeout 300 mpirun --bind-to none -np "$np" "${xs[@]}" "$exe" "${a[@]}" < /dev/null > "$d/log" 2>&1
    rc=$?
  fi
  echo "$rc $(($(date +%s) - t0))" > "$d/run.rc"
  du -sk "$d/trace" | cut -f1 > "$d/trace.kb"
  echo "$d"
}

analyse() {      # the cell directories in $@: reports, sites, pending pairs, rows, traces deleted
  printf '%s\n' "$@" | xargs -P "$JOBS" -I{} sh -c '
    s=$(date +%s); timeout 3600 python3 -m stretto analyze --json "{}/trace" > "{}/report.json" 2> "{}/report.err"
    echo $? $(($(date +%s) - s)) > "{}/report.rc"
    python3 -I "$0/experiments/slate/finding_sites.py" "{}/trace" "{}/report.json" > "{}/sites.txt" 2> "{}/sites.err"
    python3 -I "$0/experiments/dlaf/pending_pairs.py" "{}/trace" > "{}/pairs.txt" 2> "{}/pairs.err"' "$REPO"
  local d
  for d in "$@"; do
    python3 -I "$REPO/experiments/sweep/cell_row.py" "$P" "$d" "$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || cat "$REPO/COMMIT")" >> "$OUT"
    rm -rf "$d/trace"
  done
}

batch=() pending=() apid=
while IFS=, read -r cell np threads env args; do
  [ -n "${ONLY:-}" ] && ! echo "$cell" | grep -q -- "$ONLY" && continue
  grep -q "^$P,$(echo "$cell" | sed 's/[.[\*^$]/\\&/g')," "$OUT" && continue
  free=$(df -BG --output=avail "$W" | tail -1 | tr -dc 0-9)
  if [ "$free" -lt "$MINFREE" ]; then echo "stop: $free GiB free" >&2; break; fi
  batch+=("$(trace_cell)")
  if [ ${#batch[@]} -ge "$BATCH" ]; then
    [ -n "$apid" ] && wait "$apid"
    analyse "${batch[@]}" & apid=$!
    batch=()
  fi
done < <(python3 -I -c '
import csv, sys
for r in csv.DictReader(open(sys.argv[1])):
    print(",".join([r["cell"], r["np"], r["threads"], r["env"], r["args"]]))' "$G")   # args last: it may hold commas
[ -n "$apid" ] && wait "$apid"
[ ${#batch[@]} -gt 0 ] && analyse "${batch[@]}"
exit 0
