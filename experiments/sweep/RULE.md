# Configuration sweep: the rule, fixed before any run

Question: do the three thread races that were found by reading code (SLATE's CALU tags, DLA-Future's
`matrix::copy` tags, PaRSEC's reshape tags; [docs/findings.md](../../docs/findings.md)) come out of
a sweep of configurations built by a fixed rule from each project's shipped drivers and documented
options, with Stretto at L1 as the only oracle? If they do, the configuration that the reading
supplied could have been supplied mechanically.

This file and the grid it produces ([grid.py](grid.py), `grid-*.csv`) are committed before any
cell runs. The cells' findings are matched against the known bugs only after every cell is
analysed.

**Bias stated up front.** The rule was written on 6 Nov 2026 by an author who knew the three bugs
and the configurations that expose them. The guards are: the axes below are generic (the same for
all three projects, written before each project's option lists were instantiated), each axis's
values come from the project's own help texts, runners and test registrations, and every place
where a choice could have been steered by that knowledge is named under "Choices open to the
charge of tailoring". The verdict on each bug says whether it rests on such a choice.

## The generic rule

1. **Drivers.** Every distributed driver the project ships, taken from its own list:
   - the test runner, if there is one (SLATE `test/run_tests.py`): every routine it runs by
     default, one cell set per command line it emits;
   - otherwise every miniapp or test program whose build registers it (DLA-Future
     `miniapp/CMakeLists.txt`, `DLAF_addMiniapp`; PaRSEC `ctest` registrations, `add_test`).
   Excluded, by kind only: GPU-only drivers, drivers that are not programs of the library (build
   steps, the code generator, Python post-processing, `rm`), drivers in a language the build did
   not enable (Fortran), and kernel drivers that take no process grid (DLA-Future
   `miniapp/kernel/*`).
2. **The driver's own arguments.** As the runner or registration gives them; where it gives a list
   for an option that is not on an axis below, its first value (to bound the cost). The element
   type: real double (`d`) where the driver has one. Repetition options: one run, no warm-up.
   Oracle options: on where the driver has one (SLATE `--check y`, the tester's default;
   DLA-Future `--check-result last`); if the driver refuses a cell because its oracle does not
   apply to the cell, the cell is run once more with the oracle off, since Stretto needs no oracle.
3. **Shape axis** (drivers with matrix dimension options): square, tall (first dimension twice
   the second), wide (second twice the first), for every driver whatever the runner restricts
   it to.
   - Where the project ships a size classification (SLATE's runner: xsmall, small, medium,
     large), its default class (medium), and in each shape the largest dimensions of that class
     (square 500, tall 1000x500, wide 500x1000; a third dimension k = the second).
   - Otherwise, block options at a quarter of their default, and the dimension D = 8 times the
     largest block of the cell; tall (2D, D), wide (D, 2D). Two block options of one matrix
     (row and column block, or source and destination) form a pair with the same three values,
     base block = the quarter-default of the first.
4. **Process grid axis.** Every factorisation P x Q of np in {2, 4}: 1x2, 2x1, 1x4, 2x2, 4x1,
   where the driver takes a grid; np as registered where it does not (PaRSEC).
5. **Threads axis.** {2, 4} threads per process (OpenMP threads, pika workers); a process count
   of registered PaRSEC tests is kept, and their computation threads are the registered `-c`, or
   4 where none is registered.
6. **Runtime-layout axis** (runtimes whose documented parameters choose how threads are grouped):
   every documented value of each such parameter. PaRSEC: `runtime_vpmap` (help text of
   `parsec/parsec.c`): `flat` (default), `hwloc`, `rr:n:p:c`, `file:`. A parameter whose
   documented meaning refers to the hardware (sockets, NUMA nodes) is taken on the host's
   topology and on the common HPC node, two sockets, given to hwloc as a synthetic topology
   (`HWLOC_SYNTHETIC`). `rr` and `file` get 2 virtual processes of half the threads each.
7. **Thread level.** What the driver requests; where it registers a variant with another level
   (PaRSEC `:mt`, `-m 1`), both registrations are drivers.
8. **Covering.** Per driver, all pairs of axis values (pairwise covering, greedy, deterministic:
   [grid.py](grid.py)); with two axes this is their product.
9. **Budget.** One traced run per cell (Stretto predicts from one run); timeout 300 s per run,
   3600 s per analysis. Tracing as in the latest campaigns: the plain (unpatched) libraries,
   Open MPI 4.1.6, atomics recorded where the build is instrumented for it (DLA-Future's pika and
   DLA-Future, PaRSEC; SLATE's threads are libgomp's, not instrumented).
10. **Recorded per cell.** The command, the run's outcome (exit, oracle, timeout), Stretto's
    status and findings (class, level, the function of each finding's receive), and the number of
    same-envelope receive pairs pending together (`experiments/dlaf/pending_pairs.py`), a cheap
    signal that needs no happens-before.
11. **Matching, after all cells.** A cell predicts a known bug when an L1 finding's receive is at
    the bug's site: SLATE `slate::internal::permuteRows` in `getrf_tntpiv`; DLA-Future the receive
    of `dlaf::matrix::copy`; PaRSEC the `MPI_Sendrecv` on the self communicator in
    `parsec_mpi_sendrecv`. Other findings are listed and examined separately.

## Instantiation

| project (commit) | drivers | axes per driver | cells |
|---|---|---|---|
| SLATE (878439b) | the 113 command lines of `run_tests.py --type d --medium` ([inputs](inputs/slate-run_tests-medium-d.txt)) | shape 3 x grid 5, threads {2, 4} covered pairwise; `--nb 64,100` as the runner gives it (one process runs both) | 1 695 (`grid-slate.csv`) |
| DLA-Future (09fe442, pika 947cf61) | the 15 miniapps outside `kernel/` ([options](inputs/dlaf-miniapp-options.txt)) | grid 5 x threads 2, plus the shape pair (`m`, `n`) and the block pair (`mb`, `nb`, or `block-size`, `block-size-dst`) where the miniapp has them | 175 (`grid-dlaf.csv`) |
| PaRSEC (6c49997) | the `add_test` registrations of a build with MPI and tests ([inputs](inputs/parsec-addtest.txt)) minus the excluded kinds | vpmap 5 (flat, hwloc on the host, hwloc on 2 sockets, rr, file) | 650 of 130 registrations (`grid-parsec.csv`) |

Expected cost: SLATE 1 695 cells at about 10 s of run and 20 s of analysis each, DLA-Future
175 cells, PaRSEC 650 short cells; one to two days of a-p13's 20 processors of this
lane (0-9, 40-49), most of it SLATE. Builds: SLATE's tester (about 25 minutes, 8 jobs), the 14
DLA-Future miniapps (instrumented build), PaRSEC's tests (instrumented build).

## Choices open to the charge of tailoring

- **Shapes for every routine** (3): SLATE's runner gives `getrf_tntpiv` square dimensions only;
  the rule widens every routine to the three shapes. The rule's justification is generic (the
  runner restricts shapes for its oracle, not for the library), but it is the axis on which
  SLATE's bug sits.
- **Oracle off when it refuses a cell** (2): SLATE's tester refuses a wide `getrf_tntpiv` with its
  check on.
- **The two-socket topology** (6): a-p13 gives this lane one NUMA node, so `hwloc` alone makes one
  virtual process here; PaRSEC's bug needs two. The rule takes the common HPC node as the second
  topology for every hardware-dependent parameter; on a two-socket host this cell is simply the
  host.
- **np in {2, 4}, grids of every shape** (4): DLA-Future's bug needs a grid with P > 1 rows and
  tags that meet between two ranks; the rule's grids include 2x1 and 4x1 because they are all the
  factorisations, not because of the bug. The shipped test (`test_matrix`) uses 3x2 and 2x3.
