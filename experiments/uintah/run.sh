#!/usr/bin/env bash
# Uintah with hypre under the multithreaded Unified scheduler (docs/threads.md, section 11): build
# hypre and Uintah's sus with the Examples component, run Examples/solvertest1.ups (8 patches)
# with `sus -nthreads N`, traced and untraced, and analyse the traces.
#
# Run inside a container of the image built from Dockerfile here, with the repository at /w and
# the tracer built for Open MPI:
#   experiments/uintah/run.sh WORK UINTAH_SRC
# UINTAH_SRC is a Uintah clone (493d2b5 on 30 Oct 2026), read only; WORK gets hypre 2.33.0 (the
# release tarball: hypre's master has dropped the SparseMSG solver that Uintah's HypreSolver
# calls), the build (about 140 MB with -O2 and no debug information) and the runs. Settings:
# JOBS (build jobs, 2), RUNS (untraced runs per setting, 10).
set -u
W=${1:?usage: run.sh WORK UINTAH_SRC}; U=${2:?usage: run.sh WORK UINTAH_SRC}
JOBS=${JOBS:-2}; RUNS=${RUNS:-10}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
LIB=$REPO/tracer/bin/openmpi/libstretto-trace.so
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
mkdir -p "$W" && cd "$W" || exit 1
if [ ! -e hypre/lib/libHYPRE.so ]; then
  [ -f hypre-2.33.0.tar.gz ] || curl -sSL -o hypre-2.33.0.tar.gz https://github.com/hypre-space/hypre/archive/refs/tags/v2.33.0.tar.gz
  mkdir -p hypre-src && tar -xzf hypre-2.33.0.tar.gz -C hypre-src --strip-components=1
  (cd hypre-src/src && ./configure --prefix="$W/hypre" CC=/usr/bin/mpicc CXX=/usr/bin/mpicxx CFLAGS="-O2 -fPIC" \
     CXXFLAGS="-O2 -fPIC" --enable-shared --disable-fortran > "$W/hypre.cfg.log" 2>&1 && make -j"$JOBS" > "$W/hypre.make.log" 2>&1 \
     && make install > "$W/hypre.inst.log" 2>&1) || { echo "hypre build failed: $W/hypre.*.log"; exit 1; }
fi
if [ ! -x opt/StandAlone/sus ]; then
  mkdir -p opt && (cd opt && "$U/src/configure" --enable-optimize="-O2" --enable-examples --with-hypre="$W/hypre" \
     --with-mpi=built-in CC=/usr/bin/mpicc CXX=/usr/bin/mpicxx F77=/usr/bin/mpif77 > "$W/uintah.cfg.log" 2>&1 \
     && make -j"$JOBS" sus > "$W/uintah.make.log" 2>&1) || { echo "Uintah build failed: $W/uintah.*.log"; exit 1; }
fi
# solvertest1 as shipped has one patch: eight let 2 to 4 ranks and their threads share the work
sed 's|<patches>   \[1,1,1\]       </patches>|<patches>   [2,2,2]       </patches>|; s|<maxTime>0.1</maxTime>|<maxTime>0.05</maxTime>|' \
  "$U/src/StandAlone/inputs/Examples/solvertest1.ups" > solvertest-8p.ups
SUS=$W/opt/StandAlone/sus
for np in 2 4; do
  for k in 1 2 3; do
    d=$W/runs/traced-np$np-$k; rm -rf "$d"; mkdir -p "$d/trace"
    (cd "$d" && timeout 300 /usr/bin/mpirun.openmpi -np $np -x STRETTO_TRACE_DIR="$d/trace" -x LD_PRELOAD="$LIB" \
       "$SUS" -nthreads 2 "$W/solvertest-8p.ups" > out.log 2>&1)
    rc=$?
    PYTHONPATH=$REPO/analysis python3 -m stretto analyze "$d/trace" > "$d/report.txt" 2> "$d/report.err"
    echo "traced np=$np run=$k rc=$rc findings: $(grep '^\[' "$d/report.txt" | sed -E 's/^\[ *([^]]*)\] *([A-Za-z0-9+]+).*/\1:\2/' | sort | uniq -c | tr -s ' ' | tr '\n' ',')"
    rm -rf "$d"/solvertest.uda*
  done
done
mkdir -p runs/plain && cd runs/plain || exit 1
for cfg in "2 -nthreads 2" "4 -nthreads 2" "4 -nthreads 4" "4"; do
  set -- $cfg; np=$1; shift; ok=0; trunc=0
  for i in $(seq 1 "$RUNS"); do
    rm -rf solvertest.uda*
    timeout 120 /usr/bin/mpirun.openmpi -np "$np" "$SUS" "$@" "$W/solvertest-8p.ups" > out.log 2>&1 && ok=$((ok + 1))
    grep -q MPI_ERR_TRUNCATE out.log && trunc=$((trunc + 1))
  done
  echo "untraced np=$np ${*:-(MPI scheduler)}: passed $ok of $RUNS, MPI_ERR_TRUNCATE in $trunc"
done
rm -rf solvertest.uda*
