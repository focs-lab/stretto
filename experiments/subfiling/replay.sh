#!/usr/bin/env bash
# Replay the wrong-worker alternatives that L1 predicts on the traces of run.sh (docs/threads.md,
# section 11): for each traced run of the version with the bug, up to SAMPLE alternatives at the
# receives of tag WRITE_INDEP_DATA (0xd8da02), each forced REPEAT times with the chain pins and
# order gates of its whole horizon and REPEAT times as its control (experiments/replay_sample.py
# --threads), every run in an emptied directory: the test's own output files change the next
# run's path through the Subfiling VFD (an extra I/O concentrator that receives nothing).
#   experiments/subfiling/replay.sh WORK        (in the container of run.sh, after run.sh WORK)
# Settings: SAMPLE (4), REPEAT (2), TIMEOUT (60 s per run), SEED (replay_sample.py's default).
# Writes WORK/replay.csv: replay_sample.py's columns with the run in front; "hits" counts the
# output lines that report wrong data read back ("expected =", the check of test_basic_dataset_write,
# which prints and goes on), in the replay and in its control.
set -u
W=${1:?usage: replay.sh WORK}
SAMPLE=${SAMPLE:-4}; REPEAT=${REPEAT:-2}; TIMEOUT=${TIMEOUT:-60}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
mkdir -p "$W/replay"
out=$W/replay.csv; rm -f "$out"
for d in "$W"/runs/pre/run*/; do
  i=$(basename "$d")
  (cd /tmp && python3 "$REPO/experiments/replay_sample.py" --threads --repeat "$REPEAT" --sample "$SAMPLE" \
     ${SEED:+--seed "$SEED"} --only 'tag=14211586' --grep 'expected =' --fresh-cwd "$W/replay/cwd" \
     --timeout "$TIMEOUT" --out "$W/replay/$i.csv" "$d/trace" 4 "$W/build-pre/bin/t_subfiling_vfd" > "$W/replay/$i.log" 2>&1)
  [ -f "$out" ] || { head -1 "$W/replay/$i.csv" | sed 's/^/run,/' > "$out"; }
  tail -n +2 "$W/replay/$i.csv" | sed "s/^/${i#run},/" >> "$out"
done
rm -rf "$W/replay/cwd"     # the test's files, about 500 MB per run
cat "$out" | cut -c1-200
