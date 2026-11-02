#!/usr/bin/env bash
# Blind scan, second part: shipped example inputs with adaptive mesh refinement (inputs/), which
# no MPI regression test runs, built with -mpi -omp -hdf5 (they write HDF5) for their problem
# generator and run on NP ranks of OMP threads through the traced mpirun of this directory (an input without
# mesh/num_threads gets it, as scan.sh does); the run's exit status and its log are its only
# oracle. Run inside a container of the image built from Dockerfile here:
#   docker run ... ath-exp:24.04 /h/repo/experiments/athena/examples.sh WORK
# Settings: MPI (openmpi), EXAMPLES ("configure flags|input" pairs separated by ";", below),
# RUNS (3), NP (4), OMP (2), TIMEOUT, MAXMB, KEEPMB, ATIMEOUT as in scan.sh. Each run leaves
# WORK/<input>-<mpi>-<i>/ with result and calls/1/ as scan.sh does.
set -u
W=${1:?usage: examples.sh WORK}
MPI=${MPI:-openmpi}; RUNS=${RUNS:-3}; NP=${NP:-4}; OMP=${OMP:-2}; TIMEOUT=${TIMEOUT:-600}
MAXMB=${MAXMB:-400}; KEEPMB=${KEEPMB:-60}; ATIMEOUT=${ATIMEOUT:-3600}
EXAMPLES=${EXAMPLES:-"-b --prob=linear_wave --flux=hlld|mhd/athinput.linear_wave2d_amr;--prob=dmr|hydro/athinput.dmr_amr"}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none TIMEOUT
export PATH=$REPO/experiments/athena:$PATH PYTHONPATH=$REPO/analysis
make -s -C "$REPO/tracer" MPI="$MPI" > /dev/null
mpicmd=(); [ "$MPI" = mpich ] && mpicmd=(--mpiccmd=mpicxx.mpich)
IFS=';' read -r -a exs <<< "$EXAMPLES"
for ex in "${exs[@]}"; do
  cfg=${ex%%|*}; inp=${ex#*|}
  n=$(basename "$inp"); n=${n#athinput.}
  B=/h/build/athena-ex-$MPI-$n
  rm -rf "$B" && cp -r /h/src/athena "$B" && rm -rf "$B/.git"
  grep -q num_threads "$B/inputs/$inp" || sed -i '/^<mesh>/a num_threads = 1   # added by the scan: the command line sets it' "$B/inputs/$inp"
  (cd "$B" && python3 configure.py $cfg -mpi -omp -hdf5 --hdf5_path=/usr/lib/x86_64-linux-gnu/hdf5/$MPI --cflag=-g \
     "${mpicmd[@]}" > configure.log 2>&1 && make -j8 > make.log 2>&1) || { echo "build of $n failed"; continue; }
  for i in $(seq 1 "$RUNS"); do
    d=$W/$n-$MPI-$i; rm -rf "$d"; mkdir -p "$d/calls" "$d/run"
    (cd "$d/run" && TRACES=$d/calls TRACER=$REPO/tracer/bin/$MPI/libstretto-trace.so \
       mpirun -n "$NP" "$B/bin/athena" -i "$B/inputs/$inp" mesh/num_threads="$OMP" > "$d/log" 2>&1); rc=$?
    res=pass; [ $rc = 124 ] && res=hang; [ $rc != 0 ] && [ $rc != 124 ] && res=fail
    grep -q "FATAL ERROR" "$d/log" && res=fail                       # Athena++ can exit 0 after one
    echo "$res $rc" > "$d/result"; rm -rf "$d/run"                     # outputs are not kept
    c=$d/calls/1; m=$(du -sm "$c" | cut -f1); echo "$m" > "$c/trace.mb"
    if [ -e "$c/killed" ]; then echo killed > "$c/report.rc"; continue; fi
    if [ "$m" -gt "$MAXMB" ]; then echo size > "$c/report.rc"; rm -f "$c"/rank-*.jsonl "$c"/sync-*.txt; continue; fi
    timeout "$ATIMEOUT" python3 -m stretto analyze --json "$c" > "$c/report.json" 2> "$c/report.err"; echo $? > "$c/report.rc"
    python3 -m stretto sites "$c" > "$c/sites.txt" 2>> "$c/report.err"
    [ "$m" -gt "$KEEPMB" ] && rm -f "$c"/rank-*.jsonl "$c"/sync-*.txt
  done
done
