#!/usr/bin/env bash
# Run one heavy job under the shared-machine protocol (agreed 2026-09-07):
#   * exclusive machine lock  $HOME/bin/logs/machine-memory.lock (override: STRETTO_MACHINE_LOCK)
#   * its own systemd scope with MemoryMax=<cap> (never MemoryHigh: no swap on this host)
# usage: scripts/heavy.sh <cap, e.g. 16G> <cmd> [args...]
# One heavy job at a time; the lock is held for the whole run. Exit status is the job's.
set -uo pipefail
cap="$1"; shift
LOCK="${STRETTO_MACHINE_LOCK:-$HOME/bin/logs/machine-memory.lock}"
mkdir -p "$(dirname "$LOCK")"
exec 9>"$LOCK"
echo "[heavy] waiting for $LOCK" >&2
flock -x 9
echo "[heavy] lock acquired $(date +%H:%M:%S); MemoryMax=$cap; $*" >&2
# CPU set agreed with the other lanes: off the benchmark cores 4-27,60-83 and the OS cores 0-3,56-59
systemd-run --user --scope -q -p MemoryMax="$cap" -- taskset -c "${STRETTO_CPUS:-28-55,84-111}" "$@"
rc=$?
echo "[heavy] done rc=$rc $(date +%H:%M:%S)" >&2
exit $rc
