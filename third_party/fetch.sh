#!/usr/bin/env bash
# Fetch and prepare every third-party input of the experiments, at the versions the results
# were produced with. Everything lands under git-ignored directories; nothing here needs root.
#
#   third_party/fetch.sh corpora   MBI and MPI-BugBench at the pinned commits, their generated programs
#   third_party/fetch.sh apps      AMG, MACSio, Nekbone (WP0a study, AMG for E5), built with the system MPI
#   third_party/fetch.sh npb       NPB 3.4.3, built twice (Open MPI and MPICH wrappers) for E4
#   third_party/fetch.sh must      the MPI-BugBench MUST container image (MUST 1.9.2 + MPICH)
#   third_party/fetch.sh all
#
# Compilers are the system ones (/usr/bin/mpicc.openmpi, /usr/bin/mpicc.mpich, /usr/bin/gcc,
# /usr/bin/gfortran); nothing under $HOME is used. A docker build on the development host must
# run under docker.slice (see experiments/RUNBOOK.md).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# upstream commits the results refer to
MBI_COMMIT=c065725      # gitlab.com/MpiBugsInitiative/MpiBugsInitiative
MBB_COMMIT=0d53002      # git-ce.rwth-aachen.de/hpc-public/mpi-bugbench
AMG_COMMIT=3ada8a1      # github.com/LLNL/AMG
MACSIO_COMMIT=7f19af3   # github.com/LLNL/MACSio
NEKBONE_COMMIT=8b0cdf1  # github.com/Nek5000/Nekbone
NPB_URL=https://www.nas.nasa.gov/assets/npb/NPB3.4.3.tar.gz

clone_at() {   # clone_at <url> <dir> <commit>
    if [ ! -d "$2/.git" ]; then git clone --filter=blob:none "$1" "$2"; fi
    git -C "$2" fetch -q --depth 1 origin "$3" 2>/dev/null || git -C "$2" fetch -q origin
    git -C "$2" checkout -q "$3"
    echo "$2 at $(git -C "$2" rev-parse --short HEAD)"
}

corpora() {
    clone_at https://gitlab.com/MpiBugsInitiative/MpiBugsInitiative.git study/apps/MBI "$MBI_COMMIT"
    [ -d study/apps/MBI/gencodes ] || (cd study/apps/MBI && python3 MBI.py -c generate)
    echo "MBI programs: $(ls study/apps/MBI/gencodes/*.c | wc -l)"
    clone_at https://git-ce.rwth-aachen.de/hpc-public/mpi-bugbench.git third_party/mpi-bugbench "$MBB_COMMIT"
    [ -d third_party/mpi-bugbench/scripts/gencodes ] || (cd third_party/mpi-bugbench/scripts && python3 generate.py --generator_dir errors --level 2 --real_world_data ../output.csv)
    echo "MPI-BugBench level-2 programs: $(find third_party/mpi-bugbench/scripts/gencodes -name '*.c' | wc -l)"
}

apps() {
    clone_at https://github.com/LLNL/AMG.git study/apps/AMG "$AMG_COMMIT"
    clone_at https://github.com/LLNL/MACSio.git study/apps/MACSio "$MACSIO_COMMIT"
    clone_at https://github.com/Nek5000/Nekbone.git study/apps/Nekbone "$NEKBONE_COMMIT"
    # AMG with the system Open MPI wrapper (the flags the E5 binary was built with; OpenMP is
    # compiled in and run with one thread): the binary experiments/amg/run.sh traces
    (cd study/apps/AMG && sed -i 's|^CC = .*|CC = /usr/bin/mpicc.openmpi|; s|^INCLUDE_CFLAGS = .*|INCLUDE_CFLAGS = -O2 -DTIMER_USE_MPI -DHYPRE_USING_OPENMP -fopenmp -DHYPRE_HOPSCOTCH -DHYPRE_USING_PERSISTENT_COMM -DHYPRE_BIGINT|; s|^INCLUDE_LFLAGS = .*|INCLUDE_LFLAGS = -lm -fopenmp|' Makefile.include && make -j8 > build.log 2>&1 && ls -l test/amg)
}

npb() {
    mkdir -p third_party/apps && cd third_party/apps
    [ -f NPB3.4.3.tar.gz ] || wget -q "$NPB_URL"
    for mpi in openmpi mpich; do
        dir=NPB3.4.3; [ "$mpi" = mpich ] && dir=NPB-mpich
        [ -d "$dir" ] || { mkdir -p "$dir" && tar xzf NPB3.4.3.tar.gz -C "$dir" --strip-components=1; }
        # Fortran kernels are traced only through the MPICH wrappers (Open MPI's Fortran
        # bindings call PMPI directly); the Open MPI build is the plain-run reference
        cat > "$dir/NPB3.4-MPI/config/make.def" <<DEF
MPIFC = /usr/bin/mpifort.$mpi
FLINK = \$(MPIFC)
FMPI_LIB =
FMPI_INC =
FFLAGS = -O2 -fallow-argument-mismatch
FLINKFLAGS = -O2
MPICC = /usr/bin/mpicc.$mpi
CLINK = \$(MPICC)
CMPI_LIB =
CMPI_INC =
CFLAGS = -O2
CLINKFLAGS = -O2
CC = /usr/bin/gcc -g
BINDIR = ../bin
RAND = randi8
WTIME = wtime.c
DEF
        (cd "$dir/NPB3.4-MPI" && for b in is ep cg mg lu; do for c in S A; do make -s "$b" CLASS=$c > /dev/null; done; done && ls bin | wc -l)
    done
}

must() {
    [ -d third_party/mpi-bugbench ] || corpora
    docker build ${DOCKER_BUILD_ARGS:-} -f third_party/mpi-bugbench/Dockerfile.must -t mbb-must:latest third_party/mpi-bugbench
    docker images mbb-must:latest
}

case "${1:-all}" in
    corpora) corpora;;
    apps) apps;;
    npb) npb;;
    must) must;;
    all) corpora; apps; npb; must;;
    *) echo "usage: $0 corpora|apps|npb|must|all" >&2; exit 2;;
esac
