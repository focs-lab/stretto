#!/usr/bin/env bash
# Second real application: Nekbone's communication suite (study/apps/Nekbone/test/nek_comm,
# PPLIST=NEKCOMM), the build in which the wildcard-source wrappers of src/comm_mpi.f (crecv,
# irecv: MPI_ANY_SOURCE with the tag as the only selector) are reached, by the ping-pong,
# message-volume and tree-reduction tests whose tag protocol makes every sender unique. Built
# with the MPICH wrappers, since Open MPI's Fortran bindings bypass C interposition, traced at
# 8 ranks and analysed. Writes experiments/results/nekbone/{trace8,report8.txt,analyze8.err,
# summary8.txt,run8.log}; summary8.txt is committed.
# usage: experiments/nekbone/run.sh [build|trace|analyse|summary|all]
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
MPI=mpich; source scripts/env.sh
D=experiments/results/nekbone; mkdir -p "$D"
SRC=study/apps/Nekbone; CASE=$SRC/test/nek_comm; EXE=$CASE/nekbone
export OMP_NUM_THREADS=1 OMPI_MCA_hwloc_base_binding_policy=none
step_build() {
    [ -d "$SRC/src" ] || { echo "$SRC missing: study/fetch.sh" >&2; return 1; }
    # makenek fixes the compilers and the source path inside the script: a local copy sets them
    sed -e "s|^SOURCE_ROOT=.*|SOURCE_ROOT=\"$R/$SRC/src\"|" -e 's|^F77=.*|F77="mpif77.mpich"|' -e 's|^CC=.*|CC="mpicc.mpich"|' \
        "$CASE/makenek" > "$CASE/makenek.local"
    (cd "$CASE" && rm -f nekbone && bash makenek.local nekbone > "$R/$D/build.log" 2>&1)
    [ -x "$EXE" ] && echo "built $EXE" || { echo "build failed: $(tail -n 3 "$D/build.log")" >&2; return 1; }
}
step_trace() {
    [ -x "$EXE" ] || { echo "$EXE missing: $0 build" >&2; return 1; }
    need_lib "$TRACER" tracer
    rm -rf "$D/trace8"; mkdir -p "$D/trace8"
    (cd "$CASE" && TIMEOUT=600 mpi_run 8 "REMATCH_TRACE_DIR=$R/$D/trace8" "LD_PRELOAD=$TRACER" "OMP_NUM_THREADS=1" -- ./nekbone data > "$R/$D/run8.log" 2>&1)
    echo "traced run: $(classify_rc $? "$D/run8.log"), $(cat "$D"/trace8/rank-*.jsonl | wc -l) records"
}
step_analyse() {
    /usr/bin/time -f 'analysis %e s, %M KB' python3 -m rematch analyze --no-sp --verbose "$D/trace8" > "$D/report8.txt" 2> "$D/analyze8.err"
    step_summary
}
step_summary() {
    # summary8.txt is what docs/results.md reads and what is committed
    { head -1 "$D/report8.txt"
      echo "findings by class: $(grep -o '^\[ *[0-9a-z-]* *\]' "$D/report8.txt" | tr -d '[] ' | sort | uniq -c | awk '{printf "%s %s, ", $2, $1}' | sed 's/, $//')"
      echo "rejected alternatives: $(grep -c "^  x " "$D/report8.txt")"
      echo "host: $(hostname), one process; $(tail -1 "$D/analyze8.err")"
    } > "$D/summary8.txt"
    cat "$D/summary8.txt"
}
for step in "${@:-all}"; do
    case "$step" in
        build) step_build;; trace) step_trace;; analyse) step_analyse;; summary) step_summary;;
        all) step_build && step_trace && step_analyse;;
        *) echo "usage: $0 [build|trace|analyse|summary|all]" >&2; exit 2;;
    esac
done
