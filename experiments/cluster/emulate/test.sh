#!/usr/bin/env bash
# The two-node test, on node1 of experiments/cluster/emulate/run.sh: the cluster's jobs, submitted
# with sbatch to the emulation's Slurm and waited for. Steps:
#   build    tracer, replay library and idioms for both MPI libraries (8 jobs); NPB MG 2.4.1 and
#            3.3.1 at 16 ranks if missing (experiments/npbmg/run.sh)
#   matrix   which launches start a two-node MPI program: srun --mpi=pmix and pmi2 with either
#            library, and Open MPI's mpirun inside the allocation (OUT/launch-matrix.txt)
#   srun     slurm.sbatch with LAUNCHER=srun PMI=pmix RUNTIME=apptainer (the emulation's stand-in,
#            emulate/apptainer): setup and idioms under Open MPI
#   mpirun   slurm.sbatch with LAUNCHER=mpirun RUNTIME=native: setup and idioms under both
#            libraries, and the documented bug NPB MG 2.4.1 at 16 ranks (8 per node, MPICH)
#            through the campaign's run.py and replays.py
#   bimpl    the eager limits across the nodes and on one (BIMPL_MPIS, default both libraries),
#            under LAUNCHER=mpirun; each bisection step that hangs waits 10 s, so this is the
#            longest step (about 5 minutes per send path and setting)
# Output: OUT (default build/cluster-emulate), under the repository.
# usage: experiments/cluster/emulate/test.sh [build matrix srun mpirun bimpl]   (default: all of them)
set -euo pipefail
R="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$R"
OUT="${OUT:-$R/build/cluster-emulate}"
export STRETTO_COMMIT="${STRETTO_COMMIT:-unknown}" MACHINE="${MACHINE:-emulation}"
mkdir -p "$OUT"

step_build() {
    local m
    for m in openmpi mpich; do
        make -s -j8 -C tracer MPI=$m && make -s -j8 -C replay MPI=$m && make -s -j8 -C idioms MPI=$m
    done
    [ -x third_party/apps/npb-mg/NPB2.4.1/NPB2.4-MPI/bin/mg.S.16 ] || NPS=16 experiments/npbmg/run.sh fetch build
}
step_matrix() {
    local hello="$OUT/hello-mpich" src="$OUT/hello.c"
    cat > "$src" <<'EOF'
#include <mpi.h>
#include <stdio.h>
int main(int c, char **v) {
    int r, n, x;
    MPI_Init(&c, &v);
    MPI_Comm_rank(MPI_COMM_WORLD, &r); MPI_Comm_size(MPI_COMM_WORLD, &n);
    x = r; MPI_Allreduce(MPI_IN_PLACE, &x, 1, MPI_INT, MPI_SUM, MPI_COMM_WORLD);
    if (r == 0) printf("%d ranks, sum %d\n", n, x);
    MPI_Finalize();
    return 0;
}
EOF
    mpicc.mpich -o "$OUT/hello-mpich" "$src"; mpicc.openmpi -o "$OUT/hello-openmpi" "$src"
    sbatch --wait -N 2 --ntasks-per-node=8 -o "$OUT/launch-matrix.txt" --wrap "
        for m in openmpi mpich; do
            for how in 'srun --mpi=pmix' 'srun --mpi=pmi2' '/usr/bin/mpirun.openmpi --mca oob_tcp_if_include eth0 --mca btl_tcp_if_include eth0 -x UCX_NET_DEVICES=eth0'; do
                case \"\$how\" in srun*) where='-N 2 -n 4';; *) where='-np 4 --map-by node';; esac
                out=\$(UCX_NET_DEVICES=eth0 OMPI_MCA_btl_tcp_if_include=eth0 timeout 30 \$how \$where $OUT/hello-\$m 2>&1 | grep -v ORTE_ERROR_LOG | tail -1)
                echo \"\$m | \${how%% --mca*} | \${out:-no output}\"
            done
        done" || true
    cat "$OUT/launch-matrix.txt"
}
submit() {   # submit <name> VAR=VALUE ...: slurm.sbatch with those settings, waited for
    local name="$1"; shift
    env "$@" OUT="$OUT/$name" LOCAL=/scratch NET=eth0 RUNS="${RUNS:-2}" REPLAYS="${REPLAYS:-6}" \
        sbatch --wait -N 2 --ntasks-per-node=8 -o "$OUT/slurm-$name.out" experiments/cluster/slurm.sbatch \
        || echo "test.sh: job $name ended with status $?" >&2
    tail -3 "$OUT/slurm-$name.out"
}
step_srun() {
    : > "$OUT/stand-in.sif"        # the stand-in apptainer runs the command as it is
    submit srun LAUNCHER=srun PMI=pmix RUNTIME=apptainer SIF="$OUT/stand-in.sif" MPIS=openmpi STEPS="build setup idioms"
}
step_mpirun() {
    submit mpirun LAUNCHER=mpirun RUNTIME=native MPIS="openmpi mpich" STEPS="build setup idioms subjects" SUBJECTS="npbmg:np16"
}
step_bimpl() {
    submit bimpl LAUNCHER=mpirun RUNTIME=native MPIS="${BIMPL_MPIS:-openmpi mpich}" STEPS="setup bimpl"
}
for s in ${@:-build matrix srun mpirun bimpl}; do
    echo "== $s"
    "step_$s"
done
