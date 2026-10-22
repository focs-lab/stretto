# MPI-PRV as a baseline

MPI-PRV is the tool of "Efficient Predictive Monitoring of Message Passing Interface Programs"
(ISSTA 2026, doi 10.1145/3832278). It instruments a C program at the source (wac: aspectcc and a
MOP file of events and one pattern), runs it once, and analyses the event trace offline
(`MPI-PRV -trace F -pattern P -np N`): it reports whether some reordering of the trace that its
dependences allow contains the pattern's events in order.

These scripts run its artifact image unchanged and compare it with Stretto. Nothing of the
artifact is copied into this repository; the scripts reference the files inside the image.

## The image

Docker Hub `mpiprv/mpiprv`, the artifact's README's `docker pull mpiprv/mpiprv:latest`, pinned
here by digest:

```
mpiprv/mpiprv@sha256:af980da29375ffca673d1e5a16aa8c1d06a9d69211e79b6f948a137ba95c43cf
image id 60489cb67c59, 24 GB, Ubuntu 24.04, Open MPI 4.1.6-7ubuntu2 (the package of
experiments/container/), clang 15 (aspectcc), MUST 1.9.0, MPI-SV
```

The analyser is the binary `/usr/local/bin/MPI-PRV` (identical to
`/home/ubuntu/MPI-PRV/MPI_PRV/MPI-PRV`). Its source, `MPI_Prediction.cpp`, is not in the final
image: a later layer deletes it. An earlier layer still holds it (dated a day before the
binary, with the `install.sh` that compiles it into `MPI-PRV`); the behaviour the probes below
measure is the binary's.

`experiments/mpiprv/prv.sh <command>` runs a command in the image with the repository at `/w`,
on processors `CPUS` (default: every processor; the runs of E9 had four, `26-27,66-67` of
the second machine) and `MEMORY` (default 16 GB). The image keeps its tool paths in
`/root/.bashrc`, which the wrapper sources. The artifact is meant to run
`--privileged`; the wrapper adds only `--cap-add SYS_PTRACE`, without which Open MPI's
single-copy transfers fail and the RMA program of the artifact's property 6 hangs.

## Steps

Each script writes its table into `experiments/results/mpiprv/`, every row with the code
commit (`COMMIT` at the root of the deployed copy, `experiments/provenance.py`), and keeps its
builds and runs under `experiments/mpiprv/work/` (git-ignored). Stretto's tracer and pin
library must be built for Open MPI first (`make -C tracer MPI=openmpi`, `make -C replay
MPI=openmpi`, in the experiment image).

| script | what | table |
|---|---|---|
| `rq1.sh rq1` | the artifact's RQ1 script, `TestallProp.sh`, five rounds | `rq1.csv`, `rq1.log` |
| `rq1.sh adept` | its motivating example: `MPI-PRV.sh` on the shipped trace, and fresh runs of the shipped instrumented kernel | `adept-prv.csv`, `adept-prv.log` |
| `rq1.sh rq2` | its RQ2/RQ3 measurement of MPI-PRV: the analyser on each application's shipped trace, and whether the pattern is reported | `rq2.csv`, `rq2.log` |
| `probes.sh` | correct programs that ask for a reordering no execution has: a wildcard receive, a barrier, unrelated events | `probes.csv`, `probes-*.trace` |
| `adept.sh` | the Adept Kernel report checked: the result under the reordering MPI-PRV predicts, forced with delays (`adept_shim.c`), a mutant as the positive control, and Stretto's buffer guard | `adept.csv`, `adept-guard.txt`, `adept-mutant.diff` |
| `run.py` | MPI-PRV on Stretto's corpora, three arms (below); `--rq1-check` first checks the combined MOP file on the artifact's RQ1 programs | `corpus.csv`, `corpus-rq1check.csv` |
| `apps.py` | Stretto, plain and with the guard, on the artifact's applications, with replays | `apps.csv`, `apps-replays.csv` |
| `stretto_corpus.py` | Stretto with the buffer guard on `run.py`'s programs, twins included, in the experiment image | `stretto-corpus.csv` |

`artifact/reproduce.sh rq2` runs all of it after the corpora (`MPIPRV=0` leaves it out): the
image takes 24 GB of disk, the runs about 6 GB and 3.5 hours on four processors, most of them
the Adept check (45 minutes) and the applications (1 h 45 min). By hand, as the paper's runs
were made:

```bash
# on the second machine, in the deployed copy (experiments/RUNBOOK.md)
docker pull mpiprv/mpiprv@sha256:af980da29375ffca673d1e5a16aa8c1d06a9d69211e79b6f948a137ba95c43cf
IMAGE=stretto-exp:24.04 CPUS=26-27,66-67 DETACH=0 experiments/container/run.sh \
  bash -c "make -C tracer MPI=openmpi && make -C replay MPI=openmpi"
experiments/mpiprv/prv.sh experiments/mpiprv/rq1.sh            # rq1 adept rq2
experiments/mpiprv/prv.sh experiments/mpiprv/probes.sh
experiments/mpiprv/prv.sh experiments/mpiprv/adept.sh          # about 45 minutes
experiments/mpiprv/prv.sh python3 experiments/mpiprv/run.py --rq1-check
experiments/mpiprv/prv.sh python3 experiments/mpiprv/run.py    # needs the corpora (third_party/fetch.sh corpora)
experiments/mpiprv/prv.sh python3 experiments/mpiprv/apps.py
IMAGE=stretto-exp:24.04 CPUS=26-27,66-67 DETACH=0 experiments/container/run.sh python3 experiments/mpiprv/stretto_corpus.py
python3 experiments/tables.py                                  # section E9 of docs/results.md
```

The tables are summarised in section E9 of [`docs/results.md`](../../docs/results.md).

## The corpus arms (`run.py`)

Programs: MPI-BugBench level 2's LocalConcurrency and Correct programs (those of
`experiments/results/mbb/mbb.csv`), MBI's families of the bug classes the ten properties name
(point-to-point LocalConcurrency, ReqLifecycle, ResLeak, with their correct versions), and the
twin of each local-concurrency error: the labelled access moved after the call that completes
its operation (`markers.py twin`), labelled correct.

Every program is instrumented with one MOP file that defines every event the shipped
properties use, run once, and its trace analysed once per pattern. The patterns are the
artifact's RQ1 ones, plus, for properties 2 and 3, the other member of the union in the
paper's table (the shipped analyser reads a single pattern per file):

| arm | patterns | markers |
|---|---|---|
| a | properties 1 (`MPIFinalize Irecv`), 6, 7, 9: every event an MPI call | none |
| b | properties 2 (`Isend Write Recv`, `Isend Write Irecv`), 3 (`Irecv Use Isend`, `Irecv Use Send`), 8 (`Recvinit Start write Wait`) | `markers.py blind`: before every statement that names the buffer of a pending nonblocking operation, without reading the labels |
| c | as b | `markers.py oracle`: at the labelled lines only, and at the moved lines of the twins; an upper bound |

Programs that use probes, or whose label is a message race or a buffering hazard, are outside
MPI-PRV's declared scope and are listed apart, not scored. The table also lists, per program,
the MPI calls that the shipped instrumentation does not record (it records `MPI_Send`,
`MPI_Recv`, `MPI_Isend`, `MPI_Irecv`, `MPI_Wait`, `MPI_Barrier` and the MOP's events).
