#!/usr/bin/env bash
# Build and install MUST 1.11.2 against one MPI library of the image, inside the image build
# (experiments/protocol/must/Dockerfile), into /opt/must/<mpi>.
# usage: configure-must.sh <mpich|openmpi> <make jobs>
set -euo pipefail
mpi=$1 jobs=$2
case "$mpi" in mpich|openmpi) ;; *) echo "MPI is mpich or openmpi" >&2; exit 2;; esac
export MPICH_CC=clang-18 MPICH_CXX=clang++-18 OMPI_CC=clang-18 OMPI_CXX=clang++-18
b=/tmp/build-must/build-$mpi
mkdir -p "$b" && cd "$b"
CC=clang-18 CXX=clang++-18 FC=gfortran cmake /tmp/build-must/MUST-v1.11.2 \
    -DCMAKE_INSTALL_PREFIX="/opt/must/$mpi" -DCMAKE_BUILD_TYPE=Release \
    -DENABLE_FORTRAN=ON -DENABLE_TYPEART=On \
    -DLLVM_DIR=/usr/lib/llvm-18/lib/cmake/llvm -DClang_DIR=/usr/lib/llvm-18/lib/cmake/clang \
    -DMPI_C_COMPILER="/usr/bin/mpicc.$mpi" -DMPI_CXX_COMPILER="/usr/bin/mpicxx.$mpi" \
    -DMPI_Fortran_COMPILER="/usr/bin/mpif90.$mpi" -DMPIEXEC_EXECUTABLE="/usr/bin/mpiexec.$mpi"
make -j"$jobs" install
make -j"$jobs" install-prebuilds
ls "/opt/must/$mpi/bin/mustrun"
