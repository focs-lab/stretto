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
   STRETTO_PIN_PROBE="rank:pidx:src[:tag][@comm:otag[:site]],..."  same for the pidx-th
                                           wildcard probe that found a message (Probe/Mprobe,
                                           and Iprobe/Improbe with flag 1), counted separately
                                           ("pidx" field of probe records). A src of -1
                                           makes an Iprobe or Improbe at that ordinal return
                                           flag 0 once, without polling: a probe that saw a
                                           message reports none (a flag flip to false).
   STRETTO_PIN_FLIP="rank:seg:src[:tag][@comm:otag[:site[:found]]],..."  a poll that failed in the trace
                                           (Iprobe or Improbe with flag 0, MPI_Test or
                                           MPI_Request_get_status that found nothing): the first
                                           call of the identity after '@' in the rank's seg-th
                                           poll segment (0-based; a segment is a maximal run of
                                           failed polls with no other call between them, numbered
                                           as the analysis numbers them, trace.poll_segments)
                                           whose source and tag arguments admit src and tag.
                                           src >= 0: the probe waits until a message of world
                                           rank src (and tag, for an ANY_TAG probe) is available
                                           and returns it with flag 1 (a flag flip to true);
                                           -2: the test waits until its request is complete and
                                           returns flag 1; -1: the call returns flag 0 without
                                           polling, the trace's own result (the control). found:
                                           the number of the rank's wildcard probes that found a
                                           message before the call; a call met after another
                                           number ends the run as misapplied ("flip pin seg=K
                                           misapplied"), since the replay diverged before it.
   STRETTO_SEND_DELAY_MS="rank:ms,..."     sleep before every send on that rank (last wins).
   STRETTO_RECV_DELAY_MS="rank:ms,..."     sleep before every receive on that rank.
   STRETTO_DELAY_AT="rank:send|recv:k:ms,..."  sleep only before the k-th send/receive.

   A pin whose ordinal falls on an operation of another identity means that the replay
   diverged from the trace before it: pinning that operation would only look like the
   alternative. The library then reports "[stretto-pin] rank R: pin widx=W misapplied: ..."
   (or "probe pin pidx=P misapplied") and ends the run with MPI_Abort and the code
   STRETTO_PIN_MISAPPLIED. A token that does not parse ends the run as well. */
#ifndef STRETTO_PIN_H
#define STRETTO_PIN_H
#include <mpi.h>
#define STRETTO_PIN_MISAPPLIED 86
/* At MPI_Init. Rank 0 prints "[stretto-pin] rank 0: loaded (replay library|tracer)" (the
   tracer only when a pin or delay variable is set): the harness requires the line, since ld.so
   ignores a preloaded library it cannot open and the run would read as a plain one. */
void stretto_pin_init(int rank);
/* Counts the wildcard receive (writes its ordinal to *widx, -1 if not a wildcard) and
   returns 1 if src/tag were substituted. The pin names the sender's world rank; it is
   translated into comm (the remote group of an intercommunicator). Every applied pin is
   reported on stderr as "[stretto-pin] rank R: applied widx=W ... id=C:G:X", with the
   operation's identity; a pin that names a rank outside the communicator is reported and
   not applied, and one on an operation of another identity ends the run (above). */
int  stretto_pin_recv(int *src, int *tag, MPI_Comm comm, int *widx);
/* A pinned receive completed (the call or the completion call returned it): reported on
   stderr as "[stretto-pin] rank R: matched widx=W src=S id=C:G:X", S the source in the
   receive's communicator and id the receive's identity. Only this line shows that the forced
   match happened. */
void stretto_pin_matched(int widx, int comm_src);
/* Peek; the ordinal counts successful probes only. Returns 1 if src/tag were substituted, 2 if
   the probe is to return flag 0 without polling (a pin with src -1, applied once and reported
   as "[stretto-pin] rank R: applied unflip pidx=P id=C:G:X"). */
int  stretto_pin_probe(int *src, int *tag, MPI_Comm comm, int *pidx);
/* The ordinal a wildcard probe would take (-1 for another probe), without applying a pin: for a
   call that a flip pin forces instead. */
int  stretto_pin_probe_ordinal(int src, int tag);
/* src, tag: the envelope the probe reported (comm-local); a pinned probe that found its message
   is reported as "[stretto-pin] rank R: applied probe pidx=P src=S tag=T id=C:G:X" */
void stretto_pin_probe_commit(int pidx, int src, int tag);
/* Every record the tracer writes (both builds call it, the replay library writing none): counts
   the poll segments for STRETTO_PIN_FLIP. body is the record's text, failed_poll whether it is
   a poll that returned false; the buffer guard's and overflow records are not calls and are
   skipped, as the analysis skips them. */
void stretto_pin_record(const char *body, int failed_poll);
/* Before a poll that may fail: is it the target of a STRETTO_PIN_FLIP token? is_test: a Test or
   MPI_Request_get_status (the source and tag are then not looked at); src, tag: the call's
   arguments. Returns 0 (not a target), 1 (force true: for a probe, *fsrc and *ftag are the
   envelope to wait for, in comm's ranks), or -1 (return flag 0 without polling). *seg receives
   the segment. Reported as "[stretto-pin] rank R: applied flip seg=K ... id=C:G:X". */
int  stretto_pin_flip(int is_test, int src, int tag, MPI_Comm comm, int *fsrc, int *ftag, int *seg);
/* A forced flip returned true: "[stretto-pin] rank R: matched flip seg=K src=S tag=T id=...",
   S and T the envelope of the message returned (comm-local; -1 for a test), the evidence that
   the flip happened. */
void stretto_pin_flip_matched(int seg, int src, int tag);
/* At MPI_Finalize: report every pin whose ordinal the run never reached ("unused"). */
void stretto_pin_finalize(void);
void stretto_pin_before_send(void);
void stretto_pin_before_recv(void);
#endif
