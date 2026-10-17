# What the proposal text must change (v3 → v4)

Derived from `related-work/analysis.md`, the audits, and the WP0 results. Applied to
`proposal_EN.md` v4 on 2026-10-16; kept as the record of why each change was made.

1. **Novelty claim 1 (predictive horizon as a new object): drop.** The MAD environment (2000)
   states the horizon verbatim; *Detecting Unaffected Race Conditions* (2004) defines unaffected
   races causally; strong trace prefixes (CAV'24) are the shared-memory analogue. Replace with: *a feasibility criterion and soundness
   theorem for alternative matchings, stated in the OOPSLA'25 template*, which none of them has.
2. **Novelty claim 3 (complexity map): reposition** as the MPI delta of *The Complexity of
   Testing Message-Passing Concurrency* (POPL'26): SPSC queues with union receives and
   per-message capacity make VCh-rf linear beyond their synchronous case; the open axis is the matching-choice problem
   parameterised by wildcard competition w, tags, and collective windows. Cite POPL'26 in
   §10.5, §12.5 and §14.
3. **§15.1 first theorem: restate.** "Single-swap feasibility is polynomial" is a corollary of
   VCh-rf for MPI-shaped channels; POPL'26 already uses the swap as a mutation operator. State
   instead: *MPI-VCh-rf (given matching, mixed eager/rendezvous, ssend/bsend/rsend modes,
   shaped collectives) is O(n) by acyclicity plus local FIFO checks*, and climb from there to
   k changed receives without rf.
4. **§7–9: add the L1+SP rung explicitly** (trace + single-path assumption = MOPPER's input)
   between L1 and L2, and the evidence (idioms 01, 06) that it is unsound on
   metadata-dependent code. Single-path is the trivial skeleton.
5. **§4/§10.4: define the bug.** A wildcard with two feasible senders is not a bug. Use the
   consequence taxonomy 1a (count/type mismatch), 1b (cross-purpose channel), 1c (orphan),
   1d (deadlock), 1e (divergent skeleton), 1f (benign), plus ②-buffering and ②-collective.
   Evaluation counts consequences, not races.
6. **§10.3 buffering: two theorems, not one.** Feasible matchings are monotone in B; deadlocks
   are not (head-to-head deadlocks vanish, race-induced ones appear). B is a per-message size
   threshold; predictions carry a feasibility profile over the message-size breakpoints, and
   ssend/bsend/rsend modes override B.
7. **§10.5: "FPT in w" → open.** Brute force is O(P^w · n): XP, not FPT, unless non-overtaking
   yields a kernel. State the conjecture with the parameter made precise.
8. **§11: collectives by shape.** Barrier-like collectives synchronise all members; bcast/
   scatter only root→others; reduce/gather others→root; scan prefix-wise. The all-to-all
   treatment is sound for feasibility claims but *unsound for deadlock claims*.
9. **§7.3 payload bet: add the transitive and benchmark-shaped cases.** Probe-driven loops,
   tag-unique wildcards, and payload that mirrors metadata (`buf[0] = rank`) as in MPI-BugBench's
   message-race programs; the "value skeleton" remark.
10. **§10.6 instrumentation: correct two claims.** Family ③ needs access instrumentation
    (PMPI cannot see buffer accesses); Open MPI's Fortran bindings bypass C interposition, so
    Fortran codes need a shim (or MPICH).
11. **§12.2/12.6 related work: add** POPL'26, CAV'24, MAD (2000), the unaffected-race papers
    (2004/2006), MPIRace-Check 2007, SAMPI CLUSTER'22, PDMPI as baseline; fix author lists (see `bib.md`).
12. **§16 evaluation: E1's DRace metric is two numbers** — racing receive flagged (L1) vs
    deadlock certified (needs the value skeleton) — because the benchmark's races are
    payload-dependent by construction.
