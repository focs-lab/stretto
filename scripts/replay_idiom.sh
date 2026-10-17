#!/usr/bin/env bash
# Confirming replay: run an idiom with pinned wildcard sources and classify the outcome.
# usage: scripts/replay_idiom.sh <NN> "<REMATCH_PIN spec>" [np] [--mpirun-arg ...] [-- program args]
set -uo pipefail
source "$(dirname "$0")/env.sh"
n="$1"; pin="$2"; shift 2
np="$(idiom_np "$n")"
if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]+$ ]]; then np="$1"; shift; fi
margs=(); while [ $# -gt 0 ] && [ "$1" != "--" ]; do margs+=("$1"); shift; done
[ "${1:-}" = "--" ] && shift
dir="$(idiom_dir "$n")"; [ -n "$dir" ] || { echo "no idiom $n" >&2; exit 1; }
need_lib "$PINLIB" replay
out="$(mktemp)"; err="$(mktemp)"
echo "== replay $(basename "$dir") with REMATCH_PIN=$pin (timeout ${TIMEOUT}s)"
mpi_run "$np" "REMATCH_PIN=$pin" "LD_PRELOAD=$PINLIB" -- "${margs[@]}" "$dir/bin/$MPI/main" "$@" > "$out" 2> "$err"
rc=$?
head -5 "$out"
grep '^\[rematch-pin\]' "$err"        # which pins were applied, or left unused
res="$(classify_rc $rc "$err")"
applied=$(grep -c '^\[rematch-pin\] rank [0-9]*: applied' "$err")
case "$res" in
  FINISHED)   echo "   result: FINISHED with $applied pin(s) applied (no hang under this matching)";;
  TIMEOUT)    if [ "$applied" -gt 0 ]; then echo "   result: HANG with $applied pin(s) applied (deadlock confirmed by replay)";
              else echo "   result: HANG but no pin was applied (not a confirmation)"; fi;;
  *)          echo "   result: $res ($applied pin(s) applied)";;
esac
rm -f "$out" "$err"
