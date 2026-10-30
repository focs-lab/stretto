#!/usr/bin/env bash
# Traced runs of copy_check (plain headers and copy-tags.patch) in the cells of series.sh, one
# copy per run, and Stretto's report on each trace (milestone F, docs/threads.md section 15:
# pika's requests completed on other workers are analysed, no longer degraded threads-handoff).
# Run inside a container of the image built from Dockerfile here, after build.sh:
#   experiments/dlaf/traced.sh BUILD WORK OUT.csv
# BUILD holds copy_check and copy_check_fixed; the traces and reports stay under WORK. RUNS
# (traced runs per cell and variant, 5), JOBS (analyses at once, 4).
set -u
B=${1:?usage: traced.sh BUILD WORK OUT.csv}; W=${2:?}; OUT=${3:?}
RUNS=${RUNS:-5}; JOBS=${JOBS:-4}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONPATH=$REPO/analysis OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none OPENBLAS_NUM_THREADS=1
T=$REPO/tracer/bin/openmpi/libstretto-trace.so
# name:ranks:threads:M:N:mb_src:nb_src:mb_dst:nb_dst:P:Q:src_r:src_c:dst_r:dst_c:jitter (series.sh)
CELLS="2x1-offset-jitter:2:4:512:512:64:64:64:64:2:1:0:0:1:0:200 4x1-offset-jitter:4:2:1024:1024:64:64:64:64:4:1:0:0:1:0:200
3x2-offset-jitter:6:2:576:576:64:64:64:64:3:2:0:0:1:0:200 2x2-offset-jitter:4:2:512:512:64:64:64:64:2:2:0:0:1:1:200
2x1-offset-1thread:2:1:512:512:64:64:64:64:2:1:0:0:1:0:200"
mkdir -p "$W"
for i in $(seq 1 "$RUNS"); do
  for c in $CELLS; do
    IFS=: read -r name np t m n mbs nbs mbd nbd p q sr sc dr dc jit <<< "$c"
    for v in plain fixed; do
      d=$W/$name-$v-$i; rm -rf "$d"; mkdir -p "$d/trace"
      exe=$B/copy_check; [ $v = fixed ] && exe=$B/copy_check_fixed
      timeout 300 mpirun --bind-to none -np "$np" -x STRETTO_TRACE_DIR="$d/trace" -x LD_PRELOAD="$T" "$exe" "$m" "$n" "$mbs" "$nbs" \
        "$mbd" "$nbd" "$p" "$q" "$sr" "$sc" "$dr" "$dc" 1 "$jit" --pika:threads="$t" --pika:bind=none > "$d/log" 2>&1
      rc=$?
      w=$(sed -n 's/^summary reps [0-9]* wrong \([0-9]*\)$/\1/p' "$d/log")
      pairs=$(sed -n 's/^provided .* colliding pairs \([0-9]*\)$/\1/p' "$d/log")
      res=crash; if [ -n "$w" ]; then if [ "$w" -gt 0 ]; then res=wrong; else res=ok; fi; elif [ $rc = 124 ]; then res=hang; fi
      echo "$res ${pairs:-}" > "$d/result"
    done
  done
done
ls -d "$W"/*/trace | xargs -P "$JOBS" -I{} sh -c 'timeout 3600 python3 -m stretto analyze --json "{}" > "{}/../report.json" 2> "{}/../report.err"'
python3 -I "$REPO/experiments/dlaf/traced_csv.py" "$W" > "$OUT"
