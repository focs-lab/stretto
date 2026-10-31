#!/usr/bin/env bash
# Build DLA-Future's dependencies (blaspp, lapackpp, Umpire, pika with MPI), the DLA-Future core
# library, its matrix unit test, the redistribution miniapp and the driver copy_check.cpp.
# Run inside a container of the image built from Dockerfile here, with the work directory
# mounted at /h (sources in /h/src, install prefix /h/install, build trees in /h/build):
#   docker run --rm --name hunt2-build --cpuset-cpus 0-5,40-45 --memory 20g \
#     --user "$(id -u):$(id -g)" -e HOME=/tmp -v ~/thr-hunt2:/h -w /h hunt2-dlaf:24.04 \
#     /h/repo/experiments/dlaf/build.sh [step ...]
# Steps (default all): deps pika dlaf driver. JOBS (default 6) bounds the parallel jobs.
# Umpire uses Ubuntu's fmt, as pika and spdlog do (its bundled fmt 10 would mix with fmt 9).
# Versions: blaspp and lapackpp 2024.10.26, Umpire v2024.07.0, pika and DLA-Future as cloned
# in /h/src (their commits are printed).
set -euo pipefail
JOBS=${JOBS:-6}
S=/h/src B=/h/build P=/h/install
mkdir -p "$S" "$B" "$P"
steps=("$@"); [ ${#steps[@]} = 0 ] && steps=(deps pika dlaf driver)
cm() { cmake -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$P \
         -DCMAKE_PREFIX_PATH=$P -DCMAKE_C_COMPILER=/usr/bin/gcc -DCMAKE_CXX_COMPILER=/usr/bin/g++ "$@"; }
for step in "${steps[@]}"; do
  case $step in
  deps)
    [ -d $S/blaspp ] || git clone -q --depth 1 -b v2024.10.26 https://github.com/icl-utk-edu/blaspp $S/blaspp
    [ -d $S/lapackpp ] || git clone -q --depth 1 -b v2024.10.26 https://github.com/icl-utk-edu/lapackpp $S/lapackpp
    [ -d $S/umpire ] || git clone -q --depth 1 --recursive --shallow-submodules -b v2024.07.0 \
      https://github.com/LLNL/Umpire $S/umpire
    [ -f $P/lib/libblaspp.so ] || cm -S $S/blaspp -B $B/blaspp -Dblas=openblas -Dbuild_tests=no -Duse_openmp=no -Dgpu_backend=none
    [ -f $P/lib/libblaspp.so ] || cmake --build $B/blaspp -j "$JOBS" --target install
    cm -S $S/lapackpp -B $B/lapackpp -DLAPACK_LIBRARIES=/usr/lib/x86_64-linux-gnu/libopenblas.so -Dbuild_tests=no -Dgpu_backend=none
    cmake --build $B/lapackpp -j "$JOBS" --target install
    cm -S $S/umpire -B $B/umpire -DENABLE_TESTS=OFF -DENABLE_EXAMPLES=OFF -DENABLE_DOCS=OFF \
       -DUMPIRE_ENABLE_TOOLS=OFF -DENABLE_BENCHMARKS=OFF -DENABLE_CUDA=OFF -DENABLE_HIP=OFF \
       -DENABLE_OPENMP=OFF -DBUILD_SHARED_LIBS=ON -Dfmt_DIR=/usr/lib/x86_64-linux-gnu/cmake/fmt
    cmake --build $B/umpire -j "$JOBS" --target install
    rm -rf $B/blaspp $B/lapackpp $B/umpire ;;
  pika)
    git -C $S/pika log -1 --format='pika %h %cd'
    cm -S $S/pika -B $B/pika -DPIKA_WITH_MPI=ON -DPIKA_WITH_MALLOC=system \
       -DPIKA_WITH_EXAMPLES=OFF -DPIKA_WITH_TESTS=OFF -DPIKA_WITH_COMPILER_WARNINGS=OFF \
       -DCMAKE_CXX_FLAGS=-g1
    cmake --build $B/pika -j "$JOBS" --target install
    rm -rf $B/pika ;;
  dlaf)
    git -C $S/dlaf log -1 --format='DLA-Future %h %cd'
    cm -S $S/dlaf -B $B/dlaf -DDLAF_BUILD_TESTING=ON -DDLAF_BUILD_MINIAPPS=ON \
       -DDLAF_WITH_OPENMP=OFF -DDLAF_LAPACK_LIBRARY=/usr/lib/x86_64-linux-gnu/libopenblas.so \
       -DMPIEXEC_PREFLAGS="--oversubscribe" -DCMAKE_CXX_FLAGS=-g1
    cmake --build $B/dlaf -j "$JOBS" --target test_matrix miniapp_redistribution
    find $B/dlaf -name '*.o' -delete ;;
  driver)
    # copy_check.cpp is compiled and linked like the redistribution miniapp (its flags.make and
    # link.txt), once with DLA-Future's headers and once with a copy patched by copy-tags.patch
    # (the fix is header-only, so libDLAF.so is the same). DRIVER_OUT (default the build trees)
    # takes the two executables, REPO (default /h/repo) is the repository whose driver is built,
    # VARIANTS (default "plain fixed") narrows them.
    O=${DRIVER_OUT:-$B} R=${REPO:-/h/repo}
    d=$B/dlaf/miniapp/CMakeFiles/miniapp_redistribution.dir
    flags() { sed -n "s/^$1 = //p" $d/flags.make; }
    link=$(sed 's/^\([^ ]*\) .* -o miniapp_redistribution //' $d/link.txt | sed "s|\.\./src/|$B/dlaf/src/|")
    mkdir -p "$O"
    rm -rf $O/dlaf-fixed-include && cp -r $S/dlaf/include $O/dlaf-fixed-include
    patch -s -d $O/dlaf-fixed-include -p2 < $R/experiments/dlaf/copy-tags.patch
    for v in ${VARIANTS:-plain fixed}; do
      inc=$(flags CXX_INCLUDES); exe=$O/copy_check
      [ $v = fixed ] && inc=${inc/-I\/h\/src\/dlaf\/include/-I$O/dlaf-fixed-include} && exe=$O/copy_check_fixed
      # shellcheck disable=SC2046
      /usr/bin/g++ $(flags CXX_DEFINES) $inc $(flags CXX_FLAGS) -Wno-sign-conversion -c \
        $R/experiments/dlaf/copy_check.cpp -o $exe.o
      /usr/bin/g++ -g1 -O3 -pthread $exe.o -o $exe $link
      rm -f $exe.o
    done ;;
  esac
done
