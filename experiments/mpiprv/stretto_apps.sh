#!/usr/bin/env bash
# MPI-PRV on the five applications whose races Stretto predicts (HYPRE 2.10.1, HYPRE 3.2.0 through
# ex5 and through PETSc's ex2, NPB MG 2.4.1, deal.II 9.3.0-rc1), inside MPI-PRV's image
# (experiments/mpiprv/prv.sh), the way its artifact instruments its own multi-file applications
# (MiniAMR, ParMETIS): the aspect library built from a MOP file (cmop, rv-monitor, as the
# artifact's MPIaspect/install.sh does; the MOP file is run.py's, with every event its shipped
# properties use), then the application's own build with wacc (C) or wacxx (C++) as the compiler,
# aspect_ah, aspect_lib and MPI=1 in the environment. Each step records whether the instrumenter
# processes the code that holds the application's MPI calls; a program it cannot instrument, or
# whose racing calls it leaves without events, cannot be traced and is "unsupported", with the
# reason. The sources are those of experiments/must/apps.sh (MUST_APPS_DIR).
#   hypre    HYPRE 2.10.1 and 3.2.0: every MPI call of HYPRE's solvers, ParaSails' included (all but
#            the FEI interface, which these builds leave out), goes through the
#            hypre_MPI_* wrappers of src/utilities/mpistubs.c (_hypre_utilities.h maps the MPI
#            names onto them), so the library's MPI events are those wacc weaves into that file;
#            make -k of src/utilities with CC=wacc, Open MPI (the image's) as in its artifact
#   dealii   deal.II 9.3.0-rc1: RemotePointEvaluation's Isend, Probe and Recv are templates of
#            include/deal.II/base/mpi_remote_point_evaluation.h, instantiated in the test's own
#            translation unit; wacxx on tests/remote_point_evaluation/mapping_01.cc, against the
#            installed headers, and a count of the monitor calls woven before its MPI calls
#   mg       NPB MG 2.4.1 is Fortran; wac instruments C and C++ only (its --compile_language)
# Writes experiments/results/mpiprv/stretto-apps.csv; logs under experiments/mpiprv/work/apps/.
# usage: experiments/mpiprv/prv.sh experiments/mpiprv/stretto_apps.sh [hypre|dealii|mg ...]
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
M="${MUST_APPS_DIR:-$R/third_party/apps/must}"
W="$R/experiments/mpiprv/work/apps"; mkdir -p "$W"
OUT="$R/experiments/results/mpiprv/stretto-apps.csv"
COMMIT="${STRETTO_COMMIT:-$(python3 experiments/provenance.py)}"
OMPI=/usr/lib/x86_64-linux-gnu/openmpi
[ -f "$OUT" ] || echo "app,version,configurations,step,result,outcome,reason,commit" > "$OUT"
row() { echo "$1,$2,\"$3\",\"$4\",\"$5\",$6,\"$7\",$COMMIT" | tee -a "$OUT"; }

aspect() {   # the aspect library of run.py's MOP file, once
    local a="$W/aspect"
    [ -f "$a/libaspect.a" ] && return 0
    mkdir -p "$a" && cd "$a" || return 1
    python3 -c "import sys; sys.path.insert(0, '$R/experiments/mpiprv'); import run; open('MPI.mop', 'w').write(run.MOP)"
    cmop -v -d . MPI.mop > cmop.log 2>&1 && rv-monitor -c -MPI MPI.rvm > rv-monitor.log 2>&1 \
        && clang++ -O3 -fPIC -c ./*.cc && ar -r -o libaspect.a ./*.o > /dev/null || { cd "$R"; return 1; }
    cd "$R"
}
woven() {    # woven <file>: monitor calls woven into an instrumented file (calls, not declarations)
    grep -c "__RVC_MPI_[A-Za-z]*(" "$1" 2>/dev/null | awk -v d="$(grep -c "void __RVC_MPI_[A-Za-z]*(" "$1" 2>/dev/null)" '{ print $1 - d }'
}
step_hypre() {
    local v src cfg configs
    for v in 2.10.1 3.2.0; do
        local d="$W/hypre-$v"; mkdir -p "$d"
        if [ $v = 2.10.1 ]; then src="$d/hypre-2.10.1"; cfg="--without-fei"; configs="ij -solver 8 -pout 0, 4 and 8 ranks"
            [ -d "$src" ] || tar xzf "$M/hypre/hypre-2.10.1.tar.gz" -C "$d"
        else src="$d/hypre-3.2.0/src"; cfg="--disable-fortran"; configs="ex5_levels and PETSc ex2 (two-level ParaSails), 4 and 8 ranks"
            [ -d "$src" ] || tar xzf "$M/hypre3/hypre-3.2.0.tar.gz" -C "$d"
        fi
        (cd "$src" && env aspect_ah="$W/aspect/MPIMonitorAspect.ah" aspect_lib="$W/aspect" MPI=1 ./configure CC=wacc CFLAGS="-O2 -g -I$OMPI/include" LDFLAGS="-L$OMPI/lib" LIBS=-lmpi $cfg --with-MPI \
             > "$d/configure.log" 2>&1) || { row hypre $v "$configs" "configure with CC=wacc" "failed: $(tail -1 "$d/configure.log")" unsupported "configure failed"; continue; }
        (cd "$src/utilities" && env aspect_ah="$W/aspect/MPIMonitorAspect.ah" aspect_lib="$W/aspect" MPI=1 make -k > "$d/make-utilities.log" 2>&1)
        local failed rankid crashed
        failed=$(grep -o "\[[^]]*: [A-Za-z_0-9]*\.o\] Error [0-9]*" "$d/make-utilities.log" | sed 's/.*: \([A-Za-z_0-9]*\.o\).*/\1/' | sort -u | tr '\n' ' ')
        rankid=$(grep -c "not find rankid" "$d/make-utilities.log"); crashed=$(grep -c "failed with exit code 139" "$d/make-utilities.log")
        local result="failed objects: ${failed% }; 'not find rankid' $rankid time(s), clang crashed (status 139) $crashed time(s)"
        if [[ " $failed" == *" mpistubs.o "* ]]; then
            row hypre $v "$configs" "make -k of src/utilities with CC=wacc" "$result" unsupported \
                "the instrumenter fails on utilities/mpistubs.c, the file of the hypre_MPI_* wrappers through which HYPRE's solvers, ParaSails included, make every MPI call; without it the trace holds no MPI event of HYPRE"
        elif [ -n "$failed" ]; then
            row hypre $v "$configs" "make -k of src/utilities with CC=wacc" "$result" unsupported "the instrumenter fails on part of HYPRE's utilities"
        else
            row hypre $v "$configs" "make -k of src/utilities with CC=wacc" "built; $(woven "$src/utilities/mpistubs_instrumentation.c") monitor calls in mpistubs.c" supported ""
        fi
    done
}
step_dealii() {
    local D="$M/dealii" d="$W/dealii"
    mkdir -p "$d/tests/remote_point_evaluation"
    cp "$D/dealii-9.3.0-rc1/tests/tests.h" "$d/tests/" && cp "$D/dealii-9.3.0-rc1/tests/remote_point_evaluation/mapping_01.cc" "$d/tests/remote_point_evaluation/" || return 1
    (cd "$d/tests/remote_point_evaluation" && env aspect_ah="$W/aspect/MPIMonitorAspect.ah" aspect_lib="$W/aspect" MPI=1 \
         wacxx -O2 -std=c++17 -I$OMPI/include -I"$D/install-9.3.0-rc1/include" -I"$D/install-9.3.0-rc1/include/deal.II/bundled" \
         -I"$D/p4est/include" -c mapping_01.cc -o "$d/mapping_01.o" > "$d/wacxx.log" 2>&1)
    local rc=$? i="$d/tests/remote_point_evaluation/mapping_01_instrumentation.i" n calls
    n=$(woven "$i"); calls=$(grep -c "MPI_Isend(buffer\|MPI_Probe(-1, internal::Tags::remote_point_evaluation\|MPI_Recv(buffer" "$i" 2>/dev/null)
    if [ "${n:-0}" -gt 0 ]; then
        row dealii 9.3.0-rc1 "mapping_01, 4 ranks" "wacxx on mapping_01.cc" "status $rc; $n monitor calls woven" supported ""
    else
        row dealii 9.3.0-rc1 "mapping_01, 4 ranks" "wacxx on mapping_01.cc" "status $rc; $calls racing MPI calls of RemotePointEvaluation in the instrumented unit, 0 monitor calls woven" unsupported \
            "the instrumenter weaves no event into the translation unit that instantiates RemotePointEvaluation (its MPI calls are header templates); the trace would hold no event of the racing calls"
    fi
}
step_mg() {
    row npbmg 2.4.1 "class S, 16 ranks" "-" "-" unsupported "NPB MG is Fortran; MPI-PRV's instrumenter (wac, wacc, wacxx) weaves C and C++ sources only"
}
aspect || { echo "the aspect library could not be built" >&2; exit 1; }
steps=("$@"); [ $# -gt 0 ] || steps=(hypre dealii mg)
for step in "${steps[@]}"; do
    case "$step" in
        hypre|dealii|mg) "step_$step";;
        *) echo "usage: $0 [hypre|dealii|mg ...]" >&2; exit 2;;
    esac
done
