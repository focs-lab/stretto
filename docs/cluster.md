# Multi-node runs

Every result so far ran on one node, in the experiment image (Open MPI 4.1.6, MPICH 4.2.0, gcc 13)
or the MUST image built from it. This file says what runs on several nodes of the cluster must
show, how they are run, and what is still open. The cluster runs Slurm; users log in only to its
login nodes, the compute nodes take no ssh, and the one home directory, shared by every node, has
no backup. The tools are in [`experiments/cluster/`](../experiments/cluster/README.md); a
two-node Slurm emulation on one machine checked them (last section).

## What the runs must show

1. **The predictions do not depend on one node.** A trace from a multi-node run predicts the
   same bug as one from a single node, at the same level (L1, L1+SP, L1+L), and forced replays
   across nodes confirm it by the same criterion (every pin matched, the predicted outcome, the
   control correct). The analysis reads ranks, not hosts; what changes is the schedule: messages
   from another node arrive later, and the observed run is another one.
2. **The buffering threshold B is the transport's.** A finding realisable only below an eager
   limit (LAMMPS's deadlock below 4 bytes; HYPRE 3.2.0's 1 340-byte reply, which must be
   buffered; deal.II 13206 below 40-248 bytes) is judged against the limit of the path its
   messages take: shared memory inside a node, the network between nodes. The runs measure both
   limits (`job.py bimpl`), and every alternative is marked by whether its message crosses nodes
   (the trace's `node` field; `analyses.csv` and `replays.csv`, column `cross_node`).
3. **The comparison holds where testing changes.** Plain runs, random delays (D50) and NINJA
   reveal schedule-dependent bugs at rates that move with latency and placement. The one-node
   table is repeated per configuration across nodes, with the same N and with timeouts
   calibrated on the cluster (RULE.md, Section 2.1), as a block of its own, not an amendment of
   the frozen campaign.

## Subjects

The 11 MPI bugs of the campaign (RULE.md, Section 4) and the 4 thread bugs. "Across nodes" is the
configuration to run, nodes x ranks per node. All run first at the campaign's process counts
spread over 2 nodes by node (every rank's neighbours remote), then larger where the input allows.

| bug | campaign | across nodes | input for more ranks | buffering |
|---|---|---|---|---|
| HYPRE 2.10.1 ParaSails, tag 222 (MPICH) | `ij -solver 8`, np 4, 8 | 2x4; 4x8, 8x8 | `ij -n` grows the grid; more ranks, more neighbours with requests in flight | no |
| HYPRE 3.2.0 `ex5`, two levels (Open MPI) | np 4, 8 | 2x4; 4x8 | `-n` per rank stays 33; the race is per level and neighbour | 1 340 B reply must be buffered: below every default limit |
| PETSc `ex2` on HYPRE 3.2.0 | np 4, 8 | 2x4; 4x8 | `-m -n` (48x48) to 200x200 at 32 ranks | as above |
| NPB MG 2.4.1, wrong-k message (MPICH) | class S, np 16, 32 | 2x8, 4x8; class A at 64, 128 | power-of-two ranks; class A or B keeps every rank busy | no |
| deal.II 9.3.0-rc1 `RemotePointEvaluation` (MPICH) | `mapping_01`, np 4 | 2x2 only | the expected output exists for 4 ranks; more need a new one | no |
| deal.II NBX, issue 8929 (Open MPI) | `nbx_calls 3`, np 4, 8 | 2x4; 4x8 | the driver checks its own answers at any np | no |
| Cabana before PR 835 (Open MPI) | `Test10NoTopo`, np 3, 4 | 2x2 (3 ranks: 2+1) | a unit test; more ranks add nothing | deadlock at L1+SP; no limit |
| stempy 3.3.5 `gatherEvents` (Open MPI) | `256 12 5`, np 4, 8 | 2x4; 4x8 | every rank but 0 is a worker; scan positions (first argument) to 1 024 at 32 ranks | no |
| MFEM 3.3.2 group rows (Open MPI) | two-mesh driver, np 4, 8 | 2x4; 4x8 | the meshes' sizes `nx1 ny1 nx2 ny2` (16 16 30 7) grow with the ranks | the truncation abort is a symptom |
| LAMMPS before March 2017 (Open MPI) | `in.displace`, np 4, 8; eager 0 | 2x4; 4x8 | `in.displace`'s box | deadlock below 4 B: never at a default limit; the eager-0 row needs the network's interface (below) |
| SuperLU_DIST 9.3.1 3D solve (both) | 4 matrices, np 3 (1x3x1), 6 (2x3x1) | 3x1, 2x3 | grids `-r -c -d` up to 2x3x2; the matrices are small | no |
| Quantum ESPRESSO `elph_prt` (MPICH) | Ni LSDA, `-nk 3`, np 3 | 3x1 (one pool per node) | `-nk 3`, 2 ranks per pool: 3x2 | no |
| Uintah + hypre, concurrent collectives (threads) | `sus -nthreads 2, 4`, 4 ranks | 2x2 | patches per rank | no |
| SLATE CALU tags, `getrf` (threads) | 4 ranks x 4 threads, 2x2 and 4x1 grids | 2x2 | p x q grids up to 4x4 | no |
| DLA-Future `matrix::copy` tags (threads) | 2-6 ranks | 2x1, 2x2 | grid rows P > 1; both tiles between the same two ranks | no |
| PaRSEC reshapes, two virtual processes (threads) | 1, 4 ranks, synthetic two-socket topology | 2x1, 2x2 | a real two-socket node gives the two virtual processes (`runtime_vpmap hwloc`) | no |

PaRSEC's racing messages stay inside a rank (`MPI_COMM_SELF`): it gains nothing from the network
and runs to check that a real two-socket node reproduces what the synthetic topology did. The
MPICH subjects need a launch that starts the image's MPICH across nodes (below).

## What changes across nodes

- **Transport.** Inside a node Open MPI uses `vader` (shared memory), MPICH its ch4 shared-memory
  module. Between nodes Open MPI uses `ob1` with `btl tcp` on Ethernet, or `pml ucx` once UCX
  finds an InfiniBand or RoCE device; the image's MPICH (ch4:ucx) uses UCX's choice of transport.
- **Eager limits, hence B.** Open MPI 4.1.6, measured (`b_impl.py`): 4 040 bytes on one node
  (vader; 256 bytes inline on `MPI_Send`/`MPI_Isend` whatever the limit), 65 480 bytes between
  two nodes over TCP (`btl_tcp_eager_limit` 65 536 less its header), 8 bytes with the TCP limit
  forced to 64. MPICH: 650 633 bytes on one node (`b_impl_mpich.csv`), more than 1 MiB between
  two nodes over UCX's TCP transport (the emulation). Under `pml ucx` the
  `btl_*_eager_limit` parameters do nothing: UCX's `UCX_RNDV_THRESH` decides, and forcing the
  limit down needs `--mca pml ob1 --mca btl self,tcp` or `UCX_RNDV_THRESH`. The campaign's LAMMPS
  eager-0 configuration (`subjects.py` EAGER0) names the loopback interface, which carries
  nothing between nodes: `job.py protocol` sets it to the job's interface (`NET`) before
  `run.py` runs, without editing the frozen file.
- **Timing.** Latency between nodes is microseconds instead of hundreds of nanoseconds, and a
  rank's neighbours are remote in a by-node placement: the observed matchings change, and so do
  the rates of plain and delayed runs. The tracer records no time, the replay library's gates
  measure only local intervals, and no part of the analysis compares two nodes' clocks.
- **Placement.** The campaign binds every rank to its own hardware thread of one node
  (`run.py launcher_env`). Across nodes the job places ranks by node (rank r on node r mod n) or
  by slot (PPN ranks per node in turn; `run.py` under `job.py protocol`), binds each to its own
  core, and records which (`map` column).

## How the runs are made

- **Launch: through Slurm only.** Two launchers, a parameter of the job (`LAUNCHER`):
  `srun --mpi=pmix|pmi2` for every rank (`srun-mpirun`, which takes the harness's Open MPI
  `mpirun` lines, so that `stretto.confirm`, `b_impl.py` and `run.py` launch unchanged), or the
  MPI's own `mpirun` inside the allocation, which starts its daemons with `srun`. With the image
  (`RUNTIME=apptainer`) only `srun` can work: an `mpirun` inside the image has no `srun` to start
  daemons with and may not ssh, so each rank is `srun ... apptainer exec <sif> env <vars> program`,
  and the harness (Python 3.10+, standard library only) runs outside the image. Natively
  (`RUNTIME=native`, `modules.sh`) both work. The ranks' variables (`LD_PRELOAD` of the tracer,
  pins) are set by `env` in front of the program, never in `srun`'s own environment.
- **Files.** The repository, the builds and the job's output are on the shared home, at the same
  path on every node. Traces of the job's own traced runs go to a node-local directory (`LOCAL`)
  and are collected after the run (one copy step per node; trace files are `rank-<r>.jsonl`,
  unique by world rank, so the nodes' parts never collide; the tracer creates the directory with
  its parents on every node). The harness's own temporary files (pin files of long horizons,
  replay traces, `run.py`'s run and trace directories, `b_impl.py`'s program) must be visible
  on every node: the steps run with `TMPDIR` on the shared home and Open MPI's session directory
  node-local (`orte_tmpdir_base=/tmp`, it holds sockets).
- **Replays.** A pin names the sender by world rank and the operation by communicator, tag and
  call-site offset in its object, none of which depends on the node; a pin file reaches the
  ranks that cannot read it from one that can. Replays and controls run on the same nodes in the
  same placement as the traced run (`job.py replay`, `job.py protocol-replays`).
- **Rows.** Every row has the campaign's columns (`host` from `MACHINE`, `commit`, ...) and the
  job's (`nodes, ppn, map, scheduler, job`); `job.json` has the node names, launcher, runtime,
  versions of both MPIs, srun and Python, and the image's path and sha256.

## Tools

| tool | across nodes | expected difference from one node |
|---|---|---|
| Stretto (trace, analysis, replay) | as on one node; traces collected from the nodes | other observed runs, the same predicted bug; B judged per path |
| MUST 1.11.2 | `mustrun --must:mpiexec <launcher>`; its tool processes need task slots beside the ranks | `run.py must_layout` counts the 36 run threads of one node: per node the layout's processes must fit the node's tasks (a cluster block); `mustrun` under `srun-mpirun` is untried |
| NINJA (random, system, app) | preloaded on every rank; the learning run's files are in the run's directory, on the shared home | its noise adds to a larger latency: rates may rise or fall |
| random delays (D50) | unchanged | the same |

## The SoC cluster

Probed 28 Nov 2026 (`experiments/cluster/probe.sh`, jobs 1216684 on xcnf0 and 1216685 on xcni0,
one node each), with the limits read from `sinfo` and `sacctmgr` on a login node.

| question | answer | what follows |
|---|---|---|
| Apptainer or Singularity on the compute nodes? | neither (nor Docker, Podman, Charliecloud, Enroot); no `module` command, `MODULEPATH` empty | `RUNTIME=native`, `MODULES` empty; `sif.sh`'s image does not run here |
| Slurm's MPI plugins, its PMIx | Slurm 26.05.2; `srun --mpi=list`: none, pmi2, pmix (pmix_v5), cray_shasta; `NamespaceType=job_container/tmpfs` (a private `/tmp` per job, from `/temp`) | `srun --mpi=pmix` starts neither library (table below): `LAUNCHER=mpirun` |
| MPI on the nodes | the system's Open MPI 4.1.6 (Ubuntu 4.1.6-7ubuntu2, the image's version), with `plm slurm`, `ess slurm`, `pmix ext3x` on PMIx 5.0.1, `btl tcp, vader, self, ofi, openib`, UCX 1.16 runtime; no MPICH | Open MPI: the system's; MPICH: the image's own packages (Ubuntu 4.2.0-5build3, ch4:ucx, PMIx client only), unpacked without root by `experiments/cluster/mpich.sh` from the image's snapshot, on the system's UCX and PMIx (the image's versions) |
| compilers, Python | gcc/g++/gfortran 13.3.0, clang 18.1.3, CMake 3.28.3, Python 3.12.3, git 2.43: the image's versions; no Spack | the system toolchain, as in the image |
| OS | Ubuntu 24.04 (24.04.5 on xcnf, 24.04.4 on xcni), kernel 6.8.0, glibc 2.39; `ptrace_scope` 2, ASLR on | the image's distribution |
| interconnect | Ethernet only: two 10 GbE ports up per node (192.168.48.0/21 and 192.168.56.0/21, both on both families), MTU 1500; no InfiniBand, RoCE or Omni-Path device | Open MPI `pml ob1`, `btl tcp`; MPICH over TCP; `NET` one of the two subnets, the same on every node of a job |
| nodes | `normal` (3 h, default 15 min), `long` (3 days, default 5 h); families xcnc/xcnd (24 threads, 2x6x2, 63 GB, 1 GbE), xcne (80, 2x20x2), xcnf (AMD EPYC 7763, 128 threads, 1x64x2, one NUMA node, 1 TB), xcni (2x AMD EPYC 9355, 128 threads, 2x32x2, two NUMA nodes, 256 GB), xcnh (512); a family is chosen with `-C` | one family per job (`-C xcnf` or `-C xcni`), never a node list; xcni has the two sockets PaRSEC's virtual processes need |
| jobs per user | QOS `normal`: 256 CPUs (hardware threads) per user over all running jobs; 16 running, 32 submitted | `--exclusive` fits 2 nodes of xcnf or xcni, not 4: 4-node jobs take at most 64 threads per node and share the nodes; one job at a time at that size |
| node-local scratch | `/tmp` (`TMPDIR=/tmp`), XFS on NVMe, 3.2 TB free on both families; wiped from time to time | `LOCAL=/tmp/...`, copied off within the job |
| the shared home | NFS (`cfs`), 24 TB free of 192 TB; no `quota` command, no backup | builds and traces fit; results leave by git |

**Which launch starts a two-node program** (`experiments/cluster/launch-matrix.sbatch`, 4 ranks
by node on xcni0 and xcni1, an `MPI_Allreduce`; jobs 1216780, 1216833, 1216871 on 28 Nov 2026):

| launch | Open MPI 4.1.6 | MPICH 4.2.0 |
|---|---|---|
| `srun --mpi=pmix` | segfault in `MPI_Init`: `PMIX_ERR_FILE_OPEN_FAILURE` in `pmix_shmem.c` | the same |
| `srun --mpi=pmix`, `PMIX_MCA_gds=hash` | `MPI_Init` fails: the ORTE session directory cannot be made | hangs: Slurm's server refuses the client's request for rank -1 (`pmixp_dmdx.c: ... asked for -1`) |
| `srun --mpi=pmix`, `SLURM_PMIX_TMPDIR` on the shared home (with or without the hash store and direct connections) | `task launch failed` | the same |
| `srun --mpi=pmi2` | `MPI_Init` fails (no PMI-2 client in Ubuntu's build) | every rank a singleton (PMIx client only) |
| hydra `mpiexec -launcher slurm` | | every rank a singleton (hydra serves PMI-1/2, the package's MPICH speaks PMIx only) |
| Open MPI's `mpirun`, no network setting | hangs (no output): the daemons try both subnets | hangs after `pmix_server_fence ... Not found` |
| Open MPI's `mpirun`, TCP on 192.168.48.0/21 | **works** | hangs |
| the same, MPICH's UCX on that subnet's interface (`UCX_NET_DEVICES`, optionally `UCX_TLS=tcp,self,sm`) | | **works** |
| Open MPI's `mpirun` on one node | | works |

So both libraries start across nodes as in the image: Open MPI's `mpirun` inside the allocation,
its daemons started with `srun`, with Open MPI's TCP traffic on one subnet and UCX on that
subnet's interface. `slurm.sbatch` sets `NET=192.168.48.0/21`, and `job.py setup` resolves it to
the node's interface for UCX (the same name on every node of one family: `enp225s0f0np0` on
xcni, `enp131s0f0` on xcnf). Direct launch with `srun` stays open: the private `/tmp` of
`job_container/tmpfs` hides the PMIx server's files from its clients, and only the cluster's
administrators can move them; nothing here needs it.

**Build and idioms** (job 1216902, `RUNTIME=native LAUNCHER=mpirun`, 2 nodes, 3 runs each): the
tracer, replay library and idioms build against both libraries inside the job; idioms 01, 04
and 05 at 3 ranks by node (ranks 0 and 2 on xcni0, rank 1 on xcni1, as every init record says)
report what they report on one node and in the emulation. Idiom 04's deadlock is confirmed by
every replay under both libraries, each control finishing; idiom 05's truncation by every
replay under Open MPI, and under MPICH not confirmed for the reason known from one node (exit
status 14, not read as `MPI_ERR_TRUNCATE`). The alternatives of the three idioms take their
message from rank 2, on rank 0's node: none crosses nodes.

## The two-node emulation

`experiments/cluster/emulate/run.sh up test down`: two containers of the experiment image with
Slurm 23.11 (slurmctld, slurmd, munge, PMIx 5), each with its own host name, network, IPC
namespace, `/tmp` and node-local `/scratch`, sharing only the repository; jobs go in with
`sbatch`. Run on 26 Nov 2026, 8 CPUs per node, MPI over TCP between the containers.

**Which launch starts a two-node program** (`test.sh matrix`, 4 ranks, an `MPI_Allreduce`):

| library | `srun --mpi=pmix` | `srun --mpi=pmi2` | Open MPI's `mpirun` in the allocation |
|---|---|---|---|
| Open MPI 4.1.6 | works | fails in `MPI_Init` (no PMI-2 client in the image's build) | works |
| MPICH 4.2.0 (ch4:ucx, PMIx) | hangs in `MPI_Init` across nodes (works on one node) | every rank a singleton | works (prints harmless `ORTE_ERROR_LOG ... pmix_server_fence` lines) |

So in the image Open MPI programs run under `srun --mpi=pmix`, and MPICH programs need Open MPI's
`mpirun` with Slurm's launcher, which an `mpirun` inside the image cannot use. Whether the
cluster's Slurm (another PMIx) starts the image's MPICH is the first thing its setup step
checks; if it does not, the MPICH subjects run natively against the cluster's MPICH, or with
the host's `srun` bound into the image.

**Eager limits** (`b_impl.py`, two ranks): Open MPI 4 040 bytes on one node, 65 480 bytes between
the two nodes (TCP, every send path), 8 bytes with the TCP limit forced to 64; the same under
ssh and under `srun`. MPICH between the nodes (UCX over TCP): no exchange hung up to 1 MiB on
any path, as on one node with UCX restricted to TCP (650 633 bytes through shared memory).

**Jobs.** `LAUNCHER=srun PMI=pmix RUNTIME=apptainer` (the stand-in) with Open MPI, and
`LAUNCHER=mpirun RUNTIME=native` with both libraries, idioms 01, 04 and 05 at 3 ranks by node
(ranks 0 and 2 on one node, 1 on the other), 2 plain and 2 traced runs each:

- every run finished; every traced run's three rank files were collected from the two nodes'
  `/scratch`, and each init record names its node;
- the reports equal those of the one-node traces (idiom 01: 1f at L1, 1c and 1d at L1+SP at
  B = 0, the latter not confirmed at the default limit, as on one node);
- idiom 04's deadlock (1d, 1c at L1+SP) is confirmed by every replay across the nodes under both
  launchers and both libraries, each control finishing; idiom 05's truncation (1a) by every
  replay under Open MPI, each replay's own trace judged a run of the program (strict verdict).
  Under MPICH the truncation ends the replay with exit status 14 and the message "Message
  truncated" (MPICH's error handler then crashes in UCX), which `classify_rc` does not read as
  `MPI_ERR_TRUNCATE`: not confirmed, exactly as on one node (checked the same day), so a
  harness gap of its own, not a multi-node one.

**A documented bug across nodes**: NPB MG 2.4.1 class S at 16 ranks, 8 per node, MPICH, through
the campaign's `run.py` and `replays.py` (files unchanged), `LAUNCHER=mpirun`:

| | bug (2.4.1) | fix (3.3.1) |
|---|---|---|
| plain runs with a wrong residual | 0 of 2 | 0 of 2 |
| D50 runs with a wrong residual | 0 of 2 | 0 of 2 |
| traced runs that predict the bug | 2 of 2 (160 alternatives each, all 160 taking a message from the other node) | 0 of 2 |
| replays (6 of a sample of 40 of 160 distinct) | 4 confirmed (another residual, every pin matched, control correct), 2 not confirmed (the residual unchanged; in one the control computed another residual) | |

On one node 384 of 480 replays changed the residual: the rate is alike. The rows carry the job's
columns (`nodes 2, ppn 8, host emulation`).

**What broke across nodes, and what was done**

| what | where | done |
|---|---|---|
| the tracer made only the last directory of `STRETTO_TRACE_DIR`: a node-local path prepared on one node left the other node's ranks without a trace | tracer | fixed: parents created (test `NodeLocalFiles`) |
| a pin file (`NAME=@file`, horizons over 16 KiB) written in the harness's temporary directory could not be read on the other node: every rank there aborted | replay library | fixed: the lowest rank that reads it broadcasts it (test with two working directories in one run) |
| the harness's temporary files (replay traces, `run.py`'s run and trace directories, `b_impl.py`'s program) live in `/tmp`, which is per node | harness | `TMPDIR` on the shared home in every step; Open MPI's session directory kept in `/tmp` |
| `b_impl.py`'s TCP setting named the loopback; its compiler and launcher were the image's paths | experiments | fixed: `STRETTO_TCP_IF`, `STRETTO_MPICC_<MPI>`, `STRETTO_MPIRUN`, `--settings` (tests `EagerTcp`) |
| the campaign's eager-0 configuration (`subjects.py` EAGER0) names the loopback; `run.py`'s launcher is a constant | frozen protocol | not edited: `job.py protocol` sets both before `run.py` runs |
| which ranks share a node was not in the trace | tracer | added: `node` in the init record, `Trace.node` |
| the ranks' `LD_PRELOAD` and pins would reach `srun` itself through its environment, and pins hold commas, which `srun --export` splits | launch | `srun-mpirun` sets them with `env` in front of the program (test `ClusterJob`) |
| MPICH under `srun --mpi=pmix`, and both libraries under `pmi2` | image and Slurm | open (table above) |
| Open MPI's ssh launcher (an earlier variant of the emulation) prefixes its remote command with a shell line, which an `apptainer exec` prefix breaks | launch | moot: the cluster allows no ssh; noted for clusters that do |

Not run in the emulation: MUST and NINJA across nodes, the thread bugs, `sif.sh`'s image under a
real Apptainer (only its packages were checked), and any timing (8 CPUs per node on one host).
