# Formal core (WP0 draft)

This is the working definition set behind `analysis/stretto`. Every claim here has, or must
get, a test in `analysis/tests` before it is written up as a theorem.

## 1. Trace

A trace σ is one sequence of records per rank. Each record `e` has two nodes, `issue(e)` and
`complete(e)`. Blocking operations complete at their own record; a nonblocking operation
completes at the wait/test record that completed its request, or at the first
`MPI_Request_get_status` that found it complete, if that comes earlier: a true flag is a result,
like a successful test's, so the match precedes it and rule 1 cuts after it (before 22 Oct 2026
such queries were dropped, and an alternative could need the match after the query that had
seen it done; `C_RequestGetStatus`). A false flag, like a failed test, says nothing, since the
model guarantees no progress.

Objects:

- send `s = (rank, ordinal)`: dst, tag, comm, bytes, blocking?
- receive `r = (rank, ordinal)`: src ∈ ranks ∪ {ANY}, tag ∈ tags ∪ {ANY}, comm, capacity; and,
  if it completed, the observed metadata (msrc, mtag, mcount)
- collective `c`: name, comm, ordinal on that comm; the k-th collective on a comm across all
  ranks forms one match set `cmatch(C)`

`r accepts s` iff dst(s) = rank(r), comm agree, src(r) ∈ {ANY, rank(s)}, tag(r) ∈ {ANY, tag(s)}.

Operations on `MPI_PROC_NULL` return at once and are no objects of the model: sends, receives,
probes (before 22 Oct 2026 a probe of `MPI_PROC_NULL` was loaded as an `ANY_SOURCE` probe) and
the `MPI_Mrecv`/`MPI_Imrecv` of the message such a probe returns.

Creating and freeing communicators. The constructors and `MPI_Comm_free` are collective (MPI
4.1 §7.4), and a member may have to wait in one until the others have entered it:
`MPI_Comm_split`'s result depends on every member's colour and key, and Open MPI and MPICH agree
on the new context by an allreduce; the standard leaves the rest open. Each such call is a collective
`comm_<call>` in the sequence of collectives of the communicator it is collective over: the
parent's (split, split_type, dup, idup, create, cart_create, cart_sub, graph_create,
dist_graph_create and its adjacent form, intercomm_merge), the result's for the two that only
its members call (create_group, intercomm_create, over both groups), and the freed one's. Its
shape is unknown: a barrier in the graph, the horizon (rule 4) and the explorer, and no edge
of the graph of guaranteed edges. That is the sound direction for acceptance: the barrier only
adds constraints, and a schedule in which the creation synchronises is an execution of an
implementation that synchronises less too, since delays are arbitrary; a deadlock claim never
rests on it (a rank cut at a creation is not one at which a contained deadlock stops). An
`MPI_Comm_idup` is a nonblocking collective from its call to the completion of its request. A
trace of the tracer before 22 Oct 2026 (format version 1) has no record of the idup's call,
and the collective is placed at the completion, after its real issue, which constrains more;
nor does it have a record at a member that obtained `MPI_COMM_NULL`, so a split, split_type,
create, cart_create or graph_create cannot be placed at every member, and such a trace counts
as incomplete (observed findings only). Before 22 Oct 2026 creations were side records with no
synchronisation, and an alternative could need a rank out of a split before another had
entered it (`C_CommunicatorCreation`).

The observed matching `M₀` is reconstructed offline: receives per rank in posting order, each
takes the earliest unconsumed send from (msrc → rank, comm, mtag). Non-overtaking makes this
exact (`trace._reconstruct_observed`). A send whose cancellation succeeded never reached its
destination and takes no part in it (before 22 Oct 2026 it did: a receive was paired with the
withdrawn message, and the message it really took became an orphan).

## 2. Alternative matching and its feasibility

An alternative matching `M'` is a partial injective map receives → sends with `r accepts M'(r)`.
Buffering threshold `B ∈ ℕ ∪ {∞}`: a send with bytes > B completes only once matched.

`M'` is **feasible at B** (`feasible.check`) iff, over the kept prefix `H` (section 3):

1. **injective**: no send consumed twice;
2. **non-overtaking (sender side)**: if `M'(r) = s`, every earlier send `s'` from the same
   (src, dst, comm) that `r` accepts is matched to a receive at dst posted before `r`, and that
   match comes first: edge `m(M'⁻¹(s'), s') → m(r, s)`;
3. **receiver order**: every receive `r'` at dst posted before `r` that accepts `s` is matched
   (to something other than `s`);
3'. **receiver order on matches**: such an `r'` is matched before `r` takes `s`: edge
   `m(r', M'(r')) → m(r, s)` (MOPPER's "conditional matches-before", stated on matches);
4. **acyclic**: the constraint graph on `H` has no cycle. Its nodes are `issue(e)` and
   `complete(e)` for the operations of `H` and a **match node** `m(r, s)` for every pair of
   `M'` whose receive is posted and whose send is issued in `H`. A blocking receive is matched
   in its own call, so its completion node serves as its match node (reachability is the same);
   a nonblocking receive has a node of its own, since its wait comes later. The edges are
   - program order: `issue(e) → complete(e) → issue(next)`;
   - match: `issue(r) → m`, `issue(s) → m`, `m → complete(r)`;
   - rendezvous: `m → complete(s)` when the send's request completes only once matched:
     `ssend`/`issend` always, `bsend`/`ibsend` never, otherwise bytes(s) > B; ready-mode sends
     add `issue(r) → issue(s)`;
   - order: the edges of 2 and 3'. They are built from the latest earlier member of each
     envelope class that accepts `s` (source `rank(s)` or ANY, tag `tag(s)` or ANY) and the
     latest earlier send of each tag of `s`'s channel that `r` accepts; within a class each
     member accepts what a later one accepts, so the order reaches the rest of the class
     through the members' own pairs (`Trace.earlier_competitors`, `Trace.earlier_sends`);
   - collectives, by shape: barrier-like (barrier, allreduce, allgather(v), alltoall(v/w),
     reduce_scatter) all-to-all; bcast/scatter(v) root → others; reduce/gather(v)
     others → root; scan/exscan i → j for i < j. Unknown collectives are treated as barriers,
     which is sound for feasibility claims but *not* for deadlock claims (the family-②
     report uses guaranteed edges only);
   - probes: `issue(seen) → complete(probe)` for the send the probe observed, and the order
     edges of 2 and 3' into `complete(probe)`: the matches of earlier receives of the rank that
     accept `seen`, and of earlier messages of its channel that the probe accepts, come first.
   Collectives of a root shape without a recorded root synchronise everyone, as in the horizon
   and the explorer.

Why match nodes. Matching order binds the *match* of the later receive, not a completion. With
the order edges attached to `complete(r)` (as before 20 Oct 2026), two shapes were accepted
that MPI cannot produce: a pending nonblocking receive overtaken by a later one, and a
rendezvous send matched after its own completion (the counterexamples under T1 below, now
`C_MatchNodes` in `analysis/tests/test_checker.py`).
Matching-order obligations (2, 3, 3') are evaluated for every receive *posted* inside `H`,
whatever its completion index: MPI decides matching at posting time. Operations that never
completed in the observed run (blocked calls, unwaited requests) contribute program-order nodes
only. Conditions 1–4 are evaluated at the breakpoints {0} ∪ message sizes ∪ {∞}: the report
prints the feasibility profile, bisected between the two ends by theorem T2 (§2b) and judged
at every breakpoint on request (`--full-profile`, the differential).

Cost. The generic check (`feasible.check`) builds the closure and the graph from scratch,
O(n + m) per alternative and threshold; it is the reference. The fast checks
(`stretto.fast`) answer the same questions for single-change alternatives from indexes:
the closure runs as a worklist over ranks, where a cut on one rank finds the earliest
receive, rendezvous sender, collective or probe it drags along by a suffix-minimum query on
a per-pair-of-ranks list, O(P log n) per lowering of a rank (and a scan of the rank's later
collectives, in which a member stays until its own issue of a group is cut, since a
nonblocking collective issued later can complete earlier; before 22 Oct 2026 a member left
the scan at the first completion it lost, and the other group of an intercommunicator was not
scanned, `C_CollectiveRule`). Those lists name the competitor of a pending receive by the message each later
receive took in the run, so the changed receive, which now takes another message, is looked up
apart: per envelope class, the earliest receive left pending by the cut among the receivers of
the removed sends (`Indexes.r6c`), which deprives the changed receive if it is posted before it
and accepts the new message. Before 22 Oct 2026 the fast check missed that case and accepted a
steal that MPI cannot produce, with a pending Irecv(ANY, tag 1) before a blocking Recv(ANY,
ANY) that steals a tag-1 message (`C_RematchedCompetitor`). Matching order follows from the
kernel that enumerated the candidate (the send is the earliest unconsumed one of its
source), so only the receives of the changed rank are looked at; and, for a steal at a
*blocking* receive, acyclicity reduces to reachability queries on the observed graph of the
threshold, answered by vector clocks computed once per threshold (match nodes carry no
timestamp of their own, like hubs; a row of the clock is stored only where a predecessor on
another rank joins, and the nodes between two joins share it). The worklist takes the rank
whose first removed record is earliest in the Lamport times of the observed run at B = ∞
(`Indexes.lamport`): a removal makes the operations that need the removed one lose their
completion, and those come later, so a rank is usually processed once, at the cut where it
ends, and a closure costs O(P² log n). The order is a heuristic, not a bound: rule 6 cuts at
a receive's posting and rule 2 at the call of the wait that completes a receive, which can
precede the removed send in that order, so a rank can be processed again, up to once per
record. Nor is O(P log n) a bound on one processing: the scan of rule 4 can visit every later
collective of the rank, with all its members, and when the competitor a suffix query names is
re-matched, dropped or the changed probe, rule 6 judges each pending receive of the suffix
alone. The worst case proved is therefore quadratic in n per closure, and O(P log n) per
lowering, O(P² log n) per closure in Lamport order and O(n P log n) per closure are what the
measurements below show, not bounds (theorem T4). Taken last in first
out, as before 22 Oct 2026, the closure of a probe of AMG's trace processed each rank 4.0
times on average at 16 processes, 5.5 at 32 and 7.7 at 64, so that its cost grew with the
trace (5, 27 and 190 ms); in Lamport order each rank once (2, 7 and 34 ms). The greatest
fixpoint does not depend on the order (the appendix's lemma on the worklist); only the reason
recorded first for a rank does, so a closure that records the single-path hangs (the observed
run's verdict) keeps the old order and its texts. Rule 1 cuts the rank right after the receive, so the
only kept successor of the new match is the completion of a rendezvous send, and a cycle
exists iff that completion reaches one of the match's predecessors: the receive's issue or
the match nodes of its order edges. Reachability in the observed graph over the whole trace
over-approximates reachability in the alternative's graph minus the new match, so the test
is sound (it may reject an alternative the generic check accepts). Steals at nonblocking
receives, whose match has later receives of the rank as successors, and swaps go to the
generic check. Collectives enter the graph
through hub nodes, 2P edges each. Preparation is O(n log n) per trace and O((n + m) P) per
threshold. The fast check is a separate algorithm, and nothing proves it equal to the generic
check, for which the theorems are proved; what supports it is a comparison of the two. The
tests compare their verdicts, and horizons where they accept, on every alternative of every
traced idiom and of the module's traces, and on random runs drawn with the shapes on which
the fast check was found wrong on 22 Oct 2026 (`C_FastEqualsGenericRandom`: Irecvs that a
message sent later reaches, ANY_TAG receives after them, swaps, nonblocking collectives waited
out of issue order, probes). The report also confirms each acceptance of the fast check with
the generic check, on traces of up to 1 000 records and on any trace with `stretto analyze
--cross-check`; a disagreement is reported and the generic verdict used
(`report._profiles_fast`). The corpus programs, the idioms and E6's programs are below that
size; on HYPRE 2.10.1's trace at 8 processes (about 6 900 records) the cross-check makes the
text report about nine times slower and the full profile forty times. (Relation to POPL'26 Thm 1.9: with rf fixed and every channel synchronous,
consistency is acyclicity of the send-receive graph; MPI's per-pair single-producer queues
keep it linear for mixed regimes.)

Probe closures in a chain. The fast check of a probe alternative (p, s) splits it in two. The
horizon is the closure of the observed matching with rule 1 cutting p's rank right after p,
in which p itself is exempt from rules 5 and 6, since it reports s and not the message it saw
(`Observed.closure(changed_probes=[p])`); it does not depend on s. Before 22 Oct 2026 the
exemption held where a pending receive is judged alone but not in the suffix-minimum query over
the receivers of a cut rank's sends, which named p by the message it saw, so the probe's own
closure could cut p where the generic check keeps it (completeness only;
`test_probe_closure_exempts_the_probe_in_the_suffix_query`). What does depend on s is
judged on that horizon, per alternative (`Observed.check_probe`): p is kept, s is issued
inside the horizon, no earlier receive of p's rank without a message accepts s, and no earlier
receive that accepts s has lost its message to the horizon (it would stay pending and take s:
rule 6 for s's envelope, which the generic check builds into its closure). The closure part is
shared between the probes of a rank. Write F_c for the rules with rule 1 cutting at c, without
the exemption; F_c is deflationary and monotone (appendix, lemma on the worklist), and for
c1 ≤ c2 on one rank F_c1 ≤ F_c2 pointwise, as cutting earlier only adds a target. Every
fixpoint Z of F_c1 then satisfies Z = F_c1(Z) ⊆ F_c2(Z), so it lies below gfp(F_c2), and
gfp(F_c1) is the greatest fixpoint of F_c1 below min(c1, gfp(F_c2)): the worklist reaches it
from that prefix, and as F_c1 and F_c2 differ in rule 1 only, the only work left is the new cut
and what it removes. The report takes each rank's probes last to first
(`report._profiles_fast`), so each closure continues the previous one
(`Observed.probe_closure`): on AMG's trace a chained closure processes 3.8 ranks on average at
16 processes, 4.7 at 32 and 5.7 at 64. A rank whose cut moved by at most 2P records since it
was last processed has those records taken one by one, the rules applied to each, instead of
the suffix-minimum queries over its whole removed suffix, whose earlier part was applied
before. A chained closure equals the probe's own exempt closure whenever it keeps p: the
exemption drops only rule instances that target p, which then did not fire, and since every
condition of a rule says that some event is not in the prefix, they do not fire on the exempt
closure either, which contains the chained one; so each is a fixpoint of the other's rules.
When the chain cuts p, the probe's own closure is computed instead (on AMG's traces at 16, 32
and 64 processes, at B0 and at B = ∞, the chain never cut a probe; on random runs it does below
B0, where the observed run is not realisable). Along a chain the cut of each rank only
decreases, so the closures of all the
probes of a rank together lower each rank at most once per record, n lowerings per rank and
threshold, against that many per probe for unchained closures in the worst case (each at the
cost of one processing, O(P log n) as measured, more in the worst case: see the cost
paragraph above). Tests:
`closure_orders_agree` on the idioms, the module's traces, E6's random programs and random runs
with many probes per rank (`C_ClosureOrderRandom`), and `test_probe_chain_that_cuts_the_probe`.

Pruning. `matching.candidates_full` drops a kernel head s of a wildcard receive r when the
completion c(r) reaches issue(s) in the observed graph at B = ∞ (`observed_hb`, or the vector
clocks of `fast.Observed`): an execution with r ← s has issue(s) → m(r, s) → c(r). The observed
graph stands in for the alternative's, so the path may not use an edge the alternative drops,
those of the pairs (r, M₀(r)) and (r₂, s), r₂ the receive that took s in σ. As the observed
graph is acyclic, an edge into or out of m(r₂, s) follows issue(s) and lies on no path into
it, and one at m(r, M₀(r)) precedes c(r), except at a blocking r, where m(r, M₀(r)) is c(r)
itself and the path's first edge may be dropped. That leaves the ready-mode edge
issue(r₂) → issue(s), which the alternative replaces by issue(r) → issue(s): for a ready send
matched in σ the query asks whether c(r) reaches the program-order predecessor of issue(s)
(`matching._follows`). A pair pruned so is one the check rejects at every threshold. Every
later edge of the path is an edge of the alternative's graph whose source the horizon keeps
whenever it keeps the target (rules 2–7 are written so), and the first leaves c(r) for a node
the horizon removes: the next record of r's rank, a later receive or probe there (rule 1), or
the completion of M₀(r) as a synchronous send that now has no receive (rule 3). So the horizon
removes issue(s) and the witness is cut; with read bounds (rule H1') the path can lie inside the
horizon, and then it closes a cycle with issue(s) → m(r, s) → c(r). Before 22 Oct 2026 the
query did not set the ready edge aside: E8b found a steal of a ready send that the check
accepts and the explorer realises pruned (synthetic program 037; `C_PruneOnKeptEdges`, and
`C_EnumerationComplete` over E6's programs).

Ready sends. MPI 4.1 §3.4 lets a ready-mode send start only once its matching receive is
posted, and the model says so of the receive the send is matched to: the edge
issue(r₂) → issue(s) and rule 7. Before 22 Oct 2026 the explorer was looser: it raised
rsend-no-receive only when no posted receive accepted the send at its issue, so a ready send
issued while a wildcard that accepts it was pending, then taken by a receive posted later, was
correct to it (E6's synthetic program 017: the pending Irecv(ANY, ANY) takes the sender's
earlier message). Such executions can realise a pair the check rightly rejects, and can hide an
accepted pair that no correct execution realises; E6's generator also drew its traces from
them. The explorer now keeps with each pending ready send the receives posted at its issue that
accept it and checks the receive that takes it at the match, where that receive is first known
(deciding at the issue would need a guess of which receive will take it, one more branch per
send); a send that keeps no such receive, because each took another message or none was
posted, is erroneous whatever follows, and so is one that nothing takes by the end of the path.
E6 and E8b count an execution only if every ready send is correct in it that the alternative's
horizon keeps or that must be issued before a pair they compare is matched (such a pair lies
beyond the horizon when the check rejects the alternative; `model_executions` in
`experiments/e6_checker_vs_explorer.py`); any other ready send is not the claim's business.
Tests `C_ReadySendBeforeItsReceive` and `T14_ReadyMode`.
A recorded run whose every execution with the observed matching posts r₂ after issuing s (over
guaranteed edges at B = ∞, the graph without ready edges reaches issue(r₂) from issue(s)) did
issue the ready send before its receive; Open MPI and MPICH usually deliver such a send, so the
run may finish. Its observed graph is cyclic at every threshold: theorem T2 has no B0, the
pruning no acyclic graph to argue on, the fast checks no vector clocks. The report states an
observed finding of class 2-ready, or one for a set of ready sends whose edges close a cycle
together and of which at least one is erroneous, and predicts nothing else
(`matching.observed_ready_errors`). Before 22 Oct 2026 it failed there with a ValueError; on a
graph cyclic for another reason `fast.Observed.reaches` now answers by search, as `observed_hb`
does.

## 2b. Theorems

Statements in the form of *Soundness of Predictive Concurrency Analyses* (OOPSLA'25): a trace,
a class of programs consistent with it, and a claim about every program of the class. Each
theorem names the test that exercises it; the proofs are sketched here and written out in the
paper.

**Programs consistent with a trace.** A program gives each rank a function from the results
its calls have returned so far (request handles, statuses with source, tag, count and data,
test and probe flags, Waitany indices, cancel flags) to its next MPI call. A program P is
consistent with trace σ on a prefix H (one prefix per rank) if, for every rank and every record
k of that rank in H, P's function applied to the results σ records for that rank's records
0..k-1 yields record k's call. The single-path program SP(σ) issues σ's calls whatever they
return. L1 makes no assumption beyond consistency on H. (The earlier wording, "every execution
that reaches the same calls continues to issue them", was circular; audit of 20 Oct 2026.)

Premises of every theorem below, stated because the proofs use them: message delays are
arbitrary (any interleaving the matching rules allow is an execution); a Waitany or Waitsome
may return any completed request; a send that does not block at B completes at once
(unbounded buffering below the threshold); collectives synchronise by their shape and do not
depend on B; no cancel succeeded in σ; and σ records every communication call of the run.

The report enforces the last premise as far as the trace shows it, and states observed findings
only (with a note that names the reason) when it fails: a call the tracer marks `unmodelled`
(one-sided windows, neighbourhood collectives, dynamic processes, generalised requests, MPI-IO,
whose collective calls synchronise and whose library communicates underneath, and the MPI-4
calls it does not model: large-count, partitioned, sessions, Isendrecv,
Intercomm_create_from_groups; the tracer probes the MPI library for them rather than trusting
MPI_VERSION); a rank without a trace file; a receive that took a
message no send in the trace matches (a sender the tracer did not see, such as a Fortran
library under Open MPI, which calls `PMPI_*` directly); and the tracer's message table
overflowing, which leaves the receives of probed messages without their envelope. A
collective that lacks only ranks without a trace file is then not an observed `2-collective`
error either: the trace is incomplete, not the program. Before 22 Oct 2026 the last three only
added notes, and predictions were made on a happens-before relation with message edges
missing (`C_IncompleteTrace`). The report's status names the reason: `degraded:` with
`unmodelled`, `missing-ranks`, `unmatched-completions`, `tracer-overflow` or `creation-unknown`.

Cancellation is outside the model: a cancel that succeeds withdraws an operation at a point that
no matching rule describes (a cancelled send is pending until its cancel, and a receive posted
before then may take it). A run with a successful cancel, or with a cancel whose outcome the
trace does not have (its request never returned), is reported with observed findings only. A
cancel that failed changed nothing, and it fails in every alternative execution too, since an
implementation may always let a cancel fail (MPI 4.1 §3.8.4). Before 22 Oct 2026 cancelled
operations were simply left out of the competitors and of the channel order, which accepts
steals that MPI cannot realise (`C_Cancellation`).

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
send of H blocks forever; the closure (rules 2–7) removed every operation whose completion H
cannot guarantee, so the order contains no completion the library would withhold. Consistency
with σ on H means P issues the operations of H in that order. Tests: `CheckerSoundness` (every
accepted alternative of every traced idiom is realised by the explorer) and E6 on the corpus
traces and on synthetic traces; evidence so far: 0 unsound pairs.

*Gaps found by the audit of 20 Oct 2026, all repaired.* (a) and (b) by the match nodes of §2
(regression tests `C_MatchNodes`); (c) by rule 6 over probes, rule 7 and the probe order edges
of §2, and by treating root-shaped collectives without a recorded root as barriers in the graph
(`C_ProbesReadyRoots`).
(a) The 3' edges of an unchanged pair are read from the observed matching
(`Trace.rs_edges_observed`): when a nonblocking wildcard r is re-matched, a later receive r3 of
the same rank keeps `issue(M0(r)) → complete(r3)` instead of `issue(M'(r)) → complete(r3)`.
Counterexample: A: Irecv r(ANY); Recv r3(ANY); Send x→C; Wait r. C: Recv x; Send sC→A.
σ: r←sB, r3←sD, sC unmatched. r←sC is accepted, yet MPI gives sD to the pending r.
(b) A 3' edge ends at `complete(r)`, but a rendezvous send is matched before its own
completion. Counterexample: A: Irecv r1(ANY); Irecv r2(ANY); Waitall. C: Send sC→A (4 KB);
Send x→B. B: Recv x; Send sB→A. σ: r1←sB, r2←sC. At B = 0 σ is unrealisable, yet
`observed_feasible_at(t, 0)` holds. The repair is a match node m(r, s) per pair, with
`issue(r) → m`, `issue(s) → m`, `m → complete(r)`, `m → complete(s)` when s blocks, the order
edges between match nodes, and 3' recomputed from M' for every receive posted after a changed
one. (c) Rule 6 does not cover probes and Mprobes; a ready send whose receive is cut is kept;
root-shaped collectives without a recorded root get no edges in `build_graph`, while the
horizon and the explorer treat them as barriers. The E6 generator cannot produce (a) or (b):
it has no ssend, rsend, probes or Waitany, and never sends after a receive within a round.

*What T1 does not cover: the fast check.* T1 is a statement about `feasible.check`. The report
judges steals at blocking receives, probe alternatives and completion-order alternatives with
`stretto.fast`, whose horizon is meant to be the same closure; that is tested, not proved (§2,
cost). The audit of 22 Oct 2026 found it different in four ways, all repaired: it accepted a
steal that a pending receive, posted before the changed one and left without its message,
would deprive (`C_RematchedCompetitor`; an L1 claim no execution realises); its rule 4 kept
completions of nonblocking collectives waited out of issue order, and of the other group of an
intercommunicator (`C_CollectiveRule`; a horizon too large, and an acceptance the generic check
rejects); the closure of a probe alternative cut the probe where the generic check keeps it
(completeness only); and the report of an observed run failed when rule 6 deprived a probe
under the single-path assumption. Random runs with these shapes (`C_FastEqualsGenericRandom`)
find each of them, and the report now confirms the fast check's acceptances with
`feasible.check` on traces of up to 1 000 records and with `--cross-check`. Above that size,
and without the flag, an alternative the report states rests on the fast check agreeing with
the generic one, which is evidence, not a theorem.

**Theorem T2 (monotonicity in B).** Let B0 be the least threshold at which the observed
matching is feasible. If a single-change alternative M' (one wildcard receive re-matched, §2)
is feasible at B ≥ B0, then it is feasible at every B' > B, and the kept prefix at B' contains
the kept prefix at B.

*Hypothesis added after the audit of 20 Oct 2026.* Below B0 the statement is false. An
alternative can be feasible at B = 100: rule 3 cuts a rank whose 150-byte send lost its
receive, and with it a later head-to-head 400-byte exchange. At 200 the exchange is kept and
cyclic, so the alternative is infeasible, and at infinity it is feasible again. The step
"acyclic because σ happened" below holds only for B ≥ B0. The report already states intervals
over the thresholds at which the observed run is realisable (`realisable` in
`report.analyze`), so its output follows the corrected statement. Since 21 Oct 2026 B0 is
taken over every edge of the observed graph, the probes' reports and collectives of unknown
shape included, as the proof needs; the 2-buffering finding keeps the guaranteed edges only,
so its threshold can be lower than B0 (a probe that waits for a message whose sender is
blocked in a rendezvous send is one case). Three gaps remain. The
bisection guard (`_profiles_fast`) catches only "feasible at 0, infeasible at infinity". The
cut on the changed rank is not the same at every B when the changed receive is nonblocking:
receives posted after it become judged as B grows. And swaps re-match two receives, so they
are not single changes.
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
bisects between them (`stretto analyze --full-profile` judges every breakpoint and is the
differential: equal on all 516 traces). Whether the statement extends to arbitrary M' is open
(an operation newly kept on a rank where M' differs from M₀ could carry a new obligation), so
two-change alternatives are profiled at every breakpoint.

**Corollary T2' (the other profiled items).** The report also bisects three profiles that are
not receive alternatives, and the same argument covers them. The observed run itself (M' = M₀,
no changed receive: the cut and the order obligations are those of σ at every B, and only
rendezvous edges disappear as B grows). A probe alternative (§3): the one edge
`issue(s) → complete(p)` replaces `issue(seen) → complete(p)`; neither mentions B, and a send
issued inside H(B) is issued inside H(B') ⊇ H(B). A completion-order alternative (§3): the cut
after the wait record is rule 1, independent of B, and the added edge does not mention B. Each
profile is therefore monotone in B and its bisection exact. Evidence: the bisect-versus-full
differential on the 516 traces and on AMG (3 263 probe alternatives, identical findings);
E2's full profiles cover the observed run and receive alternatives, not probes, so for probes
and completion order the differential is the only measurement. Should a profile ever come out
feasible at B = 0 and infeasible at B = ∞, `report._profiles_fast` judges it at every
breakpoint and the report says so in a note, so a counterexample cannot pass as an interval.

**Proposition T3 (deadlock predictions are not monotone).** The set of thresholds at which a
predicted deadlock occurs is neither upward- nor downward-closed in general: idiom 03
(head-to-head exchange) deadlocks below 400 bytes and not above; the loop races of MBI deadlock
under the stolen match only when the stolen message is buffered (B ≥ 4), since below that the
steal itself is infeasible. Both are witnesses, no proof needed. Each witness set is itself an
interval; an earlier wording claimed "not an interval", which they do not show.

**Theorem T4 (cost).** Let a trace have n records, an alternative matching M' (|M'| ≤ n),
P ranks, w wildcard receives of which w_q on rank q, at most T tags on any channel that an
ANY_TAG receive reads, and k distinct message sizes. Then
(i) the candidates number at most w·P steals (the non-overtaking kernel: per wildcard receive
and source, only the earliest send no earlier receive has taken) and at most Σ_q w_q(w_q−1)/2
swaps (a swap exchanges the messages of two wildcard receives of one rank, so each unordered
pair gives at most one);
(ii) one check of one alternative at one threshold (`feasible.check`) takes
O((n + |M'|·T) log n) time: the horizon closure is a worklist in which each record loses its
issue at most once and rule 6 costs one bisection (`horizon.compute_cut`), matching order costs
O(T) lookups per pair (`matching.matching_order_ok`), and the constraint graph has
O(n + |M'|·T) edges (a match node and at most four receiver-side and T sender-side order edges
per pair, P edges per collective through its hub), searched for a cycle in linear time;
(iii) a steal's profile over the breakpoints takes O(log k) checks from B0 on (T2), a swap's
k + 2 (T2 does not cover it).
The check is thus quasi-linear per alternative for any number of changed receives; what grows
with the number of changed receives is the number of alternatives, and the choice of which
receives to change is the problem that is NP-complete in general (MOPPER's), which this work
does not solve. The theorem is about the generic check, whose worklist takes records, not
ranks, and whose bound (ii) holds whatever the order. The fast check (`stretto.fast`), for
steals at blocking receives and for probe and completion-order alternatives, has no proved
bound of that form, and its equality with the generic check is not proved either: both are
measured (§2, cost: the tests, and the report's cross-check of its acceptances). It needs
O(n log n) preparation per trace and the vector clocks per threshold, O((n + m) P). A
lowering of a rank in its closure costs O(P log n) for the suffix-minimum queries, plus the
scan of rule 4 over the rank's later collectives, up to all of them with all their members,
plus, when the competitor a query names is re-matched, dropped or the changed probe, one
rule-6 lookup per pending receive of the suffix; the matching-order test of a steal reads the
wildcard and unmatched receives of the rank before the changed one. A lowering lands on a
record, so a closure is at worst quadratic in n. The bounds stated before 22 Oct 2026, O(P log
n) per lowering, O(n P log n) per closure (within a factor P of (ii)), O(P² log n) when each
rank is lowered once, which the Lamport order of the worklist makes the usual case (§2, cost),
and O(n P log n) per threshold for the chained closures of a rank's probes (§2, probe
closures), describe what was measured on AMG's traces (§2), not what holds in general.
*Sketch.* (i) by construction of `matching.candidates_full`; (ii) each rule of the closure is
evaluated at the start and then once per record whose issue it depends on, records lose their
issue at most once because cuts only decrease, and the cut states are ordered (index, issue
removed) < (index, issue kept), so the worklist reaches the greatest fixpoint that the
rescanning `compute_cut_fixpoint` reaches (tests `closure_equals_fixpoint` and the 503 corpus
traces); the order edges suffice by the chain argument of §2 (equal verdicts with the
scanning versions on 816 traces, 20 Oct 2026). The O(T) lookups per pair of matching order
include skipping the sends a matched Mprobe took from the queue before the receive: a rank's
receives and Mprobes take each queue's messages in order, so the latest earlier send no Mprobe
took is found in one step (`Trace.earlier_sends`); before 22 Oct 2026 it was found by a walk
back over the taken ones, O(n) per pair on a channel served by Mprobe and Mrecv
(`test_earlier_sends_skip_mprobed_ones_at_once`). Evidence of the practical cost: AMG at 8 ranks
(39 643 sends, 53 wildcard receives, 10 978 probes, 3 263 probe alternatives, about 260 message
sizes) 13.2 s and 0.56 GB on the second machine; Nekbone's communication suite at 8 ranks
(10.4 million records, 18 759 alternatives) 63 min and 33.3 GB (31.0 GiB), about 0.2 s per
alternative (`results.md`, E5 and E5b; measured before the changes of 20 Oct 2026, to be
remeasured).

**Lemma T5 (the single-path assumption is unsound on metadata-dependent code).** There are two
programs consistent with the same trace σ on the whole trace and a threshold B such that one
deadlocks at B under an alternative matching and the other has an execution at B realising it
that terminates. Idiom 01 (the reply goes to `MPI_SOURCE`) at B = 0 is the witness: SP(σ)
deadlocks, while the real program, written as its L2 twin, does not. Idiom 06 (a collective
guarded by `MPI_SOURCE`) is not a witness of this statement, since both programs deadlock
there; E3 records its single-path deadlock as not spurious, and nothing committed shows that
the mechanism differs from the program's. Consequence: an
L1+SP finding is a claim about SP(σ), never about the program; the report labels it so.

**Theorem T7 (deadlocks contained in the horizon).** Let M' be feasible at B with horizon H. A
set D of ranks is a *contained deadlock* if no rank of D is cut by rule 1, every rank of D stops
at an operation it issues but that nothing inside H can complete (no acceptable message issued
in H and not taken there, no acceptable posted receive still waiting, the collective's required
members missing), and every rank whose future operations could complete one of those operations
is in D or has terminated. Then for every program consistent with σ on H and on the operations
at which D stops, every execution given by T1 extends only to executions in which the ranks of D
never complete those operations.
*Sketch.* The ranks of D are not cut by rule 1, so the operations at which they stop are what
every consistent program issues; nothing H holds completes them, and the only ranks that could
are blocked in D or gone. Every cut descends from rule 1, so a contained deadlock needs a
changed rank that stops *before* its changed receive completes: a nonblocking wildcard receive
whose rank blocks on another operation before the wait. A blocking changed receive cuts its rank
right after it, and every rank that waits on that rank waits on an unknown continuation; such
deadlocks stay L1+SP or L2 claims. Implemented in `stretto.deadlock`, reported as class 1d at
level L1; tests `C_ContainedDeadlock`, and E6 checks every L1 deadlock against the explorer
(every execution with the changed match must deadlock).

## 3. Horizon as a causal closure (level L1)

Given `M'` and the set `R*` of receives whose match changed, the kept prefix `H` is the largest
set closed under (`horizon.compute_cut`):

1. for `r ∈ R*`: everything after `complete(r)` in its rank is removed (the payload and metadata
   differ; the trace does not determine the continuation); if r never completes in σ,
   everything after its posting is removed, since a later test of its request could now
   succeed. Rule H1' (`stretto analyze --reads`, `docs/firstread.md`) removes instead what
   follows the receive's read bound, the first record the program may issue after reading a
   result of r, computed from the program's sources;
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
   and what follows it are removed. A later probe that reports such a message is removed too,
   since a probe reports what a receive posted at that point would take. The rule is what
   lets the claim cover receives that complete beyond the prefix: a pending receive competes
   for every message its rank gets;
7. a ready-mode send whose `M'` receive is not posted inside the prefix would be erroneous:
   the send and what follows it are removed.

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
an operation removed by rules 2–7 is one its rank never completes: a hang. `feasible.check`
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
deadlock that the real program cannot reach (01) or reaches for a different reason (06). With
read bounds (`stretto analyze --reads`, `docs/firstread.md`), a consequence one of whose
witness executions has no rank issue a record past the read bound of a result that differs is
an L1 finding: every consistent program that respects the bounds has that execution.

Level **L2** is represented by the DSL twins in `analysis/tests/idioms_dsl.py`: the same
programs with their metadata dependencies (`Send(dst=lambda env: env["st"].src)`,
`If(lambda env: env["st"].src == 1, ...)`). The explorer over a twin is the ground truth for
what a skeleton-aware analysis should predict. The twin can also be the program itself
(`stretto.rerun`, `docs/l2-auto.md`): each rank follows its trace while its results are the
recorded ones and is re-executed alone under a shim (`rerun/`) from the first result that
differs. Its claims are about the traced program under premises R1-R4 there (a rank's calls are
a function of its results; the shim serves what MPI would; recorded payloads; P1), with no
horizon: an execution that reaches a continuation the shim cannot determine is `unknown`.

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
everyone. A ready-mode send that no receive posted before its issue takes is an error on the
path (rsend-no-receive): it is recorded at the issue when no posted receive accepts the send,
at the match when a receive posted later takes it, once every receive that accepted it at its
issue has taken another message, or at the end of a path on which nothing takes it (§2, ready
sends). Since that depends on the order of posting and issue, a program with a ready-mode send
is explored without the reduction. Outcomes: done, deadlock, truncate, rsend-no-receive,
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
  skeleton (`payload == sender rank`) for the benchmark-shaped races. Re-execution
  (`docs/l2-auto.md`) needs none of it written down; what it lacks is probes, tests and
  communicator creation in the shim, and a proof in the paper.
- Buffer races (family ③): request spans are in the trace; accesses are not, except the ones
  the tracer's buffer guard (`STRETTO_GUARD=1`) catches in the run itself: the first access
  between MPI calls to a pending operation's buffer, a send buffer changed before its
  completion, two pending operations owning the same bytes. These are observed findings
  (class 3-local) and outside the model: no matching changes them, and predicting one for
  another run would need the program's accesses, not its calls.
- Several threads calling MPI (MPI_THREAD_MULTIPLE, SERIALIZED, or a misused FUNNELED): a
  rank's call order then depends on the threads' scheduling, not only on its results, so it
  is not a function of them as the definition of a program needs, and per-rank program order
  is not total. The tracer records the calling thread at every level (on every record under
  MULTIPLE, on the records of threads other than the initialising one below it), and the
  analysis stops at observed findings when a rank's records come from more than one thread.
  Before 22 Oct 2026 the thread was recorded under MULTIPLE only, and a SERIALIZED rank with
  several calling threads was analysed as one deterministic sequence.
