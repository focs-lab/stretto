#!/usr/bin/env bash
# The whole counted campaign in its phases (README.md, "Running, resuming"), each step only if the
# one before it exited 0:
#   1. the prerequisites and calibrations: NINJA's learning runs, MUST's warm-ups, the
#      applications' calibrations, the suites' builds and calibrations, the thread campaigns'
#      calibrations (schedule.py --blocks CALIBRATION)
#   2. their T: calibrate.py derive, derive-suites, derive-threads
#   3. every other block of plan.csv: the counted runs, the deferred analyses of the jobs that run
#      alone, the enumerations and the replays (one schedule.py, which orders them itself)
# No step needs a choice: T comes from the rule. A step that fails, or the scheduler's resumable
# stop (status 75: free space below the floor), stops the script with that status; running it
# again resumes, since a done job is not run again and each derive gives the same T from the same
# calibration rows. It runs on the experiment machine's host, where schedule.py starts the
# containers (it runs nothing else there), detached:
#   MACHINE=a-p13 STRETTO_COMMIT=<frozen> nohup experiments/protocol/campaign.sh STATE RESULTS \
#     > RESULTS.log 2>&1 < /dev/null &
# Every step and its status go to RESULTS/campaign.log. Options after RESULTS go to every
# schedule.py (as --runs 1 for a rehearsal); DRY_RUN=1 lists what the scheduler would start and
# runs no derive; PYTHON names the interpreter (python3).
set -u
STATE=${1:?usage: campaign.sh STATE RESULTS [schedule.py options]}; RESULTS=${2:?}; shift 2
: "${MACHINE:?set MACHINE (e.g. a-p13)}" "${STRETTO_COMMIT:?set STRETTO_COMMIT to the frozen commit}"
PY=${PYTHON:-python3}
P=$(cd "$(dirname "$0")" && pwd)
CALIBRATION='/(ninja-learn|must-warmup|calibrate|[a-z]+-build|[a-z]+-calibrate|calibrate-.*)$'
mkdir -p "$STATE" "$RESULTS"
dry=(); [ "${DRY_RUN:-0}" = 1 ] && dry=(--dry-run)

step() {   # step NAME COMMAND...: run it, note it and its status; return its status
  local name=$1 rc; shift
  echo "$(date -u +%FT%TZ) start $name: $*" >> "$RESULTS/campaign.log"
  "$@"; rc=$?
  echo "$(date -u +%FT%TZ) end $name: status $rc" >> "$RESULTS/campaign.log"
  return $rc
}
sched() { "$PY" "$P/schedule.py" --state "$STATE" --results "$RESULTS" --commit "$STRETTO_COMMIT" "${dry[@]}" "$@"; }

step calibration sched --blocks "$CALIBRATION" "$@" || exit $?
if [ "${DRY_RUN:-0}" != 1 ]; then
  step derive "$PY" "$P/calibrate.py" derive --results "$RESULTS" || exit $?
  step derive-suites "$PY" "$P/calibrate.py" derive-suites --results "$RESULTS" || exit $?
  step derive-threads "$PY" "$P/calibrate.py" derive-threads --results "$RESULTS" || exit $?
fi
step counted sched "$@" || exit $?
echo "$(date -u +%FT%TZ) campaign done" >> "$RESULTS/campaign.log"
