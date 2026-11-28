#!/usr/bin/env bash
# Keep the results of finished jobs in the repository and push them to the branch cluster-runs:
# experiments/cluster/keep.sh for each job (a job without results is skipped), one commit, a push.
# The last job of experiments/cluster/submit-scale.sh's chain; by hand, from a login node:
#   experiments/cluster/collect.sh <job id> ...
# COLLECT_NO_GIT=1 keeps only (the emulation). The push uses GIT_CONFIG_GLOBAL (default
# ~/.agent-creds/gitconfig-focs1, the checkout's credentials) if that file exists.
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
cfg="${GIT_CONFIG_GLOBAL:-$HOME/.agent-creds/gitconfig-focs1}"
[ -f "$cfg" ] && export GIT_CONFIG_GLOBAL="$cfg"
if [ "${COLLECT_NO_GIT:-0}" != 1 ]; then
    [ "$(git rev-parse --abbrev-ref HEAD)" = cluster-runs ] || { echo "collect.sh: not on the branch cluster-runs" >&2; exit 1; }
    [ -n "$(git config user.email)" ] || { echo "collect.sh: git has no user.email (GIT_CONFIG_GLOBAL=${GIT_CONFIG_GLOBAL:-})" >&2; exit 1; }
fi
kept=()
for j in "$@"; do
    if [ -f "build/cluster/$j/job.json" ]; then
        # a copy that keep.sh refuses (a home path left in it) is not committed half-made
        if experiments/cluster/keep.sh "$j"; then kept+=("$j"); else rm -rf "experiments/results/cluster/$j"; fi
    else
        echo "collect.sh: job $j has no results (build/cluster/$j/job.json), skipped"
    fi
done
[ ${#kept[@]} -gt 0 ] || { echo "collect.sh: nothing to keep"; exit 0; }
[ "${COLLECT_NO_GIT:-0}" = 1 ] && { echo "collect.sh: kept ${kept[*]}; no commit (COLLECT_NO_GIT)"; exit 0; }
git add -- "${kept[@]/#/experiments/results/cluster/}"
if git diff --cached --quiet; then
    echo "collect.sh: the results of ${kept[*]} are committed already"; exit 0
fi
what="job ${kept[0]}"; [ ${#kept[@]} = 1 ] || what="jobs ${kept[0]} to ${kept[-1]}"
git commit -q -m "cluster: Keep the results of $what" \
    -m "experiments/cluster/collect.sh after the jobs ended: ${kept[*]}."
git pull -q --rebase origin cluster-runs
git push -q origin HEAD:cluster-runs
git log --oneline -1
