#!/usr/bin/env bash
# A new bug in SuperLU_DIST's default 3D triangular solve (master 9f7c819, the same commit as
# v9.3.1; found by Stretto, not yet reported upstream).
#
# pdgssvx3d solves with pdgstrs3d_newsolve and the tree communication (NEW3DSOLVE=1,
# NEW3DSOLVETREECOMM=1 by default). Its L-solve loop (dForwardSolve3d_newsolve_reusepdgstrs,
# SRC/double/pdgstrs3d.c:3903) and its U-solve loop (dBackSolve3d_newsolve_reusepdgstrs, :5946)
# both call MPI_Recv(MPI_ANY_SOURCE, MPI_ANY_TAG, grid->comm) a fixed number of times, count every
# message and handle only their own two tags (BC_L = 1, RD_L = 2; BC_U = 3, RD_U = 4). The
# barrier that separated the loops (line 4180, and the one at 5396) has been commented out since
# commit 79644321 of 5 Jan 2023; every release from 9.0.0 has the code. When the matrix is
# reducible (its elimination tree a forest), a process that finished the L-solve of one tree
# sends U-solve messages while another is still in its L-solve loop, which can take one, count it
# and drop it: the solve hangs, every process in the U-solve's MPI_Recv, or returns a wrong x
# with info = 0. barrier.patch restores the barrier at the end of the L-solve in the three
# precisions (the proposed fix). The driver is SuperLU_DIST's own EXAMPLE/pddrive3d, unmodified,
# which prints ||X - Xtrue|| / ||X|| (experiments/superlu3d/judge.py: correct at most 1e-4).
#
# Settings (SETTINGS, matrix:RxCxD, the process grid of pddrive3d -r R -c C -d D):
#   nos2:1x3x1       NOS2 (Harwell-Boeing LANPRO, 957 rows, components of 638 and 319)
#   mhd3200b:1x3x1   Bai/mhd3200b (NEP, 3200 rows, 14 components)
#   blk12x6:1x3x1    6 independent 12x12 grid operators (blkdiag.py 12 6, 864 rows)
#   blk8x10:2x3x1    10 independent 8x8 grid operators (blkdiag.py 8 10, 640 rows)
#   g20:1x3x1        g20.rua, shipped with SuperLU_DIST (400 rows, connected): the contrast
# NOS2 and mhd3200b come from NIST's Matrix Market (the SuiteSparse collection, which has them as
# HB/nos2 and Bai/mhd3200b, answers 403 since 16 Oct 2026); both the archives' and the matrices'
# sha256 are checked. blkdiag.py writes the generated ones, whose sha256 is checked too.
#
# Steps, inside the experiment container, from the repository root, for each MPI of MPIS:
#   fetch    download SuperLU_DIST at 9f7c819, CMake 3.28.3 and the two NIST matrices, check
#            their sha256, generate the block-diagonal matrices and check theirs
#   build    pddrive3d of 9f7c819 and of 9f7c819 with barrier.patch, with the wrappers of each
#            MPI: CMake defaults but no ParMETIS (not in the image; the ordering is then
#            MMD_AT_PLUS_A), the internal BLAS, double precision only, -O2 -g, -j$MAKE_JOBS;
#            OpenMP stays on, every run has OMP_NUM_THREADS=1
#   predict  RUNS traced runs of 9f7c819 per setting, each analysed at L1 (the explorer is not
#            needed: the alternatives are L1 findings), a seeded sample of SAMPLE of the cross
#            alternatives of each setting (experiments/superlu3d/classify.py: a receive of one
#            solve's loop taking the other solve's message) replayed with the pins of its whole
#            horizon and with its control, TIMEOUT seconds each (experiments/history_predict.py)
#   stress   STRESS_RUNS untraced runs of 9f7c819 per setting, plain and with random send and
#            receive delays from the replay library, TIMEOUT seconds each, SLOW_TIMEOUT for NOS2
#            and mhd3200b, whose delayed runs take up to a minute when they finish (about 7 700
#            MPI calls per process on NOS2) (experiments/history_stress.py)
#   fixed    predict and stress on 9f7c819 with barrier.patch
#   all      fetch build predict stress fixed
# Writes experiments/results/superlu3d/predict-<version>-<mpi>-<matrix>-<grid>.csv and
# stress-<version>-<mpi>.csv for version 9f7c819 and 9f7c819-barrier.
# usage: experiments/superlu3d/run.sh [fetch|build|predict|stress|fixed|all]
#        MPIS ("openmpi mpich"), SETTINGS (the five above), RUNS (10), SAMPLE (10), STRESS_RUNS
#        (40), TIMEOUT (60), SLOW_TIMEOUT (300), JOBS (3), MAKE_JOBS (8), SLU_DIR
#        (third_party/apps/superlu3d)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${SLU_DIR:-$R/third_party/apps/superlu3d}"
OUT="$R/experiments/results/superlu3d"; mkdir -p "$OUT"
MPIS="${MPIS:-openmpi mpich}"
SETTINGS="${SETTINGS:-nos2:1x3x1 mhd3200b:1x3x1 blk12x6:1x3x1 blk8x10:2x3x1 g20:1x3x1}"
RUNS="${RUNS:-10}"; SAMPLE="${SAMPLE:-10}"; STRESS_RUNS="${STRESS_RUNS:-40}"; TIMEOUT="${TIMEOUT:-60}"
SLOW_TIMEOUT="${SLOW_TIMEOUT:-300}"; JOBS="${JOBS:-3}"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
# the container refuses process_vm_readv, the single-copy mechanism of Open MPI's shared-memory
# transport, which then prints a line per refused transfer; without it the transport copies
# through its shared buffers (mpirun passes OMPI_MCA_* settings on to the ranks). Concurrent
# runs must not all bind their ranks to the container's first cores, and OpenMP stays at one
# thread per rank.
export OMPI_MCA_btl_vader_single_copy_mechanism=none OMPI_MCA_hwloc_base_binding_policy=none OMP_NUM_THREADS=1
SLU=9f7c8196d8f692cbfc59d81b61a74ceb4b37789b
BUG=9f7c819; FIX=9f7c819-barrier
NIST=https://math.nist.gov/pub/MatrixMarket2
declare -A SHA=([superlu_dist-${SLU:0:8}.tar.gz]=811a34fdd40e55228ea1e3273f1fb3be6c8bbc0a5d4cca0d5cadf35405cf54db
                [cmake-3.28.3-linux-x86_64.tar.gz]=804d231460ab3c8b556a42d2660af4ac7a0e21c98a7f8ee3318a74b4a9a187a6
                [nos2.mtx.gz]=2e64095a3df034ddb4739695c3f37f63a084f8b27c239dcc562a5080255e97ab
                [mhd3200b.mtx.gz]=f109c0c6fa5f4e5374eec4aaa9e92008f3f76dde50bbfc6d5cdbd0e7f6680a2c)
declare -A URL=([superlu_dist-${SLU:0:8}.tar.gz]=https://github.com/xiaoyeli/superlu_dist/archive/$SLU.tar.gz
                [cmake-3.28.3-linux-x86_64.tar.gz]=https://github.com/Kitware/CMake/releases/download/v3.28.3/cmake-3.28.3-linux-x86_64.tar.gz
                [nos2.mtx.gz]=$NIST/Harwell-Boeing/lanpro/nos2.mtx.gz
                [mhd3200b.mtx.gz]=$NIST/NEP/mhd/mhd3200b.mtx.gz)
# the matrices themselves: NOS2's as the SuiteSparse collection lists it, blk* as blkdiag.py
# writes them, g20.rua as the tarball ships it
declare -A MSHA=([nos2.mtx]=348a7064bc5ca5bfce2922365aea20af2434d744c062371d688fe69aa7351ed5
                 [mhd3200b.mtx]=9b6d4020470fa315285ffc7d2d982e60d8bd6d32ba299c9ddb3ccd9a9ab427ed
                 [blk12x6.mtx]=b1fe0c3764581df949e37af7c911a86dbdcf98b011c6c2f6423da5856345644b
                 [blk8x10.mtx]=b336adefa7bea785ada48b9fb0dfc1d4c684ca2967d7a83d373c48e2db29af88
                 [g20.rua]=d0a406f6f86fc8283f97e6fb97996d462ec4486585c6dce84e418d3cc954f614)
CMAKE="$D/cmake-3.28.3-linux-x86_64/bin/cmake"
exe() { echo "$D/build-$1-$2/EXAMPLE/pddrive3d"; }        # exe <mpi> <version>
src() { [ "$1" = "$BUG" ] && echo "$D/superlu_dist-$SLU" || echo "$D/superlu_dist-$SLU-barrier"; }
matrix() { case "$1" in g20) echo "$D/mats/g20.rua";; *) echo "$D/mats/$1.mtx";; esac; }

step_fetch() {
    mkdir -p "$D/mats" && cd "$D" || return 1
    local f m
    for f in "${!URL[@]}"; do fetch_checked "$f" "${URL[$f]}" "${SHA[$f]}" || return 1; done
    [ -d cmake-3.28.3-linux-x86_64 ] || tar xzf cmake-3.28.3-linux-x86_64.tar.gz || return 1
    [ -d "superlu_dist-$SLU" ] || tar xzf "superlu_dist-${SLU:0:8}.tar.gz" || return 1
    if [ ! -d "superlu_dist-$SLU-barrier" ]; then
        cp -a "superlu_dist-$SLU" "superlu_dist-$SLU-barrier.part" \
            && patch -s -p1 -d "superlu_dist-$SLU-barrier.part" < "$R/experiments/superlu3d/barrier.patch" \
            && mv "superlu_dist-$SLU-barrier.part" "superlu_dist-$SLU-barrier" || { rm -rf "superlu_dist-$SLU-barrier.part"; return 1; }
    fi
    cd mats || return 1
    for m in nos2 mhd3200b; do [ -f "$m.mtx" ] || gunzip -c "../$m.mtx.gz" > "$m.mtx" || return 1; done
    [ -f blk12x6.mtx ] || python3 -I "$R/experiments/superlu3d/blkdiag.py" 12 6 blk12x6.mtx || return 1
    [ -f blk8x10.mtx ] || python3 -I "$R/experiments/superlu3d/blkdiag.py" 8 10 blk8x10.mtx || return 1
    cp -f "../superlu_dist-$SLU/EXAMPLE/g20.rua" g20.rua || return 1
    for m in "${!MSHA[@]}"; do echo "${MSHA[$m]}  $m" | sha256sum -c --quiet - || harness_error "$m does not have the sha256 ${MSHA[$m]}" || return 1; done
    cd "$R"
}
step_build() {
    local mpi v b
    for mpi in $MPIS; do
        for v in "$BUG" "$FIX"; do
            [ -x "$(exe "$mpi" "$v")" ] && continue
            b="$D/build-$mpi-$v"
            ("$CMAKE" -S "$(src "$v")" -B "$b" -DCMAKE_BUILD_TYPE=Release \
                 -DCMAKE_C_COMPILER=/usr/bin/mpicc.$mpi -DCMAKE_CXX_COMPILER=/usr/bin/mpicxx.$mpi \
                 -DMPI_C_COMPILER=/usr/bin/mpicc.$mpi -DMPI_CXX_COMPILER=/usr/bin/mpicxx.$mpi \
                 -DCMAKE_C_FLAGS="-O2 -g" -DTPL_ENABLE_PARMETISLIB=OFF -DTPL_ENABLE_INTERNAL_BLASLIB=ON \
                 -DTPL_ENABLE_LAPACKLIB=OFF -Denable_single=OFF -Denable_complex16=OFF -Denable_double=ON \
                 -DXSDK_ENABLE_Fortran=OFF -Denable_python=OFF -Denable_tests=OFF -Denable_examples=ON > "$b.log" 2>&1 \
             && "$CMAKE" --build "$b" -j "${MAKE_JOBS:-8}" --target pddrive3d >> "$b.log" 2>&1) \
                || { echo "build of SuperLU_DIST $v with $mpi failed ($b.log)" >&2; return 1; }
        done
    done
}
grid() { local g=${1#*:}; IFS=x read -r gr gc gd <<< "$g"; }     # sets gr gc gd from matrix:RxCxD
predict() {   # predict <version>
    local mpi s m
    for mpi in $MPIS; do
        need_file "$(exe "$mpi" "$1")" || return 1
        for s in $SETTINGS; do
            m=${s%%:*}; grid "$s"
            python3 experiments/history_predict.py --version "$1" --mpi "$mpi" --np $((gr * gc * gd)) --runs "$RUNS" --no-sp \
                --classify experiments/superlu3d/classify.py --replay cross --sample "$SAMPLE" --timeout "$TIMEOUT" \
                --trace-timeout 120 --jobs "$JOBS" --judge experiments/superlu3d/judge.py \
                --out "$OUT/predict-$1-$mpi-$m-${s#*:}.csv" -- "$(exe "$mpi" "$1")" -r "$gr" -c "$gc" -d "$gd" "$(matrix "$m")" || return 1
        done
    done
}
stress() {    # stress <version>
    local mpi s m t
    for mpi in $MPIS; do
        need_file "$(exe "$mpi" "$1")" || return 1
        rm -f "$OUT/stress-$1-$mpi.csv"
        for s in $SETTINGS; do
            m=${s%%:*}; grid "$s"
            case "$m" in nos2|mhd3200b) t=$SLOW_TIMEOUT;; *) t=$TIMEOUT;; esac
            python3 experiments/history_stress.py --version "$1" --label "$m-${s#*:}" --mpi "$mpi" --np $((gr * gc * gd)) \
                --runs "$STRESS_RUNS" --timeout "$t" --jobs "$JOBS" --judge experiments/superlu3d/judge.py \
                --out "$OUT/stress-$1-$mpi.csv" -- "$(exe "$mpi" "$1")" -r "$gr" -c "$gc" -d "$gd" "$(matrix "$m")" || return 1
        done
    done
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) predict "$BUG";; stress) stress "$BUG";;
        fixed) predict "$FIX" && stress "$FIX";;
        all) step_fetch && step_build && predict "$BUG" && stress "$BUG" && predict "$FIX" && stress "$FIX";;
        *) echo "usage: $0 [fetch|build|predict|stress|fixed|all]" >&2; exit 2;;
    esac || exit 1
done
