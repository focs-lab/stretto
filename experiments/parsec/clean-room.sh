#!/usr/bin/env bash
# The reproduction block of the upstream report, verbatim, in an empty directory: a fresh clone
# of PaRSEC, its reshape test built and run with two virtual processes, then the patch applied
# and the test rebuilt and run again. Run inside a container of the image built from Dockerfile
# here, in an empty directory with self-tags.patch copied into it:
#   cd DIR && bash /path/to/clean-room.sh
set -ex
J=6   # parallel build jobs
# --- reproduction block (report) ---
git clone -q https://github.com/ICLDisco/parsec && cd parsec
cmake -B build -DCMAKE_BUILD_TYPE=RelWithDebInfo -DPARSEC_DIST_WITH_MPI=ON -DBUILD_TESTING=ON
cmake --build build -j$J --target reshape
export HWLOC_SYNTHETIC="package:2 core:3 pu:1"   # two sockets; not needed on a two-socket node
for i in 1 2 3; do
  mpirun -np 1 -x HWLOC_SYNTHETIC build/tests/collections/reshape/reshape -N 120 -t 9 -c 6 -m 1 \
    -- --mca runtime_vpmap hwloc 2>&1 | grep '^Test'
done
# the fix
git apply ../self-tags.patch && cmake --build build -j$J --target reshape
for i in 1 2 3; do
  mpirun -np 1 -x HWLOC_SYNTHETIC build/tests/collections/reshape/reshape -N 120 -t 9 -c 6 -m 1 \
    -- --mca runtime_vpmap hwloc 2>&1 | grep '^Test'
done
# --- end of block ---
git log -1 --format='PaRSEC %h %cd'
