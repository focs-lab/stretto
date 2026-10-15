# Shared environment for the Rematch scripts. System MPI only.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MPI="${MPI:-openmpi}"
# Ubuntu's MPICH is PMIx-based and its hydra starts singletons; Open MPI's mpirun launches
# MPICH binaries correctly, so it is the launcher for both.
MPIRUN="/usr/bin/mpirun.openmpi"
TRACER="$ROOT/tracer/bin/$MPI/librematch-trace.so"
PINLIB="$ROOT/replay/bin/$MPI/librematch-pin.so"
export PYTHONPATH="$ROOT/analysis${PYTHONPATH:+:$PYTHONPATH}"
export OMPI_MCA_rmaps_base_oversubscribe=1
TIMEOUT="${TIMEOUT:-15}"

# mpi_run <np> <NAME=VALUE ...> -- <exe> [args]
# Environment goes to the ranks only (mpirun -x NAME=VALUE), never to this shell.
# stdin is /dev/null; the whole process group is killed on timeout.
mpi_run() {
    local np="$1"; shift
    local xargs=()
    while [ "$1" != "--" ]; do xargs+=(-x "$1"); shift; done; shift
    timeout --foreground -k 5 "$TIMEOUT" "$MPIRUN" -np "$np" "${xargs[@]}" "$@" < /dev/null
}

# classify_rc <rc> [stderr-file] -> FINISHED | TIMEOUT | MPI-ERROR:<name> | SIGNAL:<n> | APP-ERROR:<rc>
classify_rc() {
    local rc="$1" log="${2:-/dev/null}"
    case "$rc" in
        0) echo FINISHED;;
        124|137) echo TIMEOUT;;
        *)  if grep -qoE 'MPI_ERR_[A-Z_]+' "$log" 2>/dev/null; then echo "MPI-ERROR:$(grep -oE 'MPI_ERR_[A-Z_]+' "$log" | head -1)";
            elif grep -qiE 'message truncated|truncat' "$log" 2>/dev/null; then echo "MPI-ERROR:MPI_ERR_TRUNCATE";
            elif [ "$rc" -ge 128 ]; then echo "SIGNAL:$((rc-128))";
            else echo "APP-ERROR:$rc"; fi;;
    esac
}

idiom_dir() { ls -d "$ROOT"/idioms/"$1"_* 2>/dev/null | head -1; }
idiom_np()  { case "$1" in 03|07) echo 2;; 10) echo 4;; *) echo 3;; esac; }
