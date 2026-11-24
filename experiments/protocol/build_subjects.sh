#!/usr/bin/env bash
# Every build the uniform campaign runs (experiments/protocol/RULE.md), from the repository root
# inside the experiment image (stretto-exp:24.04): Stretto's tracer and replay library for both
# MPI libraries, NINJA's libraries (experiments/ninja/build.sh, the campaign's
# libninja-fixed-all.so among them), and each subject of RULE.md Section 4 at its version with the
# bug and its fix, by the subject's own fetch and build steps, into their default directories
# under third_party/apps/. One build of each program serves every tool: MUST runs in
# stretto-must-u:24.04, which is this image with MUST added (experiments/protocol/must/).
#   SUBJECTS   which subjects (default: all of them, the names below)
#   MAKE_JOBS  make jobs of each build (default 8)
#   STEPS      fetch, build or both (default "fetch build")
# Each subject's log goes to third_party/apps/protocol-build-<subject>.log; a failed subject is
# reported and the next one is built.
# usage: experiments/protocol/build_subjects.sh [tools|ninja|<subject> ...]
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
export MAKE_JOBS="${MAKE_JOBS:-8}"
STEPS="${STEPS:-fetch build}"
ALL="tools ninja hypre hypre3 petsc npbmg dealii dealii8929 cabana stempy mfem332 lammps superlu3d qeelph"
LOGS="$R/third_party/apps"; mkdir -p "$LOGS"

steps_of() {   # the commands of one subject and step, one per line
    local s=$1 step=$2
    case "$s:$step" in
        tools:fetch) ;;
        tools:build) for d in tracer replay; do for m in openmpi mpich; do echo "make -s -C $d MPI=$m"; done; done;;
        ninja:fetch) echo "experiments/ninja/build.sh fetch";;
        ninja:build) echo "experiments/ninja/build.sh build";;
        hypre:*) echo "experiments/hypre/run.sh $step";;
        hypre3:*) echo "experiments/hypre3/run.sh $step"; echo "VARIANT=fix experiments/hypre3/run.sh $step";;
        petsc:*) echo "experiments/petsc/run.sh $step"; echo "VARIANT=fix experiments/petsc/run.sh $step";;
        npbmg:*) echo "NPS='16 32' experiments/npbmg/run.sh $step";;
        dealii:*) echo "experiments/dealii/run.sh $step";;
        qeelph:build) echo "experiments/qeelph/run.sh build"; echo "experiments/qeelph/run.sh inputs";;
        superlu3d:*|qeelph:*|dealii8929:*|cabana:*|stempy:*|mfem332:*|lammps:*) echo "experiments/$s/run.sh $step";;
        *) echo "no subject $s" >&2; return 2;;
    esac
}
failed=()
for s in ${SUBJECTS:-${*:-$ALL}}; do
    log="$LOGS/protocol-build-$s.log"; : > "$log"
    t0=$SECONDS ok=1
    for step in $STEPS; do
        cmds=$(steps_of "$s" "$step") || { ok=0; break; }
        while IFS= read -r c; do
            [ -n "$c" ] || continue
            echo "+ $c" >> "$log"
            bash -c "$c" < /dev/null >> "$log" 2>&1 || { ok=0; echo "FAILED: $c" >> "$log"; break; }
        done <<< "$cmds"
        [ $ok = 1 ] || break
    done
    if [ $ok = 1 ]; then echo "$s: ok ($((SECONDS - t0)) s)"; else echo "$s: FAILED ($((SECONDS - t0)) s; $log)"; failed+=("$s"); fi
done
[ ${#failed[@]} = 0 ] || { echo "failed: ${failed[*]}"; exit 1; }
