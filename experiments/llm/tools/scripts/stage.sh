#!/usr/bin/env bash
# Stage the source trees of the blind benchmark, one directory per item id, inside the
# llm-prep container (/h = the a-p13 home, read-only; /w = ~/llm-bench).
# usage: stage.sh <id>...   (no argument: every item)
set -uo pipefail
H=/h; W=/w; ST=$W/stage; DRV=$W/drivers
mkdir -p "$ST"
HY=$H/stretto-rerun2/third_party/apps/hypre
NPB=$H/stretto-rerun2/third_party/apps/npb-mg
DL=$H/stretto-rerun2/third_party/apps/dealii
D8=$H/tools-nbx/stretto/third_party/apps/dealii8929
HI=$H/stretto-hist/stretto/third_party/apps
NB=$H/stretto-newbug/src
SL=$H/newcase/stretto/third_party/apps/superlu3d
QE=$H/newcase/stretto/third_party/apps/qeelph
REPO=$W/repo-experiments   # copy of the repository's experiments/ (patches)
PH=$W/patches

fresh() { rm -rf "$ST/$1"; mkdir -p "$ST/$1"; echo "$ST/$1"; }
untar() { tar xzf "$1" -C "$2" --strip-components=1 --no-same-owner; }
# delete one exact line (fixed string) from a file; fail if it is not there exactly once
dropline() {
    local f=$1 s=$2 n
    n=$(grep -cxF -- "$s" "$f"); [ "$n" = 1 ] || { echo "dropline: $n matches in $f: $s" >&2; return 1; }
    grep -vxF -- "$s" "$f" > "$f.tmp" && cat "$f.tmp" > "$f" && rm "$f.tmp"
}

s_a55842() { local t; t=$(fresh a55842); untar $HY/hypre-2.10.1.tar.gz "$t"; }
s_520bef() { local t; t=$(fresh 520bef); untar $HY/hypre-2.11.0.tar.gz "$t"
    dropline "$t/CHANGELOG" "- Fixed an MPI bug that was causing Parasails to hang occasionally"; }
s_0d199a() { local t; t=$(fresh 0d199a); untar $NPB/NPB2.4.1.tar.gz "$t"; }
s_ccd0e2() { local t; t=$(fresh ccd0e2); untar $NPB/NPB3.3.1.tar.gz "$t"; bash $W/scripts/npb331-strip.sh "$t"; }
dealii_tests() { # $1 tree, $2 the extracted tree of the experiment with its fetched tests
    mkdir -p "$1/tests"; cp -n "$2/tests/tests.h" "$1/tests/" 2>/dev/null
    for d in remote_point_evaluation base mpi; do
        [ -d "$2/tests/$d" ] && { mkdir -p "$1/tests/$d"; cp -rn "$2/tests/$d/." "$1/tests/$d/"; }
    done; true; }
s_9e1162() { local t; t=$(fresh 9e1162); untar $DL/dealii-9.3.0-rc1.tar.gz "$t"; dealii_tests "$t" $DL/dealii-9.3.0-rc1; }
s_55816f() { local t; t=$(fresh 55816f); untar $DL/dealii-9.3.0.tar.gz "$t"; dealii_tests "$t" $DL/dealii-9.3.0; }
s_8dba4d() { local t; t=$(fresh 8dba4d); untar $D8/dealii-2c1b27fbee49028f7de95863e44efe33d41f26ac.tar.gz "$t"
    mkdir -p "$t/driver"; cp $DRV/nbx_calls.cc "$t/driver/"; }
s_ec7c94() { local t; t=$(fresh ec7c94); untar $D8/dealii-f9ea339613b8041c4e42e0c8b9e7297aa0a823be.tar.gz "$t"
    mkdir -p "$t/driver"; cp $DRV/nbx_calls.cc "$t/driver/"; }
s_8df9ee() { local t; t=$(fresh 8df9ee); untar $HI/cabana/cabana-fbbcc37f.tar.gz "$t"; }
s_a7d8e5() { local t; t=$(fresh a7d8e5); untar $HI/cabana/cabana-38170631.tar.gz "$t"
    local f="$t/core/src/Cabana_CommunicationPlan.hpp"
    # comments the fix added (strip list in the key)
    python3 - "$f" <<'EOF'
import sys
f = sys.argv[1]
s = open(f).read()
blocks = [
"""        // A barrier is needed because of the use of wildcard receives. This
        // avoids successive calls interfering with each other.
""",
"""
        // Increment the mpi_tag for this round of messages to ensure messages
        // are processed in the correct order from the previous round of Isends
        // and Irecvs.
""",
"""        // No barrier is needed because all ranks know who they are receiving
        // and sending to.
""",
"""        // No barrier is needed because all ranks know who they are receiving
        // from and sending to.

""",
]
counts = [2, 2, 1, 1]
for b, c in zip(blocks, counts):
    n = s.count(b)
    assert n == c, (n, b)
    s = s.replace(b, '')
open(f, 'w').write(s)
EOF
}
s_377dc2() { local t; t=$(fresh 377dc2); untar $HI/stempy/stempy-3.3.5.tar.gz "$t"; mkdir -p "$t/driver"; cp $DRV/harness.cpp "$t/driver/"; }
s_245580() { local t; t=$(fresh 245580); untar $HI/stempy/stempy-3.3.6.tar.gz "$t"; mkdir -p "$t/driver"; cp $DRV/harness.cpp "$t/driver/"
    sed -i 's|^  // First get msg size using the source rank$|  // First get the message size|' "$t/stempy/electron_mpi.cpp"
    grep -q '^  // First get the message size$' "$t/stempy/electron_mpi.cpp" || { echo "stempy strip failed" >&2; return 1; }; }
s_ab5082() { local t; t=$(fresh ab5082); untar $HI/mfem332/mfem-3.3.2.tar.gz "$t"; mkdir -p "$t/driver"; cp $DRV/twomesh.cpp "$t/driver/"; }
s_1c72c6() { local t; t=$(fresh 1c72c6); untar $HI/mfem332/mfem-3.4.tar.gz "$t"; mkdir -p "$t/driver"; cp $DRV/twomesh.cpp "$t/driver/"
    bash $W/scripts/mfem34-strip.sh "$t"; }
lammps_tree() { untar "$1" "$2"; mkdir -p "$2/driver"; cp $DRV/in.displace "$2/driver/"; }
s_12506a() { local t; t=$(fresh 12506a); lammps_tree $HI/lammps/lammps-3a2da51a8210970a2382185f003fd512c12bada9.tar.gz "$t"; }
s_50e789() { local t; t=$(fresh 50e789); lammps_tree $HI/lammps/lammps-411c069ba66fb10b0e1121d0499b7e888cac9c80.tar.gz "$t"
    local f="$t/src/irregular.cpp"
    sed -i -e 's|^    MPI_Request tmpReq; // Use non-blocking send to avoid possible deadlock$|    MPI_Request tmpReq;|' \
           -e 's|^    MPI_Request_free(&tmpReq); // the MPI_Barrier below marks completion$|    MPI_Request_free(\&tmpReq);|' "$f"
    [ "$(grep -c 'deadlock\|marks completion' "$f")" = 0 ] || { echo "lammps strip failed" >&2; return 1; }; }
hypre3_tree() { untar $NB/hypre-3.2.0.tar.gz "$1"; local ex="$1/src/examples"
    sed -e 's/^   int vis, print_system;/   int vis, print_system;\n   int sai_levels = 1;/' \
        -e 's/^         else if ( strcmp(argv\[arg_index\], "-vis") == 0 )/         else if ( strcmp(argv[arg_index], "-sai_levels") == 0 )\n         {\n            arg_index++;\n            sai_levels = atoi(argv[arg_index++]);\n         }\n         else if ( strcmp(argv[arg_index], "-vis") == 0 )/' \
        -e 's/int      sai_max_levels = 1;/int      sai_max_levels = sai_levels;/' "$ex/ex5.c" > "$ex/ex5_levels.c"
    [ "$(diff "$ex/ex5.c" "$ex/ex5_levels.c" | grep -c '^>')" = 7 ] || { echo "ex5 patch failed" >&2; return 1; }; }
s_21631d() { local t; t=$(fresh 21631d); hypre3_tree "$t"; }
s_fc1e24() { local t; t=$(fresh fc1e24); hypre3_tree "$t"; local p="$t/src/distributed_ls/ParaSails/ParaSails.c"
    sed -i -e 's/^#define ROW_PRUNED_REQ_TAG        221$/#define ROW_PRUNED_REQ_TAG        221\n#define ROW_PRUNED_REQ_TAG_EVEN   227\n#define PRUNED_REQ_TAG(level) ((level) % 2 ? ROW_PRUNED_REQ_TAG : ROW_PRUNED_REQ_TAG_EVEN)/' \
           -e 's/SendRequests(comm, ROW_PRUNED_REQ_TAG,/SendRequests(comm, PRUNED_REQ_TAG(level),/' \
           -e 's/ReceiveRequest(comm, &source, ROW_PRUNED_REQ_TAG,/ReceiveRequest(comm, \&source, PRUNED_REQ_TAG(level),/' "$p"
    [ "$(grep -c 'PRUNED_REQ_TAG(level),' "$p")" = 6 ] || { echo "ParaSails patch failed" >&2; return 1; }; }
slu_tree() { untar $SL/superlu_dist-9f7c8196.tar.gz "$1"; mkdir -p "$1/driver"; cp $SL/mats/blk12x6.mtx "$1/driver/"; }
s_77ec6a() { local t; t=$(fresh 77ec6a); slu_tree "$t"; }
s_86c6b6() { local t; t=$(fresh 86c6b6); slu_tree "$t"; patch -s -p1 -d "$t" < $PH/superlu-barrier-nocomment.patch || return 1; }
qe_tree() { untar $QE/q-e-615e40cf.tar.gz "$1"; mkdir -p "$1/driver"; cp $QE/run/k39/ph.in $QE/run/k39/ni.scf.in "$1/driver/"; }
s_d9668c() { local t; t=$(fresh d9668c); qe_tree "$t"; }
s_fd3e23() { local t; t=$(fresh fd3e23); qe_tree "$t"; patch -s -p1 -d "$t" < /h/newcase/stretto/experiments/qeelph/spin-tags.patch || return 1; dropline "$t/PHonon/PH/elphon.f90" "  ! The tags depend on the spin: with LSDA the two spins of the k point can be held by two" && dropline "$t/PHonon/PH/elphon.f90" "  ! different pools, which send at the same time, and the receives below take any source."; }
foam_tree() { git -C $H/nbx-hunt/src/openfoam archive --format=tar HEAD | tar xf - -C "$1"
    mkdir -p "$1/driver/nbxExchange/Make"; cp $DRV/nbxExchange.C "$1/driver/nbxExchange/"
    printf 'nbxExchange.C\n\nEXE = $(FOAM_USER_APPBIN)/nbxExchange\n' > "$1/driver/nbxExchange/Make/files"
    printf 'EXE_INC =\n\nEXE_LIBS =\n' > "$1/driver/nbxExchange/Make/options"; }
s_8d0015() { local t; t=$(fresh 8d0015); foam_tree "$t"; }
s_dcaf36() { local t; t=$(fresh dcaf36); foam_tree "$t"; }
libsc_tree() { git -C $H/probe-hunt/src/libsc archive --format=tar HEAD | tar xf - -C "$1"; mkdir -p "$1/driver"; cp $DRV/notify2.c "$1/driver/"; }
libsc_fix() { patch -s -p1 -d "$1" < $H/probe-hunt/drivers/libsc/fix.diff 2>/dev/null || patch -s -p1 -d "$1" < $PH/libsc-fix.diff; }
s_1bff96() { local t; t=$(fresh 1bff96); libsc_tree "$t"; }
s_65f689() { local t; t=$(fresh 65f689); libsc_tree "$t"; patch -s -p1 -d "$t" < $PH/libsc-fix.diff || return 1; }
s_c869a1() { local t; t=$(fresh c869a1); libsc_tree "$t"; }
s_10a10b() { local t; t=$(fresh 10a10b); libsc_tree "$t"; patch -s -p1 -d "$t" < $PH/libsc-fix.diff || return 1; }
swift_tree() { cp -a $H/probe-hunt/src/swift_t/. "$1/"; mkdir -p "$1/driver"; cp $DRV/adlbsub.c "$1/driver/"; }
s_61a354() { local t; t=$(fresh 61a354); swift_tree "$t"; }
s_64c6e0() { local t; t=$(fresh 64c6e0); swift_tree "$t"; patch -s -p1 -d "$t" < $PH/swift-fix-nocomment.diff || return 1; }
s_c06ed6() { local t; t=$(fresh c06ed6); untar $NB/hypre-3.2.0.tar.gz "$t"; }
s_175dd3() { local t; t=$(fresh 175dd3); mkdir -p "$t/dune-common" "$t/dune-istl"
    git -C $H/probe-hunt/src/dune_common archive --format=tar HEAD | tar xf - -C "$t/dune-common"
    git -C $H/probe-hunt/src/dune_istl archive --format=tar HEAD | tar xf - -C "$t/dune-istl"; }
s_3b67af() { local t; t=$(fresh 3b67af); untar $NB/petsc-3.26.0.tar.gz "$t"; }
s_8fa246() { local t; t=$(fresh 8fa246); untar $NB/mfem-4.10.tar.gz "$t"; }
s_e3cf4d() { local t; t=$(fresh e3cf4d); untar $NB/dealii-9.8.0.tar.gz "$t"; dealii_tests "$t" $NB/dealii98/dealii-9.8.0; }
s_224d88() { local t; t=$(fresh 224d88); git -C $H/bughunt-new/src/trilinos archive --format=tar HEAD packages/zoltan | tar xf - -C "$t"; }

mkdir -p $W/tmp
ids=("$@"); [ ${#ids[@]} = 0 ] && ids=(a55842 520bef 0d199a ccd0e2 9e1162 55816f 8dba4d ec7c94 8df9ee a7d8e5 377dc2 245580 ab5082 1c72c6 12506a 50e789 21631d fc1e24 77ec6a 86c6b6 d9668c fd3e23 8d0015 dcaf36 1bff96 65f689 c869a1 10a10b 61a354 64c6e0 c06ed6 175dd3 3b67af 8fa246 e3cf4d 224d88)
rc=0
for i in "${ids[@]}"; do
    if "s_$i"; then echo "$i staged $(du -sh "$ST/$i" | cut -f1)"; else echo "$i FAILED"; rc=1; fi
done
exit $rc
