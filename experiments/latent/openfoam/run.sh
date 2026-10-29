#!/usr/bin/env bash
# A latent race in OpenFOAM's sparse exchange (ESI develop 68a81857c, 18 Oct 2026, API 2609),
# reached only through a driver over the public API (docs/findings.md, latent bugs).
#
# Pstream::exchange on Map containers sends its sizes through Pstream::exchangeSizes(Map), which
# by default runs UPstream::allToAllConsensus (src/Pstream/mpi/UPstreamWrapping.txx, the Map
# variant near line 887): NBX with the constant tag msgType() + 1867 = 1868: Issend to each
# target, then MPI_Iprobe(MPI_ANY_SOURCE, 1868) and MPI_Recv until the sends are done,
# MPI_Ibarrier, and probing until the barrier completes. Nothing orders two calls in a row: a
# rank whose barrier completed sends the next call's sizes while another, still in its loop,
# probes and takes them for the old call, and the exchange then delivers wrong data or none. The
# routine's initial MPI_Barrier runs only with nbx.tuning > 0 (default 0). Its only in-tree caller
# with Map is applications/test/parallel-chunks (with nbx.min=2), which checks nothing, and
# snappyHexMesh's exchanges (with pbufs.tuning=1) have no alternative across calls in the traced
# tutorials. The 2024 issue 3238 was the same class at call sites (fixed there by unique tags).
#
# The driver, nbxExchange (nbxExchange/nbxExchange.C, built with wmake): CALLS calls of
# Pstream::exchange<labelList, label>(Map, Map) with every default, patterns ring (rank r sends
# to r + 1), all (to every other rank) and scatter (rank 0 to all), each call's data checked;
# the master prints "nbxExchange: N calls on P ranks, pattern X, W wrong answers", ": ok" when W
# is 0.
#
# Image: experiments/latent/openfoam/Dockerfile (stretto-foam:24.04, the experiment image with
# flex and bison). Steps, inside it, from the repository root, Open MPI:
#   fetch      OpenFOAM at 68a81857c (git checks the commit)
#   build      wmake, OSspecific, Pstream (system Open MPI) and libOpenFOAM (Gcc, Opt, 32-bit
#              labels, -j$MAKE_JOBS), the driver and the shipped Test-parallel-chunks
#   predict    RUNS traced runs of nbxExchange per pattern (ring, scatter, all) at 4 and 8
#              processes, analysed at L1, every cross alternative at tag 1868
#              (experiments/latent/openfoam/classify.py: a probe of one call that would see a
#              send of another; calls are numbered by the rank's completed Ibarriers) replayed with
#              its horizon pins and its control, and the error-class findings by stretto confirm
#              (experiments/history_predict.py)
#   tuning     the same for ring and scatter with -opt-switch nbx.tuning=1 (the existing barrier)
#   stress     STRESS_RUNS untraced runs per pattern and process count, plain and with random
#              delays, of ring and scatter, with and without nbx.tuning=1 (experiments/history_stress.py)
#   chunks     CHUNK_RUNS traced runs of the unmodified Test-parallel-chunks with -opt-switch
#              nbx.min=2 at 4 and 8, cross alternatives replayed (the test prints "End" whatever
#              it received, so the replays show the prediction only)
#   shm-build  the libraries and applications snappyHexMesh needs (several hours at -j8; not in all)
#   shm        SHM_RUNS traced runs of the unmodified snappyHexMesh with -opt-switch pbufs.tuning=1
#              (the setting that makes PstreamBuffers use NBX) on three shipped tutorials, each run
#              in a fresh copy of the prepared case, analysed at L1 (no replay: none is cross):
#              insidePoints (6 ranks), gap_detection (8), distributedTriSurfaceMesh (8, its surface
#              refined twice instead of ten times, the alternative its dictionary names)
#   summary    the compact tables experiments/results/latent/openfoam-{predict,stress}.csv
#   all        fetch build predict tuning stress chunks summary
# The per-run tables go to experiments/results/latent/openfoam/ (not committed).
# usage: experiments/latent/openfoam/run.sh [fetch|build|predict|tuning|stress|chunks|shm-build|shm|summary|all]
#        RUNS (10), STRESS_RUNS (40), CHUNK_RUNS (5), SHM_RUNS (3), CALLS (3), TIMEOUT (10),
#        JOBS (1), MAKE_JOBS (8), FOAM_DIR (third_party/apps/latent-openfoam)
set -uo pipefail
R="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$R"
H="$R/experiments/latent/openfoam"
D="${FOAM_DIR:-$R/third_party/apps/latent-openfoam}"
OUT="$R/experiments/results/latent/openfoam"; mkdir -p "$OUT"
RUNS="${RUNS:-10}"; STRESS_RUNS="${STRESS_RUNS:-40}"; CHUNK_RUNS="${CHUNK_RUNS:-5}"; SHM_RUNS="${SHM_RUNS:-3}"
CALLS="${CALLS:-3}"; TIMEOUT="${TIMEOUT:-10}"; JOBS="${JOBS:-1}"; MAKE_JOBS="${MAKE_JOBS:-8}"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
export OMPI_MCA_btl_vader_single_copy_mechanism=none
export NBX_TAGS=1868
FOAM=68a81857c3af06fceb9f0ea2001446df34962d01
SRC="$D/openfoam"
OK='nbxExchange: [0-9]+ calls on [0-9]+ ranks, pattern [a-z]+, 0 wrong answers: ok'
NBX="$D/bin/nbxExchange"; CHUNKS="$D/bin/Test-parallel-chunks"

foam_env() {
    set +u
    # shellcheck disable=SC1091
    source "$SRC/etc/bashrc" WM_MPLIB=SYSTEMOPENMPI WM_COMPILER=Gcc WM_COMPILE_OPTION=Opt WM_LABEL_SIZE=32 \
        WM_NCOMPPROCS="$MAKE_JOBS" > /dev/null 2>&1
    set -u
    export FOAM_USER_APPBIN="$D/bin"; mkdir -p "$D/bin"
}
step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    if [ ! -d openfoam ]; then
        rm -rf openfoam.part && git init -q openfoam.part \
            && git -C openfoam.part fetch -q --depth 1 https://gitlab.com/openfoam/core/openfoam.git "$FOAM" \
            && git -C openfoam.part checkout -q FETCH_HEAD && [ "$(git -C openfoam.part rev-parse HEAD)" = "$FOAM" ] \
            && mv openfoam.part openfoam || { harness_error "cannot fetch OpenFOAM $FOAM"; return 1; }
    fi
    cd "$R"
}
step_build() {
    foam_env
    (cd "$SRC/wmake/src" && make -j"$MAKE_JOBS" > "$D/build-wmake.log" 2>&1) || { echo "build of wmake failed" >&2; return 1; }
    (cd "$SRC/src" && wmakeLnInclude -u OpenFOAM && wmakeLnInclude -u OSspecific/POSIX \
         && OSspecific/POSIX/Allwmake -j"$MAKE_JOBS" && Pstream/Allwmake -j"$MAKE_JOBS" && OpenFOAM/Alltouch -check \
         && wmake -j"$MAKE_JOBS" libso OpenFOAM) > "$D/build-lib.log" 2>&1 \
        || { echo "build of libOpenFOAM failed ($D/build-lib.log)" >&2; return 1; }
    # wmake builds in the application's directory: copies of the driver and of the shipped test
    rm -rf "$D/nbxExchange" "$D/parallel-chunks"
    cp -a "$H/nbxExchange" "$D/nbxExchange" && cp -a "$SRC/applications/test/parallel-chunks" "$D/parallel-chunks" || return 1
    (cd "$D/nbxExchange" && wmake -j"$MAKE_JOBS") > "$D/build-driver.log" 2>&1 \
        && (cd "$D/parallel-chunks" && wmake -j"$MAKE_JOBS") >> "$D/build-driver.log" 2>&1 \
        || { echo "build of the driver or the test failed ($D/build-driver.log)" >&2; return 1; }
    need_file "$NBX" "$CHUNKS"
}
p1() {        # p1 <version> <np list> <runs> <ok regex> <exe> <args...>
    local ver=$1 nps=$2 runs=$3 ok=$4; shift 4
    rm -f "$OUT/predict-$ver.csv"
    # shellcheck disable=SC2086
    python3 experiments/history_predict.py --version "$ver" --np $nps --runs "$runs" --timeout "$TIMEOUT" --jobs "$JOBS" \
        --no-sp --confirm --classify "$H/classify.py" --replay cross --ok "$ok" --out "$OUT/predict-$ver.csv" -- "$@"
}
step_predict() {
    foam_env; need_file "$NBX" || return 1
    local p
    for p in ring scatter all; do p1 $p "4 8" "$RUNS" "$OK" "$NBX" -parallel -calls "$CALLS" -pattern $p || return 1; done
}
step_tuning() {
    foam_env; need_file "$NBX" || return 1
    local p
    for p in ring scatter; do
        p1 $p-tuning1 "4 8" "$RUNS" "$OK" "$NBX" -parallel -calls "$CALLS" -pattern $p -opt-switch nbx.tuning=1 || return 1
    done
}
step_stress() {
    foam_env; need_file "$NBX" || return 1
    local p t
    for p in ring scatter; do
        for t in "" -tuning1; do
            local sw=(); [ -n "$t" ] && sw=(-opt-switch nbx.tuning=1)
            rm -f "$OUT/stress-$p$t.csv"
            python3 experiments/history_stress.py --version "$p$t" --label "nbxExchange -calls $CALLS -pattern $p${sw[*]:+ ${sw[*]}}" \
                --np 4 8 --runs "$STRESS_RUNS" --timeout 30 --jobs "$JOBS" --ok "$OK" --out "$OUT/stress-$p$t.csv" \
                -- "$NBX" -parallel -calls "$CALLS" -pattern $p "${sw[@]}" || return 1
        done
    done
}
step_chunks() {
    foam_env; need_file "$CHUNKS" || return 1
    p1 nbxmin2 "4 8" "$CHUNK_RUNS" '^End' "$CHUNKS" -parallel -opt-switch nbx.min=2
}
SHM_LIBS="fileFormats surfMesh meshTools finiteArea finiteVolume mesh/blockMesh mesh/extrudeModel dynamicMesh
          genericPatchFields parallel/decompose/decompositionMethods dummyThirdParty lagrangian/basic dynamicFvMesh
          sampling parallel overset ODE transportModels thermophysicalModels TurbulenceModels fvOptions fvMotionSolver
          mesh/snappyHexMesh"
SHM_APPS="mesh/generation/blockMesh mesh/generation/snappyHexMesh parallelProcessing/decomposePar
          miscellaneous/foamDictionary surface/surfaceRefineRedGreen surface/surfaceTransformPoints surface/surfaceOrient
          mesh/manipulation/checkMesh"
step_shm_build() {
    foam_env
    local l a
    (cd "$SRC/src" && parallel/decompose/AllwmakeLnInclude && wmakeLnInclude -u fvOptions && wmakeLnInclude -u overset \
         && wmakeLnInclude -u regionFaModels && wmakeLnInclude -u faOptions) > "$D/build-shm.log" 2>&1 || return 1
    for l in $SHM_LIBS; do
        if [ -x "$SRC/src/$l/Allwmake" ]; then (cd "$SRC/src/$l" && ./Allwmake -j"$MAKE_JOBS")
        else (cd "$SRC/src" && wmake -j"$MAKE_JOBS" libso "$l"); fi >> "$D/build-shm.log" 2>&1 \
            || { echo "build of $l failed ($D/build-shm.log)" >&2; return 1; }
    done
    for a in $SHM_APPS; do
        (cd "$SRC/applications/utilities/$a" && wmake -j"$MAKE_JOBS") >> "$D/build-shm.log" 2>&1 \
            || { echo "build of $a failed ($D/build-shm.log)" >&2; return 1; }
    done
}
shm_case() {  # shm_case <tutorial> <dest>: the case up to decomposePar, as its Allrun prepares it
    local c=$1 d=$2 t="$SRC/tutorials/mesh/snappyHexMesh/$1" tri
    rm -rf "$d" && mkdir -p "$(dirname "$d")" && cp -r "$t" "$d" && cd "$d" || return 1
    if [ "$c" = distributedTriSurfaceMesh ]; then
        tri=constant/triSurface; mkdir -p $tri && cp -f "$SRC/tutorials/resources/geometry/box_12.obj.gz" $tri \
            && blockMesh > log.blockMesh 2>&1 \
            && surfaceRefineRedGreen $tri/box_12.obj $tri/box_12_1.obj > log.r1 2>&1 \
            && surfaceRefineRedGreen $tri/box_12_1.obj $tri/box.obj > log.r2 2>&1 \
            && surfaceTransformPoints -rotate '((1 0 0)(1 0.8 0.9))' -translate '(0.1 0.101 0.103)' \
                   $tri/box.obj $tri/box_trans.obj > log.t1 2>&1 \
            && surfaceOrient -inside $tri/box.obj '(100 100 100)' $tri/box_flipped.obj > log.o 2>&1 \
            && surfaceTransformPoints -translate '(0.2 0.3 0.3)' -write-scale '(0.5)' \
                   $tri/box_flipped.obj $tri/box_scaled.obj > log.t2 2>&1 || return 1
    else
        blockMesh > log.blockMesh 2>&1 || return 1
    fi
    decomposePar > log.decomposePar 2>&1; local rc=$?; cd "$R"; return $rc
}
step_shm() {
    foam_env
    local shm="$FOAM_APPBIN/snappyHexMesh" s c np ow v
    need_file "$shm" || return 1
    for s in insidePoints:6:-overwrite:insidePoints-pbufs1 gap_detection:8:-overwrite:gap_detection-pbufs1 \
             distributedTriSurfaceMesh:8::dist-pbufs1; do
        IFS=: read -r c np ow v <<< "$s"
        shm_case "$c" "$D/shm/$c" || { echo "preparing $c failed" >&2; return 1; }
        rm -f "$OUT/predict-$v.csv"
        # shellcheck disable=SC2086
        NBX_TAGS= python3 experiments/history_predict.py --version "$v" --np "$np" --runs "$SHM_RUNS" --timeout 120 \
            --trace-timeout 900 --jobs "$JOBS" --no-sp --classify "$H/classify.py" --replay cross --base "$D/shm/$c" \
            --ok "Finished meshing without any errors" --out "$OUT/predict-$v.csv" \
            -- "$shm" -parallel $ow -opt-switch pbufs.tuning=1 || return 1
    done
}
step_summary() {
    local s="$R/experiments/results/latent" f
    rm -f "$s/openfoam-predict.csv" "$s/openfoam-stress.csv"
    for f in ring scatter all ring-tuning1 scatter-tuning1 nbxmin2 insidePoints-pbufs1 gap_detection-pbufs1 dist-pbufs1; do
        [ -f "$OUT/predict-$f.csv" ] || continue
        python3 -I experiments/latent/summarize.py predict --case openfoam --source 68a81857c --out "$s/openfoam-predict.csv" \
            "$OUT/predict-$f.csv" || return 1
    done
    python3 -I experiments/latent/summarize.py stress --case openfoam --source 68a81857c --out "$s/openfoam-stress.csv" \
        "$OUT"/stress-*.csv
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) step_predict;; tuning) step_tuning;; stress) step_stress;;
        chunks) step_chunks;; shm-build) step_shm_build;; shm) step_shm;; summary) step_summary;;
        all) step_fetch && step_build && step_predict && step_tuning && step_stress && step_chunks && step_summary;;
        *) echo "usage: $0 [fetch|build|predict|tuning|stress|chunks|shm-build|shm|summary|all]" >&2; exit 2;;
    esac || exit 1
done
