# Shared by the experiment drivers (bash, sourced after R is set to the repository root).
#
# need_file FILE...   each file must exist: ld.so ignores an LD_PRELOAD library it cannot open,
#                     and the run then goes on unperturbed, so a missing library would read as a
#                     baseline that never exposes the bug
# preload_ok LOG      the replay library (or the tracer applying pins or delays) announced itself
#                     in the run's output (replay/pin.c prints "[stretto-pin] rank 0: loaded" at
#                     MPI_Init); a run without that line is a harness error, not a plain run
# preload_ok_in TEXT  the same, on the run's output held in a variable
# harness_error MSG   say so on stderr and fail, for the driver to stop the step
# run_result RC SECONDS TIMEOUT
#                     how a run under `timeout -k` ended: FINISHED, TIMEOUT, or rc=RC. 124 is
#                     timeout's own status; 137 (SIGKILL) once TIMEOUT seconds have passed is
#                     timeout's -k kill of a launcher that ignored SIGTERM, which Open MPI's does
#                     when its ranks are deadlocked: a hang too (stretto.confirm.classify_rc).
#                     Before that, 137 is a memory cap or a kill, not a hang.
# keep_trace DIR CAMPAIGN NAME
#                     with KEEP_TRACES set, archive the trace directory DIR as
#                     $KEEP_TRACES/CAMPAIGN/NAME.tar.xz with a line in its MANIFEST.tsv
#                     (stretto.keep), once the run that recorded it is over; without it, nothing.
#                     Silent; a trace that cannot be kept fails with KEEP-ERROR on stderr.

PRELOAD_BANNER='^\[stretto-pin\] rank 0: loaded'
HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
need_file() { local f; for f; do [ -f "$f" ] || { echo "HARNESS-ERROR: missing $f" >&2; return 1; }; done; }
preload_ok() { grep -q "$PRELOAD_BANNER" "$1" 2>/dev/null; }
preload_ok_in() { grep -q "$PRELOAD_BANNER" <<< "$1"; }
harness_error() { echo "HARNESS-ERROR: $*" >&2; return 1; }
keep_trace() {
    [ -n "${KEEP_TRACES:-}" ] || return 0
    PYTHONPATH="$HARNESS_ROOT/analysis" python3 -m stretto.keep "$@"
}
run_result() {
    local rc=$1 s=$2 to=$3
    if [ "$rc" = 0 ]; then echo FINISHED
    elif [ "$rc" = 124 ] || { [ "$rc" = 137 ] && [ "$s" -ge "$to" ]; }; then echo TIMEOUT
    else echo "rc=$rc"; fi
}
