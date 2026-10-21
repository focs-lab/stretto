"""Checker regression tests for the A.1 audit findings. Traces are built in memory from
raw records in the tracer's JSON format (new encoding: anysrc/anytag/procnull flags)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest

from stretto import dsl, explore, fast, trace as T, types
from stretto.feasible import breakpoints
from stretto.matching import Overlay, probe_candidates
from stretto.report import _completion_alternatives
from stretto.feasible import check, feasible_profile, observed_feasible_at, profile_summary
from stretto.matching import candidates_full
from stretto.report import analyze
import reference


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


def recv(src, tag, msrc, mtag, count=1, comm=0, widx=None):
    d = {"op": "recv", "src": 0 if src == T.ANY else src, "tag": 0 if tag == T.ANY else tag, "comm": comm,
         "count": count, "tsize": 4, "msrc": msrc, "mtag": mtag, "mcount": count}
    if src == T.ANY:
        d["anysrc"] = 1
    if tag == T.ANY:
        d["anytag"] = 1
    if widx is not None:
        d["widx"] = widx
    return d


def irecv(src, tag, req, count=1, comm=0):
    d = {"op": "irecv", "src": 0 if src == T.ANY else src, "tag": 0 if tag == T.ANY else tag, "comm": comm,
         "count": count, "tsize": 4, "req": req}
    if src == T.ANY:
        d["anysrc"] = 1
    if tag == T.ANY:
        d["anytag"] = 1
    return d


def wait(*done, kind="wait", reqs=None):
    return {"op": "wait", "kind": kind, "reqs": list(reqs) if reqs is not None else [q for q, _, _ in done],
            "done": [{"req": q, "msrc": s, "mtag": tg, "mcount": 1} for q, s, tg in done]}


def coll(name, root=-1, comm=0, req=None):
    d = {"op": "coll", "name": name, "comm": comm, "root": root, "count": 1, "tsize": 4}
    if req is not None:
        d["req"] = req
    return d


class C_Overlay(unittest.TestCase):
    def test_overlay_behaves_as_the_edited_dict(self):
        """An alternative's M' is the observed matching plus a few changes and removals; it must
        read exactly like the dict copy it replaces (candidates once copied the whole matching
        per alternative, quadratic memory on a trace with a million wildcard receives)."""
        base = {(0, 1): (1, 1), (0, 2): (1, 2), (2, 1): (1, 3)}
        o = Overlay(base, {(0, 1): (2, 5), (3, 1): (1, 9)}, [(0, 2)])
        d = dict(base); d[(0, 1)] = (2, 5); d[(3, 1)] = (1, 9); del d[(0, 2)]
        self.assertEqual(dict(o), d)
        self.assertEqual(o, d)
        self.assertEqual(len(o), len(d))
        self.assertEqual(set(o), set(d))
        self.assertEqual(o[(0, 1)], (2, 5))
        self.assertEqual(o.get((0, 2)), None)
        self.assertNotIn((0, 2), o)
        self.assertIn((3, 1), o)
        with self.assertRaises(KeyError):
            o[(0, 2)]
        self.assertEqual(base, {(0, 1): (1, 1), (0, 2): (1, 2), (2, 1): (1, 3)})   # the base is untouched
        o2 = o.with_(changed={(2, 1): (1, 7)}, removed=[(3, 1)])
        self.assertEqual(dict(o2), {(0, 1): (2, 5), (2, 1): (1, 7)})
        self.assertEqual(dict(o), d)                                              # with_ copies
        # a removed key that the base lacks, and an overlay over an overlay: len agrees with iteration
        o3 = Overlay(base, {(9, 9): (1, 1)}, [(0, 2), (7, 7)])
        self.assertEqual(len(o3), len(list(o3)))
        self.assertEqual(len(o3), 3)
        o4 = Overlay(o3, {(0, 1): (5, 5)}, [(9, 9), (8, 8)])
        self.assertEqual(len(o4), len(list(o4)))
        self.assertEqual(dict(o4), {(0, 1): (5, 5), (2, 1): (1, 3)})


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
        alts = [(pp.key, s.key) for pp, s in __import__("stretto.matching", fromlist=["probe_candidates"]).probe_candidates(t)]
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

    def test_killed_rank_whose_last_record_is_a_send(self):
        t = T.Trace()
        T._build(t, {0: [{"op": "init", "np": 2}, send(1, 1)], 1: [{"op": "init", "np": 2}, {"op": "finalize"}]})
        rep = analyze(t, with_sp=False)
        self.assertEqual(t.truncated, [0])
        self.assertFalse(rep.by_class("2-blocked"))

    def test_spinning_rank_is_reported(self):
        t = T.Trace()
        p = {"op": "probe", "kind": "iprobe", "src": 1, "tag": 0, "comm": 0, "flag": 0}
        T._build(t, {0: [{"op": "init", "np": 2}, p], 1: [{"op": "init", "np": 2}, {"op": "finalize"}]})
        rep = analyze(t, with_sp=False)
        self.assertTrue(any("spinning" in f.title for f in rep.by_class("2-blocked")))


class C_ProgramFromTrace(unittest.TestCase):
    """The single-path program is built from the loaded objects: world ranks, global
    communicator ids, the same ordinals as the trace, PROC_NULL operations skipped."""

    def _split(self):
        # comm 2 = world ranks {1, 2}; on it rank 2 (comm rank 1) sends to comm rank 0 (= world
        # rank 1), rank 1 receives from ANY; both then barrier on it. Rank 0 sends to PROC_NULL.
        c = {"op": "comm", "name": "split", "parent": 0, "id": 2, "ranks": [1, 2], "gord": 0}
        return mk({0: [dict(c, id=2, ranks=[0]), {**send(-2, 1), "procnull": 1}, send(1, 5)],
                   1: [c, {**recv(0, 1, 1, 1, comm=2), "anysrc": 1}, coll("barrier", comm=2), recv(0, 5, 0, 5)],
                   2: [c, send(0, 1, comm=2), coll("barrier", comm=2)]})

    def test_world_ranks_and_global_comms(self):
        t = self._split()
        prog = dsl.program_from_trace(t)
        self.assertEqual(len(prog[0]), 1)                       # the PROC_NULL send is not an operation
        s = prog[2][0]
        self.assertEqual((s.dst, s.comm), (1, t.sends[(2, 0)].comm))
        r = prog[1][0]
        self.assertEqual((r.src, r.comm), (dsl.ANY, t.recvs[(1, 0)].comm))
        res = explore.explore(prog, None, comms=t.comm_members)
        self.assertEqual(res.kinds(), {"done"})
        for rk, sk in t.observed.items():                       # every observed pair is a key of the program
            self.assertTrue(res.outcomes_with(rk, sk), (rk, sk))

    def test_correct_program_has_no_sp_consequence(self):
        rep = analyze(self._split())
        self.assertFalse({"1c", "1d", "2-blocked"} & set(rep.classes()), rep.classes())

    def test_blocked_wait_is_in_the_program(self):
        # rank 0 posts a receive nobody serves and is killed inside the wait
        t = mk({0: [irecv(1, 1, 0), {"op": "begin", "call": "wait", "kind": "wait", "reqs": [0]}],
                1: []})
        prog = dsl.program_from_trace(t)
        self.assertEqual(prog[0][-1], dsl.Wait("q0"))
        self.assertEqual(explore.explore(prog, None).kinds(), {"deadlock"})


class C_HorizonRules(unittest.TestCase):
    def test_rendezvous_send_completes_on_a_posted_receive(self):
        # rank 0 posts an Irecv for rank 1's 4000B message, takes a tag-1 message from ANY,
        # then waits. Rank 1 sends the 4000B message and a tag-1 message. The alternative
        # (the wildcard takes rank 1's tag-1 send instead of rank 2's) needs rank 1's first
        # send to complete at B=0: it does, the receive is posted, although its wait lies
        # beyond the horizon.
        t = mk({0: [irecv(1, 2, 0, count=1000), recv(T.ANY, 1, 2, 1), wait((0, 1, 2))],
                1: [send(0, 2, count=1000), send(0, 1)],
                2: [send(0, 1)]})
        alt = next(a for a in candidates_full(t)[0] if a.recv == (0, 1) and a.send == (1, 1))
        self.assertTrue(check(t, alt.M, alt.changed, 0, "L1").feasible)

    def test_probe_whose_message_is_never_issued_is_cut(self):
        # rank 1's wildcard takes rank 3's message instead of rank 2's; everything after it on
        # rank 1 is beyond the horizon, including the tag-3 send that rank 0's probe observed.
        t = mk({0: [{"op": "probe", "kind": "probe", "src": 1, "tag": 3, "comm": 0, "flag": 1, "msrc": 1, "mtag": 3}, recv(1, 3, 1, 3)],
                1: [recv(T.ANY, 1, 2, 1), send(0, 3)],
                2: [send(1, 1)], 3: [send(1, 1)]})
        alt = next(a for a in candidates_full(t)[0] if a.recv == (1, 0) and a.send == (3, 0))
        v = check(t, alt.M, alt.changed, None, "L1")
        self.assertTrue(v.feasible)
        self.assertEqual(v.cut[0], t.probes[(0, 0)].idx, "the probe and what follows it are undetermined")

    def test_pending_receive_takes_the_later_receives_message(self):
        # rank 0 posts an Irecv from ANY/ANY, then receives rank 2's tag-1 message with a
        # specific receive, then waits for rank 1's tag-2 message. Rank 1's wildcard takes
        # rank 4's message instead of rank 3's (stealing it from the specific receive that
        # follows), which cuts rank 1 before its send to rank 0: rank 0's Irecv is then
        # pending through the prefix and would take rank 2's message itself (the standard's
        # second non-overtaking clause), so the prefix ends at the specific receive, not at
        # the wait. The alternative stays feasible; the pair recv(0, 1) <- send(2, 0) is just
        # no longer certified.
        t = mk({0: [irecv(T.ANY, T.ANY, 0), recv(2, 1, 2, 1), wait((0, 1, 2))],
                1: [recv(T.ANY, 5, 3, 5, widx=0), recv(4, 5, 4, 5), send(0, 2)],
                2: [send(0, 1)], 3: [send(1, 5)], 4: [send(1, 5)]})
        alt = next(a for a in candidates_full(t)[0] if a.recv == (1, 0) and a.send == (4, 0))
        v = check(t, alt.M, alt.changed, None, "L1")
        self.assertTrue(v.feasible)
        self.assertEqual(v.cut[0], t.recvs[(0, 1)].idx, "the specific receive cannot get its message past the pending Irecv")
        self.assertEqual(v.cut[1], t.recvs[(1, 0)].idx + 1)
        fast_equals_generic(self, t)
        # the explorer agrees (rank 1 hangs on the stolen receive, so its send never comes and the
        # Irecv takes rank 2's message): no execution gives that message to the specific receive
        prog = dsl.program_from_trace(t)
        res = explore.explore(prog, None, force={(1, 0): (4, 0)}, max_states=20000, comms=t.comm_members)
        self.assertTrue(res.outcomes)
        self.assertFalse(any(dict(o.matching).get((0, 1)) == (2, 0) for o in res.outcomes))
        # with the Irecv completed before the specific receive, nothing is pending and the pair is certified
        t2 = mk({0: [irecv(T.ANY, T.ANY, 0), wait((0, 1, 2)), recv(2, 1, 2, 1)],
                 1: [recv(T.ANY, 5, 3, 5, widx=0), recv(4, 5, 4, 5), send(0, 2)],
                 2: [send(0, 1)], 3: [send(1, 5)], 4: [send(1, 5)]})
        alt2 = next(a for a in candidates_full(t2)[0] if a.recv == (1, 0) and a.send == (4, 0))
        v2 = check(t2, alt2.M, alt2.changed, None, "L1")
        self.assertTrue(v2.feasible)
        self.assertEqual(v2.cut[0], t2.recvs[(0, 0)].complete_idx, "rank 0 stops where the Irecv's message is missing")
        fast_equals_generic(self, t2)

    def test_unreceived_rendezvous_send_hangs_under_single_path(self):
        # a 4000B blocking send nobody receives completed in the run (eager); below 4000 bytes
        # it would wait for a match forever
        t = mk({0: [send(1, 1, count=1000)], 1: []})
        v = observed_feasible_at(t, 0)
        self.assertTrue(v.feasible, "the matching itself is realisable")
        self.assertEqual(list(v.hangs), [0])
        self.assertFalse(v.ok)
        self.assertTrue(observed_feasible_at(t, None).ok)
        rep = analyze(t, with_sp=False)
        f = rep.by_class("2-buffering")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].B, "B < 4000")


class C_UnrelatedCycle(unittest.TestCase):
    def test_interval_is_stated_over_realisable_thresholds(self):
        # ranks 0 and 1 exchange 400B messages head to head (a deadlock below 400 bytes);
        # rank 2's wildcard race with ranks 3 and 4 has nothing to do with it
        t = mk({0: [send(1, 1, count=100), recv(1, 1, 1, 1, count=100)],
                1: [send(0, 1, count=100), recv(0, 1, 0, 1, count=100)],
                2: [recv(T.ANY, 7, 3, 7), recv(T.ANY, 7, 4, 7)],
                3: [send(2, 7)], 4: [send(2, 7)]})
        rep = analyze(t, with_sp=False)
        self.assertEqual([f.B for f in rep.by_class("2-buffering")], ["B < 400"])
        race = [f for f in rep.findings if f.cls == "1f" and f.recv == (2, 0)]
        self.assertEqual(len(race), 1)
        self.assertEqual(race[0].B, "any B")
        self.assertTrue(any("left out: B in 0" in d for d in race[0].detail), race[0].detail)


class C_SinglePathBaseline(unittest.TestCase):
    """L1+SP consequences are compared with what the straight-line program does without
    the change; a deadlock or an orphan the observed run has anyway is not the
    alternative's. Each finding says whether every execution with the pair has the
    consequence or only some, and its pin realises a whole witness execution."""

    def test_observed_orphan_is_not_attributed(self):
        # rank 3's tag-9 message is never received; the race on rank 0 has nothing to do with it
        t = mk({0: [recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 2, 1, widx=1)],
                1: [send(0, 1)], 2: [send(0, 1)], 3: [send(0, 9)]})
        rep = analyze(t)
        self.assertEqual([f.level for f in rep.by_class("1c")], ["observed"])
        self.assertFalse(rep.by_class("1d"))

    def test_head_to_head_deadlock_is_not_attributed(self):
        t = mk({0: [send(1, 1, count=100), recv(1, 1, 1, 1, count=100)],
                1: [send(0, 1, count=100), recv(0, 1, 0, 1, count=100)],
                2: [recv(T.ANY, 7, 3, 7, widx=0), recv(T.ANY, 7, 4, 7, widx=1)],
                3: [send(2, 7)], 4: [send(2, 7)]})
        rep = analyze(t)
        self.assertEqual(set(rep.classes()), {"1f", "2-buffering"})

    def test_certain_deadlock_with_its_pin(self):
        # rank 0 takes ANY then specifically rank 2: if ANY takes rank 2's message, the
        # second receive can never complete, in every execution
        t = mk({0: [recv(T.ANY, 1, 1, 1, widx=0), recv(2, 1, 2, 1)], 1: [send(0, 1)], 2: [send(0, 1)]})
        rep = analyze(t)
        d = rep.by_class("1d")
        self.assertEqual([f.certainty for f in d], ["all"])
        self.assertEqual(d[0].pin, "STRETTO_PIN=0:0:2")
        c = rep.by_class("1c")
        self.assertEqual([(f.level, f.certainty) for f in c], [("L1+SP", "all")])

    def test_deadlock_in_some_executions_pins_the_witness(self):
        # after ANY takes rank 2's first message, the second ANY may take rank 1's (done) or
        # rank 2's second (then the specific receive from rank 2 starves)
        t = mk({0: [recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 2, 1, widx=1), recv(2, 1, 2, 1)],
                1: [send(0, 1)], 2: [send(0, 1), send(0, 1)]})
        rep = analyze(t)
        d = rep.by_class("1d")
        self.assertEqual([f.certainty for f in d], ["some"])
        self.assertEqual(d[0].pin, "STRETTO_PIN=0:0:2,0:1:2")


class C_ProbeAndCompletionOrder(unittest.TestCase):
    def _probe(self, src, tag, msrc, mtag, pidx=None):
        d = {"op": "probe", "kind": "probe", "src": 0 if src == T.ANY else src, "tag": tag, "comm": 0, "flag": 1,
             "msrc": msrc, "mtag": mtag}
        if src == T.ANY:
            d["anysrc"] = 1
        if pidx is not None:
            d["pidx"] = pidx
        return d

    def test_probed_message_must_be_issued_within_the_horizon(self):
        # rank 2 sends the alternative message only after it receives what rank 0 sends after
        # the probe; that send is beyond the horizon, so rank 2's receive never completes
        # within it and the message is never issued: the probe cannot have seen it
        t = mk({0: [self._probe(T.ANY, 1, 1, 1), send(2, 5), recv(T.ANY, 1, 1, 1), recv(2, 1, 2, 1)],
                1: [send(0, 1)], 2: [recv(0, 5, 0, 5), send(0, 1)]})
        p = t.probes[(0, 0)]
        v = check(t, t.observed, set(), None, "L1", changed_probes={p.key}, probe_alt={p.key: (2, 0)})
        self.assertFalse(v.feasible)
        self.assertIn("never issued within the horizon", v.reason)

    def test_probe_alternative_carries_its_threshold(self):
        # rank 2's tag-1 message follows a 4000B send that rank 0 receives only after the
        # probe: below 4000 bytes rank 2 is stuck in that send while rank 0 probes
        t = mk({0: [self._probe(T.ANY, 1, 1, 1, pidx=0), recv(2, 9, 2, 9, count=1000), recv(T.ANY, 1, 1, 1, widx=0), recv(2, 1, 2, 1)],
                1: [send(0, 1)], 2: [send(0, 9, count=1000), send(0, 1)]})
        rep = analyze(t, with_sp=False)
        alts = [f for f in rep.by_class("probe-alt") if "send(2, 1)" in f.title]
        self.assertEqual(len(alts), 1)
        self.assertEqual(alts[0].B, "B >= 4000")
        self.assertEqual(alts[0].pin, "STRETTO_PIN_PROBE=0:0:2")

    def test_waitsome_order_alternative(self):
        t = mk({0: [irecv(1, 1, 0), irecv(2, 1, 1), wait((0, 1, 1), kind="waitsome", reqs=[0, 1]), wait((1, 2, 1))],
                1: [send(0, 1)], 2: [send(0, 1)]})
        rep = analyze(t, with_sp=False)
        w = rep.by_class("waitany-order")
        self.assertEqual(len(w), 1)
        self.assertTrue(w[0].title.startswith("waitsome at rank 0"))
        self.assertEqual((w[0].B, w[0].pin, w[0].level), ("any B", None, "L1"))


class C_CancelAndMprobe(unittest.TestCase):
    def test_cancelled_send_is_not_a_candidate(self):
        # rank 2 cancels its send: the message is withdrawn, so the wildcard cannot take it
        t = mk({0: [recv(T.ANY, 1, 1, 1, widx=0)], 1: [send(0, 1)],
                2: [send(0, 1, mode="isend", req=0), {"op": "cancel", "req": 0}, wait((0, None, None))]})
        self.assertTrue(t.sends[(2, 0)].cancelled)
        self.assertEqual(candidates_full(t)[0], [])
        self.assertFalse(analyze(mk({0: [recv(T.ANY, 1, 1, 1)], 1: [send(0, 1)],
                                     2: [send(0, 1, mode="isend", req=0), {"op": "cancel", "req": 0}, wait((0, None, None))]}),
                                 with_sp=False).by_class("1c"))

    def test_cancelled_send_does_not_block_fifo(self):
        # rank 1's first send is cancelled; the wildcard's alternative is rank 1's second send
        t = mk({0: [recv(T.ANY, 1, 2, 1, widx=0), recv(1, 1, 1, 1)],
                1: [send(0, 1, mode="isend", req=0), {"op": "cancel", "req": 0}, wait((0, None, None)), send(0, 1)],
                2: [send(0, 1)]})
        alts = [(a.recv, a.send) for a in candidates_full(t)[0]]
        self.assertIn(((0, 0), (1, 1)), alts)
        alt = next(a for a in candidates_full(t)[0] if a.send == (1, 1))
        self.assertTrue(check(t, alt.M, alt.changed, None, "L1").feasible)

    def test_mprobe_removes_the_message_from_the_queue(self):
        # rank 0 mprobes rank 1's message, then posts a wildcard that rank 2 serves, then
        # mrecvs the probed message: the wildcard could not have taken rank 1's message
        mp = {"op": "probe", "kind": "mprobe", "src": 1, "tag": 1, "comm": 0, "flag": 1, "msrc": 1, "mtag": 1}
        t = mk({0: [mp, recv(T.ANY, 1, 2, 1, widx=0), {**recv(1, 1, 1, 1), "mrecv": 1}],
                1: [send(0, 1)], 2: [send(0, 1)]})
        self.assertEqual(t.observed, {(0, 0): (2, 0), (0, 1): (1, 0)})
        self.assertEqual(t.sends[(1, 0)].reserved_idx, t.probes[(0, 0)].idx)
        self.assertEqual(candidates_full(t)[0], [])


class C_TypeSignatures(unittest.TestCase):
    def test_contiguous_equals_repeated_basic(self):
        self.assertTrue(types.compatible("MPI_INT*4", 1, "MPI_INT", 4))
        self.assertTrue(types.compatible("MPI_INT", 2, "MPI_INT", 4))          # a shorter message fits
        self.assertTrue(types.compatible("MPI_INT*2,MPI_DOUBLE", 1, "MPI_INT,MPI_INT,MPI_DOUBLE*3", 1))

    def test_mismatch_and_unknown(self):
        self.assertFalse(types.compatible("MPI_INT*4", 1, "MPI_DOUBLE", 4))
        self.assertFalse(types.compatible("MPI_INT,MPI_DOUBLE", 1, "MPI_INT", 2))
        self.assertIsNone(types.compatible("derived:c3/s16/e16/lb0", 1, "MPI_INT", 4))
        self.assertIsNone(types.compatible(None, 1, "MPI_INT", 1))

    def test_classify_uses_signature_compatibility(self):
        # a contiguous(4, INT) sent as one element into four INTs is the same signature
        t = mk({0: [recv(T.ANY, 1, 1, 1, count=4, widx=0)], 1: [send(0, 1, count=4)],
                2: [{**send(0, 1, count=1), "tsize": 16, "tsig": "MPI_INT*4"}]})
        t.recvs[(0, 0)].tsig = "MPI_INT"; t.sends[(1, 0)].tsig = "MPI_INT"
        rep = analyze(t, with_sp=False)
        self.assertEqual([f.cls for f in rep.findings if f.send == (2, 0)], ["1f"])
        t.sends[(2, 0)].tsig = "MPI_DOUBLE*2"
        rep = analyze(t, with_sp=False)
        self.assertEqual([f.cls for f in rep.findings if f.send == (2, 0)], ["1a"])


class C_Kernel(unittest.TestCase):
    """The non-overtaking kernel enumerates exactly the alternatives the full enumerator
    would have accepted at some threshold."""

    def _accepted(self, t, enum):
        alts, _ = enum(t)
        out = set()
        for a in alts:
            for B in [0] + t.message_sizes() + [None]:
                if check(t, a.M, a.changed, B, "L1").feasible:
                    out.add((a.recv, a.send, a.kind))
                    break
        return out

    def _same(self, t):
        self.assertEqual(self._accepted(t, candidates_full), self._accepted(t, reference.candidates_full_slow))

    def test_master_worker_rounds(self):
        # two rounds from each of three workers: only the head of each queue is a candidate
        rounds = [(1, 2, 3), (2, 3, 1)]
        ops = []
        for rnd in rounds:
            for src in rnd:
                ops.append(recv(T.ANY, 1, src, 1, widx=len(ops)))
        t = mk({0: ops, 1: [send(0, 1), send(0, 1)], 2: [send(0, 1), send(0, 1)], 3: [send(0, 1), send(0, 1)]})
        self._same(t)
        self.assertLess(len(candidates_full(t)[0]), len(reference.candidates_full_slow(t)[0]))

    def test_tags_and_consumed_heads(self):
        t = mk({0: [recv(1, 1, 1, 1), recv(T.ANY, T.ANY, 2, 5, widx=0), recv(1, 2, 1, 2)],
                1: [send(0, 1), send(0, 2)], 2: [send(0, 5)]})
        self._same(t)


def fast_equals_generic(tc, t):
    """Every alternative of t judged by the fast checks and by the generic checker alike, at
    every threshold: the same verdict, the same cut when feasible, the same reason when not."""
    ix = t.fast_indexes()
    hb = fast.Observed(t, None, ix).reaches
    alts, _ = candidates_full(t, hb)
    probes = probe_candidates(t, hb)
    waits = _completion_alternatives(t, hb)
    for B in breakpoints(t) + [None]:
        ob = fast.Observed(t, B, ix)
        g, f = check(t, t.observed, set(), B, "SP", strong_only=True), ob.observed_verdict()
        tc.assertEqual((g.ok, g.feasible, g.cut, set(g.hangs)), (f.ok, f.feasible, f.cut, set(f.hangs)), f"observed at B={B}")
        for a in alts:
            f = ob.check_alternative(a)
            if f is None:
                continue
            g = check(t, a.M, a.changed, B, "L1")
            tc.assertEqual(g.feasible, f.feasible, f"{a.describe(t)} at B={B}: {g} vs {f}")
            tc.assertEqual(g.cut if g.feasible else g.reason, f.cut if f.feasible else f.reason, f"{a.describe(t)} at B={B}")
        for p, s in probes:
            g = check(t, t.observed, set(), B, "L1", changed_probes={p.key}, probe_alt={p.key: s.key})
            f = ob.check_probe(p, s)
            if f is None:
                continue
            tc.assertEqual((g.feasible, g.cut if g.feasible else None), (f.feasible, f.cut if f.feasible else None), f"{p} sees {s} at B={B}")
        for rec, j, r_j, s_j in waits:
            g = check(t, t.observed, set(), B, "L1", extra_edges=[(s_j.issue_node(), rec.complete)],
                      changed_events={(rec.rank, rec.idx)}, must_issue=[s_j.key])
            f = ob.check_event(rec.rank, rec.idx, s_j)
            if f is None:
                continue
            tc.assertEqual((g.feasible, g.cut if g.feasible else None), (f.feasible, f.cut if f.feasible else None), f"wait {rec.idx} at B={B}")


def closure_equals_fixpoint(tc, t):
    """The worklist closure (horizon.compute_cut) against the rescanning reference
    (compute_cut_fixpoint): same cut, same issued sends, same hang reasons per rank, for the
    observed matching in SP mode and for every candidate alternative and probe alternative in
    L1 mode, at every breakpoint."""
    from stretto.horizon import compute_cut, compute_cut_fixpoint
    alts = candidates_full(t)[0]
    probes = probe_candidates(t)
    for B in breakpoints(t) + [None]:
        items = [(t.observed, set(), "SP", {}, None)]
        items += [(a.M, a.changed, mode, {}, None) for a in alts for mode in ("L1", "SP")]
        items += [(t.observed, set(), "L1", {p.key: s.key}, p.key) for p, s in probes]
        for M, changed, mode, palt, pk in items:
            res = []
            for f in (compute_cut, compute_cut_fixpoint):
                issued, hangs = {}, {}
                cut = f(t, M, set(changed), B, mode, {pk} if pk else set(), hangs, set(), issued, palt)
                res.append((cut, issued, set(hangs)))
            tc.assertEqual(res[0], res[1], f"B={B} mode={mode} changed={changed} probe={pk}")


class C_FastEqualsGeneric(unittest.TestCase):
    def test_traces_of_this_module(self):
        # every in-memory trace the other tests build, re-created here
        mp = {"op": "probe", "kind": "mprobe", "src": 1, "tag": 1, "comm": 0, "flag": 1, "msrc": 1, "mtag": 1}
        pr = {"op": "probe", "kind": "probe", "src": 0, "anysrc": 1, "tag": 1, "comm": 0, "flag": 1, "msrc": 1, "mtag": 1}
        traces = [
            mk({0: [irecv(1, 2, 0, count=1000), recv(T.ANY, 1, 2, 1), wait((0, 1, 2))], 1: [send(0, 2, count=1000), send(0, 1)], 2: [send(0, 1)]}),
            mk({0: [send(1, 1, count=100), recv(1, 1, 1, 1, count=100)], 1: [send(0, 1, count=100), recv(0, 1, 0, 1, count=100)],
                2: [recv(T.ANY, 7, 3, 7, widx=0), recv(T.ANY, 7, 4, 7, widx=1)], 3: [send(2, 7)], 4: [send(2, 7)]}),
            mk({0: [recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 2, 1, widx=1), recv(2, 1, 2, 1)], 1: [send(0, 1)], 2: [send(0, 1), send(0, 1)]}),
            mk({0: [recv(T.ANY, 1, 1, 1, widx=0), recv(2, 1, 2, 1)], 1: [send(0, 1)], 2: [send(0, 1)], 3: [send(0, 9)]}),
            mk({0: [pr, recv(2, 9, 2, 9, count=1000), recv(T.ANY, 1, 1, 1, widx=0), recv(2, 1, 2, 1)], 1: [send(0, 1)], 2: [send(0, 9, count=1000), send(0, 1)]}),
            mk({0: [irecv(1, 1, 0), irecv(2, 1, 1), wait((0, 1, 1), kind="waitany", reqs=[0, 1]), wait((1, 2, 1))], 1: [send(0, 1)], 2: [send(0, 1)]}),
            mk({0: [mp, recv(T.ANY, 1, 2, 1, widx=0), {**recv(1, 1, 1, 1), "mrecv": 1}], 1: [send(0, 1)], 2: [send(0, 1)]}),
            mk({0: [send(1, 1, count=1000)], 1: []}),
            mk({0: [recv(T.ANY, 1, 1, 1, widx=0), coll("barrier"), recv(2, 1, 2, 1)], 1: [send(0, 1), coll("barrier")], 2: [coll("barrier"), send(0, 1)]}),
            mk({0: [recv(T.ANY, 1, 1, 1, widx=0), coll("bcast", root=0), recv(T.ANY, 1, 2, 1, widx=1)], 1: [send(0, 1), coll("bcast", root=0)], 2: [coll("bcast", root=0), send(0, 1)]}),
            mk({0: [recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 2, 1, widx=1), recv(T.ANY, 1, 1, 1, widx=2)],
                1: [send(0, 1), send(0, 1, mode="ssend")], 2: [send(0, 1, count=2000)]}),
        ]
        for i, t in enumerate(traces):
            with self.subTest(trace=i):
                fast_equals_generic(self, t)
                closure_equals_fixpoint(self, t)


class C_MatchNodes(unittest.TestCase):
    """The audit's counterexamples to T1 (20 Oct 2026). Matching order binds the *match* of a
    later receive, not its completion: with the order edges attached to completions, a
    pending nonblocking receive could be overtaken (first case) and a rendezvous send could be
    matched after its own completion (second case)."""

    def stale_order_trace(self):
        # rank 0: Irecv r(ANY); Recv r3(ANY) <- 3; Send x -> 2; Wait r <- 1.
        # rank 2: Recv x; Send sC -> 0 (never received in the run).  ranks 1, 3: one send each.
        return mk({
            0: [irecv(T.ANY, 0, 10), recv(T.ANY, 0, 3, 0), send(2, 5), wait((10, 1, 0))],
            1: [send(0, 0)],
            2: [recv(0, 5, 0, 5), send(0, 0)],
            3: [send(0, 0)],
        })

    def test_pending_receive_is_not_overtaken(self):
        """r <- sC needs sC before r3 is matched (r is posted before r3 and accepts r3's
        message), and sC needs x, which needs r3 complete: a cycle. Before the fix the edge
        into r3 came from r's observed send and the steal was accepted."""
        t = self.stale_order_trace()
        alts = [a for a in candidates_full(t)[0] if a.recv == (0, 0) and a.send == (2, 0)]
        self.assertEqual(len(alts), 1, [a.describe(t) for a in candidates_full(t)[0]])
        a = alts[0]
        for B in (0, None):
            g = check(t, a.M, a.changed, B, "L1")
            self.assertFalse(g.feasible, f"generic at B={B}: {g.reason}")
            f = fast.Observed(t, B).check_alternative(a)
            if f is not None:
                self.assertFalse(f.feasible, f"fast at B={B}: {f.reason}")

    def test_pending_receive_case_in_the_report(self):
        t = self.stale_order_trace()
        rep = analyze(t, with_sp=False)
        self.assertNotIn(((0, 0), (2, 0)), [(f.recv, f.send) for f in rep.findings])

    def rendezvous_order_trace(self):
        # rank 0: Irecv r1(ANY); Irecv r2(ANY); Waitall (r1 <- 1, r2 <- 2).
        # rank 2: Send sC -> 0 (4000 B); Send x -> 1.  rank 1: Recv x; Send sB -> 0.
        return mk({
            0: [irecv(T.ANY, 0, 10), irecv(T.ANY, 0, 11), wait((10, 1, 0), (11, 2, 0), kind="waitall")],
            1: [recv(2, 5, 2, 5), send(0, 0)],
            2: [send(0, 0, count=1000), send(1, 5)],
        })

    def test_rendezvous_send_is_matched_before_it_completes(self):
        """At B = 0 sC completes only once matched, and it is matched while r1 is still
        pending (sB needs x, which needs sC complete), so r1 would take it: the recorded run
        is realisable only for B >= 4000. Before the fix the order edge ended at the Waitall
        and B = 0 was accepted."""
        t = self.rendezvous_order_trace()
        self.assertFalse(observed_feasible_at(t, 0).feasible)
        self.assertFalse(observed_feasible_at(t, 3999).feasible)
        self.assertTrue(observed_feasible_at(t, 4000).feasible)
        self.assertTrue(observed_feasible_at(t, None).feasible)
        ob = fast.Observed(t, 0)
        self.assertFalse(ob.observed_verdict().feasible)
        rep = analyze(t, with_sp=False)
        self.assertEqual([f.B for f in rep.by_class("2-buffering")], ["B < 4000"])

    def test_both_in_the_fast_equals_generic_set(self):
        for t in (self.stale_order_trace(), self.rendezvous_order_trace()):
            fast_equals_generic(self, t)
            closure_equals_fixpoint(self, t)


class C_ProbesReadyRoots(unittest.TestCase):
    """Rules the audit of 20 Oct 2026 found missing (docs/model.md §2b, gap c)."""

    def test_rootless_bcast_synchronises_everyone(self):
        # without a recorded root a root-shaped collective is a barrier in the graph too, as it
        # is in the horizon and in the explorer
        t = mk({0: [send(1, 1, 250), coll("bcast")],
                1: [coll("bcast"), recv(0, 1, 0, 1, 250)]})
        self.assertIsNone(t.colls[0].root)
        self.assertFalse(observed_feasible_at(t, 0).feasible)
        self.assertFalse(fast.Observed(t, 0).observed_verdict().feasible)
        self.assertTrue(observed_feasible_at(t, None).feasible)

    def probe_trace(self):
        pr = {"op": "probe", "kind": "probe", "src": 0, "anysrc": 1, "tag": 1, "comm": 0, "flag": 1, "msrc": 3, "mtag": 1}
        return mk({0: [irecv(T.ANY, 1, 10), irecv(T.ANY, 1, 11), pr, recv(3, 1, 3, 1),
                       wait((10, 1, 1), (11, 2, 1), kind="waitall")],
                   1: [send(0, 1)], 2: [send(0, 1)], 3: [send(0, 1)]})

    def test_pending_receive_deprives_a_later_probe(self):
        """r <- s2 leaves the second Irecv without a message; it stays pending and would take
        rank 3's message, so the probe after it cannot report that message: rule 6 cuts at
        the probe, not only at the receive after it."""
        t = self.probe_trace()
        alts = [a for a in candidates_full(t)[0] if a.recv == (0, 0) and a.send == (2, 0) and a.kind == "steal"]
        self.assertEqual(len(alts), 1)
        a = alts[0]
        probe_idx = next(p.idx for p in t.probes.values())
        for B in (0, None):
            v = check(t, a.M, a.changed, B, "L1")
            self.assertTrue(v.feasible, v.reason)
            self.assertEqual(v.cut[0], probe_idx)
        fast_equals_generic(self, t)

    def test_ready_send_without_its_receive_is_cut(self):
        """Stealing the second sender's message for rank 1's first receive leaves rank 0's ready
        send without a posted receive inside the horizon: rule 7 cuts it."""
        t = mk({0: [send(1, 1, mode="rsend")], 2: [send(1, 1)],
                1: [recv(T.ANY, 1, 0, 1, widx=0), recv(T.ANY, 1, 2, 1, widx=1)]})
        alts = [a for a in candidates_full(t)[0] if a.recv == (1, 0) and a.send == (2, 0) and a.kind == "steal"]
        self.assertEqual(len(alts), 1)
        v = check(t, alts[0].M, alts[0].changed, None, "L1")
        self.assertTrue(v.feasible, v.reason)
        self.assertEqual(v.cut[0], t.sends[(0, 0)].idx)
        fast_equals_generic(self, t)


class C_MonotoneAboveB0(unittest.TestCase):
    def test_profile_dips_below_the_observed_threshold(self):
        """Theorem T2 holds from B0 on, the least threshold at which the recorded run is
        realisable, and not below it (audit of 20 Oct 2026). Rank 3 sends 148 bytes to rank 0
        after the changed receive, then exchanges 400 bytes head to head with rank 4. Below
        148 the cut of rank 3 removes the exchange; at 148 the exchange is kept and cyclic;
        from 400 on it is buffered. The report bisects from B0 = 400 and states the interval
        over the realisable thresholds only."""
        t = mk({0: [recv(T.ANY, 1, 2, 1, widx=0), recv(3, 2, 3, 2, count=37), recv(1, 1, 1, 1)],
                1: [send(0, 1)], 2: [send(0, 1)],
                3: [send(0, 2, count=37), send(4, 3, count=100), recv(4, 3, 4, 3, count=100)],
                4: [send(3, 3, count=100), recv(3, 3, 3, 3, count=100)]})
        self.assertEqual([B for B in (0, 4, 148, 400, None) if observed_feasible_at(t, B).feasible], [400, None])
        a = next(a for a in candidates_full(t)[0] if a.recv == (0, 0) and a.kind == "steal")
        full = feasible_profile(t, a.M, a.changed, full=True)
        self.assertEqual(full, {0: True, 4: True, 148: False, 400: True, None: True})
        self.assertEqual(feasible_profile(t, a.M, a.changed, full=False, floor=400), {0: False, 4: False, 148: False, 400: True, None: True})
        rep = analyze(t, with_sp=False)
        f = next(f for f in rep.findings if f.recv == (0, 0))
        self.assertEqual(f.B, "any B")
        self.assertTrue(any("left out: B in 0,4,148" in d for d in f.detail), f.detail)

    def test_probe_report_raises_the_floor(self):
        """B0 is taken over every edge of the observed graph, the probes' reports included,
        and not over the guaranteed edges that decide 2-buffering (proof appendix,
        conventions). Rank 0 probes for rank 1's tag-2 message, then receives rank 1's
        400-byte tag-1 message and the probed one. Below 400 bytes the tag-1 send waits for
        a receive posted after the probe returns, and the probe needs the tag-2 message
        that follows that send: the recorded run is realisable only from 400 on. Without
        the probe's report it need not deadlock, so there is no 2-buffering finding, but
        the race on rank 2 is stated from 400 on, where theorem T2 holds."""
        pr = {"op": "probe", "kind": "probe", "src": 1, "tag": 2, "comm": 0, "flag": 1, "msrc": 1, "mtag": 2}
        t = mk({0: [pr, recv(1, 1, 1, 1, count=100), recv(1, 2, 1, 2)],
                1: [send(0, 1, count=100), send(0, 2)],
                2: [recv(T.ANY, 7, 3, 7, widx=0), recv(T.ANY, 7, 4, 7, widx=1)],
                3: [send(2, 7)], 4: [send(2, 7)]})
        bps = (0, 4, 400, None)
        self.assertEqual([B for B in bps if observed_feasible_at(t, B).ok], [0, 4, 400, None])
        self.assertEqual([B for B in bps if observed_feasible_at(t, B, strong_only=False).ok], [400, None])
        for generic in (False, True):
            rep = analyze(t, with_sp=False, generic=generic)
            self.assertEqual(rep.by_class("2-buffering"), [])
            f = next(f for f in rep.findings if f.recv == (2, 0))
            self.assertEqual(f.B, "any B")
            self.assertTrue(any("not realisable are left out: B in 0,4" in d for d in f.detail), f.detail)
            self.assertTrue(any("realisable only from B=400" in n for n in rep.notes), rep.notes)
        fast_equals_generic(self, t)


class C_CompletenessSteps(unittest.TestCase):
    """Conservative steps of the implementation removed so that it meets theorem T6
    (completeness relative to the horizon; proof appendix, steps 1, 4 and 5)."""

    def test_obligations_only_for_judged_receives(self):
        """The first witness of the monotonicity appendix. Rank 0: Irecv r(ANY), Irecv r'(from
        1), Wait r, Wait r', Recv r3(ANY); rank 1 sends s_o then s_1, rank 2 sends s. The steal
        r <- s releases s_o. At B = 0, s_o blocks and stays issued, s_1 is never issued, so r'
        is posted but not judged and its obligation concerns a match outside the horizon: the
        steal is realisable there (rank 0 posts r and r', r takes s, Wait r returns). From
        B = 4 on, s_1 is issued, r' is judged, and r' accepts the unmatched s_o sent before
        s_1: infeasible. Checking r' at B = 0 too used to reject a realisable alternative."""
        t = mk({0: [irecv(T.ANY, 1, 10), irecv(1, 1, 11), wait((10, 1, 1)), wait((11, 1, 1)), recv(T.ANY, 1, 2, 1, widx=1)],
                1: [send(0, 1), send(0, 1)], 2: [send(0, 1)]})
        self.assertTrue(observed_feasible_at(t, 0).feasible)
        a = next(a for a in candidates_full(t)[0] if a.recv == (0, 0) and a.send == (2, 0) and a.kind == "steal")
        self.assertTrue(check(t, a.M, a.changed, 0, "L1").feasible, check(t, a.M, a.changed, 0, "L1").reason)
        v = check(t, a.M, a.changed, None, "L1")
        self.assertFalse(v.feasible)
        self.assertIn("non-overtaking", v.reason)
        self.assertEqual(feasible_profile(t, a.M, a.changed, full=True), {0: True, 4: False, None: False})
        rep = analyze(t, with_sp=False)
        self.assertIn(((0, 0), (2, 0)), [(f.recv, f.send) for f in rep.findings])

    def probe_on_blocked_send_trace(self):
        # rank 0: Probe(from 1) sees s1; Recv r(ANY) <- s1; Recv(ANY) <- s2. ranks 1, 2: one send each
        pr = {"op": "probe", "kind": "probe", "src": 1, "tag": 1, "comm": 0, "flag": 1, "msrc": 1, "mtag": 1}
        return mk({0: [pr, recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 2, 1, widx=1)],
                   1: [send(0, 1)], 2: [send(0, 1)]})

    def test_probe_sees_a_send_that_is_issued_but_blocked(self):
        """r <- s2 leaves s1 without a receive. At B = 0 rule 3 removes the completion of s1 but
        keeps its call, so the probe that saw s1 can still see it (rule 5 asks for the send to
        be issued, not completed). The probe used to be removed with s1's record, which cut
        rank 0 before r and rejected the steal at B = 0."""
        t = self.probe_on_blocked_send_trace()
        a = next(a for a in candidates_full(t)[0] if a.recv == (0, 0) and a.send == (2, 0) and a.kind == "steal")
        for B in (0, None):
            v = check(t, a.M, a.changed, B, "L1")
            self.assertTrue(v.feasible, f"B={B}: {v.reason}")
            f = fast.Observed(t, B).check_alternative(a)
            self.assertIsNotNone(f)
            self.assertTrue(f.feasible, f"fast at B={B}: {f.reason}")
        fast_equals_generic(self, t)
        closure_equals_fixpoint(self, t)

    def test_fast_check_defers_when_the_observed_graph_is_cyclic(self):
        """Below the recorded run's own threshold the observed graph has a cycle; it need not
        lie in an alternative's horizon, so the fast check leaves the verdict to the generic
        one instead of rejecting (the trace of C_MonotoneAboveB0: feasible at B = 0)."""
        t = mk({0: [recv(T.ANY, 1, 2, 1, widx=0), recv(3, 2, 3, 2, count=37), recv(1, 1, 1, 1)],
                1: [send(0, 1)], 2: [send(0, 1)],
                3: [send(0, 2, count=37), send(4, 3, count=100), recv(4, 3, 4, 3, count=100)],
                4: [send(3, 3, count=100), recv(3, 3, 3, 3, count=100)]})
        a = next(a for a in candidates_full(t)[0] if a.recv == (0, 0) and a.kind == "steal")
        self.assertFalse(fast.Observed(t, 0).acyclic())
        self.assertIsNone(fast.Observed(t, 0).check_alternative(a))
        self.assertTrue(check(t, a.M, a.changed, 0, "L1").feasible)


class C_CallSites(unittest.TestCase):
    def test_findings_grouped_by_the_site_of_the_changed_receive(self):
        """The tracer writes a site record the first time a call site appears and tags later
        records with its id. Processes load the program at different addresses, so a site is
        its object and its offset from the load address; the loader keeps the table out of
        the events, and the report counts findings per site, not per rank and sender."""
        site = lambda base: {"op": "site", "id": 0, "obj": "/opt/app/solver", "addr": base + 0x1a2b, "base": base}
        rw = lambda msrc: dict(recv(T.ANY, 1, msrc, 1, widx=0), site=0)
        t = mk({0: [site(0x5000), rw(2)], 1: [site(0x9000), rw(3)],
                2: [send(0, 1), send(1, 1)], 3: [send(0, 1), send(1, 1)]})
        self.assertEqual(len(t.sites), 2)
        self.assertNotIn("site", [r.op for recs in t.ranks.values() for r in recs])
        self.assertEqual(t.site_label(0, t.recvs[(0, 0)].idx), "solver+0x1a2b")
        self.assertEqual(t.site_of(1, t.recvs[(1, 0)].idx), ("/opt/app/solver", 0x1a2b))
        rep = analyze(t, with_sp=False)
        changed = [f for f in rep.findings if f.recv is not None and f.level == "L1"]
        self.assertTrue(changed)
        self.assertEqual({f.site for f in changed}, {"solver+0x1a2b"})
        self.assertEqual([s for s, _ in rep.by_site()], ["solver+0x1a2b"])
        self.assertEqual(sum(rep.by_site()[0][1].values()), len([f for f in rep.findings if f.site]))


class C_Unmodelled(unittest.TestCase):
    def test_unmodelled_call_keeps_the_report_to_observed_findings(self):
        """A call outside the modelled fragment means the trace is not a complete record of the
        run's communication (the premise of T1): no alternative is predicted."""
        t = mk({0: [recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 2, 1, widx=1)], 1: [send(0, 1)],
                2: [{"op": "unmodelled", "call": "Win_create"}, send(0, 1)]})
        self.assertEqual(t.unmodelled, [(2, "Win_create")])
        rep = analyze(t, with_sp=False)
        self.assertEqual([f for f in rep.findings if f.level != "observed"], [])
        self.assertTrue(any("outside the modelled fragment" in n for n in rep.notes), rep.notes)


class C_ContainedDeadlock(unittest.TestCase):
    """Theorem T7: a deadlock contained in the horizon is certified at L1, without the
    single-path assumption, when a changed rank stops before its changed receive completes."""

    def test_nonblocking_wildcard_then_specific_receive(self):
        # rank 0: Irecv r(ANY); Recv r3 from 1; Wait r. Ranks 1 and 2 send once each; in the
        # run r took rank 2's message. If r takes rank 1's, r3 has nothing left to take and rank
        # 0 stops before its wait, whatever the program does after r: a deadlock at L1.
        t = mk({0: [irecv(T.ANY, 1, 10), recv(1, 1, 1, 1), wait((10, 2, 1))],
                1: [send(0, 1)], 2: [send(0, 1)]})
        rep = analyze(t, with_sp=True)
        l1d = [f for f in rep.findings if f.cls == "1d" and f.level == "L1"]
        self.assertEqual([(f.recv, f.send) for f in l1d], [((0, 0), (1, 0))], [str(f) for f in rep.findings])
        self.assertIn("rank 0 stops at recv", l1d[0].detail[0])
        # ground truth: every execution of the straight-line program with that match deadlocks
        prog = dsl.program_from_trace(t)
        for B in (0, None):
            res = explore.explore(prog, B, force={(0, 0): (1, 0)}, comms=t.comm_members)
            outs = res.outcomes_with((0, 0), (1, 0))
            self.assertTrue(outs and all(o.kind == "deadlock" for o in outs), [str(o) for o in outs])

    def test_blocking_wildcard_is_not_contained(self):
        # the same with a blocking wildcard: rank 0 is cut right after it (rule 1), so its next
        # receive is beyond the horizon and the deadlock is a single-path claim only
        t = mk({0: [recv(T.ANY, 1, 2, 1, widx=0), recv(1, 1, 1, 1)], 1: [send(0, 1)], 2: [send(0, 1)]})
        rep = analyze(t, with_sp=True)
        self.assertEqual([f for f in rep.findings if f.cls == "1d" and f.level == "L1"], [])
        self.assertTrue([f for f in rep.findings if f.cls == "1d" and f.level == "L1+SP"])

    def test_a_later_message_of_the_partner_breaks_it(self):
        # rank 1 sends a second message, which the stranded receive can take
        t = mk({0: [irecv(T.ANY, 1, 10), recv(1, 1, 1, 1), wait((10, 2, 1)), recv(1, 1, 1, 1)],
                1: [send(0, 1), send(0, 1)], 2: [send(0, 1)]})
        rep = analyze(t, with_sp=False)
        self.assertEqual([f for f in rep.findings if f.cls == "1d" and f.level == "L1"], [])


class C_LoaderRejectsDamagedTraces(unittest.TestCase):
    def write(self, d, rank, lines):
        with open(os.path.join(d, f"rank-{rank}.jsonl"), "w") as fh:
            fh.write("\n".join(lines) + "\n")

    def test_gaps_bad_lines_and_mixed_runs(self):
        import json
        import tempfile
        ok = [{"r": 0, "seq": 0, "op": "init", "np": 2}, {"r": 0, "seq": 1, "op": "finalize"}]
        one = [{"r": 1, "seq": 0, "op": "init", "np": 2}, {"r": 1, "seq": 1, "op": "finalize"}]
        cases = {
            "gap": ([{"r": 0, "seq": 0, "op": "init", "np": 2}, {"r": 0, "seq": 2, "op": "finalize"}], one, "missing"),
            "wrong rank": ([{"r": 1, "seq": 0, "op": "init", "np": 2}, {"r": 1, "seq": 1, "op": "finalize"}], one, "belongs"),
            "np": (ok, [{"r": 1, "seq": 0, "op": "init", "np": 4}, {"r": 1, "seq": 1, "op": "finalize"}], "disagree"),
        }
        for name, (r0, r1, msg) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as d:
                self.write(d, 0, [json.dumps(x) for x in r0])
                self.write(d, 1, [json.dumps(x) for x in r1])
                with self.assertRaisesRegex(ValueError, msg):
                    T.load(d)
        with tempfile.TemporaryDirectory() as d:
            self.write(d, 0, [json.dumps(ok[0]), "{not json", json.dumps(ok[1])])
            self.write(d, 1, [json.dumps(x) for x in one])
            with self.assertRaisesRegex(ValueError, "unparseable"):
                T.load(d)
        with tempfile.TemporaryDirectory() as d:          # a partial last line is the killed rank's: tolerated
            self.write(d, 0, [json.dumps(ok[0]), '{"r": 0, "seq": 1, "op": "fin'])
            self.write(d, 1, [json.dumps(x) for x in one])
            t = T.load(d)
            self.assertIn(0, t.truncated)


class C_TracerRecords(unittest.TestCase):
    """Loader behaviour for the records the tracer writes besides point-to-point."""

    def test_begin_pairs_with_the_same_thread(self):
        # thread 1 is inside a blocking receive while thread 2 sends: the begin is not blocked
        t = mk({0: [{"op": "begin", "call": "recv", "src": 1, "tag": 1, "comm": 0, "count": 1, "tsize": 4, "tid": 1},
                    {**send(2, 9), "tid": 2}, {**recv(1, 1, 1, 1), "tid": 1}],
                1: [send(0, 1)], 2: [recv(0, 9, 0, 9)]})
        self.assertEqual(t.blocked_calls(), [])
        self.assertEqual(t.threads[0], 2)

    def test_side_record_inside_a_wait_is_not_a_blocked_call(self):
        # an Idup completed by a Waitall registers its communicator between the begin and the
        # wait record; that is not the wait being blocked
        t = mk({0: [irecv(1, 1, 10),
                    {"op": "begin", "call": "wait", "kind": "waitall", "reqs": [10, 11]},
                    {"op": "comm", "name": "idup", "parent": 0, "id": 2, "ranks": [0, 1], "gord": 1},
                    wait((10, 1, 1), kind="waitall")],
                1: [send(0, 1)]})
        self.assertEqual(t.blocked_calls(), [])

    def test_thread_multiple_gives_observed_findings_only(self):
        t = mk({0: [{"op": "init", "np": 3, "thread": 3}, {**recv(T.ANY, 1, 1, 1, widx=0), "tid": 1}, {**recv(T.ANY, 1, 2, 1, widx=1), "tid": 2}],
                1: [send(0, 1)], 2: [send(0, 1)]})
        rep = analyze(t)
        self.assertFalse(rep.by_class("1f"))
        self.assertTrue(any("THREAD_MULTIPLE" in n for n in rep.notes))

    def test_freed_receive_takes_its_message_unobserved(self):
        t = mk({0: [irecv(1, 1, 0), {"op": "reqfree", "req": 0}], 1: [send(0, 1)]})
        self.assertTrue(t.recvs[(0, 0)].freed)
        self.assertFalse(analyze(t, with_sp=False).by_class("1c"))

    def test_cancelled_send_status_is_authoritative(self):
        t = mk({0: [send(1, 1, mode="isend", req=0), {"op": "cancel", "req": 0}, {"op": "wait", "kind": "wait", "reqs": [0], "done": [{"req": 0, "cancelled": 1}]}],
                1: []})
        self.assertTrue(t.sends[(0, 0)].cancelled)
        t = mk({0: [send(1, 1, mode="isend", req=0), {"op": "cancel", "req": 0}, {"op": "wait", "kind": "wait", "reqs": [0], "done": [{"req": 0, "cancelled": 0}]}],
                1: [recv(0, 1, 0, 1)]})
        self.assertFalse(t.sends[(0, 0)].cancelled, "the cancel failed: the message was delivered")
        t = mk({0: [send(1, 1, mode="isend", req=0), {"op": "cancel", "req": 0}, {"op": "wait", "kind": "wait", "reqs": [0], "done": [{"req": 0}]}],
                1: []})
        self.assertTrue(t.sends[(0, 0)].cancelled, "an older trace without the flag: the cancel record stands")

    def test_intercommunicator_peers_are_the_remote_group(self):
        # ranks {0,1} and {2,3} joined by an intercommunicator: rank 0 sends to remote rank 1 (= world 3)
        left = {"op": "comm", "name": "intercomm", "parent": 0, "id": 2, "inter": 1, "ranks": [2, 3], "local": [0, 1], "members": [0, 1, 2, 3], "gord": 0}
        right = {"op": "comm", "name": "intercomm", "parent": 0, "id": 2, "inter": 1, "ranks": [0, 1], "local": [2, 3], "members": [0, 1, 2, 3], "gord": 0}
        t = mk({0: [left, send(1, 1, comm=2)], 1: [left], 2: [right], 3: [right, recv(0, 1, 0, 1, comm=2)]})
        s = t.sends[(0, 0)]
        self.assertEqual(s.dst, 3)
        self.assertEqual(t.observed, {(3, 0): (0, 0)})
        self.assertEqual(t.comm_members[s.comm], [0, 1, 2, 3])

    def test_overflow_and_getstatus(self):
        t = mk({0: [{"op": "overflow", "what": "message table"}, irecv(1, 1, 0),
                    {"op": "wait", "kind": "getstatus", "flag": 1, "reqs": [0], "done": []}, wait((0, 1, 1))],
                1: [send(0, 1)]})
        self.assertTrue(any("overflowed" in w for w in t.warnings))
        self.assertEqual(t.observed, {(0, 0): (1, 0)})


class C_TraceFromExecution(unittest.TestCase):
    def test_round_trip_keeps_the_matching(self):
        rng = __import__("random").Random(7)
        n = 0
        for i in range(150):
            prog, comms = dsl.random_program(rng, rng.choice([2, 3]), branches=False, waitany=False)
            res = explore.explore(prog, None, comms=comms, max_states=200_000)
            done = [o for o in res.outcomes if o.kind == "done" and not o.leaked]
            if not done:
                continue
            o = done[0]
            raw = dsl.trace_from_execution(prog, dict(o.matching), comms)
            t = T.Trace()
            T._build(t, raw)
            self.assertEqual(t.observed, dict(o.matching), f"program {i}")
            self.assertEqual(len(dsl.program_from_trace(t)), len(prog))
            n += 1
        self.assertGreaterEqual(n, 5)   # most random programs deadlock or leak a receive


class C_HorizonCollectiveShapes(unittest.TestCase):
    def test_bcast_root_sends_after_the_bcast(self):
        # rank 0 broadcasts (root) and then sends; rank 1's wildcard takes rank 2's message
        # in the run and rank 0's in the alternative, which cuts rank 1 before its bcast: the
        # root returns from a bcast regardless, so rank 0's later send is issued and the
        # alternative is feasible; with a barrier instead it is not
        def mk_t(name):
            return mk({0: [coll(name, root=0), send(1, 1)],
                       1: [recv(T.ANY, 1, 2, 1, widx=0), coll(name, root=0), recv(0, 1, 0, 1)],
                       2: [send(1, 1), coll(name, root=0)]})
        t = mk_t("bcast")
        alt = next(a for a in candidates_full(t)[0] if a.recv == (1, 0) and a.send == (0, 0))
        self.assertTrue(check(t, alt.M, alt.changed, None, "L1").feasible)
        fast_equals_generic(self, t)
        t = mk_t("barrier")
        alts, pruned = candidates_full(t)
        self.assertFalse([a for a in alts if a.send == (0, 0)], "rank 0's send follows the barrier, which follows the receive")
        self.assertTrue(any("causally follows" in why for _, why in pruned))


class C_TwoChanges(unittest.TestCase):
    def test_two_ranks_two_steals(self):
        # ranks 0 and 1 each receive from ANY twice; each has one steal alternative; the pair of
        # steals is a two-change alternative, feasible at every B
        t = mk({0: [recv(T.ANY, 1, 2, 1, widx=0), recv(T.ANY, 1, 3, 1, widx=1)],
                1: [recv(T.ANY, 2, 2, 2, widx=0), recv(T.ANY, 2, 3, 2, widx=1)],
                2: [send(0, 1), send(1, 2)], 3: [send(0, 1), send(1, 2)]})
        from stretto.matching import candidates_k
        ks = candidates_k(t, 2)
        self.assertEqual(len(ks), 1)
        self.assertTrue(check(t, ks[0].M, ks[0].changed, None, "L1").feasible)
        rep = analyze(t, with_sp=False, changes=2)
        self.assertTrue([f for f in rep.findings if f.title.startswith("two changes")])


if __name__ == "__main__":
    unittest.main()
