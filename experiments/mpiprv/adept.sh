#!/usr/bin/env bash
# Is the bug MPI-PRV reports in its motivating example real? The example is the overlapped
# 27-point stencil of Adept Kernel MPI (stencil.c, double_stencil27_overlapped): each rank posts
# its halo sends and receives, calls the marker usea0(), runs the interior loop, waits for the
# receives, updates the two edge planes and copies a1 back into a0. MPI-PRV reports the
# pattern Irecv Use Isend: a receive, the marker, then a send posted after the marker.
# Inside MPI-PRV's image (experiments/mpiprv/prv.sh), from the example's own sources, built
# without instrumentation (-O3 -g, as its platform file but with mpicc):
#   build   the kernel as shipped; "fixed", the same with its two halo sends completed
#           (experiments/mpiprv/adept_variants.py: the original never waits for them, and its
#           copy back overwrites their buffers); "mutant", the fixed kernel with its waits moved
#           after the edge updates, which then read the halo planes while their receives may be
#           pending: the positive control; experiments/mpiprv/adept_shim.c with and without its
#           MPI wrappers
#   runs    4 ranks, -b stencil -o 27 -s 512 -d double (the command line of the example's
#           MUST.sh), each printing the hashes of a0 and a1 at their free():
#             normal          -a normal: blocking receives before any computation (the reference)
#             overlapped-1/2  -a overlapped as shipped, twice
#             fixed           -a overlapped, fixed
#             fixed-delay1/2  fixed, with rank 1 (rank 2) sleeping DELAY ms before each of its
#                             MPI_Isend calls: its neighbours call the marker, run the interior
#                             loop and reach their MPI_Wait before the matching send is posted,
#                             the reordering MPI-PRV reports
#             mutant, mutant-delay1   the mutant, plain and with rank 1 delayed
#             guard, guard-fixed      shipped and fixed, under Stretto's tracer with STRETTO_GUARD=1
#   summary the guard records by source line (addr2line), the guard runs' reports, and
#           experiments/mpiprv/adept.py over the logs
# The logs stay in experiments/mpiprv/work/adept/ (git-ignored); the summary goes to
# experiments/results/mpiprv/: adept.csv, adept-guard.txt, adept-variants.diff.
# usage (repository root, on the host): experiments/mpiprv/prv.sh experiments/mpiprv/adept.sh [build runs summary] ...
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
RES=experiments/results/mpiprv; mkdir -p "$RES"
COMMIT="${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || echo unknown)}"
W="$R/experiments/mpiprv/work/adept"; LOG="$W/logs"; mkdir -p "$LOG"
E=/home/ubuntu/Example/adept-kernel-mpi
DELAY="${DELAY:-2500}"
ARGS="-b stencil -o 27 -s 512 -d double"
SRC="main.c level1.c blas_op.c utils.c stencil.c fileparse.c matrix_utils.c cg.c"

step_build() {
    rm -rf "$W/src" "$W/fixed" "$W/mutant"
    for d in src fixed mutant; do
        mkdir -p "$W/$d"
        # main.c and blas_op.c include the monitor's header (declarations only)
        cp $E/*.c $E/*.h $E/MPIaspect/__RVC_MPI_Monitor.h "$W/$d/"
        [ $d = src ] || python3 experiments/mpiprv/adept_variants.py $d "$E/stencil.c" > "$W/$d/stencil.c" || return 1
        (cd "$W/$d" && mpicc -O3 -g -o kernel $SRC -lm 2> build.err) || { cat "$W/$d/build.err"; return 1; }
    done
    { echo "# experiments/mpiprv/adept_variants.py fixed"; diff "$E/stencil.c" "$W/fixed/stencil.c"
      echo "# experiments/mpiprv/adept_variants.py mutant"; diff "$E/stencil.c" "$W/mutant/stencil.c"; } > "$RES/adept-variants.diff"
    mpicc -O2 -shared -fPIC -o "$W/sum.so" experiments/mpiprv/adept_shim.c -ldl || return 1
    mpicc -O2 -shared -fPIC -DORDER -o "$W/order.so" experiments/mpiprv/adept_shim.c -ldl || return 1
    cat "$RES/adept-variants.diff"
}

# one <name> <dir> <algo> <preload> [env ...]: one run of the kernel at 4 ranks
one() {
    local name=$1 dir=$2 algo=$3 pre=$4; shift 4
    local env=(); for e in "$@"; do env+=(-x "$e"); done
    echo "# $name: $dir/kernel $ARGS -a $algo LD_PRELOAD=$(basename "$pre") ${*}; commit $COMMIT; $(date -u +%Y-%m-%dT%H:%MZ)" > "$LOG/$name.log"
    /usr/bin/timeout 3600 mpirun -np 4 -x LD_PRELOAD="$pre" "${env[@]}" "$W/$dir/kernel" $ARGS -a "$algo" > "$W/$name.out" 2> "$W/$name.err"
    echo "rc $?" >> "$LOG/$name.log"
    grep -v '^usea0$' "$W/$name.out" >> "$LOG/$name.log"
    cat "$W/$name.err" >> "$LOG/$name.log"
}

step_runs() {
    one normal src normal "$W/sum.so"
    one overlapped-1 src overlapped "$W/order.so"
    one overlapped-2 src overlapped "$W/order.so"
    one fixed fixed overlapped "$W/order.so"
    one fixed-delay1 fixed overlapped "$W/order.so" ADEPT_DELAY=1:$DELAY
    one fixed-delay2 fixed overlapped "$W/order.so" ADEPT_DELAY=2:$DELAY
    one mutant mutant overlapped "$W/order.so"
    one mutant-delay1 mutant overlapped "$W/order.so" ADEPT_DELAY=1:$DELAY
    # the buffer guard; no shim
    for g in guard:src guard-fixed:fixed; do
        rm -rf "$W/${g%%:*}.trace"; mkdir -p "$W/${g%%:*}.trace"
        one "${g%%:*}" "${g#*:}" overlapped "$R/tracer/bin/openmpi/libstretto-trace.so" STRETTO_TRACE_DIR="$W/${g%%:*}.trace" STRETTO_GUARD=1
    done
}

step_summary() {
    for g in guard:src guard-fixed:fixed; do
        local name=${g%%:*} dir=${g#*:} t="$W/${g%%:*}.trace"
        cat "$t"/rank-*.jsonl | grep '"op":"guard"' > "$W/$name-records.jsonl"
        echo "# $name: Adept Kernel MPI ($dir), -a overlapped $ARGS, 4 ranks, Stretto's tracer with STRETTO_GUARD=1; run at $(sed -n 's/.*; commit \([^;]*\);.*/commit \1/p' "$LOG/$name.log" | head -1), summarised at commit $COMMIT"
        echo "# guard records: count, site (module+offset), function and source line (addr2line -f -i)"
        python3 experiments/mpiprv/adept.py --sites "$t" "$W/$dir/kernel"
        echo "# guard records by kind"
        grep -oE '"what":"[a-z]+"(,"req":[0-9]+)?(,"kind":"[a-z]+")?' "$W/$name-records.jsonl" | sed -E 's/,"req":[0-9]+//' | sort | uniq -c
        echo "# stretto analyze --no-sp: the findings by class, then the first two"
        PYTHONPATH="$R/analysis" python3 -m stretto analyze --no-sp "$t" > "$W/$name-report.txt" 2>&1
        grep -oE "^\[ *[0-9a-z-]+ *\]" "$W/$name-report.txt" | tr -d "[] " | sort | uniq -c
        head -9 "$W/$name-report.txt"
        echo
    done > "$RES/adept-guard.txt"
    python3 experiments/mpiprv/adept.py "$LOG" "$W" > "$RES/adept.csv"
    cat "$RES/adept.csv"
}

steps=("$@"); [ ${#steps[@]} -eq 0 ] && steps=(build runs summary)
for s in "${steps[@]}"; do "step_$s" || exit 1; done
