#!/usr/bin/env bash
# Build, trace and analyse one idiom.   usage: scripts/run_idiom.sh <NN> [np] [-- program args]
# STRETTO_GUARD=1 in the environment traces with the buffer guard on, STRETTO_PAYLOAD=<bytes>
# with message payloads recorded.
set -uo pipefail
source "$(dirname "$0")/env.sh"
n="$1"; shift
np="$(idiom_np "$n")"
if [ $# -gt 0 ] && [ "$1" != "--" ]; then np="$1"; shift; fi
[ "${1:-}" = "--" ] && shift
dir="$(idiom_dir "$n")"; [ -n "$dir" ] || { echo "no idiom $n"; exit 1; }
make -s -C "$ROOT/idioms" MPI="$MPI" >/dev/null || { echo "build failed"; exit 1; }
make -s -C "$ROOT/tracer" MPI="$MPI" >/dev/null || exit 1
make -s -C "$ROOT/replay" MPI="$MPI" >/dev/null || exit 1
need_lib "$TRACER" tracer
out="$ROOT/traces/$MPI/$(basename "$dir")"
rm -rf "$out"; mkdir -p "$out"
echo "== $(basename "$dir") (np=$np, $MPI)"
mpi_run "$np" "STRETTO_TRACE_DIR=$out" "LD_PRELOAD=$TRACER" ${STRETTO_GUARD:+"STRETTO_GUARD=$STRETTO_GUARD"} ${STRETTO_PAYLOAD:+"STRETTO_PAYLOAD=$STRETTO_PAYLOAD"} -- "$dir/bin/$MPI/main" "$@" > "$out/stdout.log" 2> "$out/stderr.log"
rc=$?
res="$(classify_rc $rc "$out/stderr.log")"
[ "$res" != FINISHED ] && echo "   observed run: $res"
ls "$out"/rank-*.jsonl > /dev/null 2>&1 || { echo "   no trace written"; exit 1; }
# the idioms are erroneous programs, so 1 (an error class found) is a success here; 2 (the trace
# cannot be loaded) and 3 (an internal error) are failures
python3 -m stretto analyze "$out"
rc=$?
[ $rc -le 1 ] || { echo "   analysis failed with status $rc"; exit $rc; }
