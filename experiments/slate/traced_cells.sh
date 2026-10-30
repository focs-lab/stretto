#!/usr/bin/env bash
# Traced runs of wide_lu in the cells of the CALU finding and its controls, and Stretto's report
# on each trace (since the tracer follows OpenMP tasks, docs/threads.md section 12): its status,
# its findings, and how many of them change a receive called in slate::internal::permuteRows
# (finding_sites.py), with their classes.
#   experiments/slate/traced_cells.sh LIBDIR DRIVER WORK OUT.csv
# LIBDIR holds plain/ and fixed/libslate.so.2 (build-lib.sh). Settings: RUNS (traced runs per
# cell, 5), JOBS (analyses at once, 4), CELLS (name:variant:m:n:p:q:method, default below),
# THREADS (OpenMP threads per rank, 4). Traces are deleted after their analysis.
set -u
L=${1:?usage: traced_cells.sh LIBDIR DRIVER WORK OUT.csv}; D=${2:?}; W=${3:?}; OUT=${4:?}
RUNS=${RUNS:-5}; JOBS=${JOBS:-4}; THREADS=${THREADS:-4}
CELLS=${CELLS:-"wide-2x2:plain:1000:2000:2:2:C wide-2x2:fixed:1000:2000:2:2:C wide-4x1:plain:1000:2000:4:1:C
wide-4x1:fixed:1000:2000:4:1:C square-2x2:plain:1000:1000:2:2:C partial-2x2:plain:1000:2000:2:2:P
mt9-2x2:plain:900:2000:2:2:C"}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONPATH=$REPO/analysis
export OPENBLAS_NUM_THREADS=1 OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
export OMP_NUM_THREADS=$THREADS
mkdir -p "$W"
for i in $(seq 1 "$RUNS"); do
  for c in $CELLS; do
    IFS=: read -r name v m n p q meth <<< "$c"
    d=$W/$name-$v-$i; rm -rf "$d"; mkdir -p "$d/trace"
    log=$(timeout 300 mpirun -x OMP_NUM_THREADS -x LD_LIBRARY_PATH="$L/$v" -x STRETTO_TRACE_DIR="$d/trace" \
          -x LD_PRELOAD="$REPO/tracer/bin/openmpi/libstretto-trace.so" -np 4 "$D" "$m" "$n" 100 "$p" "$q" "$meth" 1 1 2>&1)
    res=$(echo "$log" | sed -n 's/^rep 0 .* \(ok\|WRONG\)$/\1/p')
    [ -z "$res" ] && res=crash:$(echo "$log" | grep -oE 'MPI_ERR_[A-Z_]+' | head -1)
    echo "$res" > "$d/result"
  done
done
ls -d "$W"/*/trace | xargs -P "$JOBS" -I{} sh -c \
  's=$(date +%s); timeout 3600 python3 -m stretto analyze --json "{}" > "{}/../report.json" 2> "{}/../report.err"; echo $? $(($(date +%s) - s)) > "{}/../report.rc"'
echo "cell,variant,run,result,stretto_rc,stretto_status,findings,in_permuteRows,classes_in_permuteRows,seconds" > "$OUT"
for i in $(seq 1 "$RUNS"); do
  for c in $CELLS; do
    IFS=: read -r name v m n p q meth <<< "$c"
    d=$W/$name-$v-$i
    st=$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["status"].replace(",", "+"), len(d["findings"]))' "$d/report.json" 2>/dev/null)
    python3 -I "$REPO/experiments/slate/finding_sites.py" "$d/trace" "$d/report.json" > "$d/sites.txt" 2>/dev/null
    inp=$(awk '/permuteRows/ {n += $2} END {print n + 0}' "$d/sites.txt")
    cls=$(awk '/permuteRows/ {printf "%s%s:%s", s, $1, $2; s = "+"}' "$d/sites.txt")
    read -r rc secs < "$d/report.rc"
    echo "$name,$v,$i,$(cat "$d/result"),$rc,${st% *},${st#* },$inp,${cls:-},$secs" >> "$OUT"
    rm -rf "$d/trace"
  done
done
cat "$OUT"
