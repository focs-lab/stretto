"""Lock reordering (docs/threads.md, section 13): the tracer's lock events and hard
happens-before as the loader reads them, when a rank's lock order is kept instead, the traced
idioms 21-23, 27 and 41-43 of both MPIs, and the analysis of several threads at L1 and L1+L
against the exhaustive explorer of stretto.explore_threads on random programs."""
import copy
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stretto import explore_threads as X, trace as T
from stretto.report import analyze, format_report

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


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


def golden(tc, mpi, n):
    d = os.path.join(GOLDEN, mpi)
    name = next((x for x in sorted(os.listdir(d)) if x.startswith(f"{n}_")), None) if os.path.isdir(d) else None
    if name is None:
        tc.skipTest(f"no golden trace of idiom {n} for {mpi}")
    return T.load(os.path.join(d, name))


class C_Idioms(unittest.TestCase):
    """The traced idioms (golden copies, both MPIs): which ones L1+L reports, and that it reports
    nothing where the order of the critical sections carries the order of the communication."""

    def classes(self, mpi, n):
        t = golden(self, mpi, n)
        self.assertEqual(t.lock_limits, [], f"{mpi} {n}")
        rep = analyze(t, with_sp=False)
        return {f"{f.level}:{f.cls}" for f in rep.findings}, format_report(t, rep)

    def test_expected(self):
        expected = {
            21: set(),                                   # a condition variable orders the senders
            22: {"L1+L:1f"},                             # senders in critical sections of one mutex
            23: {"L1+L:probe-alt"},                      # probe and receive in one critical section
            27: {"L1+L:2-lock-order"},                   # a mutex held across a receive
            41: set(),                                   # a flag in empty critical sections orders the sends
            42: set(),                                   # the flag polled in the critical section that sends
            43: {"L1+L:2-thread-collective"},            # broadcasts of two types in critical constructs
        }
        for mpi in ("openmpi", "mpich"):
            for n, want in expected.items():
                with self.subTest(mpi=mpi, idiom=n):
                    got, text = self.classes(mpi, n)
                    self.assertEqual({c for c in got if not c.startswith("observed:")}, want, text)


class C_Explorer(unittest.TestCase):
    def test_lock_held_across_a_receive(self):
        """Idiom 27 as a program: a deadlock, at every threshold, when the receiving thread takes
        the mutex first; a finished run when the sending thread does."""
        prog = X.TProgram([[[], [X.TOp("lock", 0), X.TOp("recv", 1, 2, 4), X.TOp("unlock", 0)],
                            [X.TOp("lock", 0), X.TOp("send", 1, 1, 4), X.TOp("unlock", 0)]],
                           [[X.TOp("recv", 0, 1, 4), X.TOp("send", 0, 2, 4)]]])
        for B in (0, None):
            dl = X.deadlocks(prog, B)
            self.assertTrue(dl and all(X.lock_waits(prog, st) for st in dl), B)
            self.assertTrue(X.exists(prog, B, {((1, 0, 0), (0, 2, 1))}))

    def test_simulated_trace_loads(self):
        rng = random.Random(5)
        for _ in range(20):
            prog = X.random_program(rng)
            got = X.trace_of(prog, None, rng)
            if got is None:
                continue
            t, where, matching = got
            self.assertEqual(t.incomplete, [])
            for rk, sk in t.observed.items():
                self.assertEqual(matching[X._op_of(t, where, t.recvs[rk])], X._op_of(t, where, t.sends[sk]))


class C_Differential(unittest.TestCase):
    """experiments/e6_locks.py on fewer programs: every claim of L1 and L1+L on random programs of
    several threads with locks is realised by some execution."""

    def test_no_unsound_claim(self):
        rng = random.Random(11)
        claims = {"L1": 0, "L1+L": 0, "deadlock": 0}
        for i in range(150):
            prog = X.random_program(rng, nprocs=rng.choice((2, 3)))
            got = X.trace_of(prog, None, rng)
            if got is None:
                continue
            t, where, _ = got
            for r in X.compare(prog, t, where):
                self.assertNotEqual(r["kind"], "UNSOUND", f"program {i}: {r}\n{prog}")
                if r["claim"]:
                    claims["deadlock" if r["item"].startswith("deadlock") else r["level"]] += 1
        self.assertTrue(all(claims.values()), claims)


if __name__ == "__main__":
    unittest.main()
