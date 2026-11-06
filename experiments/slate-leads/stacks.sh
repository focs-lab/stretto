#!/usr/bin/env bash
# Run one tester cell without the tracer until a run outlasts WAIT seconds, then dump every
# thread's stack of every rank (LEADS.md, lead T). Inside hunt-slate:24.04, with the tester at BIN
# (default /b/build/test/tester) and ptrace allowed (--cap-add SYS_PTRACE):
#   experiments/slate-leads/stacks.sh SWEEP_RUNS CELL_DIR OUT_DIR [WAIT_SECONDS [REPEAT]]
# OUT_DIR gets runs.txt (one line per run: number, seconds, finished or hung) and, for the run
# that hung, its log and stacks.txt.
set -u
S=${1:?}; name=$(basename "${2:?}"); O=${3:?}; WAIT=${4:-60}; REPEAT=${5:-1}
BIN=${BIN:-/b/build/test/tester}
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none OPENBLAS_NUM_THREADS=1
ulimit -c 0
threads=${name##*_t}; grid=${name%_t*}; grid=${grid##*_}; np=$(( ${grid%x*} * ${grid#*x} ))
eval "a=($(cat "$S/$name/args"))"
mkdir -p "$O"; cd "$O"
for n in $(seq 1 "$REPEAT"); do
  mpirun --bind-to none -np "$np" -x OMP_NUM_THREADS="$threads" "$BIN" "${a[@]}" < /dev/null > log 2>&1 &
  m=$!; t=0
  while kill -0 "$m" 2>/dev/null && [ "$t" -lt "$WAIT" ]; do sleep 1; t=$((t + 1)); done
  if ! kill -0 "$m" 2>/dev/null; then
    wait "$m"; echo "$n $t finished $?" >> runs.txt; continue
  fi
  echo "$n $t hung" >> runs.txt
  for p in $(pgrep -f "$BIN"); do
    [ "$(cat /proc/$p/comm)" = tester ] || continue
    echo "== pid $p, $(ps -o etimes= -o pcpu= -p "$p")" >> stacks.txt
    gdb -p "$p" -batch -ex "thread apply all bt 25" >> stacks.txt 2>/dev/null
  done
  kill "$m"; sleep 2; pkill -9 -f "$BIN"
  break
done
