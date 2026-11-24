# Uniform evaluation protocol: the rule, fixed before any run

Question: what does every tool reveal per run, on the same configurations, counted the same way,
with intervals a reader can check? The committed results answer this only cell by cell: an audit
of 24 Nov 2026 found that their run counts range from 3 to 320 per cell, that one row of a table
mixes configurations, machines, MPI libraries, delay sets and NINJA builds, that the best NINJA
setting is chosen after the runs, and that Stretto leaves out the traced runs in which the bug
struck while the thread results count them. This file fixes one protocol for every tool and every
subject. It is committed before any run of the campaign. The decisions on the audit's open
questions were taken on 24 Nov 2026 and are written into the rules below.

The campaign's results replace the cells of the paper's tables. The earlier CSVs stay in the
repository as they are, and nothing in them is overwritten.

## 1. Principles

1. **One configuration set per subject**, the same for every tool (Section 4). A tool that cannot
   run a configuration gets a cell "cannot run", with the reason taken from an attempted run. A
   reason argued without a run does not count.
2. **One N.** N = 40 runs per configuration per tool on the buggy version, and N = 40 on the
   fixed version or control. This covers Stretto's traced runs, plain runs, random delays, NINJA
   and MUST, and holds for the thread bugs too. Suites are run in passes (Section 5).
3. **One outcome classification**, applied to every run of every tool (Section 3). Every run
   counts, and no run is left out after its outcome is known.
4. **No choice after the runs.** Every setting is reported separately. The main table shows the
   settings declared here: NINJA's random mode (Section 2.4) and the gate mode of the thread
   replays (Section 6). Everything else goes to the appendix.
5. **One machine.** Every run is on a-p13.
   - Runs use CPUs 0-17 and 40-57.
   - CPUs 18-19 and 58-59 are left for analysis, rendering and the LLM judges' container.
   - Each rank of a job is pinned to its own hardware thread, with no oversubscription.
   - Jobs run in parallel on disjoint CPU sets, so a job gets np of the 36 run threads. NPB MG at
     32 ranks runs alone.
   - Every CSV row records `host`, `cpus` and `jobs`.
6. **One MPI per configuration** for every tool. Open MPI 4.1.6 and MPICH 4.2.0 are those of the
   experiment image `stretto-exp:24.04`. MUST 1.11.2 is built in that image against both
   (Section 2.3).
7. **One frozen commit.**
   - The campaign runs from the first commit after this file that completes the harness changes
     of Section 7. Its hash is written in `README.md` beside this file before the first run, and
     in every row (`commit`).
   - A change to a tool during the campaign is a declared deviation, listed in `README.md` with
     its date and reason. Every block it can affect is run again.
8. **A rerun is declared.** A campaign is replaced only for a harness failure: a full disk, a
   killed container, a host pause. The failure is named in `README.md`. The replaced rows stay in
   the CSV with `superseded` set.
9. **A concurrency check.** Parallel placement is checked against running one job at a time. For
   each bug, the first configuration of Section 4 gets 40 plain runs at `jobs` = 1 beside the 40
   parallel ones, and both rates are reported with Fisher's exact test.

## 2. Settings

1. **Timeouts.** For each configuration and each tool setting, T is fixed before that block's
   runs:
   - T = max(30 s, 10 × the slowest of 10 runs of the fixed version under the same tool and
     setting);
   - for the buggy version, the same T;
   - for subjects without a fixed version, 10 runs of the buggy version whose symptom did not
     strike;
   - T is written in every row (`timeout`).

   A run past T whose bug is a hang counts as the symptom. A run past T on any other bug counts as
   a tool failure. Stretto's analysis gets 3 600 s. An analysis past that is a tool failure
   ("analysis timeout").
2. **Random delays.** One set, D50: before every send and before every receive, each rank sleeps
   for a delay drawn once per run from {0, 0, 0, 1, 5, 20, 50} ms, separately for sends and for
   receives (`experiments/stress_baseline.py`, `replay/pin.h`).
   - The seed is `Random(i*100+np)` for run i, as before, and is written in the row (`seed`).
   - Only delayed runs count in the delays column. Plain runs are a separate column.
3. **MUST.** Only release 1.11.2, built in `stretto-exp:24.04` against Open MPI 4.1.6 and
   against MPICH 4.2.0; the images of the earlier MUST campaigns are not used.
   - A run counts as reported when MUST's report names the racing call. The judgement is by call
     site, not by the kind of error.
   - NPB MG is judged by this rule like every other bug, with no cell set by hand.
   - Release 1.9.2 is dropped from every table, the MBI union and the sign tests included.
4. **NINJA.** One library carries all of the repairs of the earlier campaigns: random noise,
   Fortran, Testsome/Waitsome, Testall/Waitall and Waitany.
   - It is used for every subject. Every earlier NINJA campaign is run again with it.
   - The main table shows the random mode, which perturbs 10% of the sends by 50 ms.
   - The system-centric and application-centric modes are run everywhere, at the same N, and go to
     the appendix.
   - HYPRE 2.10.1's 1.2 s random setting goes to the appendix, as a row of its own.
   - Each configuration also gets 10 runs with no noise, recorded beside it.
5. **Plain runs.** 40 untraced runs per configuration, with nothing loaded. They give the symptom
   rate that every other column is read against.
6. **MPI-PRV.** It is attempted on every subject, the Fortran ones (NPB MG, Quantum ESPRESSO)
   included. A build or instrumentation failure is recorded from the attempt.

## 3. Outcome of a run

Two observations are made for each run r of the buggy version.

- **S(r):** the bug's own symptom occurred in r. It is decided by each subject's judge as committed
  (hang, wrong result, abort).
- **F(r):** the tool's report names the bug.
  - Stretto: a finding of the bug's class at its site (`classify.py`).
  - MUST: Section 2.3.
  - Delays, NINJA and plain runs: F(r) = S(r).

| category | S | F |
|---|---|---|
| predicted | no | yes |
| observed, reported | yes | yes |
| observed, not reported | yes | no |
| missed | no | no |
| tool failure | the tool or its analysis did not complete | |

**Main cell for every tool:** revealed per run = (predicted + observed, reported) / N, with its
Wilson 95% interval. Struck traced runs are analysed like every other run.

**Shown beside it:**
- Stretto's predicted / (predicted + missed): the share of clean runs whose trace predicts the bug;
- the symptom rate S / N of every tool, plain runs included;
- the number of tool failures.

On the fixed version, F without the bug is a false alarm; the false alarms are counted the same
way and are reported, whatever their number.

**Why N = 40:** with 0 of 40 the upper bound of the interval is 8.8% per configuration, and with
0 of 80 it is 4.6% per bug over two configurations. 40 of 40 gives a lower bound of 91%. The
worst-case half-width is 0.15. The paper's comparisons, Stretto at 0.9-1 against the other tools at
0-0.7, are separated at this N.

## 4. Configurations

| subject (bug / fix) | configurations, every tool | MPI |
|---|---|---|
| HYPRE 2.10.1 / 2.11.0 | `ij -solver 8 -pout 0`, np 4, 8 | MPICH |
| HYPRE 3.2.0 `ex5`, two levels / fix | np 4, 8 | Open MPI |
| PETSc `ex2` on HYPRE 3.2.0, two levels / fix | np 4, 8 | Open MPI |
| NPB MG 2.4.1 / 3.3.1 | class S, np 16, 32 (32 alone, on 32 hardware threads) | MPICH |
| deal.II 9.3.0-rc1 / 9.3.0 | `mapping_01`, np 4 | MPICH |
| deal.II NBX (issue 8929) / fix | `nbx_calls 3`, np 4, 8 | Open MPI |
| Cabana before PR 835 / after | `Test10NoTopo`, np 3, 4; `-repeat2` as a supplementary row for every tool | Open MPI |
| stempy 3.3.5 / 3.3.6 | np 4, 8 | Open MPI |
| MFEM 3.3.2 / 3.4 | the two-mesh driver, np 4, 8 | Open MPI |
| LAMMPS before / after the March 2017 fix | `in.displace`, np 4, 8; eager limit 0 as a supplementary row | Open MPI |
| SuperLU_DIST 9.3.1 / fix | the 4 reducible matrices (nos2, mhd3200b and blk12x6 at np 3, blk8x10 at np 6); the g20 contrast as a supplementary row | Open MPI and MPICH (8 configurations) |
| Quantum ESPRESSO `elph_prt` / fix | Ni, `-nk 3`, np 3 | MPICH |
| Uintah with hypre, SLATE CALU, DLA-Future, PaRSEC | the series cells and traced cells as committed in `experiments/{uintah,slate,dlaf,parsec}`, each with its fixed or control cell | as committed |
| MUST's vector-clock checks | the cells of the four thread bugs above | as above |

The driver arguments, inputs and judges of each subject are the committed ones in
`experiments/<subject>/`. Where a subject's script differs from this table, the table is the rule.

## 5. Suites

The suites are MBI (the 115 programs of `tab:rq2`), MPI-BugBench (388 programs) and MPI-CorrBench
(513 programs, and the 103 of its OpenMP directory). Every tool runs R = 10 passes, one run per
program per pass, on a-p13 under Open MPI. The tools are:
- Stretto, by traced run and analysis;
- random delays with D50;
- NINJA in its three modes;
- MUST 1.11.2.

CorrBench also runs under MPICH, as before. Two views are reported, both:

1. **Per run.** For each program, the share of the R runs that reveal its bug, and per pass the
   number of programs found. The mean and range over the passes are reported, along with F1 per
   pass.
2. **Budget.** Stretto and MUST get one run. Delays and each NINJA mode get up to 30 runs and stop
   at the first failure, as in `tab:rq2`. The budget is printed in each column's heading.

MBI's headline stays at L1. L2 by re-execution is a row of its own.

## 6. Replays

Replays apply only to configurations whose traces predict the bug.

- **Which alternatives:** every distinct alternative, by its set of pins, over the configuration's
  traces. If there are more than 40, a sample of 40 is drawn with the seed recorded.
- **Denominator:** distinct alternatives replayed. Replays per trace are reported only beside it.
- **Controls:** every replay gets its control run. A replay is confirmed by the subject's committed
  criterion: pins matched, the predicted outcome, and the control correct.
- **Thread replays:** the gate mode is `after`, the defaults of `stretto.confirm`
  (`experiments/replay_modes.sh`).
  - Gates give way after 1 000 ms without progress.
  - The control follows the recorded order beyond the horizon.
  - The modes `before`, `yield` and `both` go to the appendix as a sensitivity row.
- **L1+L lock replays:** every finding, two replays each, as committed.

## 7. Harness changes before the first run

- **Every CSV row** gains:
  - `host`, `cpus`, `jobs`, `timeout`, `seed` and `commit`;
  - `superseded`;
  - the outcome category of Section 3.
- **Thread campaigns** gain the `commit` column they lack.
- **`tables.py`:**
  - computes every proportion of the paper from these CSVs, with Wilson interval macros;
  - drops the macros that pick NINJA's best setting;
  - generates the numbers now typed by hand: threads, LLM, buffers and L2 by re-execution.

## 8. LLM baseline

The three conditions of `experiments/llm/` run k = 3 judges per item for every model reported, and
the mean and range are given.
- Condition 2 is scored on its 20 items, and on 19 beside condition 3 for comparison.
- Condition 3 keeps 19 items. The OpenFOAM negative `dcaf36` (batch b8) is a negative only by its
  command line, and its tree is identical to a buggy one.

## 9. What is outside this rule

The configuration sweep (`experiments/sweep/`) keeps its own rule, one traced run per cell, since
it answers a different question. The blind scans keep theirs. Timed overhead legs (`e4`) keep
their announced protocol.
