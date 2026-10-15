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
dir="$(idiom_dir "$n")"
log="$(mktemp)"
echo "== replay $(basename "$dir") with REMATCH_PIN=$pin (timeout ${TIMEOUT}s)"
mpi_run "$np" "REMATCH_PIN=$pin" "REMATCH_VERBOSE=1" "LD_PRELOAD=$PINLIB" -- "${margs[@]}" "$dir/bin/$MPI/main" "$@" > "$log" 2>&1
rc=$?
grep -v '^\[rematch-pin\]' "$log" | head -5
res="$(classify_rc $rc "$log")"
case "$res" in
  FINISHED)   echo "   result: FINISHED (prediction not confirmed as a hang)";;
  TIMEOUT)    echo "   result: HANG (deadlock confirmed by replay)";;
  *)          echo "   result: $res";;
esac
rm -f "$log"
