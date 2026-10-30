#!/usr/bin/env bash
# Stage the trees of condition 3 (source tree only): the condition-2 trees, built by stage.sh,
# without what the benchmark added for the runs. Removed from every tree: driver/ (written
# drivers, input files, the SuperLU matrix, the OpenFOAM application) and HYPRE's
# src/examples/ex5_levels.c (ex5 with the -sai_levels option). Every time stamp is then set to
# one value. Nothing else changes.
# Runs inside a container: /h = the a-p13 home (read-only), /w = the work directory with
# scripts/stage.sh, patches/ and drivers/, /t = the output directory (one tree per id).
# usage: stage3.sh <id>...
set -uo pipefail
rc=0
for i in "$@"; do
    if ! bash /w/scripts/stage.sh "$i"; then echo "$i FAILED"; rc=1; continue; fi
    t=/w/stage/$i
    rm -rf "$t/driver" "$t/src/examples/ex5_levels.c"
    rm -rf "/t/$i"; mv "$t" "/t/$i"
    # one time stamp for every file and directory: the patched files of a fixed tree, and the
    # directories the removals touched, would otherwise be the only recent ones
    find "/t/$i" -exec touch -h -d "2000-01-01 00:00:00 UTC" {} +
    echo "$i ready: $(du -sh "/t/$i" | cut -f1), $(find "/t/$i" -type f | wc -l) files"
done
exit $rc
