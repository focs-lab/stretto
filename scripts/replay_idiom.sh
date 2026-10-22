#!/usr/bin/env bash
# Confirming replay: run an idiom with pinned wildcard sources and classify the outcome.
# usage: scripts/replay_idiom.sh <NN> "<STRETTO_PIN spec>" [np] [--mpirun-arg ...] [-- program args]
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
echo "== replay $(basename "$dir") with STRETTO_PIN=$pin (timeout ${TIMEOUT}s)"
mpi_run "$np" "STRETTO_PIN=$pin" "LD_PRELOAD=$PINLIB" -- "${margs[@]}" "$dir/bin/$MPI/main" "$@" > "$out" 2> "$err"
rc=$?
# the replay library announces itself: without that line ld.so ignored it and the run was a plain one
grep -q '^\[stretto-pin\] rank 0: loaded' "$err" || { echo "   HARNESS-ERROR: $PINLIB did not load"; cat "$err"; rm -f "$out" "$err"; exit 1; }
head -5 "$out"
grep '^\[stretto-pin\]' "$err"        # which pins were applied, matched, or left unused
res="$(classify_rc $rc "$err")"
# every pin of the spec must have matched: "applied" only says the source argument was set,
# and forcing an impossible match also hangs. The control rerun (the observed senders pinned)
# is stretto confirm's job; this script shows one replay.
npins=$(tr ',' '\n' <<< "$pin" | grep -c .)
matched=$(grep -c '^\[stretto-pin\] rank [0-9]*: matched' "$err")
case "$res" in
  FINISHED)   echo "   result: FINISHED with $matched of $npins pin(s) matched (no hang under this matching)";;
  TIMEOUT)    if [ "$matched" -ge "$npins" ]; then echo "   result: HANG after all $npins pinned receive(s) matched (a deadlock under this matching; confirm against a control with stretto confirm)";
              else echo "   result: HANG with $matched of $npins pin(s) matched (not a confirmation: a pinned receive may be waiting for an impossible match)"; fi;;
  *)          echo "   result: $res ($matched of $npins pin(s) matched)";;
esac
rm -f "$out" "$err"
