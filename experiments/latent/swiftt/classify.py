"""Kinds of the probe alternatives of ADLB's server loop (adlbsub driver), for
experiments/history_predict.py --classify.

ADLB tags (lb/code/src/messaging.h, enum from ADLB_TAG_PUT = 1): ADLB_TAG_SYNC_REQUEST = 28,
ADLB_TAG_SYNC_SUB = 40. A server receives SYNC_REQUEST into pre-posted MPI_Irecv(ANY_SOURCE)
buffers (sync.c:152, 713) and every other request with xlb_poll's MPI_Iprobe(ANY_SOURCE,
ANY_TAG) (server.c:376), whose tag selects the handler; no handler is registered for
ADLB_TAG_SYNC_SUB (handlers.c:165-), which only the sync handlers receive, by a receive from the
sender (sync.c:965, 1057, 1104).
  syncsub  a wildcard-tag probe that could see a SYNC_SUB message (xlb_handle would fail with
           "invalid tag")
  unflip   a probe that found a message could have found none
  other    any other probe alternative (another request taken first: what the loop is for)"""

SYNC_SUB = 40


def alternatives(t, rep):
    out = []
    for f in rep.findings:
        if f.level != "L1" or f.probe is None:
            continue
        if f.send is None:
            out.append((f, "unflip"))
            continue
        p, s = t.probes[f.probe], t.sends[f.send]
        out.append((f, "syncsub" if p.tag == -1 and s.tag == SYNC_SUB else "other"))
    return out
