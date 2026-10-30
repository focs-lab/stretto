#!/usr/bin/env bash
# One traced run of each nightly threaded ARCHES test (nightly.sh) with the tracer following the
# threads (trace format 3), the analysis, and every pair of collectives two threads call
# concurrently (pairs.py; README, "Other collectives on MPI_COMM_WORLD").
#
# Run inside a container of the image built from Dockerfile here, with the repository at REPO
# (default: this script's) and the tracer built for Open MPI (make -C tracer MPI=openmpi):
#   experiments/uintah/trace-nightly.sh OUTDIR UINTAH_SRC SUS
# Settings: TESTS ("1L 2L DO"), SETTING ("2 -nthreads 8"), MAXSTEPS (as in nightly.sh), TMO (600).
# OUTDIR/<test>/ gets out.log, report.txt (stretto analyze), pairs.txt; the trace is deleted
# unless KEEP=1.
set -u
O=$(mkdir -p "${1:?usage: trace-nightly.sh OUTDIR UINTAH_SRC SUS}" && readlink -f "$1"); U=$(readlink -f "${2:?UINTAH_SRC}")
SUS=$(readlink -f "${3:?SUS}")
TESTS=${TESTS:-1L 2L DO}; SETTING=${SETTING:-2 -nthreads 8}; MAXSTEPS=${MAXSTEPS:-}; TMO=${TMO:-600}
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
LIB=$REPO/tracer/bin/openmpi/libstretto-trace.so
[ -f "$LIB" ] || { echo "no tracer $LIB"; exit 1; }
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
read -r np args <<< "$SETTING"
for t in $TESTS; do
  d=$O/$t; rm -rf "$d"; mkdir -p "$d/trace" && cd "$d" || exit 1
  ln -s "$U/src/StandAlone/inputs" inputs
  ups=$U/src/StandAlone/inputs/ARCHES/RMCRT/multibox_rmcrt_coal_$t.ups
  if [ -n "$MAXSTEPS" ]; then
    sed "s|<max_Timesteps>[0-9]*</max_Timesteps>|<max_Timesteps>$MAXSTEPS</max_Timesteps>|" "$ups" > "$t.ups"
  else
    cp "$ups" "$t.ups"
  fi
  s=$(date +%s)
  timeout -k 10 "$TMO" /usr/bin/mpirun.openmpi --bind-to none -np "$np" -x STRETTO_TRACE_DIR="$d/trace" -x LD_PRELOAD="$LIB" \
    "$SUS" $args "$t.ups" > out.log 2>&1
  rc=$?; e=$(date +%s)
  rm -rf ./*.uda*
  PYTHONPATH=$REPO/analysis python3 -m stretto analyze "$d/trace" > report.txt 2> report.err
  PYTHONPATH=$REPO/analysis python3 "$REPO/experiments/uintah/pairs.py" "$d/trace" > pairs.txt 2> pairs.err
  echo "$t: rc=$rc $((e - s)) s, trace $(du -sh trace | cut -f1), findings: $(grep '^\[' report.txt | sed -E 's/^\[ *([^]]*)\] *([A-Za-z0-9+-]+).*/\1:\2/' | sort | uniq -c | tr -s ' ' | tr '\n' ',') $(head -1 pairs.txt)"
  [ "${KEEP:-0}" = 1 ] || rm -rf trace
done
