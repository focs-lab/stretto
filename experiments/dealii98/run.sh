#!/usr/bin/env bash
# deal.II 9.8.0, the current release, scouted for wildcard matching bugs.
#
# deal.II's point-to-point exchanges probe from MPI_ANY_SOURCE in many places:
# Utilities::MPI::some_to_some (include/deal.II/base/mpi.h), the consensus algorithms NBX and
# PEX (mpi_consensus_algorithms.h; the selector takes NBX above 99 processes in a release
# build, PEX below), compute_point_to_point_communication_pattern (source/base/mpi.cc),
# RemotePointEvaluation, the multigrid transfers and grid_tools. Since the races of 2019 (two
# consensus calls mixed up) and 2021 (RemotePointEvaluation, experiments/dealii/), each
# exchange has its own tag in mpi_tags.h and the ones that can follow each other take a
# CollectiveMutex. The drivers are deal.II's own tests of those functions, built as programs
# as in experiments/dealii/, each compared with its expected output after deal.II's
# cmake/scripts/normalize.pl.
#
# Steps, inside the experiment container, from the repository root, Open MPI build:
#   fetch    download deal.II 9.8.0, p4est 2.2 and CMake 3.28.3 (the image has none), check
#            their sha256; the archive leaves tests/ out, so tests.h and the tests below with
#            their expected outputs come one by one from the commit the tag names
#   build    p4est with the Open MPI wrappers, then deal.II in Release with MPI and p4est and
#            nothing else that can be switched off, -j$MAKE_JOBS, then the tests as programs
#   predict  RUNS traced runs of each test, analysed, every alternative replayed with its
#            horizon pins and its control (experiments/scout.py), JOBS at a time
#   all
# Writes experiments/results/dealii98/predict.csv.
# usage: experiments/dealii98/run.sh [fetch|build|predict|all]
#        RUNS (5), JOBS (1), MAKE_JOBS (12), DEALII98_DIR (third_party/apps/dealii98)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${DEALII98_DIR:-$R/third_party/apps/dealii98}"
OUT="$R/experiments/results/dealii98"; mkdir -p "$OUT"
RUNS="${RUNS:-5}"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
V=9.8.0
COMMIT=02c5025ef5e042381dbd5772a73691520a4b1aea   # the commit tag v9.8.0 names
S="$D/dealii-$V"
CMAKE="$D/cmake-3.28.3-linux-x86_64/bin/cmake"
B="$D/drivers-$V"
declare -A SHA=([dealii-9.8.0.tar.gz]=d8d66aac57baad145a752d3f11cf72cfa9457e3f99ae09e5c8d5c9259a83aee1
                [p4est-2.2.tar.gz]=1549cbeba29bee2c35e7cc50a90a04961da5f23b6eada9c8047f511b90a8e438
                [cmake-3.28.3-linux-x86_64.tar.gz]=804d231460ab3c8b556a42d2660af4ac7a0e21c98a7f8ee3318a74b4a9a187a6)
declare -A URL=([dealii-9.8.0.tar.gz]=https://github.com/dealii/dealii/archive/refs/tags/v9.8.0.tar.gz
                [p4est-2.2.tar.gz]=https://p4est.github.io/release/p4est-2.2.tar.gz
                [cmake-3.28.3-linux-x86_64.tar.gz]=https://github.com/Kitware/CMake/releases/download/v3.28.3/cmake-3.28.3-linux-x86_64.tar.gz)
TESTFILES="851879aeddf01b2aecefb0f8b76057aaa7cffcd765d2e18eb64d668546a8bf10 tests.h
4c1f4d42021638b966c55fc61ba816fcd11ffa287dc5c5c6b5ef42fd9080be4b remote_point_evaluation/mapping_01.cc
68865ea36218ff5b4d731ee8880e283d093a9ba44bfde253a2a9ec7b42e88cfa remote_point_evaluation/mapping_01.with_p4est=true.mpirun=4.output
110f1056073ac59d36caf50e5e0270ac0600262a27be224db896468c0669a36a base/consensus_algorithm_01.cc
313e2c52ac3b58ded669c8ba1d3595a4d9680ba079515059559e12ccfbb2f3dc base/consensus_algorithm_01.mpirun=4.output
5f9aafc2598232808a4758fb6ecf94f6645ce75cc4df3c7c47075f280484f813 base/mpi_some_to_some_02.cc
95ca2b62e5a8708520ad0bc71165365957ccbf8704f81731642979281bbe88de base/mpi_some_to_some_02.mpirun=4.output
dd303e8a88ae581dcf285e7473bad16c96e0dd36831093c1a9260a967f15d458 base/mpi_some_to_some_03.cc
bbbdfa7f27d83b008e5aeb68ed48a3339d728893276cf741a7ae579aa992b843 base/mpi_some_to_some_03.mpirun=4.output
5de6702a0f82541d0ed55884596c73c289aaca83cf9c8daacfcac533f2eb17ef mpi/point_to_point_pattern_01.cc
622d21bb4376d71639469a8e5b41bed0f07f89adbc0c3038a85152edd95c38f2 mpi/point_to_point_pattern_01.mpirun=4.output
4ec5ce2a822299b6e81c854047606434641e882b2166c2fcdcfa9daf0a619de9 mpi/parallel_partitioner_05.cc
e75f192e00cec6bfc10ab7953a51958a63a3a28cc43548be4d3e35d57aa1ead0 mpi/parallel_partitioner_05.mpirun=4.output
88c73901ff24a30742a52d0c190b6b48d36618cb81c07dfb00ad50aa82d56ff0 mpi/cell_weights_01_back_and_forth_01.cc
f25282705b68aa5ba3749a4ee1baf2771358ea15464ce38e0d185be3b9625306 mpi/cell_weights_01_back_and_forth_01.mpirun=4.with_p4est=true.output
445265128bcb8875b536431aef4ab3302ffe17bafd3b522584e34d0eef8008f2 base/mpi_noncontiguous_partitioner_02.cc
e257ed0109e03b0662ba238a7fdae9948c518fea18e1149b3c0681e1378e538c base/mpi_noncontiguous_partitioner_02.with_p4est=true.mpirun=4.output"
# test (directory/name), process count
TESTS="remote_point_evaluation/mapping_01 4
base/consensus_algorithm_01 4
base/mpi_some_to_some_02 4
base/mpi_some_to_some_03 4
mpi/point_to_point_pattern_01 4
mpi/parallel_partitioner_05 4
mpi/cell_weights_01_back_and_forth_01 4
base/mpi_noncontiguous_partitioner_02 4"

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    for f in "${!URL[@]}"; do
        [ -f "$f" ] || curl -sSfL -o "$f" "${URL[$f]}" || return 1
        echo "${SHA[$f]}  $f" | sha256sum -c - || return 1
        [ -d "${f%.tar.gz}" ] || tar xzf "$f" || return 1
    done
    while read -r sha f; do
        mkdir -p "$S/tests/$(dirname "$f")"
        [ -f "$S/tests/$f" ] || curl -sSfL -o "$S/tests/$f" "https://raw.githubusercontent.com/dealii/dealii/$COMMIT/tests/$f" || return 1
        echo "$sha  $S/tests/$f" | sha256sum -c --quiet - || return 1
    done <<< "$TESTFILES"
    cd "$R"
}
step_build() {
    local j="${MAKE_JOBS:-12}"
    if [ ! -f "$D/p4est/lib/libp4est.so" ]; then
        mkdir -p "$D/build-p4est" && (cd "$D/build-p4est" && "$D/p4est-2.2/configure" --enable-mpi --enable-shared \
             --disable-static --disable-vtk-binary --without-blas --prefix="$D/p4est" CC=/usr/bin/mpicc.openmpi CFLAGS=-O2 \
             CPPFLAGS=-DSC_LOG_PRIORITY=SC_LP_ESSENTIAL > "$D/p4est-configure.log" 2>&1 \
         && make -j"$j" > "$D/p4est-make.log" 2>&1 && make install >> "$D/p4est-make.log" 2>&1) \
            || { echo "build of p4est failed" >&2; return 1; }
    fi
    if [ ! -f "$D/install-$V/lib/libdeal_II.so" ]; then
        mkdir -p "$D/build-$V" && (cd "$D/build-$V" && "$CMAKE" -S "$S" -B . -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_C_COMPILER=/usr/bin/mpicc.openmpi -DCMAKE_CXX_COMPILER=/usr/bin/mpicxx.openmpi \
             -DCMAKE_Fortran_COMPILER=/usr/bin/mpifort.openmpi -DMPI_Fortran_COMPILER=/usr/bin/mpifort.openmpi \
             -DCMAKE_INSTALL_PREFIX="$D/install-$V" -DDEAL_II_ALLOW_AUTODETECTION=OFF -DDEAL_II_FORCE_BUNDLED_BOOST=ON \
             -DDEAL_II_WITH_MPI=ON -DDEAL_II_WITH_P4EST=ON -DP4EST_DIR="$D/p4est" -DDEAL_II_WITH_COMPLEX_VALUES=OFF \
             -DDEAL_II_COMPONENT_EXAMPLES=OFF > "$D/configure-$V.log" 2>&1 \
         && make -j"$j" install > "$D/make-$V.log" 2>&1) || { echo "build of deal.II $V failed" >&2; return 1; }
    fi
    mkdir -p "$D/drivers-src-$V" "$B"
    cat > "$D/drivers-src-$V/CMakeLists.txt" <<'CMAKE'
CMAKE_MINIMUM_REQUIRED(VERSION 3.13)
FIND_PACKAGE(deal.II 9.8 REQUIRED HINTS ${DEAL_II_DIR})
DEAL_II_INITIALIZE_CACHED_VARIABLES()
PROJECT(newbug_tests CXX)
FOREACH(_src ${TEST_SOURCES})
  GET_FILENAME_COMPONENT(_name ${_src} NAME_WE)
  ADD_EXECUTABLE(${_name} ${_src})
  DEAL_II_SETUP_TARGET(${_name})
ENDFOREACH()
CMAKE
    local srcs; srcs=$(awk '{print "'"$S"'/tests/" $1 ".cc"}' <<< "$TESTS" | paste -sd';')
    (cd "$B" && "$CMAKE" -S "$D/drivers-src-$V" -B . -DDEAL_II_DIR="$D/install-$V" -DTEST_SOURCES="$srcs" \
         > "$D/drivers-$V.log" 2>&1 && make -j"$j" >> "$D/drivers-$V.log" 2>&1) || { echo "build of the tests failed" >&2; return 1; }
}
step_predict() {
    rm -f "$OUT/predict.csv"
    while read -r t np; do
        python3 experiments/scout.py --code dealii --version "$V" --driver "$t" --np "$np" --runs "$RUNS" \
            --jobs "${JOBS:-1}" --timeout 120 --output-file output --normalize-script "$S/cmake/scripts/normalize.pl" \
            --out "$OUT/predict.csv" -- "$B/$(basename "$t")" || return 1
    done <<< "$TESTS"
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) step_predict;;
        all) step_fetch && step_build && step_predict;;
        *) echo "usage: $0 [fetch|build|predict|all]" >&2; exit 2;;
    esac || exit 1
done
