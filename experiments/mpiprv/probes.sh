#!/usr/bin/env bash
# Small correct programs that ask MPI-PRV for a reordering no execution has, inside its image
# (experiments/mpiprv/prv.sh). Each case is instrumented with wac, run REPS times, and every
# trace analysed with the case's pattern; a "Pattern Matched!" on a case whose expectation is
# "none" reports a reordering that no execution has, or one that is not the error the
# property names.
#   wild_irecv  MPI_Irecv, MPI_Wait, then the marker; the shipped receive-buffer property
#               (RQ1/MPI3.mop: Irecv Use Isend), with the sender's rank and with MPI_ANY_SOURCE
#   wild_recv   MPI_Recv, then the marker; pattern Recv Use Send (probes/RecvUseSend.mop)
#   order       marker A on rank 0 before a synchronisation, marker B on rank 1 after it;
#               pattern B A (no execution) and A B (the control, the observed order), with
#               MPI_Barrier and with a blocking send and receive
#   unrelated   four ranks: 1 sends to 0 (MPI_Irecv, MPI_Wait, then the marker), later 2 sends
#               to 3 with MPI_Isend; the shipped receive-buffer property
#   lc          MPI-BugBench's LocalConcurrency-receive-mpi_irecv-001 with the marker at its
#               labelled access (markers.py oracle), and its corrected twin, the access and
#               marker moved after the MPI_Wait (markers.py twin); the shipped receive-buffer
#               property, TWIN_REPS runs each (default 20), since the order in which the ranks
#               append their records to the shared trace varies; the twin a second time with
#               four busy loops on the same processors
# Writes experiments/results/mpiprv/probes.csv and, for the first repetition of each case,
# its trace as probes-<case>.trace.
# usage (repository root, on the host): experiments/mpiprv/prv.sh experiments/mpiprv/probes.sh
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
OUT=experiments/results/mpiprv; mkdir -p "$OUT"
REPS="${REPS:-5}"
COMMIT="${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || echo unknown)}"
P=experiments/mpiprv/probes
W=/tmp/prv-probes; rm -rf "$W"; mkdir -p "$W"
cp /home/ubuntu/RQ1/MPI3.mop "$W/MPI3.mop"
cp $P/*.mop "$W/"
echo "case,source,define,pattern,expected,np,rep,result,matched,events,commit" > "$OUT/probes.csv"

# probe <case> <source> <define-line-from> <define-line-to> <mop> <expected> [np [reps]]
# (a source without a directory is taken from experiments/mpiprv/probes/)
probe() {
    local case=$1 path=$2 from=$3 to=$4 mop=$5 exp=$6 np=${7:-2} reps=${8:-$REPS} d="$W/$1"
    local src; src=$(basename "$path"); [ "$src" = "$path" ] && path="$P/$src"
    mkdir -p "$d"; rm -f "$d"/*.txt
    sed "s/^$from\$/$to/" "$path" > "$d/$src"
    cp "$W/$mop" "$d/"
    (cd "$d" && wac -mop "$mop" "$src" -o prog -np -MPI) > "$d/wac.log" 2>&1
    local pat; pat=$(sed -n 's/^Pattern : {\(.*\)}/\1/p' "$d/${mop%.mop}aspect/Pattern.txt" | tr ',' ' ')
    for rep in $(seq 1 "$reps"); do
        rm -f "$d/output.txt"
        (cd "$d" && /usr/bin/timeout 60 mpirun -np "$np" ./prog) > "$d/run$rep.log" 2>&1
        (cd "$d" && MPI-PRV -trace output.txt -pattern "./${mop%.mop}aspect/Pattern.txt" -np "$np") > "$d/prv$rep.log" 2>&1
        local res=none m=""
        if grep -q "Pattern Matched!" "$d/prv$rep.log"; then res=matched; m=$(grep -A1 "Pattern Matched!" "$d/prv$rep.log" | tail -1 | sed 's/ *$//'); fi
        [ "$rep" = 1 ] && cp "$d/output.txt" "$OUT/probes-$case.trace"
        echo "$case,$src,\"$to\",$pat,$exp,$np,$rep,$res,\"$m\",$(grep -c '^event' "$d/output.txt"),$COMMIT" >> "$OUT/probes.csv"
    done
}

ANY='#define SRC MPI_ANY_SOURCE'
probe wild_irecv-src1 wild_irecv.c "$ANY" '#define SRC 1' MPI3.mop none
probe wild_irecv-any  wild_irecv.c "$ANY" "$ANY" MPI3.mop none
probe wild_recv-src1  wild_recv.c  "$ANY" '#define SRC 1' RecvUseSend.mop none
probe wild_recv-any   wild_recv.c  "$ANY" "$ANY" RecvUseSend.mop none
B0='#define SYNC 0'
probe order-barrier-BA  order.c "$B0" "$B0" BA.mop none
probe order-barrier-AB  order.c "$B0" "$B0" AB.mop matched
probe order-sendrecv-BA order.c "$B0" '#define SYNC 1' BA.mop none
probe order-sendrecv-AB order.c "$B0" '#define SYNC 1' AB.mop matched
probe unrelated unrelated.c '#include <mpi.h>' '#include <mpi.h>' MPI3.mop none 4
LC=third_party/mpi-bugbench/scripts/gencodes/P2P/LocalConcurrency-receive-mpi_irecv-001.c
if [ -f "$LC" ]; then
    python3 experiments/mpiprv/markers.py oracle "$LC" > "$W/lc_error.c"
    python3 experiments/mpiprv/markers.py twin "$LC" > "$W/lc_twin.c"
    probe lc-error "$W/lc_error.c" '#include <mpi.h>' '#include <mpi.h>' MPI3.mop matched 2 "${TWIN_REPS:-20}"
    probe lc-twin  "$W/lc_twin.c"  '#include <mpi.h>' '#include <mpi.h>' MPI3.mop none 2 "${TWIN_REPS:-20}"
    # the twin again while four busy loops share the container's processors: the ranks then
    # append their records to the shared trace in more orders
    for i in 1 2 3 4; do (while :; do :; done) & done
    probe lc-twin-loaded "$W/lc_twin.c" '#include <mpi.h>' '#include <mpi.h>' MPI3.mop none 2 "${TWIN_REPS:-20}"
    kill $(jobs -p) 2>/dev/null; wait 2>/dev/null
fi
cat "$OUT/probes.csv"
