#!/usr/bin/env bash
# The upstream report's reproduction block, run as written in an empty directory: a fresh clone
# of GA, the progress-thread port, the shipped nbtestc on 4 ranks of one machine (no emulated
# nodes), then the patch applied by git apply. Run inside a container of the image built from
# Dockerfile here (Ubuntu 24.04 with Open MPI, autotools), with --shm-size 4g:
#   cd "$(mktemp -d)" && PATCH=/path/to/nbtest-progress.patch experiments/ga/clean-room.sh
set -x
J=${J:-8}
git clone -q https://github.com/GlobalArrays/ga && cd ga
autoreconf -fi > /dev/null 2>&1
mkdir build && cd build
../configure --with-mpi-pt --disable-f77 > /dev/null
make -j$J > /dev/null 2>&1 && make -j$J checkprogs > /dev/null 2>&1
for i in 1 2 3; do
  timeout 60 mpirun -np 4 global/testing/nbtestc.x 2>&1 | grep -E 'OK|fail'; echo "exit ${PIPESTATUS[0]}"
done
# the fix
(cd .. && git apply "${PATCH:?the patch}") && make -j$J > /dev/null 2>&1 && make -j$J checkprogs > /dev/null 2>&1
for i in 1 2 3; do
  timeout 60 mpirun -np 4 global/testing/nbtestc.x 2>&1 | grep -E 'OK|fail'; echo "exit ${PIPESTATUS[0]}"
done
