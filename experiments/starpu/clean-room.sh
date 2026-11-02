#!/usr/bin/env bash
# The upstream report's reproduction block, run as written in an empty directory: a fresh clone
# of StarPU, its default configuration with MPI (Ubuntu 24.04's Open MPI 4.1, which has no
# MPI_Type_vector_c), the shipped mpi/tests/large_set on 4 ranks with Open MPI's eager limit at
# its minimum (so that MPI_Send does not return before it is matched), then the patch applied by
# git apply. Run inside a container of the image built from Dockerfile here:
#   cd "$(mktemp -d)" && PATCH=/path/to/large-set-dump.patch experiments/starpu/clean-room.sh
set -x
J=${J:-8}
git clone -q https://gitlab.inria.fr/starpu/starpu.git && cd starpu
./autogen.sh > /dev/null 2>&1
mkdir build && cd build
../configure --enable-mpi --disable-cuda --disable-opencl --disable-hip --disable-fortran --disable-build-doc > /dev/null
make -j$J > /dev/null 2>&1 && make -C mpi/tests -j$J large_set > /dev/null 2>&1
run() {
  for i in 1 2 3; do
    STARPU_NCPU=2 STARPU_MPI_NOBIND=1 timeout -k 10 60 mpirun --oversubscribe -np 4 \
      --mca btl self,tcp --mca btl_tcp_eager_limit 56 --mca btl_self_eager_limit 56 \
      mpi/tests/large_set > /dev/null 2>&1; echo "exit $?"
  done
}
run
# the fix
(cd .. && git apply "${PATCH:?the patch}") && make -C mpi/tests -j$J large_set > /dev/null 2>&1
run
