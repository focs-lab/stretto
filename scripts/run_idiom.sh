#!/usr/bin/env bash
# Build, trace and analyse one idiom.   usage: scripts/run_idiom.sh <NN> [np] [-- program args]
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
out="$ROOT/traces/$MPI/$(basename "$dir")"
rm -rf "$out"; mkdir -p "$out"
echo "== $(basename "$dir") (np=$np, $MPI)"
mpi_run "$np" "REMATCH_TRACE_DIR=$out" "LD_PRELOAD=$TRACER" -- "$dir/bin/$MPI/main" "$@" > "$out/stdout.log" 2> "$out/stderr.log"
rc=$?
res="$(classify_rc $rc "$out/stderr.log")"
[ "$res" != FINISHED ] && echo "   observed run: $res"
python3 -m rematch analyze "$out"
