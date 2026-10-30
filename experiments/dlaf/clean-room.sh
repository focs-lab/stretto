#!/usr/bin/env bash
# The reproduction block of the upstream report, verbatim, in an empty directory: fresh clones,
# DLA-Future and its dependencies installed to ./prefix, the driver built with CMake, run on 2
# ranks; then the patch applied, DLA-Future reinstalled, the driver rebuilt and run again.
# Run inside a container of the image built from Dockerfile here (Ubuntu 24.04 with the
# packages the block lists), in an empty directory DIR with copy_check.cpp, CMakeLists.txt and
# copy-tags.patch copied into it:
#   cd DIR && bash /path/to/clean-room.sh
set -ex
J=6   # parallel build jobs
# --- reproduction block (report) ---
P=$PWD/prefix; export LD_LIBRARY_PATH=$P/lib   # blaspp, lapackpp, pika install without RPATH
git clone -q --depth 1 -b v2024.10.26 https://github.com/icl-utk-edu/blaspp
git clone -q --depth 1 -b v2024.10.26 https://github.com/icl-utk-edu/lapackpp
git clone -q --depth 1 --recursive --shallow-submodules -b v2024.07.0 https://github.com/LLNL/Umpire
git clone -q https://github.com/pika-org/pika
git clone -q https://github.com/eth-cscs/DLA-Future
OB=/usr/lib/x86_64-linux-gnu/libopenblas.so
cmake -S blaspp -B b/blaspp -DCMAKE_INSTALL_PREFIX=$P -Dblas=openblas -Dbuild_tests=no -Dgpu_backend=none
cmake --build b/blaspp -j$J --target install
cmake -S lapackpp -B b/lapackpp -DCMAKE_INSTALL_PREFIX=$P -DCMAKE_PREFIX_PATH=$P -DLAPACK_LIBRARIES=$OB \
  -Dbuild_tests=no -Dgpu_backend=none
cmake --build b/lapackpp -j$J --target install
cmake -S Umpire -B b/umpire -DCMAKE_INSTALL_PREFIX=$P -DENABLE_TESTS=OFF -DENABLE_EXAMPLES=OFF \
  -DENABLE_DOCS=OFF -DUMPIRE_ENABLE_TOOLS=OFF -DBUILD_SHARED_LIBS=ON -Dfmt_DIR=/usr/lib/x86_64-linux-gnu/cmake/fmt
cmake --build b/umpire -j$J --target install
cmake -S pika -B b/pika -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$P -DPIKA_WITH_MPI=ON \
  -DPIKA_WITH_MALLOC=system -DPIKA_WITH_EXAMPLES=OFF -DPIKA_WITH_TESTS=OFF
cmake --build b/pika -j$J --target install
cmake -S DLA-Future -B b/dlaf -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=$P -DCMAKE_PREFIX_PATH=$P \
  -DDLAF_LAPACK_LIBRARY=$OB -DDLAF_BUILD_TESTING=OFF -DDLAF_BUILD_MINIAPPS=OFF
cmake --build b/dlaf -j$J --target install
cmake -S . -B b/driver -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH=$P && cmake --build b/driver
# M N mb_src nb_src mb_dst nb_dst P Q src_rank(r c) dst_rank(r c) copies jitter_us
mpirun -np 2 -x LD_LIBRARY_PATH b/driver/copy_check 512 512 64 64 64 64 2 1 0 0 1 0 10 0 --pika:threads=4 --pika:bind=none
# the fix
git -C DLA-Future apply ../copy-tags.patch
cmake --build b/dlaf -j$J --target install && cmake --build b/driver --clean-first
mpirun -np 2 -x LD_LIBRARY_PATH b/driver/copy_check 512 512 64 64 64 64 2 1 0 0 1 0 10 0 --pika:threads=4 --pika:bind=none
# --- end of block ---
git -C DLA-Future log -1 --format='DLA-Future %h %cd'
git -C pika log -1 --format='pika %h %cd'
