#!/usr/bin/env bash
# A documented race in deal.II's ConsensusAlgorithm_NBX, from the project's history (issue 8929,
# October 2019, never in a release).
#
# Utilities::MPI::ConsensusAlgorithm_NBX::run() (source/base/mpi.cc) Isends one request to each
# target with tag 12, then loops MPI_Iprobe(MPI_ANY_SOURCE, 12) and answers what it finds (tag
# 13) until the answers to its own requests are in, calls MPI_Ibarrier, and goes on probing and
# answering until the barrier completes. Successive calls on one communicator use the same tags
# and nothing separates them: a rank whose barrier has completed starts the next call and sends
# its requests while a slower rank still probes in the previous call, whose MPI_Iprobe can then
# take the next call's request and answer it from the old call's data. The issue reported a
# segmentation fault in ConsensusAlgorithmPayload::process_request at 1792 processes, in the
# multigrid transfer's set-up, which calls compute_index_owner() once per level ("multiple calls
# to the consensus algorithm (originating from different multigrid levels) get mixed up");
# ConsensusAlgorithmSelector took NBX only above 99 processes in a release build (14 in a debug
# build, whose NBX also ends with an MPI_Barrier) and PEX below.
#
# Upstream code (github.com/dealii/dealii):
#   2c1b27fbee49  master just before PR 8935 was merged (21 Oct 2019): the race (BEFORE)
#   dafd15549c17  merge of PR 8935, "add a barrier to avoid MPI race in ConsensusAlgorithm"
#                 (5bcf04365db3): an MPI_Barrier at the end of ConsensusAlgorithmPayload::
#                 get_requesters() only; source/base/mpi.cc and every other file but
#                 mpi_compute_index_owner_internal.h are those of 2c1b27fbee49, so a direct call
#                 of ConsensusAlgorithm_NBX is as before and this commit is not built
#   f9ea339613b8  merge of PR 8997, "use MPI::CollectiveMutex in several places" (4 Nov 2019,
#                 f2e40116dbd0 and 3f34fa26a604): run() takes a static CollectiveMutex on the
#                 communicator, whose lock and unlock are both MPI_Barrier (the Ibarrier variant
#                 is under "#if 0"): the fix of NBX itself (FIXED). PRs 8955 and 8956
#                 (duplicated communicators) were closed unmerged; v9.2.0 has the mutex.
# deal.II has no test of that time that runs NBX twice, so the driver,
# experiments/dealii8929/nbx_calls.cc, calls ConsensusAlgorithm_NBX directly CALLS times back to
# back on MPI_COMM_WORLD, every rank requesting from every other in every call, and checks that
# every answer comes from the rank asked, in the same call; rank 0 prints the count of wrong
# answers, "ok" when there are none.
#
# Steps, inside the experiment container, from the repository root, Open MPI build:
#   fetch    download both commits' archives and CMake 3.28.3 (the image has none), check their
#            sha256
#   build    each deal.II in Release with MPI and nothing else that can be switched off (no
#            p4est, threads, zlib, LAPACK, muparser; bundled Boost), -j$MAKE_JOBS, which gcc 13
#            compiles unpatched; then the driver against each, as deal.II builds user programs
#   predict  RUNS traced runs of the driver on BEFORE per process count of NPS, analysed at L1,
#            the findings of an error class replayed by stretto confirm, every cross alternative
#            (experiments/dealii8929/classify.py: a probe of one call that would see a request of
#            another, a flag flip) replayed with its horizon pins, the flip pinned too
#            (STRETTO_PIN_FLIP: the failed Iprobe waits for that request and returns it), and
#            with its control (the Iprobe pinned to its recorded false), JOBS traced runs at a
#            time (experiments/history_predict.py)
#   stress   STRESS_RUNS untraced runs of the driver on BEFORE per process count, plain and with
#            random send and receive delays from the replay library (experiments/history_stress.py)
#   fixed    predict and stress on FIXED
#   ninja    NINJA (experiments/ninja/build.sh, Open MPI library) on the driver at BEFORE, as on the
#            other cases of E5g: NINJA_RUNS runs per process count of NPS in its random (10% of the
#            sends by 50 ms), system- and application-centric modes, TIMEOUT seconds each; the
#            value is rank 0's count of wrong answers, and the marks are "ok" (its verdict of none)
#            and "wrong" (a rank printed a wrong answer) (experiments/ninja/run.py)
#   ninja-fixed  the same on FIXED
#   ninja-check  CHECK_RUNS runs at both commits and each process count, plain and with NINJA's
#            library loaded but no send delayed (random mode, a ratio of 1e-6%), as on deal.II
#            9.3.0-rc1 (experiments/dealii/run.sh ninja-check)
#   all      fetch build predict stress fixed
# Writes experiments/results/dealii8929/{predict,stress}-{2c1b27fb,f9ea3396}.csv and
# experiments/results/ninja/dealii8929{,-fix,-check}.csv.
# usage: experiments/dealii8929/run.sh [fetch|build|predict|stress|fixed|ninja|ninja-fixed|ninja-check|all]
#        NPS ("4 8"), RUNS (10), STRESS_RUNS (40), NINJA_RUNS (40), CHECK_RUNS (5), CALLS (3), TIMEOUT (60),
#        JOBS (2), MAKE_JOBS (12), DEALII8929_DIR (third_party/apps/dealii8929), MPI_CC, MPI_CXX and
#        MPI_FC (the build's MPI wrappers, default Open MPI's: experiments/must/apps.sh builds with MPICH's)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${DEALII8929_DIR:-$R/third_party/apps/dealii8929}"
OUT="$R/experiments/results/dealii8929"; mkdir -p "$OUT"
NPS="${NPS:-4 8}"; RUNS="${RUNS:-10}"; STRESS_RUNS="${STRESS_RUNS:-40}"; CALLS="${CALLS:-3}"; TIMEOUT="${TIMEOUT:-60}"
MPI_CC="${MPI_CC:-/usr/bin/mpicc.openmpi}"; MPI_CXX="${MPI_CXX:-/usr/bin/mpicxx.openmpi}"; MPI_FC="${MPI_FC:-/usr/bin/mpifort.openmpi}"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
# the container refuses process_vm_readv, the single-copy mechanism of Open MPI's shared-memory
# transport, which then prints a line per refused transfer (experiments/mfem332/run.sh)
export OMPI_MCA_btl_vader_single_copy_mechanism=none
BEFORE=2c1b27fbee49028f7de95863e44efe33d41f26ac
FIXED=f9ea339613b8041c4e42e0c8b9e7297aa0a823be
declare -A SHA=([dealii-$BEFORE.tar.gz]=8f45383aeea0af3cbc21154af816b6f1601e18a2753eb5e306222ce64f0ab244
                [dealii-$FIXED.tar.gz]=d05bdb13b054163c445c8ed9fa4f13ff29fc54cf3a45b1ceb3316298c7c2001a
                [cmake-3.28.3-linux-x86_64.tar.gz]=804d231460ab3c8b556a42d2660af4ac7a0e21c98a7f8ee3318a74b4a9a187a6)
declare -A URL=([dealii-$BEFORE.tar.gz]=https://github.com/dealii/dealii/archive/$BEFORE.tar.gz
                [dealii-$FIXED.tar.gz]=https://github.com/dealii/dealii/archive/$FIXED.tar.gz
                [cmake-3.28.3-linux-x86_64.tar.gz]=https://github.com/Kitware/CMake/releases/download/v3.28.3/cmake-3.28.3-linux-x86_64.tar.gz)
CMAKE="$D/cmake-3.28.3-linux-x86_64/bin/cmake"
OK='nbx_calls: [0-9]+ calls on [0-9]+ ranks, 0 wrong answers: ok'
short() { echo "${1:0:8}"; }
exe() { echo "$D/driver-$(short "$1")/nbx_calls"; }

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    local f
    for f in "${!URL[@]}"; do
        fetch_checked "$f" "${URL[$f]}" "${SHA[$f]}" || return 1
        [ -d "${f%.tar.gz}" ] || tar xzf "$f" || return 1
    done
    cd "$R"
}
step_build() {
    local j="${MAKE_JOBS:-12}" c v
    for c in $BEFORE $FIXED; do
        v=$(short $c)
        if [ ! -f "$D/install-$v/lib/libdeal_II.so" ]; then
            mkdir -p "$D/build-$v" && (cd "$D/build-$v" && "$CMAKE" -S "$D/dealii-$c" -B . -DCMAKE_BUILD_TYPE=Release \
                 -DCMAKE_C_COMPILER="$MPI_CC" -DCMAKE_CXX_COMPILER="$MPI_CXX" \
                 -DCMAKE_Fortran_COMPILER="$MPI_FC" -DMPI_Fortran_COMPILER="$MPI_FC" \
                 -DCMAKE_INSTALL_PREFIX="$D/install-$v" -DDEAL_II_ALLOW_AUTODETECTION=OFF -DDEAL_II_FORCE_BUNDLED_BOOST=ON \
                 -DDEAL_II_WITH_MPI=ON -DDEAL_II_WITH_THREADS=OFF -DDEAL_II_WITH_ZLIB=OFF -DDEAL_II_WITH_LAPACK=OFF \
                 -DDEAL_II_WITH_MUPARSER=OFF -DDEAL_II_WITH_COMPLEX_VALUES=OFF -DDEAL_II_COMPONENT_EXAMPLES=OFF \
                 > "$D/configure-$v.log" 2>&1 \
             && make -j"$j" install > "$D/make-$v.log" 2>&1) || { echo "build of deal.II $v failed ($D/make-$v.log)" >&2; return 1; }
        fi
        mkdir -p "$D/driver-src" "$D/driver-$v"
        cat > "$D/driver-src/CMakeLists.txt" <<'CMAKE'
CMAKE_MINIMUM_REQUIRED(VERSION 3.13)
FIND_PACKAGE(deal.II 9.1 REQUIRED HINTS ${DEAL_II_DIR})
DEAL_II_INITIALIZE_CACHED_VARIABLES()
PROJECT(nbx_calls CXX)
ADD_EXECUTABLE(nbx_calls ${DRIVER})
DEAL_II_SETUP_TARGET(nbx_calls)
CMAKE
        (cd "$D/driver-$v" && "$CMAKE" -S "$D/driver-src" -B . -DDEAL_II_DIR="$D/install-$v" \
             -DDRIVER="$R/experiments/dealii8929/nbx_calls.cc" > "$D/driver-$v.log" 2>&1 && make -j"$j" >> "$D/driver-$v.log" 2>&1) \
            || { echo "build of the driver for $v failed ($D/driver-$v.log)" >&2; return 1; }
    done
}
predict() {   # predict <commit>
    need_file "$(exe "$1")" || return 1
    python3 experiments/history_predict.py --version "$(short "$1")" --np $NPS --runs "$RUNS" --timeout "$TIMEOUT" \
        --jobs "${JOBS:-2}" --no-sp --confirm --classify experiments/dealii8929/classify.py --replay cross --ok "$OK" \
        --out "$OUT/predict-$(short "$1").csv" -- "$(exe "$1")" "$CALLS"
}
stress() {    # stress <commit>
    need_file "$(exe "$1")" || return 1
    rm -f "$OUT/stress-$(short "$1").csv"
    python3 experiments/history_stress.py --version "$(short "$1")" --label "nbx_calls $CALLS" --np $NPS --runs "$STRESS_RUNS" \
        --timeout "$TIMEOUT" --jobs "${JOBS:-2}" --ok "$OK" --out "$OUT/stress-$(short "$1").csv" -- "$(exe "$1")" "$CALLS"
}
# the marks of NINJA's runs: "ok", the verdict without the counts before it (run.py splits --marks
# at commas), and "wrong", a rank's wrong answer; the value is rank 0's count of wrong answers
NINJA_MARKS=(--marks "ok= 0 wrong answers: ok,wrong=: the answer of rank [0-9]+ is" --value "([0-9]+) wrong answers")
ninja() {     # ninja <commit> <table>
    need_file "$(exe "$1")" || return 1
    local t="$R/experiments/results/ninja/$2.csv" np m
    mkdir -p "$(dirname "$t")" && rm -f "$t"
    for np in $NPS; do
        for m in random system app; do
            local extra=(); [ $m = random ] && extra=(--rand-ratio 10 --rand-delay 50000)
            python3 experiments/ninja/run.py runs --np "$np" --mode $m "${extra[@]}" --runs "${NINJA_RUNS:-40}" --timeout "$TIMEOUT" \
                --mpi openmpi --label "nbx_calls-$(short "$1")" --out "$t" "${NINJA_MARKS[@]}" \
                --keep "$R/experiments/results/ninja/logs" -- "$(exe "$1")" "$CALLS"; [ $? != 3 ] || return 1
        done
    done
}
ninja_check() {
    # does NINJA's library alone change the runs? CHECK_RUNS plain runs, and CHECK_RUNS with
    # libninja-fixed.so loaded in random mode at a ratio of 1e-6% (NINJA refuses 0), which delays
    # one send in 10^8 by a microsecond
    local t="$R/experiments/results/ninja/dealii8929-check.csv" c np
    rm -f "$t"
    for c in $BEFORE $FIXED; do
        need_file "$(exe "$c")" || return 1
        for np in $NPS; do
            local a=(--np "$np" --runs "${CHECK_RUNS:-5}" --timeout "$TIMEOUT" --mpi openmpi --out "$t" "${NINJA_MARKS[@]}"
                     --keep "$R/experiments/results/ninja/logs")
            python3 experiments/ninja/run.py runs --mode plain --label "nbx_calls-$(short "$c")" "${a[@]}" -- "$(exe "$c")" "$CALLS" || return 1
            python3 experiments/ninja/run.py runs --mode random --rand-ratio 0.000001 --rand-delay 1 \
                --label "nbx_calls-$(short "$c") libninja-fixed" "${a[@]}" -- "$(exe "$c")" "$CALLS" || return 1
        done
    done
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) predict $BEFORE;; stress) stress $BEFORE;;
        fixed) predict $FIXED && stress $FIXED;;
        ninja) ninja $BEFORE dealii8929;; ninja-fixed) ninja $FIXED dealii8929-fix;; ninja-check) ninja_check;;
        all) step_fetch && step_build && predict $BEFORE && stress $BEFORE && predict $FIXED && stress $FIXED;;
        *) echo "usage: $0 [fetch|build|predict|stress|fixed|ninja|ninja-fixed|ninja-check|all]" >&2; exit 2;;
    esac || exit 1
done
