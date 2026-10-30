#!/usr/bin/env bash
# deal.II 9.3.0's FETools::extrapolate on a parallel::distributed::Triangulation reuses one send
# buffer for every MPI_Isend of a round (fe_tools_extrapolate.templates.h, send_cells: the
# iterator over the send buffers is never advanced), so each cell's data is packed into the
# buffer of the previous, still pending send. Fixed upstream in PR 13206 (b3b2b97, 9 Jan 2022),
# whose description says it went unnoticed because the libraries copy small messages in the call.
# The buffer guard sees the writes in a clean run, the report says from which eager threshold
# they change messages (docs/model.md section 7), and runs with that threshold forced show it.
# Inside the experiment container (experiments/container/run.sh), from the repository root, after
# experiments/dealii/run.sh fetch build with DEALII_VERSIONS=9.3.0 (deal.II with p4est, MPICH):
#   build      the in-tree test mpi/fe_tools_extrapolate_03 (its sources from the v9.3.0 tag,
#              checked by sha256), linked as is and against the upstream fix: the one line
#              "++buffer;" added to send_cells, compiled into a library of the file's
#              instantiations that the fixed driver loads before libdeal_II
#   predict    RUNS (10) runs of each, traced with the guard and message sums at MPICH's
#              defaults: exit status, 3-local findings and their thresholds (predict.csv)
#   replay     RUNS untraced runs of each at the defaults, with every message through UCX by
#              rendezvous (MPIR_CVAR_NOLOCAL=1 UCX_RNDV_THRESH=1), and with UCX's threshold just
#              above and at the largest misused message: exit status and the test's own check
#              (replay.csv)
#   thresholds experiments/guard/thresholds.py on the test (thresholds.csv)
# Environment: DEALII_DIR (default third_party/apps/dealii), EXTRAPOLATE_DIR (default
# $DEALII_DIR/extrapolate), RUNS, MAKE_JOBS (default 2).
# usage: experiments/dealii13206/run.sh [build|predict|replay|thresholds|all] ...
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${DEALII_DIR:-$R/third_party/apps/dealii}"
E="${EXTRAPOLATE_DIR:-$D/extrapolate}"   # the test, its build and the fix
OUT="$R/experiments/results/dealii13206"; mkdir -p "$OUT"
RUNS="${RUNS:-10}"; NP=3; TIMEOUT=300
TRACER="$R/tracer/bin/mpich/libstretto-trace.so"
export OMPI_MCA_rmaps_base_oversubscribe=1 PYTHONPATH="$R/analysis"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
source "$R/experiments/harness.sh"
CMAKE="$D/cmake-3.28.3-linux-x86_64/bin/cmake"
TAG=9f731344ce4ed0ac2cab7e3273da48db35050316     # v9.3.0
TESTFILES="0cc36444714a2a4a004839e5c99285fb52f546f53bf93c5b84fe1a31d035ee44 tests.h
064e59d35656e757713a46200620fb6b6e82d5d70a332f00156b61703f896394 mpi/fe_tools_extrapolate_common.h
11e9a3b468b1caf29672551acc6f6b181fba718a986c51b9267c7efb43cb2973 mpi/fe_tools_extrapolate_03.cc
b8b74943f5c8bad42710a083d8658faa98d5bc7bd90b66b86c69e323ed02b15e mpi/fe_tools_extrapolate_03.mpirun=3.with_p4est=true.output"
BUG="$E/build/fe_tools_extrapolate_03"; FIX="$E/build/fe_tools_extrapolate_03_fix"

step_build() {
    need_file "$D/install-9.3.0/lib/libdeal_II.so.9.3.0" "$CMAKE" || return 1
    mkdir -p "$E/tree/mpi" "$E/build" "$E/fix/include/deal.II/fe"
    while read -r sha f; do
        [ -f "$E/tree/$f" ] || curl -sSfL -o "$E/tree/$f" "https://raw.githubusercontent.com/dealii/dealii/$TAG/tests/$f" || return 1
        echo "$sha  $E/tree/$f" | sha256sum -c --quiet - || return 1
    done <<< "$TESTFILES"
    cat > "$E/tree/CMakeLists.txt" <<'CMAKE'
CMAKE_MINIMUM_REQUIRED(VERSION 3.13)
FIND_PACKAGE(deal.II 9.3 REQUIRED HINTS ${DEAL_II_DIR})
DEAL_II_INITIALIZE_CACHED_VARIABLES()
PROJECT(extrapolate CXX)
ADD_EXECUTABLE(fe_tools_extrapolate_03 mpi/fe_tools_extrapolate_03.cc)
DEAL_II_SETUP_TARGET(fe_tools_extrapolate_03)
CMAKE
    (cd "$E/build" && "$CMAKE" -S "$E/tree" -B . -DDEAL_II_DIR="$D/install-9.3.0" > cmake.log 2>&1 \
        && make -j"${MAKE_JOBS:-2}" > make.log 2>&1) || { echo "build of the test failed" >&2; return 1; }
    # the fix of PR 13206 on the 9.3.0 template: advance the buffer after each send
    local h="$E/fix/include/deal.II/fe/fe_tools_extrapolate.templates.h"
    sed '/std::vector<std::vector<char>>::iterator buffer = sendbuffers.begin();/,/^        }$/ s/^          AssertThrowMPI(ierr);$/&\n          ++buffer;/' \
        "$D/install-9.3.0/include/deal.II/fe/fe_tools_extrapolate.templates.h" > "$h"
    [ "$(grep -c '^          ++buffer;$' "$h")" = 1 ] || { echo "the fix did not apply" >&2; return 1; }
    /usr/bin/mpicxx.mpich -DBOOST_NO_AUTO_PTR -I"$E/fix/include" -I"$D/build-9.3.0/source/fe" -I"$D/build-9.3.0/include" \
        -I"$D/dealii-9.3.0/include" -I"$D/dealii-9.3.0/bundled/boost-1.70.0/include" -isystem "$D/p4est/include" \
        -fPIC -O2 -fopenmp-simd -shared -o "$E/fix/libextrapolate_fix.so" "$D/dealii-9.3.0/source/fe/fe_tools_extrapolate.cc" || return 1
    (cd "$E/build" && /usr/bin/mpicxx.mpich -rdynamic -fuse-ld=gold CMakeFiles/fe_tools_extrapolate_03.dir/mpi/fe_tools_extrapolate_03.cc.o \
        -o "$FIX" -Wl,--no-as-needed "$E/fix/libextrapolate_fix.so" -Wl,-rpath,"$E/fix:$D/install-9.3.0/lib:$D/p4est/lib" \
        "$D/install-9.3.0/lib/libdeal_II.so.9.3.0" -lrt "$D/p4est/lib/libp4est.so" "$D/p4est/lib/libsc.so") || return 1
    echo "built $BUG and $FIX"
}
# one run in a fresh directory: run <dir> <exe> [mpirun -x arguments]; sets rc
run() {
    local d=$1 exe=$2; shift 2
    rm -rf "$d"; mkdir -p "$d"
    (cd "$d" && /usr/bin/timeout -k 5 $TIMEOUT /usr/bin/mpirun.openmpi -np $NP "$@" "$exe" > out 2> err < /dev/null)
    rc=$?
}
step_predict() {
    need_file "$BUG" "$FIX" "$TRACER" || return 1
    echo "variant,run,rc,records,guard_records,findings_3local,thresholds,sizes,commit" > "$OUT/predict.csv"
    for v in bug fix; do
        local exe=$BUG; [ $v = fix ] && exe=$FIX
        for i in $(seq 1 "$RUNS"); do
            local d=/tmp/stretto-x13206/predict.$v.$i
            run "$d" "$exe" -x STRETTO_TRACE_DIR="$d/trace" -x LD_PRELOAD="$TRACER" -x STRETTO_GUARD=1 -x STRETTO_SUMS=1
            python3 -m stretto analyze --no-sp "$d/trace" > "$d/report.txt" 2>&1
            local n th sz
            n=$(grep -c '^\[ *3-local\]' "$d/report.txt")
            th=$(grep '^\[ *3-local\]' "$d/report.txt" | grep -oE '\[[^]]*\]$' | sort | uniq -c | awk '{$1=$1 "x"; print}' | paste -sd ';' -)
            sz=$(grep '^\[ *3-local\]' "$d/report.txt" | grep -oE '\(([0-9]+) B\)' | tr -dc '0-9\n' | sort -n | uniq | paste -sd ' ' -)
            echo "$v,$i,$rc,$(cat "$d"/trace/rank-*.jsonl | wc -l),$(cat "$d"/trace/rank-*.jsonl | grep -c '"op":"guard"'),$n,\"$th\",\"$sz\",$STRETTO_COMMIT" >> "$OUT/predict.csv"
            echo "predict $v $i: rc=$rc 3-local=$n sizes=[$sz]"
            rm -rf "$d"
        done
    done
}
step_replay() {
    need_file "$BUG" "$FIX" "$OUT/predict.csv" || return 1
    # the largest message a 3-local finding of the bug's runs is about
    local max; max=$(awk -F, '$1 == "bug" {gsub(/"/, "", $8); n = split($8, a, " "); for (i = 1; i <= n; i++) if (a[i] + 0 > m) m = a[i] + 0} END {print m + 0}' "$OUT/predict.csv")
    [ "$max" -gt 0 ] || { echo "no 3-local finding in predict.csv" >&2; return 1; }
    echo "variant,setting,threshold,run,rc,check,commit" > "$OUT/replay.csv"
    for v in bug fix; do
        local exe=$BUG; [ $v = fix ] && exe=$FIX
        for s in default rendezvous below-largest at-largest; do
            local args=() thr=-
            case $s in
                rendezvous) thr=0; args=(-x MPIR_CVAR_NOLOCAL=1 -x UCX_RNDV_THRESH=1) ;;
                below-largest) thr=$((max - 1)); args=(-x MPIR_CVAR_NOLOCAL=1 -x UCX_RNDV_THRESH=$max) ;;
                at-largest) thr=$max; args=(-x MPIR_CVAR_NOLOCAL=1 -x UCX_RNDV_THRESH=$((max + 1))) ;;
            esac
            local bad=0
            for i in $(seq 1 "$RUNS"); do
                local d=/tmp/stretto-x13206/replay.$v.$s.$i check=passed
                run "$d" "$exe" ${args[@]+"${args[@]}"}
                grep -q "violated condition" "$d/err" && check=failed
                [ $rc = 0 ] || bad=$((bad + 1))
                echo "$v,$s,$thr,$i,$rc,$check,$STRETTO_COMMIT" >> "$OUT/replay.csv"
                rm -rf "$d"
            done
            echo "replay $v $s (B=$thr): $bad of $RUNS runs failed"
        done
    done
}
step_thresholds() {
    need_file "$BUG" || return 1
    python3 experiments/guard/thresholds.py --mpi mpich --timeout $TIMEOUT --out "$OUT/thresholds.csv" "fe_tools_extrapolate_03=$NP:$BUG"
}
steps=("$@"); [ ${#steps[@]} -eq 0 ] || [ "${steps[0]}" = all ] && steps=(build predict replay thresholds)
for s in "${steps[@]}"; do "step_$s" || exit 1; done
