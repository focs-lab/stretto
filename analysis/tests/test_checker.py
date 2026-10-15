"""Checker regression tests for the A.1 audit findings. Traces are built in memory from
raw records in the tracer's JSON format (new encoding: anysrc/anytag/procnull flags)."""
import unittest

from rematch import trace as T
from rematch.feasible import check, feasible_profile, observed_feasible_at, profile_summary
from rematch.matching import candidates_full
from rematch.report import analyze


def mk(raw_ranks, np=None):
    t = T.Trace()
    np = np or len(raw_ranks)
    for rank in raw_ranks:
        raw_ranks[rank] = [{"op": "init", "np": np}] + raw_ranks[rank] + [{"op": "finalize"}]
    T._build(t, raw_ranks)
    return t


def send(dst, tag, count=1, mode="std", req=None, comm=0):
    d = {"op": "send", "mode": mode, "dst": dst, "tag": tag, "comm": comm, "count": count, "tsize": 4}
    if req is not None:
        d["req"] = req
    return d


def recv(src, tag, msrc, mtag, count=1, comm=0):
    d = {"op": "recv", "src": 0 if src == T.ANY else src, "tag": 0 if tag == T.ANY else tag, "comm": comm,
         "count": count, "tsize": 4, "msrc": msrc, "mtag": mtag, "mcount": count}
    if src == T.ANY:
        d["anysrc"] = 1
    if tag == T.ANY:
        d["anytag"] = 1
    return d


def irecv(src, tag, req, count=1, comm=0):
    d = {"op": "irecv", "src": 0 if src == T.ANY else src, "tag": 0 if tag == T.ANY else tag, "comm": comm,
         "count": count, "tsize": 4, "req": req}
    if src == T.ANY:
        d["anysrc"] = 1
    if tag == T.ANY:
        d["anytag"] = 1
    return d


def wait(*done, kind="wait"):
    return {"op": "wait", "kind": kind, "done": [{"req": q, "msrc": s, "mtag": tg, "mcount": 1} for q, s, tg in done]}


def coll(name, root=-1, comm=0, req=None):
    d = {"op": "coll", "name": name, "comm": comm, "root": root, "count": 1, "tsize": 4}
    if req is not None:
        d["req"] = req
    return d


class C1_PostingIndex(unittest.TestCase):
    """Auditor's counterexample: an Irecv posted before the changed receive but waited
    after the cut keeps its matching-order obligations."""

    def setUp(self):
        self.t = mk({
            0: [send(1, 1), send(1, 2)],
            2: [send(1, 9)],
            1: [irecv(T.ANY, T.ANY, 10), irecv(0, T.ANY, 11), irecv(T.ANY, T.ANY, 12),
                wait((10, 0, 1), (12, 2, 9), kind="waitall"), wait((11, 0, 2))],
        })

    def test_observed(self):
        self.assertEqual(self.t.observed, {(1, 0): (0, 0), (1, 1): (0, 1), (1, 2): (2, 0)})

    def test_swap_rejected(self):
        M = dict(self.t.observed)
        M[(1, 0)] = (2, 0)
        M[(1, 2)] = (0, 0)
        for B in (0, None):
            v = check(self.t, M, {(1, 0), (1, 2)}, B, "L1")
            self.assertFalse(v.feasible, v.reason)
            v = check(self.t, M, {(1, 0), (1, 2)}, B, "SP")
            self.assertFalse(v.feasible, v.reason)


class C3_CollectiveShapes(unittest.TestCase):
    def test_bcast_root_does_not_wait(self):
        # rank 0: Send(->1, 1000B); Bcast(root=1).  rank 1: Bcast(root=1); Recv(0).
        t = mk({0: [send(1, 1, 250), coll("bcast", root=1)],
                1: [coll("bcast", root=1), recv(0, 1, 0, 1, 250)]})
        rep = analyze(t, with_sp=False)
        self.assertEqual(rep.by_class("2-buffering"), [])
        self.assertTrue(observed_feasible_at(t, 0).feasible)

    def test_barrier_does(self):
        t = mk({0: [send(1, 1, 250), coll("barrier")],
                1: [coll("barrier"), recv(0, 1, 0, 1, 250)]})
        self.assertFalse(observed_feasible_at(t, 0).feasible)
        self.assertTrue(observed_feasible_at(t, None).feasible)
        rep = analyze(t, with_sp=False)
        self.assertEqual(len(rep.by_class("2-buffering")), 1)

    def test_reduce_root_waits(self):
        t = mk({0: [coll("reduce", root=0), send(1, 1, 250)],
                1: [recv(0, 1, 0, 1, 250), coll("reduce", root=0)]})
        # root 0 must wait for rank 1, which waits for 0's send: cycle at any B? The send
        # comes after the reduce at rank 0, so the reduce cannot complete: infeasible even at inf.
        self.assertFalse(observed_feasible_at(t, None).feasible)
        t2 = mk({0: [coll("reduce", root=1), send(1, 1, 250)],
                 1: [recv(0, 1, 0, 1, 250), coll("reduce", root=1)]})
        # non-root 0 may leave the reduce at once (root-in shape): feasible.
        self.assertTrue(observed_feasible_at(t2, None).feasible)


class C4_SendModes(unittest.TestCase):
    def test_ssend_head_to_head_never_feasible(self):
        t = mk({0: [send(1, 1, 1, "ssend"), recv(1, 1, 1, 1)], 1: [send(0, 1, 1, "ssend"), recv(0, 1, 0, 1)]})
        prof = feasible_profile(t, t.observed, set(), mode="SP", strong_only=True)
        self.assertEqual(profile_summary(prof)[2], "never")

    def test_bsend_head_to_head_always_feasible(self):
        t = mk({0: [send(1, 1, 1000, "bsend"), recv(1, 1, 1, 1, 1000)],
                1: [send(0, 1, 1000, "bsend"), recv(0, 1, 0, 1, 1000)]})
        prof = feasible_profile(t, t.observed, set(), mode="SP", strong_only=True)
        self.assertEqual(profile_summary(prof)[2], "any B")

    def test_std_head_to_head_threshold(self):
        t = mk({0: [send(1, 1, 100), recv(1, 1, 1, 1, 100)], 1: [send(0, 1, 100), recv(0, 1, 0, 1, 100)]})
        prof = feasible_profile(t, t.observed, set(), mode="SP", strong_only=True)
        self.assertEqual(profile_summary(prof)[2], "B >= 400")


class C7_ProcNull(unittest.TestCase):
    def test_procnull_receive_is_not_a_wildcard(self):
        raw = recv(0, 1, -1, -1)
        raw.update({"src": -1, "procnull": 1, "msrc": -1})
        t = mk({0: [send(1, 1)], 1: [raw, recv(0, 1, 0, 1)]})
        self.assertEqual(len(t.recvs), 1)
        self.assertEqual(analyze(t, with_sp=False).by_class("1f"), [])


class C9_UnwaitedRequest(unittest.TestCase):
    def test_no_fabricated_rendezvous_edge(self):
        # rank 0: Isend(->1, 4000B) never waited; Recv(1). rank 1: Send(->0); Recv(0)
        t = mk({0: [send(1, 1, 1000, "isend", req=5), recv(1, 2, 1, 2)],
                1: [send(0, 2), recv(0, 1, 0, 1, 1000)]})
        self.assertFalse(t.sends[(0, 0)].completed)
        self.assertTrue(observed_feasible_at(t, 0).feasible)


class C14_LeakedReceive(unittest.TestCase):
    def test_leaked_irecv_does_not_silence_rank(self):
        # a leaked Irecv from rank 2 tag 9 (never sent), then a genuine 3-rank race
        t = mk({0: [irecv(2, 9, 7), recv(T.ANY, 1, 1, 1), recv(2, 1, 2, 1)],
                1: [send(0, 1)], 2: [send(0, 1)]})
        alts, _ = candidates_full(t)
        feas = [a for a in alts if check(t, a.M, a.changed, None, "L1").feasible]
        self.assertTrue(feas, "the wildcard alternative must survive the leaked request")

    def test_leaked_receive_that_accepts_blocks_alternative(self):
        # the leaked Irecv accepts rank 2's message: it would take it, so r(0,1) <- s(2,0) is infeasible
        t = mk({0: [irecv(2, 1, 7), recv(T.ANY, 1, 1, 1)],
                1: [send(0, 1)], 2: [send(0, 1)]})
        alts, _ = candidates_full(t)
        for a in alts:
            if a.recv == (0, 1) and a.send == (2, 0):
                self.assertFalse(check(t, a.M, a.changed, None, "L1").feasible)


class C20_CollectiveMismatch(unittest.TestCase):
    def test_name_mismatch_reported(self):
        t = mk({0: [coll("bcast", root=0)], 1: [coll("barrier")]})
        rep = analyze(t, with_sp=False)
        self.assertEqual(len(rep.by_class("2-collective")), 1)


class C_Comms(unittest.TestCase):
    def test_subcommunicator_ranks_translate(self):
        # comm 1 = ranks {1, 2}; rank 2 sends to comm-rank 0 (= world rank 1) on comm 1
        c1 = {"op": "comm", "name": "split", "parent": 0, "id": 2, "ranks": [1, 2], "gord": 0}
        t = mk({0: [dict(c1, id=2, ranks=[0])],
                1: [c1, {**recv(0, 1, 1, 1, comm=2), "anysrc": 1}],
                2: [c1, send(0, 1, comm=2)]})
        self.assertEqual(t.observed, {(1, 0): (2, 0)})
        s = t.sends[(2, 0)]
        self.assertEqual(s.dst, 1)
        self.assertEqual(s.comm, t.recvs[(1, 0)].comm)


class C_Probe(unittest.TestCase):
    def test_probe_alternative(self):
        p = {"op": "probe", "kind": "probe", "src": 0, "tag": 1, "comm": 0, "flag": 1, "msrc": 1, "mtag": 1, "anysrc": 1}
        t = mk({0: [p, recv(1, 1, 1, 1), recv(2, 1, 2, 1)], 1: [send(0, 1)], 2: [send(0, 1)]})
        self.assertEqual(t.probes[(0, 0)].seen, (1, 0))
        rep = analyze(t, with_sp=False)
        self.assertEqual(len(rep.by_class("probe-alt")), 1)


class C_Blocked(unittest.TestCase):
    def test_unpaired_begin_is_a_blocked_call(self):
        t = T.Trace()
        raw = {0: [{"op": "init", "np": 2}, {"op": "begin", "call": "recv", "src": 1, "tag": 1, "comm": 0, "count": 1, "tsize": 4}],
               1: [{"op": "init", "np": 2}, {"op": "begin", "call": "recv", "src": 0, "tag": 1, "comm": 0, "count": 1, "tsize": 4}]}
        T._build(t, raw)
        self.assertEqual(sorted(t.truncated), [0, 1])
        self.assertEqual(len(t.blocked_calls()), 2)
        self.assertFalse(t.recvs[(0, 0)].completed)
        rep = analyze(t, with_sp=False)
        self.assertTrue(any("blocked in recv" in n for n in rep.notes))


if __name__ == "__main__":
    unittest.main()


class C_BlockedFinding(unittest.TestCase):
    def test_blocked_receive_reported(self):
        t = T.Trace()
        raw = {0: [{"op": "init", "np": 2}, {"op": "begin", "call": "recv", "src": 1, "tag": 1, "comm": 0, "count": 1, "tsize": 4}],
               1: [{"op": "init", "np": 2}, {"op": "begin", "call": "recv", "src": 0, "tag": 1, "comm": 0, "count": 1, "tsize": 4}]}
        T._build(t, raw)
        rep = analyze(t, with_sp=False)
        self.assertEqual(len(rep.by_class("2-blocked")), 2)
        self.assertTrue(all("no compatible unconsumed send" in f.detail[0] for f in rep.by_class("2-blocked")))


class C_ProbeFifo(unittest.TestCase):
    def test_probe_alternatives_respect_fifo(self):
        p = {"op": "probe", "kind": "iprobe", "src": 0, "tag": 1, "comm": 0, "flag": 1, "msrc": 1, "mtag": 1, "anysrc": 1}
        # rank 1 sends twice with the same tag; the probe saw the first: the second is not an alternative
        t = mk({0: [p, recv(1, 1, 1, 1), recv(1, 1, 1, 1), recv(2, 1, 2, 1)], 1: [send(0, 1), send(0, 1)], 2: [send(0, 1)]})
        alts = [(pp.key, s.key) for pp, s in __import__("rematch.matching", fromlist=["probe_candidates"]).probe_candidates(t)]
        self.assertEqual(alts, [((0, 0), (2, 0))])


class C_CommFree(unittest.TestCase):
    def test_free_record_does_not_remap(self):
        c = {"op": "comm", "name": "create", "parent": 0, "id": 2, "ranks": [0, 1], "gord": 1}
        free = {"op": "comm", "name": "free", "parent": -1, "id": 2}
        t = mk({0: [c, coll("allgather", comm=2), free], 1: [c, coll("allgather", comm=2), free]})
        self.assertEqual(analyze(t, with_sp=False).by_class("2-collective"), [])
        self.assertEqual(len(t.coll_sets()), 1)


class C_ObservedOrphanAndSpin(unittest.TestCase):
    def test_unreceived_send_is_an_orphan(self):
        t = mk({0: [send(1, 1)], 1: []})
        self.assertEqual(len([f for f in analyze(t, with_sp=False).findings if f.cls == "1c" and f.level == "observed"]), 1)

    def test_cancelled_send_is_not_an_orphan(self):
        t = mk({0: [send(1, 1, mode="isend", req=3), {"op": "cancel", "req": 3}, wait((3, None, None))], 1: []})
        self.assertEqual([f for f in analyze(t, with_sp=False).findings if f.cls == "1c"], [])

    def test_imrecv_of_a_probed_message_receives_it(self):
        t = T.Trace()
        T._build(t, {0: [{"op": "init", "np": 2},
                         {"op": "probe", "kind": "mprobe", "src": 1, "tag": 0, "comm": 0, "flag": 1, "msrc": 1, "mtag": 0, "mbytes": 4},
                         {"op": "irecv", "src": 1, "tag": 0, "comm": 0, "mrecv": 1, "count": 1, "tsize": 4, "req": 0},
                         {"op": "wait", "kind": "wait", "reqs": [0], "done": [{"req": 0, "msrc": 1, "mtag": 0, "mcount": 1}]},
                         {"op": "finalize"}],
                     1: [{"op": "init", "np": 2},
                         {"op": "send", "mode": "std", "dst": 0, "tag": 0, "comm": 0, "count": 1, "tsize": 4},
                         {"op": "finalize"}]})
        self.assertEqual(t.observed[(0, 0)], (1, 0))
        self.assertEqual(analyze(t, with_sp=False).by_class("1c"), [])

    def test_spinning_rank_is_reported(self):
        t = T.Trace()
        p = {"op": "probe", "kind": "iprobe", "src": 1, "tag": 0, "comm": 0, "flag": 0}
        T._build(t, {0: [{"op": "init", "np": 2}, p], 1: [{"op": "init", "np": 2}, {"op": "finalize"}]})
        rep = analyze(t, with_sp=False)
        self.assertTrue(any("spinning" in f.title for f in rep.by_class("2-blocked")))
