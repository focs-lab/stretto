#!/usr/bin/env bash
# The tracer's buffer guard (STRETTO_GUARD=1, local concurrency, class 3-local) on the real
# stack, inside the experiment container (experiments/container/run.sh), from the repository
# root, with the tracer built for both MPI implementations:
#   scenarios  experiments/guard/scenarios.c under both MPIs: allowed and erroneous accesses,
#              page sharing, holes in a datatype, persistent reuse, MPI_IN_PLACE, buffered
#              sends, Mprobe/Imrecv, and large exchanges while the program polls and computes;
#              each case names the guard records it must give, and the run must finish
#   lc         the LocalConcurrency programs of MPI-BugBench level 2 under both MPIs (Open MPI
#              4.1.6 lacks MPI_Pready, so the two partitioned ones build with MPICH only)
#   apps       NPB LU (S, A), HYPRE 2.10.1 ij -solver 8 -pout 0 and AMG at 4 ranks, guard off
#              and on: the program's own check, records, guard records, classes, wall time and
#              NPB's own timer; REPS repetitions (default 1)
# The container's default seccomp profile refuses process_vm_readv/writev, so the single-copy
# transports (Open MPI's CMA, UCX's cma) fall back to copies; with DOCKER_ARGS="--cap-add
# SYS_PTRACE" on experiments/container/run.sh they work, and the results go to <step>-cma.txt.
# Every run preloads experiments/guard/cmacount.c after the tracer, which prints how many
# single-copy calls the library made and how many failed. STRETTO_PAYLOAD=<bytes> in the
# environment records message payloads too (level L2's re-execution), to check that the two
# coexist; the results then go to <step>[-cma]-payload.txt and count the records with a payload.
# Writes experiments/results/guard/{scenarios,lc,apps}[-cma][-payload].txt.
# usage: experiments/guard/run.sh [scenarios|lc|apps|all] ...
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
OUT=experiments/results/guard; mkdir -p "$OUT"
W=/tmp/stretto-guard-run; mkdir -p "$W"
source "$R/experiments/harness.sh"
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_hwloc_base_binding_policy=none OMP_NUM_THREADS=1 PYTHONPATH="$R/analysis"
COMMIT="${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || git -C "$R" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
gcc -O1 -shared -fPIC -o "$W/cmacount.so" experiments/guard/cmacount.c -ldl || exit 1
single_copy() {   # does process_vm_readv work here?
    python3 -c '
import ctypes, os, sys
class iov(ctypes.Structure): _fields_ = [("b", ctypes.c_void_p), ("n", ctypes.c_size_t)]
libc = ctypes.CDLL(None, use_errno=True)
src, dst = ctypes.create_string_buffer(b"stretto", 8), ctypes.create_string_buffer(8)
l, r = iov(ctypes.addressof(dst), 8), iov(ctypes.addressof(src), 8)
sys.exit(0 if libc.process_vm_readv(os.getpid(), ctypes.byref(l), 1, ctypes.byref(r), 1, 0) == 8 else 1)'
}
if single_copy; then TAG=-cma; SC=on; else TAG=; SC="off (process_vm_readv refused)"; fi
PAYLOAD="${STRETTO_PAYLOAD:-0}"; [ "$PAYLOAD" -gt 0 ] && TAG="$TAG-payload"
header() { echo "# $1; commit $COMMIT; host ${MACHINE:-$(hostname)}; $(date -u +%Y-%m-%dT%H:%MZ); single-copy $SC; payloads up to $PAYLOAD bytes"; }
# run <dir> <mpi> <guard> <np> <exe> [args]: one traced run; sets rc
run() {
    local d=$1 mpi=$2 g=$3 np=$4; shift 4
    # ld.so would ignore a missing library, and the run would show no guard record
    need_file "$R/tracer/bin/$mpi/libstretto-trace.so" "$W/cmacount.so" || exit 1
    rm -rf "$d"; mkdir -p "$d/trace"
    (cd "$d" && /usr/bin/timeout -k 5 900 /usr/bin/mpirun.openmpi -np "$np" -x STRETTO_TRACE_DIR="$d/trace" \
        -x LD_PRELOAD="$R/tracer/bin/$mpi/libstretto-trace.so:$W/cmacount.so" -x STRETTO_GUARD="$g" -x STRETTO_PAYLOAD="$PAYLOAD" \
        -x OMP_NUM_THREADS=1 "$@" > "$d/out" 2> "$d/err" < /dev/null)
    rc=$?
}
# records carrying a payload (data, din or dout)
payloads() { cat "$1"/trace/rank-*.jsonl 2>/dev/null | grep -cE '"(data|din|dout)(big)?":'; }
# the guard records of a run as what[:kind]*count, e.g. "access:write*2 overlap*1"
guards() {
    cat "$1"/trace/rank-*.jsonl 2>/dev/null | grep -oE '"op":"guard","what":"[a-z]+"(,"req":[0-9]+,"kind":"[a-z]+")?' \
        | sed -E 's/.*"what":"([a-z]+)"(,"req":[0-9]+,"kind":"([a-z]+)")?/\1:\3/; s/:$//' | sort | uniq -c \
        | awk '{printf "%s%s*%s", (NR > 1 ? " " : ""), $2, $1}'
}
singlecopy() { grep -h '^\[single-copy\]' "$1/err" | sed 's/\[single-copy\] //' | sort | uniq -c | awk '{$1 = $1 "x"; print}' | paste -sd ';' -; }
classes() { python3 -m stretto analyze --no-sp "$1/trace" 2>/dev/null | grep -oE '^\[ *[0-9a-z-]+ *\]' | tr -d '[] ' | sort | uniq -c | awk '{printf "%s%s*%s", (NR > 1 ? " " : ""), $2, $1}'; }

step_scenarios() {
    header "scenarios: case, exit status, expected and found guard records"
    for mpi in openmpi mpich; do
        /usr/bin/mpicc.$mpi -O1 -g -Wall -o "$W/scenarios.$mpi" experiments/guard/scenarios.c || return 1
        local pass=0 n=0
        for s in $(seq 1 19); do
            run "$W/sc.$mpi.$s" $mpi 1 2 "$W/scenarios.$mpi" $s
            local d="$W/sc.$mpi.$s" exp got ok=1
            exp=$(sed -n 's/^expect //p' "$d/out" | head -1); got=$(guards "$d")
            [ $rc -eq 0 ] || ok=0
            if [ "$exp" = none ]; then [ -z "$got" ] || ok=0; else echo " $got" | grep -q " ${exp}\*" || ok=0; fi
            n=$((n + 1)); [ $ok = 1 ] && pass=$((pass + 1))
            printf '%-8s %2s rc=%-3s expect=%-13s got=[%s] %s payloads=%s single-copy=[%s]\n' $mpi $s $rc "$exp" "$got" "$([ $ok = 1 ] && echo ok || echo FAIL)" "$(payloads "$d")" "$(singlecopy "$d")"
        done
        echo "$mpi: $pass of $n cases as expected"
    done
}
step_lc() {
    header "MPI-BugBench LocalConcurrency programs: exit status, guard records, Stretto classes"
    for mpi in openmpi mpich; do
        local found=0 built=0
        for f in third_party/mpi-bugbench/scripts/gencodes/{P2P,COLL}/LocalConcurrency-*.c; do
            local n; n="$(basename "$(dirname "$f")")__$(basename "$f" .c)"
            if ! /usr/bin/mpicc.$mpi -O0 -g -o "$W/$n.$mpi" "$f" -lm 2> /dev/null; then printf '%-8s %-50s not built\n' $mpi "$n"; continue; fi
            built=$((built + 1))
            run "$W/lc.$mpi.$n" $mpi 1 2 "$W/$n.$mpi"
            local c; c=$(classes "$W/lc.$mpi.$n")
            echo " $c" | grep -q " 3-local" && found=$((found + 1))
            printf '%-8s %-50s rc=%-3s guard=[%s] classes=[%s] payloads=%s\n' $mpi "$n" $rc "$(guards "$W/lc.$mpi.$n")" "$c" "$(payloads "$W/lc.$mpi.$n")"
        done
        echo "$mpi: 3-local in $found of $built programs built"
    done
}
step_apps() {
    header "applications at 4 ranks, guard off and on"
    local npb=third_party/apps/NPB-mpich/bin
    [ -d "$npb" ] || npb=third_party/apps/NPB-mpich/NPB3.4-MPI/bin
    for rep in $(seq 1 "${REPS:-1}"); do for g in 0 1; do for w in ${APPS:-lu.S lu.A hypre amg}; do
        local d="$W/app.$w.$g.$rep" mpi=mpich t0 t1
        t0=$(date +%s.%N)
        case $w in
            lu.*) run "$d" mpich $g 4 "$R/$npb/$w.x" ;;
            hypre) run "$d" mpich $g 4 "$R/third_party/apps/hypre/hypre-2.10.1/test/ij" -solver 8 -pout 0 ;;
            amg) mpi=openmpi; run "$d" openmpi $g 4 "$R/study/apps/AMG/test/amg" -P 2 2 1 -n 16 16 16 -problem 1 ;;
        esac
        t1=$(date +%s.%N)
        local check npbt
        check=$(grep -m1 -E "Verification +=|Final Relative Residual Norm|Iterations =" "$d/out" | tr -s ' ' | sed 's/^ //')
        npbt=$(sed -n 's/^ *Time in seconds *= *//p' "$d/out" | head -1)
        printf '%-6s %-7s guard=%s rep=%s rc=%-3s wall=%6.2fs npb=%-6s records=%-7s guard=[%s] check=[%s] classes=[%s] payloads=%s single-copy=[%s]\n' \
            $w $mpi $g $rep $rc "$(echo "$t1 - $t0" | bc)" "${npbt:--}" "$(cat "$d"/trace/rank-*.jsonl | wc -l)" \
            "$(guards "$d")" "$check" "$(classes "$d")" "$(payloads "$d")" "$(singlecopy "$d")"
    done; done; done
}
steps=("$@"); [ ${#steps[@]} -eq 0 ] || [ "${steps[0]}" = all ] && steps=(scenarios lc apps)
for s in "${steps[@]}"; do "step_$s" | tee "$OUT/$s$TAG.txt"; done
