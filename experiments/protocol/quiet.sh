#!/usr/bin/env bash
# schedule.py on a machine shared with projects that time benchmarks (RULE.md amendment 37): while
# a marker file of theirs exists, nothing of the campaign runs there.
#   quiet.sh 'MARKER-GLOB...' RESULTS schedule.py options
# MARKER-GLOB is one or more shell patterns, separated by spaces (e.g. '/x/LEG /x/PAUSE*'). While
# none matches, the scheduler runs; when one appears, it gets two SIGINTs: no new jobs, and its
# running containers are killed (their jobs are "discarded" in the journal and run again later,
# from the start). Once every marker is gone it starts again, and resumes as after any stop.
# Each pause and resume goes to RESULTS/campaign.log. The script ends with the scheduler's status
# once it ends on its own: 0 when every selected job is done.
set -u
MARKERS=${1:?usage: quiet.sh MARKER-GLOB RESULTS schedule.py options}; RESULTS=${2:?}; shift 2
P=$(cd "$(dirname "$0")" && pwd)
PY=${PYTHON:-python3}
note() { echo "$(date -u +%FT%TZ) $*" >> "$RESULTS/campaign.log"; }
quiet() {
  local g f
  for g in $MARKERS; do
    for f in $g; do [ -e "$f" ] && return 1; done
  done
  return 0
}
pid=
trap '[ -n "$pid" ] && kill -INT "$pid"; exit 130' INT TERM
while :; do
  if [ -z "$pid" ] && quiet; then
    note "start counted (quiet.sh): schedule.py $*"
    "$PY" "$P/schedule.py" --results "$RESULTS" "$@" &
    pid=$!
  elif [ -n "$pid" ] && ! quiet; then
    note "pause: a marker of $MARKERS exists; the running jobs are killed and run again later"
    kill -INT "$pid"; sleep 2; kill -INT "$pid"
    wait "$pid"
    note "paused: schedule.py ended with status $?"
    pid=
  elif [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid"; rc=$?
    note "end counted (quiet.sh): status $rc"
    exit $rc
  fi
  sleep 5
done
