/* Stretto confirming-replay pinning, compiled into the tracer (and, with
   -DSTRETTO_PIN_ONLY, into the standalone replay library). No-ops unless set:

   STRETTO_PIN="rank:widx:src[:tag][@comm:otag[:site]],..."
                                           for the widx-th wildcard receive of a rank
                                           (0-based, counted over Recv/Irecv/Sendrecv/
                                           persistent starts, exactly as the tracer's
                                           "widx" field) replace MPI_ANY_SOURCE by src and
                                           MPI_ANY_TAG by tag. After '@', the identity of the
                                           traced operation at that ordinal: its communicator
                                           (the tracer's local id, the creation ordinal of
                                           the communicator on the rank), its tag argument
                                           (-1 for MPI_ANY_TAG) and, in hexadecimal, its
                                           call site (the return address of the MPI call
                                           minus its object's load address, as the tracer's
                                           site records give it); 'x' leaves a part
                                           unchecked. A pin without '@' is not checked.
   STRETTO_PIN_PROBE="rank:pidx:src[:tag][@comm:otag[:site[:n]]],..."  same for the pidx-th
                                           wildcard probe that found a message (Probe/Mprobe,
                                           and Iprobe/Improbe with flag 1), counted separately
                                           ("pidx" field of probe records). A src of -1
                                           makes an Iprobe or Improbe at that ordinal return
                                           flag 0 once, without polling: a probe that saw a
                                           message reports none (a flag flip to false).
                                           n: the probe's ordinal among the rank's wildcard
                                           probes of its envelope (communicator and tag
                                           argument, both given) that found a message; a
                                           token with it pins the probe of that envelope at
                                           that ordinal, whatever the rank's ordinal, and
                                           pidx only names it in the report lines. Two polling
                                           loops of other tags or communicators from one call
                                           site find their messages in another order in a
                                           replay than in the trace, which moves the rank's
                                           ordinal and not the envelope's. Where the rank's
                                           wildcard probes all have one envelope the two
                                           ordinals are equal.
   STRETTO_PIN_FLIP="rank:key:src[:tag][@comm:otag[:site[:found]]],..."  a poll that failed in the trace
                                           (a poll is an Iprobe or Improbe, or an MPI_Test,
                                           Testall, Testany, Testsome or MPI_Request_get_status;
                                           it failed when it returned flag 0, or no index): the
                                           first call of the identity after '@', among the
                                           rank's calls after exactly key calls that are not
                                           failed polls (the poll's key, the analysis's
                                           Trace.poll_key; the buffer guard's and overflow
                                           records and a blocking call's begin record do not
                                           count), whose source and tag arguments admit src and
                                           tag. The key, unlike the ordinal of the poll's
                                           segment, does not depend on whether a loop failed
                                           between two of its calls.
                                           src >= 0: the probe waits until a message of world
                                           rank src (and tag, for an ANY_TAG probe) is available
                                           and returns it with flag 1 (a flag flip to true);
                                           -2: the test waits until its request is complete and
                                           returns flag 1; -1: the call returns flag 0 without
                                           polling, the trace's own result (the control). found:
                                           the number of the wildcard probes of the call's
                                           envelope (as for STRETTO_PIN_PROBE's n) that found a
                                           message before the call, or of all the rank's
                                           wildcard probes for a test or an unchecked
                                           communicator; a call met after another number ends
                                           the run as misapplied ("flip pin key=K
                                           misapplied"), since the replay diverged before it.
   STRETTO_SEND_DELAY_MS="rank:ms,..."     sleep before every send on that rank (last wins).
   STRETTO_RECV_DELAY_MS="rank:ms,..."     sleep before every receive on that rank.
   STRETTO_DELAY_AT="rank:send|recv:k:ms,..."  sleep only before the k-th send/receive.

   A pin whose ordinal falls on an operation of another identity means that the replay
   diverged from the trace before it: pinning that operation would only look like the
   alternative. The library then reports "[stretto-pin] rank R: pin widx=W misapplied: ..."
   (or "probe pin pidx=P misapplied") and ends the run with MPI_Abort and the code
   STRETTO_PIN_MISAPPLIED. A token that does not parse ends the run as well.

   Several threads (docs/threads.md, sections 4 and 11). In a process whose threads call MPI
   the rank's ordinals (widx, pidx) depend on how the threads interleave, so a replay names an
   operation by its chain (the tracer's logical thread name: "0" for the thread that initialised
   MPI, "c.k" for the k-th thread chain c created, "c/p.j" for thread j of the p-th parallel
   region chain c entered) and its ordinal among the chain's operations of its kind: receives
   (every receive but those of MPI_PROC_NULL and Mrecv/Imrecv, counted at posting), sends
   (every send but those to MPI_PROC_NULL) and probes that found a message (but those of
   MPI_PROC_NULL). These names and ordinals need the tracer's thread support: the replay
   library alone ends a run that sets either variable below.
   STRETTO_PIN_CHAIN="rank:chain:ridx:src[:tag][@comm:otag[:site]][=what],..."
                                           the ridx-th receive of the chain: an ANY_SOURCE
                                           receive takes src (a world rank), an ANY_TAG one tag;
                                           a receive with both given is only checked (its
                                           identity, after '@', as for STRETTO_PIN) and its
                                           completion reported. What follows '=' is the
                                           harness's (the message the receive must take) and is
                                           ignored here. Reported as "[stretto-pin] rank R chain
                                           C: applied ridx=K ...", "... matched ridx=K src=S
                                           id=...", "... pin ridx=K misapplied: ...".
   STRETTO_PIN_ORDER="rank:A#k#n<B#k#n,..."  order gates: chain B's n-th operation of kind k
                                           (r a receive, s a send, p a probe) waits, before it
                                           is posted, until chain A's n-th operation of its kind
                                           has been posted (a probe: has returned a message).
                                           Under MPI_THREAD_MULTIPLE the tracer posts every
                                           point-to-point operation as its nonblocking form
                                           under the issue lock, so "posted" is the return of
                                           that call. A gate still closed after
                                           STRETTO_GATE_TIMEOUT_MS (default 10000) ends the run
                                           with STRETTO_PIN_GATE_TIMEOUT, naming the gate: the
                                           replay diverged, or the order needs synchronisation
                                           the trace does not record.
   STRETTO_PIN_LOCK="rank:A#n>B#m>...,rank:A#n!,..."  lock gates (docs/threads.md, section 14):
                                           a token lists one lock's acquisitions in the order
                                           of the trace's sync log (the tracer's
                                           STRETTO_SYNC_ORDER=1), each named by its chain and
                                           the ordinal of the attempt among the chain's
                                           attempts to take a lock (mutexes, read-write and
                                           spin locks, semaphore waits, condition waits, OpenMP
                                           critical sections, atomic regions, locks and single
                                           constructs the program reaches). An attempt waits
                                           until the one before it in the token has taken the
                                           lock, and must take the lock the token's first one
                                           took ("lock pin a#n misapplied" ends the run
                                           otherwise); a try listed there takes the lock with
                                           the blocking call. "A#n!" is a try that failed in the
                                           trace: it fails without trying; "A#nt" a condition
                                           wait that timed out, which returns ETIMEDOUT. An
                                           attempt no token names, on a lock a token names,
                                           waits until every listed acquisition of that lock has
                                           happened (they are the horizon's). Timeouts as for
                                           STRETTO_PIN_ORDER. */
#ifndef STRETTO_PIN_H
#define STRETTO_PIN_H
#include <mpi.h>
#define STRETTO_PIN_MISAPPLIED 86
#define STRETTO_PIN_GATE_TIMEOUT 87
/* the kinds of operation a chain counts (STRETTO_PIN_ORDER's r, s, p) */
#define STRETTO_OP_RECV 0
#define STRETTO_OP_SEND 1
#define STRETTO_OP_PROBE 2
/* At MPI_Init. Rank 0 prints "[stretto-pin] rank 0: loaded (replay library|tracer)" (the
   tracer only when a pin or delay variable is set): the harness requires the line, since ld.so
   ignores a preloaded library it cannot open and the run would read as a plain one. */
void stretto_pin_init(int rank);
/* Counts the wildcard receive (writes its ordinal to *widx, -1 if not a wildcard) and
   returns a nonzero handle if a pin applies (src/tag substituted, or a chain pin checked): the
   widx plus 1 for a pin of STRETTO_PIN, a negative number for one of STRETTO_PIN_CHAIN, whose
   chain ordinal the last stretto_pin_gate(STRETTO_OP_RECV, ...) of the thread gave. The pin
   names the sender's world rank; it is translated into comm (the remote group of an
   intercommunicator). Every applied pin is reported on stderr as "[stretto-pin] rank R:
   applied widx=W ... id=C:G:X", with the operation's identity; a pin that names a rank outside
   the communicator is reported and not applied, and one on an operation of another identity
   ends the run (above). */
int  stretto_pin_recv(int *src, int *tag, MPI_Comm comm, int *widx);
/* A pinned receive completed (the call or the completion call returned it), h the handle
   stretto_pin_recv returned: reported on stderr as "[stretto-pin] rank R: matched widx=W src=S
   id=C:G:X" (or "rank R chain C: matched ridx=K ..."), S the source in the receive's
   communicator and id the receive's identity. Only this line shows that the forced match
   happened. */
void stretto_pin_matched(int h, int comm_src);
/* Several threads: before an operation of kind (STRETTO_OP_*) is posted. skip: an operation
   the chains do not count (MPI_PROC_NULL). Returns the operation's ordinal on the calling
   thread's chain (for a probe, the ordinal its next message would have), -1 if neither
   STRETTO_PIN_CHAIN nor STRETTO_PIN_ORDER is set, after waiting for the operation's gates.
   Called outside the issue lock. */
int  stretto_pin_gate(int kind, int skip);
/* Would stretto_pin_gate(kind, skip) return at once, every gate of the calling thread's next
   operation of kind open? 1 also when no gate applies. Does not count the operation. */
int  stretto_pin_gate_open(int kind, int skip);
/* The operation of kind with chain ordinal ord (stretto_pin_gate's) has been posted (a probe:
   has found a message): the gates that wait for it open. */
void stretto_pin_posted(int kind, int ord);
/* Lock gates (STRETTO_PIN_LOCK): what the tracer does with an attempt to take a lock. */
#define STRETTO_LOCK_GO 0          /* call as the program asked */
#define STRETTO_LOCK_FAIL 1        /* a try that failed in the trace: fail without calling */
#define STRETTO_LOCK_BLOCK 2       /* a try that succeeded in the trace: take the lock with the blocking call */
#define STRETTO_LOCK_NOTYET 3      /* (poll) the trace's acquisition before it has not happened yet */
#define STRETTO_LOCK_TIMEDOUT 4    /* a condition wait that timed out in the trace: return ETIMEDOUT */
/* Are lock gates set for this rank? (after stretto_pin_init) */
int  stretto_pin_locks_on(void);
/* Before attempt n of the calling thread's chain on the lock at key (the tracer's ordinal):
   waits for its gate, unless poll, which returns STRETTO_LOCK_NOTYET instead (a condition
   wait, which must release its mutex while it waits). is_try: a try or timed call. */
int  stretto_pin_lock(long n, const void *key, int is_try, int poll);
/* Attempt n has returned, ok if it took the lock: the gates that wait for it open. */
void stretto_pin_locked(long n, int ok);
/* The receive ordinal the next stretto_pin_recv of this thread applies chain pins to, for a
   call that gates several receives before posting them (MPI_Startall). */
void stretto_pin_use_ord(int ord);
/* Peek; the ordinal counts successful probes only. Returns 1 if src/tag were substituted, 2 if
   the probe is to return flag 0 without polling (a pin with src -1, applied once and reported
   as "[stretto-pin] rank R: applied unflip pidx=P id=C:G:X"). */
int  stretto_pin_probe(int *src, int *tag, MPI_Comm comm, int *pidx);
/* The ordinal a wildcard probe would take (-1 for another probe), without applying a pin: for a
   call that a flip pin forces instead. */
int  stretto_pin_probe_ordinal(int src, int tag);
/* A wildcard probe found a message: the rank's ordinal and that of its envelope (comm, otag:
   its communicator and tag argument) advance. src, tag: the envelope the probe reported
   (comm-local); a pinned probe that found its message is reported as "[stretto-pin] rank R:
   applied probe pidx=P src=S tag=T id=C:G:X", P the pin's pidx */
void stretto_pin_probe_commit(int pidx, MPI_Comm comm, int otag, int src, int tag);
/* Every record the tracer writes (both builds call it, the replay library writing none): counts
   the calls that are not failed polls, the key of STRETTO_PIN_FLIP. body is the record's text,
   failed_poll whether it is a poll that returned false; the buffer guard's and overflow records
   are not calls, and a begin record is followed by its call's own, so they are skipped, as the
   analysis skips them. */
void stretto_pin_record(const char *body, int failed_poll);
/* Before a poll that may fail: is it the target of a STRETTO_PIN_FLIP token? is_test: a Test or
   MPI_Request_get_status (the source and tag are then not looked at); src, tag: the call's
   arguments. Returns 0 (not a target), 1 (force true: for a probe, *fsrc and *ftag are the
   envelope to wait for, in comm's ranks), or -1 (return flag 0 without polling). *key receives
   the call's key. Reported as "[stretto-pin] rank R: applied flip key=K ... id=C:G:X". */
int  stretto_pin_flip(int is_test, int src, int tag, MPI_Comm comm, int *fsrc, int *ftag, int *key);
/* A forced flip returned true: "[stretto-pin] rank R: matched flip key=K src=S tag=T id=...",
   S and T the envelope of the message returned (comm-local; -1 for a test), the evidence that
   the flip happened. */
void stretto_pin_flip_matched(int key, int src, int tag);
/* At MPI_Finalize: report every pin whose ordinal the run never reached ("unused"). */
void stretto_pin_finalize(void);
void stretto_pin_before_send(void);
void stretto_pin_before_recv(void);
#endif
