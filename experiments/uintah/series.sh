#!/usr/bin/env bash
# Untraced series of Uintah's sus with and without the fix of hypre-comm.patch (README, "Cause
# and fix"). Runs alternate between the builds, so that both see the same machine state.
#
# Run inside a container of the image built from Dockerfile here:
#   experiments/uintah/series.sh OUT.csv UPS NAME=SUS [NAME=SUS ...]
# UPS is solvertest-8p.ups as run.sh writes it; each NAME=SUS is a label and a sus binary. Settings:
# RUNS (runs per setting and build, 40), SETTINGS (";"-separated, "4 -nthreads 2;4 -nthreads 4"),
# MPIRUN (mpirun.openmpi), MPI (label for the CSV, openmpi), TMO (seconds before a run counts
# as hung, 60). Each CSV row is one run: mpi,build,np,args,run,rc,outcome,seconds,provided,
# iterations. outcome is pass (exit 0), truncate (MPI_ERR_TRUNCATE or MPICH's "Message
# truncated"), hang (killed after TMO seconds) or fail; provided is the thread level sus
# reports ("Provided: 3" is MPI_THREAD_MULTIPLE); iterations is the solver's count in the last
# time step.
set -u
OUT=$(readlink -f "${1:?usage: series.sh OUT.csv UPS NAME=SUS ...}"); UPS=$(readlink -f "${2:?UPS}"); shift 2
RUNS=${RUNS:-40}; MPIRUN=${MPIRUN:-/usr/bin/mpirun.openmpi}; MPI=${MPI:-openmpi}; TMO=${TMO:-60}
SETTINGS=${SETTINGS:-4 -nthreads 2;4 -nthreads 4}
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
[ -s "$OUT" ] || echo "mpi,build,np,args,run,rc,outcome,seconds,provided,iterations" > "$OUT"
D=$(mktemp -d "${TMPDIR:-/tmp}/uin-series.XXXXXX") && cd "$D" || exit 1
IFS=';' read -r -a CFG <<< "$SETTINGS"
BUILDS=("$@")
for i in $(seq 1 "$RUNS"); do
  for cfg in "${CFG[@]}"; do
    for b in "${BUILDS[@]}"; do
      name=${b%%=*}; sus=${b#*=}; read -r np args <<< "$cfg"
      rm -rf solvertest.uda*
      s=$(date +%s.%N)
      timeout -k 5 "$TMO" "$MPIRUN" -np "$np" "$sus" $args "$UPS" > out.log 2>&1
      rc=$?; e=$(date +%s.%N)
      if [ $rc -eq 0 ]; then o=pass
      elif grep -q -i -E 'MPI_ERR_TRUNCATE|message truncated' out.log; then o=truncate
      elif [ $rc -eq 124 ] || [ $rc -eq 137 ]; then o=hang
      else o=fail; fi
      prov=$(sed -n 's/.*MPI Level Required: [0-9]*, Provided: \([0-9]*\).*/\1/p' out.log | head -1)
      it=$(grep 'Solve of' out.log | tail -1 | sed -n 's/.*, \([0-9]*\) iterations.*/\1/p')
      [ "$o" = fail ] && cp out.log "${OUT%.csv}-fail-$MPI-$name-np$np${args// /}-$i.log"
      echo "$MPI,$name,$np,${args:-mpi-scheduler},$i,$rc,$o,$(echo "$e - $s" | bc | cut -c1-6),$prov,$it" >> "$OUT"
    done
  done
done
cd / && rm -rf "$D"
