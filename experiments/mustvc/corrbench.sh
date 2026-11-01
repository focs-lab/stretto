#!/usr/bin/env bash
# MUST-vc (the clock layout) on the 103 OpenMP programs of MPI-CorrBench a05644b
# (micro-benches/0-level/openmp), 2 processes of 2 OpenMP threads as Stretto's runs, REPS runs
# each, and one run without MUST for the time. Inside the mustvc image:
#   experiments/mustvc/corrbench.sh CORRBENCH_OPENMP_DIR OUT.csv WORK
# Each program is built with the image's mpicc (clang 15, -fopenmp: LLVM's runtime with OMPT)
# as the artifact builds its CorrBench copies (-g), with CorrBench's directory on the include
# path. Settings: REPS (3), TIMEOUT (120 s per run), ONLY (a regular expression on the path),
# COMPAT (1: build from a copy whose `num_threads(NUM_THREADS)` clauses are removed, OpenMP
# then taking OMP_NUM_THREADS=2 threads as before; clang calls __kmpc_global_thread_num at the
# entry of a function with such a clause, which starts the OpenMP runtime before MPI_Init, and
# MUST-vc does not support that; the artifact's copies of CorrBench programs drop the clause).
# Row: dir,label,program,run,result,rc,seconds,errors,warnings,early,messages,commit; run 0 is
# the run without MUST (errors, warnings and early empty).
set -u
C=${1:?usage: corrbench.sh CORRBENCH_OPENMP_DIR OUT.csv WORK}; OUT=${2:?}; W=${3:?}
REPS=${REPS:-3}; TO=${TIMEOUT:-120}
R=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || echo unknown)}
export OMP_NUM_THREADS=2
mkdir -p "$W/bin"
if [ "${COMPAT:-0}" = 1 ]; then
  rm -rf "$W/src" && cp -r "$C" "$W/src" && C=$W/src
  find "$C" -name '*.c' -exec sed -i 's/ num_threads(NUM_THREADS)//' {} +
fi
[ -e "$OUT" ] || echo "dir,label,program,run,result,rc,seconds,errors,warnings,early,messages,commit" > "$OUT"
(cd "$C" && find . -name '*.c' | sed 's|^\./||' | sort) | grep -E "${ONLY:-.}" | while read -r f; do
  dir=$(echo "$f" | cut -d/ -f1)
  case $f in */correct/*) label=correct;; *) label=erroneous;; esac
  name=$(echo "${f%.c}" | sed 's|/|__|g')
  if ! mpicc -g -fopenmp -I"$C" "$C/$f" -o "$W/bin/$name" -lm > "$W/bin/$name.build.log" 2>&1; then
    echo "$dir,$label,$f,0,BUILD-FAILED,,,,,,,$COMMIT" >> "$OUT"; continue
  fi
  row=$(NATIVE=1 "$R/experiments/mustvc/once.sh" 2 "$TO" "$W/out/$name/native" "$W/bin/$name")
  IFS=, read -r res rc secs _ <<< "$row"
  echo "$dir,$label,$f,0,$res,$rc,$secs,,,,,$COMMIT" >> "$OUT"
  for k in $(seq 1 "$REPS"); do
    echo "$dir,$label,$f,$k,$("$R/experiments/mustvc/once.sh" 2 "$TO" "$W/out/$name/$k" "$W/bin/$name"),$COMMIT" >> "$OUT"
  done
done
