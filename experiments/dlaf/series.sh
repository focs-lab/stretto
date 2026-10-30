#!/usr/bin/env bash
# Interleaved series: dlaf::matrix::copy between two distributions (copy_check.cpp), the plain
# headers against copy-tags.patch, one run of each in turn, in the cells below. A run counts as
# wrong when any destination element differs from the source.
# Run inside a container of the image built from Dockerfile here, after build.sh:
#   experiments/dlaf/series.sh BUILD OUT.csv
# BUILD holds copy_check and copy_check_fixed. Settings: RUNS (runs per cell and variant, 20),
# REPS (copies per run, 5), CELLS (name:ranks:threads:M:N:mb_src:nb_src:mb_dst:nb_dst:P:Q:src_r:src_c:dst_r:dst_c:jitter).
set -u
B=${1:?usage: series.sh BUILD OUT.csv}; OUT=${2:?}
RUNS=${RUNS:-20}; REPS=${REPS:-5}
CELLS=${CELLS:-"2x1-offset:2:4:512:512:64:64:64:64:2:1:0:0:1:0:0
2x1-offset-jitter:2:4:512:512:64:64:64:64:2:1:0:0:1:0:200
2x1-blocks-jitter:2:4:512:512:128:128:64:64:2:1:0:0:0:0:200
4x1-offset-jitter:4:2:1024:1024:64:64:64:64:4:1:0:0:1:0:200
3x2-offset-jitter:6:2:576:576:64:64:64:64:3:2:0:0:1:0:200
2x2-offset-jitter:4:2:512:512:64:64:64:64:2:2:0:0:1:1:200"}
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none OPENBLAS_NUM_THREADS=1
echo "cell,variant,run,ranks,threads,colliding_pairs,reps,wrong_reps,rc,result" > "$OUT"
for i in $(seq 1 "$RUNS"); do
  for c in $CELLS; do
    IFS=: read -r name np t m n mbs nbs mbd nbd p q sr sc dr dc jit <<< "$c"
    for v in plain fixed; do
      exe=$B/copy_check; [ $v = fixed ] && exe=$B/copy_check_fixed
      log=$(timeout 120 mpirun --bind-to none -np "$np" "$exe" "$m" "$n" "$mbs" "$nbs" "$mbd" "$nbd" "$p" "$q" \
            "$sr" "$sc" "$dr" "$dc" "$REPS" "$jit" --pika:threads="$t" --pika:bind=none 2>&1)
      rc=$?
      pairs=$(echo "$log" | sed -n 's/^provided .* colliding pairs \([0-9]*\)$/\1/p')
      wrong=$(echo "$log" | sed -n 's/^summary reps [0-9]* wrong \([0-9]*\)$/\1/p')
      res=crash
      if [ -n "$wrong" ]; then if [ "$wrong" -gt 0 ]; then res=wrong; else res=ok; fi
      elif [ $rc = 124 ]; then res=hang
      else e=$(echo "$log" | grep -oE 'MPI_ERR_[A-Z_]+' | head -1); [ -n "$e" ] && res=crash:$e; fi
      echo "$name,$v,$i,$np,$t,${pairs:-},$REPS,${wrong:-},$rc,$res" >> "$OUT"
      [ "$res" = ok ] || [ "$res" = wrong ] || echo "$log" | tail -5 > "$OUT.$name-$v-$i.log"
    done
  done
done
awk -F, 'NR > 1 { k = $1 "," $2; n[k]++; w[k] += ($10 == "wrong"); o[k] += ($10 == "ok"); r[k] += $8 }
         END { for (k in n) print k ": " n[k] " runs, " w[k] " wrong, " o[k] " ok, " n[k] - w[k] - o[k] " other, wrong copies " r[k] }' "$OUT" | sort
