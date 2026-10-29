#!/usr/bin/env bash
# Build SLATE's tester, CPU only (no GPU backend), against Ubuntu's OpenBLAS and Open MPI.
# Run inside a container of the image built from Dockerfile here:
#   experiments/slate/build.sh SLATE_SRC BUILD
# SLATE_SRC is a clone with its submodules (git clone --recursive), read only; BUILD gets the
# build tree. Settings: JOBS (build jobs, 8), CXXFLAGS (extra flags, for example -g1).
set -eu
S=${1:?usage: build.sh SLATE_SRC BUILD}; B=${2:?usage: build.sh SLATE_SRC BUILD}
JOBS=${JOBS:-8}
mkdir -p "$B"
cmake -S "$S" -B "$B" -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=/usr/bin/gcc \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++ -DCMAKE_CXX_FLAGS="-O2 ${CXXFLAGS:-}" \
  -DMPI_CXX_COMPILER=/usr/bin/mpicxx.openmpi -DMPI_C_COMPILER=/usr/bin/mpicc.openmpi \
  -Dgpu_backend=none -Dblas=openblas -Dblas_threaded=false -Dbuild_tests=yes -Dc_api=no \
  -Dcolor=no > "$B/cmake.log" 2>&1
cmake --build "$B" -j "$JOBS" --target tester > "$B/make.log" 2>&1
ls -l "$B/test/tester"
