# Stretto: artifact

This directory is the entry point for reproducing the evaluation of the paper (`paper/`,
section "Evaluation"). The repository holds the tool (the tracer `tracer/`, the analysis
`analysis/stretto/`, the replay library `replay/`), the experiment scripts
(`experiments/`), the result tables the paper was built from (`experiments/results/`), and
the paper's sources. Every number in the evaluation is a macro that `experiments/tables.py`
writes into `paper/generated/numbers.tex` from those tables, and each table row names the
code commit that produced it.

`artifact/reproduce.sh <target>` runs one part of the evaluation inside the experiment image
and then checks the paper's claims against the numbers it produced (`artifact/claims.py`).

## Requirements

- Linux on x86-64 with Docker (20.10 or later), and a user that may run containers.
- For the corpora: 12 cores and 24 GB of memory, as in the paper's runs (fewer cores work,
  only slower). Nekbone needs 48 GB in one process.
- About 10 GB of disk: the experiment image is 0.9 GB, MUST's image 2.8 GB, the TeX image
  for the paper 5.3 GB, Nekbone's trace 1.2 GB.
- Network access for `image` and `inputs` (the Ubuntu archive snapshot, GitHub, GitLab, the
  RWTH Aachen GitLab and the NAS web site).

## Getting started (about 30 minutes)

```bash
artifact/reproduce.sh image    # build stretto-exp:24.04 and compare its packages with the pinned list
artifact/reproduce.sh kick     # build the libraries and the idioms, trace the idioms, run the 192 unit tests
artifact/reproduce.sh tables   # regenerate the paper's numbers from the committed tables and check the claims
```

`tables` prints every claim with `holds` or `FAILS`, then every number of the paper next to
the value it regenerated; with the committed tables all of them are equal. `artifact/out/`
keeps the logs and the copy of the paper's numbers the comparison uses.

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
| RQ1, soundness | no accepted alternative is unsound against an exhaustive explorer; no replay contradicts its finding, and every inconclusive one belongs to a racing loop labelled erroneous; AMG's and Nekbone's sampled replays match their pins and finish, as do their controls | `rq1` | 2.5 h (1 h without Nekbone) |
| RQ2, effectiveness (table "Labelled errors found") | F1 on MBI above MUST (1.9.2 and 1.11.2), stress testing and NINJA with no false positive; every error stress testing misses is predicted, and NINJA exposes none of them; on MPI-BugBench at least MUST's F1; with the buffer guard every local-concurrency program found and nothing else changed, and MUST 1.11.2 with ThreadSanitizer finds none the guard misses; MBI run ten times: no false positive in any run, no error the run shows more often than Stretto reports it, and every clean run Stretto predicts nothing from is of a value-check program | `rq2` | 13.5 h, most of it MUST |
| RQ3, buffering | thresholds measured per send path; every replay at, below and above the predicted bound behaves as predicted | `rq3` | 1.5 h |
| RQ4, levels | findings per level and class on the corpora | `rq4` | 1 h |
| RQ5, cost | tracing overhead on NPB, analysis times of lu.A, AMG and Nekbone | `rq5` | 1.5 h, on an idle machine |
| RQ6, real applications (table "HYPRE, ParaSails") | every cross alternative of HYPRE 2.10.1 hangs when replayed and every control finishes; the fix has none; stress testing never hangs; every cross alternative of deal.II 9.3.0-rc1 prints wrong values when replayed and 9.3.0 has none; NINJA hangs HYPRE 2.10.1 in some runs and the fix in none (NPB MG and NINJA's replica are rerun too) | `rq6` | 15 h |

Each target reruns what its question needs, regenerates the tables and prints the claims of
that question. RQ1 to RQ4 share one traced and analysed run of the corpora, AMG and Nekbone
run once for RQ1, RQ5 and RQ6: a marker in `artifact/out/` (`corpora.done`, `amg.done`,
`nekbone.done`) records each, and removing the marker reruns the step. `all` runs every
question. `NEKBONE=0` leaves Nekbone out.

Before `rq1` to `rq6`, fetch and build the inputs:

```bash
artifact/reproduce.sh inputs    # clones, corpus programs, AMG and NPB builds (5 minutes on 2 cores), then MUST's image
artifact/reproduce.sh rq1 rq3   # for example
artifact/reproduce.sh paper     # paper/build/main.pdf with the numbers just regenerated
```

The times are those of the paper's runs: 12 cores of one NUMA node, `JOBS=6`.

## What varies between runs

The claims are what has to hold. Only the numbers the inputs fix (the corpora's sizes and
labels, how many runs, samples and repetitions the scripts make, the buffering thresholds)
have to equal the paper's; every count taken from recorded runs depends on their schedule.
In a rerun of `rq1`, `rq3` and `rq4` from fresh inputs on 21 Oct 2026 every claim held and
such counts differed by up to a fifth: 78 MBI replays instead of 74 (64 confirmed in both,
14 inconclusive instead of 10, none contradicting its finding), 820 judged pairs in the
explorer comparison instead of 826, 49 recorded runs realisable only from a buffering
threshold on instead of 51.

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

`artifact/claims.py --against OLD NEW` compares any two copies of the numbers, marking with
`~` a difference in a count taken from the runs or in a time, and with `!` one in a number
the inputs fix.

## Layout

| path | what |
|---|---|
| `artifact/reproduce.sh`, `artifact/claims.py` | the targets above; the claims check |
| `experiments/container/` | the image (`Dockerfile`, `versions.txt`, `check.sh`) and `run.sh`, which runs a command in it |
| `experiments/run_remaining.sh` | the corpus steps (build, tests, MBI, MPI-BugBench, stress, buffering, explorer comparison, AMG, levels) |
| `experiments/timing_leg.sh`, `experiments/amg/`, `experiments/nekbone/`, `experiments/hypre/` | the timed leg and the applications |
| `experiments/results/` | the result tables; `docs/results.md` is generated from them |
| `experiments/RUNBOOK.md` | how the paper's runs were done on a shared machine, and every variable the scripts read |
| `third_party/fetch.sh` | the inputs at the pinned versions |
