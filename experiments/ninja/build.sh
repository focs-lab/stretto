#!/usr/bin/env bash
# NINJA, the noise-injection tool of "Noise Injection Techniques to Expose Subtle and Unintended
# Message Races" (PPoPP 2017; github.com/PRUNERS/NINJA, LGPL-2.1, Spack package pruners-ninja):
# an LD_PRELOAD library that delays sends from a helper thread, keeping the non-overtaking
# order per destination. Inside the experiment container, from the repository root:
#   fetch   clone NINJA at v1.0.2 (9 Feb 2026) into third_party/ninja/src and check the commit
#   build   libninja.so for MPICH and for Open MPI (third_party/ninja/<mpi>/libninja.so), from
#           the sources and flags of src/Makefile.am, with the system MPI C++ wrappers (no
#           autotools in the image), and libninja-fixed.so, the same with two repairs (random
#           noise, Fortran programs; below); and NINJA's replica of the HYPRE ParaSails race
#           (test/ninja_test_hypre_parasails.c, MPICH), which as shipped exits unless it runs
#           on 64 processes: a copy with that check removed runs on 4 and 8, where its
#           destination lists keep the ranks below the communicator size
# usage: experiments/ninja/build.sh [fetch|build|all]
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
N="${NINJA_DIR:-$R/third_party/ninja}"
COMMIT=09b21ffaab880fcb9b185bf8a320da57dda2995f   # tag v1.0.2
SOURCES="ninj.cpp ninj_fc.cpp ninj_thread.cpp nin_util.cpp nin_mpi_util.cpp mst_io.cpp"

step_fetch() {
    [ -d "$N/src/.git" ] || git clone -q https://github.com/PRUNERS/NINJA.git "$N/src"
    git -C "$N/src" checkout -q "$COMMIT"
    [ "$(git -C "$N/src" rev-parse HEAD)" = "$COMMIT" ] && echo "NINJA at $COMMIT (v1.0.2)"
}
step_build() {
    # libninja.so as shipped, and libninja-fixed.so with two repairs, which the runs use:
    #  - random noise as documented. As shipped, NIN_init_ndrand() reseeds before every random
    #    draw with (int)(time in microseconds + rank); the double is far beyond int, the
    #    conversion gives INT_MIN on x86-64, so every draw is the same number and NIN_PATTERN=1
    #    delays either no send (any NIN_RAND_RATIO below 100) or every send. The repair keeps
    #    the reseeding and takes the low bits of the time instead. The other modes draw only
    #    after seeding with the run's identifier, so they do not change.
    #  - Fortran programs. MPICH's Fortran MPI_INIT calls MPI_Init(NULL, NULL), and NINJA
    #    dereferences both to name its learning files after argv[0], so every Fortran run
    #    crashed in MPI_Init; the repair takes the program's name from the C library instead.
    rm -rf "$N/fixed-src" && cp -r "$N/src/src" "$N/fixed-src"
    sed -i 's|srand((int)(NIN_get_time() \* 1000000 + nin_my_rank));|srand((unsigned int)((long long)(NIN_get_time() * 1000000) + nin_my_rank));|' \
        "$N/fixed-src/nin_util.cpp"
    sed -i 's|^  ninj_fc_init(\*argc, \*argv);$|  static char *nin_argv[] = {program_invocation_name, NULL};\n  ninj_fc_init(argc ? *argc : 1, (argv \&\& *argv) ? *argv : nin_argv);|' \
        "$N/fixed-src/ninj.cpp"
    grep -q 'srand((unsigned int)((long long)' "$N/fixed-src/nin_util.cpp" && grep -q 'nin_argv);$' "$N/fixed-src/ninj.cpp"
    for mpi in mpich openmpi; do
        mkdir -p "$N/$mpi"
        (cd "$N/src/src" && /usr/bin/mpicxx.$mpi -O2 -g -fPIC -shared -std=c++0x -pthread -w -I. \
             -o "$N/$mpi/libninja.so" $SOURCES -lstdc++)
        (cd "$N/fixed-src" && /usr/bin/mpicxx.$mpi -O2 -g -fPIC -shared -std=c++0x -pthread -w -I. \
             -o "$N/$mpi/libninja-fixed.so" $SOURCES -lstdc++)
        echo "built $N/$mpi/libninja.so and libninja-fixed.so"
    done
    # the replica, with the 64-process check (main(), "comm_size != 64") turned off; and its
    # fixed variant, the same without ENABLE_HANG, whose stored-rows requests get their own tag
    # as in HYPRE 2.11.0 (the replica's control)
    local t="$N/src/test" out="$N/mpich/parasails"
    mkdir -p "$out"
    sed 's/if (comm_size != 64) {/if (0) {/' "$t/ninja_test_hypre_parasails.c" > "$out/parasails_anysize.c"
    sed 's|^#define ENABLE_HANG$|/* #define ENABLE_HANG */|' "$out/parasails_anysize.c" > "$out/parasails_anysize_fixed.c"
    grep -q 'if (0) {' "$out/parasails_anysize.c" && grep -q '^/\* #define ENABLE_HANG \*/$' "$out/parasails_anysize_fixed.c"
    for f in "$t/ninja_test_hypre_parasails.c" "$out/parasails_anysize.c" "$out/parasails_anysize_fixed.c"; do
        /usr/bin/mpicc.mpich -O2 -g -I"$t" -o "$out/$(basename "$f" .c)" "$f" "$t/ninja_test_util.c" -lm
    done
    echo "built $out/ninja_test_hypre_parasails (as shipped), $out/parasails_anysize and $out/parasails_anysize_fixed"
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; all) step_fetch && step_build;;
        *) echo "usage: $0 [fetch|build|all]" >&2; exit 2;;
    esac
done
