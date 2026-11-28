#!/usr/bin/env bash
# The multi-node job: experiments/cluster/job.sh on the allocation's first node. Submit from the
# repository root on the shared home, from a login node (nothing heavy runs there), with the
# job's settings in the environment (job.sh lists them: LAUNCHER, PMI, RUNTIME, SIF, PYTHON,
# NET, MACHINE, STEPS, ...), for example
#   MACHINE=<cluster> sbatch --nodes 2 experiments/cluster/slurm.sbatch
#       (srun --mpi=pmix, every rank in build/sif/stretto-exp_24.04.sif)
#   MACHINE=<cluster> RUNTIME=native LAUNCHER=mpirun STEPS="build setup idioms" sbatch --nodes 2 experiments/cluster/slurm.sbatch
#       (the system's Open MPI and experiments/cluster/mpich.sh's MPICH, or the cluster's MPI
#       modules loaded below from MODULES; the SoC cluster's way)
# Ranks start through Slurm only: srun, or an mpirun that uses srun itself; never ssh. The
# #SBATCH lines are defaults, the command line overrides them. The defaults fit the SoC cluster
# (docs/cluster.md, "The SoC cluster"): partition normal, whose limit is 3 h (a longer job goes to
# -p long, up to 3 days), and one node family, xcni (-C xcnf for the other). Whole nodes are
# asked for on the command line (--ntasks-per-node 128 and an explicit --mem; users may not ask
# for --exclusive), for runs whose timing counts: two nodes of 128 threads take the user's
# whole QOS of 256, so a 4-node job shares its nodes.
#   MODULES  modules to load first, space-separated (for example "apptainer python/3.12", or a
#            compiler and an MPI for RUNTIME=native)
#SBATCH --job-name=stretto-cluster
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=8
#SBATCH --partition=normal
#SBATCH --constraint=xcni
#SBATCH --time=02:00:00
#SBATCH --output=build/cluster/slurm-%j.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
# the subnet of MPI's traffic between the nodes: unset, Open MPI's daemons and MPICH's UCX try
# both of the nodes' subnets and hang (docs/cluster.md)
export NET="${NET:-192.168.48.0/21}"
mkdir -p build/cluster
if [ -n "${MODULES:-}" ]; then
    # shellcheck disable=SC2086
    module load $MODULES
fi
echo "job $SLURM_JOB_ID on $SLURM_JOB_NODELIST, $SLURM_NTASKS_PER_NODE tasks per node; srun $(srun --version)"
srun --mpi=list 2>&1 | tr '\n' ' '; echo
exec experiments/cluster/job.sh
