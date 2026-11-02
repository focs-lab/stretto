#!/usr/bin/env bash
# Forced replays of S1 (the steals at the runtime's preposted wildcard receives) from right runs:
# for each test, RUNS traced runs on 4 ranks with the lock order recorded (scan.sh with SYNC=1),
# each analysed in full, then up to SAMPLE of its L1 alternatives, each forced REPEAT times with
# the chain pins, order gates and lock gates of its horizon and REPEAT times as its control
# (experiments/replay_sample.py --threads; docs/threads.md sections 14 and 16). The oracle is the
# test's own output: "hits" counts lines that report an error or a failed check (GREP). Run
# inside a container of the image built from Dockerfile here:
#   experiments/madness/replay.sh WORK OUTDIR
# Settings: MPI (openmpi), TREE (mad-$MPI-plain-tsan), TESTS (world/test_hashdc world/test_worldptr
# world/test_future3), RUNS (2), SAMPLE (4), REPEAT (1), TIMEOUT (120), SEED, GREP.
set -u
W=${1:?usage: replay.sh WORK OUTDIR}; O=${2:?}
MPI=${MPI:-openmpi}; TREE=${TREE:-mad-$MPI-plain-tsan}; RUNS=${RUNS:-2}; SAMPLE=${SAMPLE:-4}; REPEAT=${REPEAT:-1}
TESTS=${TESTS:-world/test_hashdc world/test_worldptr world/test_future3}; TIMEOUT=${TIMEOUT:-120}
GREP=${GREP:-'(?i)(error|fail|mismatch|wrong|abort|exception)'}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none PYTHONPATH=$REPO/analysis
mkdir -p "$O"
out=$O/replay-$MPI.csv
for t in $TESTS; do
  n=$(basename "$t")
  MPI=$MPI TREE=$TREE TESTS=$t NPS=4 RUNS=$RUNS SYNC=1 MAXMB=100000 KEEPMB=100000 ATIMEOUT=900 AOPTS=--no-order \
    "$REPO/experiments/madness/scan.sh" "$W"
  for i in $(seq 1 "$RUNS"); do
    run=$n-$MPI-np4-$i; d=$W/$run
    [ -d "$d/trace" ] || continue
    (cd "$d/cwd" && python3 "$REPO/experiments/replay_sample.py" --threads --mpi "$MPI" --repeat "$REPEAT" \
       --sample "$SAMPLE" ${SEED:+--seed "$SEED"} --grep "$GREP" --timeout "$TIMEOUT" --cls 1f \
       --env MAD_NUM_THREADS=2 --env MAD_CHECK_BINDING=OFF --keep-output "$O/out/$run" --out "$O/$run.csv" \
       "$d/trace" 4 "/h/build/$TREE/src/madness/$t") > "$O/$run.log" 2>&1
    [ -f "$O/$run.csv" ] || continue
    [ -f "$out" ] || { head -1 "$O/$run.csv" | sed 's/^/run,/' > "$out"; }
    tail -n +2 "$O/$run.csv" | sed "s/^/$run,/" >> "$out"
  done
done
