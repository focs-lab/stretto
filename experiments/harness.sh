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

PRELOAD_BANNER='^\[stretto-pin\] rank 0: loaded'
need_file() { local f; for f; do [ -f "$f" ] || { echo "HARNESS-ERROR: missing $f" >&2; return 1; }; done; }
preload_ok() { grep -q "$PRELOAD_BANNER" "$1" 2>/dev/null; }
preload_ok_in() { grep -q "$PRELOAD_BANNER" <<< "$1"; }
harness_error() { echo "HARNESS-ERROR: $*" >&2; return 1; }
