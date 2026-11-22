# Configuration sweep: would a mechanical sweep have found the three thread races?

SLATE's CALU tags, DLA-Future's `matrix::copy` tags and PaRSEC's reshape tags were found by
reading code, which also chose the configuration that exposes each race
([docs/findings.md](../../docs/findings.md)). This experiment replaces the reading by a rule: the
grid of [RULE.md](RULE.md), drawn from each project's shipped drivers and documented options,
committed (638f645) before any cell ran, with Stretto at L1 as the only oracle and the matching
against the known bugs done after every cell was analysed.

## Result

| bug | cells (drivers) | cells predicting the bug at L1 | which | what the rule needed |
|---|---|---|---|---|
| SLATE CALU tags | 1 695 (113 runner lines) | 3 | `getrf_tntpiv`, wide 500x1000, grids 2x2, 2x1, 4x1: every wide cell with more than one process row; 34 to 101 findings (`1f`, `1a` in two), all at receives in `slate::internal::permuteRows` and, in one, the tile receive at the other end | the shape axis applied to every routine: the runner gives `getrf_tntpiv` square sizes only |
| DLA-Future copy tags | 175 (15 miniapps) | 4 | `miniapp_redistribution` with unequal source and destination blocks on 2x1 and 4x1: every redistribution cell with tiles that move between ranks and more than one process row except 2x2 (which, as the series' 2x2 control, has no colliding pair); 2 to 57 concurrent swaps or steals of copy's receives | the block pair and the P x 1 grids; matched by a criterion added after the run (below) |
| PaRSEC reshape tags | 650 (130 registrations) | 0 within the rule's budget; 2 with 6 hours per analysis (outside the rule) | shipped `reshape:mt` (1 rank) and `reshape:mp:mt` (4 ranks), both with two virtual processes from the two-socket topology: 4 479 and 2 644 `1f` findings at `parsec_mpi_sendrecv` (the 4-rank cell has 1 179 more at the activation receives below); 5 866 and 8 166 s of analysis | the two-socket topology (`HWLOC_SYNTHETIC`); a budget above the rule's 3 600 s |

The fixed analysis of branch theory-threads (e3ce53c: the orientation of a pair against a
concurrent receive, and every threshold judged on threaded traces) gives the same rows for every
predicting cell, with the same counts ([sweep-fixed.csv](sweep-fixed.csv); SLATE 1 306 kept
traces, 0 rows differ; DLA-Future 175, 5 differ, each a cell of `waitany-order` warnings only
that now exceeds the budget; PaRSEC 650, 0 differ; the 6-hour analyses, the same two cells, 5 964
and 8 948 s). So all three races come out of the sweep, SLATE's and DLA-Future's within the
rule, PaRSEC's only with a longer analysis than the rule allowed.

No cell predicts anything else at a receive except steals at PaRSEC's activation receives
(`mpi_no_thread_enable`, `mpi_no_thread_serve_cb`, 24 cells), a site [docs/findings.md](../../docs/findings.md)
lists as safe by design (activations are handled by their contents). Warnings with no changed
receive: `waitany-order` in 52 DLA-Future cells (47 within the budget of the fixed analysis) and 6 PaRSEC cells (pika's and PaRSEC's `Testsome`
polls).

## Choices that decided the result

- **SLATE: shapes for every routine.** An earlier sweep of 39 routines at square 800
  ([../slate/README.md](../slate/README.md), "Sweep") found nothing; the runner never factors a
  wide matrix with CALU. The rule's justification (the runner restricts shapes for its oracle,
  not for the library) is generic, but this is the axis the bug sits on. Rule 2 (oracle off when
  the tester refuses) did not matter: with the check off the tester still aborts on its
  square-only `TriangularMatrix` check after the factorisation is traced (43 `exit134` cells of
  the LU family and `getri`), and the check-on trace of the same cell was not analysed.
- **DLA-Future: the block pair.** With equal blocks no tile changes owner and copy sends nothing
  (the five square-block redistribution cells: 0 findings, 0 pending pairs). The shipped test
  (`test_matrix`, grids 3x2 and 2x3) has no colliding pair.
- **PaRSEC: the two-socket topology.** a-p13 gives this lane one NUMA node; `hwloc` there makes
  one virtual process (`hwloc-host`: 0 cells), and the other two maps that make two, `rr` and
  `file`, segfault in 220 of their 260 cells (as [../parsec/README.md](../parsec/README.md) records).
  The rule names this as open to the charge of tailoring; on a two-socket host it would be the
  host itself. The thread level needed no choice: CTest registers `:mt` variants with `-m 1`.
- **PaRSEC: the budget.** The decisive cells hit the 3 600 s cap with main's and with the fixed
  analysis; their traces hold 86 and 2 517 pending same-envelope pairs against 60 and 2 430 with
  one virtual process, so the cheap signal does not tell them apart. Not analysed beyond the
  budget: the shipped `remote_multiple_outs_same_pred_flow:mt` and `:mp:mt` with two sockets
  (over budget too; their traces were analysed after the 6-hour run had started; kept, run
  `TIMEOUT=21600 ONLY=remote_multiple reanalyse.sh`).

## Matching

Rule 11 names the bug's function. SLATE and PaRSEC matched as written. For DLA-Future the
recorded site stops one frame up, in the sender adaptor `dlaf::comm::internal::recv` that every
DLA-Future receive goes through, so the pre-registered test (a name carrying `copy`) matches
nothing. Added after the run, in [match.py](match.py): in `miniapp_redistribution`, whose only
point-to-point messages are `dlaf::matrix::copy`'s (`miniapp_redistribution.cpp:101-119`), the
adaptor's receives count as copy's. The tags of the predicted receives (11 to 125) are copy's
formula's values; nothing else in the miniapp sends.

## What else the sweep met (not examined)

- SLATE observed findings in runs that passed: `2-thread-finalize` in 103 cells (the four mixed
  precision solvers `gesv_mixed`, `gesv_mixed_gmres`, `posv_mixed`, `posv_mixed_gmres`, the
  Aasen routines), `2-thread-collective` and `2-collective` in `hetrf`, `hesv`, `hetrs` and
  `gecondest`. Leads, not checked.
- SLATE `hetrf`, `hesv`, `hetrs` and a few other cells time out traced (19 cells at 300 s).
- DLA-Future observed `2-buffering` (the observed matching judged infeasible) in the 11
  redistribution cells that move tiles, with both analyses. Not diagnosed.
- PaRSEC `rr` and `file` vpmaps crash almost every test (known); 168 core files of the test
  directory and 120 of SLATE cells were deleted during the run ([cores.txt](cores.txt) names
  the cells of SLATE).
- The runner's complex-only lines (`--type ?` for `d`: the second `herk`, `her2k`, `syrk`,
  `syr2k` lines and others) are refused by the tester (95 `exit254` cells), and for routines whose
  runner dimensions are `1 x n x k` the rule's tall shape sets the unused m: instantiation defects
  of [grid.py](grid.py), harmless to the result.

## Cost

Builds: SLATE's tester 25 minutes, DLA-Future's 14 further miniapps and PaRSEC's tests
(instrumented) about 20 minutes, 8 jobs each. The three campaigns, the fixed-tree re-analyses and
the 6-hour analyses ran in about 13 hours of wall time on a-p13 (6 Nov, its clock), on 10 to 26
processors: SLATE's cells take 1-2 s each (`hetrf` and the aborting LU cells more), DLA-Future's
and PaRSEC's analyses dominate (`bt_band_to_tridiag`, the eigensolvers, PaRSEC's `redistribute`
and the decisive reshape cells at or near the 3 600 s cap). The run stopped three times on the
12 GB disk floor (core dumps of crashing cells, a sibling's traces) and resumed by run.sh's skip
of finished cells.

## Files

[RULE.md](RULE.md), [grid.py](grid.py) and `grid-*.csv` (committed before the runs, 638f645),
`inputs/` (the runners' and registrations' text the grid was built from), [run.sh](run.sh) and
[cell_row.py](cell_row.py) (one traced run per cell, rows), [reanalyse.sh](reanalyse.sh) (kept
traces, another analysis tree or budget), [match.py](match.py) (rule 11, after the run).
Results: [sweep.csv](sweep.csv) (the rule: one row per cell, main's analysis; the 389 SLATE rows
with commit e4a95b0, `gbmm` to `gesv_tntpiv`, were written before traces were kept and have none),
[sweep-fixed.csv](sweep-fixed.csv) (the kept traces analysed by e3ce53c), [sweep-long.csv](sweep-long.csv)
(outside the rule: PaRSEC's `collections/reshape*` traces with 21 600 s per analysis, main's
analysis rows with commit ef153ed and the fixed one's with e3ce53c), [match.txt](match.txt)
(match.py's output on the three). The traces (`trace.tar.xz` per cell, 1 GB in all) stay on
a-p13 in `~/sweep-work/runs`.

Commands (a-p13, containers; images `hunt-slate:24.04` for SLATE, `atm-clang:24.04` for the
instrumented DLA-Future and PaRSEC builds):

```
python3 grid.py inputs .
experiments/sweep/run.sh slate|dlaf|parsec grid-<p>.csv WORK OUT.csv     # one row per cell
experiments/sweep/reanalyse.sh <p> WORK OUT.csv TREE LABEL               # TIMEOUT, ONLY
python3 match.py sweep.csv grid-slate.csv grid-dlaf.csv grid-parsec.csv
```
