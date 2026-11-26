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

## Open until the cluster's details are known

| question | what depends on it |
|---|---|
| Apptainer or Singularity on the compute nodes? (`module avail`) | `RUNTIME=apptainer` (`APPTAINER=singularity` takes the same options), else `RUNTIME=native` with `modules.sh`: another MPI, another configuration |
| Slurm's MPI plugins (`srun --mpi=list`), its PMIx version | `PMI`; whether the image's MPICH starts under `srun --mpi=pmix` (it hung in the emulation, below) |
| MPI modules: an Open MPI 4.1 with Slurm support? an MPICH? | `LAUNCHER=mpirun` natively; with only an MPICH `mpiexec` (hydra syntax), `LAUNCHER=srun` |
| interconnect: Ethernet, InfiniBand, Omni-Path, Slingshot | Open MPI's `pml` (`ob1` or `ucx`), UCX's transports, the eager limits, `NET` |
| partitions, nodes per job, wall time, jobs in the queue, exclusive nodes | N per cell and cells per job; `--exclusive` in `slurm.sbatch` |
| node-local scratch: does it exist, where (`$TMPDIR`, `/scratch`, `/tmp`), how large | `LOCAL`; traces of NPB MG class A and Nekbone size (tens of GB) |
| Python 3.10+ outside the image (a module) | the harness under `RUNTIME=apptainer`, and `modules.sh` |
| cores, hardware threads and sockets per node | ranks per node, binding as on one node; PaRSEC's two virtual processes |
| the shared home's quota | the subjects' builds (third_party/apps) and kept traces; copy results off after each job (no backup) |

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
