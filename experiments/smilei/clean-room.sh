#!/usr/bin/env bash
# The upstream report's reproduction block, run as written in an empty directory: a fresh clone
# of Smilei, its default build with Ubuntu's Open MPI and parallel HDF5, the shipped benchmark
# tst3d_24_envelope_Laguerre_Gauss_from_xmin on 4 ranks of 2 OpenMP threads, then the patch
# applied by git apply. Run inside a container of the image built from Dockerfile here (Ubuntu
# 24.04, Python 3.12):
#   cd "$(mktemp -d)" && PATCH=/path/to/envelope-gil.patch experiments/smilei/clean-room.sh
set -x
J=${J:-8}
export OMPI_MCA_rmaps_base_oversubscribe=1
git clone -q https://github.com/SmileiPIC/Smilei && cd Smilei
make -j$J PYTHONEXE=python3 HDF5_ROOT_DIR=/usr/lib/x86_64-linux-gnu/hdf5/openmpi > /dev/null 2>&1; ls -la smilei
run() { for i in 1 2 3; do
  rm -rf run && mkdir run && (cd run && OMP_NUM_THREADS=2 timeout 120 mpirun -np 4 ../smilei \
    ../benchmarks/tst3d_24_envelope_Laguerre_Gauss_from_xmin.py > out 2>&1; echo "exit $?"; grep -c ' END' out)
done; }
run
# the fix
git apply "${PATCH:?the patch}" && make -j$J PYTHONEXE=python3 HDF5_ROOT_DIR=/usr/lib/x86_64-linux-gnu/hdf5/openmpi > /dev/null 2>&1
run
