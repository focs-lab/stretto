#!/usr/bin/env bash
# A ParaSails race in the current HYPRE release (3.2.0, and master at 9f52d27 on 22 Oct 2026,
# whose ParaSails.c is the same file), found from clean traced runs.
#
# ExchangePrunedRows (src/distributed_ls/ParaSails/ParaSails.c) builds the sparsity pattern of
# the approximate inverse one level at a time. Each level Isends its row requests with tag
# ROW_PRUNED_REQ_TAG (221), counts the requests each rank will get with the Allreduce of
# FindNumReplies, takes that many with MPI_Probe(MPI_ANY_SOURCE, 221) and MPI_Recv from the
# probed source, answers each at once, then receives its own replies. All levels use the one
# tag, and a rank that has its replies starts the next level, whose requests it sends before
# that level's Allreduce, while a neighbour may still be probing for requests of the level
# before. The neighbour's probe can take the request of the next level; the request it was
# waiting for stays queued, its sender waits for a reply that comes only after the next
# Allreduce, which that sender never reaches: the setup hangs. HYPRE 2.11.0 gave the pruned
# and the stored exchange different tags (the race of experiments/hypre/), not one level and
# the next. The race needs nlevels >= 2 (HYPRE_ParaSailsSetParams, HYPRE_BoomerAMGSetLevel
# for the ParaSails smoother); the default is 1. hypre's own drivers fix it at 1 (ex5.c
# hard-codes 1, test/ij.c sets max_levels = 1 for its ParaSails solvers), so the driver is
# ex5.c with one option added, -sai_levels, which sets ParaSails' nlevels and nothing else;
# PETSc's KSP tutorial reaches the same code unmodified (experiments/petsc/).
#
# VARIANT=fix builds, in a copy of the tree, ParaSails.c with the requests of even levels sent
# under a tag of their own (227, unused in ParaSails), which is enough: a request of level
# L+2 is sent after the Allreduce of level L+1, which no rank enters before it has taken all
# its requests of level L. Its tables get the suffix -fix.
#
# Steps, inside the experiment container, from the repository root, Open MPI build:
#   fetch    download HYPRE 3.2.0 from GitHub, check its sha256
#   build    configure (Open MPI wrappers, -O2 -g, no Fortran) and make -j$MAKE_JOBS; then the
#            driver ex5_levels from src/examples/ex5.c
#   predict  RUNS traced runs per process count (4, 8) and level count (1, 2), each analysed,
#            every probe alternative replayed with its horizon pins and its control, JOBS at a
#            time (experiments/hypre3/predict.py)
#   stress   STRESS_RUNS untraced runs per process count and level count, plain and with
#            random send and receive delays from the replay library (the budget and delays of
#            experiments/hypre/campaign.sh), TIMEOUT seconds each
#   ninja    NINJA (experiments/ninja/build.sh, Open MPI library) on the driver at two levels:
#            STRESS_RUNS runs per process count in its random (50 ms), system- and
#            application-centric modes (experiments/ninja/run.py)
#   all      fetch build predict stress
# Writes experiments/results/hypre3/{predict,stress,ninja}[-fix].csv.
# usage: experiments/hypre3/run.sh [fetch|build|predict|stress|ninja|all]
#        RUNS (10), STRESS_RUNS (40), TIMEOUT (30), JOBS (1), MAKE_JOBS (12), VARIANT (release),
#        HYPRE3_DIR (third_party/apps/hypre3), NINJA_DIR (third_party/ninja)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${HYPRE3_DIR:-$R/third_party/apps/hypre3}"
OUT="$R/experiments/results/hypre3"; mkdir -p "$OUT"
RUNS="${RUNS:-10}"; STRESS_RUNS="${STRESS_RUNS:-40}"; TIMEOUT="${TIMEOUT:-30}"
PINLIB="$R/replay/bin/openmpi/libstretto-pin.so"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || git -C "$R" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
V=3.2.0
SHA=5273205a310fb6aa3ae506ce216760fb67b30e02024874f3cdb8b811e4801de7
case "${VARIANT:-release}" in
    release) TREE="$D/hypre-$V"; SUF=;;
    fix) TREE="$D/hypre-$V-fix"; SUF=-fix;;
    *) echo "VARIANT is release or fix" >&2; exit 2;;
esac
SRC="$TREE/src"
DRIVER="$SRC/examples/ex5_levels"

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    [ -f "hypre-$V.tar.gz" ] || curl -sSfL -o "hypre-$V.tar.gz" "https://github.com/hypre-space/hypre/archive/refs/tags/v$V.tar.gz" || return 1
    echo "$SHA  hypre-$V.tar.gz" | sha256sum -c - || return 1
    [ -d "hypre-$V" ] || tar xzf "hypre-$V.tar.gz"
    if [ "${VARIANT:-release}" = fix ] && [ ! -d "$TREE" ]; then
        mkdir -p "$TREE.tmp" && tar xzf "hypre-$V.tar.gz" -C "$TREE.tmp" && mv "$TREE.tmp/hypre-$V" "$TREE" && rmdir "$TREE.tmp" || return 1
        local p="$SRC/distributed_ls/ParaSails/ParaSails.c"
        sed -i -e 's/^#define ROW_PRUNED_REQ_TAG        221$/#define ROW_PRUNED_REQ_TAG        221\n#define ROW_PRUNED_REQ_TAG_EVEN   227\n#define PRUNED_REQ_TAG(level) ((level) % 2 ? ROW_PRUNED_REQ_TAG : ROW_PRUNED_REQ_TAG_EVEN)/' \
               -e 's/SendRequests(comm, ROW_PRUNED_REQ_TAG,/SendRequests(comm, PRUNED_REQ_TAG(level),/' \
               -e 's/ReceiveRequest(comm, &source, ROW_PRUNED_REQ_TAG,/ReceiveRequest(comm, \&source, PRUNED_REQ_TAG(level),/' "$p"
        # the three level loops (ExchangePrunedRows and its two Ext variants) send and receive with it
        [ "$(grep -c 'PRUNED_REQ_TAG(level),' "$p")" = 6 ] || { echo "the ParaSails.c patch did not apply" >&2; return 1; }
    fi
    cd "$R"
}
step_build() {
    if [ ! -f "$SRC/hypre/lib/libHYPRE.a" ]; then
        (cd "$SRC" && ./configure CC=/usr/bin/mpicc.openmpi CXX=/usr/bin/mpicxx.openmpi CFLAGS="-O2 -g" CXXFLAGS="-O2 -g" \
             --disable-fortran --with-MPI > "$TREE/configure.log" 2>&1 && make -j"${MAKE_JOBS:-12}" > "$TREE/make.log" 2>&1) \
            || { echo "build of HYPRE $V ($TREE) failed" >&2; return 1; }
    fi
    # ex5.c with -sai_levels <n>: the ParaSails solver's nlevels, hard-coded to 1 in ex5.c
    local ex="$SRC/examples"
    sed -e 's/^   int vis, print_system;/   int vis, print_system;\n   int sai_levels = 1;/' \
        -e 's/^         else if ( strcmp(argv\[arg_index\], "-vis") == 0 )/         else if ( strcmp(argv[arg_index], "-sai_levels") == 0 )\n         {\n            arg_index++;\n            sai_levels = atoi(argv[arg_index++]);\n         }\n         else if ( strcmp(argv[arg_index], "-vis") == 0 )/' \
        -e 's/int      sai_max_levels = 1;/int      sai_max_levels = sai_levels;/' "$ex/ex5.c" > "$ex/ex5_levels.c"
    [ "$(diff "$ex/ex5.c" "$ex/ex5_levels.c" | grep -c '^>')" = 7 ] || { echo "the ex5.c patch did not apply" >&2; return 1; }
    (cd "$ex" && /usr/bin/mpicc.openmpi -O2 -g -I../hypre/include -o ex5_levels ex5_levels.c -L../hypre/lib -lHYPRE -lm) \
        || { echo "build of ex5_levels failed" >&2; return 1; }
}
step_predict() {
    python3 experiments/hypre3/predict.py "$DRIVER" --version "$V$SUF" --runs "$RUNS" --np 4 8 --levels 1 2 \
        --jobs "${JOBS:-1}" --out "$OUT/predict$SUF.csv"
}
step_stress() {
    echo "version,np,levels,run,setting,result,seconds,residual,commit" > "$OUT/stress$SUF.csv"
    local w; w=$(mktemp -d)
    for levels in 2 1; do
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
                    (cd "$w" && /usr/bin/timeout -k 5 "$TIMEOUT" mpirun.openmpi --oversubscribe -np $np "${env[@]}" "$DRIVER" \
                         -solver 8 -sai_levels $levels -n 33 < /dev/null > "$w/out" 2>&1)
                    rc=$?; r=FINISHED; [ $rc = 0 ] || { [ $rc = 124 ] && r=TIMEOUT || r="rc=$rc"; }
                    res=$(sed -n 's/.*Final Relative Residual Norm = *\([^ ]*\).*/\1/p' "$w/out" | head -1)
                    echo "$V$SUF,$np,$levels,$i,$setting,$r,$((SECONDS - t0)),$res,$STRETTO_COMMIT" | tee -a "$OUT/stress$SUF.csv"
                done
            done
        done
    done
    rm -rf "$w"
}
step_ninja() {
    local np m
    rm -f "$OUT/ninja$SUF.csv"
    for np in 4 8; do
        for m in random system app; do
            local extra=(); [ $m = random ] && extra=(--rand-ratio 10 --rand-delay 50000)
            python3 experiments/ninja/run.py runs --np $np --mode $m "${extra[@]}" --runs "$STRESS_RUNS" --timeout "$TIMEOUT" \
                --mpi openmpi --label "ex5_levels-$V$SUF-2" --out "$OUT/ninja$SUF.csv" \
                --marks "pattern=num_levels,solved=Final Relative Residual Norm" --value "Final Relative Residual Norm = (\S+)" \
                -- "$DRIVER" -solver 8 -sai_levels 2 -n 33 || return 1
        done
    done
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) step_predict;; stress) step_stress;; ninja) step_ninja;;
        all) step_fetch && step_build && step_predict && step_stress;;
        *) echo "usage: $0 [fetch|build|predict|stress|ninja|all]" >&2; exit 2;;
    esac || exit 1
done
