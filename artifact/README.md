# Stretto: artifact

This directory is the entry point for reproducing the evaluation of the paper (section
"Evaluation"). The repository holds the tool (the tracer `tracer/`, the analysis
`analysis/stretto/`, the replay library `replay/`), the experiment scripts
(`experiments/`) and the result tables the paper was built from (`experiments/results/`);
the paper's sources are kept apart. Every number in the evaluation is a macro that
`experiments/tables.py` writes into `experiments/generated/numbers.tex` from those tables, and
each table row names the code commit that produced it.

`artifact/reproduce.sh <target>` runs one part of the evaluation inside the experiment image
and then checks the paper's claims against the numbers it produced (`artifact/claims.py`).

## Requirements

- Linux on x86-64 with Docker (20.10 or later), and a user that may run containers.
- For the corpora: 12 cores and 24 GB of memory, as in the paper's runs (fewer cores work,
  only slower). Nekbone's analysis needs 48 GB in one process (33 GB at its peak), as does each
  of its replays when E8c checks it (about 17 GB); AMG's analysis at 64 processes takes 8.7 GB.
- About 10 GB of disk: the experiment image is 0.9 GB, MUST's image 2.8 GB, the TeX image
  for the paper 5.3 GB, Nekbone's trace 1.2 GB. MPI-PRV's image (`rq2`, unless `MPIPRV=0`)
  takes 24 GB more, and its runs about 6 GB; the HYPRE 3.2.0 and PETSc builds (`rq6`, unless
  `NEWBUG=0`) 1.3 GB, and the sources and builds of the five bugs from projects' histories
  (`rq6`, unless `HISTBUGS=0`) 3.3 GB. Keeping every trace of `all` (`KEEP_TRACES`, below) takes
  about 5 GB more.
- Network access for `image` and `inputs` (the Ubuntu archive snapshot, GitHub, GitLab, the
  RWTH Aachen GitLab and the NAS web site).

## Getting started (about 30 minutes)

```bash
artifact/reproduce.sh image    # build stretto-exp:24.04 and compare its packages with the pinned list
artifact/reproduce.sh kick     # build the libraries and the idioms, trace the idioms, run the unit tests (about 360)
artifact/reproduce.sh tables   # regenerate the paper's numbers from the committed tables and check the claims
```

`tables` prints every claim with `holds` or `FAILS`, then every number of the paper next to
the value it regenerated; with the committed tables all of them are equal. A claim whose
numbers are missing fails, and so does one with nothing to check (no trace, no replay), as
does a number the inputs fix that differs from the paper's. `artifact/out/` keeps the logs and
the copy of the paper's numbers the comparison uses. A step that fails stops `reproduce.sh`
with a non-zero status. Of the unit tests `kick` runs, 14 skip in the experiment image: those
of the first-read analysis that compile C need clang 18, which the image of
`experiments/container/Dockerfile.firstread` has.

`CPUS` (default: every processor) and `MEMORY` (default `24g`) bound the containers, `JOBS`
(default 6) the programs run at a time.

## The image

`experiments/container/Dockerfile` pins the base image `ubuntu:24.04` by digest and takes
every package from the Ubuntu archive as it was at 7 Oct 2026, 09:54 UTC
(snapshot.ubuntu.com), when the image the results ran in got its packages. The packages
that decide what a run does (gcc 13.3.0, Open MPI 4.1.6, MPICH 4.2.0, Python 3.12.3) are
also pinned by version. `experiments/container/versions.txt` lists every package of the
image as built on 21 Oct 2026; the 260 packages it shares with the image of the paper's runs
have the same versions there. `experiments/container/check.sh` compares a built image with
the list.

Inputs are pinned as well: `third_party/fetch.sh` checks out MBI, MPI-BugBench, AMG, MACSio
and Nekbone at fixed commits, and checks NPB's and HYPRE's archives by SHA-256. MUST is built
from MPI-BugBench's own `Dockerfile.must` at the pinned commit. That file starts from
`debian:12` and downloads MUST 1.9.2 by URL, so its image is not pinned; the paper's MUST runs
used the image `ceb926607397` built on 7 Sep 2026.

## Claims

| question (paper) | what the paper claims | target | time on 12 cores |
|---|---|---|---|
| RQ1, soundness | no accepted alternative is unsound against an exhaustive explorer; no replay contradicts its finding, and every inconclusive one belongs to a racing loop labelled erroneous; AMG's and Nekbone's sampled replays match their pins and finish, as do their controls | `rq1` | 3.5 h: 2.5 h (1 h without Nekbone), and about an hour for E3b, the read bounds (`READS=0` skips it) |
| RQ2, effectiveness (table "Labelled errors found") | F1 on MBI above MUST (1.9.2 and 1.11.2), stress testing at the default limits and NINJA with no false positive; every error stress testing misses there is predicted, and NINJA exposes none of them; with the eager limit forced down, stress testing exposes all of them but those that send to their own rank, and fails no correct program; on MPI-BugBench at least MUST's F1, and on the programs stress testing runs there above its F1 at the default limits and at least its F1 with the limit forced down; with the buffer guard every local-concurrency program found and nothing else changed, and MUST 1.11.2 with ThreadSanitizer finds none the guard misses; MBI run ten times: no false positive in any run, no error the run shows more often than Stretto reports it, and every clean run Stretto predicts nothing from is of a value-check program; MPI-PRV (appendix): its RQ1 reproduces in every round and its pattern is reported in none of its shipped configurations, every wildcard and barrier probe gets the impossible order in every run and its control in none, its buffer properties with script-placed markers report fewer local-concurrency errors than the guard, the Adept kernel with its missing waits gives the blocking result under the reported order while the shipped kernel does not and the guard reports its writes, Heat's predicted deadlock is confirmed and every rendezvous replay hangs as predicted | `rq2` | 20.5 h: 13.5 h, most of it MUST, 1.5 h for the stress runs on MPI-BugBench at the default limits and on both corpora with the eager limit forced down, 2 h for ten more stress passes on MBI, and 3.5 h for MPI-PRV (`MPIPRV=0` skips it) |
| RQ3, buffering | thresholds measured per send path; every replay at, below and above the predicted bound behaves as predicted | `rq3` | 1.5 h |
| RQ4, levels | findings per level and class on the corpora | `rq4` | 1 h, and E3b if `rq1` has not run it |
| RQ5, cost | tracing overhead on NPB, analysis times of lu.A, AMG and Nekbone; from 16 to 64 processes every traced run of HYPRE, AMG and NPB LU and every analysis finishes, and every sampled replay and its control match their pins and finish | `rq5` | 3 h: 1.5 h for the timed leg (on an idle machine) and Nekbone, 80 minutes on 6 cores for the runs from 16 to 64 processes |
| RQ6, real applications (table "HYPRE, ParaSails") | every cross alternative of HYPRE 2.10.1 hangs when replayed and every control finishes; the fix has none; stress testing never hangs; every cross alternative of deal.II 9.3.0-rc1 prints wrong values when replayed and 9.3.0 has none; NINJA hangs HYPRE 2.10.1 in some runs and the fix in none (NPB MG and NINJA's replica are rerun too); the new bug in HYPRE 3.2.0: every two-level trace of hypre's `ex5` and of PETSc 3.26.0's unmodified `ex2` predicts a probe that takes the next level's request and no one-level trace does, as in every trace of the scan with two or three levels; every such replay hangs with its pins matched and is a run of the program, and every control finishes; no plain or delayed run hangs, while NINJA hangs both drivers in some runs; the reply the race needs buffered is smaller than Open MPI's default eager limit; with a tag for even levels no trace has the alternative; and PETSc's level count without a threshold gives none; five documented bugs from projects' histories: every clean trace of Cabana fbbcc37f predicts its hang and every replay of its error findings hangs while every control finishes; every clean trace of stempy 3.3.5 has an alternative taking a message of the other kind, and at 4 ranks every such replay fails while every control gathers correctly; at 4 ranks every replay of MFEM 3.3.2's cross-call alternatives fails while every control finishes correctly; every clean trace of deal.II's NBX at 2c1b27fb predicts a probe taking the next call's request, every such replay forces it and prints wrong answers, and those whose control prints the expected answers count as confirmed, the others as inconclusive, while fewer than one untraced run in twenty shows the race; every traced run of LAMMPS 3a2da51a predicts its deadlock below a 4-byte eager threshold, every rerun just below it hangs and every rerun at it or with the defaults finishes, while no plain or delayed run hangs; and none of the five fixes has such a finding or fails an untraced run | `rq6` | 36.5 h or more: 15 h, about 10 h for HYPRE 3.2.0 and PETSc (`NEWBUG=0` skips them), 2.5 h for the five bugs from projects' histories (`HISTBUGS=0`), about 8 h for E8c (`VALID=0`) and an hour or more for E8b (`HB=0`) |

Since 22 Oct 2026 the replays and controls of `rq1` and `rq6` run under the tracer, and each
row says whether the run's own trace is one of the program without the pins. The claims that the
replays behind the results are such runs (E8c in `docs/results.md`, under RQ1 and RQ6 in
`claims.py`) come from the reruns in `experiments/results/valid/`, which `rq6` makes again at
its end (`experiments/valid/run.sh`, the commands of `experiments/RUNBOOK.md`, "Replays as runs
of the program": about eight hours on 12 cores; `VALID=0` skips them and keeps the committed
tables). `rq6` also reruns E8b, happens-before selection against the check, which the appendix
on the applications reports (`experiments/e8_hb.py`; about 45 minutes for the comparison and
then the replays of the disagreements; `HB=0` skips it), and `rq1` and `rq4` rerun E3b, the read
bounds (`experiments/reads/run.sh`: the first-read maps in the image of
`experiments/container/Dockerfile.firstread`, the corpus run analysed again with them, the
replays of the programs whose levels they change, and E6 with read bounds, about 20 minutes on
8 processors; `READS=0` skips it).

Each target reruns what its question needs, regenerates the tables and prints the claims of
that question. RQ1 to RQ4 share one traced and analysed run of the corpora, AMG and Nekbone
run once for RQ1, RQ5 and RQ6: a marker in `artifact/out/` (`corpora.done`, `amg.done`,
`nekbone.done`, `scale.done` for the runs from 16 to 64 processes of RQ5, `mpiprv.done`
for MPI-PRV's in RQ2, `newbug.done` for the HYPRE 3.2.0 and PETSc campaigns of RQ6,
`reads-maps.done` and `reads.done` for E3b, `hb.done` for E8b, `valid.done` and
`valid-nekbone.done` for E8c) records each that succeeded, and removing the marker reruns the
step. `all` runs every question, then checks every claim once more. `NEKBONE=0` leaves Nekbone
out, `SCALE=0` the runs from 16 to 64 processes, `MPIPRV=0` MPI-PRV: its artifact image (24 GB,
pulled by digest) and the runs of `experiments/mpiprv/` (`experiments/mpiprv/README.md`),
`NEWBUG=0` the HYPRE 3.2.0 and PETSc campaigns (`experiments/hypre3/`, `experiments/petsc/`),
`HISTBUGS=0` the five bugs from projects' histories (E5g: `experiments/cabana/`, `stempy/`,
`mfem332/`, `dealii8929/`, `lammps/`; marker `histbugs.done`), and `READS=0`, `HB=0` and `VALID=0` E3b, E8b and
E8c.

Before `rq1` to `rq6`, fetch and build the inputs:

```bash
artifact/reproduce.sh inputs    # clones, corpus programs, AMG and NPB builds (5 minutes on 2 cores), then MUST's image
artifact/reproduce.sh rq1 rq3   # for example
PAPER_DIR=../paper artifact/reproduce.sh paper   # a copy of the paper's sources, built with the numbers just regenerated
```

The times are those of the paper's runs: 12 cores of one NUMA node, `JOBS=6`.

## What varies between runs

The claims are what has to hold. Only the numbers the inputs fix (the corpora's sizes and
labels, how many runs, samples and repetitions the scripts make, the buffering thresholds)
have to equal the paper's; every count taken from recorded runs depends on their schedule.
In a rerun of `rq1`, `rq3` and `rq4` from fresh inputs on 21 Oct 2026, with the code of that
day, every claim held and such counts differed by up to a fifth from the numbers the paper then
had (78 MBI replays instead of 74, 14 inconclusive instead of 10, none contradicting its
finding). The paper's corpus numbers now come from a run of 22 Oct, after a fix of the tracer:
70 judged MBI replays, 39 confirmed and 31 inconclusive (`\nMBIReplayFindings`,
`\nMBIReplayConfirmed`, `\nMBIReplayInconclusive` in `experiments/generated/numbers.tex`), every
inconclusive one in a racing loop labelled erroneous whose control hangs too. The ten repeated
MBI runs of RQ2 (`experiments/results/reps/`) show how far such counts move between runs of
one code version.

- The inputs are the paper's: MBI's 1 871 generated programs are identical byte for byte, and
  so are MPI-BugBench's 2 067 as a set. But MPI-BugBench's generator numbers its correct
  programs in an order that changes from one generation to the next, so 82 of the 388
  programs the corpus run selects come out under other file names than in the paper's tables
  (checked on 21 Oct 2026; the one other difference is the indentation of a comment in a
  program the run does not select). A corpus rerun keeps MUST's verdicts by file name until
  `rq2` runs MUST again, so until then such a program carries the verdict of another correct
  program.

- Which corpus programs hang under the harness's 60 s timeout, and so the observed findings,
  depends on the schedule of the run. The paper's MUST columns come from MUST's own runs
  (`experiments/mbi/run_must.py`), which rebuild each program with MPICH.
- Stress testing draws random delays: how many runs a race needs varies.
- How many HYPRE traces record an order that exposes the race varies with the schedule of the
  traced runs (on four processes, 15 of 20 traces in the paper).
- Times are claims only from a timed leg on an otherwise idle machine
  (`experiments/RUNBOOK.md`, "Timed leg"). Elsewhere they show the cost, not the paper's numbers.
- A few HYPRE runs on eight processes exited with status 15 in the paper's campaign, replays
  and controls alike, which plain runs did not reproduce; they count as neither hanging nor
  finishing.
- Three of the committed PETSc tables are partial: the untraced runs stopped after the two-level
  ones and 43 one-level runs, the scan with `ex45` complete at four processes only, and the
  ParaSails step before the driver that gives the level count without a threshold, which a
  first pass at a smaller grid covers instead. `rq6` runs them in full, so their counts grow.

`artifact/claims.py --against OLD NEW` compares any two copies of the numbers, marking with
`~` a difference in a count taken from the runs or in a time, and with `!` one in a number
the inputs fix.

## Keeping the traces

The scripts analyse the traces they record and then delete them, or leave the last one in
place for the next run of the step to replace. With `KEEP_TRACES` set to a directory, every
trace a rerun records is kept there, traced runs, replays and controls alike, as one `.tar.xz`
per trace directory (`analysis/stretto/keep.py`); it is off by default:

```bash
KEEP_TRACES=artifact/out/traces artifact/reproduce.sh all
```

An archive is `<KEEP_TRACES>/<campaign>/<name>.tar.xz`: the campaign is the table the run's rows
go to, as its path under `experiments/results/` (`mbi/mbi`, `hypre/predict`, `valid/nekbone`),
or the step for a trace kept in place (`amg`, `nekbone`, `e4/e4`), and the name is the run as
those rows name it (the program; `np8-run03`; for a replay or control the changed pin and
`-replay` or `-control`). A replay's archive also holds `replay.txt`: its command, pins,
outcome and output. No archive is overwritten: a step run twice keeps both runs' traces, the
second as `<name>~2`. `MANIFEST.tsv` there has a line per archive with the directory it came
from, its sizes before and after, its SHA-256 and the code commit. A relative directory is the
copy's and must be one git ignores, such as `artifact/out/traces` (`reproduce.sh` refuses
another, whose files would mark every row's commit `-dirty`); an absolute one is mounted into
the containers. The tables come out the same with it and without it.

A full rerun records about 26 000 traces, 220 GB before compression, which take about 5 GB
kept (two thirds of the uncompressed size is Nekbone's replays) and about an hour of one
processor to compress, spread over the steps. The runs that record no trace of Stretto's
tracer keep nothing: the stress, delayed and NINJA runs, E2's replays, MUST's and MPI-PRV's own
runs. `experiments/RUNBOOK.md` ("Keeping every trace") lists the names of each campaign.

## Layout

| path | what |
|---|---|
| `artifact/reproduce.sh`, `artifact/claims.py` | the targets above; the claims check |
| `experiments/container/` | the image (`Dockerfile`, `versions.txt`, `check.sh`) and `run.sh`, which runs a command in it |
| `experiments/run_remaining.sh` | the corpus steps (build, tests, MBI, MPI-BugBench, stress, buffering, explorer comparison, AMG, levels) |
| `experiments/timing_leg.sh`, `experiments/amg/`, `experiments/nekbone/`, `experiments/hypre/`, `experiments/hypre3/`, `experiments/petsc/` | the timed leg and the applications |
| `experiments/reads/run.sh`, `experiments/e8_hb.py`, `experiments/valid/run.sh` | E3b (read bounds), E8b (happens-before selection), E8c (the confirmations' replays as runs of the program) |
| `experiments/results/` | the result tables; `docs/results.md` is generated from them |
| `experiments/RUNBOOK.md` | how the paper's runs were done on a shared machine, and every variable the scripts read |
| `analysis/stretto/keep.py`, `experiments/harness.sh` | `KEEP_TRACES`: every trace a rerun records, archived |
| `third_party/fetch.sh` | the inputs at the pinned versions |
