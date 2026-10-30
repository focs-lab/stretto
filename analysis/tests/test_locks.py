"""Lock reordering (docs/threads.md, section 13): the tracer's lock events and hard
happens-before as the loader reads them, when a rank's lock order is kept instead, and what
level L1+L reports on a trace of two threads sending in critical sections of one mutex."""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stretto import trace as T
from stretto.report import analyze, format_report



def senders_in_a_mutex():
    """Idiom 22's trace as the tracer writes it: rank 0's threads 0.0 and 0.1 each send to rank 1
    inside a critical section of one mutex, 0.0 first; the edge between the two critical
    sections is soft (both contain a send)."""
    init = {"op": "init", "np": 2, "version": 3, "thread": 3, "issue": 1, "lockorder": 1}
    snd = {"op": "send", "mode": "std", "dst": 1, "tag": 0, "comm": 0, "count": 1, "tsize": 4, "tsig": "MPI_INT"}
    rcv = {"op": "recv", "src": 0, "tag": 0, "comm": 0, "count": 1, "tsize": 4, "tsig": "MPI_INT", "msrc": 0, "mtag": 0, "mcount": 1}
    r0 = [dict(init, seq=0),
          {"seq": 1, "op": "chain", "id": 1, "name": "0.0", "parent": 0, "kind": "pthread"},
          dict(snd, seq=2, tid=1, after=[[0, 0, 1, 0]], locks=[[1, 1, 1, 0]], iseq=0),
          {"seq": 3, "tid": 1, "op": "locks", "locks": [[1, 1, 2, 2]]},
          {"seq": 4, "op": "chain", "id": 2, "name": "0.1", "parent": 0, "kind": "pthread"},
          dict(snd, seq=5, tid=2, after=[[0, 0, 2, 0], [1, 2, 2, 1]], hafter=[[0, 0, 2, 0]], locks=[[1, 2, 1, 1]], iseq=1),
          {"seq": 6, "tid": 2, "op": "locks", "locks": [[1, 2, 2, 2]]},
          {"seq": 7, "op": "finalize", "after": [[1, 2, 3, 3], [2, 5, 3, 4]]}]
    r1 = [dict(init, seq=0), dict(rcv, seq=1, iseq=0), dict(rcv, seq=2, iseq=1), {"seq": 3, "op": "finalize"}]
    return {0: r0, 1: r1}


def load(raw):
    t = T.Trace()
    T._build(t, copy.deepcopy(raw))
    return t


class C_Loader(unittest.TestCase):
    def test_soft_edge_leaves_the_hard_order(self):
        t = load(senders_in_a_mutex())
        a, b = (c for c in t.chains[0] if c != 0)
        css = t.locks[0][1]
        self.assertEqual([(cs.n, cs.chain, cs.soft, cs.empty) for cs in css], [(1, a, False, False), (2, b, True, False)])
        s1, s2 = t.sends[(a, 0)], t.sends[(b, 0)]
        self.assertTrue(t.hb.before(a, s1.idx, b, s2.idx))            # the run's order, through the mutex
        self.assertFalse(t.hb_hard.before(a, s1.idx, b, s2.idx))      # not in the hard happens-before
        self.assertTrue(t.hb_hard.before(0, 0, b, s2.idx))            # the creation is

    def test_a_trace_without_lock_events_is_analysed_as_before(self):
        raw = senders_in_a_mutex()
        for recs in raw.values():
            for r in recs:
                r.pop("hafter", None)
                r.pop("locks", None)
                r.pop("lockorder", None)
            recs[:] = [r for r in recs if r["op"] != "locks"]
        t = load(raw)
        self.assertIsNone(t.hb_hard)
        self.assertEqual(t.locks, {})
        rep = analyze(t, with_sp=False)
        self.assertFalse([f for f in rep.findings if f.level == "L1+L"])

    def test_lock_order_kept_where_it_is_not_known(self):
        """A thread that synchronised after a lock operation before its first record (marker -2),
        too many lock events (marker -1), or a critical section missing from the trace: the rank
        keeps its order (its hard happens-before is the full one) and the report says why."""
        for change, why in ((lambda r0: r0[5]["locks"].append([-2, 0, 0, 0]), "before its first record"),
                            (lambda r0: r0[5]["locks"].append([-1, 0, 0, 0]), "more lock operations"),
                            (lambda r0: r0.__setitem__(3, {"seq": 3, "tid": 1, "op": "locks", "locks": []}), "not recorded in full")):
            raw = senders_in_a_mutex()
            change(raw[0])
            t = load(raw)
            self.assertEqual(t.locks, {})
            self.assertTrue(t.lock_limits and why in t.lock_limits[0][1], t.lock_limits)
            a, b = (c for c in t.chains[0] if c != 0)
            self.assertTrue(t.hb_hard.before(a, 0, b, 0))
            rep = analyze(t, with_sp=False)
            self.assertIn("lock order of rank 0 kept", format_report(t, rep))
            self.assertFalse([f for f in rep.findings if f.level == "L1+L"])

    def test_polling_keeps_the_edge(self):
        """A critical section entered right after an empty one of the same lock on its thread (a
        polling loop) is not soft, whatever the tracer wrote."""
        raw = senders_in_a_mutex()
        r0 = raw[0]
        # thread 0.1 polls once (critical section 2, empty) before the one that sends (now 3)
        r0[5]["locks"] = [[1, 2, 1, 0], [1, 2, 2, 2], [1, 3, 3, 1]]
        r0[6]["locks"] = [[1, 3, 4, 2]]
        t = load(raw)
        css = t.locks[0][1]
        self.assertEqual([(cs.n, cs.empty, cs.soft) for cs in css], [(1, False, False), (2, True, False), (3, False, False)])


class C_Analysis(unittest.TestCase):
    def test_senders_in_a_mutex(self):
        """Idiom 22: L1 keeps the lock order and finds nothing; L1+L enters the second thread's
        critical section first, so rank 1's first receive takes its message."""
        t = load(senders_in_a_mutex())
        rep = analyze(t, with_sp=False)
        self.assertEqual([f.cls for f in rep.findings if f.level == "L1"], [])
        lf = [f for f in rep.findings if f.level == "L1+L"]
        self.assertEqual({f.cls for f in lf}, {"1f"}, format_report(t, rep))
        self.assertTrue(any("P-T8" in d for f in lf for d in f.detail))
        self.assertTrue(any("another order than in the run" in d for f in lf for d in f.detail))


if __name__ == "__main__":
    unittest.main()
