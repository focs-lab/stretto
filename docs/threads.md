# Several threads calling MPI: design

Status: designed 29 Oct 2026; milestone A (tracer, loader, analysis at L1, the observed thread
classes, tests) implemented the same day, section 9 says how it differs from the design; section
10 says how milestone B made the check scale and what it found in HDF5's Subfiling VFD; section 11
says how milestone C replays alternatives of several threads, section 12 how milestone D follows
OpenMP tasks (both 30 Oct 2026), section 13 how level L1+L reorders critical sections, section 14
how milestone E replays pools of threads and tasks in the trace's lock order, section 15 how
milestone F analyses requests handed between threads and cancelled receives (30 Oct 2026).
Decided
29 Oct 2026: Stretto is extended to programs in which several threads of an MPI process call MPI
on shared communicators (`MPI_THREAD_MULTIPLE`, and `MPI_THREAD_SERIALIZED` with several calling
threads). Before, such a trace was analysed for observed findings only (`docs/model.md` §6). This
document says what the standard allows, what the tracer must record, how the model, the analysis
and the replay change, which programs test it, which benchmarks contain such programs, and what
the work costs. File and line references in sections 1-8 are to the code before milestone A.

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
| | `GOMP_task`, `GOMP_taskwait`, `GOMP_taskgroup_*`, dependences | not in v1: an MPI call inside an explicit task degrades the trace (`threads-tasks`); since milestone D (section 12) each task is a chain, ordered by its creation, dependences, taskwait, taskgroup and team barriers |

Synchronisation inside an MPI call (the library's own mutexes, which Open MPI may take through
`pthread_mutex_lock`) and from the library's progress threads must be ignored: the wrapper skips
calls made while the calling thread is inside an MPI wrapper, and calls whose return address lies
in the MPI library's objects (a `dladdr` per call site, cached). Otherwise the library's locking
would order user threads.

OpenMP through OMPT (LLVM's libomp, which gcc's libgomp does not implement) is the portable route
and the one that covers tasks, as the Archer tool does for data races; it needs clang and libomp
in the image (the experiment image has neither; the first-read image has clang 18 but no libomp).
Planned for v2; not built (section 12: libgomp has no OMPT, and tasks are followed through its
entry points).

### 2.3 What the first version assumes

| premise | what it says | if it fails |
|---|---|---|
| P-T1 data-race freedom w.r.t. recorded sync | every shared-memory value that decides a chain's calls is read after the write it reads, in HB | the analysis assumes a chain issues its recorded calls in an order where it would read another value: over-prediction, and an L1 claim about calls the program does not make (unsound) |
| P-T2 complete sync recording | every synchronisation that orders such accesses is one of the table above | as P-T1 |
| P-T3 arbitrary scheduling and per-thread progress | any thread may be delayed arbitrarily; a blocked thread does not stop others (§12.6.1, requirement 2) | none in practice: the standard requires the second part of any thread-compliant implementation |
| P-T4 physical matching | the implementation matches in the order in which its posting entry points ran, serialised by the issue lock | the observed matching is wrong; checked against counts and hashes, and the trace degraded (`reconstruction`) when they disagree |
| P-T5 deterministic chain names | each chain creates threads and enters parallel regions in the same order in every run | replays name the wrong chain and end as misapplied (inconclusive); no effect on the analysis |
| P-T6 no MPI in explicit tasks (v1; since milestone D, none in tasks the tracer does not follow, section 12) | | the trace is degraded |

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
  of section 6, all but `deadlock_probe.c`), it loses nothing. Since 30 Oct 2026 level L1+L
  (section 13) predicts these where both critical sections contain an MPI call.
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

## 8. Decisions (29 Oct 2026)

1. L1 keeps lock order (sync-preserving); lock reordering comes at L1+SP-T, a later milestone. No
   static analysis of critical sections now. (Since 30 Oct 2026 level L1+L, section 13, reorders
   critical sections that contain MPI calls, as an extension of L1 under premise P-T8.)
2. The issue lock and the blocking rewrite under MULTIPLE, as designed, switchable by
   `STRETTO_THREADS=0`, with their perturbation measured.
3. OpenMP through libgomp's entry points in v1; no clang or libomp image now.
4. MPI calls in explicit OpenMP tasks: degraded in v1 (tasks are chains since milestone D, section 12).
5. The observed thread classes (3.7) are in scope, reported as observed findings.
6. Targets: MPICH's `test/mpi/threads` (commit fb003aa; the `mt_*` tests run a racy phase and
   two controls) and MPI-CorrBench's OpenMP ordering pairs (Probe against Mprobe) as programs
   with known answers; then HDF5's Subfiling VFD at the parent of 27bb358, a fixed bug; Uintah
   with hypre later.
7. The incompleteness of the orientation picks (3.2) is accepted in v1 and documented (3.6).
8. `docs/model.md`'s MPI 4.0 section numbers are corrected in a commit of their own.

## 9. Milestone A as built (29 Oct 2026)

**Tracer** (`tracer/mpitrace.c`, trace format 3). As in 2.2, with these differences: no message
hashes and no `cs` records yet; the `chain` record's `parent` is the nearest ancestor that has
an id; work-share read-modify-writes are recorded after the libgomp call returns (holding a lock
across it could deadlock), which adds only edges consistent with the record order; not wrapped:
libgomp's legacy `GOMP_parallel_start`/`_end`, the `_ull_` loop forms, `pthread_tryjoin_np` and
`pthread_timedjoin_np`. Synchronisation calls whose return address lies in the MPI library, the
OpenMP runtime, libc or the tracer are ignored. `omp_in_explicit_task()` (present in the image's
libgomp) detects MPI calls inside tasks. Failed polls fold per chain.

**Loader** (`analysis/stretto/chains.py`, `trace.py`). A rank with records of several chains is
split; a `begin` record's `after` moves to the record it folds into; the observed matching of a
split rank is reconstructed in `iseq` order (in file order below MULTIPLE), and an `Mrecv` takes
the message of its own thread's `Mprobe`; a pair whose sizes disagree degrades the trace
(`reconstruction`). A request completed by another thread than the one that started it degraded
the trace (`threads-handoff`) until milestone F modelled it (section 15); so does an MPI call
inside an OpenMP task (`threads-tasks`).

**Analysis** (`analysis/stretto/threads.py`, with changes in `matching.py`, `horizon.py`,
`feasible.py`, `report.py`). As in section 3, with these choices:

- Orientation picks (3.2). Two receives of one process on different chains, r taking s and x
  accepting s: when x's message is of s's channel, the postings follow the order of the two
  sends (x is matched first if its message was issued first), otherwise the physical order of
  the two postings in the run; sends keep the order in which MPI saw them unless happens-before
  orders their receives otherwise. Probes are oriented against concurrent receives and sends in
  the same way. The check is incomplete where another pick would accept an alternative.
- Candidates: steals (the concurrent loser left pending in H) and concurrent swaps; a swap's
  second receive is classified too, so a truncation on it is a 1a finding. A steal that
  re-matches the loser to its next message (a two-change alternative) is not enumerated.
- Rule 8 and the synchronisation points between two records of a chain. A chain that waits at
  an acquire inside such a gap reaches the releases of the gap made before that acquire, and no
  later ones. Each `after` entry carries the ordinals of the release and of the acquire among
  their threads' synchronisation calls (`[chain, seq, kc, ka]`), which orders them. For an
  entry without ordinals, a release counts as reached only if the record it precedes also
  precedes every point the chain waits for: had the chain waited first, the run would have
  needed that point before itself. A chain cut by rule 1 reaches nothing after its cut.
  Contained deadlocks (T7) are claimed only for chains that stop at an MPI operation, never
  through a wait for a synchronisation.
- Not for several chains yet: the fast checks (the generic check judges everything), the
  single-path explorer (L1+SP), read bounds, flag flips, the comparison with happens-before
  (`hb.declines`), replay pins.
- New observed classes, error classes in the scorer too: `2-thread-collective`,
  `2-thread-wait`, `2-thread-finalize`, `2-thread-level`. With `threads-tasks`, only the
  thread-level check runs, and the cross-rank collective-ordering check is skipped (the order
  of a rank's collectives is not known).

**Idioms** (numbered 15-31; the table of section 5 maps as 15→15, 16a→16, 16b→17, 17→18,
18→19, 19→20, 20→21, 21→22, 22→23, 23→24, 24→25, 25→26, 26→27, 27→28, 28→29, 29→30, 30→31), with
their traces under `analysis/tests/golden/{openmpi,mpich}`.

**Results (29 Oct 2026, a-p13 in the experiment image, Open MPI 4.1.6 unless said).**

- *Byte-identical gate.* The 531 traces with one chain per rank (503 corpus traces of the last
  MBI and MPI-BugBench run, 14 idiom traces, 14 golden traces), analysed by main (2f82750) and by
  this branch as a text report, with `--json` and with `--full-profile --json`: 3 186 outputs and
  exit statuses, 0 differences. The unit tests: 483 run, 18 skipped (as on main), all pass.
- *Idioms 15-31* (traces of Open MPI and MPICH): as expected in section 5, at L1. Two
  receivers (15): a concurrent swap; probe split (16): a 1a truncation on the swapped probing
  receive; probe split with one message (17): a contained deadlock (1d) of the probing thread;
  both threads probing (18): a probe alternative across threads (its hang is for the replay);
  Mprobe (19): probe alternatives without consequence; two sender threads (20): the sender-side
  alternative; ordered by a condition variable (21), by a mutex (22, L1 keeps lock order),
  probe and receive in one critical section (23), a communicator per thread (24): nothing;
  concurrent collectives (25): `2-thread-collective` (Open MPI; in the MPICH run one thread ran
  both sections, so the run has no concurrency, and the section assignment is kept); double wait
  (26): `2-thread-wait` (degraded `threads-handoff`, the request is completed by another thread);
  lock held across a receive (27): nothing at L1; head-to-head between threads (28): no
  2-buffering; early finalize (29), FUNNELED violation (30): their observed classes; MULTIPLE
  with one thread (31): analysed as a single chain.
- *MPICH `test/mpi/threads/pt2pt`* (fb003aa, built against Open MPI; 2 ranks of 4 threads, 16
  iterations): every finding of the seven `mt_*` tests lies in the racy phase (communicator 0,
  tag 0, the phase's threads), none in the two control phases: `mt_sendrecv` 135 L1 1f,
  `mt_ssendrecv` 143, `mt_isendirecv` 3, `mt_probe_sendrecv` 4 and one probe alternative,
  `mt_iprobe_sendrecv` 4, `mt_mprobe_sendrecv` and `mt_improbe_sendrecv` 63 probe alternatives
  each (which thread's Mprobe gets which message). 2 to 11 s per trace. Untraced,
  `mt_probe_sendrecv` and `mt_iprobe_sendrecv` fail in their racy phase (MPI_ERR_COUNT, 3 of 3
  runs); traced they pass, so the issue lock hides it, and the L1 report names the race (1f, all
  messages have one size) but not that consequence. The 1 024-iteration traces (25 000 to
  170 000 records) are beyond milestone A: `mt_isendirecv` (24 702 records) did not finish in
  50 minutes with the generic check, which judges every alternative over the whole trace.
- *MPI-CorrBench OpenMP ordering* (a05644b, 2 ranks, 2 threads): of the 23 programs with an
  error, found: `deadlock`, `deadlock_2`, `deadlock_send_recv`, `dependant/deadlock_probe_2`
  (observed hangs; the last also as L1 1d), `two_collectives`, `_2` (`2-thread-collective`),
  `request_reuse` (observed 1c, degraded `threads-handoff`), `dependant/probe` (L1 1f only, a
  warning: the run had the race, and with messages of 8 and 4 bytes no truncation follows).
  Missed by design: `dependant/deadlock_probe` (lock order, L1+SP-T), `two_collectives_9`
  (collectives ordered by a critical section whose order differs between ranks: lock order),
  `two_collectives_3`-`_8`, `deadlock_task_send_recv`, `nondeterministic_deadlock`,
  `task_comm_free` (MPI in tasks: degraded), and the handle lifetime and buffer programs
  (outside the model). Of the 15 correct ones, none has an error class; the Mprobe versions of
  `probe` and `deadlock_probe_2` have two probe alternatives each (warnings).
- *Overhead* (median of 3, wall time with mpirun start, `-iter=1024`; untraced / traced with
  `STRETTO_THREADS=0` / traced with threads): `mt_sendrecv` 0.64 / 0.57 / 0.57 s,
  `mt_isendirecv` 0.54 / 0.60 / 0.60 s, `mt_mprobe_sendrecv` 0.70 / 0.80 / 0.92 s;
  `osu_latency_mt` small messages 3.3 / 16.2 / 16.6 µs. Writing the trace dominates; the issue
  lock, the rewrite and the clocks add 0-15 % on top of it.

## 10. Milestone B as built (29 Oct 2026): scale, and a known bug

Not the replay of the plan's milestone B (7.1): no pins for several threads yet.

**Why the 1 024-iteration `mt_*` traces did not finish.** Not the horizon: on `mt_isendirecv`
(24 702 records) the enumeration alone ran over 300 s (a search of the observed graph per
candidate, and each sending chain's queue walked from its start per receive), and each of the
about 32 700 alternatives (threads at 1 024 iterations, against 435 at 16) was judged at every
threshold with a graph of 2 * 10^7 orientation edges (60-73 s, 4 GB): the racy phase's four
receiving threads are concurrent throughout, so every pair had an orientation edge against
every receive and send of the phase. The horizon kept a third of the trace.

**What changed** (`threads.py`, `report.py`):

- Enumeration: vector clocks over the chains for the observed happens-before
  (`observed_reaches`), and per receiving chain and queue a pointer past the sends taken before
  (`_Heads`). The same candidates on all 84 multithreaded traces.
- Orientation edges up to reachability (`_Reduced`): per pair and other chain, the earliest
  operation that goes after (program order reaches the rest), and the last of each run of
  operations matched before, whose match nodes the competitor and sender-side order edges link
  anyway. The window is cut by bisection where a chain's receives of one source take their
  messages in physical order and its sends are issued in order (always, under the issue lock),
  with the alternative's re-matched receives judged one by one, and walked elsewhere. The full
  construction stays (`FULL_ORIENTATION`) and the tests compare the three on every alternative,
  probe alternative and threshold of the multithreaded golden traces and random thread pools.
  The equivalence assumes that physical order agrees with happens-before for the sends of a
  process (it does under the issue lock; where the bisection meets a pair that contradicts it,
  it walks).
- Order filter (`OrderFilter`): obligations of the changed receives that hold whatever the
  horizon (rule 6 only takes messages away): an earlier message of the channel that no receive
  before it takes; a receive before it without a message; an earlier receive of its class
  taking a later message of the channel; and the previous receive of its class on its thread
  taking a message that a concurrent thread issued after the new one, whose two sender-side
  orientation edges form a cycle of two issue nodes (or, without that message in the horizon,
  that receive is left pending before the changed one). The generic check rejects each such
  alternative at every threshold, so the report rejects it with that reason. The same for probe
  alternatives (none rejected on these traces).

**Gate.** The 531 single-chain traces of milestone A and idiom 32 (Open MPI, 3 ranks), against
main (68b00af) as text, `--json` and `--full-profile --json`: 1 596 outputs and exit statuses, 0
differences. The 84 multithreaded traces (idioms 15-31 of both MPIs, 38 CorrBench, the 16-iteration
`mt_*`): findings, notes, status and exit statuses unchanged; in the JSON of five `mt_*` traces
the reason of 22 to 310 rejected alternatives names the filter's obligation instead of the first
one the generic check meets. Unit tests: 512 run, 35 skipped, all pass (main: 509, the same tree).

**Times** (a-p13, one text analysis, wall clock; at 1 024 iterations main's numbers are those
above, the analyses did not finish): `mt_isendirecv` 286.5 s (5 L1 1f), `mt_sendrecv` 158.7 s
(39), `mt_ssendrecv` 55.1 s (49), every finding on the racy phase's chains; 142, 41 and 49
alternatives per trace reach the generic check. At 16 iterations, main / branch: `mt_isendirecv`
10.3 / 1.1 s, `mt_sendrecv` 10.2 / 2.8, `mt_ssendrecv` 6.5 / 3.4, `mt_probe` 4.3 / 2.7, `mt_iprobe`
5.2 / 3.3, `mt_mprobe` 1.7 / 1.9, `mt_improbe` 2.2 / 2.4. **Not finished**: the four probe tests at
1 024 iterations (99 633 to 172 127 records) have about 16 400 probe alternatives each, judged by
the generic check at 45 s each (probe windows are walked, and most are rejected by a cycle that
the probing chain's cut creates: its later receives lose their match nodes, and the sender-side
orientation then orders two sending threads both ways, which no horizon-free rule decides).

**HDF5 Subfiling VFD** (`experiments/subfiling`). The bug of PR #1883's message ("multiple write
requests from a given rank being read by the wrong worker thread") is fixed by ffc254983 (17 Nov
2021) on the subfiling branch that 27bb358 squashed into develop; develop's parent of 27bb358
has no Subfiling VFD, so the version with the bug is ffc254983's parent 9975eaeb2. `t_subfiling_vfd`
on 4 ranks, Open MPI, 5 traced runs per version: every clean trace of 9975eaeb2 predicts at L1 a
concurrent swap or steal between two worker threads of the I/O concentrator receiving one
client's data messages (4-6 per trace, tag `WRITE_INDEP_DATA`; in an earlier run also two 1a
truncations, an 800-byte write into a 96-byte receive); ffc254983's traces have none. Untraced,
9975eaeb2 passed 20 of 20 runs. Not replayed (no pins for threads). Both versions also report probe
alternatives of the concentrator's `Iprobe(ANY_SOURCE, ANY_TAG)` (about 170 and 230 per trace,
without consequence) and an observed `2-thread-finalize` (not examined).

**Uintah + hypre** (read only, not built). `StandAlone/inputs/Examples/solvertest1.ups` drives the
hypre solver from the Examples component, and `sus -nthreads N` runs the Unified scheduler under
`MPI_THREAD_MULTIPLE`; the hazard comment is in `KokkosScheduler.cc`, which needs Kokkos as well.
Core, Schedulers, Solvers and Examples are about 255 `.cc` files (95 000 lines); with hypre built
from source and libxml2's headers added to the image, a build under 2 hours on 4 cores looks
feasible (a guess; nothing was built).

## 11. Milestone C as built (30 Oct 2026): replay pins for several threads

The replay of section 4, with these choices (`replay/pin.h`, `replay/pin.c`, the tracer's
wrappers, `analysis/stretto/confirm.py`).

- **Naming.** A chain pin (`STRETTO_PIN_CHAIN`) names a receive by the tracer's logical chain
  name and its ordinal among the chain's receives (all of them but those of `MPI_PROC_NULL` and
  `Mrecv`/`Imrecv`), a gate names a send or a probe that found a message the same way. Only
  the tracer names chains, so the replay library alone refuses both variables; the harness runs
  replays under the tracer anyway. In a trace with several chains every receive of the horizon
  gets a chain pin, a rank of one chain included (its chain is `0`); probes are pinned by their
  rank ordinal only where one chain of the process probes, and flips and probe alternatives of
  such traces are not replayed.
- **Order gates** (`STRETTO_PIN_ORDER="rank:A#k#n<B#k#n"`): B's operation waits, before it is
  posted and outside the issue lock, until A's is posted (a probe: has returned a message). Gated
  pairs: two concurrent operations of one process in the horizon that could take or report one
  message (receives and probes with overlapping envelopes, sends to one process on one
  communicator), each operation against the latest such operation of each other chain, once in
  a topological order of the alternative's constraint graph (ties broken by the run's file
  order) and once in the run's issue order. Only the pairs on which the two orders disagree are
  gated, in the first order in the replay and in the second in the control; the rest are left
  free. Gating every pair (the first version of 30 Oct) failed on HDF5: its pools of worker
  threads take other requests in each run, so a worker posts another number of operations and
  a gate between two pools' workers never opens. Operations beyond the horizon wait for the
  horizon's and nothing waits for them. A gate closed after 80 % of the run's timeout ends the
  run (exit 87): inconclusive.
- **Evidence.** A chain pin's token carries the message its receive must take (sender process,
  chain, send ordinal) and its happens-before (per other chain of its process, how many of that
  chain's operations precede it). It counts as matched only when the replay's own trace,
  reconstructed in issue order (P-T4), shows that message; the library's report line names the
  source, which two messages of one channel share, and no message hash is recorded yet. A
  replay or control whose happens-before differs from the trace's (`sync_divergence`), or whose
  control did not take its pinned messages, is inconclusive. A replay of several threads that
  hung or left receives pending is not judged as a run of the program (`unpinned_violation`
  says undetermined).
- **Not built:** the `k` replays per alternative inside `confirm` (`experiments/replay_sample.py
  --repeat` does it), message hashes, lock-order gates (L1+SP-T).

**Gate and tests.** The 531 single-chain traces and idiom 32, analysed by main (b1187c7) and this
branch as text, `--json` and `--full-profile --json`: 1 596 outputs, exit statuses and stderr
files, 0 differences (a-p13). Unit tests: 519 run, 18 skipped, all pass, the end-to-end replays
included (idioms traced by `scripts/run_all.sh`, Open MPI).

**Thread idioms** (Open MPI, two replays and two controls each, `test_replay_e2e.Threads`): the
concurrent swap of idiom 15 (both threads print "wrong header"), the sender-side alternative of
20 ("wrong order: 2 before 1"), the probe split of 16 (`MPI_ERR_TRUNCATE`, `confirm`: confirmed)
and of 17 (hang, 1d confirmed); every required pin matched, every control finished without the
consequence and with its own pins matched, no gate timed out and no replay diverged.

**HDF5 Subfiling** (`experiments/subfiling`, `replay.sh`, `replay.csv`): 20 wrong-worker
alternatives of 9975eaeb2 (4 per trace of 5 new traces, all 1f), each replayed twice with its
control. 0 of 40 replays took their pinned messages; 38 replays and all 40 controls synchronised
their threads otherwise than the trace; outcomes of the replays: 12 finished, 13 crashed (SIGSEGV,
SIGABRT), 1 failed, 12 misapplied pins, 2 gate timeouts; of the controls 31 finished, 1 crashed;
no run read back wrong data. Nothing is confirmed. The I/O concentrator hands each request to
whichever worker of its pool wakes first (a mutex and a condition variable), so the workers'
receive counts differ between runs and a worker's k-th receive names another request; P-T5
holds for the chain names, not for the work a pool's chain does. MPI-level gates cannot force
the hand-out: that needs the synchronisation order replayed (lock gates of L1+SP-T, with
condition-variable waits whose number varies), or a receive named by the request it serves
(its happens-before position on the dispatching chain). The fix's 5 traces have no alternative
at a receive: nothing to replay.

**Uintah + hypre** (`experiments/uintah`): built in 6 minutes (hypre 2.33.0, since hypre's
master dropped SparseMSG; Uintah 493d2b5, Examples only, Unified scheduler, no Kokkos), driver
`Examples/solvertest1.ups` with 8 patches, `sus -nthreads 2` on 2 and 4 ranks. L1 predicts no
receive alternative (Uintah and hypre receive from named sources) and probe alternatives only
between hypre messages of different threads (tags 1000, 3000, 4000), none with a Uintah message;
they are not replayed. Every trace has an observed `2-thread-collective`: Uintah's reduction
(`OnDemandDataWarehouse::reduceMPI`) and hypre's `MPI_Allreduce` on two threads of a rank, on
`MPI_COMM_WORLD`, unordered. Untraced, runs with threads end in `MPI_ERR_TRUNCATE` inside
`MPI_Allreduce` (4 ranks: 4 of 20 passed with 2 threads, 0 of 20 with 4; 2 ranks, 2 threads: 14
of 20), the single-threaded MPI scheduler passes 20 of 20. That the failing call is that pair was
a hypothesis; confirmed 30 Oct (abort stacks, and hypre on a duplicated communicator passes 40
of 40 in every setting with both MPIs; [experiments/uintah](../experiments/uintah/README.md),
"Cause and fix").

**Defect found on the way.** The reduced orientation edges of milestone B kept the views of a
chain's receive classes and send tags in one cache with keys of one shape; on Uintah's trace a
collision ended the analysis (internal error). Fixed; on the 84 multithreaded traces of
milestone B text and JSON reports are unchanged.

## 12. Milestone D as built (30 Oct 2026): OpenMP tasks as chains

Planned in 7.1 as OMPT with libomp. Not possible in the images: libgomp (gcc 14.2's, in the
experiment image and the SLATE image) exports no OMPT symbol, and neither image has clang or
libomp. Tasks are followed through libgomp's entry points instead, as parallel regions are
(decision 3).

**Tracer** (`tracer/mpitrace.c`, section "OpenMP explicit tasks"; the trace format keeps version
3). Each explicit task is a chain of kind `task`, named `c~k` (the k-th task chain c created), so
an MPI call in a task is no longer degraded. `GOMP_task` hands libgomp a trampoline and a copy of
the task's data behind a pointer to the task's state (through the program's copy function when
it has one). Edges:

- creation: a release of the creator, which the task's start takes;
- dependences, kept per parent and address as gcc 14's `gomp_task_handle_depend` orders them: in
  and inoutset after the latest out, inout or mutexinoutset of the address and the other in-type
  since it, out-type after all of these, `omp_all_memory` after every earlier sibling with
  dependences and before every later one, `omp_depend_t` objects included; recorded also when the
  sibling has already completed (libgomp then does not wait, but the order is the program's);
- the task's end, a release taken by the parent's next `taskwait`, by the innermost `taskgroup`
  open at its creation, by the departures of the team barrier of its generation (a task belongs to
  its creator's generation) and the region's end, and by the creator's return from `GOMP_task`
  when libgomp ran the task inside the call (undeferred: `if(0)`, a final task's child, or
  libgomp's bound on queued tasks); `taskwait depend(...)` takes the ends its clauses name.

Not followed, written as a `task` record (`what`: `untracked` or `detach`) and degraded
`threads-tasks`: MPI calls in tasks of `taskloop` (libgomp creates them inside), and any MPI call
after a detached task was created (its end is `omp_fulfill_event`'s). Chains are now reference
counted: one without records is freed only after the chains it created, which a child looks up
for its parent's id (before, an implicit task's chain was freed at the region's end while a task
it created could still run). The chains named `u` of SLATE's traces (`experiments/slate`) are
gone: the deferred tasks that libgomp runs in the closing barrier run on their own chains.

**Analysis.** Unchanged but the loader's message for `what: detach`: a task chain is a chain, so
candidates, the L1 check and the observed classes apply to tasks. P-T6 (2.3) now reads: MPI
calls are made outside tasks or in tasks the tracer follows. A new premise, **P-T7 task
progress**: an alternative's schedule needs, at every point, a thread for each task in progress;
a tied task blocked in MPI holds its thread, the team has a fixed number of threads, and libgomp
decides which ready task a free thread runs. The claim of T1-T holds where the tasks that the
alternative keeps in progress at once fit the team; where they do not, the alternative is
unrealisable (over-prediction; a replay times out as inconclusive). The other way, a program can
hang because every thread blocks in a task that waits for a task no thread runs (CorrBench's
`deadlock_task_send_recv`); L1 does not predict that (under-prediction).

**Step 3: the finding.** The cross-task match of SLATE's CALU collision is a receive alternative
the machinery already expresses: the two tasks' receives with one envelope are concurrent
receives with a competitor, and the concurrent swap and the steal of 3.3 change both or one of
them; they are certified by T1-T, so no new class was needed. A class from physical overlap
alone (`experiments/slate/overlap.py`) was not added: an overlap of two operations' [posting,
completion] intervals shows concurrency only where each is posted and completed in one call
with no synchronisation in between, which the trace does not say, and with the tasks' order
recorded the happens-before already gives concurrency where it holds.

**Results** (a-p13, Open MPI unless said).

- Idioms 33-36, both MPIs: 33 (a receive's type freed while pending) counts 1 and 1, no finding;
  34 (two unordered tasks receive on one tag) an L1 concurrent swap with its 1a truncation; 35
  (ordered by `depend`) and 36 (ordered by a taskgroup, a taskwait, a team barrier, a nested
  task, an undeferred task) no finding. Mutations of the tracer that drop the task-end joins,
  the dependence edges or the undeferred edge make 35 and 36 report findings (1 to 19).
- SLATE wide CALU (`experiments/slate/traced_cells.sh`, [traced-cells.csv](../experiments/slate/traced-cells.csv),
  5 traced runs per cell, 4 ranks of 4 threads, nb 100): plain 1000x2000 on 2x2, 4 of 5 traces
  report 4 to 86 L1 findings, every L1 one at a receive in `slate::internal::permuteRows`
  (internal_swap.cc) with a tag in the collision range [20, 29]; the fifth run aborted with
  `MPI_ERR_TRUNCATE` and its trace is degraded `reconstruction`. Plain on 4x1: 5 of 5, 7 to 105
  findings, two of them 1a (the 80 000-byte tile into a 36 000-byte row receive), including 2
  runs whose factor came out right. 8 further traced 2x2 runs, all with a right factor: 37 to 90
  findings each (one 1a), all in `permuteRows`, tags 20 to 27 (not in the CSV: the same command
  with the traces kept, for the replay below). Patched library (2x2 and 4x1), square 1000x1000,
  partial pivoting and 900x2000 (q does not divide A_mt): 0 findings in all 25 traces, status
  complete.
  Traces 0.7 MB, 54 to 58 chains per rank; analysis 34 to 436 s per plain trace, 1 to 3 s per
  patched one. One plain 2x2 trace whose factor came out wrong also had an observed
  `2-buffering` ("observed matching infeasible even with infinite buffering"); not diagnosed (no
  wrong traced run since). Untraced 10 of 10 runs are wrong; traced, the share varies between
  sessions (7 of 10 wrong and 1 aborted in the series; 0 of 52 later, and 0 of 10
  with the tracer of main at that time; that the load of the machine decides it is a guess).
- SLATE sweep (`sweep.sh`, [sweep-d.csv](../experiments/slate/sweep-d.csv), 39 tester routines,
  square 800): every routine passes traced and untraced; 30 traces complete and 9 degraded
  `threads-handoff` (requests completed by another task: `gesv`, `gesv_tntpiv`, `geqrf`, `gelqf`,
  `gels`, `unmqr`, `he2hb`, `ge2tb`, `svd`), 0 findings in all 39 (before: 31 degraded
  `threads-tasks`).
- Replay (`replay_sample.py --threads`, the 1a alternative of a run with a right factor, 3
  replays and 3 controls): not confirmed. One replay ended in `MPI_ERR_TRUNCATE`, the predicted
  consequence, but its pins did not take their messages and its synchronisation differed from the
  trace's; two ended at a gate timeout; all three controls finished with a wrong factor, the race
  happening on its own. SLATE's tasks take locks (`LockGuard` on tile nodes, `critical(slate_mpi)`,
  `include/slate/BaseMatrix.hh`, `Tile.hh`) whose order varies between runs, and a gated task
  holds its thread (P-T7): forcing one pair of tasks needs the lock order replayed, as for HDF5
  (section 11).
- MPI-CorrBench (a05644b, 2 ranks, 2 threads): the 14 OpenMP ordering programs that call MPI in
  tasks, all degraded `threads-tasks` before, retraced. Of the 9 with an error,
  `two_collectives_3`, `_4`, `_6`, `_7` now report `2-thread-collective`, `_5` also `2-collective`;
  missed: `two_collectives_8` (the broadcasts' order across ranks is decided by a critical
  section: lock order, L1+SP-T), `deadlock_task_send_recv` (every thread blocks in a task: P-T7),
  `task_comm_free` (handle lifetime, outside the model), and `nondeterministic_deadlock`
  overflows the tracer's chain table (more than 256 chains with records on a rank; degraded
  `tracer-overflow`). Of the 5 labelled correct, `correct/two_collectives_4` reports
  `2-thread-collective` and `_5` `2-collective`, and both are errors as built: in the first each
  of the 2 threads creates a barrier task and a broadcast task, so two barriers of a rank are
  concurrent; in the second rank 0's 2 threads issue 4 broadcasts against rank 1's 2.
- Chain table: SLATE's wide CALU at nb 100 makes about 3 chains per tile column and rank (54 to
  58 at 1000x2000, 114 at 2000x4000, 174 at 3000x6000), so the 256 chains per rank of the tracer
  overflow from about 4 500x9 000 (extrapolated). Not raised: a vector clock is 4 KB per object
  at 256 chains and grows with the bound.
- Gate: the 531 single-chain traces, idiom 32 and the 84 multithreaded traces of milestone B,
  analysed by main (b857bd2) and this branch as text, `--json` and `--full-profile --json`: 1 848
  outputs and exit statuses, 0 differences (those traces predate task chains; their
  `threads-tasks` stays).
- Unit tests (RUNBOOK tests step, `STRETTO_REQUIRE_TRACES=1`, idioms traced first): 525 run, 17
  skipped, all pass, in the experiment image and in the SLATE image.

## 13. Lock reordering, level L1+L (30 Oct 2026)

Decided 30 Oct 2026: two critical sections of one lock may run in the other order than in the
run where both contain an MPI call of their thread, under a new premise about shared state; the
rest of the model stays L1's. This is an extension of L1, not the threads analogue of L1+SP
that section 2.4 planned (L1+SP-T): the horizon, its rules and the constraint graph are L1's,
the changed receives still cut their chains (rule 1), and nothing is assumed about what a chain
does with the results of its receives. What changes is the happens-before (the hard one below)
and one more condition on a horizon: its critical sections must fit in some order of each lock.
Code: `analysis/stretto/locks.py`; the tracer's section "critical sections"; the loader in
`chains.py`; the explorer `explore_threads.py`.

### 13.1 What the trace records

- **Lock events.** Mutual-exclusion locks are pthread mutexes (lock, trylock, timedlock), spin
  locks, OpenMP locks and critical constructs. Each acquire and release of one is a lock event
  `[lid, n, k, type]` on the chain's next record (`locks`), or, after its last record, on a
  side record `{"op": "locks"}` written when the chain ends: `lid` numbers the lock per process,
  `n` its critical sections in the order they were entered, `k` is the chain's ordinal of the
  operation, `type` 0 an acquire, 1 a soft acquire, 2 a release. Read-write locks, semaphores,
  nest locks, `ordered`, `atomic` and work shares keep every edge as before.
- **Soft edges.** The edge from critical section n-1 to n is *soft* when both contain a record
  of their chain and the chains differ. The acquire keeps the lock's clock aside until its chain
  writes a record (the edge stays out) or synchronises otherwise first (it is taken in, type 0).
  The acquire that ends a condition-variable wait keeps its edge: the wait returned because the
  previous critical section changed the state it waits for.
- **Hard happens-before.** Every clock has a second half without the soft edges; its deltas are
  `hafter`, written where they differ from `after`. When a chain raises one component twice
  before its next record, the earlier point is written too, with its own acquire ordinal (in
  `after` only the latest is), so that the operations of a gap need only what precedes them.
  Init records carry `lockorder: 1`.
- **Where the order is kept.** A rank whose lock events do not determine its critical sections
  keeps its full happens-before as the hard one (`Trace.lock_limits`, a note in the report): a
  lock with a soft edge whose sections are not all recorded with both ends (a thread without
  records took it, or one held it at the end), nested sections of a lock on one chain (a
  recursive lock), more than 4 096 lock events of a chain between two records, or a chain that
  synchronised with others after a lock operation and before its first record (its points are
  in no clock, so an edge from there is not recorded). The loader also keeps the edge into a
  critical section that its chain enters right after leaving an empty one of the same lock (a
  polling loop: what the section does may depend on what the empty ones read).

### 13.2 Model

**Hard happens-before HB⁻**: program order on each chain and the recorded synchronisation edges
but the soft ones. A critical section without an MPI call keeps the edges into and out of it,
so a lock-protected flag that orders the communication (idiom 41) or a polled one (42) orders
it in HB⁻ too.

**Lock-feasible schedule** of a horizon H at B: a linear order of the nodes of the constraint
graph of the alternative over HB⁻ (section 3.4 with HB⁻ in place of HB) and of the
synchronisation operations in the gaps between a chain's records, in which (i) every graph edge
holds; (ii) every recorded hard edge holds from the release's ordinal to the acquire's (an edge
without ordinals from the end of the source gap to the start of the target gap); (iii) a
critical section whose edge from the previous one is hard follows it; (iv) the critical sections
of each lock are disjoint, and one whose release lies beyond its chain's cut holds the lock to
the end of H; (v) a chain cut by rule 1 performs nothing after its last record in H; (vi) each
process issues its collectives on a communicator in σ's order (the graph pairs the k-th with the
others' k-th; a deadlock is not claimed where a thread has reached a collective that this order
holds back).

**Premise P-T8 (lock-protected state decides no call).** Every two conflicting accesses (one a
write) to a shared-memory location that some read deciding a call of a chain in H accesses are
ordered by HB⁻. It is P-T1 with HB⁻ in place of HB and implies P-T1. It fails where two critical
sections that both contain an MPI call also exchange state a call depends on (a flag set in one
and tested in the other, outside a polling loop), or where such a pair is the only order between
unprotected data written before one and read after the other. P-T2 extends to the lock events.

**Theorem T1-L (soundness of L1+L).** Premises of T1-T with P-T8 for P-T1. If `feasible.check`
over HB⁻ accepts M' at B with horizon H and H has a lock-feasible schedule, then for every
program consistent with σ on H there is an execution at B in which every operation of H is
issued and the matching agrees with M' on the judged receives. *Sketch.* Run the chains in the
schedule's order (P-T3). Each chain issues its records: its results on H are σ's but at the
changed receives, which end their chains in H (rule 1, condition (v)). A read that decides a
call reads in σ a write w that precedes it in HB⁻, and every other conflicting write is HB⁻-
ordered with both (P-T8); the schedule respects HB⁻ ((i)-(iii); a wait is placed at its ordinal
with every point of the gap the tracer keeps, a release point at its ordinal), so w is still the
last such write before the read, and the chain reads σ's value. (iv) makes the schedule an
execution of the locks whatever order of critical sections it takes. Postings and issues reach
MPI in the schedule's order, and the graph makes FIFO on that order give M' (3.2). Where the
schedule keeps every critical section in σ's order it respects HB, and T1-T's premises suffice
(the report says "premise P-T8 is not needed").

**Theorem T7-L (lock-order deadlocks).** If a schedule as above over the whole trace with M₀
enters critical section y before x (x before y in σ) and stops, and the chains it stops form a
set D in which every chain stops at an MPI operation only chains of D or terminated chains could
complete, at the acquire of a lock a chain of D holds, or at a wait for a point of a chain of D
whose ordinals the trace gives, with x's chain at the acquire y's chain holds, then under the
premises of T1-L some execution of every program consistent with σ deadlocks. *Sketch.* The
stopped prefix is an execution with σ's results (T1-L with M₀); a chain that passed the gap
before its next record reaches that call in every continuation, so a send there counts as
issued; T7-T's closure argument then holds with the two new stops, since a lock held by a chain
that never returns is never released and a point of such a chain never reached. The claim is
"some execution": the deadlock needs the other order of x and y, a scheduling choice.

**Collectives ordered by a lock.** Two collectives a, b of a process on one communicator, inside
critical sections of one lock on two chains, ordered by HB but not by HB⁻: if a schedule enters
b's section before a's and issues b as the process's k-th collective on the communicator (every
process having issued its collectives before b in σ's order, so their pairing is σ's),
another process's k-th collective in σ has other arguments (name, root, count, type size or
signature) than b, and §12.6.2 makes the program erroneous (class `2-thread-collective`, level
L1+L). Two identical calls are not reported (CorrBench's `correct/two_collectives_5` and `_6`).

**What the paper's theorems would need** (the paper is not edited): T1 gains T1-L as a variant
under P-T8, whose proof is T1-T's with HB⁻ and the schedule; T7 gains stops at lock acquires
and at waits with ordinals; T2 (monotonicity in B) is not claimed for L1+L, which is judged at
B = 0 and B = inf only; T6 (completeness) is not claimed: the schedule search is bounded, and
deciding lock-feasibility is NP-hard in general (*The Complexity of Dynamic Data Race
Prediction*, LICS 2020, for the analogous question about traces with locks). In the related-work
terms of 2.4: L1 is sync-preserving (SyncP's ideal); L1+L reorders critical sections as WCP and
M2 do, with the absence of conflicts a premise (P-T8) instead of a check on accesses the trace
does not have, restricted to sections that contain an MPI call; its deadlocks are the MPI
counterpart of sync-preserving deadlock prediction (*Sound Dynamic Deadlock Prediction in Linear
Time*, PLDI 2023), with cycles that pass through MPI waits and the run's matching as its
reads-from.

### 13.3 Analysis

- **Alternatives** (`locks.alternatives`): the candidates of section 3.3 over HB⁻ that L1 did
  not accept, judged at B = 0 and B = inf by `feasible.check` over HB⁻ and a schedule search
  (`locks._Sim`): critical sections without a record and gaps of the run are taken as they
  come; entering one with a record is a choice, tried in the run's order first, then depth first
  within a budget of 400 states. Where every schedule tried stops with chains waiting for a lock
  that a chain holds to the end of H, those chains are cut before the acquire (`check(...,
  caps=...)`, horizon rule caps) and the alternative judged again, up to 4 rounds: a smaller
  horizon, closed under the same rules.
- **Deadlocks** (`locks.lock_deadlocks`): per lock, pairs (y, x) of sections with records on two
  chains, x one of the 4 entered before y, y's acquire not after x's release in HB⁻ (64 pairs per
  lock, 512 per trace); a schedule with σ's matching that enters y first and every other section
  in σ's order where it can, stopped; T7-L's set D.
- **Report**: level `L1+L`, the premise in every finding, the critical sections taken in another
  order, the thresholds judged. Class `2-lock-order` (an error class) for the deadlocks.

Not covered, where L1+L under-predicts: alternatives that need a critical section *without* an
MPI call reordered (by design: their edges are hard); condition-variable handshakes, read-write
locks, semaphores; probe alternatives and flips beyond the ones of 3.3; contained deadlocks (1d)
of L1+L alternatives; thresholds other than 0 and infinity; the replay (no lock gates, section 11).

### 13.4 Results (30 Oct 2026, a-p13, the experiment image)

- **Idioms** (both MPIs, golden traces): 22 (senders in a mutex) L1+L 1f, the second thread's
  message first; 23 (probe and receive in one critical section) L1+L probe-alt; 27 (a mutex held
  across a receive) L1+L 2-lock-order at B = 0 and inf; 43 (two_collectives_9's shape) L1+L
  2-thread-collective on each rank; 21 (condition variable), 41 (flag in empty critical sections)
  and 42 (flag polled in the section that sends): nothing.
- **Explorer differential** (`experiments/e6_locks.py`, seeds 2 and 4, 3 000 random programs
  each, 2 or 3 processes, up to two worker threads each, one lock per process): no unsound claim
  among 4 983 L1 claims, 1 214 L1+L alternatives and 161 lock-order deadlocks. Upper bounds of
  what is missed: 372 rejected L1+L alternatives whose changed pair some execution realises, and
  149 deadlocks with a thread waiting at a lock that no claim names (the rows of seed 2's first
  1 000 programs: [results/e6_locks.csv](../experiments/results/e6_locks.csv)). The first version
  of the differential found one unsound L1 acceptance (an earlier message waiting for a receive
  beyond its thread's cut; fixed, `threads.matching_order_ok`) and one unsound lock-order deadlock
  of this level (lock ids of two processes taken for one lock). Two more false deadlocks of this
  level were found on idiom 22 at B = 0 (a thread held back before a send it had reached by an orientation of
  the run's matching: it now counts as issued) and on CorrBench's `correct/two_collectives` (a swap
  that reorders a process's collectives, after which the run's pairing of collectives no longer
  holds: schedules keep each process's collectives in the run's order). All fixed before these
  runs.
- **MPI-CorrBench** (a05644b, OpenMP ordering, Open MPI, 2 ranks of 2 threads, all 38 retraced):
  16 of 23 erroneous programs found (13 before), the new ones at L1+L: `dependant/deadlock_probe`
  (2-lock-order), `two_collectives_8` (2 findings) and `_9` (1), 2-thread-collective. Missed: the
  task deadlock (P-T7), the handle-lifetime programs, the buffer race and the one that overflows the
  chain table. Of the 15 correct programs 3 get an error class; the new one,
  `correct/dependant/deadlock_probe`, deadlocks when the thread with the tag-2 Mprobe takes the
  mutex first (its source expects that under `USE_DISTURBED_THREAD_ORDER`): a real deadlock that
  the suite's label does not count.
- **MPICH `mt_*`** (16 iterations, retraced): reports equal to main's on the same traces (the
  tests' critical sections, MTest's thread barrier, contain no MPI call).
- **Gate** (`experiments/report_diff.py` against main 8dfff4b, text, `--json`, `--full-profile
  --json`): the 531 single-chain traces and idiom 32, 1 596 comparisons, 0 differences; the 84
  multithreaded traces of milestone B (traced without lock events), 252 comparisons, 9 differences,
  all from the L1 fix: 22 findings of `mt_sendrecv`, `mt_probe_sendrecv` and `mt_iprobe_sendrecv`
  hold at B = 0 only instead of every B.
- **Tracer**: a thread the tracer did not see start could hang in `MPI_Init_thread` (UCX's memory
  hooks take a lock whose wrapper allocated the thread's chain again inside that allocation;
  every OpenMP idiom under MPICH once the chain structure grew); fixed. The hard clock doubles the
  clocks (8 KiB each at 256 chains).

## 14. Milestone E (30 Oct 2026): replaying pools of threads and tasks

**Why milestone C could not confirm them.** A chain pin names a receive by its thread's ordinal.
That names the same piece of work in a replay only if the threads share out their work as in the
trace. A pool of worker threads takes requests from a queue under a mutex and a condition
variable (HDF5 Subfiling); OpenMP tasks take roles, tiles or slots in the order they take a lock
(SLATE's tile locks, idiom 45). Which thread takes what follows the lock order, which varies from
run to run and which the replay left free, so milestone C's controls did not reproduce the
recorded run, and a replay of the wrong work could even be judged *not confirmed*: on idiom 45,
2 of 4 milestone C replays (Open MPI) were refuted wrongly, the tasks having no MPI operation
before their receives for `sync_divergence` to see.

**Design.**

1. *The lock order is recorded* (tracer, section "lock order for replays";
   `STRETTO_SYNC_ORDER=1`): each chain numbers its attempts to take a lock (mutexes, read-write
   and spin locks, semaphore waits, the mutex a condition wait takes again, OpenMP critical
   sections, atomic regions, locks and nest locks, `single` constructs), and `sync-<r>.txt`
   lists them per lock in the order the lock was taken: `lock chain ordinal seq [f|t|l]` (seq the
   chain's last record before the attempt; `f` a try that failed, `t` a condition wait that timed
   out, `l` a thread that lost a `single`). A lock is numbered at its first attempt and again
   after an `init` or `destroy` at its address. The trace proper is unchanged. The lock events of
   section 13 are not enough for a replay: they cover mutual exclusion only (no nest locks,
   whose order SLATE's tasks follow, nor read-write locks, semaphores, failed tries or the
   winner of a `single`). The locks of numerical libraries (OpenBLAS's buffer allocator) are
   followed as synchronisation but not listed: their order is the library's and varies.
2. *The replay gates it* (`STRETTO_PIN_LOCK`, `replay/pin.h`): one token per lock lists its
   acquisitions of the horizon in the trace's order; an attempt waits until the one before it in
   its token has taken the lock and must take the lock the token's first attempt took (`lock pin
   misapplied` otherwise); a try fails where the trace's failed and blocks where it succeeded; a
   `single` lets the trace's winner call first, so that the tasks it creates get the trace's
   names. An acquisition lies in the horizon when its chain's next record does, or the one before
   it does and no changed receive cuts the chain there (rule 1); each token stops at its first
   acquisition outside, and an attempt outside waits until the listed acquisitions of its lock
   have happened.
3. *A condition wait does not hold its mutex while it waits for its turn*: it waits in slices of
   2 ms and returns once the trace's previous acquisition of the mutex has happened, whether a
   signal came or not (a spurious wakeup, which POSIX allows; the program then reads what it read
   in the trace, P-T1), with the trace's result (`t`). A signal may wake another waiter than in the
   trace; the slices make that harmless. A wait that the tokens do not list asks again when it
   wakes: it may have begun before its lock's first listed acquisition named the lock, and must
   not take the mutex before the listed ones (idiom 44's idle worker took a request so).
4. *Stable identity.* With the lock order fixed, a chain's k-th receive is the k-th piece of the
   work it did in the trace, so chain names plus ordinals (P-T5) identify the work, which is what
   naming a worker by the request it serves would have bought. A probe that several threads make
   on one envelope is pinned by its envelope ordinal when the threads took turns (each probed in
   one stretch: HDF5's I/O concentrators, one per file).
5. *Every concurrent pair is gated.* Milestone C gated only the pairs the alternative reorders,
   since gating the others failed where the work was shared out differently. With the work fixed,
   every pair of concurrent operations that could take one message is gated: in the run's order
   in the control, so that the control is the recorded run; in the replay in the alternative's
   order where its constraint graph forces it (a path, or an operation beyond the graph, which
   waits for the horizon) and in the run's order elsewhere. A single topological order also
   reorders pairs that nothing orders, among them pairs that synchronisation the trace does not
   see orders (HDF5's pools of two files, below), and those gates never open. A receive that
   completes beyond the horizon is pinned but not required to take its message, in the control
   either.
6. *Tasks and their threads (P-T7).* A gated task holds its thread: libgomp's `GOMP_taskyield`
   does nothing and no entry point runs another task from inside one. The gates are at the
   operations only (an MPI call, a lock attempt), never at a task's start, and a gate that times
   out says how many threads wait at gates: as many as the team has threads is a starved team,
   not a diverged replay. Replays of SLATE with the trace's 4 threads starve (every thread waits
   for a task none is free to start); they run with 8 (`REPLAY_THREADS`), which leaves the tasks'
   names unchanged (a task is named after its creator, and the team's other implicit tasks make
   no MPI call), and the claim is then about a run of the program with a team of 8.
7. *A truncation that the program survives* is recorded: a receive that returns
   `MPI_ERR_TRUNCATE` under `MPI_ERRORS_RETURN` (HDF5's communicators) now carries the source and
   tag of its status, so the replay's own trace shows which message it took (before, its message
   was unknown and the pin counted as not taken).

"Confirmed" is unchanged: every required pin taken as the replay's own trace shows, the predicted
outcome, and a control that finishes, takes its pinned messages and synchronises as the trace; a
misapplied pin or lock pin, a gate timeout or a divergence makes the run inconclusive.

**Results** (a-p13, Open MPI 4.1.6 unless said; the merged tracer and analysis of main 5fdd932).

- *Idioms 44 (a pool of three workers under a mutex and a condition variable, the shape of HDF5's
  I/O concentrator) and 45 (two tasks drawing slots under a nest lock)*, both MPIs: an L1
  concurrent swap with a 1a truncation, and which worker or task does what varies between runs.
  With lock gates 10 of 10 replays (6 and 4) were confirmed (`MPI_ERR_TRUNCATE`, control
  finished); without them (milestone C) 0 of 10: 4 gate timeouts, 3 other inconclusive runs and
  3 wrong refutations (the replay finished without its pins, and no divergence was seen). Under
  MPICH (idiom 44, 2 replays) the replays end with exit status 14 and no `MPI_ERR_TRUNCATE` text,
  which `classify_rc` does not read as the predicted truncation: not confirmed (a harness limit;
  whether their pins were taken was not checked).
- *HDF5 Subfiling 9975eaeb2* ([experiments/subfiling](../experiments/subfiling/README.md), 4
  ranks): 13 traced runs of each version; every `pre` trace predicts 2 to 7 alternatives at the
  data receives, no `fix` trace one. Of the 65, 58 have a worker of one file's pool take data a
  worker of another file's pool took: over-predictions, since the concentrators of two files run
  one after the other, ordered by an atomic flag the tracer does not record (2.3). A sample of 16
  of them, twice each: 0 of 32 replays took their pins (29 gate timeouts, 3 SIGSEGV), 32 of 32
  controls finished with every pin taken. The 7 within one pool, 3 times each: in the trace of a
  run that passed (2 alternatives, a 1264-byte write into a 96-byte receive), 4 of 6 replays took
  every pin, the truncation recorded in the replay's trace and returned to HDF5, which went on
  (the run finished; 1 timeout, 1 finished without its pins), and 6 of 6 controls finished with
  every pin taken and no divergence: the race is realised as predicted, a confirmation by the
  criterion above with the truncation read from the replay's trace instead of the run's end. In
  the trace of the run that crashed (5 alternatives) 15 of 15 replays and 14 of 15 controls timed
  out: a control cannot follow a run that crashed. Milestone C: 0 of 40. (Before the rebase on
  5fdd932, with the analysis then on main, 9 of 33 alternatives of 5 other traces were within one
  pool and 27 of 27 of their replays took every pin with 27 of 27 controls clean; the cross-pool
  alternatives were as above.)
- *SLATE 878439b, wide CALU* ([experiments/slate](../experiments/slate/README.md), 4
  ranks, 1000x2000, 2x2): traced with 4 threads, replayed with 8. All 9 traced runs ended with a
  wrong factor or `MPI_ERR_TRUNCATE` (the load of the machine; earlier sessions had right
  factors), so every recorded run already has the race's consequence. 6 L1 1f alternatives of
  3 traces, 3 times each: 15 of 18 replays took every pin without divergence, 3 starved (8 of 8
  threads at gates);
  13 of 18 controls reproduced the recorded run (pins taken, no divergence, wrong factor), 2 timed
  out, 3 ended in `MPI_ERR_TRUNCATE`; 11 of 18 replays have both. The alternatives are realised;
  no consequence tells a replay from its control. Two 1a alternatives (the 80 000-byte tile into a
  36 000-byte row receive), 3 times each: 6 of 6 replays ended at a lock gate of a task waiting for
  a task that never started (one thread at a gate: not starvation; not diagnosed), 6 of 6
  controls reproduced the run. Milestone D: 0 of 3. A control run by hand with the trace's 4
  threads starved (4 of 4 threads at gates).
- *Gate* (`experiments/report_diff.py`, against main 5fdd932): the 531 single-chain traces, 38
  CorrBench and 7 `mt_*` traces, every golden (44 of Open MPI, 26 of MPICH, idioms 44 and 45
  among them) and the 47 idiom traces of the test run, as text, `--json` and `--full-profile
  --json`: 2 079 outputs and exit statuses equal but for the `analysis_commit` field of the JSON.
  The analysis is unchanged; the replay and the tracer's lock wrappers changed.
- *Unit tests* (RUNBOOK tests step, `STRETTO_REQUIRE_TRACES=1`, idioms traced first): 567 run, 17
  skipped, all pass, in the experiment image and in the SLATE image. Two earlier runs while the
  campaigns ran had one failure each that passed when run alone (an Open MPI truncation whose
  help text mpirun lost, exit 15; a misapplied pin's abort that outlasted the timeout).
- The HDF5 and SLATE campaigns ran with the tracer before a waiting thread re-checks its turn
  after waking (item 3). Run 7's two alternatives replayed again with the final tracer, 3 times
  each ([replay-same-final.csv](../experiments/subfiling/replay-same-final.csv)): 4 of 6 replays
  took every pin, 5 of 6 controls did, all finished and none diverged, 3 of 6 had both; the rest
  was not remeasured.

## 15. Milestone F (30 Oct 2026): requests across threads

Three real bugs of the class "matching race between threads" were missed or blurred: DLA-Future's
traces were degraded `threads-handoff` (pika's polling completes requests on whichever worker
polls), PaRSEC's `threads-cancel` (its communication engine cancels 24 persistent wildcard
receives at finalisation), and a third of SLATE's were blurred by two tool defects. Code: the
tracer's request table, `stretto.trace` (loader), `stretto.chains.add_edges`,
`stretto.threads.withdrawn_edges`, `stretto.horizon`; idiom 46.

### 15.1 Request handles reused across threads (tracer)

The tracer told pending requests apart by handle, then by the variable the program received the
handle in, then by age. Two cases defeat that with several threads, both seen with a diagnostic
build that printed every posting whose handle another pending entry still held and every
completion credited to another thread's entry (not committed):

- *A shared handle.* For a send that completes at once both libraries return one permanently
  complete request (Open MPI's `ompi_request_empty` for an inline send, MPICH's builtin request
  `0x6c000001`), so two threads hold equal handles at once. A thread that posts into a variable
  of its own and waits on a copy of the handle, as SLATE's `tileIbcastToSet` does, then took
  the other thread's entry by age. Idiom 46 (two threads, 100 such `Isend`s each): 40 of 40
  runs, 20 per MPI, degraded `threads-handoff`; a variant with larger messages (5 runs per size)
  did so at every size up to 16 KB under MPICH and up to 1 KB under Open MPI.
- *A handle freed and reused.* Inside a thread's `MPI_Waitall` the library frees a completed
  request and the next `Isend` of another thread gets the same handle before the first thread's
  wrapper has dropped its entry; the second thread's lookup by age then takes the first one's.
  In 6 of 10 traced square SLATE runs (2x2, 4 threads, 16 processors) a task's `Isend` got a
  heap handle that another task's pending entry held; in those runs the age happened to point
  at the right entry, in the defect's traces (1 of 5 diagnostic runs, 2 of 35 of the cells, 30
  Oct) it did not. Idiom 46 does not reproduce this case (no reuse seen at 4 and 16 KB under
  Open MPI, where the requests are real).

Fix: an entry records the chain that posted it, and a lookup prefers, among entries of the handle,
one in the program's variable, then one of the calling chain, then the oldest; a completion call
names (claims) its entries before it calls MPI and retires them by id afterwards, and lookups pass
over entries that a completion in progress has named while another entry has the handle. With it
idiom 46 is analysed completely in 40 of 40 runs, no finding.

### 15.2 Observed 2-buffering in SLATE and PaRSEC (analysis)

"Observed matching infeasible even with infinite buffering" in SLATE's 2x2 traces (4 of 13 on 30
Oct) and in 6 of 10 PaRSEC traces (once analysed, 15.3) was a defect of milestone B's reduced
orientation edges (section 10): `_thread_edges_reduced` cached physical keys by operation key,
and a send and a receive of one chain share their key `(chain, ordinal)`, so a send could get its
namesake receive's place in the run and be oriented against the run's physical order. It needs a
chain that both sends and receives (PaRSEC's `Sendrecv` to itself, SLATE's row exchanges), which
the random pools of the tests lacked; they now include such chains. Found by comparing the
constructions on a PaRSEC trace: the full one accepted the observed matching, the reduced one
had 52 edges the full one does not imply. With the cache keyed by object no such edge is left on
the two PaRSEC traces checked so, the observed matching is feasible under all three
constructions on them, and on 5 of the 6 SLATE traces of 15.4 the report finds it feasible (the
sixth is an aborted run). Neither the tracer's handle defect nor a
cross-chain edge was involved: 2-buffering appeared with the old tracer and the fixed one alike.

### 15.3 Model: hand-offs and cancels between threads

**Hand-off.** A request posted by chain A and completed (a wait or a test that returns it),
found complete (`MPI_Request_get_status`), freed or cancelled by chain B. Its completion node is
B's record (`Send.complete_at`); the horizon's rules 1 to 4, the deadlock check and the graph read
the completion there. The call on B names the request, so A's posting returned before it: the
loader adds the edge posting(A) → that record(B) wherever the clocks lack it (pika passes requests
through atomics) and closes it transitively (`chains.add_edges`; a record is written when its call
returns, so file order is a linear extension of both). Rule 1 cuts B, the chain that completed a
changed receive, right after the completion, and not A.

**Cancel.** A receive w posted on chain A and withdrawn by a successful cancel on chain C (C = A
included) is posted from its record to the cancel record and never matched, as for one chain
(docs/model.md §2b). For a receive or probe x of its process and communicator that takes or
reports a message s that w accepts: x happens-before after w's posting and not after its cancel
gets the edge complete(cancel) → m(x, s) (`Trace.withdrawn_competitors`); x concurrent with w's
posting gets one orientation (`threads.withdrawn_edges`), x posted first (issue(x) → issue(w)) or
w withdrawn first (complete(cancel) → m(x, s), a probe: → complete(x)), picked by the run's
physical order as in 3.2, the first only when the horizon does not keep the cancel; w with its
cancel outside the horizon stays posted and counts as a receive without a message for the
matching order (`threads.matching_order_ok`).

| premise | what it says | if it fails |
|---|---|---|
| P-T9 hand-off order | a call that names a request starts after the call that created it returned | cannot fail physically: the handle exists only then |
| P-T10 results at the completion | every call that depends on an operation's result (its buffer, its status) follows, in happens-before, the call that completed it | over-prediction, as for P-T1: a chain that receives the result through synchronisation the tracer does not record (pika schedules a task's continuation through atomics) keeps calls in the horizon that the changed result decides |
| P-T11 cancel at its call | a successful cancel withdraws the receive inside `MPI_Cancel`, on whichever thread calls it (Open MPI and MPICH do) | as for one chain: the edges from the cancel are no guaranteed edges (`strong_only`) |

**Soundness (T1-T extended).** Premises of T1-T with P-T9 to P-T11. Take a topological order of
the graph with completions on their completing chains and the implied edges, and schedule the
chains by it (P-T3). The implied edges hold in every execution (P-T9), so the order is a schedule
of the program. A chain cut by rule 1 after a changed receive's completion issues its records up
to there; any call that reads the result lies HB-after that completion (P-T10), so on that chain
beyond the cut or on another chain beyond a synchronisation the cut chain does not reach (rule
8), and the posting chain's records, which do not follow the completion, do not depend on it. A
withdrawn receive is posted over [posting, cancel] in the schedule; every x that takes or reports
a message it accepts is ordered before its posting or after its cancel by the edges above, so
MPI's FIFO rule on the schedule never hands such a message to w, and w's cancel succeeds there
too (P-T11). The rest is T1-T's argument; the picks add constraints, so acceptance stays sound and
completeness is lost as in 3.6. T7-T: a chain that completes another's request stops at that
completion, which is where `contained_deadlock` looks.

**What stays degraded or outside.** `threads-handoff` and `threads-cancel` are no longer
reasons: every hand-off and every successful cancel of a receive is modelled. A successful cancel
of a send and a cancel whose outcome the trace lacks stay `unmodelled`, as for one chain. P-T10
cannot be checked from the trace; the report counts the requests completed by another chain
and names the premises. Not adapted: the replay harness (`confirm.py`, another track's) still
places a completion on the posting chain, so replays of traces with hand-offs are not validated;
the fast checks and read bounds are single-chain only, as before.

### 15.4 Results (30 Oct 2026, a-p13, Open MPI 4.1.6 unless said)

- *Idiom 46* (both MPIs): with the tracer of main, 40 of 40 runs degraded `threads-handoff`
  (20 per MPI); with the fix 40 of 40 complete without a finding. Golden traces of both MPIs.
- *DLA-Future 09fe442, `matrix::copy`* ([experiments/dlaf](../experiments/dlaf/README.md),
  `traced.csv`, 5 traced runs per cell and variant, one copy each): 15 of 15 plain traces of the
  three cells with colliding tags (2x1, 4x1, 3x2) predict L1 swaps or steals of two receives
  with one source and one colliding tag, 34 to 206 per trace; 0 of 15 patched traces and 0 of
  20 controls (2x2 without a colliding pair, one pika thread; plain and patched). All 15 plain
  runs copied wrongly, none of the others. Before: every trace degraded `threads-handoff`. What
  stays: the observed false alarms of pika's unrecorded synchronisation (0 to 13
  `2-thread-collective`, 2 to 6 `2-thread-wait` per trace) and `waitany-order` warnings of
  pika's `MPI_Testsome` polling (45 to 2 443), on patched traces too; the claims rest on P-T10,
  which pika does not give the trace (a continuation reads a result on another worker after
  atomics).
- *PaRSEC 6c49997, local reshapes* ([experiments/parsec](../experiments/parsec/README.md),
  `traced.csv`, 5 per cell and variant): every run came out right; 10 of 10 plain traces with
  two virtual processes (1 rank of 6 threads; 4 ranks of 2) predict 15 to 20 swaps or steals of
  two threads' reshape receives on a rank's self duplicate at the shared tag, 0 of 10 patched
  traces and 0 of 20 controls (`MPI_THREAD_SERIALIZED`, one virtual process). Before: degraded
  `threads-cancel`. The 10 earlier plain traces give 15 or 17 each, 6 of them only 5 and an
  observed `2-buffering` without the fix of 15.2.
- *SLATE observed `2-buffering`* (15.2): of 24 traced wide 2x2 runs on 12 processors (12 with
  the tracer of main, 12 with the fixed one; the setting of the 30 Oct remeasurement), 6
  reported it with the analysis of main (3 with each tracer), with 0 to 41 L1 findings; with the
  fix, 5 of them have a feasible observed matching and 41 to 70 L1 findings, and the sixth, an
  aborted run (`MPI_ERR_TRUNCATE`), keeps a different 2-buffering finding: the observed run
  deadlocks below the 80 000-byte tile's size, the tile the abort left without a receive. The
  trace of main's tracer degraded `threads-handoff` (the defect of 15.1) has 23 L1 findings now.
- *Gate* (`experiments/report_diff.py` against main ba9e3c8, text, `--json`, `--full-profile
  --json`, the JSON's `analysis_commit` field left out): the 531 single-chain traces and idiom 32,
  1 596 comparisons, 0 differences; 148 traces with threads (every golden of both MPIs, 38
  CorrBench, the 7 `mt_*` of 16 iterations, HDF5 Subfiling's 10 traces of section 10, Uintah's 6
  of section 11, the earlier DLA-Future 2 and PaRSEC 15), 444 comparisons, 60 differences, and
  the 92 idiom traces of the test run, 276 comparisons, 6 differences. Every difference is a
  trace that main degraded `threads-handoff` or `threads-cancel` and this branch analyses: idiom
  26 (4 traces; status complete, the same `2-thread-wait`), CorrBench `request_reuse` (an
  erroneous program: 2 L1 1f at its two receives that never got a message, and the observed run
  deadlocks below 4 bytes), DLA-Future's 2 and PaRSEC's 15 (now analysed). The reduced-orientation
  fix changes no report of these traces.
- *Unit tests* (RUNBOOK tests step, `STRETTO_REQUIRE_TRACES=1`, idioms of both MPIs traced
  first): 572 run, 17 skipped, all pass, in the experiment image and in the SLATE image.
- *SLATE 878439b, wide CALU, on 16 processors* ([experiments/slate](../experiments/slate/README.md),
  `traced-cells-16.csv`): 42 plain wide traced runs (15 on 2x2, 27 on 4x1), every trace analysed
  completely with 15 to 109 L1 swaps or steals at `permuteRows` receives (1a in 7), none
  degraded, none with an observed 2-buffering; 7 runs had a right factor (1 on 2x2, 6 on 4x1)
  and each of their traces predicts the race (36 to 108); 0 findings in 10 patched and 15 control
  traces. The target of 10 right-factor runs was not reached (right factors are rare on this
  host: 1 in 15 on 2x2).
