# Several threads calling MPI: design

Status: design (29 Oct 2026), nothing implemented. Decided 29 Oct 2026: Stretto is extended to
programs in which several threads of an MPI process call MPI on shared communicators
(`MPI_THREAD_MULTIPLE`, and `MPI_THREAD_SERIALIZED` with several calling threads). Today such a
trace is analysed for observed findings only (`analysis/stretto/report.py:254-265`,
`analysis/stretto/hb.py:272-277`, `docs/model.md` §6). This document says what the standard
allows, what the tracer must record, how the model, the analysis and the replay change, which
programs test it, which benchmarks contain such programs, and what the work costs.

Terms used below. A *chain* is one logical thread of control of a process: a POSIX thread, or an
OpenMP implicit task (one thread of one parallel region). A rank's records become one chain per
calling thread. *HB* is the intra-process happens-before relation: program order on each chain
plus the edges of recorded synchronisation (thread creation and join, locks, barriers, OpenMP
constructs). Two operations of one process are *concurrent* when neither precedes the other in
HB. *Sync-preserving* (from the predictive race analysis line of work, *Optimal Prediction of
Synchronization-Preserving Races*, POPL 2021) means: every recorded synchronisation object is used
in the same order as in the trace.

## 1. What the standard says

Section numbers are those of the MPI 4.1 standard as the Forum's HTML rendering
(mpi-forum.org/docs/mpi-4.1/mpi41-report/) numbers them, read on 29 Oct 2026. Until 29 Oct 2026
`docs/model.md` and comments in the analysis cited MPI 4.0's numbers as MPI 4.1's (§3.5 for
non-overtaking, §7.4 for communicator creation); in 4.1 point-to-point communication is chapter 4
and communicators chapter 8.

**Threads are not addressable; any thread may receive** (§12.6.1): "threads are not separately
addressable: the rank argument in a send or receive call identifies an MPI process, not a
thread. A message sent to an MPI process can be received by any thread in this MPI process."
And: "It is the user's responsibility to prevent races when threads within the same application
post conflicting communication calls. The user can make sure that two threads in the same
process will not issue conflicting communication calls by using distinct communicators at each
thread."

**Calls are atomic, blocking blocks one thread** (§12.6.1): "two concurrently running threads may
make MPI calls and the outcome will be as if the calls executed in some order, even if their
execution is interleaved", and "Blocking MPI calls will block the calling thread only [...] A
blocked thread will not prevent progress of other runnable threads on the same process". The
example that follows (a thread sends to its own process, another receives) "should always
succeed".

**Non-overtaking binds ordered operations only** (§4.5, Order): "If an MPI process has a single
thread of execution, then any two communication operations executed by this MPI process are
ordered. [...] if the MPI process is multithreaded, then the semantics of thread execution may
not define a relative order between two send operations executed by two distinct threads. The
operations are logically concurrent, even if one physically precedes the other. In such a case,
the two messages sent can be received in any order. Similarly, if two receive operations that are
logically concurrent receive two successively sent messages, then the two messages can match the
two receives in either order." The standard brackets this paragraph with two notes: "The MPI
Forum believes the following paragraph is ambiguous and may clarify the meaning in a future
version of the MPI Standard." Section 3.2 below avoids depending on its reading.

**Probe** (§4.8.1): a receive with the source and tag a probe returned "will receive the message
that was matched by the probe, if no other intervening receive occurs after the probe, and the
send is not successfully cancelled before the receive. If the receiving MPI process is
multithreaded, it is the user's responsibility to ensure that the last condition holds." Probe
progress has an exception for threads: `MPI_Probe` returns once a matching send is initiated
"unless the message is received by another concurrent receive operation (that is executed by
another thread at the probing MPI process)", and a polling `MPI_Iprobe` eventually returns true
"unless the message is received by another concurrent receive operation or matched by a
concurrent matching probe". §12.6.2 repeats it ("it is up to the user to enforce this condition
using suitable mutual exclusion logic [...] Alternatively, MPI_MPROBE or MPI_IMPROBE can be
used"), and §4.8.2 introduces the matching probe because "the list of incoming messages is global
among the threads of each MPI process".

**Collectives must be ordered by the user** (§12.6.2): "Matching of collective calls on a
communicator, window, or file handle is done according to the order in which the calls are
issued at each process. If concurrent threads issue such calls on the same communicator, window
or file handle, it is up to the user to make sure the calls are correctly ordered, using
interthread synchronization." §7.14: "it is the user's responsibility to ensure that the same
communicator is not used concurrently by two different collective communication initialization
calls at the same MPI process."

**Requests** (§12.6.2): "A program in which two threads block, waiting on the same request, is
erroneous. Similarly, the same request cannot appear in the array of requests of two concurrent
MPI_{WAIT|TEST}{ANY|SOME|ALL} calls."

**Finalize** (§12.6.2): `MPI_Finalize` "should occur on the same thread that initialized MPI
[...] only after all process threads have completed their MPI calls, and have no pending
communication or I/O operations."

**Levels** (§12.2.1): FUNNELED, "the application must ensure that only the main thread makes MPI
calls"; SERIALIZED, "multiple threads may make MPI calls, but only one at a time: MPI calls are
not made concurrently from two distinct threads"; MULTIPLE, "with no restrictions".

### What is ordered and what is not

| pair of operations of one process | ordered for matching? |
|---|---|
| same chain, program order | yes, as today (non-overtaking on both sides) |
| different chains, ordered by HB (join, barrier, lock release then acquire, cond handshake) | yes: "the semantics of thread execution" define the order |
| different chains, concurrent | no: two sends may arrive in either order, two receives may take two messages in either order; a receive of one chain may take the message another chain probed |
| two collectives on one communicator | must be HB-ordered, or the program is erroneous |
| two waits on one request | must be HB-ordered and not overlapping, or erroneous |

### Thread levels

- **SINGLE, FUNNELED**: one chain calls MPI. The trace is a single chain and nothing new is
  predicted, provided the main thread's calls are a function of its results (the other threads
  feed it data, not calls). New observed checks: a call from a thread other than the main one
  violates FUNNELED (the tracer already marks such records with `tid`,
  `tracer/mpitrace.c:8-12`, `255-257`).
- **SERIALIZED**: several chains call MPI, never at once. Everything below applies: calls of
  different chains are ordered by whatever synchronisation serialises them, usually a lock, and
  which chain goes first is a scheduling choice. The races are those of MULTIPLE with
  lock-ordered chains (section 2.4: they are predicted at level L1+SP-T only). Physical call order
  equals record order, so the tracer needs no issue lock (2.2). New observed check: two calls of
  different chains overlapping in time (the tracer counts threads inside MPI).
- **MULTIPLE**: everything below.

## 2. Trace

### 2.1 What the tracer records today

- The provided level: `MPI_Init` calls `PMPI_Query_thread` after `PMPI_Init`
  (`tracer/mpitrace.c:1186-1191`, since 65affcf, 21 Oct 2026), `MPI_Init_thread` records the
  returned `provided` (`:1192`); the init record carries it as `thread` (`:1180`). So a run
  whose implementation grants more than `MPI_Init` asks is recorded with the level granted. Not
  covered: the sessions model (unmodelled), and Fortran under Open MPI (no trace at all).
- The calling thread: `tid` (the kernel thread id, `gettid`) on every record under MULTIPLE and
  on the records of other threads than the initialising one below it (`:233-257`).
- Record order: one global per-rank `seq`, assigned under the tracer's mutex at emission
  (`:239-264`). A record is emitted when the call returns; a blocking call also emits a `begin`
  record before it (`:1400`). The order of two threads' records in the file is the order of
  their emissions, which says nothing about the order in which MPI saw the calls.
- Nothing about intra-process synchronisation.
- The buffer guard is off under MULTIPLE (`:296-297`, `:781`).

The loader counts calling threads per rank (`analysis/stretto/trace.py:557-566`) and pairs a
`begin` with the next record of the same `tid` (`:521-547`); the report then declines to
predict.

### 2.2 What the tracer must record (format version 3)

1. **Chains.** Every record carries `tid`, now a small chain id assigned by the tracer, and a
   side record `chain` gives each id once: `name` (the logical name, below), `parent` (the
   creating chain), `at` (the parent's `seq` at creation) and `kind` (`pthread`, `omp`). The
   logical name is what makes a chain the same chain in another run: the main thread is `0`; the
   k-th thread created by chain `c` is `c.k`; the j-th OpenMP thread of the p-th parallel region
   entered by chain `c` is `c/p.j`. Names are stable when each chain creates threads and enters
   parallel regions in the same order in every run (premise P-T5, replay only). No per-thread
   sequence field is needed: a chain's records are the rank's records of its `tid`, in file
   order (each thread emits its own records in its own order), and `check_records`
   (`trace.py:478`) already detects a gap in `seq`.
2. **Happens-before, as vector-clock deltas.** The tracer keeps a vector clock per chain and per
   synchronisation object, in units of the chain's own records (TSan's scheme, at record
   granularity), and every record carries `after`: the `seq` of the latest record of each other
   chain that now precedes it in HB and did not precede the chain's previous record. The loader
   turns these into edges `complete(a) -> issue(b)`. Writing clocks instead of one record per
   lock operation keeps the trace proportional to the MPI calls, whatever the program's locking
   rate. A synchronisation point lies between two records of its chain, and `after` names the
   last record before it. The horizon keeps the point only if that record completes in H *and*
   the chain's continuation after it is determined: rule 1 cuts a chain right after a changed
   receive, and a release the chain performs after that receive is not in the horizon even
   though the receive is (section 3.4, rules 1 and 8).
3. **Communicating critical sections.** For a lock held by a chain when it makes an MPI call,
   side records `cs` (lock id, the acquire's ordinal on that lock, first and last `seq` inside).
   The L1 analysis needs only `after`; the level that reorders locks (L1+SP-T, 2.4) and the
   replay need to know which critical sections contain communication.
4. **Physical issue order under MULTIPLE (`iseq`).** The observed matching is reconstructed
   today by processing each rank's receives in record order (`trace.py:923-966`); with threads
   the record order of two chains is not the order in which MPI saw them, and the reconstruction
   would be wrong. Under MULTIPLE the tracer therefore (a) holds a per-process *issue lock*
   across the call that posts a point-to-point operation (`PMPI_Isend` family, `PMPI_Irecv`,
   `PMPI_Iprobe`, `PMPI_Improbe`, `PMPI_Imrecv`) and numbers it there (`iseq`); (b) rewrites the
   blocking forms as the nonblocking form plus `PMPI_Wait` (for matching this changes nothing:
   "Nonblocking communication operations are ordered according to the execution order of the
   calls that initiate the communication", §4.7.4), `MPI_Probe`/`MPI_Mprobe` as an `Iprobe`/`Improbe`
   loop that releases the lock between iterations, and `MPI_Sendrecv` as Isend + Irecv +
   Waitall (it is modelled so already, `mpitrace.c:1424-1434`). The records keep their kinds
   (a blocking receive is still a `recv` record), so the loader's view of a call does not
   change. Below MULTIPLE no lock is needed, since calls never overlap. Not covered:
   `MPI_Sendrecv_replace` (rare; marked, and a rank that runs it concurrently with an overlapping
   receive is degraded).
5. **Message hashes under MULTIPLE.** A 64-bit hash of the packed message (bounded by
   `STRETTO_HASH_MAX`, default 4 KiB) on sends and receive completions. It checks the
   reconstruction (premise P-T4 below) and lets a replay say which of two messages with
   one envelope a receive took.

What produces `after`:

| source | operations | edge |
|---|---|---|
| pthreads, by preloaded wrappers (`dlsym(RTLD_NEXT)`) | `pthread_create` (trampoline into the child), `pthread_join` | creator -> child's first event; child's last -> joiner |
| | `pthread_mutex_lock/trylock/unlock`, `pthread_rwlock_*`, `pthread_spin_*` | release -> next acquire (a successful trylock is an acquire; a failed one is a read of the lock state and is kept between the holder's acquire and release) |
| | `pthread_cond_wait/timedwait` | an unlock and a lock of the mutex; correct programs change the predicate under the mutex, so the mutex edges carry the order |
| | `pthread_barrier_wait` | all arrivals of one generation -> all departures |
| | `sem_post`/`sem_wait` | the latest post before the wait in tracer order -> the wait (stronger than needed: a sound over-constraint) |
| OpenMP with gcc's libgomp, by wrapping its `GOMP_*` entry points (exported by `libgomp.so.1`; 157 `GOMP_` symbols in the image) and the `omp_*_lock` API | `GOMP_parallel` (wrap the outlined function) | encountering chain -> each implicit task; each implicit task's end -> the encountering chain |
| | `GOMP_barrier`, `GOMP_loop_end`, `GOMP_sections_end`, `GOMP_single_*` with its barrier | barrier of the team |
| | `GOMP_critical_start/end`, `GOMP_critical_name_*`, `omp_set/unset_lock`, nest locks, `GOMP_ordered_*` | as mutexes |
| | `GOMP_sections_next`, `GOMP_loop_*_next` (dynamic and guided schedules), `GOMP_single_start` | a read-modify-write of the work-share: which chain gets which section or chunk is a result of the runtime, so its order is preserved like a lock's |
| | `GOMP_task`, `GOMP_taskwait`, `GOMP_taskgroup_*`, dependences | not in v1: an MPI call inside an explicit task degrades the trace (`threads-tasks`) |

Synchronisation inside an MPI call (the library's own mutexes, which Open MPI may take through
`pthread_mutex_lock`) and from the library's progress threads must be ignored: the wrapper skips
calls made while the calling thread is inside an MPI wrapper, and calls whose return address lies
in the MPI library's objects (a `dladdr` per call site, cached). Otherwise the library's locking
would order user threads.

OpenMP through OMPT (LLVM's libomp, which gcc's libgomp does not implement) is the portable route
and the one that covers tasks, as the Archer tool does for data races; it needs clang and libomp
in the image (the experiment image has neither; the first-read image has clang 18 but no libomp).
Planned for v2.

### 2.3 What the first version assumes

| premise | what it says | if it fails |
|---|---|---|
| P-T1 data-race freedom w.r.t. recorded sync | every shared-memory value that decides a chain's calls is read after the write it reads, in HB | the analysis assumes a chain issues its recorded calls in an order where it would read another value: over-prediction, and an L1 claim about calls the program does not make (unsound) |
| P-T2 complete sync recording | every synchronisation that orders such accesses is one of the table above | as P-T1 |
| P-T3 arbitrary scheduling and per-thread progress | any thread may be delayed arbitrarily; a blocked thread does not stop others (§12.6.1, requirement 2) | none in practice: the standard requires the second part of any thread-compliant implementation |
| P-T4 physical matching | the implementation matches in the order in which its posting entry points ran, serialised by the issue lock | the observed matching is wrong; checked against counts and hashes, and the trace degraded (`reconstruction`) when they disagree |
| P-T5 deterministic chain names | each chain creates threads and enters parallel regions in the same order in every run | replays name the wrong chain and end as misapplied (inconclusive); no effect on the analysis |
| P-T6 no MPI in explicit tasks (v1) | | the trace is degraded |

What is lost, and in which direction:

- **Atomics** (`__atomic_*`, C11/C++ atomics, `#pragma omp atomic`, spin flags, lock-free queues,
  C++20 `std::barrier`/`latch`, raw futexes) are not recorded: the instructions are inlined and
  cannot be interposed. A missing edge a -> b makes everything before a concurrent with
  everything after b. The analysis then predicts schedules the program cannot take (b's chain
  waits for a in reality) and, when the atomic decides control flow (an `atomic capture`
  counter that hands out work), calls the chain would not make. That is over-prediction, and
  unsound in the sense of T1 (premise P-T1 fails). The replay catches the first kind: the gate
  that forces b's side first never opens, so the replay times out as inconclusive and is never
  a confirmation; the second kind ends as misapplied when the chain reaches a pinned index with
  another call. A small annotation API (`stretto_hb_release(addr)`, `stretto_hb_acquire(addr)`,
  in the style of the dynamic annotations TSan understands) lets a user add such edges.
- **A mutex not recorded** (statically linked, a home-made spinlock): the same as an atomic.
- **Locks recorded but not reordered** (L1 is sync-preserving): every alternative that needs
  two critical sections of one lock in the other order is missed: under-prediction. Its size:
  for a program whose MPI calls all sit in critical sections of one lock (SERIALIZED emulation),
  L1 predicts exactly what the single-threaded analysis of the observed interleaving would
  predict, and none of the thread-order races; for a program whose threads communicate without
  locks around the calls (the common MULTIPLE style; of the eight CorrBench message-race programs
  of section 6, all but `deadlock_probe.c`), it loses nothing.
- **Work-share order preserved** (dynamic loop schedules, sections, single): the assignment of
  chunks to threads is the observed one; another assignment is a different set of chains'
  calls, which the trace does not contain. Under-prediction.

### 2.4 Ideas from predictive data-race analysis, and what applies

Those analyses reorder a trace of memory accesses and locks into another *correct reordering*,
in which every read reads from the same write. Stretto's trace has no memory accesses. That
decides most of the question:

- **Strong trace prefixes** (*Predictive Monitoring with Strong Trace Prefixes*, CAV 2024; see
  `docs/related-work/analysis.md` §4): a prefix downward closed under strong dependence (program
  order, reads-from) that preserves the weak dependence (lock order) on the events it keeps;
  earlier critical sections may be dropped. Rule 1 of the horizon is already this construction
  for matching: the changed receive reads from another send and its chain stops there. With
  threads, locks are the weak dependence, but without reads-from we must assume that each
  critical section may read what the previous one on the lock wrote, so lock order becomes
  strong dependence. A strong prefix can then drop a critical section only together with
  everything HB-after it, which is what rule 8 (3.4) does.
- **Sync-preserving reorderings** (SyncP, *Optimal Prediction of Synchronization-Preserving
  Races*, POPL 2021): reorderings that keep the order of critical sections on each lock, and an
  ideal (prefix) closure that computes them. This is the level L1 adopts: the horizon is a
  sync-preserving ideal, and its closure gains one rule.
- **Schedulable happens-before** (*What Happens-After the First Race?*, OOPSLA 2018): HB with
  reads-from edges, sound beyond the first race because the prefix up to each event is
  schedulable. Stretto's constraint graph already includes its reads-from analogue (a match
  edge per pair, a probe edge per observation), and acyclicity of the graph over the horizon is
  its schedulability check.
- **Weak causal precedence** (*Dynamic Race Prediction in Linear Time*, PLDI 2017) and **M2**
  (*Fast, Sound, and Effectively Complete Dynamic Race Prediction*, POPL 2020) weaken lock
  edges between critical sections that do not conflict, or reorder them guided by reads-from.
  Both need the accesses. Without them, the only sound way to reorder critical sections is to
  assume, as MOPPER's single-path assumption does for receives, that a chain issues the same
  calls whatever order its critical sections run in. That is level **L1+SP-T**: critical sections
  of one lock are atomic blocks that may run in any order respecting mutual exclusion, each chain
  issues its recorded calls, and the consequence is computed by the explorer and confirmed by
  replay. It is where lock-order deadlocks (a thread blocked in MPI while holding a lock another
  thread needs to send) and the thread-order races of SERIALIZED programs are predicted.
- A finer L1 for locks needs to know that the code between an acquire and an MPI call inside
  the critical section reads nothing another critical section writes. The first-read analysis
  (`docs/firstread.md`) computes the analogous fact for receive results from the sources; a
  version for lock-protected state is an open question (section 8).

## 3. Model and analysis

### 3.1 Trace model

A rank's trace is a set of chains; each chain is a sequence of records with issue and complete
nodes as today (`docs/model.md` §1). The process order is HB: program order on each chain plus
the edges of `after` (and an edge from the call that created a request to any call of another
chain that waits on it, which a correct program implies). Envelopes name processes, as before:
`r accepts s` is unchanged, with `rank(r)` the process of r's chain.

Identity: a send, receive or probe is `(chain, ordinal)`. In the code a chain id plays the role
the rank plays today in program-order structures (`Trace.ranks`, `cut`, `Rec.idx`), and a new
`proc` field takes over its envelope role (`Recv.accepts`, `Send.dst`, the queues). The main
chain of a rank has the rank's id, other chains ids from `np` on; a report names chain c of
rank r as `r.c` and lists logical names in a note. A rank with one chain is today's rank in
every structure.

The observed matching `M₀` is reconstructed per process in `iseq` order instead of record
order: each receive takes the earliest unconsumed send of its (msrc, mtag) queue in the
sender's `iseq` order. Under P-T4 this is exact. Every pair is checked against the recorded
count and, when present, the hash; a mismatch degrades the trace.

### 3.2 Physical order, not the ambiguous paragraph

An alternative is accepted only if it is realisable on an implementation that matches in the
physical order of postings and issues: the replay of a topological order of the graph posts and
issues in that order, and MPI's FIFO rule on that order yields M'. Thread scheduling provides
the freedom: two concurrent receives can be posted in either order because nothing orders their
chains. So no claim rests on the "logically concurrent, even if one physically precedes"
paragraph that the Forum calls ambiguous, and every accepted alternative is also allowed under
its permissive reading. The cost is a choice: for two concurrent receives r, r' of a process that
both accept s = M'(r), FIFO needs `issue(r) -> issue(r')` or `m(r', M'(r')) -> m(r, s)` (r' was
posted later, or was matched first). The graph must stay a DAG, so the check picks one
orientation per pair: the match edge if M'(r') precedes s on a common channel or r' is matched
in σ before r (by `iseq`), the issue edge otherwise. Sender side likewise, for two concurrent
sends of one channel that a receive accepts. A pick adds constraints, so acceptance stays sound;
it can reject an alternative that the other pick would accept (completeness, 3.6).

### 3.3 Candidates

Today candidates are steals and swaps at wildcard receives, by the non-overtaking kernel: per
source, the earliest send that no receive posted before r took (`matching.py:409-459`). With
chains:

- **"Earlier" becomes HB-earlier.** The kernel for r and a source is the set of sends of that
  channel that r accepts, that no HB-earlier receive took, and whose HB-earlier sends on the
  channel (that r accepts) were all taken by HB-earlier receives: an antichain, one send per
  sending chain at most, instead of one send.
- **Non-wildcard receives become candidates** when they have a concurrent competitor (a
  concurrent receive of the process with an overlapping envelope) or a concurrent sender (two
  sending chains on the channel): two threads each calling `MPI_Recv(src=1, tag=5)` race with no
  wildcard.
- **Concurrent swaps.** The natural thread race exchanges the messages of two concurrent
  receives r, r' (one per chain). Both are changed and both chains are cut by rule 1. Today a
  swap is two changes on one rank, of which only the first is certified at L1 (`docs/model.md`
  §3, finding 13); a concurrent swap is certified on both chains, since neither cut contains the
  other receive.
- **Steals with a concurrent loser.** A steal r <- s where s was taken in σ by a concurrent r2:
  r2 is changed too, either to the next message of its kernel (a second change) or left
  unmatched in H (pending, judged by rule 6).
- **The probe split.** A probe p of chain A that saw s, followed on A by a receive r with p's
  envelope that took s; a receive r' of another chain, concurrent with r and not HB-before p,
  that accepts s. Alternative: r' <- s, r <- the next message of its envelope in H, or nothing.
  p's result is the observed one, so A's chain up to r is in H and r is posted with the
  recorded arguments; the claim covers what r gets: a longer message (class 1a, truncation),
  one with other contents, or none (r blocks: a contained deadlock, class 1d, if nothing else in
  H can serve it). This is the §4.8.1 hazard stated at L1.
- **Probe alternatives across chains.** A probe whose result changes (it reports a message
  another chain's receive took, or both chains' probes report one message) cuts its chain right
  after it (rule 1, as today), so its consequence is outside L1 and is left to the replay, as for
  flag flips (`docs/model.md` §3, levels). CorrBench's `probe.c` and `deadlock_probe_2.c` are of
  this kind.
- **Flag flips** unchanged, with the poll key counted per chain: a chain's calls between failed
  polls are deterministic, a rank's are not.

Pruning (`matching.py:461-476`, `_follows`) must use only guaranteed edges: program order, sync,
match and rendezvous edges, never the orientation picks of 3.2, which an alternative may
reverse.

Count (T4 (i) restated): steals at most (receives with a wildcard, a concurrent competitor or a
concurrent sender) × P × W, with W the largest number of mutually concurrent sending chains on a
channel; concurrent swaps at most the same, since each pairs a receive with the receiver of a
kernel send. A thread pool of k chains receiving from one queue gives up to k-1 swaps per
receive; the report caps them per receive (a parameter) and says so.

### 3.4 Constraint graph and horizon

Graph (`matching.py:125-275`), changes:

- program order per chain; **sync edges** `complete(a) -> issue(b)` from `after`;
- match, rendezvous and ready edges unchanged;
- order edges (conditions 2 and 3', `matching.py:195-208`, `trace.py:300-356`): from
  `earlier_competitors` and `earlier_sends`, restricted to HB-earlier operations. "The latest
  earlier member of each envelope class" becomes the maximal HB-earlier members of the class, one
  per chain at most;
- the **orientation edges** of 3.2;
- a **probe edge** `complete(p) -> m(r', s)` for the receive r' that takes the probed message s
  under M', when r' is on another chain than p: the message was unmatched when the probe saw it.
  On one chain this order is implied, so the edge is drawn only across chains;
- collectives: a process's collectives on a communicator must be HB-ordered; the k-th is the k-th
  in that order. Concurrent ones are an observed error (3.7) and the trace is degraded.

Horizon (`horizon.py:79`), rules per chain instead of per rank:

1. A changed receive cuts *its chain* after its completion; the cut removes the chain's
   continuation, including the synchronisation points after that record (a release there is not
   in H). The read bound (H1') applies per chain unchanged: `firstread` follows only local
   objects whose address never escapes, so another thread cannot read them, and the
   synchronisation calls between the completion and the bound are issued whatever the result.
2. and 3. unchanged.
4. A collective member is the chain that issues it.
5. unchanged.
6. A pending receive deprives the later receives *in HB*, not in record order: on its own chain
   after it, and on every other chain of the process from the first record whose clock has
   passed it. With vector clocks per chain this is one bisection per chain (`horizon.py:170`
   today bisects one rank's list). A concurrent receive is not deprived: it competes.
7. unchanged.
8. **New: intra-process order.** A record is removed when a sync predecessor is not reached:
   the predecessor's chain is cut before the sync point (by rule 1, or at a record before it).
   This is the sync-preserving ideal: an acquire stays only if the previous critical section on
   the lock is complete in H, a join only if the joined chain's end is in H, a barrier departure
   only if every member arrived. Monotone and deflationary like the others, so the worklist and
   the greatest fixpoint are as before (`docs/model.md` §2, lemma on the worklist).

**Buffering profile.** Unchanged in form: breakpoints, B0, bisection. A blocking send blocks its
chain only (P-T3), so a process whose threads exchange messages with themselves, or a send in one
thread against a receive in another, has no head-to-head deadlock at B = 0 (the §12.6.1
example), where today's per-rank order would report one.

**Fast checks.** `stretto.fast` (`fast.py:111`, `:331`) indexes per rank and assumes a total
order; v1 routes every trace with a multi-chain rank to the generic check
(`report._profiles_generic`, `report.py:1118`). O(n + m) per alternative and threshold, as T4
(ii). Extending the fast checks to chains (clocks per chain, suffix queries per chain) is later
work.

### 3.5 Soundness (T1, T2) under the new model

**Programs consistent with a trace (threads).** A program gives each chain a function from the
results its calls have returned so far, and the values it has read from shared memory, to its
next call (MPI or synchronisation). P is consistent with σ on H if each chain's function, applied
to σ's results on H and to the values σ's schedule would have it read, yields its records. Under
P-T1 and P-T2 the values a chain reads in a sync-preserving schedule that contains its prefix are
those of σ, so consistency is again a condition on recorded results.

**T1-T (soundness of the L1 check).** Premises: those of T1 (`docs/model.md` §2b) and P-T1 to
P-T3 (P-T4 for the observed matching that M' is built from). If `feasible.check` accepts M' at B
with horizon H, then for every program consistent with σ on H there is an execution at B in which
every operation of H is issued, every recorded synchronisation object is used in σ's order, and
the matching agrees with M' on the judged receives. *Sketch.* Take a topological order of the
graph, sync and orientation edges included, and schedule the chains by it (P-T3). Each chain
issues its records: its results on H are σ's except at the cuts, and its reads see σ's writes,
since every write it reads precedes it in HB, lies in H by rule 8, and is the same write by P-T1
and P-T2. Postings and issues reach MPI in the topological order, and conditions 1-3' restricted
to HB plus the orientation edges make FIFO on that order give M' (3.2). Rule 6 over HB removes
every receive a pending one would deprive. The rest is T1's argument per chain.

**T2-T (monotonicity in B).** Statement unchanged; "the changed receive's rank" becomes its
chain, and a single change now also covers a concurrent swap: both cuts come from rule 1 and do
not depend on B, and a receive kept only at the larger threshold is not HB-after either changed
receive (rule 8 would remove it), so its obligations involve operations on which M' and M₀ agree.
Rule 8 does not mention B. To be tested before it is claimed (the E2 differential on the thread
idioms and random multi-chain traces).

**What stays exactly as is.** On a trace in which every rank has one chain: the chain ids are the
ranks, every new edge and rule fires only between two chains of one process (rule 8, orientation
edges, the cross-chain probe edge, rule 6's other chains), HB-earlier is record order, the kernels
are single sends, no concurrent swap exists, the fast checks run, and pins keep today's format.
Reports must be byte-identical: the goldens of `analysis/tests/golden` and a re-analysis of the
committed corpus traces are the regression gate of every step. A MULTIPLE run with one calling
thread is traced with the issue lock and the extra fields, which the loader ignores for a single
chain; its report equals that of the same calls traced without them (the run itself is perturbed
by the lock, 7.3).

### 3.6 Completeness (T6) and contained deadlocks (T7)

T6 below is the completeness theorem of the paper (`thm:complete`, `paper/sections/prediction.tex`,
which `docs/gate.md` calls T6): under C1, a surviving M' is feasible iff a realisation over H
exists. With chains it holds relative to sync-preserving realisations whose physical order agrees
with the orientation picks of 3.2, under C1 and a new C2 (every rank's collectives on a
communicator are HB-ordered). It does not hold in general: a realisation may need the other pick
for some pair. How often that happens is a measurement (E6's checker-versus-explorer comparison
on random multi-chain programs), not a proof. An exact check would branch on the picks of the
pairs a changed receive involves; the number of such pairs is small in practice (one per
concurrent competitor), so a bounded search is possible later.

T7-T (deadlock at L1). D is a set of chains. A chain of D stops at an MPI operation nothing in H
completes, as today, or at a synchronisation that only a chain of D, or a chain that terminates
in H, could release: a join of a chain of D, an acquire of a lock held by a chain of D (blocked
inside its critical section), a barrier with a member in D. The closure condition is unchanged
with "rank" read as "chain". A process is reported hung when one of its chains is in D and the
main chain reaches a join of it or `MPI_Finalize` (which §12.6.2 requires to come after every
chain's MPI calls). As today, every cut descends from rule 1, so a contained deadlock needs a
changed receive whose chain stops before the receive completes, or, new with threads, a chain
that blocks because another chain's changed receive took its message: the probe split's hang is
of this kind.

### 3.7 New observed findings (no alternative needed)

Read from HB and the records of σ, reported whatever the run's outcome:

- `2-thread-collective`: two collectives of a process on one communicator that are concurrent
  (§12.6.2, §7.14);
- `2-thread-wait`: two concurrent waits or tests whose request arrays share a request (§12.6.2);
- `2-thread-finalize`: `MPI_Finalize` on a chain other than the initialising one, or not
  HB-after every MPI record of the process (§12.6.2);
- `2-thread-level`: a call from a non-main chain under FUNNELED or SINGLE; two overlapping calls
  under SERIALIZED (§12.2.1).

## 4. Replay

**Naming a call.** A pin names `(rank, chain name, per-chain ordinal)` of the call's kind: `ridx`
for receives (all receives, not only wildcards, since specific receives race too), `pidx` for
probes that found a message, `sidx` for sends, the flip key per chain. For a rank with one chain
the chain is omitted and the counters are today's (`replay/pin.h`, `widx` counts wildcard
receives per rank), so existing pins and their results stay valid. The replay library computes
the same chain names as the tracer: it wraps `pthread_create` and `GOMP_parallel` and keeps the
counters in thread-local storage, under the issue lock as in the tracer.

**Forcing a match in a multithreaded process.** Source substitution (`pin.c:234-261`) forces a
wildcard receive onto a source; it cannot decide which of two threads' receives takes a message
from that source. The library adds *order gates*: `STRETTO_PIN_ORDER="rank:A#r#k<B#r#j,..."`
makes chain B's j-th receive wait, before it is posted, until chain A's k-th receive has been
posted (or a send issued, `#s#`, or a probe returned, `#p#`). With blocking calls rewritten as in
the tracer, "posted" is observable. Gates realise the physical order of 3.2: the alternative's
topological order for every pair of concurrent operations whose order decides a match in H
(concurrent receives with overlapping envelopes, concurrent sends of one channel, the probe and
the stealing receive and the probing chain's receive of a probe split). Other pairs are left
free. Gates follow the graph, which contains the recorded sync edges, so they cannot deadlock the
program unless synchronisation is missing from the trace; a gate that waits longer than its
timeout ends the run as inconclusive, naming the gate.

**Evidence.** "matched" lines gain the chain and the message's count and hash: `[stretto-pin]
rank R chain C: matched ridx=K src=S tag=T count=N hash=H`. A confirmation needs every pin
matched with the predicted hash where the alternative separates messages by contents.

**Control and validity.** The control replay gates the same pairs in σ's physical order (`iseq`)
and pins σ's sources: it must finish and match, as today (`confirm.py`, `control_pin`). A replay
or control is valid if every gated and pinned call is reached by the named chain at its ordinal
with the recorded identity (communicator, tag, call site; otherwise misapplied, as today). The
interleavings that no gate fixes still vary from run to run, and so can the consequence. Each
alternative is therefore replayed k times (k = 5 by default) with k controls; the report gives
the consequence as a count ("4 of 5 replays truncate, 5 of 5 controls finish"), as the README's
tables already do across runs. A consequence seen only in replays is attributed to the
alternative only if no control shows it.

**Flips, probe alternatives, L1+SP-T.** A probe alternative across chains is pinned by the probe
pin plus a gate (the probing chain's probe after the other chain's receive is posted); a
lock-order alternative (L1+SP-T) by a gate on the acquire (the library wraps the lock calls of
communicating critical sections, known from the `cs` records).

## 5. Idioms and tests

New idioms (`idioms/`, C, built with the system `mpicc` and `-pthread` or `-fopenmp`), each with
its expected report. "L1" means a finding the L1 check certifies; "replay" means a consequence
only a replay states.

| idiom | program | expected |
|---|---|---|
| 15 two receivers, one tag (pthreads) | rank 0: two threads, each `Recv(1, 0)`, then each checks a header; rank 1 sends two messages with different headers | L1 concurrent swap; consequence wrong result, by replay |
| 16a probe then receive, a thread steals (pthreads) | thread A: `Probe(ANY, 0)`, `malloc(count)`, `Recv(status.src, 0)`; thread B: `Recv(ANY, 0)`; rank 1 sends 8 B then 400 B | L1 probe split; A's receive takes the 400-B message into its 8-B buffer: 1a truncate |
| 16b as 16a, one message per source | | L1 probe split; A's receive blocks: 1d contained deadlock, process hangs at the join |
| 17 both threads probe (OpenMP, CorrBench `deadlock_probe_2.c` shape) | two threads `Probe(0, ANY)`, `Recv(0, status.tag)` | L1 probe alternative across chains (warning); hang by replay |
| 18 Mprobe version of 16 and 17 (negative) | `Mprobe`/`Mrecv` | no truncation, no hang; at most a concurrent swap of the Mprobes, without consequence |
| 19 two sender threads (pthreads) | rank 0: two threads each `Send(1, 0)` with different data; rank 1 receives twice from 0 and checks the order | L1 alternative on the sender side (the second thread's message first); wrong result by replay |
| 20 senders ordered by a condition variable (negative) | thread B sends only after thread A signals that its send returned | nothing: the sends are HB-ordered |
| 21 senders in critical sections of one mutex | as 19, each send inside `lock; Send; unlock` | L1: nothing (lock order preserved); L1+SP-T: the race of 19, confirmed by a replay that gates the acquire. A mutex serialises the sends but does not order them, so this is not a negative |
| 22 probe and receive in one critical section (negative for the split) | 16a with `lock; Probe; Recv; unlock` in every receiving thread | no probe split at any level; at L1+SP-T, which thread gets which message, without consequence |
| 23 one communicator per thread (negative) | 15 with `MPI_Comm_dup` per thread | nothing (the standard's advice, §12.6.1) |
| 24 concurrent collectives (OpenMP sections) | two sections each `MPI_Bcast` on `MPI_COMM_WORLD` | observed `2-thread-collective`, whether or not the run went wrong |
| 25 two threads wait on one request | | observed `2-thread-wait` |
| 26 lock held across a receive (CorrBench `deadlock_probe.c` shape) | A: `lock; Probe(0, 2); Recv; unlock`; B: `lock; Send(0, 1); unlock`; rank 0 answers tag 1 with tag 2 | L1: nothing; L1+SP-T: deadlock if A takes the lock first; replay confirms with a gate on the acquire |
| 27 head-to-head across threads (negative for 2-buffering) | thread A sends 1 MB to rank 1, thread B receives from rank 1; rank 1 the same | no 2-buffering at B = 0 (idiom 03's single-threaded form deadlocks below 400 B) |
| 28 finalize while a thread communicates | | observed `2-thread-finalize` |
| 29 FUNNELED, a worker thread calls MPI | | observed `2-thread-level` |
| 30 MULTIPLE, one calling thread | idiom 01 with `MPI_Init_thread(MULTIPLE)` | report equal to idiom 01's |

Tests:

- **Byte-identical gate**: every golden of `analysis/tests/golden` and the committed corpus traces
  re-analysed after each step, diffed against `main`'s reports.
- **Loader**: chains, `after` to edges, `iseq` reconstruction, degrade on a hash mismatch, version
  2 multi-thread traces still declined.
- **Checker against explorer** (`CheckerSoundness`, E6): the explorer gains chains, locks,
  barriers, create and join (it is also the engine of L1+SP-T); DSL twins of idioms 15-30 in
  `idioms_dsl.py`; random multi-chain programs. Measures: unsound pairs (must be 0), incomplete
  pairs (the cost of the orientation picks).
- **T2-T**: full profile against bisection on the thread idioms and random runs.
- **Replay end to end** (`test_replay_e2e.py`): idioms 15, 16a, 19 and 26 confirmed, each with its
  control.

## 6. Benchmarks with threads

Checked on 29 Oct 2026 by searching the sources for `MPI_Init_thread`, `MPI_THREAD_`, `pthread`
and `#pragma omp`:

- **MBI** (`study/apps/MBI`, commit c065725): 0 of 1 871 generated programs (`gencodes/*.c`), and
  no generator (`scripts/generators`, `deprecated`, `workInProgress`) uses threads.
- **MPI-BugBench** (`third_party/mpi-bugbench`, commit 0d53002): 0 of 2 067 generated programs
  (`scripts/gencodes`). Its template can emit `MPI_Init_thread` with a level
  (`scripts/Infrastructure/Template.py:67-70`), but no generator under `scripts/errors` sets one.
- **MPI-CorrBench** (`study/apps/MPI-CorrBench`, commit a05644b): 103 OpenMP programs in
  `micro-benches/0-level/openmp` (of 616 C files in the suite), none with pthreads; 59 with an
  error, 44 correct; 22 use OpenMP tasks.

  | directory | with an error | correct | in scope |
  |---|---|---|---|
  | `ordering` | 23 | 15 | 8 message-race or deadlock programs: `deadlock.c`, `deadlock_2.c`, `deadlock_send_recv.c` (deadlock in every execution: observed), `dependant/probe.c`, `dependant/deadlock_probe_2.c` (probe alternative across threads: L1 warning, consequence by replay), `dependant/deadlock_probe.c` (lock-order deadlock: L1+SP-T), `dependant/deadlock_task_send_recv.c` and `dependant/nondeterministic_deadlock.c` (tasks: v2). The three correct `dependant/{probe,deadlock_probe,deadlock_probe_2}.c` use `MPI_Mprobe`: negatives. `two_collectives{,_2..._9}.c` (9 with error, 7 correct): `2-thread-collective`. `request_reuse.c`: `2-thread-wait`. `comm_free`, `task_comm_free`, `derived_datatype`, `info_free` (handle freed while another thread uses it): outside the model. `send_recv_same_buffer.c`: a buffer race (family 3) |
  | `threading` | 16 | 11 | thread-level misuse (`missing_init_thread*`, `wrong_threading_level*`, `missing_threading_level_check`, `finalize_missuse*`): `2-thread-level` and `2-thread-finalize`, observed |
  | `data_race` | 15 | 13 | buffer races between threads: family 3, and the guard is off under MULTIPLE; out of scope for v1 |
  | `memory` | 5 | 5 | lifetime of thread-private buffers: family 3; out of scope |

So the benchmark evidence for the predictive part is small: about 5 CorrBench programs with a
thread message race whose run may finish (`probe.c`, `deadlock_probe.c`, `deadlock_probe_2.c`,
and the two task programs in v2), plus the idioms. A guess, not checked: MPICH's test suite has a
`test/mpi/threads` directory of multithreaded point-to-point tests, a possible source of correct
programs for false-positive counts. Real applications that run MULTIPLE with shared
communicators are needed for the evaluation (section 8).

## 7. Work plan

### 7.1 Steps and effort (working days, one person)

| step | content | days |
|---|---|---|
| 1 tracer | chain ids and names; pthreads wrappers with vector clocks and `after`; `cs` records; skip MPI-internal sync; under MULTIPLE the issue lock, blocking rewrite, `iseq`, hashes; libgomp `GOMP_*` wrappers (parallel, barriers, critical, locks, ordered, work-shares); tasks detected and degraded; format version 3; overhead on a threaded microbenchmark | 7-8 |
| 2 loader | chains and `proc`; `after` to edges and clocks; reconstruction by `iseq` with count and hash checks; collective ordinals by HB; degrade reasons (`threads-tasks`, `reconstruction`, version 2 multi-thread traces) | 3-4 |
| 3 analysis | (a) chain/process split through trace, matching, horizon, feasible, deadlock, report, single-chain output unchanged, 4; (b) HB-restricted order edges, orientation picks, cross-chain probe edge, rule 6 over HB, rule 8, 3; (c) enumeration: kernels as antichains, non-wildcard candidates, concurrent swaps, probe split, pruning on guaranteed edges, caps, 2; (d) observed thread findings, 1; (e) generic-check routing, naming in the report, 1 | 11 |
| 4 explorer | chains, locks, barriers, create and join in the explorer and the DSL; L1+SP-T on top of it | 5-6 |
| 5 replay | chain naming and per-chain counters in the pin library; order gates; hash in matched lines; horizon pins and controls with gates in `confirm.py`; k replays per alternative | 5 |
| 6 tests | idioms 15-30 and their twins; goldens gate; loader tests; E6 and E2 on random multi-chain programs; replay end to end | 5 |
| 7 CorrBench | harness for the 103 OpenMP programs (build with `-fopenmp`, run at MULTIPLE, score) | 2-3 |
| 8 docs | `docs/model.md` (chains, rule 8, T1-T, T2-T, T6, T7), this document updated with what was built, README, RUNBOOK | 2 |

Total about 40-44 days. Milestones, each usable alone:

- **A** (steps 1-3, 6 without replay; about 26 days): L1 predictions and observed thread
  findings for pthreads and OpenMP without tasks, checked against the explorer.
- **B** (step 5, 5 days): confirmations by replay.
- **C** (step 4's L1+SP-T, about 3 days beyond the explorer work): lock reordering.
- **D** (later, about 6 days, not in the total): OMPT with libomp in a new image, tasks, fast
  checks for chains.

### 7.2 Order

Loader and analysis can start on hand-written multi-chain traces (in-memory, as
`test_flips.py` does) while the tracer is built; the explorer's chain support is needed before
the checker can be trusted (the E6 comparison), so it moves before step 3 (c).

### 7.3 Risks

- **Perturbation.** The issue lock serialises the posting of point-to-point operations and the
  blocking rewrite changes which call blocks where. Traced runs show fewer interleavings (the
  prediction covers the others) and run slower; a program whose threads rely on a blocking call
  inside MPI to wait (not on the request) behaves the same, since `PMPI_Wait` blocks the thread
  as the blocking call did. To be measured, with `STRETTO_THREADS=0` as the off switch.
- **P-T4 on real implementations.** Matching in the order of the posting entry points holds for
  Open MPI's ob1 and, it seems, for UCX-based matching with one worker; MPICH with several VCIs,
  or an Open MPI build with several UCX workers, may match messages of one communicator out of
  that order. The count and hash checks detect it after the fact; the image's Open MPI 4.1.6 and
  MPICH 4.2.0 (ch4:ucx) both grant MULTIPLE (checked with `ompi_info` and `mpichversion` in the
  image).
- **Unrecorded synchronisation** (atomics, spin flags): unsound L1 claims (2.3). The replay
  catches them as inconclusive; the annotation API is the remedy; how common they are in MPI+X
  applications is unknown.
- **libgomp interposition.** The `GOMP_*` ABI is internal; wrapping `GOMP_parallel`'s outlined
  function, nested parallelism and thread reuse need care. OMPT is the clean route but needs
  another compiler and runtime in the image.
- **Enumeration size.** Thread pools receiving from one queue make many concurrent pairs; the cap
  per receive keeps the report bounded and loses completeness there.
- **Cost.** Without the fast checks, multi-chain traces are checked at O(n + m) per alternative
  and threshold; a trace of 10⁵ records with 10⁴ alternatives is minutes, not seconds.
- **Inconclusive replays.** Gates add timeouts and the unforced interleavings vary; the rate of
  inconclusive replays may be high.
- **Benchmarks.** Neither MBI nor MPI-BugBench has a threaded program, and CorrBench's are
  micro-programs; an evaluation needs applications.
- **Regression.** The chain/process split touches every module; the byte-identical gate after each
  step is the protection.

## 8. Open questions

1. L1 keeps lock order (sync-preserving), so lock-order races and SERIALIZED programs' thread
   races are predicted only at L1+SP-T, confirmed by replay. Is that the intended trade, or
   should lock-protected state be analysed statically (a first-read analysis for critical
   sections) to certify some reorderings at L1?
2. The issue lock and the blocking rewrite under MULTIPLE perturb the run in exchange for an exact
   observed matching. Acceptable, or should the reconstruction instead search for a consistent
   matching (counts, hashes, acyclicity) without the lock, and degrade when it is ambiguous?
3. OpenMP: wrap libgomp (gcc 13, the image as it is) in v1 and OMPT later, or add clang and
   libomp to an image and use OMPT only?
4. MPI calls in explicit OpenMP tasks: v1 degrades them. Two CorrBench programs need them.
5. Are the observed thread findings (3.7) in scope for the paper, or only the predictions?
6. Which applications use MULTIPLE with shared communicators and could be traced?
7. The orientation picks (3.2) make the check incomplete; is a bounded search over the picks of
   the changed receives worth its cost, or is a measured incompleteness enough?
8. `docs/model.md` cites MPI 4.0 section numbers as MPI 4.1's (1); fix them in a separate commit?
