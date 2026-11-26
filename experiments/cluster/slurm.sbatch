#!/usr/bin/env bash
# The multi-node job: experiments/cluster/job.sh on the allocation's first node. Submit from the
# repository root on the shared home, from a login node (nothing heavy runs there), with the
# job's settings in the environment (job.sh lists them: LAUNCHER, PMI, RUNTIME, SIF, PYTHON,
# NET, MACHINE, STEPS, ...), for example
#   MACHINE=<cluster> sbatch --nodes 2 experiments/cluster/slurm.sbatch
#       (srun --mpi=pmix, every rank in build/sif/stretto-exp_24.04.sif)
#   MACHINE=<cluster> RUNTIME=native LAUNCHER=mpirun STEPS="build setup idioms" sbatch --nodes 2 experiments/cluster/slurm.sbatch
#       (the cluster's MPI modules, loaded below from MODULES)
# Ranks start through Slurm only: srun, or an mpirun that uses srun itself; never ssh. The
# #SBATCH lines are defaults, the command line overrides them; account, partition and QOS are
# the cluster's own (sbatch -A, -p, -q).
#   MODULES  modules to load first, space-separated (for example "apptainer python/3.12", or a
#            compiler and an MPI for RUNTIME=native)
#SBATCH --job-name=stretto-cluster
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=8
#SBATCH --exclusive
#SBATCH --time=04:00:00
#SBATCH --output=build/cluster/slurm-%j.out
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p build/cluster
if [ -n "${MODULES:-}" ]; then
    # shellcheck disable=SC2086
    module load $MODULES
fi
echo "job $SLURM_JOB_ID on $SLURM_JOB_NODELIST, $SLURM_NTASKS_PER_NODE tasks per node; srun $(srun --version)"
srun --mpi=list 2>&1 | tr '\n' ' '; echo
exec experiments/cluster/job.sh
