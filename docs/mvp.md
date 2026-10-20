# Stretto MVP: what it does, how to run it, what it does not do

## One-line pipeline

```
trace one run  →  reconstruct the observed matching  →  enumerate single-change alternatives
at wildcard receives / probes / Waitany  →  keep the feasible ones (horizon closure + MPI
matching order + acyclicity, polynomial)  →  classify the consequence  →  confirm by pinned replay
```

## Commands

```bash
# trace (system Open MPI or MPICH build; MPICH binaries are launched with mpirun.openmpi)
mpirun.openmpi -np 4 -x STRETTO_TRACE_DIR=$PWD/tr -x LD_PRELOAD=$PWD/tracer/bin/openmpi/libstretto-trace.so ./app args

# analyse (PYTHONPATH=analysis)
python3 -m stretto analyze tr                 # findings with B-intervals and a STRETTO_PIN per finding
python3 -m stretto analyze --json tr          # machine-readable
python3 -m stretto analyze --no-sp tr         # skip the single-path explorer (large traces)
python3 -m stretto analyze --changes 2 tr     # also two-change alternatives
# exit status: 0 no error class, 1 an error class was found, 2 the trace could not be loaded, 3 internal error
python3 -m stretto show tr                    # events, communicators, observed matching, probes
python3 -m stretto candidates tr              # every candidate alternative with its verdict at B=0 / inf
python3 -m stretto oracle tr                  # exhaustive exploration of the trace as a straight-line program

# confirm (replays each error finding with its pin under the replay library)
python3 -m stretto confirm tr ./app args --np 4 --timeout 20

# corpus runs
python3 experiments/mbi/run_mbi.py --corpus mbi --select 'MessageRace_' --confirm    # MBI
python3 experiments/mbi/run_mbi.py --corpus mbb --select '^P2P/' --confirm            # MPI-BugBench level 2
python3 experiments/mbi/run_must.py experiments/results/mbi/mbi.csv                   # MUST via container
python3 experiments/mbi/reanalyze.py experiments/results/mbi/mbi.csv                  # re-analyse kept traces
python3 experiments/mbi/score.py experiments/results/mbi/mbi.csv                      # MBB-style scoring
python3 experiments/tables.py                                                         # docs/results.md
```

## Report classes

| class | level | meaning | counts as |
|---|---|---|---|
| 1a | L1 | the alternative message does not fit the receive (count, or type signature compared as flattened basic types) | error |
| 1b | L1 | the alternative message is from another logical channel (tag) | warning |
| 1f | L1 | same shape as the observed message (another sender); benign unless the continuation depends on it | info |
| 1c | L1+SP | a send is left unmatched under the single-path assumption, in every or in some of its executions | error |
| 1c | observed | a send was never received in the observed run (pending at finalize, or the run was killed) | error |
| 1d | L1 | deadlock contained in the horizon (theorem T7): a changed rank stops before its nonblocking wildcard completes and every rank that could unblock it stops too or has terminated | error |
| 1d | L1+SP | deadlock under the single-path assumption, in every or in some of its executions | error |
| 1d | L2 | deadlock of the twin program (metadata dependencies encoded) under the alternative | error |
| 1e | L2 | the twin's continuation diverges from the trace (a send to another destination or tag, a different number of collectives), or the twin aborts on the program's own check | error |
| 2-buffering | observed | a finished run that would deadlock on any implementation with an eager threshold below a bound (replayable with the limits forced down) | error |
| 2-collective | observed | ranks disagree on a collective (name or participation) | error |
| 2-blocked | observed | the run ended with a rank blocked in a call, or spinning in a polling loop (deadlock or kill) | error |
| probe-alt | L1 | a wildcard probe could have observed another message | warning |
| waitany-order | L1 | another request could have completed first (Waitany/Waitsome/Testany/Testsome) | warning |

"L1" findings are certified from the trace alone up to the horizon: the changed receive and
everything the closure keeps around it; their B-interval lists the thresholds at which the
alternative is feasible among those at which the observed run is realisable. "L1+SP" findings
assume the recorded call sequence does not change and are unsound on metadata-dependent code
(idioms 01 and 06); each says whether every execution containing the pair has the consequence
or only some, and a consequence the straight-line program has without the change (a hang of the
run, a send nobody receives) is not attributed to the alternative. "L2" findings come from a
twin program given to `analyze(twin=...)` (`experiments/mbi/twins.py`, `analysis/tests/
idioms_dsl.py`). Every receive and probe alternative carries a `STRETTO_PIN` or
`STRETTO_PIN_PROBE`; an L1+SP finding's pin fixes every wildcard receive of one witness
execution; completion-order alternatives have no replay mechanism. `--changes 2` also lists
alternatives that re-match two wildcard receives on different ranks.

A replay confirms a finding only if every pinned receive *matched* in the rerun (the replay
library prints `matched widx=W` when a pinned receive completes; `applied` only says the source
argument was rewritten, and forcing an impossible match hangs as well), the run reaches the
predicted outcome (a hang for 1c/1d, the specific MPI error for 1a), and a control rerun that
pins the same receives to their observed senders finishes. A finding whose control does not
finish is inconclusive. `stretto confirm` prints all three.

## Coverage

Traced: all point-to-point modes, persistent requests (a pinned Start runs a shadow request
with the pinned source), `Sendrecv(_replace)`, all wait/test variants and `Request_get_status`,
probes incl. `Mprobe/Improbe/Mrecv/Imrecv` (consuming), `Cancel` (with the completion's
cancelled status), `Request_free`, all blocking collectives and their nonblocking variants,
communicator creation (`Comm_dup/idup/split/split_type/create/create_group`, `Cart_create/sub`,
`Graph_create`, `Dist_graph_create(_adjacent)`, `Intercomm_create/merge`) with world-rank
groups and both groups of an intercommunicator, `Buffer_attach`, `Abort`, `PROC_NULL`, derived
types flattened to their basic signature. Blocking calls emit a `begin` record so a killed rank
shows where it was blocked. Failed polls repeated without other calls in between are written
once with a repeat count. Under `MPI_THREAD_MULTIPLE` records carry the thread id and the
analysis stops at observed findings, since per-rank program order is not total.

Not modelled: RMA, neighbourhood collectives, dynamic processes, generalised requests, MPI-4
`Isendrecv`, partitioned communication, persistent collectives and large-count point-to-point
calls. The tracer writes an `unmodelled` record when the program creates a window, calls one of
the others, or starts a persistent request it does not track, and the analysis then reports
observed findings only, since the trace is no longer a complete record of the run's
communication. Not seen at all: Fortran under Open MPI (its bindings bypass C interposition:
use the MPICH build, as the NPB experiment does) and memory accesses (family ③).

## Limits

- The explorer (L1+SP consequences) is exhaustive and exponential: `stretto analyze` skips it
  above `--sp-max-events` (default 2000) and stops a forced exploration at `--max-states`
  (default 300000); both are noted in the report. The corpus harness uses 600 and 30000.
- Every alternative is profiled at the two ends of the B range and bisected between them,
  which theorem T2 (`model.md`) makes exact for single-change alternatives; `--full-profile`
  judges every message-size breakpoint and `--generic` uses the generic checker, both for
  differential testing (two-change alternatives are always profiled in full).
- Analysis cost grows with the number of alternatives (wildcard receives × compatible
  sends, probes × other senders) times the thresholds the bisection visits, plus the trace
  size: 8-rank AMG (53 wildcard receives, 10 978 probes, 3263 probe alternatives) takes
  13 s and 0.56 GB on the second machine (300 s with `--full-profile`); lu.A at 16
  ranks (3 million events, no wildcards) 50 s; Nekbone's communication suite at 8 ranks
  (10 million records, 35 714 wildcard receives, 18 759 alternatives) 63 min and 33.3 GB in
  one process, about 3 KB per record and 0.2 s per alternative (`docs/results.md`, E4, E5
  and E5b). Memory is the loaded trace and its graph; time is one check per alternative over
  the whole trace.
- `1f` and `probe-alt` are reported for every feasible alternative sender; on master–worker
  and probe-driven code that is one finding per competing sender.
