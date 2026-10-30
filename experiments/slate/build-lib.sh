#!/usr/bin/env bash
# Build libslate (no tester) for one MPI, plain and with tntpiv-tags.patch, and the wide_lu
# driver against it. Run inside a container of the image built from Dockerfile here:
#   experiments/slate/build-lib.sh SLATE_SRC BUILD LIBDIR DRIVER MPI
# SLATE_SRC is a clone with its submodules and without the patch (the script applies it, builds,
# and reverses it); MPI is openmpi or mpich. LIBDIR gets plain/libslate.so.2 and
# fixed/libslate.so.2, DRIVER the wide_lu binary. Settings: JOBS (8).
set -eu
S=${1:?usage: build-lib.sh SLATE_SRC BUILD LIBDIR DRIVER MPI}; B=${2:?}; L=${3:?}; D=${4:?}; M=${5:?}
JOBS=${JOBS:-8}
HERE=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$B" "$L/plain" "$L/fixed" "$(dirname "$D")"
cmake -S "$S" -B "$B" -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=/usr/bin/gcc \
  -DCMAKE_CXX_COMPILER=/usr/bin/g++ -DCMAKE_CXX_FLAGS="-O2" \
  -DMPI_CXX_COMPILER=/usr/bin/mpicxx.$M -DMPI_C_COMPILER=/usr/bin/mpicc.$M \
  -Dgpu_backend=none -Dblas=openblas -Dblas_threaded=false -Dbuild_tests=no -Dc_api=no \
  -Dcolor=no > "$B/cmake.log" 2>&1
cmake --build "$B" -j "$JOBS" --target slate > "$B/make.log" 2>&1
cp -L "$B/libslate.so.2" "$L/plain/"
(cd "$S" && git apply "$HERE/tntpiv-tags.patch")
cmake --build "$B" -j "$JOBS" --target slate >> "$B/make.log" 2>&1 || { (cd "$S" && git apply -R "$HERE/tntpiv-tags.patch"); exit 1; }
cp -L "$B/libslate.so.2" "$L/fixed/"
(cd "$S" && git apply -R "$HERE/tntpiv-tags.patch")
/usr/bin/mpicxx.$M -O2 -fopenmp -I"$S/include" -I"$B/blaspp/include" -I"$S/blaspp/include" \
  -I"$B/lapackpp/include" -I"$S/lapackpp/include" "$HERE/wide_lu.cc" -o "$D" \
  -L"$B" -L"$B/blaspp" -L"$B/lapackpp" -lslate -llapackpp -lblaspp \
  -Wl,-rpath,"$B:$B/blaspp:$B/lapackpp"
ls -l "$L"/*/libslate.so.2 "$D"
