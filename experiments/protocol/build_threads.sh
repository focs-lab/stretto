#!/usr/bin/env bash
# Builds of the thread campaigns that the uniform campaign rebuilds (experiments/protocol/
# machine-<MACHINE>.env names where each campaign's build is), inside the campaign's image, from the
# repository root (/w), with the build's home mounted at /h, where its binaries' runpaths point:
#   slate  SLATE at 878439b (icl-utk-edu/slate, the commit of experiments/slate/), its library plain
#          and with tntpiv-tags.patch and the wide_lu driver, for Open MPI (/h/lib, /h/wide_lu) and
#          for MPICH (/h/lib-mpich, /h/wide_lu-mpich), by experiments/slate/build-lib.sh
#          docker run --rm --ulimit core=0 --cpuset-cpus <cpus> --user <uid>:<gid> -e HOME=/tmp \
#            -v <repo>:/w -v <SLATE_HOME>:/h -w /w hunt-slate:24.04 experiments/protocol/build_threads.sh slate
#   uintah Uintah 493d2b5 (experiments/uintah/build.sh): hypre and the plain and fixed sus for one MPI
#          docker run ... -v <UINTAH_HOME>:/h [-v <Uintah clone>:/usrc:ro] stretto-uintah:24.04 \
#            experiments/protocol/build_threads.sh uintah openmpi
# Settings: JOBS (make jobs, 8).
# usage: experiments/protocol/build_threads.sh slate|uintah [openmpi|mpich]
set -euo pipefail
SLATE_COMMIT=878439b
case "${1:-}" in
  slate)
    [ -d /h/src/.git ] || git clone -q --recursive https://github.com/icl-utk-edu/slate.git /h/src
    git -C /h/src checkout -q "$SLATE_COMMIT"
    git -C /h/src submodule update -q --init --recursive
    JOBS=${JOBS:-8} experiments/slate/build-lib.sh /h/src /h/build /h/lib /h/wide_lu openmpi
    JOBS=${JOBS:-8} experiments/slate/build-lib.sh /h/src /h/build-mpich /h/lib-mpich /h/wide_lu-mpich mpich
    ;;
  uintah)
    # Uintah 493d2b5 (README) at /usrc, read only, or cloned into /h/uintah; hypre 2.33.0 and the
    # plain and fixed sus (Examples, solvertest) for MPI ($2, openmpi or mpich) into /h, mounted at
    # /h when they run (their runpaths)
    src=/usrc
    if [ ! -d "$src/src" ]; then
      src=/h/uintah
      [ -d "$src/.git" ] || git clone -q https://github.com/Uintah/Uintah.git "$src"
      git -C "$src" checkout -q 493d2b5
    fi
    JOBS=${JOBS:-8} experiments/uintah/build.sh /h "$src" "${2:-openmpi}"
    ;;
  *) echo "usage: $0 slate|uintah [openmpi|mpich]" >&2; exit 2;;
esac
