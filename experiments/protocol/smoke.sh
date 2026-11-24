#!/usr/bin/env bash
# The smoke test of the campaign's harness (experiments/protocol/README.md): for the subjects
# given, RUNS runs (default 2) of every block of the plan that run.py and replays.py run, through
# the same path as the campaign: NINJA's learning runs and the calibration (RUNS runs per setting
# instead of 10, at most CAL_TIMEOUT seconds each), the timeouts derived from it, then every other
# block with those timeouts, all subjects' jobs placed together. Its tables, journal, traces and
# outputs go to OUT (default ~/protocol/scratch/smoke), which is scratch: none of it is a result,
# and none of it is committed.
# usage: experiments/protocol/smoke.sh <subject> ...     (on the experiment machine's host)
#        RUNS (2), CAL_TIMEOUT (600), OUT, MACHINE (a-p13), STRETTO_COMMIT (smoke), EXCLUDE (a
#        regular expression of blocks to leave out of the second step), CONFIGS (a regular
#        expression of the configurations to run, default all)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
RUNS="${RUNS:-2}"; OUT="${OUT:-$HOME/protocol/scratch/smoke}"
export MACHINE="${MACHINE:-a-p13}" STRETTO_COMMIT="${STRETTO_COMMIT:-smoke}"
mkdir -p "$OUT/results" "$OUT/state"
S=(--results "$OUT/results" --state "$OUT/state" --commit "$STRETTO_COMMIT" --prefix smoke --runs "$RUNS" --configs "${CONFIGS:-.}")
alt=$(IFS='|'; echo "$*")
t0=$SECONDS
python3 experiments/protocol/schedule.py --blocks "^($alt)/(ninja-learn|calibrate)\$" --timeout "${CAL_TIMEOUT:-600}" "${S[@]}"
python3 experiments/protocol/calibrate.py derive --results "$OUT/results" --subjects "$@" --runs "$RUNS"
echo "smoke calibration: $((SECONDS - t0)) s"
python3 experiments/protocol/schedule.py --blocks "^($alt)/(?!${EXCLUDE:-\$^})" "${S[@]}"
echo "smoke: $((SECONDS - t0)) s"
