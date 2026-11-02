#!/usr/bin/env bash
# Forced replays of S1 (the steals at the communication thread's wildcard envelope receive) from
# right runs: for each test, RUNS traced runs on 4 ranks with the lock order recorded (scan.sh
# with SYNC=1, the mode of MODE), each analysed in full, then up to SAMPLE of its 1f
# alternatives, each forced REPEAT times with the chain pins, order gates and lock gates of its
# horizon and REPEAT times as its control (experiments/replay_sample.py --threads; docs/threads.md
# sections 14 and 16). The oracle is the test's exit status and output (GREP). Run inside a
# container of the image built from Dockerfile here:
#   experiments/starpu/replay.sh WORK OUTDIR
# Settings: MPI (openmpi), TREE (spu-$MPI-plain), TESTS (gather partition mpi_redux), MODE (ser),
# RUNS (2), SAMPLE (4), REPEAT (1), TIMEOUT (60), NCPU (2), SEED, GREP.
set -u
W=${1:?usage: replay.sh WORK OUTDIR}; O=${2:?}
MPI=${MPI:-openmpi}; TREE=${TREE:-spu-$MPI-plain}; RUNS=${RUNS:-2}; SAMPLE=${SAMPLE:-4}; REPEAT=${REPEAT:-1}
TESTS=${TESTS:-gather partition mpi_redux}; MODE=${MODE:-ser}; TIMEOUT=${TIMEOUT:-60}; NCPU=${NCPU:-2}
GREP=${GREP:-'(?i)(error|fail|mismatch|wrong|abort|assert)'}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
B=/h/build/$TREE L=$B/src/.libs:$B/mpi/src/.libs
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none PYTHONPATH=$REPO/analysis
mkdir -p "$O"
out=$O/replay-$MPI.csv
for t in $TESTS; do
  MPI=$MPI TREE=$TREE TESTS=$t SENDS=$MODE NPS=4 RUNS=$RUNS SYNC=1 MAXMB=100000 KEEPMB=100000 ATIMEOUT=600 \
    "$REPO/experiments/starpu/scan.sh" "$W"
  for i in $(seq 1 "$RUNS"); do
    run=$t-ms$MODE-$MPI-np4-$i; d=$W/$run
    [ -d "$d/trace" ] || continue
    exe=$B/mpi/tests/.libs/$t; [ -x "$exe" ] || exe=$B/mpi/tests/$t
    (cd "$d/cwd" && python3 "$REPO/experiments/replay_sample.py" --threads --mpi "$MPI" --repeat "$REPEAT" \
       --sample "$SAMPLE" ${SEED:+--seed "$SEED"} --grep "$GREP" --timeout "$TIMEOUT" --cls 1f \
       --env LD_LIBRARY_PATH="$L" --env STARPU_NCPU="$NCPU" --env STARPU_MPI_NOBIND=1 --env STARPU_WORKERS_NOBIND=1 \
       --env STARPU_SILENT=1 --env STARPU_HOME="$d/cwd" \
       --keep-output "$O/out/$run" --out "$O/$run.csv" "$d/trace" 4 "$exe") > "$O/$run.log" 2>&1
    [ -f "$O/$run.csv" ] || continue
    [ -f "$out" ] || { head -1 "$O/$run.csv" | sed 's/^/run,/' > "$out"; }
    tail -n +2 "$O/$run.csv" | sed "s/^/$run,/" >> "$out"
  done
done
