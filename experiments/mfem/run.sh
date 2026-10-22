#!/usr/bin/env bash
# MFEM 4.10, the current release, scouted for wildcard matching bugs.
#
# MFEM's parallel non-conforming meshes (mesh/pncmesh.cpp) exchange refinements, derefinements,
# ghost layers and rebalanced elements as VarMessage objects (general/communication.hpp): each
# message type has its own tag, the sender Isends one message to each neighbour, and the
# receiver calls MPI_Probe(MPI_ANY_SOURCE, tag) once per expected message and receives from the
# probed source (RecvAll notes "no guard against receiving two messages from the same rank").
# Two calls of one exchange with nothing collective between them would let a probe take the
# next call's message, as in deal.II 9.3.0-rc1 (experiments/dealii/). The drivers are MFEM's
# own adaptive examples: ex6p (static adaptive refinement, with and without rebalancing, and
# with p-refinement) and ex15p (dynamic refinement, derefinement and rebalancing). ex6p
# rebalances by default (-reb is the default, -no-reb turns it off; with -pref it does not).
# MFEM's one other wildcard site is the conforming local refinement of triangles,
# ParMesh::LocalRefinement (mesh/pmesh.cpp), which sends each neighbour the splitting of the
# shared edges with tag 0 and receives it from that neighbour with MPI_ANY_TAG, once per round
# of green refinement, the rounds separated by an MPI_Allreduce. ex6p refines simplices
# non-conformingly by default (Mesh::EnsureNCMesh(true)); with -cs it keeps a triangle mesh
# conforming, and ex6p on square-disc.mesh then reaches LocalRefinement (ex6p-tri-cs; ex6p-tri is
# the same mesh refined non-conformingly).
#
# Steps, inside the experiment container, from the repository root, Open MPI build:
#   fetch    download MFEM 4.10 (GitHub), METIS 4.0.3 (the copy in MFEM's tpls repository on
#            GitHub) and HYPRE 3.2.0 (GitHub), check their sha256
#   build    METIS, HYPRE (unless HYPRE_BUILD names a built src/hypre), MFEM's parallel library
#            (-O2 -g) and the examples ex6p and ex15p, -j$MAKE_JOBS
#   predict  RUNS traced runs of each driver below, analysed, every alternative replayed with
#            its horizon pins and its control (experiments/scout.py), JOBS at a time, except
#            that of the alternatives within one exchange (kind "same", of no error class) a
#            sample of SAME_SAMPLE per driver is replayed ("all" replays every one); PAR
#            drivers at a time
#   all
# Writes experiments/results/mfem/predict.csv.
# usage: experiments/mfem/run.sh [fetch|build|predict|all]
#        RUNS (5), JOBS (1), SAME_SAMPLE (20), PAR (1), MAKE_JOBS (12),
#        MFEM_SRC_DIR (third_party/apps/mfem), HYPRE_BUILD
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${MFEM_SRC_DIR:-$R/third_party/apps/mfem}"
OUT="$R/experiments/results/mfem"; mkdir -p "$OUT"
RUNS="${RUNS:-5}"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
V=4.10
M="$D/mfem-$V"
HYPRE="${HYPRE_BUILD:-$D/hypre-3.2.0/src/hypre}"
declare -A SHA=([mfem-4.10.tar.gz]=d5aabe991b8b5569aa26e2b5d4b59ac617ee10ebac3d45fd6ff9c74b2c1a47dd
                [metis-4.0.3.tar.gz]=5efa35de80703c1b2c4d0de080fafbcf4e0d363a21149a1ad2f96e0144841a55
                [hypre-3.2.0.tar.gz]=5273205a310fb6aa3ae506ce216760fb67b30e02024874f3cdb8b811e4801de7)
declare -A URL=([mfem-4.10.tar.gz]=https://github.com/mfem/mfem/archive/refs/tags/v4.10.tar.gz
                [metis-4.0.3.tar.gz]=https://github.com/mfem/tpls/raw/gh-pages/metis-4.0.3.tar.gz
                [hypre-3.2.0.tar.gz]=https://github.com/hypre-space/hypre/archive/refs/tags/v3.2.0.tar.gz)
# driver name, process counts, example and its arguments (meshes from MFEM's data/; each run
# and replay runs in a fresh directory, where the examples write their solutions). ex6p on the
# triangle mesh and ex15p run at 4 processes only: at 8 a run has 3 and 5 million records, and
# the validity check of each replay, which analyses the replay's own trace, took the container
# past its 48 GB with two replays at a time
DRIVERS="ex6p|4 8|ex6p -m $M/data/star.mesh -no-vis -md 4000
ex6p-norebalance|4 8|ex6p -m $M/data/star.mesh -no-vis -md 4000 -no-reb
ex6p-quad|4|ex6p -m $M/data/inline-quad.mesh -no-vis -md 4000 -reb
ex6p-pref|4|ex6p -m $M/data/star.mesh -no-vis -md 4000 -pref
ex6p-tri|4|ex6p -m $M/data/square-disc.mesh -no-vis -md 4000
ex6p-tri-cs|4 8|ex6p -m $M/data/square-disc.mesh -no-vis -md 4000 -cs
ex15p|4|ex15p -m $M/data/star-hilbert.mesh -no-vis -tf 0.2
ex15p-hex|4|ex15p -m $M/data/inline-hex.mesh -no-vis -tf 0.1"

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    for f in "${!URL[@]}"; do
        [ -f "$f" ] || curl -sSfL -o "$f" "${URL[$f]}" || return 1
        echo "${SHA[$f]}  $f" | sha256sum -c - || return 1
    done
    [ -d "mfem-$V" ] || tar xzf "mfem-$V.tar.gz"
    [ -d metis-4.0.3 ] || tar xzf metis-4.0.3.tar.gz
    [ -n "${HYPRE_BUILD:-}" ] || [ -d hypre-3.2.0 ] || tar xzf hypre-3.2.0.tar.gz
    cd "$R"
}
step_build() {
    local j="${MAKE_JOBS:-12}"
    [ -f "$D/metis-4.0.3/libmetis.a" ] || (cd "$D/metis-4.0.3" && make -j"$j" CC=/usr/bin/gcc OPTFLAGS="-O2" > "$D/metis.log" 2>&1) \
        || { echo "build of METIS failed" >&2; return 1; }
    if [ ! -f "$HYPRE/lib/libHYPRE.a" ]; then
        (cd "$D/hypre-3.2.0/src" && ./configure CC=/usr/bin/mpicc.openmpi CXX=/usr/bin/mpicxx.openmpi CFLAGS="-O2 -g" \
             --disable-fortran --with-MPI > "$D/hypre-configure.log" 2>&1 && make -j"$j" > "$D/hypre-make.log" 2>&1) \
            || { echo "build of HYPRE failed" >&2; return 1; }
    fi
    local mk=(MPICXX=/usr/bin/mpicxx.openmpi "OPTIM_FLAGS=-O2 -g -std=c++17" HYPRE_DIR="$HYPRE" METIS_DIR="$D/metis-4.0.3")
    (cd "$M" && make parallel -j"$j" "${mk[@]}" > "$D/mfem-make.log" 2>&1 && make -C examples -j"$j" ex6p ex15p "${mk[@]}" \
         > "$D/mfem-examples.log" 2>&1) || { echo "build of MFEM failed" >&2; return 1; }
}
same_sample() { [ "${SAME_SAMPLE:-20}" = all ] || echo "--same-sample ${SAME_SAMPLE:-20}"; }
step_predict() {
    # PAR drivers at a time, each into a table of its own, joined in the drivers' order: the
    # analysis of one adaptive run takes minutes on one processor (160 000 records and 4 000
    # alternatives for ex6p -pref at 4 processes)
    rm -f "$OUT/predict.csv"
    local parts n=0 name nps cmd p
    parts=$(mktemp -d)
    while IFS='|' read -r name nps cmd; do
        read -r -a a <<< "$cmd"
        n=$((n + 1)); p="$parts/$(printf %02d $n)"
        # Open MPI's shared-memory BTL prints a "Read -1 ... errno" line per refused single-copy
        # transfer in the container; it is not program output and differs run to run
        (python3 experiments/scout.py --code mfem --version "$V" --driver "$name" --np $nps --runs "$RUNS" \
             --jobs "${JOBS:-1}" --timeout 120 $(same_sample) --ignore "errno|Read -1, expected" \
             --out "$p.csv" -- "$M/examples/${a[0]}" "${a[@]:1}" > "$p.log" 2>&1; echo $? > "$p.rc") < /dev/null &
        while [ "$(jobs -rp | wc -l)" -ge "${PAR:-1}" ]; do wait -n; done
    done <<< "$DRIVERS"
    wait
    local ok=0
    for p in "$parts"/*.rc; do
        cat "${p%.rc}.log"
        [ "$(cat "$p")" = 0 ] && [ -f "${p%.rc}.csv" ] || { echo "a driver failed: ${p%.rc}.log" >&2; ok=1; }
    done
    [ $ok = 0 ] || return 1
    head -1 "$parts/01.csv" > "$OUT/predict.csv"
    for p in "$parts"/*.csv; do tail -n +2 "$p" >> "$OUT/predict.csv"; done
    rm -rf "$parts"
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) step_predict;;
        all) step_fetch && step_build && step_predict;;
        *) echo "usage: $0 [fetch|build|predict|all]" >&2; exit 2;;
    esac || exit 1
done
