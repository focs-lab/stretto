#!/usr/bin/env bash
# Submit the scale series (experiments/cluster/scale.sh) as plain batch jobs, from a login node of
# the SoC cluster in the repository's checkout, and return: nothing of it stays on the login
# node. The jobs, chained by dependencies:
#   build                 every application at every count (normal partition, one node)
#   per application:      runs (all threads of 2 xcni nodes) -> analyse (one xcnf node, 1 TB) -> replays (2 nodes)
#   step 3 on four nodes  NPB MG at 32 ranks and HYPRE at 8, the campaign's protocol (docs/cluster.md, "Runs")
#   collect               after all of them: experiments/cluster/collect.sh keeps every job's CSVs in
#                         experiments/results/cluster/ and pushes them to the branch cluster-runs
#   APPS     the applications (default "hypre amg lu mg-bug mg-fix")
#   NPS      process counts (default "16 32 64 128 256")
#   STEP3    1 (default): the step-3 runs on four nodes too; 0: not
#   EARLIER  finished jobs whose results collect keeps as well (default: step 3's last three)
#   DRY      1: print the sbatch commands, submit nothing (no --test-only checks either)
#   EMULATE  1: the same chain on the two-node emulation (experiments/cluster/emulate/), at 4 and 8
#            ranks on class S, with small jobs, the image's MPICH, and collect without git
# usage: experiments/cluster/submit-scale.sh        (from the repository root on xlogin)
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
APPS="${APPS:-hypre amg lu mg-bug mg-fix}"
export NPS="${NPS:-16 32 64 128 256}"
STEP3="${STEP3:-1}"
EARLIER="${EARLIER:-1218019 1218021 1218022}"
export MACHINE=soc-cluster RUNTIME=native LAUNCHER=mpirun
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "submit-scale.sh: the checkout has changes; commit or stash them" >&2; exit 1; }
[ "${EMULATE:-0}" = 1 ] || [ "$(git rev-parse --abbrev-ref HEAD)" = cluster-runs ] \
    || { echo "submit-scale.sh: check out the branch cluster-runs first (collect.sh pushes there)" >&2; exit 1; }
mkdir -p build/cluster
B=experiments/cluster/slurm.sbatch
ids=()
sub() {   # sub <name> <environment...> -- <sbatch options...>: submit, print, remember the id
    local name=$1; shift
    local envs=()
    while [ "$1" != -- ]; do envs+=("$1"); shift; done; shift
    if [ "${DRY:-0}" = 1 ]; then
        echo "env ${envs[*]} sbatch -J $name $* $B" >&2
        echo "<$name>"
    else
        # a job whose dependency can no longer be met (its build failed) is cancelled, not left
        # pending, so that collect, after all of them, still runs
        env "${envs[@]}" sbatch --parsable --kill-on-invalid-dep=yes -J "$name" "$@" "$B"
    fi
}
# the jobs' shapes; every one is checked with sbatch --test-only before anything is submitted
O_BUILD=(-p normal -N 1 --ntasks-per-node 4 -c 8 --mem 64G -t 01:30:00)   # modules.sh runs a 3-rank check
# every hardware thread of two xcni nodes (the QOS's 256 CPUs, so no other job shares their
# processors); --exclusive is not permitted for users, and DefMemPerCPU times 128 exceeds a node
O_RUNS=(-p long -N 2 --ntasks-per-node 128 --mem 200G -t 04:00:00)
O_ANALYSE=(-p long -C xcnf -N 1 --ntasks-per-node 1 -c 4 --mem 500G -t 1-00:00:00)
O_REPLAYS=(-p long -N 2 --ntasks-per-node 128 --mem 200G -t 12:00:00)        # the trace is loaded again
O_S3MG=(-N 4 --ntasks-per-node 8 --mem-per-cpu 2G -t 02:30:00)
O_S3HY=(-p long -N 4 --ntasks-per-node 2 --mem-per-cpu 2G -t 08:00:00)
O_COLLECT=(-p normal -N 1 -n 1 -c 1 --mem 4G -t 00:30:00)
if [ "${EMULATE:-0}" = 1 ]; then
    pre="$R/build/cluster-emulate/mpich-prefix"; mkdir -p "$pre/bin"
    for w in mpicc.mpich mpicxx.mpich mpifort.mpich mpichversion; do ln -sf "/usr/bin/$w" "$pre/bin/$w"; done
    export NPS="4 8" CLASS=S CORES=2 MPICH_PREFIX="$pre" NET=eth0 LOCAL=/scratch MACHINE=emulation SAMPLE=4 COLLECT_NO_GIT=1
    EARLIER=""; STEP3=0
    O_BUILD=(-N 1 --ntasks-per-node 4 -t 01:00:00); O_RUNS=(-N 2 --ntasks-per-node 8 -t 01:00:00)
    O_ANALYSE=(-N 1 --ntasks-per-node 1 -t 01:00:00); O_REPLAYS=(-N 2 --ntasks-per-node 8 -t 01:00:00)
    O_COLLECT=(-N 1 -n 1 -t 00:10:00)
fi
if [ "${DRY:-0}" != 1 ]; then
    for shape in O_BUILD O_RUNS O_ANALYSE O_REPLAYS O_COLLECT $( [ "$STEP3" = 1 ] && echo O_S3MG O_S3HY); do
        declare -n o=$shape
        sbatch --test-only "${o[@]}" --wrap true > /dev/null 2>&1 \
            || { echo "submit-scale.sh: the cluster refuses the $shape job's options: ${o[*]}" >&2
                 sbatch --test-only "${o[@]}" --wrap true; exit 1; }
    done
    # a refusal half-way cancels what was submitted before it
    trap '[ ${#ids[@]} -gt 0 ] && scancel "${ids[@]}"; echo "submit-scale.sh: failed; cancelled ${ids[*]:-nothing}" >&2' ERR
fi
build=$(sub stretto-scale-build STEPS="setup scalebuild" -- "${O_BUILD[@]}")
ids+=("$build"); echo "build      $build"
for app in $APPS; do
    runs=$(sub "stretto-scale-$app" APP="$app" STEPS="setup scaleruns" RUNS=5 -- "${O_RUNS[@]}" --dependency=afterok:"$build")
    ids+=("$runs")
    an=$(sub "stretto-scale-$app-analyse" APP="$app" STEPS="setup scaleanalyse" FROM="$R/build/cluster/$runs" \
         -- "${O_ANALYSE[@]}" --dependency=afterany:"$runs")
    ids+=("$an")
    rep=$(sub "stretto-scale-$app-replays" APP="$app" STEPS="setup scalereplays" FROM="$R/build/cluster/$runs" \
          PLAN="$R/build/cluster/$an" -- "${O_REPLAYS[@]}" --dependency=afterany:"$an")
    ids+=("$rep"); printf '%-10s runs %s, analyse %s, replays %s\n' "$app" "$runs" "$an" "$rep"
done
if [ "$STEP3" = 1 ]; then
    mg=$(sub stretto-s3-mg32x4 STEPS="setup subjects" SUBJECTS="npbmg:np32" RUNS=5 REPLAYS=10 \
         -- "${O_S3MG[@]}" --dependency=afterok:"$build")
    ids+=("$mg")
    hy=$(sub stretto-s3-hypre-x4 STEPS="setup subjects" SUBJECTS="hypre:np8 hypre3:np8" RUNS=5 REPLAYS=10 \
         -- "${O_S3HY[@]}" --dependency=afterok:"$build")
    ids+=("$hy"); echo "step 3     $mg $hy"
fi
deps=$(IFS=:; echo "${ids[*]}")
if [ "${DRY:-0}" = 1 ]; then
    echo "sbatch -J stretto-collect ${O_COLLECT[*]} --dependency=afterany:$deps experiments/cluster/collect.sh $EARLIER ${ids[*]}" >&2
else
    col=$(sbatch --parsable --kill-on-invalid-dep=yes -J stretto-collect "${O_COLLECT[@]}" --dependency=afterany:"$deps" \
          -o build/cluster/slurm-%j.out --wrap "experiments/cluster/collect.sh $EARLIER ${ids[*]}")
    trap - ERR      # everything is submitted: nothing after this cancels it
    echo "collect    $col (pushes the results to cluster-runs when every job above has ended)"
    squeue -u "$(id -un)" -o "%.10i %.28j %.9T %.10l %.20R" | head -40
fi
