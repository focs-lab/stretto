# Athena++: OpenMP threads that post and test their own boundary exchanges

Target 3 of the third thread hunt (2 Nov 2026), a blind scan like those of Global Arrays
([experiments/ga](../ga/README.md)) and Smilei ([experiments/smilei](../smilei/README.md)): the
shipped tests are traced and analysed first, the findings committed (the pre-registration),
and only then is the communication code read at the reported sites. The reason to look here
(an audit, unverified before the scan): Athena++ has hybrid MPI+OpenMP regression tests and a
build without dependencies, and packs its tags without overflow checks.

Athena++ master 053bc73 (21 Oct 2026), configured by its `configure.py` with `-mpi -omp` and
`--cflag=-g` (and `-hdf5` for the examples), gcc 13, Open MPI 4.1.6 or MPICH 4.2.0,
MACHINE=a-p13, CPUs 0-7,40-47, in the experiment image plus FFTW with its MPI interface (Open
MPI only in Ubuntu) and parallel HDF5 (`Dockerfile`). Every traced run's `init` record says
`MPI_THREAD_MULTIPLE`. What was read before the pre-registration: `configure.py`'s options, the
regression harness (`tst/regression/run_tests.py`, `scripts/utils/athena.py`), the scripts of
the tests run (`scripts/tests/{hybrid,mpi,grav,turb,fft}`), the names and `<mesh>` blocks of
the example inputs, and the function names and line numbers that `stretto sites` prints.

## Blind scan (pre-registration)

Recorded before any of Athena++'s communication code was read (`src/bvals`, `src/mesh`,
`src/task_list`, `src/fft`, `src/gravity`); what was read is listed above.

**What ran.** Two parts, MACHINE=a-p13, CPUs 0-7,40-47, scripts of fdb1968 (the commit that
squashes the scripts as they ran; the deviations below say which part ran before which
change), analysis code unchanged since ac02b15.

- `scan.sh`, the regression tests that use MPI, through Athena++'s `run_tests.py`, built with
  `-mpi -omp`, 3 runs of each test, every `mpirun` call traced by the wrapper `mpirun` of this
  directory, 2 Nov 09:28-11:20 +08: `hybrid/hybrid_linwave` (its own calls: 1 rank of 1 thread,
  2 ranks of 2 threads), `mpi/mpi_linwave` (1, 2 and 4 ranks), `grav/jeans_3d`, `turb/turb_3d`
  and `fft/fft` (1, 2 and 4 ranks; FFTW's MPI interface exists for Open MPI only), the last four
  with `mesh/num_threads=2` on every run; with Open MPI all five, with MPICH the first two.
  Tables [scan-openmpi-runs.csv](scan-openmpi-runs.csv), [scan-mpich-runs.csv](scan-mpich-runs.csv)
  (one row per traced call: the test and its result, ranks, threads, trace size, report status,
  findings by level and class, the commit) and `scan-*-sites.csv`.
- `examples.sh`, two shipped inputs with adaptive refinement, which no MPI test runs:
  `mhd/athinput.linear_wave2d_amr` and `hydro/athinput.dmr_amr`, built with `-mpi -omp -hdf5`,
  2 ranks of 2 threads, 3 runs each with both MPIs, 2 Nov 10:47-11:42 +08: tables
  `examples-*-runs.csv` and `examples-*-sites.csv`.

Every traced run's `init` record says `MPI_THREAD_MULTIPLE` with the issue lock on; every
analysed call of 2 or more ranks has 2 chains per rank (both threads call MPI), the 1-rank
calls one. Deviations, all recorded here or in the
tables:

- Athena++ takes a parameter on the command line only if the input file has it. The first runs
  of `jeans_3d`, `turb_3d`, `fft` and of the DMR example failed at start under
  `mesh/num_threads=2` and were discarded; the scripts then gave every input without the
  parameter `num_threads = 1` (the default) in its `<mesh>` block, and those tests were run
  again (from 10:30 +08). The two linear-wave tests, whose inputs have the parameter, ran
  before that change and are not affected by it.
- The examples write HDF5, which an executable configured without `-hdf5` rejects; the first
  attempt is discarded, and the image got parallel HDF5. A pilot of them on 4 ranks wrote
  1.3 GB in 20 s (linear wave) and passed 1.5 GB (DMR); they ran on 2 ranks.
- A traced `jeans_3d` call of 2 ranks wrote 14 GB in its first minutes and filled the machine's
  disk at 10:35 +08 (all lanes' disk, for about 5 minutes, until that trace was deleted); the
  wrapper now kills a call whose trace passes 1.5 GB. In all 3 runs `jeans_3d`'s 2-rank call
  was killed so (the test then fails), and its 4-rank calls did not run.
- Traces above 400 MB (650 MB for the examples) were not analysed: every 4-rank call of
  `mpi_linwave` (1 038-1 140 MB) and every DMR run (1 049-1 065 MB). The 1-thread call and
  the 2-rank calls of every test, and every linear-wave AMR run, were analysed.
- `run_tests.py` deletes the test's executable when the test ends, so `stretto sites` names no
  function for the regression tests; the site below was named from an executable built again
  with the test's configuration (`-mpi -fft --prob=fft -omp --cflag=-g`), with `addr2line` at
  the site record's offset.

**Findings, by site.**

| # | Site | Class, level | Calls (traces with it) | Per trace |
|---|---|---|---|---|
| W1 | `remap_3d` at `src/fft/plimpton/remap_3d.cpp:109` (site 15, `athena+0x26ffa3`): an `MPI_Waitany` | `waitany-order` at L1 (another receive of the `Waitany` could complete first; the report says the continuation is undetermined only if it depends on the index, and that no replay mechanism exists for completion order) | `fft`, 4 ranks of 2 threads, Open MPI: 3 of 3 traces, at ranks 1 and 2 (204 each), none with 1 or 2 ranks | 408 |

No other finding of any class in any analysed trace: 33 traces of the regression tests with
Open MPI (of `hybrid_linwave`, `mpi_linwave`, `jeans_3d`'s 1-rank call, `turb_3d` and `fft`),
12 with MPICH, and the 6 traces of the linear-wave AMR example: all `complete`, no
alternative at L1, no observed error. The tests passed in every run (but `jeans_3d`, above);
so did every example run. The traces of 2 or more ranks but `fft`'s hold 39 000 to 674 000 probes each
(in the kept traces every one is an `MPI_Iprobe` with `MPI_ANY_SOURCE`, and none found a
message), with no alternative there.

**Questions for the reading** (step 3): W1, does `remap_3d` depend on which receive completes
first, or does it unpack each message into its own place (an order-free `Waitany` loop)? Why
no finding in the boundary exchanges, the AMR restriction and prolongation and the wildcard
probes: are the tags of concurrent exchanges distinct, and what do the probes do with what
they find? Where would the tag packing overflow (the audit's lead)?

## Verdicts (after the pre-registration, from reading the code at the sites)

Read from 11:45 +08: `src/fft/plimpton/remap_3d.cpp`, the tag builders
(`BoundaryBase::CreateBvalsMPITag`, `bvals/bvals_base.cpp:271-291`;
`Mesh::CreateAMRMPITag`, `mesh/amr_loadbalance.cpp:1469-1480`), the `MPI_Iprobe` calls of
`src/bvals`, and the OpenMP loops of `task_list/task_list.cpp`. Line numbers are those of
053bc73.

| # | Verdict | Why |
|---|---|---|
| W1 | harmless by design | `remap_3d` (`remap_3d.cpp:65-112`) posts every receive into its own place of the scratch buffer (`recv_bufloc[irecv]`) from a named source, sends with `MPI_Send`, and unpacks each completed receive from its own place into its own offset of the output (`recv_offset[irecv]`, `unpackplan[irecv]`): the order in which `MPI_Waitany` returns them changes nothing. All remap messages share tag 0 on the plan's communicator (`MPI_Comm_dup`, `:438`), and a remap completes all its receives before it returns, so per-source FIFO keeps two remaps apart |
| none elsewhere | no race in these tests, by construction | a boundary tag packs the destination block's local id, the buffer and the physics (`(lid<<11) \| (bufid<<5) \| phys`), an AMR tag the local id and the child offsets (`(lid<<8) \| (ox1<<7) \| (ox2<<6) \| (ox3<<5)`): distinct for every message in flight between two ranks; every receive names its source. The `MPI_Iprobe(MPI_ANY_SOURCE, MPI_ANY_TAG)` calls (`flux_correction_fc.cpp:1635`, `bvals_var.cpp:258` and others) only push progress: their result is discarded (the code calls it "black magic"), so no probe alternative has a consequence. The audit's lead is real but not a race: no check of the tags against `MPI_TAG_UB` (a `\todo` at `bvals_base.cpp:283-284`), so a rank with 2^17 blocks would pass MPICH's bound of 2^28-1 here and 2^20 blocks Open MPI's; nothing near in the shipped tests |

No bug found, by the tool or by reading. Not reached here, by size: the 4-rank calls of
`mpi_linwave`, the DMR example, and `jeans_3d` beyond its 1-rank call (its 2-rank call of the
multigrid build wrote more than 1.5 GB in its first minutes).

## What the tool did here

Nothing wrong found, and the tool's one class (W1) is the completion order of a `Waitany` whose
loop does not depend on it, which its own text says it cannot tell. The absence of other
findings has the reason reading gives: tags unique among messages in flight and receives that
name their source, with the only wildcard calls being probes whose results are thrown away.
The traces of Athena++'s threaded runs are large (350 MB for a 2-rank linear wave of 16 blocks,
over 1 GB with 4 ranks), which bounded what could be analysed.

## Files

`Dockerfile`, `mpirun` (the traced wrapper, first in PATH), `scan.sh` (the regression tests;
`scan-{openmpi,mpich}-{runs,sites}.csv`), `examples.sh` (the AMR examples;
`examples-{openmpi,mpich}-{runs,sites}.csv`), `scan_csv.py` (the tables).
