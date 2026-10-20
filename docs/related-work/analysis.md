# Related work: what each line defines, proves, and lacks

Fetched and read on 2026-09-07 (PDFs in `papers/`, git-ignored; bibliographic data and
access status in `bib.md`). Each entry ends with the one line that matters for Stretto:
what we must not claim because of it, and what remains ours.

## 1. Complexity of message-passing consistency

**The Complexity of Testing Message-Passing Concurrency.
POPL 2026** (arXiv 2505.05162, 56 pages). Model: threads with program order, FIFO channels
with capacity cap(ch) ∈ ℕ (0 = synchronous, unbounded when cap ≥ number of sends). Problems:
VCh (is an abstract execution ⟨S, po⟩ with capacities consistent, i.e. does some linear trace
realise it) and VCh-rf (same, with a fixed reads-from = matching). Results: VCh NP-complete
even with two threads or a single synchronous/capacity-1 channel (Thm 1.1–1.3); VCh in
O(n^{t+1}·t^{km}) (Thm 1.4); VCh-rf NP-complete for k = 1 async channels, t = 3 with k = 2, or
t = 3 with five unbounded channels (Thm 1.6); O(n²) on acyclic topologies with capacity ≤ 1 or
unbounded (Thm 1.7, via 2SAT and a compositionality lemma); not in O(n^{2−ε}) under SETH
(Thm 1.8); **O(n) when every channel is synchronous** (Thm 1.9: pack each (snd, rcv) pair into
one node, add po edges, check acyclicity, Lemma 3.3). Saturation rules (§5.1) infer orderings
from FIFO, matched-before-unmatched, and capacity 0/1. Evaluation generates negative
instances by *swapping two reads-from pairs on a channel* and checks 103 Go instances.

Relation to Stretto. VCh-rf with a fixed matching is exactly `feasible.check`; their synchronous
case is our constraint graph at B = 0; their saturation rules are our matching-order checks and
rendezvous edges. Their hardness for unbounded channels comes from FIFO over *multiple senders
on one channel*. MPI queues are per (src, dst, comm): one producer, one consumer, so FIFO
reduces to program order and the mixed eager/rendezvous case stays linear. A wildcard receive
pops from a *union* of such queues, and buffering is a per-message size threshold, not a
per-channel count. VCh without rf is the matching-choice problem, whose MPI instance is
MOPPER's NP-complete deadlock problem.

*Must not claim:* "a complexity map for message-passing prediction" as new territory, nor the
single-swap question as a new object (it is their mutation operator). *Ours:* the MPI-shaped
class (SPSC queues, union receives, per-message capacity, collectives, non-overtaking with
tags) as a channel class where VCh-rf is O(n) beyond their synchronous case, and the
parameterisation of VCh by wildcard competition w, tags and collective windows. The first
theorem of the proposal (§15.1) is restated as this extension.

## 2. The first-race / unaffected-race line (1990s–2000s)

**Debugging race conditions in message-passing programs. SPDT 1996** (paywalled; known
through citations in the 2004 unaffected-race paper and the MAD overview of 2000).
Defines message races between messages that could be received by the same wildcard
receive, and *first races*: races not caused by an earlier race. The SC'92 paper
"Optimal tracing and replay" traces only racing receives so that a run can be replayed.

**A brief overview of the MAD debugging activities. AADEBUG 2000** (arXiv cs/0012012, read). Race conditions occur at receive events when "the
source of the received message is unspecified" and "there must be at least two so-called
racing messages that can be accepted at that particular wild card receive". MAD highlights
wildcard receives, determines the racing messages for a selected one (vector clocks in the
event graph), lets the user pick another racing message, and re-executes: "before the
user-selected wild card receive event the program is replayed as observed", "at the selected
wild card receive the specified message will be accepted", and **"after the exchange the
program is re-executed without any constraints, since it is impossible to make predictions
about the future of the program after the alteration at the nondeterministic receive."**

**Detecting unaffected race conditions in message-passing programs. EuroPVM/MPI 2004** (abstract only): "unaffected race conditions before which no other races causally
happened"; traces locally-first races per process and their affect-relations, on the fly.
Follow-ups: "Detecting unaffected message races in parallel programs" (2006), MPIRace-Check
(2007, on-the-fly tool for C MPI programs reporting all races with source line, processes and
channel).

Relation to Stretto. The predictive horizon is this community's folklore, stated in 2000 in
almost our words, and the "unaffected race" is defined causally, as our closure is. What none
of them has: a definition of when the *alternative* is realisable (availability, FIFO across
tags, buffering regime, collectives), a soundness statement in the modern sense (every
reported alternative is an execution of every program with this trace), any complexity
statement, or anything past the horizon. Their races are all our class 1f; none classifies
the consequence (1a count mismatch, 1b cross-purpose, orphan, deadlock).

*Must not claim:* the horizon as "a new theoretical object with no analogue" (proposal
novelty claim 1). *Ours:* the feasibility criterion, the soundness theorem in the OOPSLA'25
template, the buffering parameter, the consequence taxonomy, and L2.

## 3. SMT/SAT prediction for MPI deadlocks (2014–2024)

**MOPPER: Precise Predictive Analysis for Discovering Communication Deadlocks in MPI
Programs. FM 2014 / TOPLAS 2017** (read). Single-path
programs: "any control flow that could affect inter-process communication is deterministic".
Two buffering models: zero-buffering (a wait on a nonblocking send blocks until the message
is received) and infinite-buffering. Semantics: states ⟨issued, matched⟩ with issue and match
transitions; a state is deadlocking if not everything matched and no transition is possible.
**Theorem 1: deadlock detection is NP-complete for both buffering models**, by a reduction from
3-SAT using 3n + m + 3 processes; the gadget forces one of two sends per variable to match
first, and a wildcard receive with m+1 ordered senders encodes clause satisfaction. Encoding:
matches-before order ≺_mo from ISP, over-approximate potential-match set M⁺, SAT variables
m_a (matched), r_a (ready), s_ab (matched together), t_ab (before), clocks; O(|C|²) variables,
O(|C|³) size; sound and complete for single-path programs modulo the buffering model.
Conditional matches-before edges (nonblocking receive from a specific source before a
nonblocking wildcard receive) are ignored ("we have not come across a benchmark that issues
a conditional matches-before edge").

**A Predictive Analysis for Detecting Deadlock in MPI Programs. ASE 2020**: dependency graph → deadlock candidates → abstract-machine
filter → SMT reachability; single-path. **Improving the Efficiency of Deadlock Detection in MPI Programs Through
Trace Compression, TPDS 2023**: same pipeline with identical
consecutive sends/receives merged into macro actions. **SAMPI, CLUSTER 2022**: match-sets from a
lazy Lamport clock protocol, refined by non-overtaking; avoids exploring paths. **PDMPI, Parallel
Computing 2024**: program partitioning by logical clocks to bound path explosion, single-path.
**Internetware 2024**: path compression and focus matching.

Relation to Stretto. This line is L1+SP (single-path) throughout, deadlock-only, and decides
the *matching-choice* problem, which is NP-complete; their buffering models are our B ∈ {0, ∞}.
MOPPER's "conditional matches-before" is exactly the receiver-side ordering edge the audit
added to `build_graph`. None of them separates the trace-only question (what is certifiable
without the single-path assumption) from the assumption; idioms 01 and 06 show the assumption
is unsound on metadata-dependent code.

*Must not claim:* polynomial deadlock prediction for single-path programs (NP-complete).
*Ours:* the split L1 / L1+SP / L2, a polynomial feasibility check for a *given* alternative
(their problem with rf fixed), classes 1a/1b that need no solver, and the parameterisation
by w. PDMPI's logical clocks are the natural baseline for the "clocks vs. sound order" claim.

## 4. Soundness template (OOPSLA'25) and strong trace prefixes (CAV'24)

**Soundness of Predictive Concurrency Analyses. OOPSLA 2025** (read §1–5).
Soundness = for every program P, every SC-consistent input graph G_σ of P and every reported
bug b: a witness G_ρ exists that is composable with b (no skipping, same control flow),
well-formed (read-feasible, lock-feasible), executable (each thread's events generated by the
program from the same prefix of states, Lemma 1/2 via data-abstract equivalence and symbolic
values), and M-consistent (memory model). Proof recipe: symbolic plain execution → insert
rf/co/sync orders → concretise. Restricted to shared memory, locks, MCA memory models.

Relation to Stretto. Our theorem statement uses this template with: executability = "each
rank's kept prefix is generated by the program" (the horizon is where symbolic values start:
after a changed receive the payload is symbolic and control flow may depend on it, so the
prefix ends there — this is exactly their "same control flow" composability condition);
M-consistency = matching consistency (FIFO per queue, availability, rendezvous, collectives).
Their Lemma 2 (symbolic values allowed where no control dependency reads them) is the formal
home of the L2 skeleton: a skeleton says which receive values are control-relevant.

**Predictive Monitoring with Strong Trace Prefixes. CAV 2024** (read §1–3). A
dual concurrent alphabet (Σ, S, W) with strong dependence S (program order, reads-from) and
weak dependence W (locks); a strong trace prefix is downward closed w.r.t. S and preserves the
S ∪ W order on the residual events; earlier critical sections can be dropped. Predictive
monitoring under strong prefixes keeps the complexity of trace theory (constant space for
races). Relation: the horizon closure is a strong-prefix construction where S = program order
∪ delivery ∪ collective sync ∪ rendezvous and there is no W (no lock-like reusable resource);
so L1 prediction is "strong-prefix monitoring for the matching alphabet".

## 5. Vector clocks for message passing outside MPI

**Two-phase dynamic analysis of message-passing Go programs based on
vector clocks. 2018** (arXiv 1807.03585, read §1–2). Thread-local traces with pre/post events
for channel operations and select; offline replay derives vector clocks; reports alternative
communication partners ("M4 could have received from M1"), blocked senders, and select
alternatives. No feasibility check beyond happens-before, no soundness theorem.
**ADVOCATE** (the same line of work, for Go): record, rewrite, re-execute to confirm.
**dl² (FSE 2025)** (read §1–2): PyTorch/NCCL interposition, execution graph,
unmatched communications, wait-for graph cycles; "tries to adjust the execution order of
asynchronous actions or buffered synchronous actions in the EG while maintaining consistency"
to expose nondeterministic deadlocks; replay for diagnosis; 100% precision/recall on their
suite. Observational with a limited reordering step; no theory of which reorderings are legal.

*Ours:* MPI's matching semantics is strictly richer (tags, per-pair FIFO, buffering regimes,
collectives); a theory for it specialises to channels. dl²'s "adjust while maintaining
consistency" is an informal VCh-rf check.

## 6. Benchmarks

**MPI-BugBench (EuroMPI 2024)**: unified harness; level-2 generation; categories
`DRace` (message race), `DMatch` (call ordering), `EBufferingHazard` (with `$zero_buffer` /
`$infty_buffer` run variants), `FOK`; tool plugins as Python classes with build/run/parse;
scoring TP/FP/FN/TN with `can_deadlock` labels. Its message-race programs branch on the
payload (`if (buf[0] != i) extra Recv`), i.e. they are L3-shaped, with the payload mirroring the
sender's rank. **MBI (SC-W 2021)**: 1,871 generated programs, 80 with wildcards (65
`MessageRace_*`, 15 tag-matching), header labels `ERROR: MessageRace`. **MPI-CorrBench
(HPDC-W 2021)**: 616 files, 19 with wildcards, 67 with `MPI_THREAD_MULTIPLE`.

## Positioning table

| | observational (MUST, dl²) | first-race line (SPDT'96, MAD, unaffected races) | SMT line (MOPPER … PDMPI) | POPL'26 | Stretto |
|---|---|---|---|---|---|
| sees unobserved matchings | no | yes (racing messages) | yes | n/a (given execution) | yes |
| certifies the alternative is executable | n/a | no | yes, single-path | yes (VCh-rf, channels) | yes, L1 trace-only; L1+SP; L2 with skeleton |
| soundness theorem | n/a | no | w.r.t. encoding | yes | in the OOPSLA'25 template |
| complexity | linear | — | NP-complete (choice) | full map for channels | O(n) feasibility for MPI-shaped channels; w-parameterised choice |
| consequence classes | all observed | race only | deadlock | consistency | 1a/1b/1c/1d/1f, ②-buffering, ②-collective |
| buffering | fixed | — | {0, ∞} | capacity per channel | per-message threshold B, profile over breakpoints |
| beyond single-path | — | replay only | no | — | L2 skeleton (metadata), value skeleton for benchmark shapes |

## Rewritten novelty ranking

1. **Primary.** A sound feasibility criterion and theorem for alternative MPI matchings (L1),
   stated in the OOPSLA'25 template, with a linear-time check that extends POPL'26 Thm 1.9 to
   MPI-shaped channel systems; the consequence taxonomy that turns a "race" into a bug class.
2. The L1 / L1+SP / L2 split with evidence that single-path is unsound on metadata-dependent
   code, and the metadata skeleton as the minimal extra input (with the value-skeleton remark
   for benchmark-shaped races).
3. The buffering parameter B with per-family monotonicity statements and replay-backed
   B-intervals.
4. The w-parameterised complexity of the matching-choice problem, positioned as the MPI delta
   of POPL'26.
5. Engineering: PMPI tracer + replay pin + the corpus harness over MBI and MPI-BugBench, evaluated against MUST.

Dropped: "a new theoretical object with no shared-memory analogue" (the horizon is MAD's
1996–2000 statement and the CAV'24 strong prefix); "the first complexity map" (POPL'26).
