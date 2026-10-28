#!/usr/bin/env bash
# A documented failure in stempy 3.3.5, from the project's history (issue #292, PR #293).
#
# stempy (4D-STEM electron counting) gathers the electron events of its MPI workers on rank 0
# in gatherEvents (stempy/electron_mpi.cpp). Each worker sends its map's size with
# MPI_Send(&size, 1, MPI_UINT64_T, 0, 0) and then the serialized map with MPIX_Send_x(data,
# size, MPI_BYTE, 0, 0) (BigMPI); rank 0, once per worker, receives a size with
# MPI_Recv(&msgSize, 1, MPI_UINT64_T, MPI_ANY_SOURCE, 0), resizes its buffer, receives a map with
# MPIX_Recv_x(buffer, msgSize, MPI_BYTE, MPI_ANY_SOURCE, 0) and deserializes it with cereal. The
# map receive can take another worker's 8-byte size, and cereal throws "Failed to read ... bytes
# from input stream!", which issue #292 reports from production runs "at random times". The code
# came with PR #285 (BigMPI, merged 10 May 2023) and was released in 3.3.5; PR #293 (merged 28 Aug
# 2023, released in 3.3.6) receives both messages from the rank of the loop.
#
# The driver, experiments/stempy/harness.cpp, runs stempy's own code: serializeEvents,
# receivePartialMap, sendPartialMap and gatherEvents, cut verbatim from each tag's
# electron_mpi.cpp (the lines from "void serializeEvents" to "void gatherBlocks"), compiled
# with cereal at the tags' submodule commit, the Empirical headers vendored at the tag and
# BigMPI. Every worker holds synthetic events for 256 scan positions (maps of different sizes
# per worker), and worker r reaches the gather r*5 ms after worker 1, as the workers of a
# counting run finish at different times; rank 0 checks the events per position and prints
# "gather ok". BigMPI's own build runs autotools, which need libtool (not in the image), so its
# three sources the gather needs (sendrecv_x, type_contiguous_x, utils) are compiled directly
# with the configuration header's defaults (every option off).
#
# Steps, inside the experiment container, from the repository root, Open MPI build:
#   fetch    download stempy 3.3.5 and 3.3.6, cereal at the submodule commit of both tags and
#            BigMPI at a pinned commit, check every archive's sha256
#   build    BigMPI's three sources, then the harness for each version
#   predict  RUNS traced runs of 3.3.5 at 4 processes, analysed with the single-path explorer,
#            and RUNS at 8, analysed at L1 (the explorer is skipped there, see
#            experiments/RUNBOOK.md); the findings of an error class replayed by stretto confirm,
#            every cross alternative (experiments/stempy/classify.py: a receive taking a size
#            where it took a map, or the reverse) replayed with its horizon pins and its control,
#            JOBS traced runs at a time (experiments/history_predict.py)
#   stress   STRESS_RUNS untraced runs of 3.3.5 per process count, plain and with random send
#            and receive delays from the replay library, TIMEOUT seconds each
#            (experiments/history_stress.py)
#   fixed    predict and stress on 3.3.6
#   ninja    NINJA (experiments/ninja/build.sh, Open MPI library) on 3.3.5: NINJA_RUNS runs per
#            process count in its random (10% of the sends by 50 ms), system- and
#            application-centric modes, TIMEOUT seconds each, the harness's verdict recorded as
#            the mark "ok" (experiments/ninja/run.py)
#   ninja-fixed  the same on 3.3.6
#   all      fetch build predict stress fixed
# Writes experiments/results/stempy/{predict-<v>-np4,predict-<v>-np8,stress-<v>}.csv for v in
# 3.3.5 and 3.3.6, and experiments/results/ninja/stempy[-fix].csv.
# usage: experiments/stempy/run.sh [fetch|build|predict|stress|fixed|ninja|ninja-fixed|all]
#        RUNS (15), STRESS_RUNS (40), NINJA_RUNS (40), TIMEOUT (30), JOBS (3), STEMPY_DIR
#        (third_party/apps/stempy), MPI_CC and MPI_CXX (the build's MPI wrappers, default Open
#        MPI's: experiments/must/apps.sh builds with MPICH's)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${STEMPY_DIR:-$R/third_party/apps/stempy}"
OUT="$R/experiments/results/stempy"; mkdir -p "$OUT"
RUNS="${RUNS:-15}"; STRESS_RUNS="${STRESS_RUNS:-40}"; TIMEOUT="${TIMEOUT:-30}"
MPI_CC="${MPI_CC:-/usr/bin/mpicc.openmpi}"; MPI_CXX="${MPI_CXX:-/usr/bin/mpicxx.openmpi}"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
# the container refuses process_vm_readv, the single-copy mechanism of Open MPI's shared-memory
# transport, which then prints a line per refused transfer; without it the transport copies
# through its shared buffers (mpirun passes OMPI_MCA_* settings on to the ranks)
export OMPI_MCA_btl_vader_single_copy_mechanism=none
CEREAL=02eace19a99ce3cd564ca4e379753d69af08c2c8     # thirdparty/cereal at 3.3.5 and at 3.3.6 (git ls-tree)
BIGMPI=5300b18cc8ec1b2431bf269ee494054ee7bd9f72
declare -A SHA=([stempy-3.3.5.tar.gz]=e22985f01a99d8470c28b29eb3d068c2e766073f134677baa3dea7cc3f88f791
                [stempy-3.3.6.tar.gz]=c10e1166fe776bdf6513a4cc47866a656c49d18e80c59d8333ad70f2924a8eb7
                [cereal-${CEREAL:0:8}.tar.gz]=68216ae99c206e564145dcf0edb9b0689118d8dc31f77ad3ba5bc2c918e01f5b
                [BigMPI-${BIGMPI:0:8}.tar.gz]=4a4452a737213aa1e26eb6060277ea566a8a5dabcedc8754cd72087aed3ecd48)
declare -A URL=([stempy-3.3.5.tar.gz]=https://github.com/OpenChemistry/stempy/archive/refs/tags/3.3.5.tar.gz
                [stempy-3.3.6.tar.gz]=https://github.com/OpenChemistry/stempy/archive/refs/tags/3.3.6.tar.gz
                [cereal-${CEREAL:0:8}.tar.gz]=https://github.com/USCiLab/cereal/archive/$CEREAL.tar.gz
                [BigMPI-${BIGMPI:0:8}.tar.gz]=https://github.com/jeffhammond/BigMPI/archive/$BIGMPI.tar.gz)
ARGS=(256 12 5)      # scan positions, most events per position, the workers' stagger in ms
OK='gather ok'
exe() { echo "$D/harness-$1"; }

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    local f
    for f in "${!URL[@]}"; do fetch_checked "$f" "${URL[$f]}" "${SHA[$f]}" || return 1; done
    for f in stempy-3.3.5 stempy-3.3.6; do [ -d "$f" ] || tar xzf "$f.tar.gz" || return 1; done
    [ -d "cereal-$CEREAL" ] || tar xzf "cereal-${CEREAL:0:8}.tar.gz" || return 1
    [ -d "BigMPI-$BIGMPI" ] || tar xzf "BigMPI-${BIGMPI:0:8}.tar.gz" || return 1
    cd "$R"
}
step_build() {
    local b="$D/bigmpi" v
    if [ ! -f "$b/lib/libbigmpi.a" ]; then
        mkdir -p "$b/include" "$b/lib" "$b/obj" || return 1
        sed -e 's/^#undef \(HAVE_[A-Z_]*_H\)$/#define \1 1/' "$D/BigMPI-$BIGMPI/src/bigmpiconf.h.in" > "$b/obj/bigmpiconf.h"
        for f in sendrecv_x type_contiguous_x utils; do
            "$MPI_CC" -O2 -g -I"$b/obj" -I"$D/BigMPI-$BIGMPI/src" -c "$D/BigMPI-$BIGMPI/src/$f.c" -o "$b/obj/$f.o" \
                || { echo "build of BigMPI's $f.c failed" >&2; return 1; }
        done
        ar rcs "$b/lib/libbigmpi.a" "$b"/obj/*.o && cp "$D/BigMPI-$BIGMPI/src/bigmpi.h" "$b/include/" || return 1
    fi
    for v in 3.3.5 3.3.6; do
        local s="$D/stempy-$v"
        awk '/^void serializeEvents/{on=1} /^void gatherBlocks/{on=0} on' "$s/stempy/electron_mpi.cpp" > "$D/gather-$v.inc"
        grep -q '^void gatherEvents' "$D/gather-$v.inc" || { echo "gatherEvents not found in $v's electron_mpi.cpp" >&2; return 1; }
        mkdir -p "$D/include-$v" && cp "$D/gather-$v.inc" "$D/include-$v/gather.inc" || return 1
        "$MPI_CXX" -O2 -g -std=c++17 -I"$D/include-$v" -I"$s/stempy" -I"$s/thirdparty/Empirical/include" \
            -I"$D/cereal-$CEREAL/include" -I"$b/include" experiments/stempy/harness.cpp -o "$(exe $v)" -L"$b/lib" -lbigmpi \
            || { echo "build of the harness for $v failed" >&2; return 1; }
    done
}
predict() {   # predict <version>
    need_file "$(exe "$1")" || return 1
    local p=(--version "$1" --runs "$RUNS" --timeout 20 --jobs "${JOBS:-3}" --confirm --classify experiments/stempy/classify.py
             --replay cross --ok "$OK")
    python3 experiments/history_predict.py "${p[@]}" --np 4 --out "$OUT/predict-$1-np4.csv" -- "$(exe "$1")" "${ARGS[@]}" \
        && python3 experiments/history_predict.py "${p[@]}" --np 8 --no-sp --out "$OUT/predict-$1-np8.csv" -- "$(exe "$1")" "${ARGS[@]}"
}
stress() {    # stress <version>
    need_file "$(exe "$1")" || return 1
    rm -f "$OUT/stress-$1.csv"
    python3 experiments/history_stress.py --version "$1" --label gatherEvents --np 4 8 --runs "$STRESS_RUNS" --timeout "$TIMEOUT" \
        --jobs "${JOBS:-3}" --ok "$OK" --out "$OUT/stress-$1.csv" -- "$(exe "$1")" "${ARGS[@]}"
}
ninja() {     # ninja <version> <table>
    need_file "$(exe "$1")" || return 1
    local t="$R/experiments/results/ninja/$2.csv" np m
    mkdir -p "$(dirname "$t")" && rm -f "$t"
    for np in 4 8; do
        for m in random system app; do
            local extra=(); [ $m = random ] && extra=(--rand-ratio 10 --rand-delay 50000)
            python3 experiments/ninja/run.py runs --np $np --mode $m "${extra[@]}" --runs "${NINJA_RUNS:-40}" --timeout "$TIMEOUT" \
                --mpi openmpi --label "gatherEvents-$1" --out "$t" --marks "ok=$OK" --value "(Failed to read)" --keep "$R/experiments/results/ninja/logs" \
                -- "$(exe "$1")" "${ARGS[@]}"; [ $? != 3 ] || return 1
        done
    done
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) predict 3.3.5;; stress) stress 3.3.5;;
        fixed) predict 3.3.6 && stress 3.3.6;;
        ninja) ninja 3.3.5 stempy;; ninja-fixed) ninja 3.3.6 stempy-fix;;
        all) step_fetch && step_build && predict 3.3.5 && stress 3.3.5 && predict 3.3.6 && stress 3.3.6;;
        *) echo "usage: $0 [fetch|build|predict|stress|fixed|ninja|ninja-fixed|all]" >&2; exit 2;;
    esac || exit 1
done
