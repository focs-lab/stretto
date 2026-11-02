#!/usr/bin/env bash
# Build StarPU with its MPI layer (CPU workers only, no CUDA, OpenCL, HIP, Fortran, Python or
# simgrid) and the shipped MPI tests (mpi/tests, built as check programs, not run), once per
# MPI, with clang 18 and ThreadSanitizer's instrumentation of atomics only, linked against
# Stretto's stub (docs/threads.md section 17: StarPU's workers synchronise through atomics);
# also the tracer and replay library for that MPI. Run inside a container of the image built
# from Dockerfile here, the work directory mounted at /h (source /h/src/starpu, the repository
# at /h/repo, build trees /h/build/spu-<mpi>-<variant>, installs /h/install/spu-<mpi>-<variant>):
#   docker run --rm --name spu-build --cpuset-cpus 0-7,40-47 --memory 12g \
#     --user "$(id -u):$(id -g)" -e HOME=/tmp -v ~/spu-work:/h -w /h spu-exp:24.04 \
#     /h/repo/experiments/starpu/build.sh [openmpi|mpich ...]
# VARIANT (default plain) names the tree; for any other VARIANT, PATCH names a patch applied
# with patch -p1. JOBS (default 8) bounds the parallel jobs. TSAN=0 builds with gcc instead.
set -euo pipefail
JOBS=${JOBS:-8} VARIANT=${VARIANT:-plain} TSAN=${TSAN:-1}
S=/h/src/starpu R=/h/repo
mpis=("$@"); [ ${#mpis[@]} = 0 ] && mpis=(openmpi mpich)
git -C $S log -1 --format='StarPU %H %cd'
for m in "${mpis[@]}"; do
  make -s -C $R/tracer MPI=$m > /dev/null
  make -s -C $R/replay MPI=$m > /dev/null
  cc=/usr/bin/gcc flags="-O2 -g1" ldflags=
  if [ "$TSAN" = 1 ]; then
    cc=/usr/bin/clang-18
    flags="-O2 -g1 -fsanitize=thread -fno-sanitize-link-runtime -mllvm -tsan-instrument-memory-accesses=0
      -mllvm -tsan-instrument-func-entry-exit=0 -mllvm -tsan-instrument-memintrinsics=0
      -Wno-unused-command-line-argument"
    ldflags="-Wl,--no-as-needed -L$R/tracer/bin -lstretto-tsan -Wl,-rpath,$R/tracer/bin"
  fi
  src=/h/build/spu-$m-$VARIANT-src B=/h/build/spu-$m-$VARIANT P=/h/install/spu-$m-$VARIANT
  rm -rf $src $B $P && cp -r $S $src && rm -rf $src/.git
  if [ "$VARIANT" != plain ]; then patch -s -d $src -p1 < "${PATCH:?PATCH names the patch of the variant}"; fi
  (cd $src && ./autogen.sh > $B.autogen.log 2>&1)
  mkdir -p $B
  export OMPI_CC=$cc MPICH_CC=$cc
  (cd $B && $src/configure --prefix=$P --enable-mpi --with-mpicc=/usr/bin/mpicc.$m --with-mpicxx=/usr/bin/mpicxx.$m \
     --with-mpiexec=/usr/bin/mpirun.openmpi --disable-cuda --disable-opencl --disable-hip \
     --disable-fortran --disable-build-doc --disable-starpupy --disable-socl --disable-starpufft \
     --disable-starpurm --disable-starpu-top --disable-gcc-extensions --disable-simgrid \
     --disable-mlr --disable-hdf5 --disable-fxt --without-fxt \
     CC=$cc CFLAGS="$(echo $flags)" LDFLAGS="$ldflags" > $B.configure.log 2>&1)
  make -C $B -j "$JOBS" > $B.make.log 2>&1
  make -C $B/mpi/tests -j "$JOBS" check TESTS= > $B.tests.log 2>&1
  find $B -name '*.o' -delete
  echo "$m $VARIANT: $(find $B/mpi/tests -maxdepth 1 -type f -perm -u+x | wc -l) MPI test programs"
done
