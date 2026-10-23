#!/usr/bin/env bash
# PETSc 3.26.0, the current release, scouted for wildcard matching bugs.
#
# PETSc's own wildcard sites (src/sys/utils/mpits.c, PetscCommBuildTwoSided with MPI_Iprobe
# from MPI_ANY_SOURCE in its NBX variant; mpimesg.c, PetscGatherMessageLengths; the legacy
# MatStash and VecStash; MatIncreaseOverlap) take a fresh tag from PetscCommDuplicate or
# PetscCommGetNewTag per call or per object, and their exchanges begin with a collective count
# or end with a barrier, so a probe of one call cannot see a message of the next. Three older
# sites do not follow that pattern and get drivers of their own: PCTFS (src/ksp/pc/impls/tfs/
# comm.c, gs.c) receives from MPI_ANY_SOURCE on MPI_COMM_WORLD itself with fixed tags (MSGTAG0-6
# in tfs.h, plus the sender's or the receiver's rank); MatGetRowMaxAbs_MPISBAIJ (src/mat/impls/
# sbaij/mpi/mpisbaij.c) receives from MPI_ANY_SOURCE with MPI_ANY_TAG on the matrix's
# communicator (PETSc's inner duplicate of the user's, as every object's), the senders' ranks as
# tags (PCJACOBI with -pc_jacobi_type rowmax calls it); and MatIncreaseOverlap_MPISBAIJ_Once (sbaijov.c) probes from MPI_ANY_SOURCE, which
# 3.26.0 runs only under its option -IncreaseOverlap_old (by default SBAIJ overlap builds the full
# pattern and takes the BAIJ algorithm). PETSc also
# drives HYPRE: PETSc 3.26.0 downloads HYPRE 3.2.0, and PCHYPRE exposes ParaSails' number of
# pattern levels and its threshold as -pc_hypre_parasails_nlevels and -pc_hypre_parasails_thresh,
# so PETSc's own KSP tutorial ex2 reaches the ParaSails race of experiments/hypre3/ with
# command-line options only. The driver and the options are all PETSc's own; nothing is patched.
# PCSetFromOptions_HYPRE_ParaSails (src/ksp/pc/impls/hypre/impl/ihypre.c, 3.26.0) passes the level
# count to HYPRE only together with the threshold (HYPRE_ParaSailsSetParams inside the if of
# -pc_hypre_parasails_thresh), so -pc_hypre_parasails_nlevels alone is ignored (ParaSails' own
# logging prints num_levels 1, -ksp_view nlevels 2) and the threshold must be given too; any value
# reaches the race (the scan step: 0.01, 0.05 and 0.1, PETSc's default, all do).
#
# Steps, inside the experiment container, from the repository root, Open MPI build:
#   fetch    download PETSc 3.26.0 (GitHub), f2cblaslapack 3.8.0.q2 (PETSc's site) and HYPRE
#            3.2.0 (GitHub), check their sha256
#   build    configure (Open MPI wrappers, no Fortran, optimised with -g, BLAS/LAPACK and HYPRE
#            from those archives), make -j$MAKE_JOBS, then the KSP tutorials ex2, ex3 and ex45
#   parasails  RUNS traced runs of ex2 with ParaSails at two pattern levels and at one (the
#            default), at 4 and 8 processes, analysed, every alternative replayed with its
#            horizon pins and its control (experiments/scout.py), JOBS at a time
#   predict  the same for the drivers of PETSc's own wildcard sites (DRIVERS below), except
#            that of the alternatives within one exchange (scout.py's kind "same", of no error
#            class), hundreds per run in PetscCommBuildTwoSided, a sample of SAME_SAMPLE per
#            driver is replayed ("all" replays every one)
#   stress   STRESS_RUNS untraced runs of ex2 with ParaSails at two levels per process count,
#            plain and with random send and receive delays (the budget and delays of
#            experiments/hypre/campaign.sh), TIMEOUT seconds each; and at one level, the default
#   ninja    NINJA (experiments/ninja/build.sh, Open MPI library) on ex2 with ParaSails at two
#            levels: STRESS_RUNS runs per process count in its random (50 ms), system- and
#            application-centric modes (experiments/ninja/run.py)
#   all      fetch build parasails predict stress
#   scan     the reach of the race: SCAN_RUNS traced runs (analysis only) per ParaSails
#            threshold (0.01, 0.05, 0.1), level count (1-3) and symmetry setting, on ex2 and
#            ex45 (3D seven-point Laplacian) at 4 and 8 processes
# Writes experiments/results/petsc/{parasails,predict,stress,ninja,scan}.csv.
# usage: experiments/petsc/run.sh [fetch|build|parasails|predict|stress|ninja|scan|all]
#        RUNS (5), STRESS_RUNS (40), SCAN_RUNS (2), TIMEOUT (30), JOBS (1), MAKE_JOBS (12), SAME_SAMPLE (20),
#        PETSC_SRC_DIR (third_party/apps/petsc), NINJA_DIR (third_party/ninja), MPI_CC and MPI_CXX
#        (the build's MPI wrappers, default Open MPI's), PETSC_ARCH (arch-ompi-opt)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${PETSC_SRC_DIR:-$R/third_party/apps/petsc}"
OUT="$R/experiments/results/petsc"; mkdir -p "$OUT"
RUNS="${RUNS:-5}"; STRESS_RUNS="${STRESS_RUNS:-40}"; TIMEOUT="${TIMEOUT:-30}"
PINLIB="$R/replay/bin/openmpi/libstretto-pin.so"
MPI_CC="${MPI_CC:-/usr/bin/mpicc.openmpi}"; MPI_CXX="${MPI_CXX:-/usr/bin/mpicxx.openmpi}"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
V=3.26.0
export PETSC_DIR="$D/petsc-$V" PETSC_ARCH="${PETSC_ARCH:-arch-ompi-opt}"
TUT="$PETSC_DIR/src/ksp/ksp/tutorials"
declare -A SHA=([petsc-3.26.0.tar.gz]=3f1d722dd3b189a6c5ed0a114abcaae624feb9f525023d8db45d514872da64bd
                [f2cblaslapack-3.8.0.q2.tar.gz]=12fa8d5001e313aeeea22eb2054883f59e3e3c656f11129f31abb02ddbf8ad60
                [hypre-3.2.0.tar.gz]=5273205a310fb6aa3ae506ce216760fb67b30e02024874f3cdb8b811e4801de7)
declare -A URL=([petsc-3.26.0.tar.gz]=https://github.com/petsc/petsc/archive/refs/tags/v3.26.0.tar.gz
                [f2cblaslapack-3.8.0.q2.tar.gz]=https://web.cels.anl.gov/projects/petsc/download/externalpackages/f2cblaslapack-3.8.0.q2.tar.gz
                [hypre-3.2.0.tar.gz]=https://github.com/hypre-space/hypre/archive/refs/tags/v3.2.0.tar.gz)
# driver name, process counts, arguments of ex2 or ex3. The ParaSails group: two pattern levels
# with the threshold given (the race; "dense" for the threshold 0.01 the campaign used); one
# level, the same threshold (a control with no cross); and the level count without a threshold,
# which PETSc does not pass on, so HYPRE runs one level (no cross).
PS="ex2 -m 48 -n 48 -pc_type hypre -pc_hypre_type parasails"
PARASAILS="ex2-parasails2-dense|4 8|$PS -pc_hypre_parasails_nlevels 2 -pc_hypre_parasails_thresh 0.01
ex2-parasails1-dense|4 8|$PS -pc_hypre_parasails_nlevels 1 -pc_hypre_parasails_thresh 0.01
ex2-parasails2-nothresh|4 8|$PS -pc_hypre_parasails_nlevels 2"
DRIVERS="ex2-gamg|4 8|ex2 -m 32 -n 32 -pc_type gamg
ex2-asm2|4|ex2 -m 32 -n 32 -pc_type asm -pc_asm_overlap 2
ex2-boomeramg|4|ex2 -m 32 -n 32 -pc_type hypre -pc_hypre_type boomeramg
ex3|4|ex3 -m 16
ex3-legacy|4|ex3 -m 16 -matstash_legacy -vec_assembly_legacy -build_twosided allreduce
ex2-tfs|4 8|ex2 -m 32 -n 32 -pc_type tfs
ex2-sbaij-rowmax|4 8|ex2 -m 32 -n 32 -mat_type sbaij -mat_ignore_lower_triangular -pc_type jacobi -pc_jacobi_type rowmax
ex2-sbaij-asm2|4|ex2 -m 32 -n 32 -mat_type sbaij -mat_ignore_lower_triangular -pc_type asm -pc_asm_overlap 2 -sub_pc_type icc
ex2-sbaij-asm2-old|4|ex2 -m 32 -n 32 -mat_type sbaij -mat_ignore_lower_triangular -pc_type asm -pc_asm_overlap 2 -sub_pc_type icc -IncreaseOverlap_old"

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    for f in "${!URL[@]}"; do
        [ -f "$f" ] || curl -sSfL -o "$f" "${URL[$f]}" || return 1
        echo "${SHA[$f]}  $f" | sha256sum -c - || return 1
    done
    [ -d "petsc-$V" ] || tar xzf "petsc-$V.tar.gz"
    cd "$R"
}
step_build() {
    if [ ! -f "$PETSC_DIR/$PETSC_ARCH/lib/libpetsc.so" ]; then
        (cd "$PETSC_DIR" && ./configure --with-cc="$MPI_CC" --with-cxx="$MPI_CXX" --with-fc=0 \
             --with-debugging=0 COPTFLAGS="-O2 -g" CXXOPTFLAGS="-O2 -g" --with-x=0 \
             --download-f2cblaslapack="$D/f2cblaslapack-3.8.0.q2.tar.gz" --download-hypre="$D/hypre-3.2.0.tar.gz" \
             --with-make-np="${MAKE_JOBS:-12}" > "$D/configure.log" 2>&1 \
         && make MAKE_NP="${MAKE_JOBS:-12}" all > "$D/make.log" 2>&1) || { echo "build of PETSc $V failed" >&2; return 1; }
    fi
    make -C "$TUT" ex2 ex3 ex45 > "$D/tutorials.log" 2>&1 || { echo "build of the tutorials failed" >&2; return 1; }
}
scout() {   # scout <table> <drivers> [more scout.py options]
    local table="$1" drivers="$2"; shift 2
    rm -f "$OUT/$table.csv"
    while IFS='|' read -r name nps cmd; do
        read -r -a a <<< "$cmd"
        python3 experiments/scout.py --code petsc --version "$V" --driver "$name" --np $nps --runs "$RUNS" \
            --jobs "${JOBS:-1}" --timeout "$TIMEOUT" "$@" --out "$OUT/$table.csv" -- "$TUT/${a[0]}" "${a[@]:1}" || return 1
    done <<< "$drivers"
}
same_sample() { [ "${SAME_SAMPLE:-20}" = all ] || echo "--same-sample ${SAME_SAMPLE:-20}"; }
step_parasails() { scout parasails "$PARASAILS"; }
step_scan() {
    # where does the race reach? SCAN_RUNS traced runs per setting, analysed only (no replay):
    # ParaSails' threshold and level count and its symmetry setting, on ex2 (2D five-point
    # Laplacian, row strips) and ex45 (3D seven-point Laplacian on a DMDA, 3D decomposition)
    rm -f "$OUT/scan.csv"
    local d np th nl sym
    for d in "ex2 -m 48 -n 48" "ex45 -da_grid_x 16 -da_grid_y 16 -da_grid_z 16"; do
        read -r -a a <<< "$d"
        for np in 4 8; do for th in 0.01 0.05 0.1; do for nl in 1 2 3; do for sym in nonsymmetric SPD; do
            python3 experiments/scout.py --code petsc --version "$V" --driver "${a[0]} thresh=$th nlevels=$nl sym=$sym" \
                --np $np --runs "${SCAN_RUNS:-2}" --no-replay --out "$OUT/scan.csv" -- "$TUT/${a[0]}" "${a[@]:1}" \
                -pc_type hypre -pc_hypre_type parasails -pc_hypre_parasails_thresh $th -pc_hypre_parasails_nlevels $nl \
                -pc_hypre_parasails_sym $sym || return 1
        done; done; done; done
    done
}
step_predict() { scout predict "$DRIVERS" $(same_sample); }
step_ninja() {
    local np m
    rm -f "$OUT/ninja.csv"
    for np in 4 8; do
        for m in random system app; do
            local extra=(); [ $m = random ] && extra=(--rand-ratio 10 --rand-delay 50000)
            python3 experiments/ninja/run.py runs --np $np --mode $m "${extra[@]}" --runs "$STRESS_RUNS" --timeout "$TIMEOUT" \
                --mpi openmpi --label ex2-parasails2-dense --out "$OUT/ninja.csv" --marks "solved=iterations" \
                --value "iterations *([0-9]+)" -- "$TUT/ex2" -m 48 -n 48 -pc_type hypre -pc_hypre_type parasails \
                -pc_hypre_parasails_nlevels 2 -pc_hypre_parasails_thresh 0.01 || return 1
        done
    done
}
step_stress() {
    need_file "$PINLIB" || return 1
    echo "version,driver,np,run,setting,result,seconds,iterations,commit" > "$OUT/stress.csv"
    local w; w=$(mktemp -d)
    for nl in 2 1; do
        for np in 4 8; do
            for i in $(seq 1 "$STRESS_RUNS"); do
                for setting in plain delays; do
                    local env=()
                    if [ $setting = delays ]; then
                        ds=$(python3 -c "import random; r=random.Random($i*100+$np); print(','.join(f'{k}:{r.choice([0,0,0,1,5,20,50])}' for k in range($np)))")
                        dr=$(python3 -c "import random; r=random.Random($i*100+$np+7); print(','.join(f'{k}:{r.choice([0,0,0,1,5,20,50])}' for k in range($np)))")
                        env=(-x LD_PRELOAD="$PINLIB" -x STRETTO_SEND_DELAY_MS=$ds -x STRETTO_RECV_DELAY_MS=$dr)
                    fi
                    t0=$SECONDS
                    (cd "$w" && /usr/bin/timeout -k 5 $TIMEOUT mpirun.openmpi --oversubscribe -np $np "${env[@]}" "$TUT/ex2" \
                         -m 48 -n 48 -pc_type hypre -pc_hypre_type parasails -pc_hypre_parasails_nlevels $nl \
                         -pc_hypre_parasails_thresh 0.01 < /dev/null > "$w/out" 2>&1)
                    r=$(run_result $? $((SECONDS - t0)) "$TIMEOUT")
                    [ $setting = plain ] || preload_ok "$w/out" || { harness_error "$PINLIB did not load (np $np, run $i)"; return 1; }
                    it=$(sed -n 's/.*iterations *\([0-9]*\).*/\1/p' "$w/out" | head -1)
                    echo "$V,ex2-parasails$nl-dense,$np,$i,$setting,$r,$((SECONDS - t0)),$it,$STRETTO_COMMIT" | tee -a "$OUT/stress.csv"
                done
            done
        done
    done
    rm -rf "$w"
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; parasails) step_parasails;; predict) step_predict;; scan) step_scan;;
        stress) step_stress;; ninja) step_ninja;;
        all) step_fetch && step_build && step_parasails && step_predict && step_stress;;
        *) echo "usage: $0 [fetch|build|parasails|predict|stress|ninja|scan|all]" >&2; exit 2;;
    esac || exit 1
done
