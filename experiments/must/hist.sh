#!/usr/bin/env bash
# experiments/must/hist.sh <step> <dir>: the builds of experiments/must/apps.sh (CASES=hist) for
# the two bugs that have no campaign of their own in this tree, inside MUST's image (MPICH 4.0.2,
# clang 14, gfortran 12, CMake 3.25.1; no BLAS or LAPACK) or, to fetch, the experiment image,
# from the repository root:
#   fetch-superlu  SuperLU_DIST at 9f7c8196 (v9.3.1), whose 3D triangular solve
#                  (SRC/double/pdgstrs3d.c) receives its L-solve and its U-solve messages with
#                  MPI_ANY_SOURCE and MPI_ANY_TAG on one communicator, with no barrier between the
#                  two loops (the barrier is commented out), so that a slower process's L-solve
#                  loop can take an early U-solve message; and the matrices of the verification
#                  (block-diagonal blk12x6 and blk8x10, which apps.sh copies in from SLU_MATS),
#                  each checked against its sha256
#   build-superlu  pddrive3d with MPICH's wrappers, as the verification builds it: no ParMETIS,
#                  the internal BLAS, double precision only, no Fortran, no OpenMP (clang 14 in the
#                  image has no OpenMP runtime; the runs set OMP_NUM_THREADS=1 anyway), -O2 -g
#   fetch-qe       Quantum ESPRESSO develop at 615e40cf (26 Oct 2026), whose elph_prt
#                  (PHonon/PH/elphon.f90) receives the matrix elements and the eigenvalues of the
#                  two spins of an LSDA k point with MPI_ANY_SOURCE under one tag each, so that
#                  the two pools that hold the spins can each deliver the other's; the submodules
#                  its CMake build of pw.x and ph.x configures (devxlib, mbd, wannier90; fox is
#                  off), Reference-LAPACK 3.12.0 (the image has no LAPACK) and the test-suite's
#                  Ni pseudopotential, every archive checked against its sha256
#   build-qe       LAPACK and BLAS (static), then pw.x and ph.x (no OpenMP, no ScaLAPACK, QE's
#                  internal FFTW), -j MAKE_JOBS; then the inputs: the test-suite's Ni case
#                  (ph_base/ni.scf.in, LSDA, 4x4x4 k points), its scf on 3 processes with 3 pools,
#                  and the phonon input at X (ph_base/ni.phX.in) with electron_phonon='prt',
#                  fildvscf and the k point (-0.375, 1.125, 0.375) of the verification, whose
#                  spins two pools hold; and the reference table, ph.x on one process
# usage: experiments/must/hist.sh fetch-superlu|build-superlu|fetch-qe|build-qe <dir>   (MAKE_JOBS, 8)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
source "$R/experiments/harness.sh"
step=$1 D=$2
J="${MAKE_JOBS:-8}"
SLU=9f7c8196d8f692cbfc59d81b61a74ceb4b37789b
QE=615e40cfa30dc8ac1e95161b32d29f326c71ae65
declare -A SHA=([superlu_dist-${SLU:0:8}.tar.gz]=811a34fdd40e55228ea1e3273f1fb3be6c8bbc0a5d4cca0d5cadf35405cf54db
                [blk12x6.mtx]=b1fe0c3764581df949e37af7c911a86dbdcf98b011c6c2f6423da5856345644b
                [blk8x10.mtx]=b336adefa7bea785ada48b9fb0dfc1d4c684ca2967d7a83d373c48e2db29af88
                [q-e-${QE:0:8}.tar.gz]=26a2c18510bff90041f39e2852f02534f3b3a7533d03ac1bb46331fb8d31d40e
                [devicexlib-a6b89ef7.tar.gz]=39d55ae63168f9692ab649e7cededb3bc181580f450d30d5d4deeb008ea4a29c
                [libmbd-89a3cc19.tar.gz]=956eaa8659a24e59573a64e78372cee9f299b4bf0a89a82f6bf98685034c7736
                [wannier90-971c8b64.tar.gz]=81cf1f5c14cf0423df5fa45ad5c48bbf97f05c06773176f2712cc31916d61fee
                [lapack-3.12.0.tar.gz]=eac9570f8e0ad6f30ce4b963f4f033f0f643e7c3912fc9ee6cd99120675ad48b
                [Ni.pbe-nd-rrkjus.UPF]=d5ab40cf6cbaa88744d4c68ba8889b21a02c69f0ec9649581fa340ac7e881324)
declare -A URL=([superlu_dist-${SLU:0:8}.tar.gz]=https://github.com/xiaoyeli/superlu_dist/archive/$SLU.tar.gz
                [q-e-${QE:0:8}.tar.gz]=https://gitlab.com/QEF/q-e/-/archive/$QE/q-e-$QE.tar.gz
                [devicexlib-a6b89ef7.tar.gz]=https://gitlab.com/max-centre/components/devicexlib/-/archive/a6b89ef77b1ceda48e967921f1f5488d2df9226d/devicexlib-a6b89ef77b1ceda48e967921f1f5488d2df9226d.tar.gz
                [libmbd-89a3cc19.tar.gz]=https://github.com/libmbd/libmbd/archive/89a3cc199c0a200c9f0f688c3229ef6b9a8d63bd.tar.gz
                [wannier90-971c8b64.tar.gz]=https://github.com/wannier-developers/wannier90/archive/971c8b64bfe703c747a52655e1d59936597fe8ea.tar.gz
                [lapack-3.12.0.tar.gz]=https://github.com/Reference-LAPACK/lapack/archive/refs/tags/v3.12.0.tar.gz
                [Ni.pbe-nd-rrkjus.UPF]=https://pseudopotentials.quantum-espresso.org/upf_files/Ni.pbe-nd-rrkjus.UPF)
fetch() { fetch_checked "$1" "${URL[$1]}" "${SHA[$1]}"; }
untar() { [ -d "$2" ] || { mkdir -p "$2" && tar xzf "$1" -C "$2" --strip-components=1; }; }

case "$step" in
fetch-superlu)
    mkdir -p "$D" && cd "$D" || exit 1
    fetch "superlu_dist-${SLU:0:8}.tar.gz" && untar "superlu_dist-${SLU:0:8}.tar.gz" superlu_dist || exit 1
    for f in blk12x6.mtx blk8x10.mtx; do
        need_file "$f" && echo "${SHA[$f]}  $f" | sha256sum -c --quiet - || harness_error "$f is not the verification's matrix" || exit 1
    done;;
build-superlu)
    [ -x "$D/build/EXAMPLE/pddrive3d" ] && exit 0
    cmake -S "$D/superlu_dist" -B "$D/build" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_C_COMPILER=/usr/bin/mpicc.mpich -DCMAKE_CXX_COMPILER=/usr/bin/mpicxx.mpich \
        -DMPI_C_COMPILER=/usr/bin/mpicc.mpich -DMPI_CXX_COMPILER=/usr/bin/mpicxx.mpich -DCMAKE_C_FLAGS="-O2 -g" \
        -DTPL_ENABLE_PARMETISLIB=OFF -DTPL_ENABLE_INTERNAL_BLASLIB=ON -DTPL_ENABLE_LAPACKLIB=OFF -Denable_openmp=OFF \
        -Denable_single=OFF -Denable_complex16=OFF -Denable_double=ON -DXSDK_ENABLE_Fortran=OFF -Denable_python=OFF \
        -Denable_tests=OFF -Denable_examples=ON > "$D/build.log" 2>&1 \
        && cmake --build "$D/build" -j "$J" --target pddrive3d >> "$D/build.log" 2>&1 \
        || harness_error "build of SuperLU_DIST failed ($D/build.log)";;
fetch-qe)
    mkdir -p "$D" && cd "$D" || exit 1
    for f in "q-e-${QE:0:8}.tar.gz" devicexlib-a6b89ef7.tar.gz libmbd-89a3cc19.tar.gz wannier90-971c8b64.tar.gz \
             lapack-3.12.0.tar.gz Ni.pbe-nd-rrkjus.UPF; do
        fetch "$f" || exit 1
    done
    untar "q-e-${QE:0:8}.tar.gz" q-e && untar devicexlib-a6b89ef7.tar.gz q-e/external/devxlib-src \
        && untar libmbd-89a3cc19.tar.gz q-e/external/mbd-src && untar wannier90-971c8b64.tar.gz q-e/external/wannier90-src \
        && untar lapack-3.12.0.tar.gz lapack || exit 1
    # the submodules' directories exist, empty, in the archive. Outside a git checkout QE's CMake
    # clones a submodule (git init, fetch, checkout) unless its directory has a .git, "a previous
    # clone": an empty one keeps the archives' files and the build off the network
    for s in devxlib mbd wannier90; do
        [ -n "$(ls -A "q-e/external/$s")" ] || { rmdir "q-e/external/$s" && mv "q-e/external/$s-src" "q-e/external/$s"; } || exit 1
        mkdir -p "q-e/external/$s/.git" || exit 1
    done;;
build-qe)
    if [ ! -f "$D/lapack-install/lib/liblapack.a" ]; then
        cmake -S "$D/lapack" -B "$D/build-lapack" -DCMAKE_BUILD_TYPE=Release -DCMAKE_Fortran_COMPILER=/usr/bin/gfortran \
            -DCMAKE_INSTALL_PREFIX="$D/lapack-install" -DCMAKE_INSTALL_LIBDIR=lib -DBUILD_SHARED_LIBS=OFF -DCBLAS=OFF \
            -DLAPACKE=OFF -DBUILD_TESTING=OFF > "$D/lapack.log" 2>&1 \
            && cmake --build "$D/build-lapack" -j "$J" >> "$D/lapack.log" 2>&1 && cmake --install "$D/build-lapack" >> "$D/lapack.log" 2>&1 \
            || harness_error "build of LAPACK failed ($D/lapack.log)" || exit 1
    fi
    if [ ! -x "$D/build/bin/ph.x" ]; then
        cmake -S "$D/q-e" -B "$D/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_Fortran_COMPILER=/usr/bin/mpifort.mpich \
            -DCMAKE_C_COMPILER=/usr/bin/mpicc.mpich -DQE_ENABLE_MPI=ON -DQE_ENABLE_OPENMP=OFF -DQE_ENABLE_SCALAPACK=OFF \
            -DQE_FFTW_INTERNAL=ON -DBLAS_LIBRARIES="$D/lapack-install/lib/libblas.a" \
            -DLAPACK_LIBRARIES="$D/lapack-install/lib/liblapack.a;$D/lapack-install/lib/libblas.a" > "$D/build.log" 2>&1 \
            && cmake --build "$D/build" -j "$J" --target pw ph >> "$D/build.log" 2>&1 \
            || harness_error "build of Quantum ESPRESSO failed ($D/build.log)" || exit 1
    fi
    export OMP_NUM_THREADS=1
    b="$D/run/base"
    if [ ! -f "$b/out/nickel.xml" ]; then
        mkdir -p "$b" && cp "$D/Ni.pbe-nd-rrkjus.UPF" "$b/" || exit 1
        sed -e "s#pseudo_dir = '../../pseudo/',#pseudo_dir = './', outdir = './out',#" "$D/q-e/test-suite/ph_base/ni.scf.in" > "$b/ni.scf.in"
        grep -q "outdir = './out'" "$b/ni.scf.in" || harness_error "ni.scf.in has changed" || exit 1
        (cd "$b" && mpirun.mpich -np 3 "$D/build/bin/pw.x" -nk 3 -in ni.scf.in < /dev/null > scf.out 2>&1) \
            && grep -q "JOB DONE" "$b/scf.out" || harness_error "the scf run failed ($b/scf.out)" || exit 1
    fi
    k="$D/run/k39"
    if [ ! -f "$k/ph.in" ]; then
        rm -rf "$k" && cp -a "$b" "$k" && rm -f "$k/scf.out" "$k/ni.scf.in" || exit 1
        sed -e "s#  fildyn='niX.dyn',#  fildyn='niX.dyn', outdir='./out',\n  fildvscf='dvscf', electron_phonon='prt',\n  kx=-0.375, ky=1.125, kz=0.375,#" \
            "$D/q-e/test-suite/ph_base/ni.phX.in" > "$k/ph.in"
        grep -q "electron_phonon='prt'" "$k/ph.in" || harness_error "ni.phX.in has changed" || exit 1
    fi
    if [ ! -f "$D/run/ref-np1.out" ]; then
        w=$(mktemp -d) && cp -a "$k/." "$w/" || exit 1
        (cd "$w" && mpirun.mpich -np 1 "$D/build/bin/ph.x" -in ph.in < /dev/null > ref.out 2>&1) && grep -q "JOB DONE" "$w/ref.out" \
            && cp "$w/ref.out" "$D/run/ref-np1.out" || harness_error "the reference run failed ($w/ref.out)" || exit 1
        rm -rf "$w"
    fi;;
*) echo "usage: $0 fetch-superlu|build-superlu|fetch-qe|build-qe <dir>" >&2; exit 2;;
esac
