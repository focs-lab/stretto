#!/usr/bin/env bash
# Build MUST 1.11.2 against the system Open MPI into third_party/must/install.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p must && cd must
[ -f MUST-v1.11.2.tar.gz ] || wget -q https://hpc.rwth-aachen.de/must/files/MUST-v1.11.2.tar.gz
[ -d MUST-v1.11.2 ] || tar xzf MUST-v1.11.2.tar.gz
mkdir -p build && cd build
CC=/usr/bin/gcc CXX=/usr/bin/g++ FC=/usr/bin/gfortran cmake ../MUST-v1.11.2 \
  -DCMAKE_INSTALL_PREFIX="$PWD/../install" -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_FORTRAN=OFF -DENABLE_TYPEART=OFF -DENABLE_TESTS=OFF \
  -DMPI_C_COMPILER=/usr/bin/mpicc.openmpi -DMPI_CXX_COMPILER=/usr/bin/mpicxx.openmpi
make -j32 install
make -j32 install-prebuilds || true
echo BUILD-DONE
