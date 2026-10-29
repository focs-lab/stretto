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
# The bugs of the projects' histories and the two found since, from the sources of
# experiments/must/apps.sh CASES=hist (HIST_APPS_DIR, default third_party/must/apps-hist): wacc
# or wacxx on the translation unit that holds (or instantiates) the racing calls, with the
# include paths of the application's own build and Open MPI's headers, and a count of the
# monitor calls woven into it, as for deal.II:
#   cabana   Cabana fbbcc37f: CommunicationPlan's Irecv(MPI_ANY_SOURCE), Send and Isend are
#            templates of core/src/Cabana_CommunicationPlan.hpp, instantiated in the test's
#            unit (tstCommunicationPlan_SERIAL.cpp, which CMake generates; Kokkos 4.1, C++17)
#   stempy   stempy 3.3.5: gatherEvents, from electron_mpi.cpp, in experiments/stempy/harness.cpp
#            (C++17, cereal, BigMPI)
#   mfem332  MFEM 3.3.2: GroupTopology::Create in general/communication.cpp (C++11)
#   lammps   LAMMPS 3a2da51a: Irregular::create_atom and create_data in src/irregular.cpp
#   superlu  SuperLU_DIST 9.3.1: the 3D solve's receive loops in SRC/double/pdgstrs3d.c (C)
#   qe       Quantum ESPRESSO 615e40cf: elph_prt in PHonon/PH/elphon.f90 is Fortran
#   dealii8929  deal.II 2c1b27fb: ConsensusAlgorithm_NBX::run() in source/base/mpi.cc (C++17,
#            against the installed headers and the generated mpi.inst of the Open MPI build of
#            experiments/dealii8929/run.sh, DEALII8929_DIR)
# Writes experiments/results/mpiprv/stretto-apps.csv; logs under experiments/mpiprv/work/apps/.
# usage: experiments/mpiprv/prv.sh experiments/mpiprv/stretto_apps.sh
#        [hypre|dealii|mg|cabana|stempy|mfem332|lammps|superlu|qe|dealii8929 ...]
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
H="${HIST_APPS_DIR:-$R/third_party/must/apps-hist}"
weave() {    # weave <app> <version> <configurations> <wacc|wacxx> <source> <what> <flags...>: one unit, a row
    local app=$1 ver=$2 cfg=$3 tool=$4 src=$5 what=$6; shift 6
    local d="$W/$app" f; f=$(basename "$src")
    mkdir -p "$d" && cp "$src" "$d/$f" || { row "$app" "$ver" "$cfg" "$tool on $f" "source missing: $src" unsupported "no source"; return; }
    (cd "$d" && env aspect_ah="$W/aspect/MPIMonitorAspect.ah" aspect_lib="$W/aspect" MPI=1 \
         "$tool" -O2 -I$OMPI/include "$@" -c "$f" -o "$d/${f%.*}.o" > "$d/$tool.log" 2>&1)
    local rc=$? i n p
    i=$(ls "$d/${f%.*}"_instrumentation.* 2>/dev/null | head -1)
    n=$(woven "$i"); n=${n:-0}
    # the events of point-to-point calls, the racing calls' kind: calls, not declarations
    p=$(grep -E "__RVC_MPI_(Send|Recv|Isend|Irecv)\(" "$i" 2>/dev/null | grep -vc "void __RVC_MPI_")
    local first; first=$(grep -m1 -i "error" "$d/$tool.log" | cut -c1-200 | tr '"' "'")
    if [ -z "$i" ]; then
        row "$app" "$ver" "$cfg" "$tool on $f" "status $rc; no instrumented unit; first error: ${first:--}" unsupported \
            "the instrumenter does not process the unit that holds $what; the trace would hold no event of the racing calls"
    elif [ "${p:-0}" -gt 0 ] && [ $rc = 0 ]; then
        row "$app" "$ver" "$cfg" "$tool on $f" "status $rc; $n monitor calls woven, $p of point-to-point calls" supported ""
    elif [ $rc = 0 ]; then
        row "$app" "$ver" "$cfg" "$tool on $f" "status $rc; $n monitor calls woven, none of a point-to-point call" unsupported \
            "the instrumenter weaves no event before the point-to-point calls of the unit that holds $what; the trace would hold no event of the racing calls"
    else
        row "$app" "$ver" "$cfg" "$tool on $f" "status $rc; $n monitor calls woven; first error: ${first:--}" unsupported \
            "the instrumenter fails on the unit that holds $what; the trace would hold no event of the racing calls"
    fi
}
step_cabana() {
    local c="$H/cabana" s="$H/cabana/Cabana-fbbcc37f4f8d2326541eeb87f3c1879148552183"
    weave cabana fbbcc37f "CommPlan.Test10NoTopo, 3 and 4 ranks" wacxx "$c/build-fbbcc37f/core/unit_test/SERIAL/tstCommunicationPlan_SERIAL.cpp" \
        "CommunicationPlan's racing Irecv, Send and Isend (header templates)" -std=c++17 -DKOKKOS_DEPENDENCE \
        -I"$s/cmake/test_harness" -I"$s/core/unit_test" -I"$s/core/src" -I"$c/build-fbbcc37f/core/src" \
        -I"$c/build-fbbcc37f/core/unit_test/SERIAL" -I"$c/kokkos/include" -I"$c/gtest/include"
}
step_stempy() {
    local s="$H/stempy"
    weave stempy 3.3.5 "gatherEvents harness, 4 and 8 ranks" wacxx "$R/experiments/stempy/harness.cpp" \
        "gatherEvents' size and map receives" -std=c++17 -I"$s/include-3.3.5" -I"$s/stempy-3.3.5/stempy" \
        -I"$s/stempy-3.3.5/thirdparty/Empirical/include" -I"$s/cereal-02eace19a99ce3cd564ca4e379753d69af08c2c8/include" -I"$s/bigmpi/include"
}
step_mfem332() {
    local s="$H/mfem332/mfem-3.3.2"
    weave mfem332 3.3.2 "two-mesh driver, 4 and 8 ranks" wacxx "$s/general/communication.cpp" "GroupTopology::Create's receives" \
        -std=c++11 -I"$s/general" -I"$s" -I"$H/mfem332/hypre-2.11.2/src/hypre/include"
}
step_lammps() {
    local s="$H/lammps/lammps-3a2da51a8210970a2382185f003fd512c12bada9/src" d="$W/lammps"
    weave lammps 3a2da51a "in.displace, 4 and 8 ranks" wacxx "$s/irregular.cpp" "Irregular::create_atom's sends and wildcard receives" \
        -std=c++11 -I"$s"
    tail -1 "$OUT" | grep -q "^lammps,.*,supported," || return 0
    # the unit is instrumented: LAMMPS built with it (the other units have no point-to-point call
    # of the bug and are compiled as by experiments/lammps/run.sh, with Open MPI's wrapper), the
    # aspect library linked, in.displace run once on 4 ranks, and the trace analysed with each
    # pattern of run.py's arm a (every event an MPI call: properties 1, 6, 7 and 9). The bug is
    # a buffering hazard, which none of MPI-PRV's properties names (run.py: outside its scope)
    local b="$d/build"
    rm -rf "$b" && mkdir -p "$b" && cp -a "$s/." "$b/" && rm -rf "$b"/Obj_* "$b/lmp_mpi" || return 1
    (cd "$b" && make -j"${MAKE_JOBS:-4}" mpi CC=mpicxx LINK=mpicxx CCFLAGS="-g -O2 -std=c++11" > "$d/make.log" 2>&1 \
         && cp "$d/irregular.o" Obj_mpi/irregular.o \
         && make mpi CC=mpicxx LINK=mpicxx CCFLAGS="-g -O2 -std=c++11" MPI_LIB="$W/aspect/libaspect.a" >> "$d/make.log" 2>&1) \
        || { row lammps 3a2da51a "in.displace, 4 ranks" "LAMMPS built with the instrumented irregular.cpp" "build failed: $(tail -1 "$d/make.log" | tr '"' "'")" unsupported "the instrumented build does not link"; return 0; }
    local r="$d/run"; rm -rf "$r" && mkdir -p "$r" && cd "$r" || return 1
    /usr/bin/timeout -k 5 120 mpirun --allow-run-as-root -np 4 "$b/lmp_mpi" -in "$R/experiments/lammps/in.displace" -log none \
        > run.log 2>&1 < /dev/null
    local rc=$? ev=0 res=() n
    [ -f output.txt ] && ev=$(grep -c "^event" output.txt)
    for n in P1 P6 P7 P9; do
        local allp pat out
        read -r allp pat < <(python3 -c "import sys; sys.path.insert(0, '$R/experiments/mpiprv'); import run; print(*run.PATTERNS['$n'])")
        printf 'parametric: 0\nallPattern : {%s}\nPattern : {%s}\n' "$allp" "$pat" > "$n.txt"
        out=$([ -f output.txt ] && /usr/bin/timeout 600 MPI-PRV -trace output.txt -pattern "$n.txt" -np 4 2>&1)
        echo "$out" > "$n.log"
        case "$out" in *"Pattern Matched!"*) res+=("$n matched");; *"Pattern Not Matched!"*) res+=("$n none");; *) res+=("$n error");; esac
    done
    cd "$R"
    row lammps 3a2da51a "in.displace, 4 ranks" "LAMMPS built with the instrumented irregular.cpp, run once, analysed" \
        "run status $rc; $ev events in the trace; patterns of arm a: ${res[*]}" out-of-scope \
        "instrumented and run; the bug is a buffering hazard (a send-send cycle that needs unbuffered standard sends), which none of MPI-PRV's properties names, and its analysis keeps every message match fixed"
}
step_superlu() {
    local s="$H/superlu/superlu_dist"
    weave superlu_dist 9.3.1 "pddrive3d, blk12x6 on 3 and blk8x10 on 6 ranks" wacc "$s/SRC/double/pdgstrs3d.c" \
        "the 3D solve's wildcard receive loops" -I"$s/SRC/include" -I"$s/SRC/double" -I"$s/SRC/prec-independent" -I"$H/superlu/build/SRC/include"
}
step_qe() {
    row qe 615e40cf "ph.x, Ni at X, 3 ranks in 3 pools" "-" "-" unsupported \
        "elph_prt (PHonon/PH/elphon.f90) is Fortran; MPI-PRV's instrumenter (wac, wacc, wacxx) weaves C and C++ sources only"
}
step_dealii8929() {
    # the Open MPI build of experiments/dealii8929/run.sh (DEALII8929_DIR): its installed headers
    # carry the configuration that compiled mpi.cc, and its build directory has mpi.inst, the
    # instantiations that deal.II's build generates and mpi.cc includes
    local D="${DEALII8929_DIR:-$R/third_party/apps/dealii8929}" c=2c1b27fbee49028f7de95863e44efe33d41f26ac
    local s="$W/dealii8929-src/dealii-$c" i="$D/install-${c:0:8}/include"
    [ -d "$s" ] || { mkdir -p "$W/dealii8929-src" && tar xzf "$D/dealii-$c.tar.gz" -C "$W/dealii8929-src"; } || return 1
    weave dealii8929 2c1b27fb "nbx_calls 3, 4 and 8 ranks" wacxx "$s/source/base/mpi.cc" \
        "ConsensusAlgorithm_NBX::run's request Isends, wildcard Iprobe and Recv" -std=c++17 -I"$i" -I"$i/deal.II/bundled" \
        -I"$D/build-${c:0:8}/source/base"
}
aspect || { echo "the aspect library could not be built" >&2; exit 1; }
steps=("$@"); [ $# -gt 0 ] || steps=(hypre dealii mg)
for step in "${steps[@]}"; do
    case "$step" in
        hypre|dealii|mg|cabana|stempy|mfem332|lammps|superlu|qe|dealii8929) "step_$step";;
        *) echo "usage: $0 [hypre|dealii|mg|cabana|stempy|mfem332|lammps|superlu|qe|dealii8929 ...]" >&2; exit 2;;
    esac
done
