#!/usr/bin/env bash
# A documented race in MFEM 3.3.2, from the project's history (commit c52c6243b).
#
# GroupTopology::Create (general/communication.cpp), the last communication of the ParMesh
# constructor ParMesh(comm, Mesh&), sends each shared group's row [the master's group id, the
# group's ranks] from the group's master with MPI_Isend (tag 822); every other member calls
# MPI_Recv(recv_buf, max_recv_size, MPI_INT, MPI_ANY_SOURCE, 822) once per group it belongs to,
# looks the group up by its ranks and stores the master's id; an MPI_Waitall ends the call.
# Nothing collective separates two constructions, so a receive of one mesh's Create can take a
# row of the next mesh's: a longer row fails with MPI_ERR_TRUNCATE, one naming a group the mesh
# does not have aborts ("ListOfIntegerSets::Lookup (), integer set not found"), and one that
# fits silently stores another master's id. Commit c52c6243b (8 Jan 2018, first released in
# 3.4): "The previous implementation was using MPI_Recv() with MPI_ANY_SOURCE which was causing
# issues where one rank was receiving data sent from different calls to Create()". MFEM has no
# test that builds two parallel meshes back to back, so the driver, experiments/mfem332/
# twomesh.cpp, builds two ParMesh objects from different serial meshes (16x16 quadrilaterals on
# the unit square, 30x7 on 2x1), then an H1 space on each, and prints each mesh's global true
# size and area (the integral of 1), "ok" when the area is right.
#
# Steps, inside the experiment container, from the repository root, Open MPI build:
#   fetch    download MFEM 3.3.2 and 3.4, HYPRE 2.11.2 and METIS 4.0.3 (the copy in MFEM's tpls
#            repository), check every archive's sha256
#   build    METIS and HYPRE, then MFEM's parallel library (-O2 -g) at both versions and the
#            driver for each, -j$MAKE_JOBS
#   predict  RUNS traced runs of the driver on 3.3.2 per process count of NPS, analysed at L1
#            (the single-path explorer hits its state cap on these traces), the findings of an
#            error class replayed by stretto confirm, every cross alternative
#            (experiments/mfem332/classify.py: a receive taking a row of the other mesh's Create)
#            replayed with its horizon pins and its control, JOBS traced runs at a time
#            (experiments/history_predict.py)
#   stress   STRESS_RUNS untraced runs of the driver on 3.3.2 per process count, plain and with
#            random send and receive delays from the replay library, TIMEOUT seconds each
#            (experiments/history_stress.py)
#   fixed    predict and stress on 3.4
#   all      fetch build predict stress fixed
# Writes experiments/results/mfem332/{predict,stress}-{3.3.2,3.4}.csv.
# usage: experiments/mfem332/run.sh [fetch|build|predict|stress|fixed|all]
#        NPS ("4 8"), RUNS (20), STRESS_RUNS (40), TIMEOUT (60), JOBS (3), MAKE_JOBS (8),
#        MFEM332_DIR (third_party/apps/mfem332)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${MFEM332_DIR:-$R/third_party/apps/mfem332}"
OUT="$R/experiments/results/mfem332"; mkdir -p "$OUT"
NPS="${NPS:-4 8}"; RUNS="${RUNS:-20}"; STRESS_RUNS="${STRESS_RUNS:-40}"; TIMEOUT="${TIMEOUT:-60}"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
# the container refuses process_vm_readv, the single-copy mechanism of Open MPI's shared-memory
# transport, which then prints a line per refused transfer; without it the transport copies
# through its shared buffers (mpirun passes OMPI_MCA_* settings on to the ranks)
export OMPI_MCA_btl_vader_single_copy_mechanism=none
declare -A SHA=([mfem-3.3.2.tar.gz]=eee063755a706f6d738ab6121909812522ae349ec8a44c3c3405e1621a069c34
                [mfem-3.4.tar.gz]=cec86054e1cf7c98b7999221e7caa5b7f280ec3363c66cf3ed54e0cbb94fa441
                [hypre-2.11.2.tar.gz]=ce929242d399064ed913bf65b11c013b90fae12b7d8aab7397fa10973a74f3f6
                [metis-4.0.3.tar.gz]=5efa35de80703c1b2c4d0de080fafbcf4e0d363a21149a1ad2f96e0144841a55)
declare -A URL=([mfem-3.3.2.tar.gz]=https://github.com/mfem/mfem/archive/refs/tags/v3.3.2.tar.gz
                [mfem-3.4.tar.gz]=https://github.com/mfem/mfem/archive/refs/tags/v3.4.tar.gz
                [hypre-2.11.2.tar.gz]=https://github.com/hypre-space/hypre/archive/refs/tags/v2.11.2.tar.gz
                [metis-4.0.3.tar.gz]=https://github.com/mfem/tpls/raw/gh-pages/metis-4.0.3.tar.gz)
OK='(?s)mesh 1:[^\n]* ok.*mesh 2:[^\n]* ok'
exe() { echo "$D/twomesh-$1"; }

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    local f
    for f in "${!URL[@]}"; do
        fetch_checked "$f" "${URL[$f]}" "${SHA[$f]}" || return 1
        [ -d "${f%.tar.gz}" ] || tar xzf "$f" || return 1
    done
    cd "$R"
}
step_build() {
    local j="${MAKE_JOBS:-8}" v
    # METIS 4's K&R sources need the C of their time
    [ -f "$D/metis-4.0.3/libmetis.a" ] || (cd "$D/metis-4.0.3" && make -j"$j" CC=/usr/bin/gcc OPTFLAGS="-O2 -std=gnu89" > "$D/metis.log" 2>&1) \
        || { echo "build of METIS failed ($D/metis.log)" >&2; return 1; }
    if [ ! -f "$D/hypre-2.11.2/src/hypre/lib/libHYPRE.a" ]; then
        (cd "$D/hypre-2.11.2/src" && ./configure CC=/usr/bin/mpicc.openmpi CXX=/usr/bin/mpicxx.openmpi CFLAGS="-O2 -g" \
             --disable-fortran --with-MPI > "$D/hypre-configure.log" 2>&1 && make -j"$j" > "$D/hypre-make.log" 2>&1) \
            || { echo "build of HYPRE 2.11.2 failed ($D/hypre-make.log)" >&2; return 1; }
    fi
    # MFEM 3.x's makefile takes HYPRE and METIS from its defaults, ../hypre-2.10.0b/src/hypre
    # and ../metis-4.0, whatever the command line says: those names point at the builds
    ln -sfn hypre-2.11.2 "$D/hypre-2.10.0b" && ln -sfn metis-4.0.3 "$D/metis-4.0" || return 1
    for v in 3.3.2 3.4; do
        if [ ! -f "$D/mfem-$v/libmfem.a" ]; then
            (cd "$D/mfem-$v" && make parallel -j"$j" MPICXX=/usr/bin/mpicxx.openmpi "OPTIM_FLAGS=-O2 -g -std=c++11" \
                 > "$D/mfem-$v.log" 2>&1) || { echo "build of MFEM $v failed ($D/mfem-$v.log)" >&2; return 1; }
        fi
        /usr/bin/mpicxx.openmpi -O2 -g -std=c++11 -I"$D/mfem-$v" -I"$D/hypre-2.11.2/src/hypre/include" \
            experiments/mfem332/twomesh.cpp -o "$(exe $v)" -L"$D/mfem-$v" -lmfem -L"$D/hypre-2.11.2/src/hypre/lib" -lHYPRE \
            -L"$D/metis-4.0.3" -lmetis -lrt -lm || { echo "build of the driver for MFEM $v failed" >&2; return 1; }
    done
}
predict() {   # predict <version>
    need_file "$(exe "$1")" || return 1
    python3 experiments/history_predict.py --version "$1" --np $NPS --runs "$RUNS" --timeout 30 --jobs "${JOBS:-3}" --no-sp \
        --confirm --classify experiments/mfem332/classify.py --replay cross --ok "$OK" --out "$OUT/predict-$1.csv" -- "$(exe "$1")"
}
stress() {    # stress <version>
    need_file "$(exe "$1")" || return 1
    rm -f "$OUT/stress-$1.csv"
    python3 experiments/history_stress.py --version "$1" --label twomesh --np $NPS --runs "$STRESS_RUNS" --timeout "$TIMEOUT" \
        --jobs "${JOBS:-3}" --ok "$OK" --out "$OUT/stress-$1.csv" -- "$(exe "$1")"
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) predict 3.3.2;; stress) stress 3.3.2;;
        fixed) predict 3.4 && stress 3.4;;
        all) step_fetch && step_build && predict 3.3.2 && stress 3.3.2 && predict 3.4 && stress 3.4;;
        *) echo "usage: $0 [fetch|build|predict|stress|fixed|all]" >&2; exit 2;;
    esac || exit 1
done
