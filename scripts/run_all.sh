#!/usr/bin/env bash
# Trace and analyse every idiom; print the class summary next to the README expectation.
set -uo pipefail
source "$(dirname "$0")/env.sh"
for d in "$ROOT"/idioms/[0-9][0-9]_*/; do
    n="$(basename "$d" | cut -c1-2)"
    "$ROOT/scripts/run_idiom.sh" "$n" "${@}"
    echo
done
