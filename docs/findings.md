# Findings

One ledger of everything the bug hunts found: real bugs, latent bugs, sites shown safe, and
defects of the tool itself. Each entry says where its evidence lives. Measured numbers of the
cases that are experiments of this repository are in [results.md](results.md) (generated).
Updated with every finding.

## Real bugs, predicted from clean runs and confirmed by forced replay

| Bug | Class, consequence | How found | Evidence | Upstream |
|---|---|---|---|---|
| HYPRE 2.10.1 ParaSails, two exchanges share tag 222 | probe takes another exchange's request; hang | documented (fixed in 2.11.0) | E5c, `experiments/hypre` | fixed |
| NPB MG 2.4.1, message of another grid level | wildcard receive; wrong numbers | documented (fixed in 3.2.1) | E5d, `experiments/npbmg` | fixed |
| deal.II 9.3.0-rc1, probe takes the next call's message | probe; wrong values | documented | E5e, `experiments/dealii` | fixed |
| deal.II NBX, issue 8929 (pre-fix 2c1b27fb) | failed `Iprobe` of call k could see call k+1's message; wrong answers | documented; reached by flag flips | E5g, E5i, `experiments/dealii8929` | fixed (f9ea3396) |
| Cabana, before PR 835 | hang | documented | E5g, `experiments/cabana` | fixed |
| stempy 3.3.5 | deserialisation exception | documented | E5g, `experiments/stempy` | fixed |
| MFEM 3.3.2, group rows | abort (truncation) or wrong groups | documented | E5g, `experiments/mfem332` | fixed |
| LAMMPS, before March 2017 | deadlock without eager buffering | documented | E5g, `experiments/lammps` | fixed |
| **HYPRE 3.2.0 ParaSails, all pattern levels share tag 221** | probe takes the next level's request; hang | new, Oct 2026 | E5f, `experiments/hypre3` | report drafted |
| **SuperLU_DIST 9.3.1, 3D solve, barrier commented out (79644321)** | L-solve takes a U-solve message on reducible matrices; hang or wrong x | new, Oct 2026 | E5h, `experiments/superlu3d` | report drafted |
| **Quantum ESPRESSO develop `elph_prt`, tags independent of spin** | ionode's receives take the other spin's data (LSDA, npool >= 3); wrong printed table | new, Oct 2026 | E5h, `experiments/qeelph` | report drafted |
| PETSc 3.26, `-pc_hypre_parasails_nlevels` ignored without `_thresh` | option handling (not a race); found while reproducing HYPRE 3.2.0 | by reading | none needed (deterministic) | report drafted |

The four drafted reports were reproduced on 29 Oct 2026 from their text alone, on the then
current heads and without this tool. QE shows in plain runs (23 and 30 of 40 wrong tables, with
MPICH and with Open MPI). SuperLU_DIST and HYPRE show only with a demonstration patch that
delays one message (20 of 20 hang in every setting, 0 of 20 with the fix). Plain runs of both
never failed on one node (0 of 80 and 0 of 160).

## Latent bugs: real, but reached only by a driver over the public API

No shipped caller reaches them, so they count apart from the bugs above.

| Bug | Evidence (driver written for the hunt) | Why no shipped caller hits it |
|---|---|---|
| OpenFOAM ESI develop 68a81857c, `Pstream::exchange(Map)`: NBX with a constant default tag; two calls in a row mix their messages | 39 of 39 clean traces predict it; 429 of 434 replays print wrong data; plain and delayed stress 1 of 320; `nbx.tuning=1` removes it | the only in-tree caller (Test-parallel-chunks, with `nbx.min=2`) checks nothing; snappyHexMesh tutorials traced with `pbufs.tuning=1` have no cross-call alternative. The 2024 issue 3238 was the same class at call sites |
| libsc 6928417 `sc_notify_payload_nbx`: fixed tag, nothing orders calls | 22 of 22 clean traces predict it; 372 of 372 replays wrong; plain stress 22-24 of 40 wrong; a barrier removes it | NBX is opt-in; the default type PEX is safe |
| libsc `sc_notify_recursive_nary`: per-level tags reused by the next call | 20 of 20 clean traces; 80 of 80 replays wrong, then abort or SEGV | opt-in; p4est callers put a collective between calls |
| libsc `sc_notify_payloadv_nbx`: sends tag NBXV, probes tag NBX | deterministic hang, 20 of 20 (not a race; found by reading) | opt-in, `sorted=0` only |
| Swift/T ADLB 4b3882d `xlb_poll`: `Iprobe(ANY, ANY)` can take SYNC_SUB, which has no handler | 20 of 20 clean traces; 40 of 40 replays abort the server; stress 0 of 290 | STC-compiled Swift uses short subscripts; only the C API or hand-written Tcl rules reach it |

## Sites shown safe

| Site | Why safe | How shown |
|---|---|---|
| hypre 3.2.0 `hypre_LocateAssumedPartition` (MGR) | `MPI_Scan` then `Bcast` from the last rank order consecutive calls | traced: 31 alternatives, all within one call |
| dune-common `IndicesSyncer::sync` | two `MPI_Allreduce` (dune-istl matrixhierarchy.hh) separate the syncs | traced `pamgtest`: 460 alternatives, 0 across syncs, 30 replays correct |
| PETSc 3.26 own wildcard sites | - | 480 replays benign |
| MFEM 4.10 | - | 160 replays: 150 benign, 10 inconclusive (ex15p hex) |
| deal.II 9.8.0 | - | 8 419 alternatives, all within one exchange; 120 replays benign |
| openfpm_vcluster, code_saturne, KaHIP | GPU-only clash; NBX never selected; not an NBX between calls | reading |

The 26 Oct screen of 70 codes and its safe list: kept with the hunt notes, outside this
repository.

## Defects and limits of the tool found by the hunts

- Failed polls were out of reach (NBX races sit at an `Iprobe` that returned no message): flag
  flips since 27 Oct 2026. Flips were not forced in replays and were named by a timing-dependent
  segment: fixed 28 Oct (d582a71, 14b6221).
- A trace whose receives are cancelled at shutdown and never completed (ADLB) is reported
  degraded and predicts nothing; the hunt stripped the trailing cancel records. Open.
- Analysis time: a SuperLU_DIST ABglobal trace of 7 403 events took 1 072 s. Under study.
- Alternatives that need two changed matches are not predicted (HDF5 ph5diff 1.8.9). Under study.
- Treating every collective as a barrier changes no finding (0 of 115 MBI, 0 of 396 MBB): the
  minimal synchronisation of collectives is not an advantage on these corpora.

## Not yet traced

PFLOTRAN (perturbation inversion, needs three or more process groups) and SPECFEM3D (HDF5 I/O
server): read only.

dolfinx a7b1840 `nbx_consensus_rounds` (MPI.cpp:182-290), two readings that disagree: an
`Irecv(ANY_SOURCE)` listener still posted when the call ends, and cancelled after its Ibarrier,
may match the next call's Issend. Against it: IndexMap runs the exchange on a communicator of its
own, and every other caller creates a communicator right after it, which synchronises in both
MPIs although the standard does not require it. Unverified; the cancelled listener also needs the
fix for cancelled receives above.

Thread-level candidates for the multithreaded extension (read only; design on branch
`threads`, docs/threads.md): Uintah + hypre (a code comment in KokkosScheduler.cc documents
crashes from hypre's `Iprobe` taking a Uintah message on the shared communicator, and the
reverse hazard it leaves); Legion/Realm's MPI network in interop mode (wildcard `Irecv` with tag 1
on `MPI_COMM_WORLD`); DLA-Future and SLATE (tags reused by concurrent tasks); HDF5 Subfiling (a
fixed wrong-worker race, 27bb358, as a known bug). By reading, not a race: Realm's
`generate_payload_tag` (am_mpi.cc:225) uses the attribute key `MPI_TAG_UB` as the tag bound.
