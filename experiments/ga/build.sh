#!/usr/bin/env bash
# Build Global Arrays with the ComEx MPI progress-thread port (configure --with-mpi-pt), C only,
# and its shipped test programs (global/testing, comex/testing), once per MPI; also Stretto's
# tracer and replay library for that MPI. Run inside a container of the image built from
# Dockerfile here, with the work directory mounted at /h (source /h/src/ga, the repository at
# /h/repo, build trees /h/build/ga-<mpi>-<variant>):
#   docker run --rm --name ga-build --cpuset-cpus 8-15,48-55 --memory 20g \
#     --user "$(id -u):$(id -g)" -e HOME=/tmp -v ~/ga-work:/h -w /h ga-exp:24.04 \
#     /h/repo/experiments/ga/build.sh [openmpi|mpich ...]
# VARIANT (default plain) names the tree; for any other VARIANT, PATCH names a patch applied
# with patch -p1. JOBS (default 8) bounds the parallel jobs.
set -euo pipefail
JOBS=${JOBS:-8} VARIANT=${VARIANT:-plain}
S=/h/src/ga R=/h/repo
mkdir -p /h/build
mpis=("$@"); [ ${#mpis[@]} = 0 ] && mpis=(openmpi mpich)
git -C $S log -1 --format='GA %H %cd'
for m in "${mpis[@]}"; do
  make -s -C $R/tracer MPI=$m > /dev/null
  make -s -C $R/replay MPI=$m > /dev/null
  src=/h/build/ga-$m-$VARIANT-src B=/h/build/ga-$m-$VARIANT
  rm -rf $src $B && cp -r $S $src && rm -rf $src/.git
  if [ "$VARIANT" != plain ]; then patch -s -d $src -p1 < "${PATCH:?PATCH names the patch of the variant}"; fi
  (cd $src && autoreconf -if > $B.autoreconf.log 2>&1)
  mkdir -p $B
  (cd $B && $src/configure --with-mpi-pt --disable-f77 --without-blas --without-lapack \
     --without-scalapack CC=/usr/bin/mpicc.$m CXX=/usr/bin/mpicxx.$m MPICC=/usr/bin/mpicc.$m \
     MPICXX=/usr/bin/mpicxx.$m CFLAGS="-O2 -g" > $B.configure.log 2>&1)
  make -C $B -j "$JOBS" > $B.make.log 2>&1
  make -C $B -j "$JOBS" checkprogs > $B.checkprogs.log 2>&1
  make -C $B/comex -j "$JOBS" checkprogs > $B.comex-checkprogs.log 2>&1
  find $B -name '*.o' -delete
  echo "$m $VARIANT: $(ls $B/global/testing/*.x | wc -l) GA tests, $(ls $B/comex/testing | grep -vc '\.') ComEx tests"
done
