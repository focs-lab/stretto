#!/usr/bin/env bash
# Interleaved series: PaRSEC's local reshapes (reshape_check, and the shipped reshape test) with
# the plain library and with self-tags.patch, one run of each in turn, in the cells below.
# Two virtual processes per rank come from runtime_vpmap=hwloc on a synthetic hwloc topology of
# two packages (HWLOC_SYNTHETIC; a-p13 grants us one NUMA node, and PaRSEC's rr: and file:
# vpmaps do not work, vpmap.c:384 and 316-325). hwloc does not bind threads on a synthetic
# topology, so they float within the container's CPUs.
# Run inside a container of the image built from Dockerfile here, after build.sh:
#   experiments/parsec/series.sh BUILD OUT.csv
# BUILD holds parsec-plain and parsec-fixed. Settings: RUNS (runs per cell and variant, 20),
# REPS (taskpools per reshape_check run, 5),
# CELLS (name:program:ranks:cores:mt:packages, packages 0 for the flat default vpmap).
set -u
B=${1:?usage: series.sh BUILD OUT.csv}; OUT=${2:?}
RUNS=${RUNS:-20}; export REPS=${REPS:-5}
CELLS=${CELLS:-"vp2-mt:reshape_check:1:6:1:2 vp2-serialized:reshape_check:1:6:0:2 vp1-mt:reshape_check:1:6:1:0
vp2-mt-4ranks:reshape_check:4:2:1:2 shipped-vp2-mt:reshape:1:6:1:2"}
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
echo "cell,variant,run,ranks,cores,mt,vps,reps,wrong_reps,failed_tests,rc,result" > "$OUT"
for i in $(seq 1 "$RUNS"); do
  for c in $CELLS; do
    IFS=: read -r name prog np cores mt pk <<< "$c"
    for v in plain fixed; do
      env=(); vpmap=()
      if [ "$pk" -gt 0 ]; then
        env=(-x HWLOC_SYNTHETIC="package:$pk core:$((cores / pk)) pu:1"); vpmap=(-- --mca runtime_vpmap hwloc)
      fi
      log=$(timeout 300 mpirun --bind-to none -np "$np" "${env[@]}" -x REPS \
            "$B/parsec-$v/tests/collections/reshape/$prog" -N 120 -t 9 -c "$cores" -m "$mt" "${vpmap[@]}" 2>&1)
      rc=$?
      vps=$(echo "$log" | sed -n 's/^provided [0-9]* vps \([0-9]*\) .*/\1/p')
      wrong=$(echo "$log" | sed -n 's/^summary reps [0-9]* wrong \([0-9]*\)$/\1/p')
      failed=$(echo "$log" | grep -c '^Test .* FAILED$')
      passed=$(echo "$log" | grep -c '^Test .* PASSED$')
      res=crash
      if [ -n "$wrong" ]; then if [ "$wrong" -gt 0 ]; then res=wrong; else res=ok; fi
      elif [ $((failed + passed)) -gt 0 ]; then if [ "$failed" -gt 0 ]; then res=wrong; else res=ok; fi
      elif [ $rc = 124 ]; then res=hang
      else e=$(echo "$log" | grep -oE 'MPI_ERR_[A-Z_]+' | head -1); [ -n "$e" ] && res=crash:$e; fi
      echo "$name,$v,$i,$np,$cores,$mt,${vps:-},$REPS,${wrong:-},$failed,$rc,$res" >> "$OUT"
      case $res in ok|wrong) ;; *) echo "$log" | tail -8 > "$OUT.$name-$v-$i.log" ;; esac
    done
  done
done
awk -F, 'NR > 1 { k = $1 "," $2; n[k]++; w[k] += ($12 == "wrong"); o[k] += ($12 == "ok"); r[k] += $9; f[k] += $10 }
         END { for (k in n) print k ": " n[k] " runs, " w[k] " wrong, " o[k] " ok, " n[k] - w[k] - o[k] " other, wrong taskpools " r[k] ", failed shipped tests " f[k] }' "$OUT" | sort
