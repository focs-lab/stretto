# SLATE leads of the configuration sweep: the list, fixed before the code is read

The configuration sweep ([../sweep/RULE.md](../sweep/RULE.md)) traced 1 695 cells of SLATE's
tester (878439b). Besides the CALU race it was built to test, its reports carry observed thread
findings and its traced runs some timeouts. This file lists them as leads and fixes how each will
be decided, before any SLATE source of the routines involved is read. [leads.csv](leads.csv) is
the list, made by [leads.py](leads.py) from the sweep's reports and `sweep-slate.csv` (sweep
commit e4a95b0 for every row).

## The leads

| lead | class | cells | runs | what the reports say |
|---|---|---|---|---|
| F | `2-thread-finalize` (observed) | 103 (99 passed, 4 failed their check) | 40 routines, mostly matgen, svd, ge2tb, the mixed-precision solvers `gesv_mixed[_gmres]`, `posv_mixed[_gmres]`, `hegst`, `pocondest` | 303 findings, each "MPI_Finalize is not preceded by chain r.-1's record ..." |
| C | `2-thread-collective` (observed) | 17, all wide, all passed | `hetrf`, `hesv`, `hetrs`, `gecondest` and others | 52 findings, each "bcast and allreduce on comm 0 are called by chains r.-1 and r with no synchronisation" |
| K | `2-collective` (observed) | 10, all wide, all passed | `hetrf`, `hesv`, `hetrs`, `gecondest` | "collective #n on comm 0: ranks disagree ['allreduce', 'bcast']" |
| T | traced run timed out (300 s) | 19 | `hetrf`, `hesv`, `hetrs`: every cell with 4 threads (15 of 15) and none with 2; `tb2bd` 2, `trtri` 1, `unmtr_hb2st` 1 (2 threads) | no verdict beyond the run's end |

Every cell of leads F, C and K has a trace in which the tracer's chain table overflowed on every
rank (more than 256 chains, mostly OpenMP tasks, wrote records); no cell of these classes has a
complete trace. Every F and C finding names chain `r.-1`, the chain into which the tracer puts
every record of a chain beyond the table, with no happens-before of its own. No T cell
overflowed. Traces are kept (`trace.tar.xz` in the sweep's cell directory) for every cell of the
routines named above; the 389 cells before `getrf_tntpiv` in the grid have none.

## How each lead will be decided

- **F, C, K: artefact of the chain table, or real?** Hypothesis to test: the findings come from
  merging every chain beyond 256 into chain -1, which loses the task edges that order those calls
  before `MPI_Finalize` and against each other. Test: trace the same cells with a tracer whose
  chain table holds every chain of the run (a build-time size), one run per cell for every F, C
  and K cell of the routines `hetrf`, `hesv`, `hetrs`, `gecondest`, `gesv_mixed`, `posv_mixed`
  and `matgen`, and every C and K cell. A class that disappears from every complete trace is an
  artefact; a class that remains in a complete trace becomes a candidate, and only then is the
  source read to find what orders (or fails to order) the two calls (task dependencies,
  `taskwait`, `taskgroup`, barriers) and whether MPI 4.1 §12.6.2 is violated.
- **K in particular**: ranks disagreeing on a collective sequence on one communicator in a run
  that passed is either an erroneous program whose mismatch happened to be harmless (bcast and
  allreduce of compatible sizes) or a recording artefact (records of chain -1 lack fields). It is
  decided like F and C, plus a check of the recorded communicator of each disagreeing pair in the
  complete trace.
- **T: does the program hang without the tracer?** Untraced series of 10 runs, 300 s each, of
  the 4-thread `hetrf` cells (5 cells) and the 4 other timeout cells, on processors no other
  process of this lane uses. A cell that hangs untraced is a candidate bug (then: where it hangs,
  from stack dumps of the hung ranks; a fix; the fixed series). A cell that finishes untraced in
  every run but times out traced is a tracer slowdown or a tracer-induced hang, and is rerun
  traced on the same quiet processors to tell which.
- **Candidates** get the protocol of the project's findings: untraced series of 20 runs, a minimal
  fix and its series, a forced replay where a matching is involved, and a draft report (not sent).
