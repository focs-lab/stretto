"""Read bounds (stretto.reads, docs/firstread.md): rule H1' of the horizon in the generic and
the indexed checks, the fallback to H1, contained deadlocks inside the window, and the bounds
a first-read map gives on a trace. Traces are built in memory as in test_checker."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stretto import fast, trace as T  # noqa: E402
from stretto.feasible import breakpoints, check  # noqa: E402
from stretto.horizon import compute_cut, compute_cut_fixpoint  # noqa: E402
from stretto.matching import candidates_full  # noqa: E402
from stretto.reads import NeverReads, ReadBounds, SiteMap, Table  # noqa: E402
from stretto.report import analyze  # noqa: E402
from test_checker import irecv, mk, recv, send, wait  # noqa: E402


def steal(t, rk, sk):
    return next(a for a in candidates_full(t)[0] if a.recv == rk and a.send == sk and a.kind == "steal")


def window_trace():
    """rank 0: Recv(ANY) r0, which took rank 2's message; Send x -> 3; Recv(1) r1. Rank 1 sends
    once, rank 2 once, rank 3 receives x. If r0 takes rank 1's message, r1 has nothing left
    to take; the send to rank 3 lies between them, before any read of r0's result."""
    return mk({0: [recv(T.ANY, 1, 2, 1, widx=0), send(3, 5), recv(1, 1, 1, 1)],
               1: [send(0, 1)], 2: [send(0, 1)], 3: [recv(0, 5, 0, 5)]})


class ReadBoundsInTheHorizon(unittest.TestCase):
    def test_cut_moves_to_the_bound(self):
        t = window_trace()
        a = steal(t, (0, 0), (1, 0))
        r0, r1 = t.recvs[(0, 0)], t.recvs[(0, 1)]
        h1 = check(t, a.M, a.changed, None, "L1")
        self.assertTrue(h1.feasible)
        self.assertEqual(h1.cut[0], r0.idx + 1)                 # H1: right after r0
        self.assertEqual(h1.cut[3], t.recvs[(3, 0)].idx)        # rank 3 never gets x
        b = Table({(0, r0.idx): len(t.ranks[0])})
        h1p = check(t, a.M, a.changed, None, "L1", bounds=b)
        self.assertTrue(h1p.feasible)
        self.assertEqual(h1p.cut[0], r1.idx)                    # H1': x kept, r1 has no message (rule 2)
        self.assertEqual(h1p.cut[3], len(t.ranks[3]))           # rank 3 receives x
        self.assertEqual(b.rule1(t, a.changed), {0: len(t.ranks[0])})

    def test_cut_stays_without_a_bound(self):
        """No bound for the record, or a bound not past the completion: rule H1."""
        t = window_trace()
        a = steal(t, (0, 0), (1, 0))
        h1 = check(t, a.M, a.changed, None, "L1")
        for b in (ReadBounds(), Table({}), Table({(0, t.recvs[(0, 0)].idx): 0})):
            self.assertEqual(check(t, a.M, a.changed, None, "L1", bounds=b).cut, h1.cut)
            self.assertFalse(b.extends(t, a.changed))

    def test_never_completed_receive_keeps_h1(self):
        """A receive that never completed in the trace may complete at any later test of its
        request: its bound is H1 whatever the program reads."""
        t = mk({0: [irecv(T.ANY, 1, 7), send(1, 2)], 1: [recv(0, 2, 0, 2)]})
        r = t.recvs[(0, 0)]
        self.assertFalse(r.completed)
        self.assertEqual(NeverReads().recv(t, r), r.idx + 1)

    def test_worklist_and_fixpoint_agree(self):
        t = window_trace()
        for b in (None, NeverReads()):
            for a in candidates_full(t)[0]:
                for B in breakpoints(t) + [None]:
                    res = []
                    for f in (compute_cut, compute_cut_fixpoint):
                        issued = {}
                        res.append((f(t, a.M, set(a.changed), B, "L1", set(), None, set(), issued, None, b), issued))
                    self.assertEqual(res[0], res[1], f"{a.describe(t)} B={B} bounds={b}")

    def test_fast_closure_applies_the_bound(self):
        """The indexed closure cuts as horizon.compute_cut does under the same bounds, and the
        indexed check hands an alternative with a longer window to the generic check."""
        t = window_trace()
        a = steal(t, (0, 0), (1, 0))
        r, s = t.recvs[a.recv], t.sends[a.send]
        s_obs = t.observed_send(r)
        r2 = t.recvs[a.stolen_from]
        b = NeverReads()
        for B in breakpoints(t) + [None]:
            ob = fast.Observed(t, B)
            ob.ix.ready()
            cut, issued = ob.closure(changed=[r.key], new_pairs={r.key: s.key}, dropped_recvs=[r2], dropped_sends=[s_obs],
                                     exclude2=(s_obs.rank, r.rank, ob.ix.sq_pos[s_obs.key]),
                                     exclude3=(r2.rank, s.rank, ob.ix.rq_pos[r2.key]), bounds=b)
            g = check(t, a.M, a.changed, B, "L1", bounds=b)
            self.assertEqual((cut, issued), (g.cut, g.issued), f"B={B}")
            self.assertIsNone(ob.check_alternative(a, bounds=b))
            self.assertIsNotNone(ob.check_alternative(a, bounds=Table({})))
            self.assertEqual(ob.check_alternative(a, bounds=Table({})).cut, ob.check_alternative(a).cut)

    def test_contained_deadlock_inside_the_window(self):
        """Under H1 rank 0 is cut right after r0 and its stranded receive r1 lies beyond the
        horizon: the deadlock is a single-path claim. With r0's result never read before r1,
        r1 is in the horizon, nothing can complete it, and rank 1 has finished: theorem T7."""
        t = window_trace()
        rep = analyze(t, with_sp=False)
        self.assertEqual([f for f in rep.findings if f.cls == "1d" and f.level == "L1"], [])
        r0 = t.recvs[(0, 0)]
        rep = analyze(t, with_sp=False, reads=Table({(0, r0.idx): len(t.ranks[0])}))
        l1d = [f for f in rep.findings if f.cls == "1d" and f.level == "L1"]
        self.assertEqual([(f.recv, f.send) for f in l1d], [((0, 0), (1, 0))], [str(f) for f in rep.findings])
        self.assertIn("rank 0 stops at recv", l1d[0].detail[0])
        self.assertTrue(any("H1'" in d for d in l1d[0].detail))
        self.assertTrue(any("read bounds" in n for n in rep.notes))

    def test_a_longer_window_that_fails_falls_back_to_h1(self):
        """r0 takes rank 2's message instead of rank 1's first one; with the window open, r1
        would be judged with its observed message, rank 1's second, while the first one, freed
        by the change, comes earlier on that channel and r1 accepts it: MPI would give r1 the
        first one, so the matching with r1 unchanged is not realisable. Rule H1 makes no claim
        about r1 and accepts; the report keeps that verdict."""
        t = mk({0: [recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 1, 1, widx=1), recv(2, 1, 2, 1)],
                1: [send(0, 1), send(0, 1)], 2: [send(0, 1)]})
        a = steal(t, (0, 0), (2, 0))
        b = NeverReads()
        self.assertTrue(check(t, a.M, a.changed, None, "L1").feasible)
        v = check(t, a.M, a.changed, None, "L1", bounds=b)
        self.assertFalse(v.feasible)
        for generic in (False, True):
            plain = analyze(t, with_sp=False, generic=generic)
            withb = analyze(t, with_sp=False, generic=generic, reads=b)
            alts = lambda rep: sorted((f.recv, f.send, f.B) for f in rep.findings if f.level == "L1" and f.cls in ("1f", "1b", "1a"))
            self.assertEqual(alts(plain), alts(withb))
            self.assertIn(((0, 0), (2, 0)), [(r, s) for r, s, _ in alts(withb)])

    def test_report_without_bounds_unchanged(self):
        """reads=None and H1 bounds give the same report."""
        t = window_trace()
        a = analyze(t, with_sp=True)
        b = analyze(t, with_sp=True, reads=ReadBounds())
        self.assertEqual([str(f) for f in a.findings], [str(f) for f in b.findings])

    def test_fast_and_generic_reports_agree_with_bounds(self):
        traces = [window_trace(),
                  mk({0: [recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 1, 1, widx=1), recv(2, 1, 2, 1)],
                      1: [send(0, 1), send(0, 1)], 2: [send(0, 1)]}),
                  mk({0: [irecv(T.ANY, 1, 10), recv(1, 1, 1, 1), wait((10, 2, 1))], 1: [send(0, 1)], 2: [send(0, 1)]})]
        for t in traces:
            for b in (NeverReads(), ReadBounds()):
                f = analyze(t, with_sp=False, reads=b)
                g = analyze(t, with_sp=False, generic=True, reads=b)
                self.assertEqual(sorted(str(x) for x in f.findings), sorted(str(x) for x in g.findings))


def site(i):
    return {"op": "site", "id": i, "obj": "/x/prog", "addr": 0x1000 + i, "base": 0}


def at(rec, i):
    return dict(rec, site=i)


class BoundsFromAMap(unittest.TestCase):
    """SiteMap with a resolver that names each tracer site by a table instead of addr2line."""

    # static sites: 0 Recv line 10, 1 Send line 11, 2 Recv line 12, 3 Barrier line 13 and
    # 4 Send on the same line, 5 Finalize line 14; after the Recv everything is free but the
    # Send on line 13
    MAP = {"format": "stretto-firstread 1",
           "sites": [{"id": 0, "file": "/src/p.c", "line": 10, "col": 3, "function": "main", "call": "MPI_Recv"},
                     {"id": 1, "file": "/src/p.c", "line": 11, "col": 3, "function": "main", "call": "MPI_Send"},
                     {"id": 2, "file": "/src/p.c", "line": 12, "col": 3, "function": "main", "call": "MPI_Recv"},
                     {"id": 3, "file": "/src/p.c", "line": 13, "col": 3, "function": "main", "call": "MPI_Barrier"},
                     {"id": 4, "file": "/src/p.c", "line": 13, "col": 30, "function": "main", "call": "MPI_Send"},
                     {"id": 5, "file": "/src/p.c", "line": 14, "col": 3, "function": "main", "call": "MPI_Finalize"}],
           "results": [{"post": 0, "complete": 0, "free": [1, 2, 3, 5]}]}
    WHERE = {0: ("main", "p.c", 10), 1: ("main", "p.c", 11), 2: ("main", "p.c", 12), 3: ("main", "p.c", 13),
             4: ("main", "p.c", 13), 5: ("main", "p.c", 14)}
    BARRIER = {"op": "coll", "name": "barrier", "comm": 0, "root": -1, "count": 1, "tsize": 4}

    def resolve(self, t, rank, idx):
        s = t.site_record(rank, idx)
        return None if s is None else self.WHERE.get(s["id"])

    def trace(self, tail):
        raw0 = [site(i) for i in range(6)] + [at(recv(T.ANY, 1, 2, 1, widx=0), 0)] + tail
        t = mk({0: raw0, 1: [send(0, 1)], 2: [send(0, 1)], 3: [recv(0, 5, 0, 5), recv(0, 6, 0, 6)]})
        return t, t.recvs[(0, 0)].idx

    def test_window_up_to_the_first_site_not_free(self):
        """The barrier record can only come from the free barrier of its line; the send record
        on that line can only come from the send that is not free."""
        t, k = self.trace([at(send(3, 5), 1), at(recv(1, 1, 1, 1), 2), at(self.BARRIER, 3), at(send(3, 6), 4)])
        b = SiteMap(self.MAP, self.resolve)
        self.assertEqual(b.limit(t, 0, k, k), k + 4)

    def test_record_without_a_site_ends_the_window(self):
        t, k = self.trace([at(send(3, 5), 1), recv(1, 1, 1, 1)])
        self.assertEqual(SiteMap(self.MAP, self.resolve).limit(t, 0, k, k), k + 2)

    def test_record_of_another_kind_ends_the_window(self):
        """A record matched by line to a free site of another kind of call is not certified."""
        t, k = self.trace([at(recv(1, 1, 1, 1), 1)])            # a receive whose line holds only a Send
        self.assertEqual(SiteMap(self.MAP, self.resolve).limit(t, 0, k, k), k + 1)

    def test_finalize_ends_the_window_at_the_end(self):
        t, k = self.trace([at(send(3, 5), 1)])
        # mk appends a finalize record without a site: give it one
        t.ranks[0][-1].raw = dict(t.ranks[0][-1].raw or {}, site=5)
        t._site_ids[0][len(t.ranks[0]) - 1] = 5
        self.assertEqual(SiteMap(self.MAP, self.resolve).limit(t, 0, k, k), len(t.ranks[0]))

    def test_unknown_pair_is_h1(self):
        m = dict(self.MAP, results=[])
        t, k = self.trace([at(send(3, 5), 1)])
        self.assertEqual(SiteMap(m, self.resolve).limit(t, 0, k, k), k + 1)
        self.assertEqual(SiteMap(m, lambda t, r, i: None).limit(t, 0, k, k), k + 1)

    def test_rejects_other_files(self):
        with self.assertRaises(ValueError):
            SiteMap({"sites": [], "results": []}, self.resolve)


if __name__ == "__main__":
    unittest.main()
