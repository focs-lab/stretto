#!/usr/bin/env bash
# Copy the traced idioms of scripts/run_all.sh (traces/openmpi) over the golden copies the
# tests use in a clean checkout (analysis/tests/golden/openmpi). Run after a change of the
# trace format, then run the tests and commit both.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
src="$root/traces/openmpi"; dst="$root/analysis/tests/golden/openmpi"
[ -d "$src" ] || { echo "no $src: run scripts/run_all.sh first" >&2; exit 1; }
rm -rf "$dst"; mkdir -p "$dst"
for d in "$src"/*/; do
    n="$(basename "$d")"; mkdir -p "$dst/$n"; cp "$d"/rank-*.jsonl "$dst/$n/"
done
echo "copied $(ls "$dst" | wc -l) idioms to $dst"
