#!/usr/bin/env bash
# A third documented bug: deal.II 9.3.0-rc1's Utilities::MPI::RemotePointEvaluation
# (include/deal.II/base/mpi_remote_point_evaluation.h). evaluate_and_process() and
# process_and_evaluate() Isend one message to each rank they serve, all with one tag
# (Tags::remote_point_evaluation), then for each rank they expect a message from call
# MPI_Probe(MPI_ANY_SOURCE, that tag), MPI_Get_count and MPI_Recv from the probed source, and
# file the values under that source. Consecutive calls on one cached object have nothing
# collective between them, so a probe of one call can take a rank's message of the next call:
# that rank is filed twice, another not at all, and the values land in the wrong places. The
# code came with upstream commit 6f1c7e8296 (Feb 2021) and is in the tag v9.3.0-rc1; upstream
# commit 16c4b8f0a8, PR #12363, "fix race condition if called multiple times", wraps both
# functions in a CollectiveMutex (a barrier on entry and on exit), backported by PR #12364 to
# the release v9.3.0.
#
# The driver is deal.II's own test tests/remote_point_evaluation/mapping_01.cc: one cached
# RemotePointEvaluation, four evaluations of the same vector back to back (avg, min, max,
# insert), 25 points that every rank asks for, compared with its expected output
# (mapping_01.mpirun=4.output). Steps, inside the experiment container
# (experiments/container/run.sh), from the repository root, MPICH build:
#   fetch    download deal.II 9.3.0-rc1 and 9.3.0 with the tests of RemotePointEvaluation,
#            p4est 2.2 (parallel::distributed::Triangulation needs it) and CMake 3.28.3 (the
#            image has none), check every file's sha256
#   build    p4est with the MPICH wrappers, then each deal.II version in Release with MPI and
#            p4est and nothing else that can be switched off (bundled Boost; no TBB, complex
#            scalars or examples), -j8, which gcc 13 compiles unpatched; then the in-tree
#            tests of RemotePointEvaluation as programs
#   predict  RUNS traced runs per version and process count, each analysed; every probe
#            alternative on the RemotePointEvaluation tag is replayed with the pins of its
#            horizon and with its control, JOBS at a time (experiments/dealii/predict.py)
#   stress   RUNS untraced runs of 9.3.0-rc1 per process count with random send and receive
#            delays from the replay library, and RUNS plain runs; each output compared with
#            the test's expected output
#   changes  for every replay, control and stress run whose output differs, the ranks and
#            calls whose values differ (experiments/dealii/changes.py, mapping_01 at 4 ranks)
#   all
# Writes experiments/results/dealii/{predict-9.3.0-rc1,predict-9.3.0,stress,changes}.csv.
# usage: experiments/dealii/run.sh [fetch|build|predict|stress|changes|all]
#        NPS (default 4), RUNS (default 20), JOBS (default 1), TEST (default mapping_01),
#        DEALII_DIR (default third_party/apps/dealii)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${DEALII_DIR:-$R/third_party/apps/dealii}"
OUT="$R/experiments/results/dealii"; mkdir -p "$OUT"
NPS="${NPS:-4}"; RUNS="${RUNS:-20}"; TEST="${TEST:-mapping_01}"; TIMEOUT=120
PINLIB="$R/replay/bin/mpich/libstretto-pin.so"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || git -C "$R" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
VERSIONS="9.3.0-rc1 9.3.0"
declare -A SHA=([dealii-9.3.0-rc1.tar.gz]=8cc3ae374e82ae02b9312cc85e0a86beee699d9269aad0e6a9a7017b39cd976d
                [dealii-9.3.0.tar.gz]=b65ea933ebc9fc698e78018b4b10cee1b36061045411d45d6dae8ff5be584526
                [p4est-2.2.tar.gz]=1549cbeba29bee2c35e7cc50a90a04961da5f23b6eada9c8047f511b90a8e438
                [cmake-3.28.3-linux-x86_64.tar.gz]=804d231460ab3c8b556a42d2660af4ac7a0e21c98a7f8ee3318a74b4a9a187a6)
declare -A URL=([dealii-9.3.0-rc1.tar.gz]=https://github.com/dealii/dealii/archive/refs/tags/v9.3.0-rc1.tar.gz
                [dealii-9.3.0.tar.gz]=https://github.com/dealii/dealii/archive/refs/tags/v9.3.0.tar.gz
                [p4est-2.2.tar.gz]=https://p4est.github.io/release/p4est-2.2.tar.gz
                [cmake-3.28.3-linux-x86_64.tar.gz]=https://github.com/Kitware/CMake/releases/download/v3.28.3/cmake-3.28.3-linux-x86_64.tar.gz)
CMAKE="$D/cmake-3.28.3-linux-x86_64/bin/cmake"
bindir() { echo "$D/drivers-$1"; }
# The archives leave tests/ out (deal.II's .gitattributes marks it export-ignore), so the tests
# of RemotePointEvaluation and their expected outputs come one by one from the commits the two
# tags name, each checked by sha256 (and equal to the blobs git lists for those commits).
declare -A COMMIT=([9.3.0-rc1]=9124f4d2dd4f18f164cf09c573bb05590a7014fd [9.3.0]=9f731344ce4ed0ac2cab7e3273da48db35050316)
TESTFILES="9.3.0-rc1 e5b885ba3fca072a5ceeececc8ce56a5beedaccb38ded2eebc65a1e7f2566704 mapping_01.cc
9.3.0-rc1 f67b413d3c0c7c5fdd286a8d39196b9a2acf4015983f43ae6b05903b46170de7 mapping_01.mpirun=1.output
9.3.0-rc1 68865ea36218ff5b4d731ee8880e283d093a9ba44bfde253a2a9ec7b42e88cfa mapping_01.mpirun=4.output
9.3.0-rc1 be6700f7e3bf5c36f3c82c4de548af2d19e65c074f858934d8408bde1dd00005 remote_point_evaluation_01.cc
9.3.0-rc1 45d4f06cd9b219b86bd7f1b44fe7772b7d3cacdd085722e67dc9a7bce4e1d98f remote_point_evaluation_01.mpirun=4.output
9.3.0-rc1 4c06744e4857084cecced7ff295c4dcf6bc9f8541d7c00c6476f81596174d6e0 remote_point_evaluation_02.cc
9.3.0-rc1 55eeb215091d5190764f1edde209872a86f494984741a66257c89a05ec1ae1aa remote_point_evaluation_02.mpirun=5.output
9.3.0-rc1 1e3fa66c85d15254ac38dbcdb6a132286d603eada895c7f5180e9d52764d9232 remote_point_evaluation_03.cc
9.3.0-rc1 2400b18dce0f3873fe0c0c1cca24b8707f01a8123a4b47d7f2c7f4563b5a9af1 remote_point_evaluation_03.mpirun=8.output
9.3.0-rc1 de3f514dc8e8dc00f6ae4f177aecfd16298de1981b915d73012b0b4adcc73515 vector_tools_evaluate_at_points_01.cc
9.3.0-rc1 9d680b9c53dcad3f5c872113f5fb032d38b1f68185918bf24124fe2703d48ee3 vector_tools_evaluate_at_points_01.mpirun=8.output
9.3.0-rc1 8dbfd2aa7351a3fe3e1170aae64b43241e7cb273300ee77e9c0fdd08290fb76f vector_tools_evaluate_at_points_03.cc
9.3.0-rc1 96b487fa0d8be202c336004decf1855d3f550dead51191d9ff5580f345a0a8fe vector_tools_evaluate_at_points_03.mpirun=4.output
9.3.0-rc1 5da8c0cbc8c81774185af2c83afdf4c48f209c050ff66fe22bb34e51467503d0 vector_tools_evaluate_at_points_04.cc
9.3.0-rc1 65241930e55e75d50f4e23026fa8e1eeee2641b48f06fd3d2fd81baca73dee2c vector_tools_evaluate_at_points_04.mpirun=4.output
9.3.0-rc1 58018f4438d77a388aa4b5e1ca7ad85614fdf4a0ce1ef3ffd0b6672fd4329fcd vector_tools_evaluate_at_points_05.cc
9.3.0-rc1 877f1ff6bff02488823c08a541039894ab62b8bc4ef868efdc2acb42481d6ba4 vector_tools_evaluate_at_points_05.mpirun=4.output
9.3.0 2cf540cc03fef8d0db1aaca8393c3ac1a22c572f2c03a31fb6d02b1c42903f68 mapping_01.cc
9.3.0 f67b413d3c0c7c5fdd286a8d39196b9a2acf4015983f43ae6b05903b46170de7 mapping_01.mpirun=1.output
9.3.0 68865ea36218ff5b4d731ee8880e283d093a9ba44bfde253a2a9ec7b42e88cfa mapping_01.mpirun=4.output
9.3.0 be6700f7e3bf5c36f3c82c4de548af2d19e65c074f858934d8408bde1dd00005 remote_point_evaluation_01.cc
9.3.0 45d4f06cd9b219b86bd7f1b44fe7772b7d3cacdd085722e67dc9a7bce4e1d98f remote_point_evaluation_01.mpirun=4.output
9.3.0 4c06744e4857084cecced7ff295c4dcf6bc9f8541d7c00c6476f81596174d6e0 remote_point_evaluation_02.cc
9.3.0 55eeb215091d5190764f1edde209872a86f494984741a66257c89a05ec1ae1aa remote_point_evaluation_02.mpirun=5.output
9.3.0 5fc707692d02020511d2eafcf6fcf18665cd6647de39841449b54d216f68a550 remote_point_evaluation_03.cc
9.3.0 2400b18dce0f3873fe0c0c1cca24b8707f01a8123a4b47d7f2c7f4563b5a9af1 remote_point_evaluation_03.mpirun=8.output
9.3.0 953e132d22cacaa7dad732b5911ffd819fb33a278fd46bc9ef7de1a0a52c2080 vector_tools_evaluate_at_points_01.cc
9.3.0 9d680b9c53dcad3f5c872113f5fb032d38b1f68185918bf24124fe2703d48ee3 vector_tools_evaluate_at_points_01.mpirun=8.output
9.3.0 3189cb774e84ea8f871c4e7a32c64c01f477b537f6206a9b44b24175e4e3d7aa vector_tools_evaluate_at_points_03.cc
9.3.0 96b487fa0d8be202c336004decf1855d3f550dead51191d9ff5580f345a0a8fe vector_tools_evaluate_at_points_03.mpirun=4.output
9.3.0 c4444243c70efd73c823c974c7a5fe380c9a30e2e1ad21187d7152c2d940bba7 vector_tools_evaluate_at_points_04.cc
9.3.0 65241930e55e75d50f4e23026fa8e1eeee2641b48f06fd3d2fd81baca73dee2c vector_tools_evaluate_at_points_04.mpirun=4.output
9.3.0 2ef94252f00759488c47933658e36c84bfa419b62774d87b5cfaf8ead5164463 vector_tools_evaluate_at_points_05.cc
9.3.0 877f1ff6bff02488823c08a541039894ab62b8bc4ef868efdc2acb42481d6ba4 vector_tools_evaluate_at_points_05.mpirun=4.output"

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    for f in "${!URL[@]}"; do
        [ -f "$f" ] || curl -sSL -o "$f" "${URL[$f]}" || return 1
        echo "${SHA[$f]}  $f" | sha256sum -c - || return 1
        [ -d "${f%.tar.gz}" ] || tar xzf "$f" || return 1
    done
    while read -r v sha f; do
        local t="$D/dealii-$v/tests/remote_point_evaluation"; mkdir -p "$t"
        [ -f "$t/$f" ] || curl -sSfL -o "$t/$f" "https://raw.githubusercontent.com/dealii/dealii/${COMMIT[$v]}/tests/remote_point_evaluation/$f" || return 1
        echo "$sha  $t/$f" | sha256sum -c --quiet - || return 1
    done <<< "$TESTFILES"
    cd "$R"
}
step_build() {
    if [ ! -f "$D/p4est/lib/libp4est.so" ]; then
        # the options of deal.II's own doc/external-libs/p4est-setup.sh, FAST variant only
        mkdir -p "$D/build-p4est" && (cd "$D/build-p4est" && "$D/p4est-2.2/configure" --enable-mpi --enable-shared \
             --disable-static --disable-vtk-binary --without-blas --prefix="$D/p4est" CC=/usr/bin/mpicc.mpich CFLAGS=-O2 \
             CPPFLAGS=-DSC_LOG_PRIORITY=SC_LP_ESSENTIAL > "$D/p4est-configure.log" 2>&1 \
         && make -j8 > "$D/p4est-make.log" 2>&1 && make install >> "$D/p4est-make.log" 2>&1) \
            || { echo "build of p4est failed" >&2; return 1; }
    fi
    for v in $VERSIONS; do
        if [ ! -f "$D/install-$v/lib/libdeal_II.so" ]; then
            # the Fortran wrapper is named too: CMake's FindMPI otherwise takes mpif90, which is
            # Open MPI's, and links its libraries next to MPICH's
            mkdir -p "$D/build-$v" && (cd "$D/build-$v" && "$CMAKE" -S "$D/dealii-$v" -B . -DCMAKE_BUILD_TYPE=Release \
                 -DCMAKE_C_COMPILER=/usr/bin/mpicc.mpich -DCMAKE_CXX_COMPILER=/usr/bin/mpicxx.mpich \
                 -DCMAKE_Fortran_COMPILER=/usr/bin/mpifort.mpich -DMPI_Fortran_COMPILER=/usr/bin/mpifort.mpich \
                 -DCMAKE_INSTALL_PREFIX="$D/install-$v" -DDEAL_II_ALLOW_AUTODETECTION=OFF -DDEAL_II_FORCE_BUNDLED_BOOST=ON \
                 -DDEAL_II_WITH_MPI=ON -DDEAL_II_WITH_P4EST=ON -DP4EST_DIR="$D/p4est" -DDEAL_II_WITH_COMPLEX_VALUES=OFF \
                 -DDEAL_II_COMPONENT_EXAMPLES=OFF > "$D/configure-$v.log" 2>&1 \
             && make -j8 install > "$D/make-$v.log" 2>&1) \
                || { echo "build of deal.II $v failed" >&2; return 1; }
        fi
        # the in-tree tests of RemotePointEvaluation, each a program of its own, built as
        # deal.II builds user programs; vector_tools_evaluate_at_points_02 needs Trilinos and
        # is not fetched
        mkdir -p "$D/drivers-src-$v" "$(bindir "$v")"
        cat > "$D/drivers-src-$v/CMakeLists.txt" <<'CMAKE'
CMAKE_MINIMUM_REQUIRED(VERSION 3.13)
FIND_PACKAGE(deal.II 9.3 REQUIRED HINTS ${DEAL_II_DIR})
DEAL_II_INITIALIZE_CACHED_VARIABLES()
PROJECT(remote_point_evaluation_tests CXX)
FILE(GLOB _tests ${TEST_DIR}/*.cc)
LIST(FILTER _tests EXCLUDE REGEX "vector_tools_evaluate_at_points_02")
FOREACH(_src ${_tests})
  GET_FILENAME_COMPONENT(_name ${_src} NAME_WE)
  ADD_EXECUTABLE(${_name} ${_src})
  DEAL_II_SETUP_TARGET(${_name})
ENDFOREACH()
CMAKE
        (cd "$(bindir "$v")" && "$CMAKE" -S "$D/drivers-src-$v" -B . -DDEAL_II_DIR="$D/install-$v" \
             -DTEST_DIR="$D/dealii-$v/tests/remote_point_evaluation" > "$D/drivers-$v.log" 2>&1 \
         && make -j8 >> "$D/drivers-$v.log" 2>&1) || { echo "build of the tests of $v failed" >&2; return 1; }
    done
}
step_predict() {
    for v in $VERSIONS; do
        python3 experiments/dealii/predict.py "$(bindir "$v")" --version "$v" --test "$TEST" --runs "$RUNS" --np $NPS \
            --expected "$D/dealii-$v/tests/remote_point_evaluation/$TEST.mpirun={np}.output" \
            --normalize "$D/dealii-$v/cmake/scripts/normalize.pl" \
            --jobs "${JOBS:-1}" --out "$OUT/predict-$v.csv" || return 1
    done
}
differing() {   # differing <output> <expected>: how many lines differ, position by position
    awk 'NR == FNR { a[FNR] = $0; n = FNR; next } { if (!(FNR in a) || a[FNR] != $0) d++; m = FNR }
         END { if (n > m) d += n - m; print d + 0 }' "$1" "$2"
}
step_stress() {
    local b; b="$(bindir 9.3.0-rc1)"
    echo "version,test,np,run,setting,result,differs,lines,commit" > "$OUT/stress.csv"
    for np in $NPS; do
        local exp="$D/dealii-9.3.0-rc1/tests/remote_point_evaluation/$TEST.mpirun=$np.output"
        for i in $(seq 1 "$RUNS"); do
            for setting in plain delays; do
                local env=() w; w=$(mktemp -d)
                if [ $setting = delays ]; then
                    ds=$(python3 -c "import random; r=random.Random($i*100+$np); print(','.join(f'{k}:{r.choice([0,0,0,1,5,20])}' for k in range($np)))")
                    dr=$(python3 -c "import random; r=random.Random($i*100+$np+7); print(','.join(f'{k}:{r.choice([0,0,0,1,5,20])}' for k in range($np)))")
                    env=(-x LD_PRELOAD="$PINLIB" -x STRETTO_SEND_DELAY_MS=$ds -x STRETTO_RECV_DELAY_MS=$dr)
                fi
                (cd "$w" && /usr/bin/timeout -k 5 $TIMEOUT mpirun.openmpi --oversubscribe -np $np "${env[@]}" "$b/$TEST" \
                     < /dev/null > "$w/stdout" 2>&1)
                rc=$?; r=FINISHED; [ $rc = 0 ] || { [ $rc = 124 ] && r=TIMEOUT || r="rc=$rc"; }
                # normalised as deal.II's test harness does (the JobId line names host and date)
                [ -f "$w/output" ] || : > "$w/output"
                perl -p "$D/dealii-9.3.0-rc1/cmake/scripts/normalize.pl" "$w/output" > "$w/normalized"
                if cmp -s "$w/normalized" "$exp"; then d=False; else
                    d=True; mkdir -p "$OUT/outputs"; cp "$w/normalized" "$OUT/outputs/stress-$np-$i-$setting.txt"
                fi
                echo "9.3.0-rc1,$TEST,$np,$i,$setting,$r,$d,$(differing "$w/normalized" "$exp"),$STRETTO_COMMIT" | tee -a "$OUT/stress.csv"
                rm -rf "$w"
            done
        done
    done
}
step_changes() {
    # the expected output of mapping_01 is the same file in both versions
    python3 experiments/dealii/changes.py --expected "$D/dealii-9.3.0-rc1/tests/remote_point_evaluation/mapping_01.mpirun=4.output" \
        --predict "$OUT/predict-9.3.0-rc1.csv" "$OUT/predict-9.3.0.csv" --stress "$OUT/stress.csv" --out "$OUT/changes.csv"
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) step_predict;; stress) step_stress;; changes) step_changes;;
        all) step_fetch && step_build && step_predict && step_stress && step_changes;;
        *) echo "usage: $0 [fetch|build|predict|stress|changes|all]" >&2; exit 2;;
    esac || exit 1
done
