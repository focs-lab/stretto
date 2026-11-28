#!/usr/bin/env bash
# Keep a finished job's results in the repository: every CSV and job.json under its OUT
# (build/cluster/<job>/, which is not committed and has no backup) copied to
# experiments/results/cluster/<job>/ at the same relative paths, with the repository's path
# replaced by "." and the home directory by "~", so that no user's path is committed. Traces,
# outputs and the job's temporary files stay in OUT. Light: runs anywhere, a login node included.
# usage: experiments/cluster/keep.sh <job id> ...
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
[ $# -gt 0 ] || { echo "usage: experiments/cluster/keep.sh <job id> ..." >&2; exit 2; }
for j in "$@"; do
    src="build/cluster/$j"; dst="experiments/results/cluster/$j"
    [ -f "$src/job.json" ] || { echo "keep.sh: no $src/job.json" >&2; exit 1; }
    n=0
    while IFS= read -r f; do
        mkdir -p "$dst/$(dirname "$f")"
        sed -e "s|$R/|./|g" -e "s|$R|.|g" -e "s|$HOME|~|g" "$src/$f" > "$dst/$f"
        n=$((n + 1))
    done < <(cd "$src" && find . \( -path ./tmp -o -path ./protocol/outputs -o -path ./protocol/keep -o -path ./protocol/state \) -prune \
                 -o -type f \( -name '*.csv' -o -name job.json \) -print | sed 's|^\./||' | sort)
    [ -f "build/cluster/slurm-$j.out" ] && sed -e "s|$R/|./|g" -e "s|$HOME|~|g" "build/cluster/slurm-$j.out" > "$dst/slurm.out" && n=$((n + 1))
    ! grep -rqE "$HOME|/home/" "$dst" || { echo "keep.sh: a home path is left in $dst" >&2; exit 1; }
    echo "$dst: $n files"
done
