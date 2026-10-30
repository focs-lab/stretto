#!/usr/bin/env bash
# Untraced series of Uintah's nightly threaded ARCHES tests (R_Tester/ARCHES.py,
# multibox_rmcrt_coal_{1L,2L,DO}_threaded: 2 ranks, -nthreads 8, hypre through HypreSolver2) with
# and without the fix of hypre-comm.patch (README, "Uintah's nightly threaded tests"). Runs
# alternate between the builds, so that both see the same machine state.
#
# Run inside a container of the image built from Dockerfile here, with ARCHES built by build.sh
# (COMPONENTS=--enable-arches):
#   experiments/uintah/nightly.sh OUT.csv UINTAH_SRC NAME=SUS [NAME=SUS ...]
# UINTAH_SRC is the Uintah clone (its src/StandAlone/inputs holds the inputs and the mixing
# table they read); each NAME=SUS is a label and a sus binary. Settings: RUNS (runs per test,
# setting and build, 20), TESTS (" "-separated, "1L 2L DO"), SETTINGS (";"-separated, "2 -nthreads
# 8", the nightly one), MAXSTEPS (if set, a copy of each input with max_Timesteps set to it; the
# inputs as shipped run 10), TMO (seconds before a run counts as hung, 600), MPIRUN
# (mpirun.openmpi, with --bind-to none so that a rank's threads may use every processor).
# Each CSV row is one run: test,build,np,args,steps,run,rc,outcome,seconds,provided,last_step,
# last_delt. outcome is pass (exit 0), truncate (MPI_ERR_TRUNCATE or "message truncated"),
# hang (killed after TMO seconds), mpierr (another MPI error class) or fail; provided is the
# thread level sus reports (3 is MPI_THREAD_MULTIPLE); last_step and last_delt are those of the
# last time step sus printed. The output of every run that is not a pass is kept next to OUT.
set -u
OUT=$(readlink -f "${1:?usage: nightly.sh OUT.csv UINTAH_SRC NAME=SUS ...}"); U=$(readlink -f "${2:?UINTAH_SRC}"); shift 2
RUNS=${RUNS:-20}; TESTS=${TESTS:-1L 2L DO}; SETTINGS=${SETTINGS:-2 -nthreads 8}; MAXSTEPS=${MAXSTEPS:-}
TMO=${TMO:-600}; MPIRUN=${MPIRUN:-/usr/bin/mpirun.openmpi --bind-to none}
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
[ -s "$OUT" ] || echo "test,build,np,args,steps,run,rc,outcome,seconds,provided,last_step,last_delt" > "$OUT"
D=$(mktemp -d "${TMPDIR:-/tmp}/uin-nightly.XXXXXX") && cd "$D" || exit 1
ln -s "$U/src/StandAlone/inputs" inputs
for t in $TESTS; do
  ups=$U/src/StandAlone/inputs/ARCHES/RMCRT/multibox_rmcrt_coal_$t.ups
  [ -f "$ups" ] || { echo "no input $ups"; exit 1; }
  if [ -n "$MAXSTEPS" ]; then
    sed "s|<max_Timesteps>[0-9]*</max_Timesteps>|<max_Timesteps>$MAXSTEPS</max_Timesteps>|" "$ups" > "$t.ups"
  else
    cp "$ups" "$t.ups"
  fi
done
steps=${MAXSTEPS:-10}
IFS=';' read -r -a CFG <<< "$SETTINGS"
for i in $(seq 1 "$RUNS"); do
  for t in $TESTS; do
    for cfg in "${CFG[@]}"; do
      for b in "$@"; do
        name=${b%%=*}; sus=${b#*=}; read -r np args <<< "$cfg"
        rm -rf ./*.uda*
        s=$(date +%s.%N)
        timeout -k 10 "$TMO" $MPIRUN -np "$np" "$sus" $args "$t.ups" > out.log 2>&1
        rc=$?; e=$(date +%s.%N)
        if [ $rc -eq 0 ]; then o=pass
        elif grep -q -i -E 'MPI_ERR_TRUNCATE|message truncated' out.log; then o=truncate
        elif [ $rc -eq 124 ] || [ $rc -eq 137 ]; then o=hang
        elif grep -q -E 'MPI_ERR_[A-Z]+' out.log; then o=mpierr
        else o=fail; fi
        prov=$(sed -n 's/.*MPI Level Required: [0-9]*, Provided: \([0-9]*\).*/\1/p' out.log | head -1)
        last=$(grep -E '^Timestep +[0-9]+ ' out.log | tail -1)
        ls=$(echo "$last" | awk '{print $2}'); ld=$(echo "$last" | sed -n 's/.*delT=\([^ ]*\).*/\1/p')
        [ "$o" != pass ] && cp out.log "${OUT%.csv}-$o-$t-$name-np$np${args// /}-$i.log"
        echo "$t,$name,$np,${args:-mpi-scheduler},$steps,$i,$rc,$o,$(echo "$e - $s" | bc | cut -c1-7),$prov,$ls,$ld" >> "$OUT"
      done
    done
  done
done
cd / && rm -rf "$D"
