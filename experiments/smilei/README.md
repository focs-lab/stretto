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

## Two tracer settings Smilei needed (docs/threads.md, section 18)

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

## Verdicts (after the pre-registration, from reading the code at the sites)

Read from 07:00 +08: `src/Patch/Patch.cpp` and `Patch.h` (the exchanges and `buildtag`),
`src/SmileiMPI/AsyncMPIbuffers.cpp` (the field tags), `src/Patch/VectorPatch.cpp` around the
envelope, `src/ElectroMagn/LaserEnvelope3D.cpp`, `src/Profiles/Function.cpp` and
`src/Tools/PyTools.h`; and the stacks of a plain hung run (gdb, 170 s into the run). Line
numbers are those of 61aa917.

| # | Verdict | Why |
|---|---|---|
| H1 | **real bug**, deterministic, not a message race; found by the observed classes of the scan (rank 0 outside MPI, everyone else waiting on it), not by a prediction | rank 0's stacks: thread 1 in `PyGILState_Ensure` (`Function_Python3D::complexValueAt`, `Function.cpp:99`, from `LaserEnvelope3D::injectEnvelopeFromXmin`, `LaserEnvelope3D.cpp:228`), the master in libgomp at the barrier of the same `omp for` (`VectorPatch::injectEnvelopeFromXminIfNeeded`, `VectorPatch.cpp:1147-1151`); the six other threads in `MPI_Wait` for its field exchange (`Patch::finalizeSumField`, `Patch.cpp:1092-1095`). Since the Python 3.12 port (`PyTools.h:46-50`), `SMILEI_PY_ACQUIRE_GIL` is `PyGILState_Ensure` instead of an `omp critical`, the master thread holds the GIL outside the loops that release it with `SMILEI_PY_SAVE_MASTER_THREAD`, and three other loops over patches that call Python from every thread are wrapped in that pair (`VectorPatch.cpp:1162-1170`, `:1441-1446`, `:4795-4852`). This one is not: a thread other than the master that gets a patch on the xmin boundary waits for the GIL forever, and the master waits for it at the loop's barrier. It needs 2 or more threads per rank, Python 3.12 or later, an envelope laser injected from xmin (`box_side="xmin"`) and a decomposition that gives a non-master thread an xmin patch (the 3D benchmarks here do; the 1D, 2D and AM ones with the same injection passed). Fix: the pair around the loop (`envelope-gil.patch`, 3 lines). Series and clean room below |
| S1 | **tool false alarm** (a limit at a stopped run) | in all 12 hung traces each of the 31 alternatives is a receive pending when the run was stopped and the one pending send of its envelope (`s1check.py`: 31 of 31 in the three traces checked): the receive's own message, which MPI matches as soon as both are posted (the progress rule); the trace has neither completion because the waits on them never returned. The tags (`buildtag`, `Patch.h:485-502`) are the sender patch's Hilbert index, a direction digit and a field digit written in decimal, distinct for every exchange in flight between two ranks; the receives name the neighbour patch's rank and tag (`Patch.cpp:918-947`). Same shape as GA's S2. Not fixed (the class is part of the corpus scores); recorded in docs/findings.md |
| F1 | not a bug of the program here | the external-field benchmark runs out of the containers' memory (killed by signal 9 at 8 to 15 GB, passed at 24 GB) |
| none elsewhere | no race, by construction for these benchmarks | no wildcard receive and no probe in any trace; every exchange's tag is unique among those in flight between two ranks (above; fields that share a field digit, such as the three E components with digit 0, are exchanged one after another, `SyncVectorPatch.cpp:547-552`, each completed before the next is posted), and a phase's exchanges are separated from the next by the barriers of its `omp for` loops, which the trace records. The audit's issue 307 is an invalid tag with Intel MPI (a tag above `MPI_TAG_UB`: `buildtag` writes the Hilbert index in decimal and then two digits, so the tag is about 100 times the patch count and outgrows a small tag bound early, and the `(int)` cast truncates from 21 million patches), not a race; the tag bounds of this scan's MPIs are 2^31-1 (Open MPI) and 2^28-1 (MPICH 4.2.0), far above the tags of these benchmarks |

## The envelope hang (H1): series, fix, report

`envelope-gil.patch` (3 lines in `VectorPatch::injectEnvelopeFromXminIfNeeded`): release the
GIL in the master thread around the loop and take it back after, as the other loops do.
`series.sh` ([series-openmpi.csv](series-openmpi.csv), [series-mpich.csv](series-mpich.csv)),
untraced, 4 ranks of 2 threads, 120 s a run, 20 runs per cell, plain and patched builds
(`build.sh` with `VARIANT=gil`) interleaved, every run that ended checked by Smilei's validation,
2 Nov 07:08-08:50 +08:

| benchmark, MPI | plain | patched |
|---|---|---|
| `tst3d_24_envelope_Laguerre_Gauss_from_xmin`, Open MPI 4.1.6 | 20 of 20 hang | 0 of 20: 20 pass their validation, 25 to 27 s |
| `tst3d_25_envelope_Hermite_Gauss_from_xmin_reduced_dispersion`, Open MPI | 20 of 20 hang | 0 of 20: 20 pass, 26 to 30 s |
| `tst3d_24_...`, MPICH 4.2.0 | 20 of 20 hang | 0 of 20: 20 pass, 28 to 31 s |
| `tst3d_25_...`, MPICH | 20 of 20 hang | 0 of 20: 20 pass, 30 to 34 s |

Control, one thread per rank ([series-omp1.csv](series-omp1.csv)): the plain build passes 20
of 20 (5 per benchmark and MPI, 15 to 28 s). With 4 threads per rank ([series-omp4.csv](series-omp4.csv), Open MPI, 3 runs per benchmark and build): plain 6 of 6 hang, patched 6 of 6 pass (31 to 35 s).

The report's reproduction block (`clean-room.sh`: a fresh clone, 61aa917, Smilei's own makefile
with Ubuntu's Open MPI and parallel HDF5, 4 ranks of 2 threads) run in an empty directory on 2
Nov: 3 of 3 plain runs printed no time step and were stopped at 120 s (exit 124), 3 of 3 runs
with the patch applied by `git apply` finished (exit 0). Not in Smilei's issue tracker
(searched 2 Nov: "GIL" 1 issue, #413, a `ParticleBinning` axis in 2021; "envelope xmin" and
"python 3.12" nothing on a hang). No forced replay applies: the hang is no message race, and the
run has no alternative matching to force. Report drafted, not sent.

## What the tool did here

No race in Smilei's shipped benchmarks at this scale, and the tool says why in its own terms:
no wildcard receive, no probe, and no two concurrent receives of a rank that accept one message
in any of the 414 traces outside the two hung benchmarks. Reading confirms it: every tag in
flight between two ranks is distinct (the issue the audit cited is a tag bound, not a race).
The real bug of the scan is a deadlock between the GIL and an OpenMP barrier, which any plain
run of two shipped benchmarks shows; the report's observed classes (rank 0 outside MPI, the
others blocked on its messages) pointed at rank 0, whose stacks named the loop. The tool's
predictions there (S1) are the stopped run's own pending matches, a false alarm of the class at
a stopped run. Two tracer settings were needed before any of this (section 18 of
docs/threads.md).

## Files

`Dockerfile`, `build.sh` (Smilei with both MPIs, `VARIANT`/`PATCH` for patched trees),
`validate.py` (Smilei's validation of one run), `scan.sh` and `scan_csv.py` (the scan and its
tables `scan-{openmpi,mpich}-{runs,sites}.csv`, and the sizing pass `size-openmpi.csv`),
`summary.py` (the findings by site and class), `s1check.py` (S1's alternatives against the
pending sends), `envelope-gil.patch`, `series.sh` (`series-*.csv`), `clean-room.sh` (the report's reproduction block).
