#!/usr/bin/env bash
# A documented hang in Cabana's CommunicationPlan, from the project's history (PR #835).
#
# CommunicationPlan::createFromImports builds a plan from the ids each rank imports
# (core/src/Cabana_CommunicationPlan.hpp, createWithoutTopology(Import)). Round 1: every rank
# posts one MPI_Irecv(1, MPI_INT, MPI_ANY_SOURCE, mpi_tag) per rank it will hear from, sends its
# own counts with blocking MPI_Send and waits (MPI_Waitall). Round 2: one MPI_Irecv(1, MPI_INT,
# send_to(i), mpi_tag) per expected id and one MPI_Isend per import id, same tag, then
# MPI_Waitall. A rank that has its counts goes on to send round-2 ids, which a slower rank's
# round-1 wildcard receive still open can take for a count; the counts no longer add up and the
# plan hangs. The code came with PR #826 (merged 13 Aug 2025); PR #835 ("Sometimes
# CommunicationPlan test 10 hangs ... race condition between the two rounds of Isends and
# Irecvs"), merge commit 38170631 of 19 Aug 2025, gives round 2 its own tag and ends with a
# barrier; its parent fbbcc37f has the bug. No release has it (0.7.0 has no import path, 0.8.0
# the fix). The driver is Cabana's own unit test CommPlan.Test10NoTopo (every rank imports 5 ids
# from its previous and 5 from its next rank) in Cabana_Core_Test_CommunicationPlan_MPI_SERIAL.
#
# Steps, inside the experiment container, from the repository root, Open MPI build:
#   fetch    download Cabana at both commits, Kokkos 4.1.00, googletest 1.14.0 and CMake 3.28.3
#            (the image has none), check every archive's sha256
#   build    Kokkos (Serial backend) and googletest with gcc, then the MPI unit test of the
#            communication plan at both commits with the Open MPI wrappers, -j$MAKE_JOBS
#   predict  RUNS traced runs of the test at the commit with the bug per process count of NPS,
#            each analysed with the single-path explorer, every finding of an error class
#            replayed by stretto confirm with its control, JOBS traced runs at a time
#            (experiments/history_predict.py)
#   stress   STRESS_RUNS untraced runs of that test per process count of STRESS_NPS, plain and
#            with random send and receive delays from the replay library, once with the test's
#            one call of createFromImports and once with --gtest_repeat=2 (two calls in one run),
#            TIMEOUT seconds each (experiments/history_stress.py)
#   fixed    predict and stress on the fix, 38170631
#   all      fetch build predict stress fixed
# Writes experiments/results/cabana/{predict,stress}-{fbbcc37f,38170631}.csv.
# usage: experiments/cabana/run.sh [fetch|build|predict|stress|fixed|all]
#        NPS (3), STRESS_NPS ("3 4"), RUNS (15), STRESS_RUNS (40), TIMEOUT (30), JOBS (3),
#        MAKE_JOBS (8), CABANA_DIR (third_party/apps/cabana)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${CABANA_DIR:-$R/third_party/apps/cabana}"
OUT="$R/experiments/results/cabana"; mkdir -p "$OUT"
NPS="${NPS:-3}"; STRESS_NPS="${STRESS_NPS:-3 4}"; RUNS="${RUNS:-15}"; STRESS_RUNS="${STRESS_RUNS:-40}"; TIMEOUT="${TIMEOUT:-30}"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
# the container refuses process_vm_readv, the single-copy mechanism of Open MPI's shared-memory
# transport, which then prints a line per refused transfer; without it the transport copies
# through its shared buffers (mpirun passes OMPI_MCA_* settings on to the ranks)
export OMPI_MCA_btl_vader_single_copy_mechanism=none
BUG=fbbcc37f4f8d2326541eeb87f3c1879148552183
FIX=38170631c4a90ab0861ddd82ee7b8bdf10e52a61
declare -A SHA=([cabana-${BUG:0:8}.tar.gz]=c777d25dafbfa326c630fac4653e442c1ca8761a13bfdf6c373e2dbd4008d686
                [cabana-${FIX:0:8}.tar.gz]=80c41a5caab25cd66b3d8f13fe61b9018c033bfc86bd8e0fe4aaee1d122512a6
                [kokkos-4.1.00.tar.gz]=cf725ea34ba766fdaf29c884cfe2daacfdc6dc2d6af84042d1c78d0f16866275
                [googletest-1.14.0.tar.gz]=8ad598c73ad796e0d8280b082cebd82a630d73e73cd3c70057938a6501bba5d7
                [cmake-3.28.3-linux-x86_64.tar.gz]=804d231460ab3c8b556a42d2660af4ac7a0e21c98a7f8ee3318a74b4a9a187a6)
declare -A URL=([cabana-${BUG:0:8}.tar.gz]=https://github.com/ECP-copa/Cabana/archive/$BUG.tar.gz
                [cabana-${FIX:0:8}.tar.gz]=https://github.com/ECP-copa/Cabana/archive/$FIX.tar.gz
                [kokkos-4.1.00.tar.gz]=https://github.com/kokkos/kokkos/archive/refs/tags/4.1.00.tar.gz
                [googletest-1.14.0.tar.gz]=https://github.com/google/googletest/archive/refs/tags/v1.14.0.tar.gz
                [cmake-3.28.3-linux-x86_64.tar.gz]=https://github.com/Kitware/CMake/releases/download/v3.28.3/cmake-3.28.3-linux-x86_64.tar.gz)
CMAKE="$D/cmake-3.28.3-linux-x86_64/bin/cmake"
TEST=(--gtest_filter=CommPlan.Test10NoTopo)
OK='\[  PASSED  \] 1 test\.'
exe() { echo "$D/build-$1/core/unit_test/Cabana_Core_Test_CommunicationPlan_MPI_SERIAL"; }

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    local f v
    for f in "${!URL[@]}"; do fetch_checked "$f" "${URL[$f]}" "${SHA[$f]}" || return 1; done
    for f in kokkos-4.1.00 googletest-1.14.0 cmake-3.28.3-linux-x86_64; do [ -d "$f" ] || tar xzf "$f.tar.gz" || return 1; done
    for v in "$BUG" "$FIX"; do [ -d "Cabana-$v" ] || tar xzf "cabana-${v:0:8}.tar.gz" || return 1; done
    cd "$R"
}
step_build() {
    local j="${MAKE_JOBS:-8}" v
    if [ ! -f "$D/kokkos/lib/cmake/Kokkos/KokkosConfig.cmake" ]; then
        (CC=/usr/bin/gcc CXX=/usr/bin/g++ "$CMAKE" -S "$D/kokkos-4.1.00" -B "$D/build-kokkos" -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_INSTALL_PREFIX="$D/kokkos" -DCMAKE_INSTALL_LIBDIR=lib -DKokkos_ENABLE_SERIAL=ON -DCMAKE_CXX_STANDARD=17 \
             > "$D/kokkos.log" 2>&1 && "$CMAKE" --build "$D/build-kokkos" -j "$j" >> "$D/kokkos.log" 2>&1 \
         && "$CMAKE" --install "$D/build-kokkos" >> "$D/kokkos.log" 2>&1) || { echo "build of Kokkos failed ($D/kokkos.log)" >&2; return 1; }
    fi
    if [ ! -f "$D/gtest/lib/cmake/GTest/GTestConfig.cmake" ]; then
        (CC=/usr/bin/gcc CXX=/usr/bin/g++ "$CMAKE" -S "$D/googletest-1.14.0" -B "$D/build-gtest" -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_INSTALL_PREFIX="$D/gtest" -DCMAKE_INSTALL_LIBDIR=lib > "$D/gtest.log" 2>&1 \
         && "$CMAKE" --build "$D/build-gtest" -j "$j" >> "$D/gtest.log" 2>&1 && "$CMAKE" --install "$D/build-gtest" >> "$D/gtest.log" 2>&1) \
            || { echo "build of googletest failed ($D/gtest.log)" >&2; return 1; }
    fi
    for v in "$BUG" "$FIX"; do
        [ -x "$(exe "${v:0:8}")" ] && continue
        (CC=/usr/bin/gcc CXX=/usr/bin/g++ "$CMAKE" -S "$D/Cabana-$v" -B "$D/build-${v:0:8}" -DCMAKE_BUILD_TYPE=Release \
             -DCabana_ENABLE_TESTING=ON -DCabana_REQUIRE_MPI=ON -DCabana_ENABLE_EXAMPLES=OFF -DCabana_ENABLE_PERFORMANCE_TESTING=OFF \
             -DMPI_C_COMPILER=/usr/bin/mpicc.openmpi -DMPI_CXX_COMPILER=/usr/bin/mpicxx.openmpi \
             -DCMAKE_PREFIX_PATH="$D/kokkos;$D/gtest" > "$D/cabana-${v:0:8}.log" 2>&1 \
         && "$CMAKE" --build "$D/build-${v:0:8}" -j "$j" --target Cabana_Core_Test_CommunicationPlan_MPI_SERIAL \
             >> "$D/cabana-${v:0:8}.log" 2>&1) || { echo "build of Cabana ${v:0:8} failed ($D/cabana-${v:0:8}.log)" >&2; return 1; }
    done
}
predict() {   # predict <commit>
    need_file "$(exe "$1")" || return 1
    python3 experiments/history_predict.py --version "$1" --np $NPS --runs "$RUNS" --timeout 20 --jobs "${JOBS:-3}" \
        --confirm --ok "$OK" --out "$OUT/predict-$1.csv" -- "$(exe "$1")" "${TEST[@]}"
}
stress() {    # stress <commit>
    need_file "$(exe "$1")" || return 1
    rm -f "$OUT/stress-$1.csv"
    local s=(--version "$1" --np $STRESS_NPS --runs "$STRESS_RUNS" --timeout "$TIMEOUT" --jobs "${JOBS:-3}" --ok "$OK"
             --out "$OUT/stress-$1.csv")
    python3 experiments/history_stress.py "${s[@]}" --label Test10NoTopo -- "$(exe "$1")" "${TEST[@]}" \
        && python3 experiments/history_stress.py "${s[@]}" --label Test10NoTopo-repeat2 -- "$(exe "$1")" "${TEST[@]}" --gtest_repeat=2
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) predict "${BUG:0:8}";; stress) stress "${BUG:0:8}";;
        fixed) predict "${FIX:0:8}" && stress "${FIX:0:8}";;
        all) step_fetch && step_build && predict "${BUG:0:8}" && stress "${BUG:0:8}" && predict "${FIX:0:8}" && stress "${FIX:0:8}";;
        *) echo "usage: $0 [fetch|build|predict|stress|fixed|all]" >&2; exit 2;;
    esac || exit 1
done
