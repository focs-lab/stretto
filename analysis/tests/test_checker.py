"""Checker regression tests for the A.1 audit findings. Traces are built in memory from
raw records in the tracer's JSON format (new encoding: anysrc/anytag/procnull flags)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest

from stretto import dsl, explore, fast, trace as T, types
from stretto.feasible import breakpoints
from stretto.matching import Overlay, build_graph, observed_hb, probe_candidates
from stretto.report import _completion_alternatives
from stretto.feasible import check, feasible_profile, observed_feasible_at, profile_summary
from stretto.matching import candidates_full
from stretto.report import analyze, classify
import reference


def mk(raw_ranks, np=None, version=None):
    """version 2: the trace format in which every member of a communicator creation records it,
    MPI_COMM_NULL results included (tracer since 22 Oct 2026)."""
    t = T.Trace()
    np = np or len(raw_ranks)
    init = {"op": "init", "np": np, **({"version": version} if version else {})}
    for rank in raw_ranks:
        raw_ranks[rank] = [dict(init)] + raw_ranks[rank] + [{"op": "finalize"}]
    T._build(t, raw_ranks)
    return t


def comm(name, ranks, id=2, parent=0, gord=1, **kw):
    """A communicator creation record; ranks None: this member obtained MPI_COMM_NULL."""
    if ranks is None:
        return {"op": "comm", "name": name, "parent": parent, "id": -1, **kw}
    return {"op": "comm", "name": name, "parent": parent, "id": id, "ranks": list(ranks), "gord": gord, **kw}


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


    def test_procnull_probe_is_not_a_wildcard(self):
        # a probe of MPI_PROC_NULL returns at once: it was loaded as an ANY_SOURCE probe, which
        # a contained deadlock would treat as waiting for any message
        p = {"op": "probe", "kind": "probe", "src": -1, "procnull": 1, "tag": -1, "anytag": 1, "comm": 0, "flag": 1,
             "msrc": -2, "mtag": -1, "mbytes": 0}
        t = mk({0: [p, recv(1, 1, 1, 1)], 1: [send(0, 1)]})
        self.assertEqual(t.probes, {})
        self.assertEqual(t.observed, {(0, 0): (1, 0)})


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
        t = mk({0: [c, coll("allgather", comm=2), free], 1: [c, coll("allgather", comm=2), free]}, version=2)
        self.assertEqual(analyze(t, with_sp=False).by_class("2-collective"), [])
        sub = t.colls[1].comm
        # the creation is a collective of the parent, the release one of the communicator freed
        self.assertEqual([[(c.name, c.comm, c.ordinal) for c in cs][0] for cs in t.coll_sets()],
                         [("comm_create", 0, 0), ("allgather", sub, 0), ("comm_free", sub, 1)])


class C_CommunicatorCreation(unittest.TestCase):
    """Creating a communicator is collective over its parent (MPI 4.1 §7.4); the model treats
    it as a barrier, which an accepted alternative may rely on (audit of 22 Oct 2026)."""

    @staticmethod
    def witness(records=True):
        # rank 0: Recv r(ANY) <- s2, Comm_split, Recv(1) <- s. Rank 1: Comm_split, Send s.
        # Rank 2: Send s2, Comm_split. r <- s needs rank 1 out of its split before rank 0 has
        # entered its own, which no implementation does: the split's result depends on rank 0
        sp = [comm("split", [0, 1, 2])] if records else []
        return mk({0: [recv(T.ANY, 1, 2, 1, widx=0), *sp, recv(1, 1, 1, 1)],
                   1: [*sp, send(0, 1)], 2: [send(0, 1), *sp]}, version=2)

    def test_split_orders_the_steal(self):
        t = self.witness()
        self.assertEqual({c.name for c in t.colls}, {"comm_split"})
        self.assertEqual(len(t.coll_sets()), 1)
        # the enumeration prunes the pair (s follows r's completion through the split), and the
        # check rejects it: rank 0 never reaches its split, so rank 1 never leaves its own
        alts, pruned = candidates_full(t)
        self.assertFalse([a for a in alts if a.recv == (0, 0) and a.send == (1, 0)])
        self.assertIn(f"{t.recvs[(0, 0)]} <- {t.sends[(1, 0)]}", [d for d, _ in pruned])
        M = Overlay(t.observed, {(0, 0): (1, 0)}, [(0, 1)])
        self.assertFalse(check(t, M, {(0, 0)}, None, "L1").feasible)
        self.assertFalse([f for f in analyze(t, with_sp=False).findings if f.recv == (0, 0) and f.send == (1, 0)])
        fast_equals_generic(self, t)

    def test_the_same_run_without_the_split_accepts(self):
        t = self.witness(records=False)
        self.assertTrue([f for f in analyze(t, with_sp=False).findings if f.recv == (0, 0) and f.send == (1, 0)])

    def test_null_result_takes_part(self):
        # rank 0 passes MPI_UNDEFINED: it records the call (version 2) and is a member of the barrier
        t = mk({0: [comm("split", None)], 1: [comm("split", [1, 2])], 2: [comm("split", [1, 2])]}, version=2)
        cs = t.coll_sets()
        self.assertEqual([[c.rank for c in s] for s in cs], [[0, 1, 2]])
        self.assertEqual(analyze(t, with_sp=False).by_class("2-collective"), [])

    def test_legacy_trace_without_null_results(self):
        # a trace of the tracer before 22 Oct 2026: rank 0's MPI_COMM_NULL left no record, so
        # who took part is unknown, and nothing is predicted (idiom 12, AMG)
        t = mk({0: [recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 2, 1, widx=1)],
                1: [comm("split", [1, 2]), send(0, 1)], 2: [comm("split", [1, 2]), send(0, 1)]})
        self.assertEqual(t.colls, [])
        self.assertTrue(any(r == "creation-unknown" and "MPI_Comm_split" in d and "MPI_COMM_NULL" in d
                            for r, d in t.incomplete), t.incomplete)
        rep = analyze(t, with_sp=False)
        self.assertEqual([f for f in rep.findings if f.level != "observed"], [])
        self.assertEqual(rep.status(), "degraded:creation-unknown")
        self.assertEqual(rep.by_class("2-collective"), [])

    def test_legacy_dup_is_modelled(self):
        # MPI_Comm_dup never returns MPI_COMM_NULL: every member recorded it in any trace
        t = mk({r: [comm("dup", [0, 1])] for r in range(2)})
        self.assertEqual(t.incomplete, [])
        self.assertEqual([c.name for c in t.colls], ["comm_dup", "comm_dup"])

    def test_idup_is_a_nonblocking_collective(self):
        # since 22 Oct 2026 the tracer records MPI_Comm_idup at its call, with its request, and
        # registers the communicator at the completion (inside the wait)
        def start():
            return {"op": "comm", "name": "idup", "parent": 0, "id": -1, "req": 4}

        def done():
            return [comm("idup", [0, 1], req=4), {"op": "wait", "kind": "wait", "reqs": [4], "done": [{"req": 4}]}]
        t = mk({0: [start(), send(1, 1), *done()], 1: [start(), recv(0, 1, 0, 1), *done()]}, version=2)
        c = next(c for c in t.colls if c.rank == 0)
        self.assertEqual((c.name, c.idx, c.complete_idx, c.completed), ("comm_idup", 1, 4, True))
        self.assertEqual(len(t.colls), 2)
        prog = dsl.program_from_trace(t)
        self.assertEqual(prog[0][0], dsl.Coll("comm_idup", 0, "q4"))
        self.assertEqual(explore.explore(prog, None, comms=t.comm_members).kinds(), {"done"})

    def test_legacy_idup_at_its_completion(self):
        reg = comm("idup", [0, 1])
        t = mk({0: [send(1, 1), reg, {"op": "wait", "kind": "wait", "reqs": [4], "done": [{"req": 4}]}],
                1: [recv(0, 1, 0, 1), reg, {"op": "wait", "kind": "wait", "reqs": [4], "done": [{"req": 4}]}]})
        self.assertEqual([(c.name, c.idx, c.completed) for c in t.colls], [("comm_idup", 2, True)] * 2)

    def test_intercommunicator_creation_is_collective_over_both_groups(self):
        # gord 1: the member set of MPI_COMM_WORLD was registered first (gord 0)
        left = {"op": "comm", "name": "intercomm", "parent": 0, "id": 2, "inter": 1, "ranks": [2, 3], "local": [0, 1], "members": [0, 1, 2, 3], "gord": 1}
        right = {"op": "comm", "name": "intercomm", "parent": 0, "id": 2, "inter": 1, "ranks": [0, 1], "local": [2, 3], "members": [0, 1, 2, 3], "gord": 1}
        t = mk({0: [left], 1: [left], 2: [right], 3: [right]}, version=2)
        (cs,) = t.coll_sets()
        self.assertEqual([c.rank for c in cs], [0, 1, 2, 3])
        self.assertEqual(t.comm_members[cs[0].comm], [0, 1, 2, 3])


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

    def test_payloads_are_kept(self):
        """STRETTO_PAYLOAD traces: the packed contents of sends and of collective
        contributions and results survive the loader, which drops the records' dictionaries."""
        t = mk({0: [dict(send(1, 1), data="2a000000"), dict(coll("bcast", root=0), din="07000000")],
                1: [recv(0, 1, 0, 1), dict(coll("bcast", root=0), dout="07000000")]})
        self.assertEqual(t.sends[(0, 0)].data, "2a000000")
        self.assertEqual([(c.rank, c.din, c.dout) for c in t.colls], [(0, "07000000", None), (1, None, "07000000")])
        self.assertIsNone(mk({0: [send(1, 1)], 1: [recv(0, 1, 0, 1)]}).sends[(0, 0)].data)


class C_ProgramFromTrace(unittest.TestCase):
    """The single-path program is built from the loaded objects: world ranks, global
    communicator ids, the same ordinals as the trace, PROC_NULL operations skipped."""

    def _split(self):
        # comm 2 = world ranks {1, 2}; on it rank 2 (comm rank 1) sends to comm rank 0 (= world
        # rank 1), rank 1 receives from ANY; both then barrier on it. Rank 0 sends to PROC_NULL.
        c = {"op": "comm", "name": "split", "parent": 0, "id": 2, "ranks": [1, 2], "gord": 0}
        return mk({0: [dict(c, id=2, ranks=[0]), {**send(-2, 1), "procnull": 1}, send(1, 5)],
                   1: [c, {**recv(0, 1, 1, 1, comm=2), "anysrc": 1}, coll("barrier", comm=2), recv(0, 5, 0, 5)],
                   2: [c, send(0, 1, comm=2), coll("barrier", comm=2)]}, version=2)

    def test_world_ranks_and_global_comms(self):
        t = self._split()
        prog = dsl.program_from_trace(t)
        # the split, a collective of MPI_COMM_WORLD, and the send to 1; the PROC_NULL send is not an operation
        self.assertEqual(prog[0], [dsl.Coll("comm_split", 0), dsl.Send(1, 5, 4, 0)])
        s = prog[2][1]
        self.assertEqual((s.dst, s.comm), (1, t.sends[(2, 0)].comm))
        r = prog[1][1]
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

    def test_cancelled_send_is_not_in_the_observed_matching(self):
        # rank 1 cancels its first message (the cancel succeeds), then sends another with the
        # same envelope: the receive took the second, which is therefore not an orphan
        t = mk({0: [recv(1, 0, 1, 0)],
                1: [send(0, 0, mode="isend", req=0), {"op": "cancel", "req": 0},
                    {"op": "wait", "kind": "wait", "reqs": [0], "done": [{"req": 0, "cancelled": 1}]}, send(0, 0)]})
        self.assertEqual(t.observed, {(0, 0): (1, 1)})
        self.assertFalse([f for f in analyze(t, with_sp=False).findings if f.cls == "1c"])

    def test_mprobe_removes_the_message_from_the_queue(self):
        # rank 0 mprobes rank 1's message, then posts a wildcard that rank 2 serves, then
        # mrecvs the probed message: the wildcard could not have taken rank 1's message
        mp = {"op": "probe", "kind": "mprobe", "src": 1, "tag": 1, "comm": 0, "flag": 1, "msrc": 1, "mtag": 1}
        t = mk({0: [mp, recv(T.ANY, 1, 2, 1, widx=0), {**recv(1, 1, 1, 1), "mrecv": 1}],
                1: [send(0, 1)], 2: [send(0, 1)]})
        self.assertEqual(t.observed, {(0, 0): (2, 0), (0, 1): (1, 0)})
        self.assertEqual(t.sends[(1, 0)].reserved_idx, t.probes[(0, 0)].idx)
        self.assertEqual(candidates_full(t)[0], [])


class C_Cancellation(unittest.TestCase):
    """The model has no cancel: a run in which a cancel succeeded, or whose outcome the trace
    does not have, is reported with observed findings only (audit of 22 Oct 2026)."""

    @staticmethod
    def witness(outcome):
        # rank 1 posts s_c and s with one envelope, receives y, and only then cancels s_c. The
        # steal r <- s is infeasible: r accepts s_c, which precedes s and is still pending when
        # r matches (the cancel needs y, and y needs r). The fast and generic checks skip the
        # cancelled send as if it had never been posted, so they accept the steal.
        done = {"req": 0} if outcome is None else {"req": 0, "cancelled": outcome}
        r1 = [send(0, 0, mode="isend", req=0), send(0, 0, mode="isend", req=1), recv(0, 9, 0, 9),
              {"op": "cancel", "req": 0}]
        r1 += [{"op": "wait", "kind": "wait", "reqs": [0], "done": [done]}, wait((1, None, None))] if outcome is not None else [wait((1, None, None))]
        r0 = [recv(T.ANY, T.ANY, 2, 7, widx=0), send(1, 9)] + ([recv(1, 0, 1, 0)] if outcome == 0 else []) + [recv(1, 0, 1, 0)]
        return mk({0: r0, 1: r1, 2: [send(0, 7)]})

    def test_successful_cancel_gives_observed_findings_only(self):
        t = self.witness(1)
        self.assertEqual(t.unmodelled, [(1, "Cancel (succeeded)")])
        alt = next(a for a in candidates_full(t)[0] if a.recv == (0, 0) and a.send == (1, 1))
        self.assertTrue(check(t, alt.M, alt.changed, None, "L1").feasible, "the model accepts what MPI cannot do")
        rep = analyze(t, with_sp=False)
        self.assertEqual([f for f in rep.findings if f.level != "observed"], [])
        self.assertTrue(any("Cancel (succeeded)" in n and "observed findings only" in n for n in rep.notes), rep.notes)

    def test_cancel_without_a_recorded_outcome(self):
        t = self.witness(None)
        self.assertEqual(t.unmodelled, [(1, "Cancel (outcome not recorded)")])

    def test_failed_cancel_changes_nothing(self):
        t = self.witness(0)
        self.assertEqual(t.unmodelled, [])
        self.assertFalse(t.sends[(1, 0)].cancelled)
        self.assertTrue([f for f in analyze(t, with_sp=False).findings if f.level == "L1"])

    def test_successful_cancel_of_a_receive(self):
        # rank 0 cancels a pending Irecv(ANY, 5) that rank 2's message would have reached
        t = mk({0: [irecv(T.ANY, 5, 0), recv(T.ANY, T.ANY, 1, 7, widx=0), {"op": "cancel", "req": 0},
                    {"op": "wait", "kind": "wait", "reqs": [0], "done": [{"req": 0, "msrc": None, "mtag": None, "mcount": None, "cancelled": 1}]},
                    recv(2, 5, 2, 5)],
                1: [send(0, 7)], 2: [send(0, 5)]})
        self.assertTrue(t.recvs[(0, 0)].cancelled)
        self.assertEqual(t.unmodelled, [(0, "Cancel (succeeded)")])
        self.assertEqual([f for f in analyze(t, with_sp=False).findings if f.level != "observed"], [])


class C_RequestGetStatus(unittest.TestCase):
    """A true MPI_Request_get_status is a result: the operation completed there, as at a
    successful test, so its match precedes the query (audit of 22 Oct 2026)."""

    @staticmethod
    def status(req, flag=1, **st):
        return {"op": "wait", "kind": "getstatus", "flag": flag, "reqs": [req], "done": [], **st}

    def test_true_flag_orders_the_match_before_the_query(self):
        # rank 0 finds its Irecv(ANY) complete, then sends x; rank 1 sends s1 only after x. In
        # MPI the receive cannot take s1, which comes after the query that found it matched;
        # with the completion at the wait the steal r <- s1 was accepted.
        t = mk({0: [irecv(T.ANY, 1, 0), self.status(0), send(1, 9), wait((0, 2, 1)), recv(1, 1, 1, 1)],
                1: [recv(0, 9, 0, 9), send(0, 1)], 2: [send(0, 1)]})
        r = t.recvs[(0, 0)]
        self.assertEqual((r.complete_idx, t.observed[r.key]), (2, (2, 0)))
        self.assertEqual([a for a in candidates_full(t)[0] if a.recv == r.key and a.send == (1, 0)], [])
        rep = analyze(t, with_sp=False)
        self.assertFalse([f for f in rep.findings if f.recv == r.key and f.send == (1, 0)])
        prog = dsl.program_from_trace(t)
        self.assertEqual(prog[0][1], dsl.Wait("q0"))

    def test_false_flag_says_nothing(self):
        t = mk({0: [irecv(1, 1, 0), self.status(0, flag=0), wait((0, 1, 1))], 1: [send(0, 1)]})
        self.assertEqual(t.recvs[(0, 0)].complete_idx, 3)

    def test_freed_after_a_true_flag(self):
        # the request is freed after the query found it complete: the status in the record
        # (written since 22 Oct 2026) names the message, so the receive is matched there
        t = mk({0: [irecv(1, 1, 0), self.status(0, msrc=1, mtag=1, mcount=1), {"op": "reqfree", "req": 0}],
                1: [send(0, 1)]})
        r = t.recvs[(0, 0)]
        self.assertEqual((r.completed, r.freed, r.complete_idx), (True, False, 2))
        self.assertEqual(t.observed, {(0, 0): (1, 0)})
        self.assertFalse(analyze(t, with_sp=False).by_class("1c"))


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

    def test_packed_and_byte(self):
        # a message of any type may be received as MPI_PACKED; a packed message's types are
        # not in the trace; MPI_BYTE is compared with MPI_BYTE only, by length
        self.assertTrue(types.compatible("MPI_INT", 4, "MPI_PACKED", 64))
        self.assertTrue(types.compatible("MPI_INT*2,MPI_DOUBLE", 1, "MPI_PACKED", 16))
        self.assertIsNone(types.compatible("MPI_PACKED", 16, "MPI_INT", 4))
        self.assertIsNone(types.compatible("MPI_PACKED", 16, "MPI_INT,MPI_PACKED", 1))
        self.assertTrue(types.compatible("MPI_BYTE", 8, "MPI_BYTE", 16))
        self.assertFalse(types.compatible("MPI_BYTE", 16, "MPI_BYTE", 8))
        self.assertIsNone(types.compatible("MPI_INT", 4, "MPI_BYTE", 16))
        self.assertIsNone(types.compatible("MPI_BYTE", 16, "MPI_DOUBLE", 2))
        self.assertIsNone(types.compatible("MPI_INT,MPI_BYTE", 1, "MPI_INT,MPI_BYTE", 1))

    def test_an_int_message_into_a_packed_receive_is_not_1a(self):
        r = T.Recv((0, 0), 0, 1, T.ANY, 1, 0, 16, 1, True, tsig="MPI_PACKED")
        s = T.Send((1, 0), 1, 1, 0, 1, 0, 4, 4, True, "std", tsig="MPI_INT")
        o = T.Send((2, 0), 2, 1, 0, 1, 0, 16, 1, True, "std", tsig="MPI_PACKED")
        self.assertNotEqual(classify(r, s, o)[0], "1a")

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

    def test_steal_of_a_ready_send(self):
        for through_rank0 in (False, True):
            self._same(ready_steal_trace(through_rank0))


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


def closure_orders_agree(tc, t):
    """fast.Observed.closure taken in Lamport order (the default) and last in first out (its
    order before 22 Oct 2026): the same cut and issued sends for the observed matching in both
    modes and the same verdicts and cuts for every alternative, probe alternative and
    completion-order alternative, at every breakpoint. And the chained closures of the probes
    (Observed.probe_closure), taken last to first as the report takes them, equal each probe's
    own closure."""
    ordered, lifo = fast.Indexes(t), fast.Indexes(t)
    lifo._lam = False
    hb = fast.Observed(t, None, fast.Indexes(t)).reaches
    alts, _ = candidates_full(t, hb)
    probes = probe_candidates(t, hb)
    waits = _completion_alternatives(t, hb)

    def v(x):
        return None if x is None else (x.feasible, x.reason, x.cut)

    for B in breakpoints(t) + [None]:
        res = []
        for ix in (ordered, lifo):
            ob = fast.Observed(t, B, ix)
            out = [ob.closure(), ob.closure(mode="SP")]
            out += [v(ob.check_alternative(a)) for a in alts]
            out += [ob.closure(changed_probes=[p]) for p, _ in probes]
            out += [v(ob.check_probe(p, s)) for p, s in probes]
            out += [v(ob.check_event(rec.rank, rec.idx, s_j)) for rec, _, _, s_j in waits]
            res.append(out)
            chained = fast.Observed(t, B, ix)
            for p in sorted({p.key: p for p, _ in probes}.values(), key=lambda p: p.key, reverse=True):
                tc.assertEqual(chained.probe_closure(p), fast.Observed(t, B, ix).closure(changed_probes=[p]), f"{p} at B={B}")
        tc.assertEqual(res[0], res[1], f"B={B}")


def clocks_agree(tc, t):
    """fast.Observed's vector clocks, whose rows are shared between the nodes of a rank up to the
    next join, against the graph's own reachability for every pair of a rank's node and any
    node, and Observed.acyclic, which the graphs of other thresholds decide where they can,
    against a cycle search in the graph of each threshold, taken in an order that uses both."""
    ix = fast.Indexes(t)
    bps = breakpoints(t) + [None]
    for B in [None] + bps[:-1] + [None]:
        g = build_graph(t, t.observed, None, B)
        acyclic = g.find_cycle() is None
        ob = fast.Observed(t, B, ix)
        tc.assertEqual(ob.acyclic(), acyclic, f"B={B}")
        if not acyclic:
            continue
        nodes = [n for n in g.nodes if n[0] >= 0]
        for a in (n for n in nodes if n[2] != "m"):
            reach = g.reach_set(a)
            for b in nodes:
                tc.assertEqual(ob.reaches(a, b), a == b or b in reach, f"{a} -> {b} at B={B}")


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
                closure_orders_agree(self, t)
                clocks_agree(self, t)

    def test_closure_order_on_a_cascade(self):
        """A cascade over four ranks that the LIFO order processes rank 3 twice in: rank 0's
        cut after its probe removes its sends to 1 and 3, and rank 1's removal reaches 3
        again, earlier. Records are numbered from the init record at 0."""
        pr = {"op": "probe", "kind": "probe", "src": 0, "anysrc": 1, "tag": 1, "comm": 0, "flag": 1, "msrc": 2, "mtag": 1}
        t = mk({0: [pr, recv(2, 1, 2, 1), send(3, 5), send(1, 4)],
                1: [recv(0, 4, 0, 4), send(3, 6)],
                2: [send(0, 1), send(0, 1)],
                3: [recv(1, 6, 1, 6), recv(0, 5, 0, 5)]})
        self.assertIsNotNone(t.fast_indexes().lamport())
        closure_orders_agree(self, t)
        ob = fast.Observed(t, None)
        cut, _ = ob.closure(changed_probes=[t.probes[(0, 0)]])
        self.assertEqual((cut[0], cut[1], cut[3]), (2, 1, 1))

    def test_probe_chain_that_cuts_the_probe(self):
        """The chain of probe closures applies rule 6 to the changed probe, which the probe's own
        closure exempts: a pending Irecv whose message is not in the trace deprives the probe
        posted after it in the chain, and only the next receive in the probe's own closure.
        The chain then defers to the probe's closure."""
        pr = {"op": "probe", "kind": "probe", "src": 0, "anysrc": 1, "tag": 1, "comm": 0, "flag": 1, "msrc": 2, "mtag": 1}
        t = mk({0: [irecv(T.ANY, 1, 0), pr, recv(2, 1, 2, 1), wait((0, 1, 1))], 1: [], 2: [send(0, 1)], 3: [send(0, 1)]})
        p = t.probes[(0, 0)]
        ob = fast.Observed(t, None)
        self.assertEqual(ob.closure()[0][0], p.idx)                  # without the exemption: the probe is cut
        self.assertEqual(ob.probe_closure(p), ob.closure(changed_probes=[p]))
        self.assertEqual(ob.probe_closure(p)[0][0], p.idx + 1)
        closure_orders_agree(self, t)


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


class C_RematchedCompetitor(unittest.TestCase):
    """Rule 6 in the fast closure under an alternative (audit of 22 Oct 2026, item 1). The fast
    closure's suffix query names the competitor of a pending receive by the message each later
    receive took in the run; a re-matched receive is the first competitor of a pending receive
    that accepts its new message, which that query never sees. Rank 0: Irecv r'(ANY, tag 1);
    Recv r(ANY, ANY), which takes rank 3's tag-2 message; Send x -> 1; Recv r2(ANY, ANY), which
    takes rank 2's tag-1 message s; Wait r', which takes rank 1's s', sent after x. The steal
    r <- s cuts rank 0 after r, so x is never sent, s' never issued, and r' stays pending: it
    accepts s and is posted before r, so MPI gives s to r' and never to r. The fast check
    accepted the steal."""

    def trace(self):
        return mk({0: [irecv(T.ANY, 1, 10), recv(T.ANY, T.ANY, 3, 2), send(1, 5), recv(T.ANY, T.ANY, 2, 1), wait((10, 1, 1))],
                   1: [recv(0, 5, 0, 5), send(0, 1)], 2: [send(0, 1)], 3: [send(0, 2)]})

    def test_pending_receive_takes_the_stolen_message(self):
        t = self.trace()
        r, s = (0, 1), (2, 0)
        a = next(a for a in candidates_full(t)[0] if a.recv == r and a.send == s)
        self.assertEqual(a.kind, "steal")
        for B in breakpoints(t) + [None]:
            g = check(t, a.M, a.changed, B, "L1")
            self.assertEqual(g.reason, "witness cut by the horizon closure", f"B={B}")
            f = fast.Observed(t, B).check_alternative(a)
            self.assertIsNotNone(f)
            self.assertEqual((f.feasible, f.reason), (False, g.reason), f"B={B}")
        for generic in (False, True):
            self.assertNotIn((r, s), [(x.recv, x.send) for x in analyze(t, with_sp=False, generic=generic).findings])
        # ground truth: the straight-line program has no execution in which r takes s
        res = explore.explore(dsl.program_from_trace(t), None, force={r: s}, comms=t.comm_members)
        self.assertEqual(res.outcomes_with(r, s), [])
        fast_equals_generic(self, t)
        closure_equals_fixpoint(self, t)
        closure_orders_agree(self, t)


class C_CollectiveRule(unittest.TestCase):
    """Rule 4 in the fast closure (audit of 22 Oct 2026, item 4): when a rank stops issuing its
    collectives, the scan over their groups dropped a member as soon as the member lost the
    completion of one group, on the ground that its later collectives complete later still. A
    nonblocking collective issued later can complete earlier, and the members of the other
    group of an intercommunicator were never scanned at all."""

    def out_of_order_trace(self):
        # ranks 1 and 2 share a communicator and post Ibarrier b0, Ibarrier b1, wait for b1
        # first and for b0 last; between the two, rank 2 sends x to rank 0. Rank 0's wildcard
        # took rank 3's message s_obs; rank 3 then sends z to rank 1, which receives it before
        # its Ibarriers. Every rank first takes part in the split that makes the communicator,
        # ranks 0 and 3 obtaining MPI_COMM_NULL (trace version 2)
        c2, null = comm("split", [1, 2], gord=0), comm("split", None)
        return mk({0: [null, recv(T.ANY, 1, 3, 1), recv(2, 1, 2, 1)],
                   1: [c2, recv(3, 5, 3, 5), coll("ibarrier", comm=2, req=20), coll("ibarrier", comm=2, req=21),
                       wait((21, None, None)), wait((20, None, None))],
                   2: [c2, coll("ibarrier", comm=2, req=20), coll("ibarrier", comm=2, req=21), wait((21, None, None)),
                       send(0, 1), wait((20, None, None))],
                   3: [null, send(0, 1), send(1, 5)]}, version=2)

    def test_collectives_waited_out_of_issue_order(self):
        """The steal r <- x leaves s_obs without a receive. At B = 0 it is a rendezvous send, so
        rank 3 never sends z, rank 1 never issues its Ibarriers, and rank 2 never returns from
        Wait b1: x is never sent. The fast closure took rank 2's completion of b0 away and left
        the scan, kept Wait b1 and x, and accepted the steal at B = 0."""
        t = self.out_of_order_trace()
        r, x = (0, 0), (2, 0)
        a = next(a for a in candidates_full(t)[0] if a.recv == r and a.send == x)
        self.assertEqual(a.kind, "steal")
        g = check(t, a.M, a.changed, 0, "L1")
        self.assertEqual(g.reason, "witness cut by the horizon closure")
        f = fast.Observed(t, 0).check_alternative(a)
        self.assertEqual((f.feasible, f.reason), (False, g.reason))
        self.assertEqual(feasible_profile(t, a.M, a.changed), {0: False, 4: True, None: True})
        for generic in (False, True):
            got = [fd.B for fd in analyze(t, with_sp=False, generic=generic).findings if (fd.recv, fd.send) == (r, x)]
            self.assertEqual(got, ["B >= 4"], generic)
        res = explore.explore(dsl.program_from_trace(t), 0, force={r: x}, comms=t.comm_members)
        self.assertEqual(res.outcomes_with(r, x), [])
        fast_equals_generic(self, t)
        closure_equals_fixpoint(self, t)
        closure_orders_agree(self, t)

    def test_intercommunicator_barrier(self):
        """Ranks {0, 1} and {2, 3} share an intercommunicator barrier. Rank 2's wildcard takes
        rank 5's message instead of rank 4's, which cuts rank 2 before the barrier: no member
        completes it. The fast closure lowered rank 3 only, the other member of rank 2's group
        as the communicator lists it, and kept ranks 0 and 1 past the barrier."""
        left = {"op": "comm", "name": "intercomm", "parent": 0, "id": 2, "inter": 1, "ranks": [2, 3], "local": [0, 1],
                "members": [0, 1, 2, 3], "gord": 0}
        right = dict(left, ranks=[0, 1], local=[2, 3])
        t = mk({0: [left, coll("barrier", comm=2), send(1, 7)],
                1: [left, coll("barrier", comm=2), recv(0, 7, 0, 7)],
                2: [right, recv(T.ANY, 1, 4, 1), coll("barrier", comm=2), recv(5, 1, 5, 1)],
                3: [right, coll("barrier", comm=2)],
                4: [send(2, 1)], 5: [send(2, 1)]})
        a = next(a for a in candidates_full(t)[0] if a.recv == (2, 0) and a.send == (5, 0))
        barrier = {c.rank: c.idx for c in t.colls}
        for B in (0, None):
            v = check(t, a.M, a.changed, B, "L1")
            self.assertTrue(v.feasible, v.reason)
            self.assertEqual({R: v.cut[R] for R in (0, 1, 3)}, {R: barrier[R] for R in (0, 1, 3)})
        fast_equals_generic(self, t)
        closure_orders_agree(self, t)


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
        # (MPI_THREAD_MULTIPLE: every record names its thread, the ones mk adds aside)
        t = mk({0: [{"op": "init", "np": 3, "thread": 3},
                    {"op": "begin", "call": "recv", "src": 1, "tag": 1, "comm": 0, "count": 1, "tsize": 4, "tid": 1},
                    {**send(2, 9), "tid": 2}, {**recv(1, 1, 1, 1), "tid": 1}],
                1: [send(0, 1)], 2: [recv(0, 9, 0, 9)]})
        self.assertEqual(t.blocked_calls(), [])
        self.assertEqual(t.threads[0], 2)
        # below MULTIPLE (since 22 Oct 2026) a record without a thread is the initialising thread's
        t = mk({0: [{**send(1, 1), "tid": 5}], 1: [recv(0, 1, 0, 1)]})
        self.assertEqual(t.threads, {0: 2, 1: 1})

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

    def test_several_threads_below_multiple_give_observed_findings_only(self):
        # MPI_THREAD_SERIALIZED (2): the tracer names the thread of every record that the
        # initialising thread did not make; the rank's call order then depends on scheduling
        t = mk({0: [{"op": "init", "np": 3, "thread": 2}, recv(T.ANY, 1, 1, 1, widx=0), {**recv(T.ANY, 1, 2, 1, widx=1), "tid": 7}],
                1: [send(0, 1)], 2: [send(0, 1)]})
        self.assertEqual(t.threads[0], 2)
        rep = analyze(t)
        self.assertEqual([f for f in rep.findings if f.level != "observed"], [])
        self.assertTrue(any("several threads called MPI on ranks [0]" in n for n in rep.notes), rep.notes)
        # one thread only, named or not: predictions as usual
        t = mk({0: [{"op": "init", "np": 3, "thread": 1}, recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 2, 1, widx=1)],
                1: [send(0, 1)], 2: [send(0, 1)]})
        self.assertEqual(t.threads[0], 1)
        self.assertTrue(analyze(t, with_sp=False).by_class("1f"))

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
        self.assertTrue(any(r == "tracer-overflow" and "message table overflowed" in d for r, d in t.incomplete), t.incomplete)
        self.assertEqual(t.observed, {(0, 0): (1, 0)})


class C_IncompleteTrace(unittest.TestCase):
    """A trace that is not a complete record of the run's communication gets observed findings
    only, with the reason (audit of 22 Oct 2026)."""

    def _race(self, extra0=(), np=None, **ranks):
        raw = {0: [recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 2, 1, widx=1), *extra0], 1: [send(0, 1)], 2: [send(0, 1)]}
        raw.update({int(k[1:]): v for k, v in ranks.items()})
        return mk({r: v for r, v in raw.items() if v is not None}, np=np)

    def test_complete_trace_predicts(self):
        self.assertTrue([f for f in analyze(self._race(), with_sp=False).findings if f.level == "L1"])

    def test_missing_rank(self):
        # rank 3 has no file: the barrier is not a 2-collective error, and nothing is predicted
        t = self._race(extra0=[coll("barrier")], np=4, r1=[send(0, 1), coll("barrier")], r2=[send(0, 1), coll("barrier")])
        self.assertEqual(t.missing, [3])
        rep = analyze(t, with_sp=False)
        self.assertEqual(rep.by_class("2-collective"), [])
        self.assertEqual([f for f in rep.findings if f.level != "observed"], [])
        self.assertTrue(any("no trace file" in n and "observed findings only" in n for n in rep.notes), rep.notes)
        self.assertTrue(any("lack only ranks without a trace file" in n for n in rep.notes), rep.notes)
        self.assertEqual(rep.status(), "degraded:missing-ranks")

    def test_a_receive_from_an_untraced_sender(self):
        # rank 2's send was made by code the tracer does not see (Fortran under Open MPI)
        t = self._race(r2=[])
        self.assertTrue(any(r == "unmatched-completions" and "no send in the trace matches" in d for r, d in t.incomplete), t.incomplete)
        rep = analyze(t, with_sp=False)
        self.assertEqual([f for f in rep.findings if f.level != "observed"], [])
        self.assertEqual(rep.status(), "degraded:unmatched-completions")

    def test_site_table_overflow_is_only_a_note(self):
        # the records are whole, only call sites stop being named: degraded, still predicting
        t = self._race(r1=[{"op": "overflow", "what": "site table"}, send(0, 1)])
        self.assertEqual(t.incomplete, [])
        rep = analyze(t, with_sp=False)
        self.assertTrue([f for f in rep.findings if f.level == "L1"])
        self.assertEqual(rep.status(), "degraded:tracer-overflow")


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


def ready_steal_trace(through_rank0: bool = False):
    """Rank 1 posts r = Irecv(ANY, tag 1), which takes rank 2's message, waits for it, then
    posts r2 = Irecv(ANY, tag 1), which takes rank 0's ready send s. In the run s needs r2
    posted, which follows r's wait. With through_rank0, rank 1 also sends x to rank 0 after
    the wait, and rank 0 receives x before its ready send."""
    r0, r1 = [send(1, 1, mode="rsend")], [irecv(T.ANY, 1, 10), wait((10, 2, 1))]
    if through_rank0:
        r0.insert(0, recv(1, 5, 1, 5))
        r1.append(send(0, 5))
    r1 += [irecv(T.ANY, 1, 11), wait((11, 0, 1))]
    return mk({0: r0, 1: r1, 2: [send(1, 1)]})


class C_PruneOnKeptEdges(unittest.TestCase):
    """candidates_full prunes a head s of a wildcard receive r when the completion of r
    reaches issue(s) in the observed graph. The path may not use an edge the alternative
    removes: E8b's synthetic program 037 (experiments/e8_hb.py) lost a steal that the check
    accepts and the explorer realises, because its only path entered issue(s) through the
    ready-send edge from s's observed receive, which posts after r completes; the steal
    gives s to r and replaces that edge by one from r. This is that shape, reduced."""

    def test_steal_through_the_ready_edge_is_enumerated(self):
        t = ready_steal_trace()
        r, s = (1, 0), (0, 0)
        self.assertEqual(t.observed, {r: (2, 0), (1, 1): s})
        self.assertTrue(observed_hb(t)(t.recvs[r].complete_node(), t.sends[s].issue_node()))
        for hb in (None, fast.Observed(t, None).reaches):
            alts, pruned = candidates_full(t, hb)
            self.assertIn((r, s, "steal"), [(a.recv, a.send, a.kind) for a in alts], pruned)
        a = next(a for a in candidates_full(t)[0] if a.recv == r and a.send == s)
        for B in (0, None):
            v = check(t, a.M, a.changed, B, "L1")
            self.assertTrue(v.feasible, f"B={B}: {v.reason}")
        for generic in (False, True):
            self.assertIn((r, s), [(f.recv, f.send) for f in analyze(t, with_sp=False, generic=generic).findings])
        res = explore.explore(dsl.program_from_trace(t), None, force={r: s}, comms=t.comm_members)
        self.assertTrue([o for o in res.outcomes if dict(o.matching).get(r) == s and "rsend-no-receive" not in o.error_kinds])
        self.assertEqual(realised_but_pruned(t, 20000), [])
        fast_equals_generic(self, t)
        closure_equals_fixpoint(self, t)

    def test_program_order_into_the_send_still_prunes(self):
        """With rank 0 waiting for rank 1's message after r before its ready send, issue(s)
        follows r's completion by program order: the pair stays pruned, and the steal it
        would give has its witness cut by the horizon at every threshold."""
        t = ready_steal_trace(through_rank0=True)
        r, s = (1, 0), (0, 0)
        for hb in (None, fast.Observed(t, None).reaches):
            alts, pruned = candidates_full(t, hb)
            self.assertNotIn((r, s), [(a.recv, a.send) for a in alts])
            self.assertTrue(any("causally follows" in why for _, why in pruned), pruned)
        M = Overlay(t.observed, {r: s}, [(1, 1)])
        for B in breakpoints(t) + [None]:
            self.assertEqual(check(t, M, {r}, B, "L1").reason, "witness cut by the horizon closure", f"B={B}")
        self.assertEqual(realised_but_pruned(t, 20000), [])


def realised_but_pruned(t, max_states: int):
    """The kernel alternatives (steals and swaps) that candidates_full prunes, with the generic
    graph or with the report's vector clocks, and that the explorer realises at B = inf as a
    single change: every changed receive is posted inside the alternative's horizon and its
    new message issued there (otherwise the trace does not determine the change, and the
    check would reject it whatever the order), and some execution of the trace as a
    straight-line program has every pair of the alternative that the horizon judges, with every
    ready send of the horizon taken by a receive posted before its issue (the explorer's
    rsend-no-receive marks the others, erroneous by MPI 4.1 section 3.4; E6's
    model_executions, here with every pair inside the horizon, which holds what the pairs
    need). Pairs whose exploration exceeds max_states are left out, and so is a
    trace whose observed graph is cyclic at B = inf: the model cannot realise its run at any
    threshold, the pruning's argument (docs/model.md, section 2) needs the graph acyclic, and
    the report predicts nothing for it. (Before 22 Oct 2026 the explorer accepted a ready send
    while any receive that accepts it was posted, so E6's generator yielded such traces.)"""
    from stretto.horizon import compute_cut
    if build_graph(t, t.observed, None, None).find_cycle() is not None:
        return []
    kept = [{(a.recv, a.send) for a in candidates_full(t, hb)[0]} for hb in (None, fast.Observed(t, None).reaches)]
    prog, out = None, []
    for a in candidates_full(t, lambda x, y: False)[0]:
        if all((a.recv, a.send) in k for k in kept):
            continue
        issued: dict = {}
        cut = compute_cut(t, a.M, set(a.changed), None, "L1", set(), {}, set(), issued, None)

        def judged(rk) -> bool:
            sk = a.M.get(rk)
            return (t.recvs[rk].idx < cut[rk[0]] and sk is not None
                    and (t.sends[sk].idx < cut[sk[0]] or issued.get(sk[0]) == t.sends[sk].idx))

        if not all(judged(rk) for rk in a.changed):
            continue
        pairs = {rk: sk for rk, sk in a.M.items() if judged(rk)}
        prog = prog or dsl.program_from_trace(t)
        try:
            res = explore.explore(prog, None, force={a.recv: a.send}, max_states=max_states, comms=t.comm_members)
        except (explore.StateCapReached, explore.ExploreError):
            continue
        clean = [o for o in res.outcomes if not any(t.sends[k].idx < cut[k[0]] for k in explore.ready_errors(o))]
        if any(all(dict(o.matching).get(rk) == sk for rk, sk in pairs.items()) for o in clean):
            out.append(a.describe(t))
    return out


class C_EnumerationComplete(unittest.TestCase):
    """Every pair the explorer realises as a single change is enumerated (realised_but_pruned),
    on the random programs of E6 (experiments/e6_checker_vs_explorer.py --synthetic: the same
    generator, seed and share of programs with synchronous and ready sends interleaved with
    receives and waits), the first PROGRAMS of them with a smaller state cap so that the test
    stays fast. Of the programs drawn before the explorer's ready-send fix (22 Oct 2026), none of
    E6's 200 had a pair that the pruning lost before it set the ready edge aside, and of E8b's
    100, all mixed, program 037 had one (C_PruneOnKeptEdges)."""
    PROGRAMS, MAX_STATES = 40, 5000

    def test_e6_programs(self):
        made = pruned = 0
        for t in e6_traces(self.PROGRAMS, self.MAX_STATES):
            made += 1
            pruned += len(candidates_full(t)[1])
            with self.subTest(program=made):
                self.assertEqual(realised_but_pruned(t, self.MAX_STATES), [])
        self.assertEqual(made, self.PROGRAMS)
        self.assertGreater(pruned, 0)


def e6_traces(n: int, max_states: int, raw: bool = False):
    """The traces of the first n random programs of E6 (experiments/e6_checker_vs_explorer.py
    --synthetic, seed 20261016) that have a wildcard receive, as C_EnumerationComplete draws them;
    with raw, their records in the tracer's format instead (experiments/report_diff.py)."""
    rng = __import__("random").Random(20261016)
    made = attempts = 0
    while made < n and attempts < n * 20:
        attempts += 1
        nranks = rng.choice([3, 4, 4, 5])
        prog, comms = dsl.random_matched_program(rng, nranks, mixed=rng.random() < 0.5)
        try:
            res = explore.explore(prog, None, comms=comms, max_states=max_states)
        except (explore.StateCapReached, explore.ExploreError):
            continue
        done = [o for o in res.outcomes if o.kind == "done" and not o.leaked and not o.errors]
        if not done:
            continue
        o = rng.choice(done)
        t = T.Trace()
        records = dsl.trace_from_execution(prog, dict(o.matching), comms)
        T._build(t, records)
        if not any(r.wildcard for r in t.recvs.values()):
            continue
        made += 1
        yield records if raw else t


def random_probe_trace(rng, nranks: int, rounds: int = 4):
    """The trace of random_probe_records."""
    return mk(random_probe_records(rng, nranks, rounds))


def random_probe_records(rng, nranks: int, rounds: int = 4):
    """A random run with many probes per rank, written in the tracer's format as it goes: in
    each round the ranks send to one another (standard sends of one to three ints, tags 1 and
    2), probe with ANY_SOURCE, and ANY_TAG or a tag, for a message in flight and then mostly
    receive it from the probed source, receive with ANY_SOURCE, and post Irecv(ANY_SOURCE)s that
    a later wait completes; the round ends with a barrier or a broadcast. A receive takes, and
    a probe reports, the earliest message of its source that it accepts, and an Irecv is
    matched when it is posted, so the records are those of an execution with every message
    buffered: the observed graph at B = inf is acyclic, and at small B rendezvous edges may
    close cycles."""
    recs = {r: [] for r in range(nranks)}
    inflight = {}                       # (src, dst) -> tags of the messages not yet taken, in order
    pending = {r: [] for r in range(nranks)}
    req = [100]

    def avail(dst, tag=None):
        return [s for (s, d), tags in sorted(inflight.items()) if d == dst and tags and (tag is None or tag in tags)]

    def take(s, d, tg):
        inflight[(s, d)].remove(tg)     # the first message of that tag

    for rnd in range(rounds):
        for _ in range(rng.randint(10 * nranks, 16 * nranks)):
            r = rng.randrange(nranks)
            act = rng.random()
            if act < 0.35:
                dst = rng.choice([q for q in range(nranks) if q != r])
                tg = rng.choice([1, 2])
                recs[r].append(send(dst, tg, count=rng.randint(1, 3)))
                inflight.setdefault((r, dst), []).append(tg)
            elif act < 0.65:
                tag = rng.choice([1, 2, None])
                cands = avail(r, tag)
                if not cands:
                    continue
                s = rng.choice(cands)
                tg = inflight[(s, r)][0] if tag is None else tag
                pr = {"op": "probe", "kind": rng.choice(["probe", "iprobe"]), "src": 0, "anysrc": 1, "comm": 0, "flag": 1,
                      "msrc": s, "mtag": tg}
                pr.update({"tag": 0, "anytag": 1} if tag is None else {"tag": tag})
                recs[r].append(pr)
                if rng.random() < 0.8:
                    recs[r].append(recv(s, tg, s, tg))
                    take(s, r, tg)
            elif act < 0.8:
                tg = rng.choice([1, 2])
                cands = avail(r, tg)
                if cands:
                    s = rng.choice(cands)
                    recs[r].append(recv(T.ANY, tg, s, tg))
                    take(s, r, tg)
            elif act < 0.9:
                tg = rng.choice([1, 2])
                cands = avail(r, tg)
                if cands:
                    s = rng.choice(cands)
                    req[0] += 1
                    recs[r].append(irecv(T.ANY, tg, req[0]))
                    take(s, r, tg)
                    pending[r].append((req[0], s, tg))
            elif pending[r]:
                recs[r].append(wait(pending[r].pop(0)))
        c = coll("barrier") if rng.random() < 0.5 else coll("bcast", root=rng.randrange(nranks))
        for r in range(nranks):
            recs[r].append(dict(c))
    for r in range(nranks):
        recs[r].extend(wait(x) for x in pending[r])
    return recs


class C_ClosureOrderRandom(unittest.TestCase):
    """closure_orders_agree on random traces: E6's programs (wildcard receives, every send mode,
    no probes) and traces with many probes per rank, so that the chained probe closures meet
    long chains, cuts that reach the probe and rendezvous cascades."""

    def test_e6_programs(self):
        n = 0
        for i, t in enumerate(e6_traces(30, 5000)):
            with self.subTest(program=i + 1):
                closure_orders_agree(self, t)
                if i < 10:
                    clocks_agree(self, t)
            n += 1
        self.assertEqual(n, 30)

    def test_traces_with_many_probes(self):
        rng = __import__("random").Random(20261022)
        probes = kept = exempt = 0
        for i in range(60):
            t = random_probe_trace(rng, rng.choice([3, 4, 5]))
            self.assertIsNotNone(t.fast_indexes().lamport())
            with self.subTest(trace=i):
                closure_orders_agree(self, t)
                if i < 10:
                    clocks_agree(self, t)
            probes += len(probe_candidates(t))
            for B in (0, None):
                ob = fast.Observed(t, B)
                for p in sorted(t.probes.values(), key=lambda p: p.key, reverse=True):
                    chained = ob.closure(cuts=[(p.rank, p.idx + 1)], start=ob.closure())[0][p.rank] > p.idx
                    own = ob.closure(changed_probes=[p])[0][p.rank] > p.idx
                    kept += chained
                    exempt += own and not chained
        self.assertGreater(probes, 300)
        self.assertGreater(kept, 2000)       # the chain serves most probes as it is
        self.assertGreater(exempt, 0)        # and defers to the probe's own closure when it cuts the probe


def legacy_profile(bps, verdict, floor: int, full: bool):
    """The dictionary report._profiles_fast filled before 22 Oct 2026 (C_ProfileReadsAsDict)."""
    prof = {}

    def judge(B):
        prof[B] = verdict(B)
        return prof[B]

    for B in bps[:floor]:
        prof[B] = False
    if full:
        for B in bps[floor:]:
            judge(B)
        return prof
    lo, hi = judge(bps[floor]), judge(None)
    sizes = bps[floor + 1:-1]
    if lo == hi:
        for B in sizes:
            prof[B] = lo
        return prof
    if lo and not hi:
        for B in sizes:
            judge(B)
        return prof
    a, b, first = 0, len(sizes) - 1, len(sizes)
    while a <= b:
        m = (a + b) // 2
        if judge(sizes[m]):
            first, b = m, m - 1
        else:
            a = m + 1
    for i, B in enumerate(sizes):
        prof.setdefault(B, i >= first)
    return prof


class C_ProfileReadsAsDict(unittest.TestCase):
    """report.Profile, the compact profile of an alternative, reads as the dictionary it
    replaced: the same items in the same order, which the report's "B in ..." ranges follow,
    and the same summary over any set of realisable thresholds."""

    def test_random_profiles(self):
        from stretto.report import bisect_profile
        rng = __import__("random").Random(5)
        for _ in range(3000):
            # 0 is a breakpoint twice when the trace has empty messages
            bps = [0] * rng.choice([1, 1, 2]) + sorted(rng.sample(range(1, 100), rng.randint(0, 9))) + [None]
            pos = {B: i for i, B in enumerate(bps)}
            floor = rng.randrange(len(bps))
            full = rng.random() < 0.2
            if rng.random() < 0.6:
                first = rng.randrange(len(bps) + 1)
                val = {B: i >= first for i, B in enumerate(bps)}
            else:
                val = {B: rng.random() < 0.5 for B in bps}
            old = legacy_profile(bps, val.__getitem__, floor, full)
            notes = []
            new, seen = bisect_profile(bps, pos, val.__getitem__, lambda v: v, floor, full, lambda: notes.append(1))
            self.assertEqual(list(new.items()), list(old.items()), (bps, floor, full, val))
            self.assertEqual((len(new), new, dict(new)), (len(old), old, old))
            self.assertTrue(set(seen) <= set(bps) and all(seen[B] == val[B] for B in seen))
            self.assertEqual(bool(notes), not full and val[bps[floor]] and not val[None])
            for real in (set(bps[floor:]), set(rng.sample(bps, rng.randint(1, len(bps))))):
                self.assertEqual(profile_summary({B: ok for B, ok in new.items() if B in real}),
                                 profile_summary({B: ok for B, ok in old.items() if B in real}))
            self.assertEqual(new.summary(), profile_summary({B: ok for B, ok in old.items() if B in set(bps[floor:])}),
                             (bps, floor, full, val))


def ready_before_receive_trace():
    """E6's synthetic program 017 (seed 20261016), reduced: rank 1 sends s0, then the ready send
    s1, then x; rank 0 posts Irecv(ANY, ANY), receives x, then the receive r2 that takes s1, and
    waits for the Irecv, which takes s0 (non-overtaking). r2 follows x, which follows s1, so
    every execution issues s1 before r2 is posted: the run is erroneous, while the Irecv, which
    accepts s1, may be pending at s1's issue."""
    return mk({0: [irecv(T.ANY, T.ANY, 10), recv(1, 9, 1, 9), recv(1, 1, 1, 1), wait((10, 1, 1))],
               1: [send(0, 1), send(0, 1, mode="rsend"), send(0, 9)]})


class C_ReadySendBeforeItsReceive(unittest.TestCase):
    """A ready send needs the receive it is matched to posted before its issue (MPI 4.1 §3.4,
    the edge issue(r) -> issue(s) and horizon rule 7). Before 22 Oct 2026 the explorer accepted
    one whenever some receive that accepts it was posted, and the report failed with a
    ValueError on a run that issued one before its receive (its observed graph is cyclic: no
    vector clocks)."""

    def test_explorer_needs_the_matched_receive_posted(self):
        t = ready_before_receive_trace()
        s1, r2 = (1, 1), (0, 2)
        self.assertEqual(t.observed, {(0, 0): (1, 0), (0, 1): (1, 2), r2: s1})
        for B in (0, None):
            res = explore.explore(dsl.program_from_trace(t), B, comms=t.comm_members)
            if B is None:
                self.assertEqual({o.matching for o in res.outcomes}, {frozenset(t.observed.items())})
            # the Irecv pending at s1's issue no longer excuses it: every execution is erroneous
            self.assertTrue(all(s1 in explore.ready_errors(o) for o in res.outcomes), [str(o) for o in res.outcomes])

    def test_ready_send_matched_to_a_receive_posted_earlier_is_fine(self):
        # the same ranks, with the ready send first: the Irecv posted before it takes it
        t = mk({0: [irecv(T.ANY, T.ANY, 10), recv(1, 9, 1, 9), recv(1, 1, 1, 1), wait((10, 1, 1))],
                1: [send(0, 1, mode="rsend"), send(0, 1), send(0, 9)]})
        res = explore.explore(dsl.program_from_trace(t), None, comms=t.comm_members)
        self.assertTrue(any(not explore.ready_errors(o) for o in res.outcomes))
        self.assertEqual(analyze(t, with_sp=False).by_class("2-ready"), [])

    def test_report_states_the_observed_error(self):
        t = ready_before_receive_trace()
        self.assertIsNotNone(build_graph(t, t.observed, None, None).find_cycle())
        for generic in (False, True):
            rep = analyze(t, generic=generic)
            f = rep.by_class("2-ready")
            self.assertEqual([(x.level, x.recv, x.send) for x in f], [("observed", (0, 2), (1, 1))], generic)
            self.assertTrue(rep.has_error())
            self.assertEqual({x.level for x in rep.findings}, {"observed"})
            self.assertTrue(any("not predicted" in n for n in rep.notes), rep.notes)

    def test_wildcard_trace_does_not_fail(self):
        """With a wildcard receive the enumeration asks the observed graph for reachability: the
        fast checks used to raise ValueError there. Rank 0 issues its ready send, then y, which
        rank 1's wildcard takes before it posts the receive for the ready send."""
        t = mk({0: [send(1, 1, mode="rsend"), send(1, 2)], 2: [send(1, 2)],
                1: [recv(T.ANY, 2, 0, 2, widx=0), recv(0, 1, 0, 1), recv(2, 2, 2, 2)]})
        for generic in (False, True):
            rep = analyze(t, generic=generic)
            self.assertEqual([(x.recv, x.send) for x in rep.by_class("2-ready")], [((1, 1), (0, 0))])
            self.assertEqual({x.level for x in rep.findings}, {"observed"})
        ob, hb = fast.Observed(t, None), observed_hb(t)
        rc = t.recvs[(1, 0)].complete_node()
        for s in t.sends.values():
            self.assertEqual(ob.reaches(rc, s.issue_node()), hb(rc, s.issue_node()))
        fast_equals_generic(self, t)
        closure_equals_fixpoint(self, t)

    def test_head_to_head_ready_sends(self):
        """Each rank issues a ready send, then posts the receive for the other's: no single
        send is to blame, but one of the two was issued before its receive."""
        t = mk({0: [send(1, 1, mode="rsend"), recv(1, 1, 1, 1)], 1: [send(0, 1, mode="rsend"), recv(0, 1, 0, 1)]})
        from stretto.matching import observed_ready_errors
        certain, others = observed_ready_errors(t)
        self.assertEqual((certain, sorted(s.key for s in others)), ([], [(0, 0), (1, 0)]))
        rep = analyze(t)
        self.assertEqual(len(rep.by_class("2-ready")), 1)
        self.assertIn("at least one", rep.by_class("2-ready")[0].title)
        fast_equals_generic(self, t)
        # the explorer agrees: no execution has both receives posted before the ready sends
        res = explore.explore(dsl.program_from_trace(t), None, comms=t.comm_members)
        self.assertTrue(all(explore.ready_errors(o) for o in res.outcomes))

    def test_ready_send_after_its_receive_is_not_reported(self):
        from stretto.matching import observed_ready_errors
        t = ready_steal_trace()
        self.assertEqual(observed_ready_errors(t), ([], []))
        self.assertEqual(analyze(t, with_sp=False).by_class("2-ready"), [])


class C_BufferGuard(unittest.TestCase):
    """Family 3: the tracer's buffer guard (STRETTO_GUARD=1) writes a guard record when the
    program misuses a buffer that a pending operation owns; the report makes it an observed
    3-local finding, an error class, and the records are side records, not events."""

    def guard(self, what, req, **kw):
        return {"op": "guard", "what": what, "req": req, **kw}

    def test_write_to_a_pending_send_buffer(self):
        # MPI-BugBench LocalConcurrency-send-mpi_isend: rank 0 writes buf[2] between Isend and Wait
        t = mk({0: [send(1, 1, count=10, mode="isend", req=0), self.guard("access", 0, kind="write", off=8),
                    wait((0, None, None))],
                1: [recv(0, 1, 0, 1, count=10)]})
        self.assertEqual(len(t.guards), 1)
        self.assertEqual(t.observed, {(1, 0): (0, 0)})
        rep = analyze(t, with_sp=False)
        local = rep.by_class("3-local")
        self.assertEqual(len(local), 1)
        self.assertEqual(local[0].level, "observed")
        self.assertIn("rank 0 wrote to the send buffer of its pending isend to 1 tag 1", local[0].title)
        self.assertIn("byte 8", local[0].detail[0])
        self.assertTrue(rep.has_error())
        self.assertEqual(rep.levels(), ["observed:3-local"])

    def test_overlapping_receive_buffers(self):
        # LocalConcurrency-mpi_irecv-001: two Irecvs into one buffer, both pending
        t = mk({0: [irecv(1, 1, 0), irecv(1, 1, 1), self.guard("overlap", 0, call="MPI_Irecv", **{"with": 1}),
                    wait((0, 1, 1), (1, 1, 1), kind="waitall")],
                1: [send(0, 1), send(0, 1)]})
        local = analyze(t, with_sp=False).by_class("3-local")
        self.assertEqual(len(local), 1)
        self.assertIn("the buffer of MPI_Irecv (request 1) overlaps the receive buffer of its pending irecv from 1", local[0].title)

    def test_one_finding_per_operation_and_misuse(self):
        t = mk({0: [send(1, 1, mode="isend", req=0), self.guard("access", 0, kind="write", off=0),
                    self.guard("access", 0, kind="write", off=0), self.guard("modified", 0), wait((0, None, None))],
                1: [recv(0, 1, 0, 1)]})
        local = analyze(t, with_sp=False).by_class("3-local")
        self.assertEqual(len(local), 2)
        self.assertIn("changed the send buffer", local[1].title)

    def test_guard_record_inside_a_call_is_not_a_blocked_call(self):
        t = mk({0: [irecv(1, 1, 0), {"op": "begin", "call": "wait", "kind": "wait", "reqs": [0]},
                    self.guard("access", 0, kind="read", off=0), wait((0, 1, 1))],
                1: [send(0, 1)]})
        self.assertEqual(t.blocked_calls(), [])
        rep = analyze(t, with_sp=False)
        self.assertEqual(rep.classes(), ["3-local"])
        self.assertIn("rank 0 read the receive buffer of its pending irecv from 1", rep.findings[0].title)

    def test_partitioned_request_is_named_by_its_start(self):
        # partitioned calls are unmodelled; with the guard on, their Start names the request
        t = mk({0: [{"op": "unmodelled", "call": "Psend_init"}, {"op": "unmodelled", "call": "Start of a partitioned send", "req": 3},
                    self.guard("access", 3, kind="write", off=8), {"op": "wait", "kind": "wait", "reqs": [3], "done": [{"req": 3}]}],
                1: []})
        rep = analyze(t, with_sp=False)
        self.assertIn("rank 0 wrote to the buffer of its pending partitioned send", rep.by_class("3-local")[0].title)
        self.assertTrue(any("outside the modelled fragment" in n for n in rep.notes))

    def test_guard_off_under_thread_multiple(self):
        t = T.Trace()
        T._build(t, {0: [{"op": "init", "np": 1, "thread": 3, "guard": 0}, {"op": "finalize"}]})
        self.assertEqual(t.guard, {0: 0})
        self.assertTrue(any("buffer guard was asked for but off" in n for n in analyze(t, with_sp=False).notes))

    def test_the_scorer_counts_it_as_an_error(self):
        import importlib.util
        from stretto.report import ERROR_CLASSES
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "experiments", "mbi", "score.py")
        spec = importlib.util.spec_from_file_location("score", path)
        score = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(score)
        self.assertEqual(score.ERROR, set(ERROR_CLASSES))
        row = {"classes": "3-local", "levels": "observed:3-local", "run": "FINISHED"}
        self.assertEqual((score.verdict(row), score.observed_verdict(row), score.predicted_verdict(row)), ("ERROR", "ERROR", "OK"))


class C_Abort(unittest.TestCase):
    """A rank that stops before MPI_Finalize outside any MPI call, or calls MPI_Abort, gives an
    observed abort finding; a rank that stopped inside a blocking call stays 2-blocked."""

    def build(self, raw):
        t = T.Trace()
        T._build(t, {r: [{"op": "init", "np": len(raw)}] + recs for r, recs in raw.items()})
        return t

    def test_rank_stops_after_a_completed_receive(self):
        # MBI MessageRace_Recv_Send_nok: rank 0 receives the wrong message and calls abort()
        t = self.build({0: [recv(T.ANY, 1, 1, 1)], 1: [send(0, 1), {"op": "finalize"}]})
        rep = analyze(t, with_sp=False)
        ab = rep.by_class("abort")
        self.assertEqual(len(ab), 1)
        self.assertEqual(ab[0].level, "observed")
        self.assertIn("rank 0 stopped outside MPI before MPI_Finalize", ab[0].title)
        self.assertIn("observed:abort", rep.levels())
        self.assertTrue(rep.has_error())

    def test_mpi_abort(self):
        t = self.build({0: [{"op": "abort", "code": 3}], 1: [{"op": "finalize"}]})
        ab = analyze(t, with_sp=False).by_class("abort")
        self.assertEqual([f.title for f in ab], ["rank 0 called MPI_Abort with code 3"])

    def test_blocked_rank_is_not_an_abort(self):
        t = self.build({0: [{"op": "begin", "call": "recv", "src": 1, "tag": 1, "comm": 0, "count": 1, "tsize": 4}],
                        1: [{"op": "finalize"}]})
        rep = analyze(t, with_sp=False)
        self.assertEqual(rep.by_class("abort"), [])
        self.assertEqual(len(rep.by_class("2-blocked")), 1)


if __name__ == "__main__":
    unittest.main()
