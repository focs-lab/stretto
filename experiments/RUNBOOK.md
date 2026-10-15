# Experiment runbook

## Where experiments run

The development host is shared and reserved for another project's benchmark legs, so the
experiments that execute MPI programs at scale run on a second machine inside a container
with the development host's userland (`experiments/container/Dockerfile`: Ubuntu 24.04,
Open MPI 4.1.6, MPICH 4.2.0, gcc 13, Python 3.12). Traces, replays and analysis are therefore
comparable across the two. The second machine is shared as well: use it for runs and
analysis, not for timing claims.

```bash
# on the development host: copy the repository and the inputs to the second machine's tmpfs
git ls-files > /tmp/files.txt
rsync -a --files-from=/tmp/files.txt . $REMOTE:/dev/shm/rematch/
rsync -a --relative --exclude bin/ traces third_party/mpi-bugbench/scripts/gencodes $REMOTE:/dev/shm/rematch/
rsync -a --relative --exclude main experiments/results/mbi experiments/results/amg/trace8 \
      experiments/results/e4/e4.csv experiments/results/e4/trace.* $REMOTE:/dev/shm/rematch/

# on the second machine: build, test, then every remaining step, detached
cd /dev/shm/rematch
IMAGE=rematch-exp:24.04 experiments/container/run.sh bash -c \
  "experiments/run_remaining.sh > experiments/results/run-remaining.log 2>&1"

# MUST baseline, on the second machine's host (its own container, MUST 1.9.2 + MPICH)
MUST_CPUS=0-11,40-51 python3 experiments/mbi/run_must.py experiments/results/mbb/mbb.csv --jobs 3 --timeout 240

# back on the development host: fetch the tables and regenerate docs/results.md
rsync -a $REMOTE:/dev/shm/rematch/experiments/results/ experiments/results/
python3 experiments/tables.py
```

`run_remaining.sh` steps: `build tests mbb e2 e6 amg e4 e3`; any subset can be given.
The MUST image is MPI-BugBench's (`third_party/mpi-bugbench/Dockerfile.must`); it can be
copied between machines with `docker save mbb-must:latest | gzip -1 | ssh $REMOTE 'gunzip |
docker load'`. Any `docker build` on the development host needs
`--cgroup-parent=docker.slice`.

## Shared-host protocol on the development host

Jobs that may exceed 20 GiB or use 8 sustained cores run one at a time through
`scripts/heavy.sh <cap> <cmd>`, which takes the machine lock and a MemoryMax scope (never
MemoryHigh: the host has no swap) and pins to the processors outside the benchmark and
operating-system sets. Nothing runs during an announced timed benchmark leg.

## Notes

- The NPB Fortran kernels are traced only when built with the MPICH wrappers: Open MPI's
  Fortran bindings call `PMPI_*` directly. A tree first built against Open MPI must be
  cleaned completely (`make veryclean` also removes `config/make.def`, which then has to be
  rewritten).
- Busy-polling loops (`MPI_Iprobe`, `MPI_Test` until a flag is set) are summarised by the
  tracer; before that, single MPI-BugBench programs produced 3 GB traces.
