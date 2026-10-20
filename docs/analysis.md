# Analysis of the proposal (living document)

Source: `proposal_EN.md`, draft v3, September 2026. This file records the technical critique that
shaped WP0 and is updated as gate results come in.

## Verdict

The framing is right and the L0–L3 ladder is the correct way to organise the work. The central
observation is correct and well argued: the shared-memory reads-from lemma fails for message passing
because a re-match changes values, and MOPPER's "single-path" restriction is data-obliviousness in
different clothes. The reframing as bipartite matching under precedence constraints, with
non-overtaking as the structure that confines nondeterminism, is the right mathematics.

The largest risks are not the payload-obliviousness bet. They are two cheaper, earlier issues that
change what the WP0 gate measures:

- (A) L1 has a 30-year-old precursor the proposal only waves at;
- (B) "message race" is not yet defined as a bug.

## A. L1 ("predictive horizon") has prior art: first / unaffected races — CONFIRMED

Read on 2026-09-07 (`related-work/analysis.md`). The MAD debugging environment (AADEBUG 2000) states the
horizon verbatim: after a manipulated wildcard receive the program "is re-executed without any
constraints, since it is impossible to make predictions about the future of the program after
the alteration at the nondeterministic receive". *Detecting Unaffected Race Conditions in Message-Passing Programs* (EuroPVM/MPI 2004) defines
*unaffected* races causally ("before which no other races causally happened"), which is our
closure. *Debugging Race Conditions in Message-Passing Programs* (SPDT'96, paywalled) introduced first
races. Strong trace prefixes (CAV'24) are the shared-memory analogue.

What none of them has, and what Stretto claims instead: a feasibility criterion for the
*alternative* (availability, FIFO across tags, buffering regime, collectives), a soundness
statement in the OOPSLA'25 template, a complexity statement, a consequence taxonomy (their
races are all our class 1f), and anything past the horizon (L2).

A second, larger overlap surfaced: *The Complexity of
Testing Message-Passing Concurrency* (POPL'26), gives the complexity landscape of channel
consistency with and without a fixed matching. Its synchronous-channel algorithm (pack
send/receive pairs, check acyclicity) is Stretto's constraint graph at B = 0, and its
evaluation uses the single-swap mutation. The proposal's complexity claims must be restated
as the MPI-shaped delta (single-producer single-consumer queues, union receives, per-message
capacity, tags, collectives, and the parameter w). See `proposal-delta.md`.

## B. Family ① is undefined as a bug

Two feasible senders for one wildcard receive is the intended design of master–worker code. Unlike a
C++ data race it carries no undefined behaviour. Reporting every such site is noise, and the metric
"bugs MUST does not see" is meaningless without a definition of a *harmful* alternative matching.

Taxonomy, from cheapest to most demanding:

| class | what the alternative matching causes | level | trace-only? |
|---|---|---|---|
| ①a type/count mismatch | the alternative sender's count or datatype exceeds or mismatches the receive (MPI error, truncation) | L1 | yes, metadata only |
| ①b cross-purpose match | a message intended for another receive is consumed (ddcMD-style tag confusion) | L1 | yes |
| ①c orphan send | a send left unmatched at termination | L1+SP / L2 | needs single-path or skeleton |
| ①d deadlock under re-match | wait-for cycle in the alternative | L1+SP / L2 | family ② |
| ①e divergent communication skeleton | the continuation issues different MPI calls, or the program aborts on its own check | L2 | no, needs skeleton (a twin; `analyze(twin=...)`) |
| ①f benign nondeterminism | none of the above | — | report as info only |

Observed findings sit beside the predicted ones with the level `observed`: a send never received
in the run (①c observed), a rank blocked or spinning when the run ended (②-blocked), ranks that
disagree on a collective (②-collective), and a finished run that is realisable only above an
eager threshold (②-buffering, the one observed finding that is a prediction about other
implementations). The results tables score the observed and the predicted rows apart.

①a and ①b are cheap, sound, trace-only, and are the shape of the documented expensive bugs
(ddcMD, "63 sent / 62 received"). They are the first predictions the prototype emits. ①e is
produced for the idioms and for MBI's payload races through hand-written twins (E3, E7); the
skeleton extractor itself is not written.

Gate criterion ② is restated as "at least one idiom yields a ①a–①e prediction".

## C. The horizon must be a causal closure, not a per-process point

Re-matching P2's receive invalidates P2's suffix, hence every message P2 sent in that suffix, hence
the suffix of every process that received one of them, transitively. Under B = 0 a send whose
receiver is now undetermined also blocks its issuer. Collectives propagate the cut to every
participant. The horizon is therefore the largest prefix-closed, match-closed subset of the trace
unaffected by the change. `stretto/horizon.py` computes exactly this fixpoint, and it is what makes
the single-swap check a plain acyclicity test.

## D. Buffering monotonicity holds per family, not globally

Feasible matchings grow monotonically with B: a schedule legal at B = 0 is legal at any larger B,
because an implementation is always allowed to delay a send's completion. But head-to-head
`Send; Recv` deadlocks vanish as B grows, while new race-induced deadlocks can appear. State two
theorems: races monotone, deadlocks non-monotone. Tag each prediction with its B-interval by message
size. Both implementations needed for calibration are installed (Open MPI 4.1.6, MPICH 4.2.0).

## E. "FPT in w" is likely XP as stated; the map itself is now POPL'26's

With w competing wildcard receives and up to P candidate senders each, brute force is O(P^w · n):
polynomial for fixed w but not f(w) · poly(n). Either the parameter must include the candidate bound,
or non-overtaking must yield a kernel (only the head of each (src, dst, comm, tag) queue is ever a
candidate). POPL'26 settles the channel version: consistency with a fixed matching is NP-complete
in general but O(n) for synchronous channels and O(n²) on acyclic topologies; consistency without
a matching is NP-complete already for two threads. MPI's per-pair FIFO makes the fixed-matching
problem linear in every buffering regime (this is `feasible.check`); the choice problem is
MOPPER's NP-complete one. What is open and ours is the w-parameterised choice problem with tags and
collective windows. Do not promise FPT before the k-swap rung is done.

## F. Payload dependence is transitive and idiom-specific

Three common patterns break the bet locally: size-header-then-data, payload sentinels for
termination (as opposed to a DIETAG, which is metadata), and work items whose contents decide the
worker's later message count. The last is transitive: a deterministic receive can carry a value that
originated in a wildcard-matched message. WP0a therefore measures taint from wildcard payload to
(i) MPI call arguments and (ii) branch conditions dominating MPI calls, transitively. Manual
classification first (`study/sites.csv`), an LLVM taint pass later. NEKbone is Fortran: manual only.

## G. Family ③ is not PMPI-only

Buffer races need memory-access events; PMPI sees none. Options: TSan annotations (MUST's route) or
an LLVM pass instrumenting accesses to buffers of live requests. The "no recompilation" claim holds
for families ① and ② only. The pass route is realistic (out-of-tree against system LLVM 18) and
belongs to WP2. Idiom 07 is traced and analysed (E3): the report is empty, as it must be for a
family ③ bug that leaves no trace in the message events.

## H. Placement of MOPPER in the ladder

Single-path is the trivial skeleton, so L2 subsumes MOPPER's input assumption:

    L0  <  L1  <  L1+SP (MOPPER, ASE'20: trace + single-path assumption)  <  L2  <  L3

The prototype exposes both L1 (horizon-cut, trace only) and L1+SP (whole trace, single-path
assumed) so that gate criterion ③ ("beyond single-path") can be checked precisely: a prediction
counts only if it needs a non-trivial skeleton (idioms 01, 02, 06) or is shown to be trace-only.

## I. L3 as stated is an indistinguishability proposition

"Trace-only prediction cannot go past L1" follows from two programs with identical traces and
different continuations. Fine as a lemma, not a headline. The interesting L3 question is the
*minimal* skeleton characterisation (necessity), which is harder and open.

## What stands unchanged

Positioning against observational, exhaustive and SMT lines; message races before deadlocks;
confirming replay as mandatory; buffering B as a parameter; collectives as both bug source and
complexity limiter; the risk table. The evidence section is honest and should stay case-study based.

## Gate status

See `gate.md` (benchmark level) and `results.md` (generated tables); `wp0-gate.md` is the idiom-level record.
