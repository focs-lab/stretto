# Level L2 without hand-written twins

Level L2 judges an alternative past the horizon with a *twin*: a DSL program
(`analysis/stretto/dsl.py`) that states how a program's control flow and MPI arguments depend on
what it receives. Until now every twin was written by hand: the ten idiom twins
(`analysis/tests/idioms_dsl.py`) and the four value-skeleton twins of E7
(`experiments/mbi/twins.py`). This note compares ways to obtain the skeleton automatically,
recommends one, states what it guarantees relative to the model's premises, and reports a
prototype of it (`rerun/`, `analysis/stretto/rerun.py`, `experiments/e7_rerun.py`).

## 1. What a skeleton has to provide

The model's program is a function per rank from the results its calls have returned (statuses,
payloads, Waitany indices, test flags, collective results) to its next call (`docs/model.md` §2b;
the paper's appendix, Conventions). A trace gives that function on the recorded results only.
L1 asks nothing more: its claims hold for every program consistent with the trace on the horizon.
L1+SP fixes the function to the recorded calls whatever the results are, which is unsound when control
flow depends on received data (Lemma T5: idiom 01; idiom 06 changes its collectives).

An L2 skeleton is any way of evaluating the function on results the trace did not record. The
explorer asks for exactly those: after the alternative's message at the changed receive, every
interleaving of the remaining messages, at B = 0 and B = ∞. Three things follow.

- Payloads matter. The E7 programs check `buf1 != 3` on the last message; the hand-written twins
  get away with a branch on `MPI_SOURCE` only because MBI's senders send their own rank.
- The skeleton must give the *arguments* of later calls (destination, source, tag, count, root,
  communicator) and the *payload* of later sends, which the receivers of those sends depend on in
  turn, and how the rank ends (finalize, abort, crash).
- The results the explorer asks about are finite and concrete. Every alternative message is a send
  of the trace, with the payload the trace can record. Nothing needs to be symbolic, as long as
  each concrete question can be answered.

A skeleton that over-approximates the program (has executions the program does not have) keeps the
claims about *every* execution sound and breaks those about *some* execution; one that
under-approximates does the opposite. The report makes both kinds (`aborts in every execution`,
`deadlock in some executions`, dismissals), so a skeleton has to be exact on the questions it
answers and say "undetermined" for the others.

## 2. Candidates

### (a) Dynamic dependence recording (taint)

Label the bytes of every received message and the fields of its status, propagate the labels
through the computation, and observe which later MPI arguments, send payloads and branch conditions
carry them.

*Compiler instrumentation* (clang's DataFlowSanitizer). The program and every library it computes
with are rebuilt with `-fsanitize=dataflow`. The MPI library stays uninstrumented and is declared in
an ABI list as `uninstrumented` and `custom`; each MPI call then needs a wrapper `__dfsw_MPI_*`. For
`MPI_Recv`, `MPI_Wait*` and collectives the wrapper calls the real function, then labels the
receive buffer, the status fields and the count with `dfsan_set_label`. For sends and every call
with a rank, tag, count or root argument, the wrapper receives the argument labels as extra
parameters (that is how DFSan's custom wrappers work) and reads the labels of the payload with
`dfsan_read_label`. Branches are observed with `-dfsan-conditional-callbacks` and
`dfsan_set_conditional_callback`. DFSan in LLVM 18, the version the image would get, has 8 base
labels (one bit each), so one run can tell apart at most 8 receives or groups of receives. The
image has no clang. The pinned addition would be `clang-18=1:18.1.3-1ubuntu1` and
`libclang-rt-18-dev=1:18.1.3-1ubuntu1` (the DFSan runtime), the same LLVM 18 the image already has
for clang-format. Fortran is out: neither gfortran nor flang has DFSan, and libgfortran would have
to be listed function by function.

*Binary instrumentation* (a taint tool on Pin, DynamoRIO or Valgrind) needs no rebuild and covers
Fortran, but costs a 10-100x slowdown. Pin is proprietary, and DynamoRIO and the Valgrind taint
tools are not in the Ubuntu archive, so none can be pinned like the image's other packages; they
would be source builds. The MPI side is the same as above: the tool must know which bytes each MPI
call writes, which is a hand-written model of every call.

*What it guarantees.* Taint observes the *recorded* path. If no branch and no MPI argument after
the changed receive carries its label, then under the determinism premise below the rank takes the
same path with any other message of the same size: the rank's continuation is the recorded one,
and the L1+SP findings for it hold for the program. DFSan tracks data flow only, so the condition
must be "no labelled branch at all", which also rules out implicit flows. That is the static check
the paper leaves to future work (its section on skeletons), done dynamically. Where
a label does reach a branch or an argument, taint says *that* the continuation depends on the
message, not *how*. It cannot run the other side of the branch, and it does not give the function
(`dest = status.MPI_SOURCE` and `dest = f(status.MPI_SOURCE)` carry the same label). It therefore
cannot produce E7's abort findings: deciding `buf1 != 3` under the alternative needs the
alternative value pushed through the program, which is execution (or symbolic execution) again.

*Cost.* One instrumented run per group of 8 receives, a rebuild of the program and its libraries with
clang, an ABI list and wrappers for every MPI call. No Fortran.

### (b) Single-rank speculative re-execution

The tracer optionally records payloads. For an alternative, the changed rank is rerun alone under
a shim MPI library that serves it the recorded messages, with the alternative's message at the
changed receive, and writes down every call it makes with its arguments and payloads. The run
stops at the first result no recorded run determines. The observed continuation is the twin.

The prototype makes two choices that go beyond that sketch:

- *The trace is a cache of the program.* Every rank follows its recorded calls for as long as its
  results are the recorded ones (metadata *and* payload), and is re-executed from the first result
  that differs. A rank that takes a different message with the same envelope and contents stays on
  its trace and costs nothing.
- *Any rank may be re-executed, not only the changed one.* A rank whose results change because
  the changed rank now sends it something else is rerun the same way. With
  `live = {changed rank}` the prototype is the minimal variant above: every other rank is known
  only on its recorded path, and a rank whose results differ stops there as undetermined.

The rest of this note is about (b); §3 states its premises and §4 the prototype.

*What it needs from MPI.* No rebuild and no ABI list. A PMPI-level shim that the dynamic loader
puts in front of the MPI library, like the tracer. MPI itself starts as a singleton (no `mpirun`)
and serves only local calls: datatypes, packing, statuses, time. The shim answers communication
calls from a script, with fake request handles, `MPI_Status_set_elements` for counts and
`MPI_Unpack` for payloads, and ends the run at the first call it does not serve.

*Cost.* One process run per (rank, history of results) the explorer reaches off the trace, each
from the start of the program: 0.2 s (MPICH) to 0.3-0.5 s (Open MPI) of singleton start-up in the
image, plus the rank's own computation up to the point. Because each run starts over, a
continuation of k results costs O(k²) work; a fork at each result point would make it O(k) (§6).
Exploration itself costs what it costs with a hand-written twin.

### (c) Static extraction from the source

A C front end (clang's libTooling, or pycparser) slices the program on MPI arguments and branch
conditions and writes the slice as a DSL program, or a verifier with an MPI model (CIVL,
MPI-SV) explores the source itself. The skeleton then covers both sides of every branch at once, and
for the MBI templates it could be done. Against the model, though, it fails on the one property §1
asks for, exactness. The E7 checks compare a received *value*, so the slice needs the senders'
values too (`buf2 = rank`): an inter-rank value analysis over pointers, aliasing, loops with
data-dependent bounds, function pointers, several translation units and libraries. Every place
where it abstracts makes the skeleton over-approximate, and then the "some execution" findings are
no longer sound. It also does not reach Fortran (Nekbone), C++ templates, or code behind macros and
callbacks (HYPRE). The front end would be a new package (`libclang-18-dev`, or `python3-pycparser`
outside the standard-library-only analysis), and CIVL and MPI-SV are not in the archive.

### (d) Other candidates

- *Whole-job pinned replay, traced.* `stretto confirm` already reruns the program with the horizon
  pinned. Tracing that rerun would give an observed continuation of every rank. But it is one
  interleaving per parallel run, nondeterministic beyond the pins, it can hang, and it supports
  only "some execution" claims. It is the right tool to *confirm* L2 findings (§6), not to build skeletons.
- *Concolic single-rank re-execution* (symbolic payloads, as in SymCC or KLEE). It would answer
  questions about payloads the trace does not have. The explorer never asks those (§1), so it buys
  nothing here and costs a symbolic engine.
- *Fork-server re-execution.* This is (b) with a checkpoint at each result point. It is an
  implementation choice, not a different skeleton (§6).

### Assessment

| | (a) taint | (b) re-execution | (c) static |
|---|---|---|---|
| gives the continuation under the alternative | no (only *whether* it depends) | yes, exactly (premise R1) | over-approximated |
| E7 abort findings | no | yes | only with an exact value analysis |
| claims it supports | dismissal of SP-unsoundness on the recorded path | some / every execution, dismissals, up to the first undetermined call | every execution (if over-approximate) |
| premise | determinism, complete wrappers | determinism R1, faithful shim R2, payloads R3, collectives R4 | soundness of the slicer and the value abstraction |
| rebuild | yes (clang) or binary tool | no | no (source needed) |
| Fortran | no / binary tool | through the MPICH bindings, as the tracer | separate front end |
| new package | clang-18, libclang-rt-18-dev | none | libclang-18-dev or more |
| cost | one instrumented run per 8 labels | one singleton run per new (rank, history) | analysis of the whole source |

**Recommendation: (b).** It is the only candidate that answers the questions L2 asks, which are
concrete and finite, exactly. It needs neither the source nor a new package, and its premise is
stated per rank and can be checked against the trace. (a) is a possible later complement: a cheap
filter that certifies "this continuation does not depend on the message" where re-execution is too
expensive, at the price of clang in the image.

## 3. Soundness of L2 by re-execution

**Premises.** In addition to the premises of every theorem of `docs/model.md` §2b (arbitrary delays,
Waitany may return any completed request, eager below B, collectives by shape, cancels as recorded,
the trace records every communication call):

- **R1 (determinism).** For each rank, the calls it makes, their arguments, the payloads it sends
  and how it ends are a function of the results its MPI calls return (statuses, payloads, indices,
  collective results), of its rank and the job size, and of inputs that are the same in the traced
  run and in every re-execution: executable, command-line arguments, environment, input files.
- **R2 (faithful service).** On the calls the shim serves, it returns what the MPI library would
  return given the same message: status source, tag and count, buffer contents, request handles as
  opaque values, `MPI_ERR_TRUNCATE` with the part that fits (the explorer's count overflow, which
  continues as under `MPI_ERRORS_RETURN`, `docs/model.md` §4b). Every call it does not serve
  ends the run as undetermined.
- **R3 (payloads).** The packed bytes the tracer records for a send (`MPI_Pack`, same library, one
  machine) are what a receive of that message unpacks.
- **R4 (collectives).** A collective's result at a member depends only on the contributions of the
  members its shape requires (P1 of `proofs.tex`).

**Program.** `rerun.Oracle(t, runner).program()` is a DSL program O. Rank i follows the trace's ops
(the same ops `dsl.program_from_trace` writes) up to its first result point. There a `dsl.Lazy` op
compares the results the explorer gave it, metadata and payload, with the recorded ones. If they are
equal the rank continues on its trace. Otherwise its continuation is the calls of a shim run on the
results so far, up to the next result point, and so on. A collective's result is served from the
trace only if the rank's call is the recorded collective of that position with the recorded
contribution. A rank whose contribution differs stops before issuing the collective, so the
collective never completes with a contribution other than the recorded ones. A continuation that
cannot be computed is `dsl.Unknown`: a call the shim does not serve, a probe or test on the trace
path, a payload the trace lacks, a different collective contribution, a rank outside `live`, a
timeout, a spent run budget, a rank not reproducible (below).

**Claim.** Fix B and an alternative pair (r, s). Every outcome of `explore(O, B, force={r: s})` is a
prefix of an execution of the real program P at B that contains the pair (r, s). It is the whole
execution, finished, deadlocked or aborted as classified, if no rank in it reached `Unknown`.
Conversely, every execution of P at B that contains the pair is represented by an outcome (same
matching, same kind) or passes, in its explored prefix, through an `Unknown` point. Hence:

- *some execution* findings (an abort, a deadlock, a departure from the trace's calls) are claims
  about P;
- *every execution* findings and dismissals are claims about P when no outcome with the pair is
  `unknown`. The report makes them only then: an `unknown` outcome is neither done nor deadlocked
  and counts against "every". A departure is a fact about the execution so far, so it stands in an
  `unknown` outcome too;
- there is no horizon. The frontier is the first `Unknown` of each execution, and with every rank
  re-executable it lies wherever the program leaves the served fragment or a payload is missing.

*Sketch.* By induction along an explored execution. A matching, completion or collective step is
a step of the MPI semantics under the model's premises (the explorer's, checked against the
unreduced explorer by the differential test T10). A rank's local step evaluates its next calls.
On the trace path its results so far are the recorded ones, so by R1 its calls are the recorded
calls, which the trace has. Off the trace, the shim's run receives exactly the results so far
(R2, R3), so by R1 it makes P's calls, with P's arguments and payloads. A collective result
served from the trace is P's by R4, since every contributing member is either on its trace path
or was checked to contribute the recorded bytes. A member that would contribute something else
never issues the collective. An abort or a crash of a run is P's, by R1. For the converse, the
explorer is exhaustive over interleavings and over the choices of wildcard matches and Waitany,
and O agrees with P on every history it is asked about. On the trace path, O is the trace itself,
so the realisation of theorem T1 is among the explored executions. The claim is about P itself,
which is stronger than L2 with a hand-written twin (there the premise "P agrees with the twin" is
never checked) and different from L1 (one program, not every consistent one, but no horizon).

**What is checked.** R1 cannot be checked in general; its consequences on the histories at hand
are. Before a rank is re-executed for the first time it is run on all its recorded results. It
must make the recorded calls with the recorded arguments and payloads and end as the trace ends
(`MPI_Finalize`, `MPI_Abort`). Every later run must repeat, call for call, what the earlier runs
and the trace say for the shorter histories it shares. A failure marks the rank as not
reproducible and turns its continuations into `Unknown`. Passing these checks is evidence for R1,
not a proof of it.

**Determinism, side effects, time, randomness.**

- *Time.* `MPI_Wtime` and the clock calls return the time of the re-execution. A program whose
  calls depend on time (adaptive tuning, timeouts) violates R1; one that only prints timings does
  not.
- *Randomness.* Seeds from time or process ids violate R1, fixed seeds do not.
- *Addresses.* Behaviour that depends on pointer values (iteration over a hash table keyed by
  address) can differ between runs. The runner disables ASLR with `setarch -R` where it may; Docker's
  default seccomp profile forbids it, so the prototype's runs in the image keep ASLR.
- *Environment.* The runs drop the launcher's variables (`OMPI_*`, `PMIX_*`, `PMI_*`, ...), which
  a singleton must not see. A program that reads them violates R1. stdin is `/dev/null`.
- *Files.* A re-executed rank does its I/O again. By default it runs in an empty scratch
  directory, so relative writes go nowhere and relative reads fail. Programs that read input files
  need `--rerun-cwd` set to the traced run's directory, and then their writes land there. Absolute
  paths are not contained: such programs belong in a container or behind an overlay.
- *Threads.* OpenMP inside a rank is fine if the MPI calls and their arguments do not depend on
  the schedule. `MPI_THREAD_MULTIPLE` traces get no alternatives at all, as at L1.
- *Ends.* `MPI_Abort`, `abort()`, a signal and an exit without `MPI_Finalize` are aborts,
  reported with the reason. A run that exceeds its timeout is undetermined.

## 4. The prototype

- **Tracer** (`tracer/mpitrace.c`): `STRETTO_PAYLOAD=<bytes>` records every send's packed message as
  `data`. For the blocking collectives of a fixed-size shape (bcast, reduce, allreduce, scan,
  exscan, gather, scatter, allgather, alltoall) it records what the rank contributes and receives
  (`din`, `dout`). Larger buffers are recorded by size only. The loader keeps them as `Send.data`,
  `Coll.din`, `Coll.dout`.
- **Shim** (`rerun/rerun.c`, `libstretto-rerun.so`, system `mpicc`, both MPIs). It serves `MPI_Init`,
  `Finalize`, `Abort`, `Comm_rank` and `Comm_size`, the four send modes blocking and not,
  `Recv`, `Irecv`, `Sendrecv(_replace)`, `Wait`, `Waitall`, `Waitany`, and the collectives above on
  `MPI_COMM_WORLD`. It writes the tracer's record fields, with request ids numbered as the tracer
  numbers them, so that a run can be compared with the trace call by call. Probes, tests, `Waitsome`,
  cancels, persistent requests, v-collectives and nonblocking collectives, communicator creation,
  windows, files and spawning end the run as undetermined.
- **Analysis** (`analysis/stretto/rerun.py`, standard library only): `Oracle` and `ShimRunner`, the
  trace path and the re-execution path above, the reproduction checks, a run cache shared across
  alternatives and thresholds, a run budget. Two DSL ops, `dsl.Lazy` (continuation computed from the
  rank's variables when it gets there) and `dsl.Unknown` (outcome `unknown`), and payloads on messages
  (`Send.data`, `Meta.data`). In `report.analyze`, `twin_label`, the undetermined continuations and
  the dismissals. A rank that has not reached its collectives is no departure when it is
  undetermined (`skeleton_divergence`).
- **Use.** `stretto analyze TRACE --rerun EXE [--rerun-args ARGS] [--rerun-mpi mpich]` on a trace
  made with `STRETTO_PAYLOAD`. L2 findings are titled `(L2 re-execution)`.
- **Tests** (`analysis/tests/test_rerun.py`): the analysis side against a Python stand-in for the
  shim. Idioms 01 and 06 and the E7 payload check agree with their hand-written twins on every
  alternative. Also covered: an aborted recorded run, a trace without payloads, a rank that does not
  reproduce, runs that disagree, an unserved call, a changed collective contribution, the minimal
  variant, the explorer's `unknown` outcome and the trace path that needs no run.

## 5. Results

`experiments/e7_rerun.py --idioms` at commit 31e57af, in the experiment image (Open MPI 4.1.6, four
processors of the second machine). It made one traced run with payloads of each of the 65 MBI
message-race templates and the 13 idioms, and judged every L1 alternative at B = 0 and B = ∞. A
second run traced each E7 program five times, so that the observed order varies
(`--repeat 5`). The tables are `experiments/results/e7_rerun.{md,csv}` and
`e7_rerun_repeat.{md,csv}`. Each judgement compares the claims of §3 (abort, deadlock or departure
in some or every execution, every execution finishing, dismissal) with those of the reference: the
hand-written twin where there is one, else the single-path program, which E7 takes as exact on
MBI's straight-line templates. "Same" means the same claims. "Weaker" means a strict subset,
because some executions were undetermined. "Other" means a claim the reference does not make.

| reference | judgements | every rank re-executable: same / weaker / other | changed rank only: same / weaker / other |
|---|---:|---|---|
| E7 twins (4 programs, 1 run each) | 24 | 24 / 0 / 0 | 24 / 0 / 0 |
| E7 twins (4 programs, 5 runs each) | 122 | 122 / 0 / 0 | 122 / 0 / 0 |
| idiom twins (01, 04, 05, 06, 08, 09) | 12 | 12 / 0 / 0 | 10 / 2 / 0 |
| single path, other MBI templates | 216 | 178 / 38 / 0 | 178 / 38 / 0 |
| single path, idioms 12 and 13 | 4 | 2 / 2 / 0 | 2 / 2 / 0 |

- **E7.** Every class 1e finding of the hand-written twins is reproduced, with its certainty:
  "aborts in every execution" in 6 of the 24 judgements (48 of 122 over the five runs), "in some
  executions" in the others. The re-executed rank aborts because the payload it unpacks is not 3,
  with no assumption that the payload is the sender's rank.
- **Dismissals.** E7's twins dismiss no alternative. On the other templates the single-path
  reference dismisses 155 judgements (every execution finishes, receives every message and keeps
  the trace's calls). Re-execution reproduces 121 of these dismissals and leaves 34 undetermined.
- **Idioms 01 and 06.** For idiom 01, the hand-written twin and re-execution both say that every
  execution finishes and that the master's replies go elsewhere than in the trace (1e). The
  single-path deadlock at B = 0 (L1+SP 1d) is therefore not a property of the program. For idiom 06,
  both say that every execution deadlocks in the `Bcast` and that the master sends an ack the
  trace does not have (1d and 1e). With only the changed rank re-executed, idiom 01's workers get
  replies with other payloads than recorded and are not rerun. The departure stands, but "every
  execution finishes" is lost: these are the 2 weaker judgements.
- **Where re-execution stays undetermined** (40 judgements, every rank re-executable). The
  `Allgatherv` and `Alltoallv` templates use a v-collective the shim does not serve (24). In
  idiom 12 it is `MPI_Comm_split` (2). The `Alltoall` templates send a `malloc`'d buffer that is
  never initialised: the recorded contribution is heap contents, the re-execution's differs, and the
  reproduction check refuses the rank (12). This is premise R1 failing, and the check catching it.
  In one `Scatter` template the traced run deadlocked inside the collective, so the trace has no
  result to serve (2).
- **No contradiction.** No judgement contains a claim the reference does not make, in either
  variant.
- **Cost.** 779 re-executions over the 78 programs, 215 s of judging in all, about 0.27 s per run.
  The four E7 programs take 13 runs and 3.5-3.8 s each. The MBI loop templates (six or seven
  alternatives each) take 121-122 runs and 33 s each.

## 6. Before it goes into the paper

- The claim of §3 as a theorem in the paper's appendix, with R1-R4 among the premises, and
  the trace-path argument written against theorem T1.
- Probes, tests and `Waitsome` as explorer choices (flags, index sets) in the DSL and the shim.
  Today they end a continuation, and on the trace path they stop the rank, which makes L2 say
  nothing on probe-heavy codes (HYPRE ParaSails, AMG).
- Communicators: virtual `Comm_dup` and `Comm_split` in the shim (the trace has their members),
  v-collectives and nonblocking collectives with their payloads.
- Cost on the applications: a fork at each result point (linear instead of quadratic
  re-execution), the tracer's overhead with payloads (not measured), and the number of distinct
  histories on AMG, Nekbone and HYPRE.
- L2 reports no 1c (orphan sends), with either kind of twin; it needs a baseline exploration like
  the L1+SP one. Until then a dismissal requires every message received.
- Confirmation of L2 findings by replay: pins from a witness execution exist for 1d; aborts need an
  expected outcome (`SIGNAL:6`, `APP-ERROR`) in `confirm.expected_outcomes`.
- Fortran through the MPICH bindings and a check of R1 under ASLR on and off, outside Docker.

## 7. Package and image changes

None. The shim is C built with the image's `mpicc` (gcc 13) for both MPIs, and the analysis
is standard-library Python. Candidate (a) would need `clang-18=1:18.1.3-1ubuntu1` and
`libclang-rt-18-dev=1:18.1.3-1ubuntu1` pinned in `experiments/container/Dockerfile`; it is not
recommended and was not built.
