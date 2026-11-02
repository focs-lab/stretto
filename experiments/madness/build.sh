#!/usr/bin/env bash
# Build MADNESS (its pthreads task pool, MPI_THREAD_MULTIPLE, static libraries) with the system
# gcc and one MPI, and the shipped unit tests that this scan runs: those of the runtime
# (src/madness/world) and the MPI tests of the numerical library (src/madness/mra, run with 2
# ranks by add_mpi_tests). Also Stretto's tracer and replay library for that MPI. Run inside a
# container of the image built from Dockerfile here, the work directory mounted at /h (source
# /h/src/madness, the repository at /h/repo, build trees /h/build/mad-<mpi>-<variant>):
#   docker run --rm --name mad-build --cpuset-cpus 0-7,40-47 --memory 16g \
#     --user "$(id -u):$(id -g)" -e HOME=/tmp -v ~/mad-work:/h -w /h mad-exp:24.04 \
#     /h/repo/experiments/madness/build.sh [openmpi|mpich ...]
# VARIANT (default plain) names the tree; for any other VARIANT, PATCH names a patch applied
# with patch -p1. JOBS (default 8) bounds the parallel jobs. TSAN=1 compiles MADNESS with clang
# 18 and ThreadSanitizer's instrumentation of atomics only, linked against Stretto's stub
# (docs/threads.md section 17); the tree is then mad-<mpi>-<variant>-tsan.
set -euo pipefail
JOBS=${JOBS:-8} VARIANT=${VARIANT:-plain} TSAN=${TSAN:-0}
S=/h/src/madness R=/h/repo
TARGETS=${TARGETS:-test_world test_dc test_hashdc test_hello test_future test_future2 test_future3
  test_hashthreaded test_queue test_replication test_worldref test_worldptr test_tree test_ar
  test_atomicint test_stack test_vector test_binsorter test_thread_specific test_prof
  test_worldprofile test_cloud test_eval test_halo test_vectormacrotask test_mul_sparse
  test_memory_measurement}
mpis=("$@"); [ ${#mpis[@]} = 0 ] && mpis=(openmpi mpich)
git -C $S log -1 --format='MADNESS %H %cd'
for m in "${mpis[@]}"; do
  make -s -C $R/tracer MPI=$m > /dev/null
  make -s -C $R/replay MPI=$m > /dev/null
  suffix=; cc=/usr/bin/gcc cxx=/usr/bin/g++ flags="-g1" ldflags=
  if [ "$TSAN" = 1 ]; then
    suffix=-tsan cc=/usr/bin/clang-18 cxx=/usr/bin/clang++-18
    flags="-g1 -fsanitize=thread -fno-sanitize-link-runtime -mllvm -tsan-instrument-memory-accesses=0
      -mllvm -tsan-instrument-func-entry-exit=0 -mllvm -tsan-instrument-memintrinsics=0
      -Wno-unused-command-line-argument"
    ldflags="-Wl,--no-as-needed -L$R/tracer/bin -lstretto-tsan -Wl,-rpath,$R/tracer/bin"
  fi
  src=/h/build/mad-$m-$VARIANT-src B=/h/build/mad-$m-$VARIANT$suffix
  if [ "$VARIANT" = plain ]; then src=$S; else
    rm -rf $src && cp -r $S $src && rm -rf $src/.git
    patch -s -d $src -p1 < "${PATCH:?PATCH names the patch of the variant}"
  fi
  rm -rf $B && mkdir -p $B
  # OMPI_CXX / MPICH_CXX make the MPI wrappers call the chosen compiler
  export OMPI_CC=$cc OMPI_CXX=$cxx MPICH_CC=$cc MPICH_CXX=$cxx
  cmake -S $src -B $B -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=$cc -DCMAKE_CXX_COMPILER=$cxx \
    -DCMAKE_C_FLAGS="$(echo $flags)" -DCMAKE_CXX_FLAGS="$(echo $flags)" \
    -DCMAKE_EXE_LINKER_FLAGS="$ldflags" \
    -DMPI_C_COMPILER=/usr/bin/mpicc.$m -DMPI_CXX_COMPILER=/usr/bin/mpicxx.$m \
    -DENABLE_MPI=ON -DMPI_THREAD=multiple -DMADNESS_TASK_BACKEND=Pthreads -DBUILD_TESTING=ON \
    -DENABLE_MKL=OFF -DENABLE_ARMPL=OFF -DENABLE_NVPL=OFF -DENABLE_BLIS=OFF -DENABLE_ACML=OFF \
    -DENABLE_LIBXC=OFF -DENABLE_INTEGRATORXX=OFF -DENABLE_DFTD3=OFF -DENABLE_LIBXSMM=OFF \
    -DENABLE_PCM=OFF -DENABLE_LIBUNWIND=OFF -DLAPACK_LIBRARIES="-llapack;-lblas" \
    > $B.cmake.log 2>&1
  make -C $B -j "$JOBS" $TARGETS > $B.make.log 2>&1
  find $B -name '*.o' -delete
  echo "$m $VARIANT$suffix: $(find $B/src -maxdepth 4 -type f -name 'test_*' -perm -u+x | wc -l) tests"
done
