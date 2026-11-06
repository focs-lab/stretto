# MPI-CorrBench outside its OpenMP directory

MPI-CorrBench a05644b has 616 C programs; its 103 OpenMP ones ran under Stretto and MUST-vc on 1 Nov
([mustvc](../mustvc/README.md)). This directory covers the other 513, in
`micro-benches/0-level/{coll,conflo,correct,pt2pt,rma,usertypes}`: 311 erroneous programs
(CorrBench's ArgError, ArgMismatch, MisplacedCall and MissingCall cases, `conflo/` repeating
them behind control flow) and 202 correct ones (MPICH test-suite programs). Most are errors of
one call's arguments, which no trace-based predictor is meant to judge; the point is a measured
scope table, not a score. Every cell below comes from running both tools (a-p13, CPUs
10-19,50-59, 6 Nov 2026; `MACHINE=a-p13`).

## Scope, fixed before the runs

`classify.py` gives each program its label (erroneous unless under `correct/`, CorrBench's own
rule) and, for an erroneous one, whether its error is in Stretto's scope: in, when the error, in
the run the program's comment describes, is something the report has a class for (a call that
never completes or a message never received, a collective sequence that differs between the
ranks, a buffer of a pending operation touched before it completes, a message race); out, with a
reason, otherwise (`programs.csv`). The rule and the table were committed (fd63267) before any
program ran. 28 erroneous programs are in scope:

| in scope | programs | expected class |
|---|---|---|
| a receive whose tag matches no send | 8 | 2-blocked, 1c |
| a deadlock in every run (call order, a missing call, a collective a rank skips) | 11 | 2-blocked, 2-collective, 1c |
| a deadlock unless the sends are buffered | 3 | 2-buffering |
| a message never received | 2 | 1c |
| a send buffer written before the wait | 2 | 3-local |
| two pending receives into one buffer | 2 | 3-local, 3-race |

283 are out of scope: 196 invalid arguments of one call (192, the null datatype and the 3
destinations outside their communicator), 57 one-sided programs, 10 signature mismatches of the
run's own match (Stretto checks signatures for alternative matches only), 8 collective-argument
mismatches, 8 datatype and request lifetimes, 2 calls before `MPI_Init` and 2 missing
`MPI_Finalize`.

## Results

Stretto: each program built with the MPI's `mpicc`, run once without the tracer, three times
traced with payloads (L1, L1+SP, and with Open MPI L2 by re-execution) and three times with the
buffer guard and buffer ranges, 2 processes as CorrBench's own harness runs them. A run is
flagged when its report has a finding of an error class (`experiments/mbi/score.py`). Cells are
programs flagged in every run / in some run; in parentheses, how many of them were flagged only
in runs that failed by themselves (a hang, an MPI error, a signal: what any run shows); in
brackets, programs with a run without a verdict.

| programs | n | Open MPI 4.1.6: native finished / hang / error | Open MPI: all configurations | MPICH 4.2: native | MPICH: all |
|---|---|---|---|---|---|
| erroneous, in scope | 28 | 12 / 16 / 0 | **28 / 28** (16) | 12 / 16 / 0 | **28 / 28** (16) |
| erroneous, out of scope | 283 | 64 / 29 / 190 | 209 / 209 (206) [2] | 80 / 9 / 194 | 191 / 191 (187) [2] |
| correct | 202 | 155 / 31 / 16 | 51 / 51 (46) | 201 / 1 / 0 | **5 / 5** (0) |

In scope, every program in every run, under both MPIs. 16 of the 28 hang by themselves and the
report says where (2-blocked, with 1c for the eight tag mismatches). The other 12 finish and are
flagged all the same: the three that deadlock unless their sends are buffered and the barrier
case that does too (2-buffering, "deadlocks on any implementation with eager threshold < B"),
the two messages never received (1c), the two reductions a rank skips (2-collective), and the
four buffer cases (3-local), which only the runs with the buffer guard report; without it 24 of
28. Nothing here is a message race (none of these programs receives with a wildcard), so L1's
alternatives and L2 add nothing: the reruns of L2 ran (up to 90 per trace) and changed no verdict.

Out of scope, the flags are the runs' own failures: 206 (Open MPI) and 187 (MPICH) programs are
flagged only in runs that crashed, aborted on an MPI error or hung, where the report says which
rank stopped where. Three are flagged in runs that finished: the two `MissingCall-MPIIBcast`
programs, whose two pending `MPI_Ibcast` write one buffer (3-local; a correct report, on a
program the rule put out of scope as a request's lifetime), and `ArgError-MPIIRecv-Rank-2`,
whose wildcard receive L1 shows truncating a message (1a). The two programs that call MPI
before `MPI_Init` leave no trace and get no verdict.

Correct programs: under MPICH 5 of 202 flagged, all in runs that finished:

- `pt2pt/dtype_send`, `inactivereq`, `patterns` (two or more pending `MPI_Irecv` into one
  buffer) and `sendrecv3` (an `MPI_Recv` into the buffer of a pending `MPI_Isend`): 3-local in
  every guarded run. These are erroneous by MPI's rule that a pending operation's buffer is not
  accessed until it completes; CorrBench labels the same shape erroneous in
  `pt2pt/ArgMismatch-MPIIrecv-buffer-overlap`, and MUST reports all four ("the memory regions to
  be transfered by this receive operation overlap").
- `pt2pt/rqfreeb`: a false alarm. Stretto reports that the run deadlocks below an eager threshold
  of 4 bytes because the last `MPI_Isend` has no posted receive; its receive was posted before a
  barrier and then freed with `MPI_Request_free`, and a freed receive takes no message in the
  observed matching (`stretto.trace._reconstruct_observed` skips receives without a status), so
  the buffering profile sees a rendezvous send with no receive. Not fixed here: the observed
  matching of freed receives is shared by every corpus (991 MBI and 194 MPI-BugBench programs
  call `MPI_Request_free`), so the fix needs those tables rerun.

Under Open MPI 46 more correct programs are flagged, all one-sided ones (`correct/rma`) whose
runs fail without the tracer too: 47 of the 72 hang (31) or abort (16) natively under the image's
Open MPI 4.1.6, and every one of them finishes under MPICH, where none is flagged.

MUST 1.11.2 (MPICH 4.0.2, the image of [must](../must/README.md), the paper's second MUST
baseline; 3 runs of 60 s each through `scripts/must_once.sh`, the run and verdict of
`scripts/must_run.sh`). Programs flagged in every run with a verdict / in some run:

| programs | n | MUST | runs without a verdict: timeout / no report / harness |
|---|---|---|---|
| erroneous, in scope | 28 | 26 / 26 | 1 / 0 / 1 |
| erroneous, out of scope | 283 | 148 / 149 | 4 / 231 / 51 |
| correct | 202 | 47 / 47 | 57 / 12 / 130 |

In scope MUST misses the two programs that write a send buffer before the wait
(`MisplacedCall-MPIWait`, OK in every run), which Stretto's guarded runs flag; it reports every
deadlock, buffering ones included, and both overlapping receives. Out of scope, invalid
arguments and argument mismatches are what MUST checks: it flags 149 of the 283, Stretto none
except where the run itself fails. On the correct programs MUST flags 47 of 202: 13 for an
intercommunicator where MUST expects an intracommunicator, 8 deadlocks, 7 collective
mismatches, 4 unknown datatypes and others (not examined one by one; every one of these programs
finishes without a tool under MPICH 4.2 in the experiment image), and 5 overlapping receive buffers: the four Stretto
reports too and `coll/iallred`, whose buffers are disjoint. 30 correct programs get no verdict
from MUST in any run (a timeout, or MUST hanging after the program finished).

## Tool defects found

- **Payloads of 2 GiB or more** (fixed, 1496d9b): with `STRETTO_PAYLOAD` set, the tracer took a
  message's packed size with `MPI_Pack_size`, whose `int` overflows at 2 GiB, and its
  `MPI_Pack` then failed under the communicator's fatal error handler: MPICH's
  `datatype/large_type_sendrec` (one 4 GiB message) aborted in every traced run with
  `MPI_ERR_TRUNCATE` and finished without the tracer. The size is now taken with
  `MPI_Type_size_x`; run again (`stretto-<mpi>-again.csv`, which replaces its rows), the test
  finishes traced and has no finding.
- **Freed receives in the observed matching** (not fixed): `rqfreeb` above.
- Not the tool's: `conflo/usertypes/ArgError-MPITypeCreateStruct-Displacements-1` crashes in
  `MPI_Send` only when traced; its displacements are uninitialised, so what it sends depends on
  the stack, which the preloaded tracer changes.

## Files

| file | what | made by |
|---|---|---|
| [programs.csv](programs.csv) | label, scope and reason of each program | `classify.py` (before the runs) |
| [stretto-openmpi.csv](stretto-openmpi.csv), [stretto-mpich.csv](stretto-mpich.csv) | one row per run: mode (native, trace, guard), outcome, classes with their levels, L2 columns | `run.py` |
| [stretto-openmpi-again.csv](stretto-openmpi-again.csv), [stretto-mpich-again.csv](stretto-mpich-again.csv) | `large_type_sendrec` with the fixed tracer | `run.py --select large_type_sendrec` |
| [must.csv](must.csv) | one row per MUST run, its verdict | `must.sh` |

`summary.py DIR [--list]` prints the tables and, with `--list`, every flagged correct program
and every in-scope program with the classes of each run. The commit column names the code of
each row ("-unchecked": read from the copy's COMMIT, `experiments/provenance.py`): Open MPI's
pass ran at bdf925d, then at 9f70d96 and 6d05d13 after the harness was made to delete traces and
core files, which changed no verdict; MPICH's at 6d05d13; the runs again at 1496d9b; MUST's rows
say bdf925d, the commit at their start (`must.sh` took the same two clean-up changes while it
ran).

Commands (a-p13, from a copy of the repository at `~/cb-work/repo` with CorrBench under
`study/apps/MPI-CorrBench`, results in `~/cb-work/results`):

```
D="docker run --rm --user $(id -u):$(id -g) -e HOME=/tmp -v $HOME/cb-work:/cb -v $HOME/cb-work/repo:/w -w /w"
C=/w/study/apps/MPI-CorrBench/micro-benches/0-level
$D --cpuset-cpus 10-15,50-55 stretto-exp:24.04 bash -c "make -C tracer MPI=openmpi && make -C rerun MPI=openmpi &&
  make -C tracer MPI=mpich && make -C rerun MPI=mpich &&
  python3 experiments/corrbench/run.py --mpi openmpi --jobs 5 $C /cb/results/stretto-openmpi.csv /cb/runs/s &&
  python3 experiments/corrbench/run.py --mpi mpich --jobs 5 $C /cb/results/stretto-mpich.csv /cb/runs/s"
$D --cpuset-cpus 16-19,56-59 --memory 16g stretto-must:1.11.2 bash -c "JOBS=3 experiments/corrbench/must.sh $C /cb/results/must.csv /cb/runs/must"
python3 experiments/corrbench/summary.py experiments/corrbench --list
```
