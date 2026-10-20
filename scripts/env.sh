# Shared environment for the Stretto scripts. System MPI only.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MPI="${MPI:-openmpi}"
# Ubuntu's MPICH is PMIx-based and its hydra starts singletons; Open MPI's mpirun launches
# MPICH binaries correctly, so it is the launcher for both.
MPIRUN="/usr/bin/mpirun.openmpi"
TRACER="$ROOT/tracer/bin/$MPI/libstretto-trace.so"
PINLIB="$ROOT/replay/bin/$MPI/libstretto-pin.so"
export PYTHONPATH="$ROOT/analysis${PYTHONPATH:+:$PYTHONPATH}"
export OMPI_MCA_rmaps_base_oversubscribe=1
TIMEOUT="${TIMEOUT:-15}"

# mpi_run <np> <NAME=VALUE ...> -- <exe> [args]
# Environment goes to the ranks only (mpirun -x NAME=VALUE), never to this shell.
# stdin is /dev/null. timeout without --foreground signals the whole process group on
# expiry, so the ranks go with mpirun instead of spinning on as orphans.
mpi_run() {
    local np="$1"; shift
    local xargs=()
    while [ "$1" != "--" ]; do xargs+=(-x "$1"); shift; done; shift
    local t0=$SECONDS rc
    /usr/bin/timeout -k 5 "$TIMEOUT" "$MPIRUN" -np "$np" "${xargs[@]}" "$@" < /dev/null; rc=$?
    MPI_RUN_SECONDS=$((SECONDS - t0))     # read by classify_rc: a SIGKILL past the timeout is a hang
    return $rc
}

# classify_rc <rc> [stderr-file] -> FINISHED | TIMEOUT | KILLED | MPI-ERROR:<name> | SIGNAL:<n> | APP-ERROR:<rc>
# 124 is timeout's own status. 137 (SIGKILL) after TIMEOUT seconds have elapsed is timeout's
# own -k kill of a launcher that ignored SIGTERM (Open MPI's does when its ranks are
# deadlocked), so it is a hang too; before that it is a memory cap or a kill, not a hang.
# The same rule as stretto.confirm.classify_rc. Only the launcher's stderr is read, and only
# for MPI error names.
classify_rc() {
    local rc="$1" log="${2:-/dev/null}"
    case "$rc" in
        0) echo FINISHED;;
        124) echo TIMEOUT;;
        137) if [ "${MPI_RUN_SECONDS:-0}" -ge "${TIMEOUT:-0}" ] && [ "${TIMEOUT:-0}" -gt 0 ]; then echo TIMEOUT; else echo KILLED; fi;;
        *)  if grep -qoE 'MPI_ERR_[A-Z_]+' "$log" 2>/dev/null; then echo "MPI-ERROR:$(grep -oE 'MPI_ERR_[A-Z_]+' "$log" | head -1)";
            elif [ "$rc" -ge 128 ]; then echo "SIGNAL:$((rc-128))";
            else echo "APP-ERROR:$rc"; fi;;
    esac
}

need_lib() { [ -f "$1" ] || { echo "missing $1: build it with make -C $2 MPI=$MPI" >&2; exit 1; }; }

idiom_dir() { ls -d "$ROOT"/idioms/"$1"_* 2>/dev/null | head -1; }
idiom_np()  { case "$1" in 03|07) echo 2;; 10|12|13) echo 4;; *) echo 3;; esac; }
