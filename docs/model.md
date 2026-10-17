# Formal core (WP0 draft)

This is the working definition set behind `analysis/rematch`. Every claim here has, or must
get, a test in `analysis/tests` before it is written up as a theorem.

## 1. Trace

A trace σ is one sequence of records per rank. Each record `e` has two nodes, `issue(e)` and
`complete(e)`. Blocking operations complete at their own record; a nonblocking operation
completes at the wait/test record that completed its request.

Objects:

- send `s = (rank, ordinal)`: dst, tag, comm, bytes, blocking?
- receive `r = (rank, ordinal)`: src ∈ ranks ∪ {ANY}, tag ∈ tags ∪ {ANY}, comm, capacity; and,
  if it completed, the observed metadata (msrc, mtag, mcount)
- collective `c`: name, comm, ordinal on that comm; the k-th collective on a comm across all
  ranks forms one match set `cmatch(C)`

`r accepts s` iff dst(s) = rank(r), comm agree, src(r) ∈ {ANY, rank(s)}, tag(r) ∈ {ANY, tag(s)}.

The observed matching `M₀` is reconstructed offline: receives per rank in posting order, each
takes the earliest unconsumed send from (msrc → rank, comm, mtag). Non-overtaking makes this
exact (`trace._reconstruct_observed`).

## 2. Alternative matching and its feasibility

An alternative matching `M'` is a partial injective map receives → sends with `r accepts M'(r)`.
Buffering threshold `B ∈ ℕ ∪ {∞}`: a send with bytes > B completes only once matched.

`M'` is **feasible at B** (`feasible.check`) iff, over the kept prefix `H` (section 3):

1. **injective**: no send consumed twice;
2. **non-overtaking (sender side)**: if `M'(r) = s`, every earlier send `s'` from the same
   (src, dst, comm) that `r` accepts is matched to a receive at dst posted before `r`;
3. **receiver order**: every receive `r'` at dst posted before `r` that accepts `s` is matched
   (to something other than `s`);
3'. **receiver-side order, strengthened**: if `r'` is posted before `r`, accepts `s`, and
   `M'(r') = s'` comes from another source, then `s'` must arrive before `s`: edge
   `issue(s') → complete(r)` (this is MOPPER's "conditional matches-before" edge);
4. **acyclic**: the constraint graph on `H` has no cycle, where the edges are
   - program order: `issue(e) → complete(e) → issue(next)`;
   - delivery: `issue(s) → complete(r)` for every `(r, s) ∈ M'` whose receive completed;
   - rendezvous: `issue(r) → complete(s)` when the send's request completes only once matched:
     `ssend`/`issend` always, `bsend`/`ibsend` never, otherwise bytes(s) > B; ready-mode sends
     add `issue(r) → issue(s)`;
   - collectives, by shape: barrier-like (barrier, allreduce, allgather(v), alltoall(v/w),
     reduce_scatter) all-to-all; bcast/scatter(v) root → others; reduce/gather(v)
     others → root; scan/exscan i → j for i < j. Unknown collectives are treated as barriers,
     which is sound for feasibility claims but *not* for deadlock claims (the family-②
     report uses guaranteed edges only);
   - probes: `issue(seen) → complete(probe)` for the send the probe observed.
Matching-order obligations (2, 3, 3') are evaluated for every receive *posted* inside `H`,
whatever its completion index: MPI decides matching at posting time. Operations that never
completed in the observed run (blocked calls, unwaited requests) contribute program-order nodes
only. Conditions 1–4 are checked over all breakpoints {0} ∪ message sizes ∪ {∞}: the report
prints the feasibility profile rather than assuming monotonicity in B.

Cost. The generic check (`feasible.check`) builds the closure and the graph from scratch,
O(n + m) per alternative and threshold; it is the reference. The fast checks
(`rematch.fast`) answer the same questions for single-change alternatives from indexes:
the closure runs as a worklist over ranks, where a cut on one rank finds the earliest
receive, rendezvous sender, collective or probe it drags along by a suffix-minimum query on
a per-pair-of-ranks list (O(P² log n) per alternative); matching order follows from the
kernel that enumerated the candidate (the send is the earliest unconsumed one of its
source), so only the receives of the changed rank are looked at; and acyclicity reduces to
one reachability query on the observed graph of the threshold, answered by vector clocks
computed once per threshold, because the closure removes every node through which an added
edge could otherwise return (an added delivery or probe edge ends at a node whose kept
successors are empty; only the rendezvous edge `issue(r) → complete(s)` can close a cycle,
iff `complete(s)` reaches `issue(r)` in the observed graph). Collectives enter the graph
through hub nodes, 2P edges each. Preparation is O(n log n) per trace and O((n + m) P) per
threshold; the tests compare the fast and the generic verdicts on every alternative of every
traced idiom. (Relation to POPL'26 Thm 1.9: with rf fixed and every channel synchronous,
consistency is acyclicity of the send-receive graph; MPI's per-pair single-producer queues
keep it linear for mixed regimes.)

## 2b. Theorems

Statements in the form of *Soundness of Predictive Concurrency Analyses* (OOPSLA'25): a trace,
a class of programs consistent with it, and a claim about every program of the class. Each
theorem names the test that exercises it; the proofs are sketched here and written out in the
paper.

**Programs consistent with a trace.** A program P is consistent with trace σ on a prefix H
(one prefix per rank) if every execution of P that reaches, on each rank, the same sequence of
MPI calls with the same arguments and the same completions as σ does on H, continues to issue
exactly the calls σ records on H. The single-path class SP(σ) is the one program whose every
rank issues σ's calls unconditionally. L1 makes no assumption beyond consistency on H.

**Theorem T1 (soundness of the L1 check).** Let M' be a matching, R* the receives whose match
differs from M₀, and H the kept prefix of §3 for (M', R*, B). If `feasible.check(σ, M', R*, B,
L1)` accepts, then for every program P consistent with σ on H there is an execution of P at
threshold B in which every operation of H is issued and whose matching agrees with M' on every
receive *judged* by H: a receive posted inside H that M' leaves unmatched, or whose M' message
is issued inside H (a match is decided once message and receive are both present, so a
nonblocking receive whose wait lies beyond H is judged all the same).
*Sketch.* The constraint graph on H is acyclic, so it has a topological order; replay the
operations of H in that order. Program order is respected by construction. Every delivery edge
`issue(s) → complete(r)` puts the send before the receive's completion; the matching-order
conditions 1–3' make each kept receive, when it completes, the earliest posted receive at its
rank that accepts the message and the message the earliest unconsumed one of its source that
the receive accepts, which is exactly MPI's matching rule (§3.5 of the standard: non-overtaking,
per communicator and source), so the MPI library has no choice but M' on the judged receives;
the obligations are evaluated for every receive posted inside H, which is why the claim can
cover receives that complete beyond it; rule 6 removed every receive that a pending receive of
its rank would have deprived of its message, so no kept receive is served out of posting order
(E6 found this gap on a synthetic trace before the rule existed: a pending Irecv from ANY/ANY
followed by a specific receive whose message the Irecv accepts). Rendezvous edges
`issue(r) → complete(s)` put each blocking send's completion after its receive is posted, so no
send of H blocks forever; the closure (rules 2–6) removed every operation whose completion H
cannot guarantee, so the order contains no completion the library would withhold. Consistency
with σ on H means P issues the operations of H in that order. Tests: `CheckerSoundness` (every
accepted alternative of every traced idiom is realised by the explorer) and E6 on the corpus
traces and on synthetic traces; evidence so far: 0 unsound pairs.

**Theorem T2 (monotonicity in B).** If a single-change alternative M' (one wildcard receive
re-matched, §2) is feasible at B, then it is feasible at every B' > B, and the kept prefix at B'
contains the kept prefix at B.
*Sketch.* A send blocks at B' only if it blocks at B (`send_blocks` is antitone in B), so rule 3
removes at B' a subset of what it removes at B; rules 1, 2, 4, 5 and 6 do not mention B and are
monotone in the prefix (rule 6 fires on a receive whose send is removed, and removal only
shrinks as B grows); the closure is the greatest fixpoint, hence H(B) ⊆ H(B'). Matching
order is evaluated per kept receive and does not mention B. On the changed receive's rank the
cut comes from rule 1 and is the same at every B, so the receives judged there are the same;
a receive kept only at B' lies on another rank, where M' coincides with M₀, and its obligations
(earlier sends of its source, earlier receives of its rank) involve only operations on which M'
and M₀ agree, so they hold as they held in σ. The graph at B' over H(B') has the edges of the
graph at B over H(B), minus rendezvous edges that disappear, plus edges among and into
H(B') \ H(B); a cycle at B' cannot lie within H(B) (a subset of an acyclic graph) and cannot
enter H(B) from outside (a node of H(B) has all its predecessors in H(B): that is what the
closure computes), so it would lie in H(B') \ H(B), where every edge is one of σ's own
constraints, acyclic because σ happened. Tests: `CheckerSoundness.test_*_monotone_in_B` (full
profile monotone, bisection equal) and E2 (profiles that are not monotone: 0 on 516 traces).
Consequence: for single-change alternatives the bisecting profile is exact and the full one is
unnecessary; the report therefore profiles each alternative at the two ends of the range and
bisects between them (`rematch analyze --full-profile` judges every breakpoint and is the
differential: equal on all 516 traces). Whether the statement extends to arbitrary M' is open
(an operation newly kept on a rank where M' differs from M₀ could carry a new obligation), so
two-change alternatives are profiled at every breakpoint.

**Proposition T3 (deadlock predictions are not monotone).** The set of thresholds at which the
observed run deadlocks is not an interval in general: idiom 03 (head-to-head exchange)
deadlocks below 400 bytes and not above; the loop races of MBI deadlock under the stolen
match only when the stolen message is buffered (B ≥ 4), since below that the steal itself is
infeasible. Both are witnesses, no proof needed.

**Theorem T4 (cost).** For a trace with n records, m constraint edges, P ranks and w wildcard
receives: the generic check costs O(n + m) per alternative and threshold; the fast check costs
O(P² log n) per alternative and threshold after O(n log n) preparation per trace and
O((n + m)·P) per threshold; the non-overtaking kernel yields at most one candidate per (wildcard
receive, source), so the number of alternatives is at most w·P; a run of the analysis over the
breakpoints of a trace with k distinct message sizes costs O(k·(w·P·P² log n + (n + m)·P)) when
every breakpoint is judged and, by T2, O(w·P·P² log n · log k + k'·(n + m)·P) with the bisecting
profile, k' ≤ k being the thresholds any item visits (two for an alternative whose two ends
agree).
Collectives contribute O(P) edges each through hub nodes. The matching problem with M' given is
therefore linear per alternative for any number of changed receives; what grows with the number
of changed receives is the number of alternatives, and the choice of which receives to change
is the problem that is NP-complete in general (MOPPER's), which this work does not solve.
*Sketch.* By construction of `rematch.fast` (§2, Cost); the measured scaling is in E4 and in the
tests `C_FastEqualsGeneric`. Evidence: a synthetic master-worker trace with 256 wildcard
receives takes 0.2 s; AMG at 8 ranks (87 000 events, 53 wildcard receives, 7 387 probes, about
260 message sizes) takes 160 s and 0.5 GB on the development host.

**Lemma T5 (the single-path assumption is unsound on metadata-dependent code).** There are two
programs consistent with the same trace σ on the whole trace, one of which deadlocks under an
alternative matching and one of which does not. Idiom 01 (the reply goes to `MPI_SOURCE`) and
idiom 06 (a collective guarded by `MPI_SOURCE`) are the witnesses: SP(σ) deadlocks, the real
program does not (01) or deadlocks for another reason (06); E3 lists them. Consequence: an
L1+SP finding is a claim about SP(σ), never about the program; the report labels it so.

## 3. Horizon as a causal closure (level L1)

Given `M'` and the set `R*` of receives whose match changed, the kept prefix `H` is the largest
set closed under (`horizon.compute_cut`):

1. for `r ∈ R*`: everything after `complete(r)` in its rank is removed (the payload and metadata
   differ; the trace does not determine the continuation);
2. a receive whose `M'` send is removed, or which has no `M'` send, does not complete;
3. a send with bytes > B whose receive is removed or absent does not complete; the receive is
   present when its *posting* is kept, since a rendezvous send matches a posted receive
   whether or not the receive's wait is kept;
4. a collective completes for a member only if the members its shape requires (§2: everyone,
   the root, everyone for the root, the members before it) issue it inside the kept prefix;
   otherwise that member's completion is removed;
5. a probe whose observed send is removed observes something else: the probe and what
   follows it are removed;
6. a receive posted inside the prefix whose `M'` send is not issued there (removed, or absent)
   stays pending through the prefix, so a later receive of its rank cannot take a message the
   pending one accepts (the standard's second non-overtaking clause, §3.5: the later receive
   "cannot be satisfied by this message if the first one is still unmatched"): that receive
   and what follows it are removed. The rule is what lets the claim cover receives that
   complete beyond the prefix: a pending receive competes for every message its rank gets.

A blocking send removed by rule 3 at its own record is still *issued*: the rank never gets
past it, but its envelope is visible to a probe. A probe alternative (`probe_alt`: the probe
observes send `s` instead of the one it saw) is feasible at B iff `s` is issued inside the
kept prefix, the probe is kept, and the graph with `issue(s) → complete(probe)` in place of the
observed probe edge is acyclic; a completion-order alternative (Waitany/Waitsome completing
another index) cuts after that record and adds `issue(s_j) → complete(wait)`. Both are
profiled over the breakpoints like receive alternatives.

`R*` must survive the closure (`witness_kept`): a blocking changed receive must complete inside
H, a nonblocking one must be posted inside H with its new message issued inside H (the match
is decided then; the wait that completes it may lie beyond H, as when a later blocking
receive of the rank never completes). Otherwise the alternative is not realisable within what
the trace determines. Consequence, by design: a *swap* of two receives on one rank
cuts at the earlier one, so only the first re-match is certified at L1 (the audit's finding 13);
the second is a claim about the continuation and belongs to L1+SP or L2. A receive that never
completed in the trace imposes nothing on the closure (rule 2 applies to completed receives),
so a leaked request does not silence its rank; but a never-completed receive posted before `r`
that accepts `s` does block `r <- s` (it would take the message).

Level **L1+SP** (MOPPER's setting) drops rule 1: the continuation is *assumed* identical, so
an operation removed by rules 2–6 is one its rank never completes: a hang. `feasible.check`
in SP mode therefore rejects a matching whose closure removes anything, naming the rank and
the operation; that is how the observed run's buffering profile (§4) detects a rendezvous
send that nobody receives. For the consequences of an alternative the prototype runs the
exhaustive explorer over the trace as a straight-line program with the changed match forced
(`report.analyze`), and compares them with the program's executions without the change: a
deadlock that every unforced execution has, or an orphan send that every unforced execution
leaves, is the observed run's own and is not attributed to the alternative. Each L1+SP
finding says whether *every* execution containing the pair has the consequence or only
*some*, and its pin fixes every wildcard receive of one witness execution, so that a replay
realises that execution rather than the first changed pair alone. Idioms 01
and 06 show that this assumption is unsound on metadata-dependent code: the explorer reports a
deadlock that the real program cannot reach (01) or reaches for a different reason (06).

Level **L2** is represented by the DSL twins in `analysis/tests/idioms_dsl.py`: the same
programs with their metadata dependencies (`Send(dst=lambda env: env["st"].src)`,
`If(lambda env: env["st"].src == 1, ...)`). The explorer over a twin is the ground truth for
what a skeleton-aware analysis should predict.

## 4. Observed run and family ②

The observed matching itself may be infeasible at small B (idiom 03): the smallest breakpoint
at which `observed_feasible_at(M₀, B).ok` holds is the threshold below which the run cannot be
realised, and the report states "deadlocks on any implementation with eager threshold < X".
E2 replays such programs with the implementation's eager limit forced down (`experiments/
b_impl.py` measures the effective sizes); a bound below the smallest eager size the transport
accepts (256 bytes on Open MPI 4.1.6) cannot be tested that way.

## 4b. Errors are not terminal

The explorer records a count overflow or a collective name mismatch as an error on the path
and continues (as under `MPI_ERRORS_RETURN`); the terminal outcome carries the errors. This keeps
every transition commutative, which the persistent-set reduction needs; a differential test
against the unreduced explorer (`tests/test_explorer.py`, T10) checks the outcome sets agree on
the idiom twins and 200 random programs.

## 5. What the explorer is

`explore.explore(program, B, force)` is an exhaustive interleaving explorer with a persistent-set
reduction: branch evaluation, send issue, receive posting, enabled waits, enabled collectives,
and matches of specific-source receives (unique by non-overtaking) are taken alone; only
wildcard-source matches and `Waitany` choices branch. Collectives complete by the same shapes
as the checker's edges (§2): a bcast root returns once it has issued, a reduce root waits for
everyone, a scan member for those before it; a collective without a known root synchronises
everyone. A ready-mode send issued before its receive is posted is an error on the path, and
since that depends on the order of posting and issue, a program with a ready-mode send is
explored without the reduction. Outcomes: done, deadlock, truncate, rsend-no-receive,
collective-mismatch, each with its matching, its orphan sends and one witness execution.
Ground truth for 2–4 ranks; the differential test T10 compares the reduced and the unreduced
explorer on the idiom twins and on random programs with every send mode, sub-communicators,
shaped collectives and metadata branches.

## 6. Open questions

- Multi-change alternatives: the check is linear for any M' (T4); the enumeration of
  k-change alternatives by kernel heads on distinct ranks is the next rung, and its count the
  question.
- L2: the minimal skeleton. The twins show what is needed: destination-of-send and
  branch-guarding-a-collective as functions of `MPI_SOURCE` / `Waitany` index; a value
  skeleton (`payload == sender rank`) for the benchmark-shaped races.
- Buffer races (family ③): request spans are in the trace; accesses are not.
- MPI_THREAD_MULTIPLE: per-rank program order is not total; the trace records thread ids and
  the analysis stops at observed findings.
