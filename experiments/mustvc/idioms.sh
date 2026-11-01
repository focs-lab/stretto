#!/usr/bin/env bash
# MUST-vc (the clock layout) on Stretto's idioms with several threads (idioms/15-47 that have a
# flags file: -pthread or -fopenmp), on the process count scripts/env.sh gives them (idiom_np),
# 2 OpenMP threads, REPS runs each and one run without MUST. Inside the mustvc image, with this
# repository at /w:
#   experiments/mustvc/idioms.sh OUT.csv WORK
# Each idiom is built with the image's mpicc (clang 15, LLVM's OpenMP runtime) and its flags,
# -O0 -g as idioms/Makefile builds it. Settings: REPS (3), TIMEOUT (120 s per run), ONLY, COMPAT
# (1: from a copy without the `num_threads(2)` clauses, as corrbench.sh; OMP_NUM_THREADS is 2).
# Row: idiom,np,run,result,rc,seconds,errors,warnings,early,messages,commit (run 0: no MUST).
set -u
OUT=${1:?usage: idioms.sh OUT.csv WORK}; W=${2:?}
REPS=${REPS:-3}; TO=${TIMEOUT:-120}
R=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || echo unknown)}
source <(grep '^idiom_np()' "$R/scripts/env.sh")
export OMP_NUM_THREADS=2
mkdir -p "$W/bin"
I=$R/idioms
if [ "${COMPAT:-0}" = 1 ]; then
  rm -rf "$W/src" && cp -r "$R/idioms" "$W/src" && I=$W/src
  find "$I" -name main.c -exec sed -i 's/ num_threads(2)//' {} +
fi
[ -e "$OUT" ] || echo "idiom,np,run,result,rc,seconds,errors,warnings,early,messages,commit" > "$OUT"
for d in "$I"/[1-4][0-9]_*; do
  n=$(basename "$d"); k=${n%%_*}
  [ "$k" -ge 15 ] && [ -f "$d/flags" ] || continue
  grep -qE "${ONLY:-.}" <<< "$n" || continue
  np=$(idiom_np "$k")
  # shellcheck disable=SC2046
  if ! mpicc -O0 -g $(cat "$d/flags") -I"$I" "$d/main.c" -o "$W/bin/$n" -lm > "$W/bin/$n.build.log" 2>&1; then
    echo "$n,$np,0,BUILD-FAILED,,,,,,,$COMMIT" >> "$OUT"; continue
  fi
  row=$(NATIVE=1 "$R/experiments/mustvc/once.sh" "$np" "$TO" "$W/out/$n/native" "$W/bin/$n")
  IFS=, read -r res rc secs _ <<< "$row"
  echo "$n,$np,0,$res,$rc,$secs,,,,,$COMMIT" >> "$OUT"
  for j in $(seq 1 "$REPS"); do
    echo "$n,$np,$j,$("$R/experiments/mustvc/once.sh" "$np" "$TO" "$W/out/$n/$j" "$W/bin/$n"),$COMMIT" >> "$OUT"
  done
done
