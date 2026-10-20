/* Rematch confirming-replay pinning, compiled into the tracer (and, with
   -DREMATCH_PIN_ONLY, into the standalone replay library). No-ops unless set:

   REMATCH_PIN="rank:widx:src[:tag],..."   for the widx-th wildcard receive of a rank
                                           (0-based, counted over Recv/Irecv/Sendrecv/
                                           persistent starts, exactly as the tracer's
                                           "widx" field) replace MPI_ANY_SOURCE by src and
                                           MPI_ANY_TAG by tag.
   REMATCH_PIN_PROBE="rank:pidx:src[:tag],..."  same for the pidx-th wildcard probe that found
                                           a message (Probe/Mprobe, and Iprobe/Improbe with
                                           flag 1), counted separately ("pidx" field of
                                           probe records).
   REMATCH_SEND_DELAY_MS="rank:ms,..."     sleep before every send on that rank (last wins).
   REMATCH_RECV_DELAY_MS="rank:ms,..."     sleep before every receive on that rank.
   REMATCH_DELAY_AT="rank:send|recv:k:ms,..."  sleep only before the k-th send/receive. */
#ifndef REMATCH_PIN_H
#define REMATCH_PIN_H
#include <mpi.h>
void rematch_pin_init(int rank);
/* Counts the wildcard receive (writes its ordinal to *widx, -1 if not a wildcard) and
   returns 1 if src/tag were substituted. The pin names the sender's world rank; it is
   translated into comm (the remote group of an intercommunicator). Every applied pin is
   reported on stderr as "[rematch-pin] rank R: applied widx=W ...", a pin that names a
   rank outside the communicator is reported and not applied. */
int  rematch_pin_recv(int *src, int *tag, MPI_Comm comm, int *widx);
/* A pinned receive completed (the call or the completion call returned it): reported on
   stderr as "[rematch-pin] rank R: matched widx=W src=S", S the source in the receive's
   communicator. Only this line shows that the forced match happened. */
void rematch_pin_matched(int widx, int comm_src);
int  rematch_pin_probe(int *src, int *tag, MPI_Comm comm, int *pidx);   /* peek; ordinal counts successful probes only */
void rematch_pin_probe_commit(int pidx);
/* At MPI_Finalize: report every pin whose ordinal the run never reached ("unused"). */
void rematch_pin_finalize(void);
void rematch_pin_before_send(void);
void rematch_pin_before_recv(void);
#endif
