# Sound Predictive Analysis for Message-Passing Programs

### From SMT encodings to partial orders: predicting concurrency bugs that a single run does not reveal

**Version:** v4, October 2026 (v3 of September 2026 rewritten after the first results; `proposal-delta.md` lists what changed and why, `gate.md` the measurements)
**Target venues:** PLDI / OOPSLA / POPL (theory + algorithms + tool), CAV (metatheory), PPoPP / SC / EuroMPI (tool paper)

> **How to read this document.** Sections 1–3 assume no background in high-performance computing or in concurrency theory; they build the vocabulary from scratch. Section 4 onwards is the technical proposal proper. A reader who already knows dynamic race prediction can start at Section 5, which contains the one idea the whole project turns on.

---

## Table of contents

```
  0.  Executive summary
  ────────────────────────────────── PRIMER (no background assumed)
  1.  Two ways parallel programs talk
  2.  MPI in one page
  3.  What "dynamic analysis" and "prediction" mean
  ────────────────────────────────── THE PROBLEM
  4.  Why observed-run tools miss the bugs that matter
  5.  Evidence: is this a real problem for real developers?
  ────────────────────────────────── THE SCIENCE
  6.  How prediction works in shared memory (the machine we want to port)
  7.  The central difficulty: why it does not transplant
  8.  Matching consistency, non-overtaking, and the shape of the problem
  9.  The research approach: a four-level hierarchy
  10. Formal core
  11. Collectives
  ────────────────────────────────── POSITIONING
  12. Related work
  13. What adapts from shared-memory theory, and what does not
  14. Novelty claims
  ────────────────────────────────── EXECUTION
  15. Work plan
  16. Evaluation plan
  17. Risks and mitigations
  18. Resources, environment, collaboration
  19. Success criteria
  ────────────────────────────────── APPENDICES
  A.  Is MPI still relevant?
  B.  Portfolio position (internal)
  C.  Glossary
  D.  References
```

---

## 0. Executive summary

Programs that run on supercomputers are built from thousands of processes that cooperate by sending each other messages. The order in which those messages arrive is not fixed: it depends on network timing, on machine load, on the phase of the moon. A program can run correctly ten thousand times and then, on the ten-thousand-and-first run — usually the expensive one, on the big machine, at 3 a.m. — take a different message order and hang, or silently produce wrong numbers.

Today's correctness tools for such programs (MUST, ITAC, PARCOACH) watch one run and report what went wrong **in that run**. If the bug needs a different message order to appear, they see nothing. The alternative — re-running the program under every possible message order — explodes combinatorially and is abandoned in practice.

There is a third way, and it works well in a neighbouring field. For shared-memory multithreaded programs, a line of research known as **predictive analysis** takes a single recorded run and proves, mathematically, that *some other schedule of the same run would have crashed* — without ever executing that schedule. Over the last decade this went from exponential SMT solving to linear-time algorithms. The research programme behind WCP, SDP and sound deadlock prediction in linear time is the spine of that line.

**This project builds the same thing for message passing.** The message-passing side has the question (the MAD environment stated the predictive horizon in 2000; the "unaffected race" papers of 2004 defined races causally; *The Complexity of Testing Message-Passing Concurrency*, POPL'26, has the complexity landscape for channels) but no sound, implemented predictor: the existing tools are solver-based, deadlock-only, and restricted to programs whose control flow does not depend on received data.

The project is not a transplant, and the proposal is organised around the precise reason why. In shared memory, reordering a run does not change what values threads read, so the recorded run tells you everything. In message passing, **changing who receives which message changes what a process reads, hence what it does next — and the recording is silent about that.** The scientific content of this project is the characterisation of the minimum extra information that repairs this gap, and polynomial algorithms relative to it. We show that the missing information is not a data solver but a thin *metadata-dependency skeleton*, and organise the results as a four-level ladder (L0–L3) of increasing power and increasing cost, each level publishable on its own.

```
  ┌────────────────────────────────────────────────────────────────┐
  │  Bugs today's tools cannot see, because they hid in the        │
  │  schedule that did not happen — and re-running everything      │
  │  is not an option. We predict them from one run, with proof.   │
  └────────────────────────────────────────────────────────────────┘
```

**Deliverables:** a feasibility criterion and a soundness theorem for alternative matchings in MPI's matching semantics, with a taxonomy of the consequences a changed match has; a linear check per alternative and a kernel that bounds the alternatives to check; a buffering profile per prediction with its two theorems; the L1 / L1+SP / L2 hierarchy with the evidence that the single-path assumption is unsound on metadata-dependent code; a tool evaluated on the MPI Bugs Initiative and on MPI-BugBench against MUST, with every prediction replayed.

**Cost profile:** no GPUs, no proprietary tools, no cluster quotas. A CPU cluster and a laptop.

---

# PRIMER

## 1. Two ways parallel programs talk

A parallel program is many streams of execution running at once. They must coordinate. There are exactly two mainstream ways to do that, and they generate completely different bug families.

```
        SHARED MEMORY (threads)                MESSAGE PASSING (processes)
        ───────────────────────                ──────────────────────────

     ┌────┐   ┌────┐   ┌────┐                ┌──────┐         ┌──────┐
     │ T1 │   │ T2 │   │ T3 │                │  P0  │         │  P1  │
     └──┬─┘   └──┬─┘   └──┬─┘                │ mem0 │         │ mem1 │
        │        │        │                  └───┬──┘         └───┬──┘
     ┌──┴────────┴────────┴──┐                   │   "here is x"  │
     │    ONE memory          │                  ├───────────────►│
     │    x = 42              │                  │                │
     └────────────────────────┘                  │   ┌──────┐     │
                                                 └──►│  P2  │◄────┘
     Everyone can touch x.                           │ mem2 │
     Danger: two threads touch it                    └──────┘
     at the same moment.
                                                 Nobody can touch anyone
     Typical bug:  DATA RACE                     else's memory.
                                                 Danger: who gets which
                                                 message, and in what order.

                                                 Typical bug: MESSAGE RACE
```

Shared memory is what you get with `pthreads`, Java threads, OpenMP, Go goroutines sharing a variable. Message passing is what you get with MPI (supercomputers), Go channels, actor systems, and — importantly for this proposal — the collective-communication layers underneath distributed machine learning (NCCL, PyTorch distributed).

The two worlds have very different tooling maturity. Shared-memory data races have ThreadSanitizer, a decade of theory, and a settled vocabulary. Message-passing correctness has good *observational* tools and almost no theory.

---

## 2. MPI in one page

MPI (Message Passing Interface) is the standard used by essentially all scientific supercomputing code. You need five concepts.

**(1) Ranks.** The program starts as N identical copies ("ranks" 0..N−1), each with private memory. Same binary, different rank number, different behaviour.

**(2) Point-to-point send and receive.**

```
   rank 0                                  rank 1
   ──────                                  ──────
   MPI_Send(buf, dest=1, tag=7)  ────────► MPI_Recv(buf, src=0, tag=7)
```

A `Recv` names who it wants to hear from (`src`), a label (`tag`), and a *communicator* (a named group of ranks). A send and a receive **match** when these agree.

**(3) Wildcards — the source of all our trouble.** A receive may say "I will take a message from *anyone*":

```
   MPI_Recv(buf, MPI_ANY_SOURCE, tag=7, comm, &status)
                 ▲▲▲▲▲▲▲▲▲▲▲▲▲▲
                 whoever gets here first wins
```

After the call, `status.MPI_SOURCE` tells you who actually sent it. This is how master–worker pools are written, and it is why the same program can behave differently on two runs. `MPI_ANY_TAG` is the same idea for labels.

**(4) Nonblocking operations.** To overlap communication with computation, MPI lets you start an operation and collect it later:

```
   MPI_Isend(buf, ..., &request);   ← starts; returns immediately
   ... compute something ...        ← DANGER ZONE: buf is owned by MPI here
   MPI_Wait(&request, ...);         ← now buf is yours again
```

Touching `buf` inside the danger zone is a bug — the same shape as a data race, and roughly as common.

**(5) Collectives.** Operations that all ranks of a communicator perform together:

```
   MPI_Bcast     one rank's data → everyone
   MPI_Reduce    everyone's data → one rank (summed, maxed, ...)
   MPI_Allreduce everyone's data → everyone
   MPI_Barrier   nobody proceeds until all arrive
```

MPI requires all ranks of a communicator to issue collectives **in the same order**. Getting that wrong is one of the most common real-world MPI bugs.

---

## 3. What "dynamic analysis" and "prediction" mean

**Static analysis** reads source code without running it. Sound in principle, drowning in false alarms in practice for concurrency.

**Dynamic analysis** runs the program and watches. Precise, but only about the run it saw.

```
   ┌─────────────────────────────────────────────────────────────┐
   │                ALL POSSIBLE SCHEDULES OF ONE INPUT          │
   │                                                             │
   │      ✗            ┌──────────┐            ✗                 │
   │           ✗       │ observed │                     ✗        │
   │                   │   run    │        ✗                     │
   │       ✗           └──────────┘                              │
   │                        ▲                    ✗               │
   │                        │                                    │
   │              a dynamic tool sees only this                  │
   │              ✗ = a bug lurking in a schedule                │
   │                  that simply did not happen                 │
   └─────────────────────────────────────────────────────────────┘
```

**Predictive analysis** is the third option: watch **one** run, then *reason* about the schedules you did not see, and report a bug only when you can prove that some real, executable alternative schedule contains it.

```
   record ONE run  ──►  build a mathematical model of the run
                        ──►  prove: "there exists a legal reordering
                                     in which X and Y collide"
                        ──►  report X,Y  (no re-execution needed)
```

Two words appear constantly and must be kept straight:

```
  SOUND      every bug we report really can happen
             (no false alarms)            ← what we insist on

  COMPLETE   every bug that can happen, we report
             (no misses)                  ← usually traded away
```

Predictive analysis chases *maximum coverage subject to soundness*. The research question is always the same: **how much of the space of alternative schedules can I certify as genuinely executable, and how fast?**

---

# THE PROBLEM

## 4. Why observed-run tools miss the bugs that matter

MPI's correctness tools — MUST, Intel Trace Analyzer, PARCOACH — are observational. They intercept every MPI call, check arguments, track outstanding requests, build a wait-for graph, and report violations **that occurred**. They are good, mature, and widely deployed. They are also blind by construction to the following, which is the archetype of the expensive MPI bug:

```
   Two workers report to a master. The master serves whoever answers first.

   rank 1  ──Send(result)──┐
                            ├──►  rank 0: Recv(ANY_SOURCE) ⇒ took rank 1
   rank 2  ──Send(result)──┘                Send(work, to = rank 1)

   Run it a thousand times on your laptop: rank 1 always wins
   (it is closer / faster / scheduled first). Everything works.

   Run it on 500,000 cores at 3 a.m.: rank 2 wins once.
   The master sends work to the wrong rank, or nobody,
   and 500,000 processes wait forever.
```

MUST sees a perfectly legal run and says nothing — correctly, because on *that* run nothing is wrong. The bug is not in the run; it is in the set of runs the program admits.

**What counts as a bug.** A wildcard receive with two feasible senders is not a bug; it is a race, and most races are benign. The project reports the *consequence* of the alternative matching and counts consequences, not races: 1a, the alternative message does not fit the receive (count or type signature); 1b, it comes from another logical channel (tag); 1c, a send is left unmatched; 1d, a deadlock; 1e, the program's continuation diverges (a send to another destination, a different collective participation, an abort on its own check); 1f, a benign alternative; ②-buffering, the run deadlocks below an eager threshold; ②-collective, ranks disagree on a collective. The evaluation (§16) scores 1a/1c/1d/1e/② as errors and 1b/1f as warnings.

**The three bug families this project targets:**

```
  ┌────────────────────────────────────────────────────────────────────┐
  │ ① MESSAGE RACES                                                    │
  │    Two sends compete for one wildcard receive.                     │
  │    Consequences: wrong data; a send left unmatched ("orphan");     │
  │    a deadlock; different numerical results run to run.             │
  │    → the central object of this project                            │
  ├────────────────────────────────────────────────────────────────────┤
  │ ② DEADLOCKS                                                        │
  │    A cycle of processes each waiting for a message that            │
  │    will never come — reachable only under some matchings.          │
  │    → studied before, but only for restricted programs              │
  ├────────────────────────────────────────────────────────────────────┤
  │ ③ BUFFER RACES ON NONBLOCKING OPERATIONS                           │
  │    Touching a buffer between Isend/Irecv and Wait.                 │
  │    → the closest analogue of a classical data race                 │
  └────────────────────────────────────────────────────────────────────┘
```

And the reason a *predictive* answer is the right answer, rather than exhaustive exploration:

```
   EXHAUSTIVE (ISP, DAMPI, MPI-SV)      PREDICTIVE (this project)
   ────────────────────────────────     ─────────────────────────
   re-run the program under every       run once, then reason
   distinct matching
                                        cost: one run + analysis
   cost: combinatorial in the number
         of competing wildcards         coverage: everything provably
                                        reachable within the model
   coverage: complete, but only for
         toy sizes                      soundness: by theorem
```

---

## 5. Evidence: is this a real problem for real developers?

An honest answer has two halves, and the proposal states both.

### 5.1 Exposure — how much code is even susceptible

Measured over the ECP proxy-application suite (14 applications) and a survey of 56 MPI-using US Exascale Computing Project teams:

```
  collectives              ████████████████████  100%  (14/14 proxy apps)
  nonblocking p2p          ████████████████░░░░   80%  (ECP survey)
  nonblocking collectives  ████████████░░░░░░░░   59%  (ECP survey)
  wildcards (any kind)     ██████░░░░░░░░░░░░░░   29%  (4/14 proxy apps)
  MPI_ANY_SOURCE           █████░░░░░░░░░░░░░░░   21%  (3/14 proxy apps)
  MPI_THREAD_MULTIPLE      █████░░░░░░░░░░░░░░░   25%  (survey) / 0% (proxy)
  RMA / one-sided          █████░░░░░░░░░░░░░░░   21%  (survey) / 7% (proxy)
```

Read this honestly: **buffer races (family ③) have near-universal exposure; message races (family ①) affect roughly a quarter of codes.** Collectives are everywhere.

### 5.2 Severity — what actually breaks, and what it costs

The rare family is the expensive one. Documented cases, all from primary sources:

| Application | Family | Cost |
|---|---|---|
| **ddcMD** (molecular dynamics) | message race — two I/O layers using fixed tags confused each other's messages | intermittent hang, only at scale; "significant person-hours" |
| **pf3d** (laser–plasma) | race exposed only at **524,288 processes** on Sequoia | a scientist spent **months** debugging with print statements, to no avail; fix cost "millions of CPU hours" |
| **HYPRE 2.10.1** | nondeterminism via `ANY_SOURCE` / `Waitany` | **>10,000 hours of compute time** spent on manual debugging |
| **Xeon vs Xeon-Phi** | orphan send from divergent expression evaluation | "**63 messages sent but only 62 attempted to be received**" → hang |
| **HYDRA** (NIF multiphysics) | race | "defied debugging for months" |

Two structural corroborations. First, the standard MPI correctness benchmark (MPI Bugs Initiative: 1,730 programs, 9 categories) carries **"Message race" as a top-level category** — the community classifies it as a first-class defect. Second, an entire sub-field exists to cope with this nondeterminism by *recording and replaying* it (ReMPI, ANACIN-X, clock-delta compression). People do not build record-replay infrastructure for a problem that does not exist.

### 5.3 A gap worth noting

**No study reports the distribution of real-world MPI bugs across these classes.** We searched: empirical MPI studies, HPC bug studies (MSR'23 covers performance only), record-replay surveys. Motivation in the literature is case-study-based, never statistical.

Two consequences, both actionable:
- the proposal must argue from *exposure* plus *case studies*, never from an invented percentage;
- mining the issue trackers of MPICH, Open MPI, PETSc, Trilinos and HYPRE for such a distribution is a cheap, citable, standalone contribution — a natural side-product of WP0.

### 5.4 The honest positioning

```
   ┌──────────────────────────────────────────────────────────────┐
   │  "Rare, but catastrophic, and covered by nobody."            │
   │                                                              │
   │  NOT: "a problem every MPI developer hits every week."       │
   │  That would be false, and a reviewer would know it.          │
   └──────────────────────────────────────────────────────────────┘
```

---

# THE SCIENCE

## 6. How prediction works in shared memory — the machine we want to port

To see why message passing is hard, you must first see why shared memory is easy. One definition and one lemma carry the whole field.

### 6.1 reads-from

`reads-from` (rf) is the relation "which write produced the value this read observed".

```
   T1                              T2
   ──                              ──
   w(x, 5) ──────────────┐
   w(x, 7)               │ rf
                         └────────────► r(x) ⇒ 5
```

Here the read returned 5, so `rf(r)` is the *first* write, even though a later write exists in the trace.

### 6.2 The lemma that makes trace-only prediction possible

Given an observed trace σ, we look for a **correct reordering** σ′ — a permutation such that:

```
   ① each thread's program order is preserved
   ② every read has the same reads-from as in σ
```

And then:

```
   ┌────────────────────────────────────────────────────────────┐
   │                                                            │
   │     ① + ②   ⟹   σ′ is executable by the program P          │
   │                  — for ANY program P                       │
   │                                                            │
   └────────────────────────────────────────────────────────────┘
                             ▲
        because a thread that observed the same sequence of
        values deterministically executes the same instructions
```

This is the engine. It means the analysis **never needs the source code**. The trace is self-contained; the problem is purely combinatorial. That is why sound partial orders such as WCP can be computed in linear time, and why the field could move from SMT solving (RVPredict, 2014) to streaming vector-clock algorithms (WCP, PLDI'17) and onward to linear-time deadlock prediction (PLDI'23).

### 6.3 The shape of the shared-memory solution

```
  trace σ  ──►  partial order P  ──►  if two conflicting events are
                (weaker than HB,       UNORDERED by P, report a race
                 still sound)
                                       soundness theorem: unordered by P
  computed by vector clocks            ⟹ a correct reordering exists
  in one streaming pass                   that puts them adjacent
```

The research programme has been: find weaker and weaker sound partial orders (HB → WCP → SDP/M2 → SyncP), map the complexity of the underlying decision problems, and keep the algorithms near-linear.

**This project asks: what is the message-passing version of that diagram?**

---

## 7. The central difficulty: why it does not transplant

### 7.1 What plays the role of reads-from

In message passing, the analogue of "which write fed this read" is **which send fed this receive** — the *match*. So the naive plan is: keep matches fixed, permute the rest, done.

It does not work, and the reason splits into two cases with no third option.

**Case A — the matching is held fixed.**

```
   P0 │ Send#1 ──────────┐
   P1 │ Send#2 ─────┐    │
   P2 │ Recv ⇐ #1 ◄─┼────┘
      │ Recv ⇐ #2 ◄─┘

   We may shuffle timings, but the pairs stay.
   Sound and cheap — and it predicts almost nothing,
   because every interesting message-passing bug IS a
   change of pairing. The only survivor is family ③
   (buffer races), which lives inside a single matching.
```

**Case B — the matching is allowed to change.** This is where the interesting predictions live, and where the machinery breaks:

```
   OBSERVED                                 HYPOTHETICAL σ′
   ────────                                 ───────────────
   P0 │ Send(→P2, 10) ──┐                   P0 │ Send(→P2, 10) ──┐
   P1 │ Send(→P2, 20) ─┐│                   P1 │ Send(→P2, 20) ─┐│
   P2 │ Recv(ANY) ⇐ 10 ◄┘                   P2 │ Recv(ANY) ⇐ 20 ◄┘
      │ v = 10                                 │ v = 20
      │ if (v > 15)  Bcast()                   │ if (v > 15)  Bcast()
      │ else         Send(→P1)                 │              ▲
      │              ▲                         │              │
      │              └── we recorded this      │        WHAT NOW?
                                               │
                              the trace is SILENT: this branch
                              was never executed, so nothing in
                              the recording says what P2 does
```

Stated once, plainly:

```
  ╔════════════════════════════════════════════════════════════════╗
  ║  A single trace cannot tell you what a process would have      ║
  ║  done had it received a message from a different sender.       ║
  ╚════════════════════════════════════════════════════════════════╝
```

This is not an algorithmic difficulty. It is an **absence of information**. No algorithm extracts from a recording what the recording does not contain.

### 7.2 Consequence: `single-path` is not a weakness, it is the boundary

The existing message-passing prediction literature (MOPPER, the ASE'20 tool and successors) restricts itself to **single-path** programs.

```
   SINGLE-PATH  (what MOPPER requires)     NOT SINGLE-PATH
   ───────────────────────────────────     ───────────────
   MPI_Recv(&v, ANY_SOURCE, ..., &st);     MPI_Recv(&v, ANY_SOURCE, ..., &st);
   MPI_Send(&r, dest = 1, ...);            MPI_Send(&r, dest = st.MPI_SOURCE,...);
   MPI_Barrier(comm);                      if (v > 0) MPI_Barrier(comm);
                                                ▲              ▲
   the sequence of MPI calls is the             │              └ whether the call
   same whatever data arrives                   └ where we send    happens at all
```

The usual reading is "an unfortunate technical limitation someone should lift." The correct reading is:

```
   single-path (message passing)  ≡  data-obliviousness (shared memory)
```

It is the *same* assumption, wearing different clothes. In shared memory it is invisible because it is baked into the reads-from lemma. In message passing it is visible because changing a match changes values. MOPPER did not fail to try harder; it hit a wall that is there by construction.

**Therefore:** any proposal promising "beyond single-path *and* polynomial" with no additional input is internally inconsistent. A polynomial approximation of value-level executability is either conservative to the point of collapse (forbid every match that influences control flow ⇒ back to single-path) or unsound. Kalhauge & Palsberg pay for completeness with SMT; that is not an oversight.

### 7.3 The way out: dependence flows through metadata, not payload

Look again at the example. The dependence went through `v` — the message **payload**. Now look at real MPI code:

```c
for (i = 0; i < N; i++) {
    MPI_Recv(&res, 1, MPI_DOUBLE, MPI_ANY_SOURCE, TAG, comm, &st);
    MPI_Send(work, 1, MPI_INT, st.MPI_SOURCE, TAG, comm);
    /*                        ^^^^^^^^^^^^^                   */
    /*   the continuation depends on the MATCH METADATA,      */
    /*   not on the contents of the message                   */
}
```

`st.MPI_SOURCE` ranges over a small finite domain (the ranks). The same profile holds for `MPI_Waitany` (index of the completed request), `MPI_Probe`/`Iprobe`, and `MPI_Get_count`.

```
   ┌────────────────────────────────────────────────────────────────┐
   │  THE BET OF THIS PROJECT                                       │
   │                                                                │
   │  In real MPI code, the communication pattern depends on        │
   │  received messages almost exclusively through METADATA         │
   │  (source rank, tag, count, completion index) and only          │
   │  rarely through PAYLOAD.                                       │
   │                                                                │
   │  If true: the problem moves from "SMT over data values"        │
   │  to "finite branching over a small domain" — a different       │
   │  complexity class entirely.                                    │
   └────────────────────────────────────────────────────────────────┘
```

This is an empirical claim and it is measurable. Measuring it was step zero of the work plan (§15; `wp0-gate.md`): on AMG, MACSio and Nekbone 18 of 19 wildcard, probe and completion sites are metadata-only or independent. Three patterns the bet must cover beyond the plain case: *probe-driven loops* (`Iprobe(ANY_SOURCE)` followed by `Recv(status.MPI_SOURCE)`, AMG's exchange code), *tag-unique wildcards* (a wildcard source whose tag already names the sender, Nekbone), and *payload that mirrors metadata*: the benchmark programs of MBI and MPI-BugBench send the sender's rank as the payload and branch on it (`if (buf != 3) abort()`), so a "value skeleton" that records which payload equals which metadata makes them metadata-dependent after all (`experiments/mbi/twins.py`, E7).

One honest residue remains: even knowing the dependence is metadata-shaped, the trace observed the behaviour for **one** metadata value only. Recovering the others needs either SPMD symmetry (the code is one function of the rank, so behaviour transfers by renaming) or a thin static pass that extracts the dependency skeleton. Hence level L2 in §9 is *trace + skeleton*, not trace alone.

---

## 8. Matching consistency, non-overtaking, and the shape of the problem

Three structural facts distinguish message matching from lock-based synchronisation. Together they explain why the shared-memory toolbox does not fit, and which toolbox does.

### 8.1 A message is consumed exactly once

```
   LOCK                                  SEND
   ────                                  ────
   T1 │ acq(m) ... rel(m)                P0 │ Send#1 ──┐
   T2 │ acq(m) ... rel(m)                P1 │ Recv_A ◄─┘
                                         P2 │ Recv_B ◄── nothing left

   Reordering changes only WHO GOES       The resource is not renewable.
   FIRST. Both threads eventually         Recv_B blocks forever.
   get the lock.
```

Consequence: permuting one match changes the **availability** of the others — a chain reaction with no analogue in lock semantics. This is the effect the v1 proposal already identified informally ("a send eaten by another wildcard is gone").

### 8.2 An alternative matching is a different *matching*, not a different *interleaving*

This is the reframing that should drive the technical work:

```
       senders                     receivers
       ───────                     ─────────
        s1 ●──────────────────────● r1
             ╲                  ╱
        s2 ●───╳───────────────╳──● r2
             ╱   ╲          ╱   ╲
        s3 ●──────╲────────╱──────● r3

   edge (s,r) is admissible  ⟺
       ▸ compatible (src / tag / communicator)
       ▸ respects both processes' program order
       ▸ respects non-overtaking (§8.3)
       ▸ the send is available under buffering regime B (§10.3)

   QUESTION: does another PERFECT MATCHING exist
             in which a correctness property is violated?
```

This is **bipartite matching under precedence constraints**, not linearisation of a partial order. That is the structural reason MOPPER's problem is NP-complete — the hardness is intrinsic, not incidental. It also tells us which mathematics to reach for: matching theory, flows, and parameterised algorithms, in addition to vector clocks.

### 8.3 Non-overtaking: MPI hands us the crucial structure for free

MPI guarantees that messages between a fixed pair of processes with matching parameters **do not overtake each other**.

```
   ONE SOURCE — order is FIXED, no nondeterminism at all
   ─────────────────────────────────────────────────────
   P0 │ Send(→P1, tag=7) "A" ──┐
   P0 │ Send(→P1, tag=7) "B" ──┼──► queue (P0→P1, tag 7): [A][B]
                                │
   P1 │ Recv(ANY_SOURCE, tag=7)─┴──► MUST receive A
   P1 │ Recv(ANY_SOURCE, tag=7)─────► then B
                                      ⇒ the pairing (B,A) is INFEASIBLE

   DIFFERENT SOURCES — here, and only here, lives the freedom
   ─────────────────────────────────────────────────────────
   P0 │ Send(→P2, tag=7) "A" ──┐   two INDEPENDENT queues,
   P1 │ Send(→P2, tag=7) "B" ──┤   unordered with respect to each other
                                │
   P2 │ Recv(ANY_SOURCE, tag=7)─┴─► may take A OR B
```

Why this matters more than it looks: non-overtaking confines *all* matching nondeterminism to competition between distinct sources at a wildcard receive. Everything else is FIFO.

```
             space of conceivable matchings
   ┌─────────────────────────────────────────────────────┐
   │  combinatorially imaginable                         │
   │  ┌───────────────────────────────────────────────┐  │
   │  │  cut down by non-overtaking                   │  │
   │  │  ┌─────────────────────────────────────────┐  │  │
   │  │  │  cut down by program order              │  │  │
   │  │  │  ┌───────────────────────────────────┐  │  │  │
   │  │  │  │  cut down by collectives          │  │  │  │
   │  │  │  │  ┌─────────────────────────────┐  │  │  │  │
   │  │  │  │  │  ACTUAL FREEDOM:            │  │  │  │  │
   │  │  │  │  │  w competing wildcards      │  │  │  │  │
   │  │  │  │  └─────────────────────────────┘  │  │  │  │
   │  │  │  └───────────────────────────────────┘  │  │  │
   │  │  └─────────────────────────────────────────┘  │  │
   │  └───────────────────────────────────────────────┘  │
   └─────────────────────────────────────────────────────┘
```

This is the sole reason to hope for polynomial algorithms where the general problem is NP-hard, and it makes `w` — the maximum number of simultaneously competing wildcard receives — the natural complexity parameter. In halo-exchange codes `w ≈ 0`; in master–worker pools the competition is local.

### 8.4 Shared memory versus message passing, side by side

```
                     SHARED MEMORY                 MESSAGE PASSING
                     ─────────────                 ───────────────
  atom of            read / write of a cell        match (send, recv)
  nondeterminism     the ORDER of accesses         WHO pairs WITH WHOM

  where a value      from the last write (rf)      from whoever won the match
  comes from

  resource model     lock: reusable, everyone      send: consumable, exactly
                     gets it in turn               one receiver ever gets it

  effect of          values unchanged              values CHANGE
  reordering         ⇒ code follows same path      ⇒ code may diverge
                     ⇒ the trace suffices          ⇒ the trace does NOT suffice

  synchronisation    explicit and separate         implicit: the transfer
                     (lock/unlock/barrier)         IS the synchronisation

  problem shape      linearisation of a            BIPARTITE MATCHING under
                     partial order                 precedence constraints

  FIFO structure     none                          non-overtaking (a gift)

  observability      TSan sees every access        PMPI sees every call,
                                                   but not the alternatives

  scale              2–128 threads                 10³–10⁶ processes
                                                   ⇒ O(P) vector clocks
                                                     become a real problem
```

Two rows decide everything. *"Values change"* is the source of the central difficulty. *"Bipartite matching"* is both the source of hardness and the source of hope.

---

## 9. The research approach: a four-level hierarchy

From §7 and §8, the project organises into four levels of increasing power and increasing informational cost. Each is independently publishable; failure at level k still leaves levels < k standing.

```
  predictive
    power
      ▲
      │
  L3  │  ░░░░░░░░░░░░  payload-dependent control flow
      │                → LOWER BOUND / "a solver is necessary" theorem
      │
  L2  │  ████████████  trace + thin static skeleton + SPMD symmetry
      │                → MAIN RESULT
      │
  L1  │  ███████       trace only, reasoning bounded by the
      │                PREDICTIVE HORIZON
      │                → FIRST REAL RESULT (object with no
      │                  shared-memory analogue)
      │
  L0  │  ███           trace only, matching held fixed
      │                → WCP transplant; sanity check, not a claim
      └──────────────────────────────────────────────────────►
                                        information required
```

| Level | Available to the analysis | What it predicts | Scientific price |
|---|---|---|---|
| **L0** | trace; matching fixed | buffer races on nonblocking ops (family ③) | near-verbatim WCP transplant — validates the machinery, claims nothing |
| **L1** | trace; matching may change; conclusions limited to the **predictive horizon** | message races, orphan sends, deadlocks whose witness fits inside the horizon | the first result: fully trace-only; the horizon itself is prior art (MAD, 2000; the unaffected-race papers, 2004), what is new is the feasibility criterion, its linear check and its soundness theorem |
| **L1+SP** | trace + the **single-path assumption** (the recorded call sequence does not change: MOPPER's input) | consequences past the horizon, for the one program that issues exactly the recorded calls | cheap and useful on straight-line code; **unsound** on metadata-dependent code, demonstrated (idioms 01 and 06, E3) |
| **L2** | trace + **metadata-dependency skeleton** + SPMD symmetry | everything past the horizon: master–worker, `Waitany`, `Probe`, metadata-dependent collective participation | **the main result**; so far represented by hand-written twins (the idioms, MBI's payload races), not extracted from code |
| **L3** | control flow depends on payload | — | **lower bound**: an honest boundary, and a theorem in its own right |

### 9.1 The predictive horizon, concretely

```
   P2's timeline in the recorded trace:

   ──●─────────●─────────◆─────────●─────────●─────────●──────►
     e1        e2      Recv       e4        e5        e6
                       (ANY)
                         │
     ◄──── still valid ──┤──────── UNDETERMINED after a match change ────►
           after any     │
           re-matching   │
                    THE HORIZON

   L1 says: any error witness lying entirely to the LEFT of every
            process's horizon is soundly predictable from the trace alone.

   L2 says: with a skeleton telling us how e4, e5, e6 depend on
            st.MPI_SOURCE, we can push the horizon to the right.
```

The methodological claim, which should be stated as a theorem and not hidden: **purely trace-based prediction cannot go past L1.** The boundary itself was named before (MAD, 2000); what this project adds is the criterion that decides, in linear time, which alternative matchings are realisable up to it, the taxonomy of what they lead to, and the demonstration that the assumption MOPPER-style tools make to cross it (L1+SP) is unsound on metadata-dependent code (`model.md`, T5).

### 9.2 A useful side effect

Static analysis stops being cosmetic and becomes structural. The Aachen line (SC-W'25, coupling static and dynamic MPI correctness tools) turns from "adjacent work we should be careful about" into a supplier of the exact machinery L2 needs. That gives the collaboration a technical reason to exist rather than a diplomatic one.

---

## 10. Formal core

### 10.1 Trace model

```
  EVENTS
  ──────
  issue(op)        start of a p2p operation (blocking or not)
  complete(op)     its completion (Wait / Waitany / Test success)
  match(s, r)      the atom of nondeterminism: send s paired with receive r
  cmatch(C, ranks) a collective match-set: all ranks of comm meeting at C
  span(req)        the interval [issue, complete] of a nonblocking request
  access(addr)     a local memory read/write (needed for family ③)
  thread events    for MPI + threads (WP4)

  OBSERVED TRACE  =  one linearisation  +  one matching
```

### 10.2 Feasibility of an alternative matching

A candidate matching `M′` is **feasible** iff:

```
  ①  program order      each process still follows its own sequence
  ②  non-overtaking     FIFO preserved per (src, dst, comm, tag)
  ③  availability       no send is consumed twice; none is consumed
                        before it is issued
  ④  buffering          consistent with regime B (§10.3): at B = 0 a
                        Send blocks until matched, which changes what
                        can happen at all
  ⑤  metadata coherence (L1/L2) each process's continuation is consistent
                        with the metadata it would actually have received
```

Constraints ③–⑤ have no shared-memory counterpart. Constraint ④ is not even fixed by the standard. The implemented criterion (`model.md` §2–3) checks ①–④ over the kept prefix of the horizon closure and replaces ⑤ by the horizon: receives whose metadata changed end their rank's determined prefix; the kept prefix is closed under the receives, rendezvous sends, collectives (by shape) and probes it drags along. Its soundness theorem (T1) is stated in the OOPSLA'25 template: every program consistent with the trace on the kept prefix has an execution that realises the accepted matching on the receives the prefix decides.

### 10.3 Buffering, and what soundness is relative to

MPI does not specify when `MPI_Send` blocks. Implementations use an *eager* protocol for small messages (copy and return) and a *rendezvous* protocol for large ones (block until the receiver is ready). Therefore:

```
   ┌────────────────────────────────────────────────────────────────┐
   │  "There is a deadlock in some schedule" is TRUE RELATIVE TO    │
   │  a class of implementations, not absolutely.                   │
   └────────────────────────────────────────────────────────────────┘
```

We turn this into a parameter. Let `B` be the buffering threshold — the number of bytes a `Send` may hand off without blocking.

```
   B = 0            standard-worst-case: sound for EVERY standard-conforming
                    implementation. Maximum predictions, maximum noise.

   B = B_impl       implementation-faithful: calibrated to the MPI library in
                    use. Minimum noise, zero portability.

   B arbitrary  ◄── every prediction carries the set of B at which it is
                    realisable, over the breakpoints {0} ∪ message sizes ∪ {∞}
                    (a per-message size threshold; ssend/bsend/rsend modes
                    override it)
```

Two theorems, not one (`model.md` T2, T3): the *feasibility* of a single-change alternative is monotone in B (rendezvous edges only disappear and the kept prefix only grows), so the profile is an interval and bisection is exact; the *deadlock predictions* are not monotone (a head-to-head exchange deadlocks below 400 bytes and not above; a stolen message in a loop race must be buffered for the deadlock to appear). B is measured per implementation (`b_impl.py`: Open MPI 4.1.6 buffers up to 4040 payload bytes by default, and no less than 256 bytes however the transport is set), and a prediction that holds only below the implementation's threshold is replayed with the threshold forced down (E2).

This answers head-on the objection every HPC reviewer will raise — *"your deadlock will never occur on my machine"* — and converts an ambiguity in the standard into an extra result.

### 10.4 Predictive partial orders

For each bug family, the weakest sound partial order whose violation certifies predictability:

```
  family ①  message races   two matches are competing and both are feasible
                            ⇒ the alternative outcome is realisable
  family ②  deadlocks       a wait-for cycle over ALTERNATIVE matchings;
                            note: a process at a blocking Recv waits for a
                            SET of possible sends ⇒ hypergraph, not graph
  family ③  buffer races    access × request-span, exactly WCP's
                            access × critical-section rule
```

All theorems are stated in the OOPSLA'25 soundness template (composability, well-formedness, executability, memory consistency), with `memory consistency` replaced by `matching consistency` (§10.2). The wait-for hypergraph of family ② is not implemented: deadlock consequences come from the exhaustive explorer under the single-path assumption (L1+SP) or a twin (L2), each compared with the program's behaviour without the change so that a deadlock the run has anyway is not attributed to the alternative.

### 10.5 Complexity map

Axes, not a single theorem:

```
                              w = 0        w bounded      w unbounded
                           ┌────────────┬──────────────┬──────────────┐
   L0 (fixed matching)     │  linear    │   linear     │   linear     │
                           ├────────────┼──────────────┼──────────────┤
   L1 (horizon)            │  linear    │  FPT  (conj.)│  NP-hard?    │
                           ├────────────┼──────────────┼──────────────┤
   L2 (+ skeleton)         │  linear    │  FPT  (conj.)│  NP-hard     │
                           ├────────────┼──────────────┼──────────────┤
   L3 (payload)            │     —      │      —       │ solver req'd │
                           └────────────┴──────────────┴──────────────┘

   w = max number of simultaneously competing wildcard receives
```

What is settled (`model.md` T4): with the matching given, the check is linear per alternative for any number of changed receives, O(n + m) generically and O(P² log n) from indexes built once per trace and threshold; non-overtaking bounds the single-change alternatives to one per (wildcard receive, source), at most w·P; the two-change rung enumerates pairs of those. The choice of which receives to change remains MOPPER's NP-complete problem, which this work does not solve. "FPT in w" is open: brute force over the choices is O(P^w · n), XP rather than FPT, unless non-overtaking yields a kernel; the conjecture is stated with its parameter and left as such. Relative to *The Complexity of Testing Message-Passing Concurrency* (POPL'26): MPI's per-pair single-producer queues with union receives and per-message capacity make the fixed-matching problem (their VCh-rf) linear beyond the synchronous case; the open axis is the matching-choice problem parameterised by w, tags and collective windows.

### 10.6 Algorithms and instrumentation

```
  trace capture   PMPI interposition (the MPI standard's own profiling
                  interface — no recompilation of the application)

  analysis        offline, over the trace: the horizon closure as a
                  worklist with suffix-minimum indexes, matching order
                  from the non-overtaking kernel, acyclicity as one
                  reachability query on the observed graph (vector clocks
                  computed once per threshold, the buffering profile
                  bisected by the monotonicity theorem); 9 s and 0.55 GB
                  for AMG at 8 ranks (3 263 probe alternatives)

  L2 skeleton     hand-written twin programs so far (the idioms, MBI's
                  payload races); the Clang pass that would extract
                  "the destination of this Send is the st.MPI_SOURCE of
                  that Recv" is not written, and a payload check needs a
                  value skeleton a syntactic pass cannot give

  family ③        PMPI interposition does not see memory accesses: buffer
                  races need access instrumentation (the 20 MPI-BugBench
                  local-concurrency programs Rematch misses are all of
                  this kind)

  Fortran         Open MPI's Fortran bindings call PMPI directly and bypass
                  C interposition; Fortran codes are traced through MPICH
                  (NPB) or need a Fortran shim

  scalability     measured up to 16 ranks and 3 million events (lu.A: 40 s,
                  4.6 GB); the claim is stated for that range
```

---

## 11. Collectives

Collectives appear in 100% of applications; ignoring them is not an option. Their role is twofold.

### 11.1 As a source of bugs

MPI requires every rank of a communicator to issue collectives in the same order. Violating this is the dominant category in the MPI Bugs Initiative benchmark ("call ordering").

```
   CORRECT                              MISMATCHED
   ───────                              ──────────
   P0 │ Bcast    Barrier                P0 │ Bcast    Barrier
   P1 │ Bcast    Barrier                P1 │ Barrier  Bcast   ← deadlock
   P2 │ Bcast    Barrier                P2 │ Bcast    Barrier
```

The predictive question: **does a feasible matching exist under which processes disagree about which collective they call?** Typically the participation depends on metadata, which places this squarely at L2:

```c
   MPI_Recv(&x, MPI_ANY_SOURCE, tag, comm, &st);
   if (st.MPI_SOURCE == 1) MPI_Bcast(...);   /* participation depends on WHO sent */
   /* else skip */
```

### 11.2 As a resource that makes the analysis tractable

A collective is a global synchronisation point. It cuts the space of matchings into independent chunks:

```
       ┌──── matching freedom ────┐     ┌──── matching freedom ────┐
   P0 ─┤                          ├──●──┤                          ├──
   P1 ─┤   wildcard exchanges     ├──●──┤   wildcard exchanges     ├──
   P2 ─┤                          ├──●──┤                          ├──
       └──────────────────────────┘  ▲  └──────────────────────────┘
                                     │
                            Barrier / Allreduce:
                            NO match may cross this line
```

Exactly as locks and barriers shrink the space of correct reorderings in shared memory. In typical HPC code the loop body is *halo exchange → Allreduce → repeat*, so the window between two collectives is short and `w` stays small. This is a direct argument for the FPT conjecture: **collectives keep the complexity under control.**

### 11.3 Modelling

```
  blocking collective      one atomic cmatch event over all ranks of comm;
                           hard ordering constraints before/after

  nonblocking collective   issue + span + complete — identical machinery
                           to family ③ (request-spans)

  the interesting freedom  the interleaving BETWEEN collectives and p2p,
                           and whether a rank reaches the collective at all

  by shape                 a member may return as soon as its own part is
                           done (MPI 4.1 §6.1): barrier-like collectives
                           synchronise everyone, bcast/scatter only root →
                           others, reduce/gather others → root, scan each
                           member with those before it. The all-to-all
                           treatment is sound for feasibility claims and
                           UNSOUND for deadlock claims; the checker, the
                           horizon closure and the explorer all use the shapes
```

---

# POSITIONING

## 12. Related work

### 12.1 The positioning map

```
                      │ sees only the observed run  │ sees unobserved schedules
  ────────────────────┼─────────────────────────────┼──────────────────────────
                      │                             │  ISP / POE, DAMPI,
   needs              │              —              │  MPI-SV, CIVL,
   re-execution       │                             │  ADVOCATE (Go)
  ────────────────────┼─────────────────────────────┼──────────────────────────
                      │  MUST, ITAC, PARCOACH,      │  MOPPER, ASE'20
   one run only       │  RMASanitizer, dl² (NCCL),  │  and successors
                      │  MPI-Checker, TypeART       │  — deadlocks only
                      │                             │  — single-path only
                      │                             │  — SMT per query
                      │                             │
                      │                             │  ★ THIS WORK
                      │                             │    3 bug families,
                      │                             │    beyond single-path,
                      │                             │    polynomial, online
```

### 12.2 Prediction for MPI — the closest competitors

- **MOPPER** — *Precise Predictive Analysis for Discovering Communication Deadlocks in MPI Programs.* FM'14; extended in **ACM TOPLAS 39, 2017**. Proves deadlock detection for single-path MPI programs NP-complete; propositional encoding over partial orders.
- *A Predictive Analysis for Detecting Deadlock in MPI Programs.* ASE'20. Dependency graph → candidates → abstract-machine filter → SMT reachability check.
- **The line is alive after 2022** — this must be stated correctly, because it is easy to check:
  - *Deadlock Detection for MPI Programs Based on Refined Match-sets*, IEEE CLUSTER 2022;
  - *Improving the Efficiency of Deadlock Detection in MPI Programs Through Trace Compression*, IEEE TPDS 34, 2023;
  - *Program partitioning and deadlock analysis for MPI based on logical clocks*, Parallel Computing 119:103061, 2024 — **logical clocks for MPI deadlocks; the closest work to ours in machinery**, though the clocks optimise a search rather than constitute a sound order with a theorem. This is our primary baseline;
  - *Efficient Deadlock Detection in MPI Programs with Path Compression and Focus Matching*, Internetware 2024.
- **MAPPED**, IEEE 2013 — an early statement of the same question, before sound theory existed.
- **Prior art for the horizon and for races:** the MAD environment (2000) states the predictive horizon verbatim for message passing; *Detecting Unaffected Race Conditions in Message-Passing Programs* (2004) and its 2006 sequel define races causally and ask which are unaffected by earlier ones; **MPIRace-Check** (2007) detects message races at run time; **SAMPI** (CLUSTER'22) samples MPI traces for race detection; **PDMPI** is the predictive deadlock tool of the logical-clock line. The horizon and the race-as-object are theirs; the feasibility criterion with a theorem, the consequence taxonomy and the buffering profile are not in any of them.
- **The complexity side:** *The Complexity of Testing Message-Passing Concurrency* (POPL'26) gives the landscape for channels (VCh-rf and relatives) and already uses the swap as a mutation operator; §10.5 positions this work as its MPI delta. *Predictive Monitoring with Strong Trace Prefixes* (CAV'24) is the shared-memory analogue of reasoning over a prefix of the trace.

**What the whole line shares — and where it stops:**

```
   ✔ genuinely predictive (no re-execution)
   ✘ deadlocks only
   ✘ single-path only
   ✘ an SMT/SAT call per query
   ✘ no partial order with a soundness theorem
   ✘ no complexity map beyond one NP-completeness result
   ✘ no online algorithms
```

So the correct novelty claim is *not* "the intersection of prediction theory and MPI is empty" — it is:

```
   ┌──────────────────────────────────────────────────────────────┐
   │  What is empty is:                                           │
   │     SOUND PARTIAL ORDERS WITH PROVEN GUARANTEES              │
   │                    ×                                         │
   │     MESSAGE-MATCHING SEMANTICS                               │
   └──────────────────────────────────────────────────────────────┘
```

### 12.3 Exhaustive exploration — contrast, not competition

**ISP / POE**, **DAMPI**, **MSPOE** (sound reduction of persistent sets; the key dependency is competition between a wildcard and a deterministic receive), **MPI-SV**, **CIVL / MPI-Spin** (symbolic verification). These cover the space completely but pay a combinatorial price in the number of competing wildcards. Prediction replaces exploration with one run plus analysis; re-execution survives only as a *confirmation* mechanism (§16).

### 12.4 Observational tools — the ecosystem we plug into

- **MUST** — the de facto MPI sanitizer: invalid arguments, deadlocks, type mismatches, data races on nonblocking and RMA operations; PMPI interception; integration with **ThreadSanitizer** through an annotation interface for local races; correctness analysis of nonblocking collectives (span init→completion, stride expressions for buffers). Mapping MPI/OpenMP onto TSan fibers (SC-W'23) costs up to 3× on nonblocking communication.
- **Coupling static and dynamic tools** (SC-W'25, RWTH Aachen): a modified TSan instrumentation pass reads static-analysis reports and instruments only potentially racy accesses, cutting MUST's overhead. **Doubly important for us** — both a differentiation point and the natural supplier of machinery for the L2 skeleton (§9.2).
- **RMASanitizer** (ICPP'24) — races in remote-memory-access programs.
- **PARCOACH** (collectives), **ITAC**, **MPI-Checker** (Clang static analysis: request lifecycle), **TypeART** (types).
- The classical statement of message races (*Optimal Tracing and Replay for Debugging Message-Passing Parallel Programs*, 1992, and the race-detection papers of that line) — no modern sound theory or algorithmics behind it.

### 12.5 Sound prediction in shared memory — the programme being ported

- **WCP** — *Dynamic Race Prediction in Linear Time.* PLDI'17. The first sound partial order weaker than happens-before with a linear-time algorithm.
- **SDP / M2, SyncP**, optimality and complexity of prediction — the same programme.
- **Sound Dynamic Deadlock Prediction in Linear Time** — PLDI / PACMPL 2023.
- **Predictive Monitoring with Strong Trace Prefixes** — CAV'24; **Optimistic Prediction of Synchronization-Reversal Data Races** — ICSE'24. Recent machinery directly applicable to §10.
- *Sound Deadlock Prediction.* OOPSLA'18. Executability constraints + SMT — both the methodological template for lifting restrictions and the demonstration of what completeness costs.
- **Soundness of Predictive Concurrency Analyses.** OOPSLA'25. A unified soundness definition for predictive analyses via execution graphs — composability, well-formedness, executability, memory consistency — with six analyses proved sound. **Explicitly restricted to shared memory (locks, reads, writes), which is precisely our opening:** carry the framework to matching semantics, replacing memory consistency with matching consistency. Our theorems are stated in this template.

### 12.6 Prediction beyond shared memory

- *Two-Phase Dynamic Analysis of Message-Passing Go Programs based on Vector Clocks*, 2018. Vector clocks for message passing (Go channels, `select`) — the closest methodological predecessor outside MPI.
- **ADVOCATE / AdvocateGo**. A predictive analysis tool for Go: record a trace → analyse → **rewrite the trace and re-execute to confirm**; distinguishes "actual" from "possible" bugs; covers channels, `select`, goroutine leaks. This settles the question of whether the channel niche is occupied:

```
   occupied INSTRUMENTALLY        ✔  a working tool exists
   occupied THEORETICALLY         ✘  no sound partial orders
                                  ✘  no complexity map
                                  ✘  no online algorithms
                                  ✘  confirmation is by re-execution,
                                     i.e. prediction is not separated
                                     from exploration
```

   Our position survives, refined. MPI semantics — matching over (src, tag, comm), non-overtaking, buffering regimes, collectives — is strictly richer than Go channels, so a theory built for MPI specialises to Go, not the other way round.

- **dl²** — *Detecting Communication Deadlocks in Deep Learning Jobs*, FSE'25 (Microsoft Research). Intercepts PyTorch distributed **and NCCL**, models the run as an execution graph, builds a wait-for graph, handles nondeterminism by action reordering; 1.00–1.09× overhead. Observational; no predictive layer. **Double significance:** independent evidence that message-matching bugs are alive in the AI stack and publishable at a top SE venue, and a natural target for WP4.

### 12.7 Benchmarks

**MPI-BugBench** (EuroMPI'24) — the unified harness merging **MPI-CorrBench**, the **MPI Bugs Initiative** (1,730 programs, 9 categories, "Message race" among them) and **RMARaceBench**; accompanied by work on quality metrics (SC-W'24) and tool coverage (SC-W'25). Reference points: MUST (best on MBI, F1 ≈ 0.87), ITAC, PARCOACH (F1 ≈ 0.29). The MPI-BugBench abstract itself concedes that no existing benchmark "fully reflects real-world MPI usage patterns" — which is where our contributed predictive test cases fit.

### 12.8 Gap table

| | Observational (MUST etc.) | Exhaustive (ISP/DAMPI) | SMT prediction (MOPPER, ASE'20, TPDS'23) | **This work** |
|---|---|---|---|---|
| Sees unobserved bugs | no | yes | yes | **yes** |
| No re-execution | yes | no | yes | **yes** (re-execution only to confirm) |
| Control-flow dependence class | any | any | none (single-path) | **metadata (L1–L2)**; payload → lower bound |
| Complexity | linear | exponential | NP (solver per query) | **polynomial on the target fragment + a map of the rest** |
| Bug families | all, observationally | deadlocks, partly races | deadlocks | **message races (primary), deadlocks, orphans, buffer races** |
| Soundness theorems | n/a | partly | yes, w.r.t. the encoding | **yes, in the OOPSLA'25 template** |

---

## 13. What adapts from shared-memory theory, and what does not

A blunt inventory, because six months can be lost trying to stretch WCP over a domain it does not fit.

| From shared memory | Transfers? | Why |
|---|---|---|
| Vector clocks, program order, HB machinery | **yes, directly** | infrastructure, domain-agnostic |
| WCP's critical-section rule → request-spans (family ③) | **yes, near-verbatim** | `Isend(buf,&req) … Wait(req)` *is* a critical section on `buf`; conflict detection is the same algorithm |
| Soundness framework (OOPSLA'25) | **yes, as a template** | one component changes: memory consistency → matching consistency |
| Deadlock prediction (PLDI'23; OOPSLA'18): abstract dependencies, wait-for cycles | **partly** | a process at a blocking `Recv` waits for a *set* of possible sends, not one lock ⇒ the wait-for structure becomes a hypergraph |
| Optimistic / synchronisation-reversal techniques (ICSE'24) | **worth testing** | "reversing" a single match is a natural minimal operation; the analogy is promising but unproven |
| WCP's lock rule as such | **no** | a lock is reusable, a send is consumed; the combinatorics differ |
| The lemma "rf preserved ⇒ σ′ executable" | **no** | this is precisely the central difficulty (§7) |

```
  ╔═══════════════════════════════════════════════════════════════════╗
  ║  Conclusion: there is no direct adaptation. What transfers is     ║
  ║  the infrastructure and one bug family out of three. The theory   ║
  ║  is built from scratch, and its proper mathematical home is not   ║
  ║  linearisation of partial orders but MATCHING UNDER PRECEDENCE    ║
  ║  CONSTRAINTS. Accept this on day one.                             ║
  ╚═══════════════════════════════════════════════════════════════════╝
```

---

## 14. Novelty claims

Ranked, not listed as equals — the ranking is itself a decision about where the project's centre of gravity sits.

```
   ┌─ PRIMARY ────────────────────────────────────────────────────────┐
   │  A FEASIBILITY CRITERION and a SOUNDNESS THEOREM for alternative │
   │  matchings in MPI's matching semantics (OOPSLA'25 template),     │
   │  with MESSAGE RACES as the central object and a taxonomy of the  │
   │  CONSEQUENCES a changed match has (1a-1f, ②), which is what the  │
   │  evaluation counts.                                              │
   └──────────────────────────────────────────────────────────────────┘

   ┌─ SUPPORTING ─────────────────────────────────────────────────────┐
   │  1. The L1 / L1+SP / L2 hierarchy, with evidence that the        │
   │     single-path assumption of the existing predictors is unsound │
   │     on metadata-dependent code, and twins that show what the     │
   │     skeleton must contain (destination-of-send, collective       │
   │     participation, payload that mirrors metadata).               │
   │  2. A LINEAR CHECK per alternative for any number of changed     │
   │     receives, a kernel from non-overtaking that bounds the        │
   │     single-change alternatives to w·P, and the position of the   │
   │     matching-choice problem relative to POPL'26 (§10.5).          │
   │  3. The BUFFERING PROFILE: every prediction carries the set of   │
   │     thresholds at which it is realisable; feasibility is         │
   │     monotone in B (theorem), deadlock predictions are not          │
   │     (witnesses); the implementation's threshold is measured and  │
   │     predictions below it replayed with the threshold forced down. │
   │  4. Replay as the confirmation discipline: every finding carries  │
   │     a pin that realises its execution, a confirmation requires    │
   │     the pin to have been applied, and the unconfirmed ones are    │
   │     listed.                                                        │
   └──────────────────────────────────────────────────────────────────┘

   The predictive horizon as such is NOT claimed: MAD (2000) stated it and
   the unaffected-race papers (2004) built on it.

   ┌─ VALIDATION, NOT A CLAIM ────────────────────────────────────────┐
   │  Buffer races on nonblocking operations (request-span as the     │
   │  analogue of a critical section). Transfers near-verbatim from   │
   │  WCP, and therefore serves as a correctness check on the model.  │
   └──────────────────────────────────────────────────────────────────┘

   ┌─ ENGINEERING ────────────────────────────────────────────────────┐
   │  A tool evaluated on the MPI Bugs Initiative (115 wildcard,      │
   │  probe, tag and buffering programs) and on MPI-BugBench level 2  │
   │  (388 programs) against MUST, with observed and predicted         │
   │  findings scored apart and every prediction replayed.            │
   └──────────────────────────────────────────────────────────────────┘
```

---

# EXECUTION

## 15. Work plan

```
        M0    M1   M2   M3   M4   M5   M6   M7   M8   M9  M10  M11  M12  M13+
        │     │    │    │    │    │    │    │    │    │    │    │    │    │
 WP0 ▐██▌     gate: kill or continue
 WP1     ▐█████████████████████████▌        theory
 WP2               ▐███████████████████████████████▌   algorithms + prototype
 WP3                              ▐███████████████████████████████▌  eval+paper
 WP4                                                            ▐████████████►
                                                                 second paper
        └──────────────────────────────────────────────────────┘
                    first top-tier submission target
```

### WP0 — the gate (3 weeks, before anything else)

Two questions, both cheap, both able to kill or reshape the project.

**(a) Measure the payload-obliviousness bet (§7.3).** Take AMG, MACSio and NEKbone — the three ECP proxy applications that actually use `MPI_ANY_SOURCE` — plus the master–worker and `Waitany` idioms from MPI-BugBench. Count: at how many wildcard-receive sites does the continuation depend only on metadata?

```
   high fraction  ⇒ L2 covers real code
                  ⇒ PLDI trajectory; the engineering is justified

   low fraction   ⇒ L2 covers toys
                  ⇒ honest project = L1 + L3 (horizon + lower bound)
                  ⇒ POPL/CAV trajectory; cut the tool work
```

**(b) Hand-check the mini-model** (blocking p2p + wildcards, 2–3 processes, levels L0–L1) on 10 MPI-BugBench idioms, **including master–worker with `ANY_SOURCE` and a `Waitany` loop** — the idioms that probe the L1/L2 boundary.

**Gate criteria (all three must hold):**

```
   ① ≥3 of 10 idioms yield a prediction invisible to MUST on the same
     trace and confirmable by hand-constructing the schedule
   ② ≥1 of them is family ① (message race), not family ③ (buffer race)
   ③ ≥1 requires going beyond single-path (i.e. needs the L1 horizon
     or the L2 skeleton) — otherwise the contribution collapses into
     a restatement of MOPPER
```

Failure on any one ⇒ stop and re-frame. *(The niche-occupancy question for channels/actors is already closed — see §12.6 — and does not need to be re-litigated here.)*

**Optional side-product:** the bug-distribution study identified in §5.3 — a few days of issue-tracker mining, a citable short paper, and better motivation for the main work.

### WP1 (M1–M5) — theory

```
   ▸ trace model and feasibility (§10.1–10.2)
   ▸ L1: definition of the predictive horizon; soundness theorem
   ▸ partial orders for message races and deadlocks
   ▸ L2: definition of the minimal skeleton; sufficiency theorem
   ▸ L3: lower bound / necessity of a solver
   ▸ complexity: NP-hardness and tractability along the w axis
   ▸ single-swap feasibility ladder (see §15.1) — the first small theorem
```

**Deliverable:** a technical report that is the theory half of the first paper.

### WP2 (M4–M9) — algorithms and prototype

```
   ▸ online algorithms over generalised vector clocks
   ▸ prototype on OUR OWN PMPI wrapper — deliberately NOT on the MUST
     code base (see the engineering risk in §17)
   ▸ CONFIRMING REPLAY: a PMPI layer that forces a chosen matching
     (pinning the source of a wildcard receive, injecting delays,
     switching eager/rendezvous) to reproduce a predicted schedule.
     Replay is NOT part of the analysis — prediction stays single-run —
     it exists to separate true predictions from model/tool divergence.
   ▸ minimal Clang pass for the L2 skeleton
   ▸ families ① and ③ first; ② and ⑤ as derivatives
```

**Deliverable:** a prototype that runs on MPI-BugBench end to end.

### WP3 (M8–M12) — evaluation and paper

```
   ▸ MPI-BugBench: coverage / precision / noise against MUST, ITAC, PARCOACH
   ▸ NAS Parallel Benchmarks and mini-apps (AMReX class) for overhead
   ▸ real bugs from the case-study list (§5.2) where reproducible
   ▸ OPTIONAL: integration with MUST and TSan annotations — needed only
     for overhead comparability, not for the main result
   ▸ artifact evaluation package (mandatory in practice at PLDI/OOPSLA)
   ▸ contribute predictive test cases back to MPI-BugBench
```

**Deliverable:** submission to PLDI or OOPSLA.

### WP4 (M12+, second paper)

```
   either  ▸ MPI + threads: composing the matching order with WCP
                            (bug family ⑨ in the taxonomy)
   or      ▸ prediction for RMA epochs — the bridge to NVSHMEM work
   or      ▸ porting L1/L2 to NCCL / PyTorch distributed (the dl² arena)
```

### 15.1 The first month, concretely

Do not start by "building a theory of prediction for message passing" — that is neither startable nor finishable. Start with one small theorem that can be settled in weeks.

```
   GIVEN     trace σ, observed matching M,
             two matches (s₁,r₁), (s₂,r₂) ∈ M

   ASK       is  M′ = M \ {(s₁,r₁),(s₂,r₂)} ∪ {(s₁,r₂),(s₂,r₁)}  feasible?

   SETTLED     linear for any M′ with the matching given (model.md T4):
               the check does not depend on how many receives changed;
               POPL'26 already uses the swap as a mutation operator, so the
               single-swap result is a corollary of their VCh-rf, not a
               theorem of ours

   THE LADDER  1 change  →  2 changes (kernel heads on two ranks, judged
               by the same check)  →  the CHOICE of which receives to
               change, which is the NP-complete problem, parameterised by
               w, tags and collective windows: open

   ┌──────────────────────────────────────────────────────────────┐
   │  This ladder IS a publishable complexity map, it fits the    │
   │  genre of prediction complexity, and it is small enough to   │
   │  finish in a month.                                          │
   └──────────────────────────────────────────────────────────────┘
```

**What not to do first:** do not start with deadlocks. Four groups are active there (CLUSTER'22, TPDS'23, Parallel Computing'24, Internetware'24) and all of them are stuck at the same wall. Message races are open, and they are where the 10,000-CPU-hour bugs live.

---

## 16. Evaluation plan

```
  (a) PRIMARY CLAIM  (E1)
      labelled errors predicted on a run that showed nothing itself,
      which MUST does not report on the same program, AND the fraction
      confirmed by replay with the pin applied. Observed and predicted
      findings are scored in separate rows; a program whose run hung
      under the tracer is observation, not prediction. Each benchmark
      race gives two numbers: racing receive flagged (L1) and
      consequence certified (L1+SP or L2), because the benchmark's races
      are payload-dependent by construction. Every finding is replayed;
      the unconfirmed ones are listed, not dropped.

  (b) GROUND TRUTH  (E1, E7)
      MBI (115 wildcard, probe, tag and buffering programs) and
      MPI-BugBench level 2 (388): precision, recall, F1 per row, the
      warnings on correct programs, findings per program (the SC-W'24
      noise view), and the L2 twins on MBI's payload races.

  (c) BASELINES
      ▸ MUST 1.9.2, the same source rebuilt and run under MPICH in
        MPI-BugBench's container, the same timeout: the observational floor
      ▸ the exhaustive single-path explorer of the prototype as the
        implementation of MOPPER's assumption (L1+SP), compared with the
        checker on every alternative (E6)
      Cut, with the reason: ITAC is proprietary and observational;
      PARCOACH is static and collective-only; PDMPI and the ASE'20 tool
      are not available to run.

  (d) OVERHEAD  (E4, E5)
      NPB 3.4.3 classes S and A at 4 and 16 ranks, AMG at 8 ranks, one node:
      traced/plain time and analysis time, stated for that range; a cluster
      sweep is not part of this work.

  (e) ABLATIONS
      ▸ by level (E3): L1, L1+SP and L2 on the idioms, with the spurious
        single-path deadlocks named
      ▸ by buffering threshold B (E2): the profile of every alternative
        over every breakpoint, the implementation's threshold measured,
        predictions replayed with it forced down
      ▸ by w: alternatives per wildcard receive, from the corpus tables
```

The confirming-replay component in (a) is not optional. Without it the primary claim is indistinguishable from a false-positive rate, and that will be the first question a reviewer asks. A soundness theorem certifies the *model*; the *tool* can still diverge from it.

---

## 17. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| **Engineering overload in WP2** — PMPI layer + online clocks + replay + static pass, in parallel with WP1 theory, for one person | **high** | prototype on our own wrapper; MUST integration demoted to optional in WP3; recruit an HPC co-author (Aachen) specifically for the tool — move this from "possible collaboration" into the plan |
| **No MPI domain expertise in the group** (no prior MPI/HPC publications) | medium | external co-author; early validation of the model through EuroMPI / the MPI Forum community; treat the domain as the project's own contribution |
| **The payload-obliviousness bet fails** (WP0a) | medium | the project narrows to L1 + L3 — still a theory contribution, retargeted to POPL/CAV, with the tool work cut |
| **The L1 horizon is too short** (most interesting witnesses lie past it) | medium | this promotes L2 from optional to mandatory; the skeleton is a small Clang pass, engineering-cheap; if L2 also fails, L3 remains as a theorem |
| **Early NP-hardness of everything interesting** | low–medium | the complexity landscape along w is itself a result in the genre of prediction complexity; conservative polynomial approximations |
| **PL reviewer: "MPI is old"** | medium | §2 framing (a class of semantics, not MPI); dl² FSE'25 as evidence; theory-first presentation |
| **Competition from the deadlock line** (CLUSTER'22 → Internetware'24) | medium | centre of gravity moved to message races and the L1/L2 hierarchy, where that line does not operate; Parallel Computing'24 is taken as a baseline rather than treated as a rival |
| **Overlap with Aachen** (SC-W'25 coupling) | low | they reduce the overhead of observational analysis; we use the same machinery for the L2 skeleton — complementary, and collaboration is desirable |
| **Vector clock scalability at P ≫ 10³** | medium | sparse / hierarchical representation; explicit statement of the claimed configuration range |
| **Artifact evaluation not budgeted** | low | time reserved in WP3 |

---

## 18. Resources, environment, collaboration

```
  COMPUTE      a CPU cluster, tens to hundreds of ranks.
               NO GPUs. NO proprietary tools. NO reserved allocations.
               This is the project's insurance value inside a wider
               portfolio: it cannot be blocked by hardware access.

  SOFTWARE     MPICH / Open MPI (both — the buffering parameter B
               requires at least two implementations); LLVM/Clang for
               the L2 pass; MPI-BugBench; NAS Parallel Benchmarks;
               MUST for baseline comparison.

  PEOPLE       one researcher plus one theorist.
               REQUIRED: one HPC-side collaborator for the tool.
               Natural candidate: the RWTH Aachen group (MUST,
               MPI-BugBench, SC-W'25 static/dynamic coupling) — they
               own the machinery the L2 skeleton needs, and the
               relationship is complementary rather than competitive.

  COMMUNITY    EuroMPI and the Correctness workshop at SC are the right
               venues for early validation of the model; MPI-BugBench
               is the right place to contribute test cases.
```

---

## 19. Success criteria

```
   MINIMUM (project is worth having done)
   ──────────────────────────────────────
   ▸ L1 formalised, the feasibility criterion with a soundness theorem
   ▸ the ladder: a linear check for any changed matching, single- and
     two-change alternatives enumerated, the choice problem placed
   ▸ benchmark bugs predicted from clean runs that MUST does not report,
     confirmed by replay with the pin applied; and on a real application
     a prediction confirmed realisable by replay, with an open statement
     that no harmful alternative was found there (AMG: 3 263 probe
     alternatives, all benign)

   TARGET
   ──────
   ▸ L2 with the minimal-skeleton theorem and the L3 lower bound
   ▸ the matching-choice problem: FPT in w or a hardness result
   ▸ tool evaluated on MBI and MPI-BugBench against MUST
   ▸ one top-tier paper (PLDI / OOPSLA / POPL)

   STRETCH
   ───────
   ▸ predictive test cases accepted into MPI-BugBench
   ▸ second paper: MPI + threads, or the port to NCCL / PyTorch
   ▸ the bug-distribution study (§5.3) as an independent short paper
```

---

# APPENDICES

## Appendix A. Is MPI still relevant?

The framing in §2 does not depend on this, but the question gets asked, so here is the answer on record.

```
  STANDARD      MPI 5.0 ratified by the MPI Forum on 5 June 2025;
                headline feature is a standardised ABI. The Forum is
                working on the next version, designated "MPI Next"
                (feature set not yet fixed). Working groups on fault
                tolerance, hybrid & accelerator programming and
                collectives meet regularly.

  INSTALLED     Every exascale system (Frontier, Aurora, El Capitan,
  BASE          Fugaku) and essentially the whole corpus of national-
                laboratory scientific software runs on MPI. Legacy code
                is measured in hundreds of millions of lines and does
                not migrate on a decade timescale.

  COMMUNITY     MPI-BugBench (EuroMPI'24); SC-W 2023–2025 papers on
                metrics and tool coverage; RMASanitizer (ICPP'24);
                static/dynamic coupling (SC-W'25). There are reviewers
                who understand this work and baselines to compare with.

  AI STACK      Mainstream LLM training does not use MPI (NCCL/Gloo/RPC)
                — say so openly. But the SEMANTICS this theory targets
                is shared, and dl² (FSE'25) shows the same bug class
                being hunted in PyTorch/NCCL at a top SE venue.
                HPC+AI convergence (scientific ML on supercomputers,
                CUDA-aware MPI) keeps MPI adjacent to GPU workloads.
```

**The sober consequence:** this direction buys no ML visibility. That is the price of minimal risk, partially offset by the WP4 bridge to NCCL / NVSHMEM.

## Appendix B. Portfolio position (internal)

The theory lane of the track: zero external dependencies — no GPUs, no closed tools, no cluster quotas — which makes it the insurance policy for a wider portfolio if GPU access slips. Its load peaks (proofs) are anti-correlated with the engineering peaks of GPU-bound lanes. Convergence point: WP4 (prediction for RMA epochs) is also WP3 of the NVSHMEM direction. Resulting research identity: *"sound prediction and compiler-assisted cheapening of dynamic concurrency analyses beyond shared memory."*

The dominant risk has shifted from scientific to engineering (WP2) and is addressed in §17. Scope risk remains low; hardware requirement is effectively zero.

## Appendix C. Glossary

| Term | Meaning |
|---|---|
| **Rank** | one process of an MPI program, identified by a number 0..N−1 |
| **Communicator** | a named group of ranks; messages never cross communicators |
| **Match** | the pairing of one `Send` with one `Recv`; the atom of nondeterminism |
| **Wildcard receive** | `MPI_Recv` with `MPI_ANY_SOURCE` and/or `MPI_ANY_TAG` — accepts from anyone |
| **Non-overtaking** | MPI's guarantee that messages between a fixed (src, dst, comm, tag) are received in send order |
| **Request-span** | the interval between starting a nonblocking operation and its `Wait`; the buffer belongs to MPI throughout |
| **Collective** | an operation all ranks of a communicator perform together (`Bcast`, `Allreduce`, `Barrier`, …) |
| **Trace** | the recorded sequence of events of one run, plus the matching that occurred |
| **reads-from (rf)** | in shared memory: which write produced the value a read observed |
| **Correct reordering** | a permutation of a trace preserving program order and reads-from — guaranteed executable |
| **Sound** | every reported bug is genuinely realisable (no false alarms) |
| **Complete** | every realisable bug is reported (no misses) |
| **Partial order** | an ordering in which some pairs are deliberately left incomparable; unordered conflicting events signal a predicted bug |
| **Vector clock** | the standard data structure for computing such orders in one streaming pass |
| **single-path** | a program whose sequence of MPI calls does not depend on received data |
| **Payload / metadata** | message contents vs. facts about the match (source rank, tag, count, completion index) |
| **Predictive horizon** | the point in each process's timeline past which a trace no longer determines behaviour once a match is changed |
| **PMPI** | the MPI standard's profiling interface — lets a tool intercept every MPI call without recompiling the application |
| **`B` (buffering threshold)** | how many bytes a `Send` may hand off without blocking; implementation-defined, hence a parameter of our soundness claims |
| **`w`** | maximum number of simultaneously competing wildcard receives; our complexity parameter |
| **FPT** | fixed-parameter tractable: solvable in `f(w)·poly(n)` — fast when the parameter is small even if the general problem is NP-hard |

## Appendix D. References

**MPI prediction**
1. Forejt, Kroening, Narayanaswamy, Sharma. *Precise Predictive Analysis for Discovering Communication Deadlocks in MPI Programs.* FM 2014; with Joshi, ACM TOPLAS 39, 2017.
2. Huang, Ogles, Mercer. *A Predictive Analysis for Detecting Deadlock in MPI Programs.* ASE 2020.
3. *Deadlock Detection for MPI Programs Based on Refined Match-sets.* IEEE CLUSTER 2022.
4. *Improving the Efficiency of Deadlock Detection in MPI Programs Through Trace Compression.* IEEE TPDS 34, 2023.
5. Li, Wang, Zhang, Liu. *Program partitioning and deadlock analysis for MPI based on logical clocks.* Parallel Computing 119:103061, 2024.
6. Hao, Wang, Zhang. *Efficient Deadlock Detection in MPI Programs with Path Compression and Focus Matching.* Internetware 2024.
7. *MAPPED: Predictive Dynamic Analysis Tool for MPI Applications.* IEEE, 2013.

**Exhaustive exploration**
8. Vakkalanka et al. *ISP / POE.* 2008–2010. — 9. Vo et al. *DAMPI.* SC 2010. — 10. Sharma, Gopalakrishnan et al. *A Sound Reduction of Persistent-Sets for Deadlock Detection in MPI Applications.* SBMF 2012. — 11. *MPI-SV.* ICSE 2020. — 12. Siegel et al. *CIVL / MPI-Spin.*

**Observational tools**
13. Hilbrich, Protze et al. *MUST.* — 14. Hilbrich, Weber, Protze, de Supinski, Nagel. *Runtime Correctness Analysis of MPI-3 Nonblocking Collectives.* EuroMPI 2016. — 15. Protze et al. *Mapping High-Level Concurrency from OpenMP and MPI to ThreadSanitizer Fibers.* SC-W 2023. — 16. Jenke et al. *Coupling Static and Dynamic MPI Correctness Tools to Optimize Accuracy and Overhead.* SC-W 2025. — 17. *RMASanitizer.* ICPP 2024. — 18. Saillard et al. *PARCOACH.* — 19. *MPI-Checker*; *TypeART*. — 20. Netzer, Miller. *Optimal tracing and replay for debugging message-passing parallel programs.* 1990s.

**Sound prediction, shared memory**
21. Kini, Mathur, Viswanathan. *Dynamic Race Prediction in Linear Time.* PLDI 2017. — 22. Mathur et al. *SDP / M2 / SyncP*; *Complexity of Dynamic Data Race Prediction.* LICS 2020. — 23. Tunç, Mathur, Pavlogiannis, Viswanathan. *Sound Dynamic Deadlock Prediction in Linear Time.* PLDI 2023. — 24. Ang, Mathur. *Predictive Monitoring with Strong Trace Prefixes.* CAV 2024. — 25. Shi, Mathur, Pavlogiannis. *Optimistic Prediction of Synchronization-Reversal Data Races.* ICSE 2024. — 26. Kalhauge, Palsberg. *Sound Deadlock Prediction.* OOPSLA 2018. — 27. *Soundness of Predictive Concurrency Analyses.* OOPSLA 2025.

**Prediction beyond shared memory**
28. Sulzmann, Stadtmüller. *Two-Phase Dynamic Analysis of Message-Passing Go Programs based on Vector Clocks.* 2018. — 29. Kassubek et al. *ADVOCATE / AdvocateGo.* — 30. Gao, Luo, Lin, Zhang, Wu, Yang. *dl²: Detecting Communication Deadlocks in Deep Learning Jobs.* FSE 2025.

**Benchmarks and empirical data**
31. *MPI-BugBench: A Framework for Assessing MPI Correctness Tools.* EuroMPI 2024. — 32. Laurent, Saillard, Quinson. *The MPI Bugs Initiative.* Correctness @ SC 2021. — 33. Hück et al. *MPI-CorrBench.* HPDC-W 2021; *Investigating the Real-World Applicability of MPI Correctness Benchmarks.* SC-W 2023. — 34. Sultana, Skjellum, Laguna et al. *Understanding the Usage of MPI in Exascale Proxy Applications.* ExaMPI@SC 2018 / CCPE 33(14), 2021. — 35. Bernholdt et al. *A Survey of MPI Usage in the US Exascale Computing Project.* CCPE 32(3), 2020. — 36. Laguna et al. *A Large-Scale Study of MPI Usage in Open-Source HPC Applications.* SC 2019. — 37. Gopalakrishnan, Hovland, Iancu, Krishnamoorthy, Laguna, Lethin, Sen, Siegel, Solar-Lezama. *Report of the HPC Correctness Summit.* arXiv:1705.07478, 2017. — 38. Laguna, Schulz et al. *Debugging High-Performance Computing Applications at Massive Scales.* CACM 58(9), 2015. — 39. Chapp et al. *ANACIN-X.* Software Impacts, 2021. — 40. Sato, Ahn, Laguna, Lee, Schulz. *Clock Delta Compression for Scalable Order-Replay of Non-Deterministic Parallel Applications.* SC 2015.
