#!/usr/bin/env bash
# Build hypre 2.33.0 and two Uintah sus binaries, the plain one and one with hypre-comm.patch, for
# one MPI (README, "Cause and fix" and "Uintah's nightly threaded tests").
#
# Run inside a container of the image built from Dockerfile here:
#   experiments/uintah/build.sh WORK UINTAH_SRC [openmpi|mpich]
# UINTAH_SRC is a Uintah clone (493d2b5), read only. WORK gets hypre/, src-fix/ (UINTAH_SRC's src
# as a tree of symbolic links, with real copies of the files the patch changes, patched), plain/
# and fix/ (the builds); the binaries are WORK/plain/StandAlone/sus and WORK/fix/StandAlone/sus.
# Settings: JOBS (build jobs, 4); COMPONENTS (configure's component options, "--enable-examples";
# "--enable-arches" for the ARCHES tests); CLEAN (1: delete the object files of each build once
# sus links, which keeps an ARCHES build small). With 4 jobs hypre takes about a minute and each
# Examples build three; the Examples builds are about 60 MB each.
set -u
W=${1:?usage: build.sh WORK UINTAH_SRC [openmpi|mpich]}; U=${2:?UINTAH_SRC}; MPI=${3:-openmpi}
JOBS=${JOBS:-4}; COMPONENTS=${COMPONENTS:---enable-examples}; CLEAN=${CLEAN:-0}
HERE=$(cd "$(dirname "$0")" && pwd)
case $MPI in
  openmpi) CC=/usr/bin/mpicc.openmpi CXX=/usr/bin/mpicxx.openmpi F77=/usr/bin/mpif77.openmpi ;;
  mpich) CC=/usr/bin/mpicc.mpich CXX=/usr/bin/mpicxx.mpich F77=/usr/bin/mpif77.mpich ;;
  *) echo "unknown MPI $MPI"; exit 1 ;;
esac
U=$(cd "$U" && pwd); mkdir -p "$W" && cd "$W" || exit 1; W=$PWD
if [ ! -e hypre/lib/libHYPRE.so ]; then
  [ -f hypre-2.33.0.tar.gz ] || curl -sSL -o hypre-2.33.0.tar.gz https://github.com/hypre-space/hypre/archive/refs/tags/v2.33.0.tar.gz
  rm -rf hypre-src && mkdir -p hypre-src && tar -xzf hypre-2.33.0.tar.gz -C hypre-src --strip-components=1
  (cd hypre-src/src && ./configure --prefix="$W/hypre" CC=$CC CXX=$CXX CFLAGS="-O2 -fPIC" CXXFLAGS="-O2 -fPIC" \
     --enable-shared --disable-fortran > "$W/hypre.cfg.log" 2>&1 && make -j"$JOBS" > "$W/hypre.make.log" 2>&1 \
     && make install > "$W/hypre.inst.log" 2>&1) || { echo "hypre build failed: $W/hypre.*.log"; exit 1; }
  rm -rf hypre-src
fi
if [ ! -d src-fix ]; then
  mkdir -p src-fix && cp -as "$U/src" src-fix/ || { echo "copy failed"; exit 1; }
  for f in $(sed -n 's|^+++ b/||p' "$HERE/hypre-comm.patch"); do
    rm "src-fix/$f" && cp "$U/$f" "src-fix/$f" || { echo "copy of $f failed"; exit 1; }
  done
  (cd src-fix && patch -p1 < "$HERE/hypre-comm.patch" > /dev/null) || { echo "patch failed"; exit 1; }
fi
for b in plain fix; do
  [ -x $b/StandAlone/sus ] && continue
  src=$U/src; [ $b = fix ] && src=$W/src-fix/src
  mkdir -p $b && (cd $b && "$src/configure" --enable-optimize="-O2" $COMPONENTS --with-hypre="$W/hypre" \
     --with-mpi=built-in CC=$CC CXX=$CXX F77=$F77 > "$W/$b.cfg.log" 2>&1 \
     && make -j"$JOBS" sus > "$W/$b.make.log" 2>&1) || { echo "Uintah build ($b) failed: $W/$b.*.log"; exit 1; }
  [ "$CLEAN" = 1 ] && find $b -name '*.o' -delete
done
echo "built: $W/plain/StandAlone/sus $W/fix/StandAlone/sus"
