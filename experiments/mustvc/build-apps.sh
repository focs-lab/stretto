#!/usr/bin/env bash
# Build the four applications of Stretto's thread bugs against the mustvc image's MPICH 4.0.2,
# plain and fixed, with the build scripts of their own experiments. Inside the mustvc image,
# with this repository at /w and the sources of the earlier hunts under SRC (slate: SLATE
# 878439b with its submodules; parsec: PaRSEC 6c49997; pika 947cf61, umpire v2024.07.0, blaspp
# and lapackpp v2024.10.26, dlaf: DLA-Future 09fe442; uintah: Uintah 493d2b5):
#   experiments/mustvc/build-apps.sh slate|slate-compat|uintah WORK
#   experiments/mustvc/build-apps.sh parsec|dlaf /h
# Each source tree is copied into the application's directory first. The PaRSEC and DLA-Future
# scripts work in /h, so for them WORK/parsec or WORK/dlaf is mounted at /h and the repository
# at /h/repo (DLA-Future's binaries find their libraries there at run time too).
# SLATE is built with clang 15 (MPICH_CXX=clang++, as the image sets it): its tasks are OpenMP
# tasks, and MUST-vc sees OpenMP only through OMPT, which LLVM's runtime has and gcc's libgomp
# has not. PaRSEC, DLA-Future (pika) and Uintah run threads of their own and no OpenMP; they are
# built with gcc 12, as their experiments build them with gcc 13. JOBS (8).
set -euo pipefail
a=${1:?usage: build-apps.sh slate|uintah|parsec|dlaf WORK}; W=${2:?}
SRC=${SRC:-/src}; export JOBS=${JOBS:-8}
case $a in
slate)
  mkdir -p "$W/slate"; [ -d "$W/slate/src" ] || cp -a "$SRC/slate" "$W/slate/src"
  git -C "$W/slate/src" log -1 --format='SLATE %h %cd'
  SLATE_CC=/usr/bin/clang SLATE_CXX=/usr/bin/clang++ /w/experiments/slate/build-lib.sh \
    "$W/slate/src" "$W/slate/build" "$W/slate/lib" "$W/slate/wide_lu" mpich
  ldd "$W/slate/wide_lu" | grep -E 'libomp|libgomp|libmpi' ;;
slate-compat)
  # the same with slate-trace-init.patch: SLATE's static initialisers call omp_get_max_threads()
  # before MPI_Init, and MUST-vc's OMPT tool crashes when OpenMP starts before MUST does
  mkdir -p "$W/slate"; [ -d "$W/slate/src-compat" ] || cp -a "$SRC/slate" "$W/slate/src-compat"
  git -C "$W/slate/src-compat" diff --quiet && git -C "$W/slate/src-compat" apply /w/experiments/mustvc/slate-trace-init.patch
  SLATE_CC=/usr/bin/clang SLATE_CXX=/usr/bin/clang++ /w/experiments/slate/build-lib.sh \
    "$W/slate/src-compat" "$W/slate/build-compat" "$W/slate/lib-compat" "$W/slate/wide_lu-compat" mpich ;;
uintah)
  mkdir -p "$W/uintah"
  MPICH_CC=gcc MPICH_CXX=g++ MPICH_F77=gfortran /w/experiments/uintah/build.sh "$W/uintah" "$SRC/uintah" mpich ;;
parsec)
  mkdir -p /h/src /h/build; [ -d /h/src/parsec ] || cp -a "$SRC/parsec" /h/src/parsec
  MPICH_CC=gcc MPICH_CXX=g++ /w/experiments/parsec/build.sh ;;
dlaf)
  mkdir -p /h/src
  for d in blaspp lapackpp umpire pika dlaf; do [ -d "/h/src/$d" ] || cp -a "$SRC/$d" "/h/src/$d"; done
  MPICH_CC=gcc MPICH_CXX=g++ /w/experiments/dlaf/build.sh ;;
*) echo "unknown application $a" >&2; exit 2 ;;
esac
