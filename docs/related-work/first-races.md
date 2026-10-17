# Related work checklist: first / unaffected message races

**Status (2026-09-07): answered; see `analysis.md` section 2.** Kept as the reading log. For each paper: what is defined,
what is proved, what is missing relative to L1. Sources to read (verify bibliographic details):

| paper | defines | proves | missing w.r.t. L1 | read? |
|---|---|---|---|---|
| *Optimal tracing and replay for debugging message-passing parallel programs.* SC'92 / J. Supercomputing 1995 | racing messages via vector timestamps; tracing only racing receives | optimality of the trace size | no feasibility notion (availability, buffering), no soundness theorem for predicted alternatives | |
| *Debugging race conditions in message-passing programs.* SPDT'96 | **first races** (not artifacts of earlier races); affected vs. unaffected | | closest to the horizon; check how "affected" is defined (per process? causal?) and whether alternatives are certified executable | |
| *Nondeterminacy: testing and debugging in message passing parallel programs.* 1993 | | | | |
| *Detecting unaffected race conditions in message-passing programs* (2004); *Efficient detection of first races in MPI programs* | unaffected races in MPI, on-the-fly | | soundness w.r.t. an execution model; buffering; collectives | |
| *Event graph analysis for debugging massively parallel programs* (MAD, 2000) | event graph, "irregular" receives, record and replay | | prediction is via replay, not proof | |
| Cypher, Leu. *The semantics of blocking and nonblocking send and receive primitives in MPI* / repeatable programs | | | | |

Answers:
1. Causal (the 2004 unaffected-race paper: "before which no other races causally happened"); MAD replays up to
   the manipulated receive and declares the rest unpredictable. L1's novelty is the feasibility
   criterion, the soundness theorem and B, not the object.
2. None certify executability of the alternative beyond vector-clock concurrency; MAD confirms
   by re-execution.
3. No complexity results in this line; POPL'26 covers channels.
4. All report our class 1f only.

Questions the checklist had to answer:

1. Is the "unaffected race" exactly the prefix-closed, match-closed horizon of `docs/model.md` §3,
   or per-process only? (If causal: L1's novelty is the soundness theorem and B, not the object.)
2. Do any of them certify that the alternative receive order is *executable* (availability,
   FIFO, buffering), or only that two messages are "concurrent"?
3. Do any state a complexity result?
4. Which of the ①a–①f classes do they report? (The SPDT'96 race = our ①f at minimum.)

Outcome feeds `analysis.md` section A and the novelty ranking in the proposal.
