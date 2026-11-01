# Smilei: OpenMP threads that post their own patch exchanges

Target 2 of the third thread hunt (1-2 Nov 2026), a blind scan like Global Arrays'
([experiments/ga](../ga/README.md)): the shipped benchmarks are traced and analysed first, the
findings committed (the pre-registration), and only then is the communication code read at the
reported sites. The reason to look here (an audit, unverified before the scan): Smilei's OpenMP
threads post their own MPI exchanges under `MPI_THREAD_MULTIPLE`, and its tags are built from
decimal digits and truncated to `int` (its GitHub issue 307).

Smilei master 61aa917 (14 Sep 2026), its default build (OpenMP on, `MPI_THREAD_MULTIPLE`, which
every run's log confirms) with gcc 13, parallel HDF5 1.10 of Ubuntu 24.04 and Open MPI 4.1.6 or
MPICH 4.2.0 (`build.sh`, 77 and 85 s with 8 jobs by its clock), MACHINE=a-p13, CPUs 0-7,40-47, in the
experiment image plus HDF5, Python headers, h5py and matplotlib (`Dockerfile`). What was read
before the pre-registration: the makefile, the installation page, the validation harness
(`validation/easi`), the benchmarks' names and namelists (which of them balance load), and the
function names and line numbers that `stretto sites` prints for the call sites of the traces.

## Two tracer settings Smilei needed (docs/threads.md, section 17)

The first traced run of the 1D benchmark `tst1d_00_em_propagation` (4 ranks of 2 threads)
overflowed the tracer's chain table: Smilei enters a few parallel regions with MPI calls per
time step, and each region's thread 1 was a chain of its own (thousands per rank). And every
benchmark writes its diagnostics through parallel HDF5, so `MPI_File_open` marked each trace
unmodelled (observed findings only). Both are fixed by settings, off by default, with which
every run here was traced: `STRETTO_OMP_CHAINS=thread` (one chain per OpenMP thread across a
chain's regions; 57cad0c) and `STRETTO_MPIIO=coll` (MPI-IO's collective calls as barrier-shaped
collectives on a duplicate of the file's communicator; 85fe6cd). With them each rank has 2
chains and the traces are complete.

## Sizing pass (pilot)

`scan.sh` with one traced Open MPI run of every benchmark that has a validation script (173),
the trace measured and deleted, 1 Nov 21:39 to 2 Nov 00:01 +08 (scripts before 2436eb9, the
tracer of 85fe6cd; a first attempt before it had a broken validation and is not tabulated):
[size-openmpi.csv](size-openmpi.csv) (result, trace size, seconds of run and validation,
whether the scan took it). An analysis takes about 15 times its trace in memory and 0.85 s per
MB (a 634 MB trace of `tst2d_04_laser_wake`: 9 min, 9.2 GB, no finding), so the scan took the
benchmarks whose run passed, failed its validation or hung with a trace of at most 150 MB with
both MPIs (57) and of 150-400 MB with Open MPI only (28): 85 of 173. Left out: 19 with
400-700 MB, 37 above 700 MB, 19 killed when their trace passed 1.5 GB, and 13 that do not run
here: 11 validation scripts whose input is not in `benchmarks/` (the GPU ones, kept in
`benchmarks/gpu`), `tstAM_04c_laser_propagation_file` (needs mpi4py) and
`tstAM_picsar_04_laser_wake` (needs a PICSAR build). Two runs of the sizing pass failed their
validation (`tst2d_v_o2_radiation_pressure_acc`, also in 3 of 3 untraced runs, and
`tstAM_04b_laser_propagation_numpy`), both above 1 GB and not scanned; three hung
(`tst3d_24_envelope_Laguerre_Gauss_from_xmin` and `tst3d_25_envelope_Hermite_Gauss_from_xmin_reduced_dispersion`
with 7 MB traces, and `tst3d_v_o4_laser_wake_boris`, still writing at 900 s).

## Blind scan (pre-registration)

Recorded before any of Smilei's communication code was read (`src/Patch`, `src/SmileiMPI`, the
exchange and diagnostic classes); what was read is listed at the top.

**What ran.** `scan.sh` at commit 2436eb9 (analysis code of ac02b15, unchanged since), 2 Nov
00:03-06:04 +08, MACHINE=a-p13, CPUs 0-7,40-47: the 85 benchmarks of the sizing pass, 4 ranks
of 2 OpenMP threads (`OMP_SCHEDULE=dynamic`), 3 traced runs each, with Open MPI (all 85) and
with MPICH (the 57 with traces of at most 150 MB), 426 runs; each run checked by Smilei's
validation against its reference, each trace analysed at L1 (`stretto analyze --json`, at most
an hour) and its sites named (`stretto sites`). Tables:
[scan-openmpi-runs.csv](scan-openmpi-runs.csv), [scan-mpich-runs.csv](scan-mpich-runs.csv)
(one row per run: result, trace size, report status, findings by level and class, the commit)
and `scan-*-sites.csv` (one row per run and site with a finding: its source line in Smilei's
tree and its counts). `summary.py` groups them by site and class. Every run's log says
`MPI_THREAD_MULTIPLE enabled` and 2 threads per process, except the six of
`tst3d_10_em_propagation_external_field_tabulated`, killed before they printed it (below).
Deviations, all recorded in the tables or here:

- The Open MPI scan was stopped at 02:45 +08 and restarted for the benchmarks it had not
  finished, with more memory for its analyses (one at a time had run, the memory budget per
  analysis being too low); the runs of a benchmark cut by the stop were made again.
- The wait that holds the runs while traces queue for analysis counted the traces kept after
  their analysis (those of at most 60 MB), and stopped the MPICH runs at 03:30 +08; the kept
  traces were moved out of the scan's directory (to `keep/`, the same files) and the runs went
  on. `scan.sh` now counts only traces still to be analysed (after the scan).
- 13 analyses of Open MPI traces of 294-359 MB were killed by the container's memory cap (three
  at once); those 13 runs were traced and analysed again, one at a time, 06:08-06:57 +08, and
  their rows are the second runs'.
- MPICH's container ran with a memory cap of 10 GB until 02:45 +08 and 8 GB after, Open MPI's
  13 GB and then 15 GB, a helper container 4 GB and then 1 GB (24 GB in all after 02:45; 27 GB
  of caps before, with at most 6 GB in use when checked).

**What the traces hold.** No trace has a wildcard receive or a probe: every receive names its
source and tag, on the world communicator, posted by the main thread or by thread 1 of a rank
(2 chains per rank; requests are often completed by the other thread, premise P-T9). A race
the analysis can predict is then two receives of one rank, on concurrent chains, that accept
one message (same source, tag and communicator), or a message that a send on another chain
makes acceptable first: a tag shared by two exchanges in flight.

**Findings, by site.** Counts per trace, both MPIs alike unless said.

| # | Site | Class, level | Benchmarks (traces with it) | Per trace |
|---|---|---|---|---|
| H1 | (observed) | `abort` (rank 0 stopped outside MPI before `MPI_Finalize`), `2-blocked` (the six other chains blocked in a wait), `1c` (79 sends never received), `2-buffering` (the observed run deadlocks below 2 304 buffered bytes) | `tst3d_24_envelope_Laguerre_Gauss_from_xmin`, `tst3d_25_envelope_Hermite_Gauss_from_xmin_reduced_dispersion`: every run hangs (600 s), 12 of 12 traced runs, both MPIs; untraced (Open MPI) 1 of 1 still at the first time step after 1 800 s, while the other 32 3D benchmarks of the scan took 6 to 156 s traced, with validation | 1, 6, 79, 1 |
| S1 | `Patch::initExchange` at `src/Patch/Patch.cpp:944` | 1f at L1 | the 12 traces of H1 only | 31 |
| F1 | (observed) | `abort` | `tst3d_10_em_propagation_external_field_tabulated`: 6 of 6 runs killed by signal 9 (exit 137) in containers of 8 to 15 GB; it passed in the sizing pass's 24 GB container | 1 to 4 |

No finding of any class in the other 408 traces (82 benchmarks with Open MPI, 54 with MPICH):
their reports are `complete` with no alternative at L1 and no observed error. Every run of
them passed its validation.

**Checks made before reading the code** (no code read for them). S1's alternatives, read in
the report: each pairs a receive posted and still pending when the run was stopped with a
send to its rank that is pending too and has its source, tag, size and communicator (the
records show them on the world communicator, tags such as 2706 on both): the receive's own
message, which MPI would match, but whose completion the stopped run never recorded. H1's
untraced run above.

**Questions for the reading** (step 3): H1, what does rank 0 compute, and is it a hang or a
very slow step at 2 threads per rank (the references were made with more)? S1, is each
alternative the receive's own message, so that the class is the end of a stopped run rather
than a race? Why no finding anywhere else: are the tags of concurrent exchanges distinct by
construction in these benchmarks, and where would the digit-concatenated tags of issue 307
collide (more patches, more fields, more species)? F1, memory only?
