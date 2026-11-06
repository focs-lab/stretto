#!/usr/bin/env bash
# Run one tester cell without the tracer, wait, and dump every thread's stack of every rank
# (LEADS.md, lead T). Inside hunt-slate:24.04 with the tester at /b/build/test/tester:
#   experiments/slate-leads/stacks.sh SWEEP_RUNS CELL_DIR OUT_DIR [WAIT_SECONDS]
set -u
S=${1:?}; name=$(basename "${2:?}"); O=${3:?}; WAIT=${4:-60}
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none OPENBLAS_NUM_THREADS=1
ulimit -c 0
threads=${name##*_t}; grid=${name%_t*}; grid=${grid##*_}; np=$(( ${grid%x*} * ${grid#*x} ))
eval "a=($(cat "$S/$name/args"))"
mkdir -p "$O"; cd "$O"
mpirun --bind-to none -np "$np" -x OMP_NUM_THREADS="$threads" /b/build/test/tester "${a[@]}" < /dev/null > log 2>&1 &
sleep "$WAIT"
for p in $(pgrep -f build/test/tester); do
  [ "$(cat /proc/$p/comm)" = tester ] || continue
  echo "== pid $p, $(ps -o etimes= -o pcpu= -p $p)" >> stacks.txt
  gdb -p "$p" -batch -ex "thread apply all bt 25" >> stacks.txt 2>/dev/null
done
pkill -f mpirun; sleep 2; pkill -9 -f build/test/tester
echo done
