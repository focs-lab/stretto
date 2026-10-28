#!/usr/bin/env bash
# A new bug in Quantum ESPRESSO's PHonon, elph_prt (develop 615e40c of 26 Oct 2026; the function
# is the same in qe-7.6; found by Stretto, not yet reported upstream).
#
# With electron_phonon='prt', ph.x prints the electron-phonon vertex of one k point (kx, ky,
# kz). With LSDA (nspin=2) each of the two spins of that k point is held by some pool, and in
# elph_prt (PHonon/PH/elphon.f90, lines 1498-1513) the root of each pool that holds spin ispn
# sends the ionode el_ph_mat with MPI_SEND(..., 4*nproc_image, ...) and the two eigenvalue
# vectors with tag 6*nproc_image, while the ionode receives each spin it does not hold with
# MPI_RECV(..., MPI_ANY_SOURCE, 4*nproc_image, ...) and twice MPI_RECV(..., MPI_ANY_SOURCE,
# 6*nproc_image, ...). The tags do not depend on the spin. With three or more pools both spins of
# some k points are held by two pools other than the ionode's, which send at the same time, and
# the receives of spin 1 can take spin 2's messages: the printed table has the |g| or the
# eigenvalues of the two spins exchanged or mixed, and the run ends normally. The code came with
# the LSDA support of elph_prt in commit aabea86 (EPW v6.1, merge request 2817); qe-7.5 has no
# spin loop there. spin-tags.patch adds ispn - 1 to the six tags (the proposed fix). The driver
# is the test-suite's Ni case (test-suite/ph_base: ni.scf.in, LSDA on a 4x4x4 grid, and
# ni.phX.in, the phonon at X) with fildvscf, electron_phonon='prt' and the k point
# (-0.375, 1.125, 0.375): k(39) of ph.x's list, whose spin-up copy is in pool 1 and spin-down
# copy in pool 2 at -nk 3. ph.x runs on 3 ranks with -nk 3, and a run is correct when its table
# matches the reference table of the version with the bug run with one pool (-nk 1, no message
# in elph_prt; experiments/qeelph/judge.py).
#
# The builds use the MPICH wrappers: Open MPI's Fortran bindings call PMPI_* directly, which the
# tracer does not see (experiments/RUNBOOK.md, Notes).
#
# Steps, inside the experiment container, from the repository root:
#   fetch    download QE at 615e40c (GitLab's archive), the archives of the three submodules
#            the build needs at the commits QE records (external/submodule_commit_hash_records:
#            devicexlib, wannier90, libmbd), CMake 3.28.3 and the Ni pseudopotential of the
#            test-suite (Ni.pbe-nd-rrkjus.UPF), check every sha256; the fixed tree is a copy
#            with spin-tags.patch applied
#   build    pw.x and ph.x of both trees with the MPICH wrappers: CMake 3.28.3, gfortran 13, no
#            OpenMP, no ScaLAPACK, QE's internal FFTW, the system's reference BLAS and LAPACK,
#            -fno-lto (Ubuntu's wrappers add -flto), -j$MAKE_JOBS
#   inputs   the scf run (pw.x of 615e40c, 3 ranks, -nk 3) into run/k39/out, the phonon input
#            run/k39/ph.in and the reference table run/ref-nk1.out (ph.x of 615e40c, 3 ranks,
#            -nk 1)
#   predict  RUNS traced runs of 615e40c, each analysed at L1, every cross alternative
#            (experiments/qeelph/classify.py: a receive of elph_prt taking the other pool's
#            message) replayed with the pins of its whole horizon and with its control, each run
#            in a fresh copy of run/k39 (ph.x writes its outdir), TIMEOUT seconds per replay
#            (experiments/history_predict.py)
#   stress   STRESS_RUNS untraced runs of 615e40c, plain and with random send and receive
#            delays from the replay library (experiments/history_stress.py)
#   fixed    predict and stress on 615e40c with spin-tags.patch
#   all      fetch build inputs predict stress fixed
# Writes experiments/results/qeelph/{predict,stress}-{615e40c,615e40c-spintags}.csv.
# usage: experiments/qeelph/run.sh [fetch|build|inputs|predict|stress|fixed|all]
#        RUNS (10), STRESS_RUNS (40), TIMEOUT (120), JOBS (3), MAKE_JOBS (8), QE_DIR
#        (third_party/apps/qeelph)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${QE_DIR:-$R/third_party/apps/qeelph}"
OUT="$R/experiments/results/qeelph"; mkdir -p "$OUT"
RUNS="${RUNS:-10}"; STRESS_RUNS="${STRESS_RUNS:-40}"; TIMEOUT="${TIMEOUT:-120}"; JOBS="${JOBS:-3}"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
# Open MPI's mpirun starts the MPICH ranks (stretto.confirm.launcher); concurrent runs must not
# all bind their ranks to the container's first cores
export OMPI_MCA_hwloc_base_binding_policy=none OMP_NUM_THREADS=1
QE=615e40cfa30dc8ac1e95161b32d29f326c71ae65
DEVXLIB=a6b89ef77b1ceda48e967921f1f5488d2df9226d
WANNIER90=971c8b64bfe703c747a52655e1d59936597fe8ea
MBD=89a3cc199c0a200c9f0f688c3229ef6b9a8d63bd
BUG=615e40c; FIX=615e40c-spintags
declare -A SHA=([q-e-${QE:0:8}.tar.gz]=26a2c18510bff90041f39e2852f02534f3b3a7533d03ac1bb46331fb8d31d40e
                [devicexlib-${DEVXLIB:0:8}.tar.gz]=39d55ae63168f9692ab649e7cededb3bc181580f450d30d5d4deeb008ea4a29c
                [wannier90-${WANNIER90:0:8}.tar.gz]=81cf1f5c14cf0423df5fa45ad5c48bbf97f05c06773176f2712cc31916d61fee
                [libmbd-${MBD:0:8}.tar.gz]=956eaa8659a24e59573a64e78372cee9f299b4bf0a89a82f6bf98685034c7736
                [cmake-3.28.3-linux-x86_64.tar.gz]=804d231460ab3c8b556a42d2660af4ac7a0e21c98a7f8ee3318a74b4a9a187a6
                [Ni.pbe-nd-rrkjus.UPF]=d5ab40cf6cbaa88744d4c68ba8889b21a02c69f0ec9649581fa340ac7e881324)
declare -A URL=([q-e-${QE:0:8}.tar.gz]=https://gitlab.com/QEF/q-e/-/archive/$QE/q-e-$QE.tar.gz
                [devicexlib-${DEVXLIB:0:8}.tar.gz]=https://gitlab.com/max-centre/components/devicexlib/-/archive/$DEVXLIB/devicexlib-$DEVXLIB.tar.gz
                [wannier90-${WANNIER90:0:8}.tar.gz]=https://github.com/wannier-developers/wannier90/archive/$WANNIER90.tar.gz
                [libmbd-${MBD:0:8}.tar.gz]=https://github.com/libmbd/libmbd/archive/$MBD.tar.gz
                [cmake-3.28.3-linux-x86_64.tar.gz]=https://github.com/Kitware/CMake/releases/download/v3.28.3/cmake-3.28.3-linux-x86_64.tar.gz
                [Ni.pbe-nd-rrkjus.UPF]=https://pseudopotentials.quantum-espresso.org/upf_files/Ni.pbe-nd-rrkjus.UPF)
CMAKE="$D/cmake-3.28.3-linux-x86_64/bin/cmake"
K="-0.375 1.125 0.375"          # k(39)
RUN="$D/run/k39"; REF="$D/run/ref-nk1.out"
ph() { echo "$D/build-$1/bin/ph.x"; }
src() { [ "$1" = "$BUG" ] && echo "$D/q-e-$QE" || echo "$D/q-e-$QE-spintags"; }

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    local f s c
    for f in "${!URL[@]}"; do fetch_checked "$f" "${URL[$f]}" "${SHA[$f]}" || return 1; done
    [ -d cmake-3.28.3-linux-x86_64 ] || tar xzf cmake-3.28.3-linux-x86_64.tar.gz || return 1
    if [ ! -d "q-e-$QE" ]; then
        tar xzf "q-e-${QE:0:8}.tar.gz" || return 1
        # QE's CMake clones a submodule into external/<name> unless the directory has a .git; the
        # archives are the commits QE records, and the empty marker makes the CMake take them
        for s in devxlib:devicexlib-${DEVXLIB:0:8} wannier90:wannier90-${WANNIER90:0:8} mbd:libmbd-${MBD:0:8}; do
            c="q-e-$QE/external/${s%%:*}"
            grep -q "^$(case ${s%%:*} in devxlib) echo $DEVXLIB;; wannier90) echo $WANNIER90;; mbd) echo $MBD;; esac) ${s%%:*}\$" \
                "q-e-$QE/external/submodule_commit_hash_records" || harness_error "QE records another commit of ${s%%:*}" || return 1
            mkdir -p "$c" && tar xzf "${s#*:}.tar.gz" -C "$c" --strip-components=1 && touch "$c/.git" || return 1
        done
    fi
    if [ ! -d "q-e-$QE-spintags" ]; then
        cp -a "q-e-$QE" "q-e-$QE-spintags.part" \
            && patch -s -p1 -d "q-e-$QE-spintags.part" < "$R/experiments/qeelph/spin-tags.patch" \
            && mv "q-e-$QE-spintags.part" "q-e-$QE-spintags" || { rm -rf "q-e-$QE-spintags.part"; return 1; }
    fi
    cd "$R"
}
step_build() {
    local v b
    for v in "$BUG" "$FIX"; do
        [ -x "$(ph "$v")" ] && [ -x "$D/build-$v/bin/pw.x" ] && continue
        b="$D/build-$v"
        ("$CMAKE" -S "$(src "$v")" -B "$b" -DCMAKE_Fortran_COMPILER=/usr/bin/mpifort.mpich -DCMAKE_C_COMPILER=/usr/bin/mpicc.mpich \
             -DCMAKE_Fortran_FLAGS=-fno-lto -DCMAKE_C_FLAGS=-fno-lto -DCMAKE_EXE_LINKER_FLAGS=-fno-lto \
             -DQE_ENABLE_MPI=ON -DQE_ENABLE_OPENMP=OFF -DQE_ENABLE_SCALAPACK=OFF -DQE_FFTW_INTERNAL=ON \
             -DBLAS_LIBRARIES=/usr/lib/x86_64-linux-gnu/libblas.so.3 -DLAPACK_LIBRARIES=/usr/lib/x86_64-linux-gnu/liblapack.so.3 \
             > "$b.log" 2>&1 && "$CMAKE" --build "$b" -j "${MAKE_JOBS:-8}" --target pw ph >> "$b.log" 2>&1) \
            || { echo "build of QE $v failed ($b.log)" >&2; return 1; }
    done
}
step_inputs() {
    need_file "$D/build-$BUG/bin/pw.x" "$(ph "$BUG")" || return 1
    local t="$D/q-e-$QE/test-suite/ph_base" w
    if [ ! -f "$RUN/out/nickel.xml" ]; then
        rm -rf "$RUN" && mkdir -p "$RUN" && cp "$D/Ni.pbe-nd-rrkjus.UPF" "$RUN/" || return 1
        sed -e "s#pseudo_dir = '../../pseudo/',#pseudo_dir = './', outdir = './out',#" "$t/ni.scf.in" > "$RUN/ni.scf.in"
        grep -q "outdir = './out'" "$RUN/ni.scf.in" || harness_error "ni.scf.in has no pseudo_dir line to set" || return 1
        (cd "$RUN" && /usr/bin/timeout 1200 /usr/bin/mpirun.openmpi --oversubscribe -np 3 "$D/build-$BUG/bin/pw.x" -nk 3 \
             -in ni.scf.in < /dev/null > scf.out 2>&1) || harness_error "the scf run failed ($RUN/scf.out)" || return 1
    fi
    read -r kx ky kz <<< "$K"
    sed -e "s#  fildyn='niX.dyn',#  fildyn='niX.dyn', outdir='./out',\n  fildvscf='dvscf', electron_phonon='prt',\n  kx=$kx, ky=$ky, kz=$kz,#" \
        "$t/ni.phX.in" > "$RUN/ph.in"
    grep -q "electron_phonon='prt'" "$RUN/ph.in" || harness_error "ni.phX.in has no fildyn line to extend" || return 1
    if ! grep -q "Electron-phonon vertex" "$REF" 2>/dev/null; then
        w=$(mktemp -d) && cp -a "$RUN/." "$w/" || return 1
        (cd "$w" && /usr/bin/timeout 1200 /usr/bin/mpirun.openmpi --oversubscribe -np 3 "$(ph "$BUG")" -nk 1 -in ph.in \
             < /dev/null > "$REF" 2>&1)
        rm -rf "$w"
        grep -q "Electron-phonon vertex" "$REF" || harness_error "the reference run printed no table ($REF)" || return 1
    fi
}
predict() {   # predict <version>
    need_file "$(ph "$1")" "$RUN/ph.in" "$REF" || return 1
    python3 experiments/history_predict.py --version "$1" --mpi mpich --np 3 --runs "$RUNS" --no-sp \
        --classify experiments/qeelph/classify.py --replay cross --timeout "$TIMEOUT" --jobs "$JOBS" \
        --judge experiments/qeelph/judge.py "$REF" --base "$RUN" --out "$OUT/predict-$1.csv" -- "$(ph "$1")" -nk 3 -in ph.in
}
stress() {    # stress <version>
    need_file "$(ph "$1")" "$RUN/ph.in" "$REF" || return 1
    rm -f "$OUT/stress-$1.csv"
    python3 experiments/history_stress.py --version "$1" --label k39-nk3 --mpi mpich --np 3 --runs "$STRESS_RUNS" \
        --timeout "$TIMEOUT" --jobs "$JOBS" --judge experiments/qeelph/judge.py "$REF" --base "$RUN" \
        --out "$OUT/stress-$1.csv" -- "$(ph "$1")" -nk 3 -in ph.in
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; inputs) step_inputs;; predict) predict "$BUG";; stress) stress "$BUG";;
        fixed) predict "$FIX" && stress "$FIX";;
        all) step_fetch && step_build && step_inputs && predict "$BUG" && stress "$BUG" && predict "$FIX" && stress "$FIX";;
        *) echo "usage: $0 [fetch|build|inputs|predict|stress|fixed|all]" >&2; exit 2;;
    esac || exit 1
done
