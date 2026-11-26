# Multi-node runs on the cluster

What the runs must show, and what is open until the cluster's details are known:
[docs/cluster.md](../../docs/cluster.md). The cluster runs Slurm; users reach only its login
nodes, and the compute nodes take no ssh, so every rank starts through Slurm: `srun`, or an
`mpirun` that uses `srun` itself. The launcher and the container runtime are parameters
(`LAUNCHER`, `PMI`, `RUNTIME`), since neither the MPI modules nor Apptainer are known yet.

| file | what it does |
|---|---|
| [sif.sh](sif.sh) | the experiment images as Apptainer images (`build/sif/*.sif`, `manifest.tsv` with the Docker image id and the .sif's sha256), built on this machine with Apptainer in a container from `docker save` archives; checks that every package of the image is in the .sif at its version |
| [modules.sh](modules.sh) | the fallback without containers: tracer, replay library, re-execution shim and idioms built against the cluster's MPI module (`MPICC`), a trace of idiom 04 analysed on the cluster's Python |
| [slurm.sbatch](slurm.sbatch) | the job script: loads `MODULES`, prints Slurm's MPI plugins, runs `job.sh` |
| [job.sh](job.sh) | the job's steps (`build setup bimpl idioms subjects`) under `LAUNCHER` (srun, mpirun), `PMI` (pmix, pmi2) and `RUNTIME` (apptainer, native) |
| [job.py](job.py) | the steps: `setup` (`job.json`, `mpi.env`, a probe of every node), `bimpl` (eager limits across nodes and on one), `run` (plain, traced, delays; traces from node-local disks collected), `analyse`, `replay` (stretto.confirm on the same nodes), `protocol` and `protocol-replays` (the campaign's `run.py` and `replays.py` on the nodes, their files unchanged) |
| [srun-mpirun](srun-mpirun) | the harness's mpirun under `LAUNCHER=srun`: the Open MPI `mpirun` syntax the harness writes (`-np`, `-x`, `--mca`, `--map-by`) as an `srun --mpi=...` step, with the ranks' variables set by `env` inside the image's prefix |
| [emulate/](emulate/) | a two-node Slurm cluster in two containers on one machine, and the test that submits the jobs to it |

## On the cluster, the first day

```bash
# here: the images, then the repository and build/sif/ to the cluster's shared home
experiments/cluster/sif.sh
# there, on a login node: submit only; builds and runs go into jobs
MACHINE=<cluster> MODULES="apptainer python/3.12" STEPS="build setup bimpl idioms" \
  sbatch -p <partition> --nodes 2 experiments/cluster/slurm.sbatch
# without Apptainer: the cluster's MPI, mpirun inside the allocation (or srun with LAUNCHER=srun)
MACHINE=<cluster> MODULES="gcc openmpi python" RUNTIME=native LAUNCHER=mpirun STEPS="build setup bimpl idioms" \
  sbatch -p <partition> --nodes 2 experiments/cluster/slurm.sbatch
```

The first job's `setup` step records which launch works (`job.json`, `probe`); `bimpl` measures
the eager limits between nodes; `idioms` runs three idioms traced, analysed and replayed across
nodes. Then the campaign's subjects, once their builds exist on the shared home
(`experiments/protocol/build_subjects.sh` in a job): `STEPS=subjects SUBJECTS="npbmg:np16 hypre:np8 ..."`.

Results land in `OUT` (default `build/cluster/<job id>/`): `job.json`, `b_impl-<mpi>-{nodes,node}.csv`,
per program `runs.csv`, `analyses.csv`, `replays.csv` and every run's output and trace, and for
the subjects `protocol/results/<subject>/<tool>[-<setting>].csv` in the campaign's columns plus
the job's. The shared home has no backup: copy `OUT` off the cluster after each job.

PBS Pro, if another cluster needs it: the same `job.sh` runs from a PBS script once
`PBS_NODEFILE`'s distinct hosts are passed as `job.py setup --hosts` and the launcher is the
MPI's own `mpirun` (PBS's TM interface, which the image's Open MPI lacks; ssh where allowed).

## The two-node emulation

```bash
experiments/cluster/emulate/run.sh up       # builds stretto-slurm:24.04 if needed, starts node1 and node2
experiments/cluster/emulate/run.sh test     # build matrix srun mpirun: sbatch jobs on the two nodes
experiments/cluster/emulate/run.sh down
```

Each node is a container with its own host name, network, IPC namespace, `/tmp` and a tmpfs
`/scratch` (its node-local disk); the repository is mounted at `/w` in both (the shared home).
Slurm 23.11 runs in them (slurmctld on node1, slurmd on both, munge), and jobs go in with
`sbatch` as on the cluster. `apptainer` in the image is a stand-in that runs its command as it
is (the nodes already are the image), so that `RUNTIME=apptainer` runs with its prefix.
Results of 26 Nov 2026: [docs/cluster.md](../../docs/cluster.md), last section.
