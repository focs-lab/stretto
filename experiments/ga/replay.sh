#!/usr/bin/env bash
# Forced replays of the scan's L1 alternatives (docs/threads.md sections 14 and 16): for each
# kept trace of the chosen tests, up to SAMPLE alternatives, each forced REPEAT times with the
# chain pins, order gates and lock gates of its horizon and REPEAT times as its control
# (experiments/replay_sample.py --threads). The oracle is the test's own output: "hits" counts
# the lines that report an error or a failed check (GREP). Run inside a container of the image
# built from Dockerfile here, with /dev/shm enlarged and libfakehost.so preloaded for every
# process (the replays set LD_PRELOAD themselves):
#   docker run --shm-size 4g -v ~/ga-work/build/ld.so.preload:/etc/ld.so.preload:ro ... \
#     experiments/ga/replay.sh SCANDIR OUTDIR
# Settings: MPI (openmpi), TESTS (testc ntestc mtest ga-mpi lock gatscat), PPNS (1 2), RUNS
# (which runs of each cell, "1"), SAMPLE (4), REPEAT (1), TIMEOUT (120 s), SEED, GREP.
set -u
S=${1:?usage: replay.sh SCANDIR OUTDIR}; O=${2:?}
MPI=${MPI:-openmpi}; TESTS=${TESTS:-testc ntestc mtest ga-mpi lock gatscat}; PPNS=${PPNS:-1 2}; RUNS=${RUNS:-1}
SAMPLE=${SAMPLE:-4}; REPEAT=${REPEAT:-1}; TIMEOUT=${TIMEOUT:-120}
GREP=${GREP:-'(?i)(error|fail|mismatch|wrong|abort)'}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export OMPI_MCA_btl_vader_single_copy_mechanism=none
mkdir -p "$O"
out=$O/replay-$MPI.csv
for t in $TESTS; do
  for p in $PPNS; do
    for i in $RUNS; do
      run=$t-$MPI-ppn$p-$i; d=$S/$run
      [ -d "$d/trace" ] || { echo "no trace for $run" >&2; continue; }
      exe=/h/build/ga-$MPI-plain/global/testing/$t.x
      [ -x "$exe" ] || exe=/h/build/ga-$MPI-plain/comex/testing/$t
      python3 "$REPO/experiments/replay_sample.py" --threads --mpi "$MPI" --repeat "$REPEAT" --sample "$SAMPLE" \
        ${SEED:+--seed "$SEED"} --grep "$GREP" --timeout "$TIMEOUT" --env FAKEHOST_PPN="$p" \
        --keep-output "$O/out/$run" --out "$O/$run.csv" "$d/trace" 4 "$exe" > "$O/$run.log" 2>&1
      [ -f "$out" ] || { head -1 "$O/$run.csv" | sed 's/^/run,/' > "$out"; }
      tail -n +2 "$O/$run.csv" | sed "s/^/$run,/" >> "$out"
    done
  done
done
