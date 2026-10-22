#!/usr/bin/env bash
# Trace and analyse every idiom; print the class summary next to the README expectation.
# Exit status 1 if an idiom failed to build, to be traced or to be analysed (run_idiom.sh).
set -uo pipefail
source "$(dirname "$0")/env.sh"
failed=()
for d in "$ROOT"/idioms/[0-9][0-9]_*/; do
    n="$(basename "$d" | cut -c1-2)"
    "$ROOT/scripts/run_idiom.sh" "$n" "${@}" || failed+=("$n")
    echo
done
[ ${#failed[@]} -eq 0 ] || { echo "idioms that failed: ${failed[*]}" >&2; exit 1; }
