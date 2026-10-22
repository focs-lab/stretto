#!/usr/bin/env bash
# E3b, read bounds (rule H1', docs/firstread.md), on the traces and binaries the corpus run left
# in experiments/results/mbi/ (experiments/run_remaining.sh mbi), inside the experiment image:
#   maps      the first-read map of every MBI program (experiments/mbi/reads_maps.py), into
#             experiments/results/reads/mbi/; needs clang 18, so it runs in the image of
#             experiments/container/Dockerfile.firstread (a few minutes)
#   analyse   the corpus run's traces analysed again with those maps (experiments/mbi/reanalyze.py
#             --reads): experiments/results/reads/mbi.csv (minutes)
#   replays   the programs whose levels the bounds change, replayed with the pins of their
#             witnesses (run_mbi.py --replay-only --reads on a copy of that table):
#             experiments/results/reads/replays.csv
# E6 with read bounds is experiments/run_remaining.sh e6reads (about 20 minutes on 8
# processors). experiments/RUNBOOK.md has the commands one by one.
# usage: experiments/reads/run.sh [maps|analyse|replays ...]     JOBS (3), TIMEOUT (60)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
export PYTHONPATH="$R/analysis" OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_hwloc_base_binding_policy=none
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
MAPS=experiments/results/reads/mbi

step_maps() { python3 experiments/mbi/reads_maps.py experiments/results/mbi/mbi.csv --out "$MAPS"; }
step_analyse() {
    python3 experiments/mbi/reanalyze.py --reads "$MAPS" --out experiments/results/reads/mbi.csv experiments/results/mbi/mbi.csv
}
step_replays() {
    local p w
    # the programs whose levels the read bounds change, as one --select pattern
    p=$(python3 - <<'PY'
import csv
with open("experiments/results/mbi/mbi.csv") as fh:
    base = {r["name"]: r.get("levels", "") for r in csv.DictReader(fh)}
with open("experiments/results/reads/mbi.csv") as fh:
    moved = sorted(r["name"] for r in csv.DictReader(fh) if r.get("levels", "") != base.get(r["name"], ""))
print("^(" + "|".join(moved) + ")$" if moved else "")
PY
)
    [ -n "$p" ] || { echo "no program changes its levels with the read bounds: nothing to replay" >&2; return 1; }
    w=$(mktemp -d)
    cp experiments/results/reads/mbi.csv "$w/mbi.csv"
    python3 experiments/mbi/run_mbi.py --replay-only --select "$p" --work experiments/results/mbi --out "$w/mbi.csv" \
        --reads "$MAPS" --jobs "${JOBS:-3}" --timeout "${TIMEOUT:-60}" --max-states 30000 --sp-max-events 600 \
        && cp "$w/replays.csv" experiments/results/reads/replays.csv
    local rc=$?
    rm -rf "$w"
    return $rc
}
for step in "${@:-maps analyse replays}"; do
    for s in $step; do
        case "$s" in
            maps|analyse|replays) "step_$s";;
            *) echo "usage: $0 [maps|analyse|replays ...]" >&2; exit 2;;
        esac || exit 1
    done
done
