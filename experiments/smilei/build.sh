#!/usr/bin/env bash
# Build Smilei (OpenMP on, its default) with parallel HDF5, once per MPI, and Stretto's tracer
# and replay library for that MPI. Run inside a container of the image built from Dockerfile
# here, with the work directory mounted at /h (source /h/src/smilei, the repository at /h/repo,
# build trees /h/build/smilei-<mpi>-<variant> with the binary `smilei` at their top):
#   docker run --rm --name smi-build --cpuset-cpus 0-7,40-47 --memory 24g \
#     --user "$(id -u):$(id -g)" -e HOME=/tmp -v ~/smi-work:/h -w /h smi-exp:24.04 \
#     /h/repo/experiments/smilei/build.sh [openmpi|mpich ...]
# VARIANT (default plain) names the tree; for any other VARIANT, PATCH names a patch applied
# with patch -p1. JOBS (default 8) bounds the parallel jobs.
set -euo pipefail
JOBS=${JOBS:-8} VARIANT=${VARIANT:-plain}
S=/h/src/smilei R=/h/repo
mkdir -p /h/build
mpis=("$@"); [ ${#mpis[@]} = 0 ] && mpis=(openmpi mpich)
git -C $S log -1 --format='Smilei %H %cd'
for m in "${mpis[@]}"; do
  make -s -C $R/tracer MPI=$m > /dev/null
  make -s -C $R/replay MPI=$m > /dev/null
  B=/h/build/smilei-$m-$VARIANT
  rm -rf $B && cp -r $S $B && rm -rf $B/.git
  if [ "$VARIANT" != plain ]; then patch -s -d $B -p1 < "${PATCH:?PATCH names the patch of the variant}"; fi
  t0=$(date +%s)
  make -C $B -j "$JOBS" SMILEICXX=/usr/bin/mpicxx.$m PYTHONEXE=python3 \
    HDF5_ROOT_DIR=/usr/lib/x86_64-linux-gnu/hdf5/$m > $B.make.log 2>&1
  find $B/build -name '*.o' -delete
  echo "$m $VARIANT: $(ls -la $B/smilei | awk '{print $5}') bytes, $(( $(date +%s) - t0 )) s"
done
