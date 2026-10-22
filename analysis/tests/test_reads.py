"""Read bounds (stretto.reads, docs/firstread.md): rule H1' of the horizon in the generic and
the indexed checks, the fallback to H1, contained deadlocks inside the window, single-path
consequences promoted to L1 when a witness respects the bounds, and the bounds a first-read
map gives on a trace. Traces are built in memory as in test_checker."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stretto import fast, trace as T  # noqa: E402
from stretto.feasible import breakpoints, check  # noqa: E402
from stretto.horizon import compute_cut, compute_cut_fixpoint  # noqa: E402
from stretto.matching import candidates_full  # noqa: E402
from stretto.reads import NeverReads, ReadBounds, SiteMap, Table, erroneous  # noqa: E402
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
        self.assertEqual(b.rule1(t, a.changed, a.M), {0: len(t.ranks[0])})

    def test_cut_stays_without_a_bound(self):
        """No bound for the record, or a bound not past the completion: rule H1."""
        t = window_trace()
        a = steal(t, (0, 0), (1, 0))
        h1 = check(t, a.M, a.changed, None, "L1")
        for b in (ReadBounds(), Table({}), Table({(0, t.recvs[(0, 0)].idx): 0})):
            self.assertEqual(check(t, a.M, a.changed, None, "L1", bounds=b).cut, h1.cut)
            self.assertFalse(b.extends(t, a.changed, a.M))

    def test_never_completed_receive_keeps_h1(self):
        """A receive that never completed in the trace may complete at any later test of its
        request: its bound is H1 whatever the program reads."""
        t = mk({0: [irecv(T.ANY, 1, 7), send(1, 2)], 1: [recv(0, 2, 0, 2)]})
        r = t.recvs[(0, 0)]
        self.assertFalse(r.completed)
        self.assertEqual(NeverReads().recv(t, r, next(iter(t.sends))), r.idx + 1)

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

    def test_fast_closure_equals_the_worklist_on_more_traces(self):
        """The indexed closure and horizon.compute_cut cut alike under read bounds that keep the
        whole rest of the changed rank, for every steal of the idiom traces kept with the tests
        and of the traces built here, at every breakpoint."""
        import glob
        traces = [window_trace(), loop_trace(),
                  mk({0: [recv(T.ANY, 1, 1, 1, widx=0), recv(T.ANY, 1, 1, 1, widx=1), recv(2, 1, 2, 1)],
                      1: [send(0, 1), send(0, 1)], 2: [send(0, 1)]})]
        golden = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "openmpi")
        traces += [T.load(d) for d in sorted(glob.glob(os.path.join(golden, "*")))]
        b = NeverReads()
        n = 0
        for t in traces:
            for a in candidates_full(t)[0]:
                if a.kind != "steal":
                    continue
                r, s = t.recvs[a.recv], t.sends[a.send]
                s_obs = t.observed_send(r)
                r2 = t.recvs[a.stolen_from] if a.stolen_from is not None else None
                for B in breakpoints(t) + [None]:
                    ob = fast.Observed(t, B)
                    ob.ix.ready()
                    cut, issued = ob.closure(changed=[r.key], new_pairs={r.key: s.key},
                                             dropped_recvs=[r2] if r2 is not None else (),
                                             dropped_sends=[s_obs] if s_obs is not None else (),
                                             exclude2=(s_obs.rank, r.rank, ob.ix.sq_pos[s_obs.key]) if s_obs is not None else None,
                                             exclude3=(r2.rank, s.rank, ob.ix.rq_pos[r2.key]) if r2 is not None else None,
                                             bounds=b)
                    ref = {}
                    self.assertEqual((cut, issued), (compute_cut(t, a.M, set(a.changed), B, "L1", set(), None, set(), ref, None, b), ref),
                                     f"{a.describe(t)} B={B}")
                    n += 1
        self.assertGreater(n, 50)

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

    def test_an_erring_message_keeps_h1(self):
        """r0 takes rank 1's message, which is longer than r0 (a truncation) or of another type:
        the call that completes r0 returns an error code, at which the program aborts or may
        branch, so rule H1 holds whatever the map says. With a message that fits, the same
        bounds keep the window, and its stranded receive gives a contained deadlock (as in
        test_contained_deadlock_inside_the_window)."""
        b = NeverReads()
        for kind in ("truncate", "type", None):
            long = kind == "truncate"
            t = mk({0: [recv(T.ANY, 1, 2, 1, widx=0), send(3, 5), recv(1, 1, 1, 1, count=2 if long else 1)],
                    1: [send(0, 1, count=2 if long else 1)], 2: [send(0, 1)], 3: [recv(0, 5, 0, 5)]})
            if kind == "type":
                t.sends[(1, 0)].tsig, t.recvs[(0, 0)].tsig = "MPI_FLOAT", "MPI_INT"
            a = steal(t, (0, 0), (1, 0))
            r0 = t.recvs[(0, 0)]
            self.assertEqual(erroneous(r0, t.sends[(1, 0)]), kind)
            self.assertEqual(b.extends(t, a.changed, a.M), kind is None, kind)
            self.assertEqual(b.rule1(t, a.changed, a.M), {0: r0.idx + 1 if kind else len(t.ranks[0])}, kind)
            if kind:
                self.assertEqual(check(t, a.M, a.changed, None, "L1", bounds=b).cut, check(t, a.M, a.changed, None, "L1").cut)
            rep = analyze(t, with_sp=False, reads=b)
            f = next(f for f in rep.findings if (f.recv, f.send) == ((0, 0), (1, 0)) and f.cls in ("1a", "1f"))
            self.assertEqual(f.cls, "1a" if kind else "1f")
            self.assertEqual(any(d.startswith("read bounds") for d in f.detail), kind is None, f.detail)
            l1d = [g for g in rep.findings if g.cls == "1d" and g.level == "L1"]
            self.assertEqual(bool(l1d), kind is None, [str(g) for g in rep.findings])

    def test_erroneous_follows_the_type_rules(self):
        """reads.erroneous decides by the count and by types.compatible: a receive of
        MPI_PACKED takes a message of any type that fits, MPI_BYTE against a typed signature
        decides nothing, and two typed signatures that differ err."""
        t = mk({0: [recv(T.ANY, 1, 1, 1, count=2)], 1: [send(0, 1)]})
        r, s = t.recvs[(0, 0)], t.sends[(1, 0)]
        for rsig, ssig, want in (("MPI_PACKED", "MPI_INT", None), ("MPI_BYTE", "MPI_INT", None),
                                 ("MPI_BYTE", "MPI_BYTE", None), ("MPI_INT", "MPI_FLOAT", "type"), ("MPI_INT", "MPI_INT", None)):
            r.tsig, s.tsig = rsig, ssig
            self.assertEqual(erroneous(r, s), want, (rsig, ssig))
        r.tsig, s.tsig, s.count = "MPI_PACKED", "MPI_INT", 3                # 12 bytes into 8
        self.assertEqual(erroneous(r, s), "truncate")

    def test_erring_receive_that_never_completes_keeps_its_deadlock(self):
        """A nonblocking changed receive that would truncate, whose rank stops before the wait
        that completes it: the error is never returned, and the contained deadlock stands."""
        t = mk({0: [irecv(T.ANY, 1, 10), recv(1, 1, 1, 1), wait((10, 2, 1))],
                1: [send(0, 1, count=2)], 2: [send(0, 1)]})
        rep = analyze(t, with_sp=False, reads=NeverReads())
        l1d = [f for f in rep.findings if f.cls == "1d" and f.level == "L1"]
        self.assertEqual([(f.recv, f.send) for f in l1d], [((0, 0), (1, 0))], [str(f) for f in rep.findings])

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


def loop_trace():
    """The shape of MBI's MessageRace_Loop_Isend_Recv: rank 0 receives four messages from ANY,
    then two from rank 3; ranks 1 and 2 send two each to 0 and one to 3; rank 3 forwards a
    message to 0 after each of theirs. If a wildcard receive takes rank 3's first message,
    rank 0's last receive waits forever."""
    return mk({0: [recv(T.ANY, 0, 2, 0, widx=0), recv(T.ANY, 0, 1, 0, widx=1), recv(T.ANY, 0, 2, 0, widx=2),
                   recv(T.ANY, 0, 1, 0, widx=3), recv(3, 0, 3, 0), recv(3, 0, 3, 0)],
               1: [send(0, 0), send(0, 0), send(3, 0)], 2: [send(0, 0), send(0, 0), send(3, 0)],
               3: [recv(1, 0, 1, 0), send(0, 0), recv(2, 0, 2, 0), send(0, 0)]})


class WitnessPromotion(unittest.TestCase):
    """A consequence the single-path explorer finds is an L1 finding when one of its witness
    executions respects the read bounds (reads.witness_respects)."""

    def levels(self, rep, cls):
        return sorted({f.level for f in rep.findings if f.cls == cls and f.recv is not None})

    def promoted(self, rep):
        return [f for f in rep.findings if "its read bounds" in f.title]

    def test_loop_deadlock_is_promoted(self):
        t = loop_trace()
        plain = analyze(t, with_sp=True)
        self.assertEqual(self.levels(plain, "1d"), ["L1+SP"])
        rep = analyze(t, with_sp=True, reads=NeverReads())
        self.assertEqual(self.levels(rep, "1d"), ["L1"])
        self.assertEqual(self.levels(rep, "1c"), ["L1"])
        promoted = [f for f in rep.findings if f.cls == "1d" and f.level == "L1"]
        for f in promoted:
            self.assertEqual(f.certainty, "some")
            self.assertTrue(f.pin and f.pin.startswith("STRETTO_PIN="), f)
        # the same alternatives, the same thresholds
        alts = lambda r: sorted((f.recv, f.send, f.B) for f in r.findings if f.cls == "1f")
        self.assertEqual(alts(plain), alts(rep))

    def test_an_early_read_keeps_the_single_path_level(self):
        """With the result of every wildcard receive read at once (H1), every witness of the
        deadlock has rank 0 issue records after a changed result: nothing is promoted."""
        t = loop_trace()
        plain = analyze(t, with_sp=True)
        rep = analyze(t, with_sp=True, reads=ReadBounds())
        self.assertEqual([str(f) for f in plain.findings], [str(f) for f in rep.findings])

    def test_witness_condition(self):
        """reads.witness_respects on the explorer's witnesses of one alternative: with no read
        at all every witness passes; with the result read at once, a witness in which rank 0
        goes past the changed receive does not."""
        from stretto import dsl, explore
        from stretto.reads import witness_respects
        t = loop_trace()
        pos = {}
        prog = dsl.program_from_trace(t, pos)
        self.assertEqual(len(pos[0]), len(prog[0]))
        self.assertEqual([t.ranks[0][i].op for i in pos[0]], ["recv"] * 6)
        res = explore.explore(prog, None, force={(0, 0): (3, 0)}, comms=t.comm_members)
        outs = res.outcomes_with((0, 0), (3, 0))
        self.assertTrue(outs)
        for o in outs:
            self.assertIsNone(witness_respects(t, o, pos, NeverReads()), str(o))
            self.assertIsNotNone(witness_respects(t, o, pos, ReadBounds()), str(o))

    def test_records_outside_the_model_block_promotion(self):
        """A rank that passes a probe or a test in the witness: the program could do anything
        there (the probe could block or report another message, the test fail)."""
        iprobe = {"op": "probe", "kind": "iprobe", "src": 0, "anysrc": 1, "tag": 9, "comm": 0, "flag": 0}
        tst = {"op": "wait", "kind": "test", "reqs": [5], "done": []}
        for extra in (iprobe, tst):
            t = mk({0: [recv(T.ANY, 1, 2, 1, widx=0), extra, recv(1, 1, 1, 1)], 1: [send(0, 1)], 2: [send(0, 1)]})
            rep = analyze(t, with_sp=True, reads=NeverReads())
            self.assertEqual(self.promoted(rep), [], [str(f) for f in rep.findings])
            self.assertIn("L1+SP", self.levels(rep, "1d"))
            t = mk({0: [recv(T.ANY, 1, 2, 1, widx=0), recv(1, 1, 1, 1), extra], 1: [send(0, 1)], 2: [send(0, 1)]})
            rep = analyze(t, with_sp=True, reads=NeverReads())
            self.assertTrue(self.promoted(rep), "a record the stopped rank never reaches does not matter")

    def test_no_promotion_past_an_error(self):
        """r0 takes rank 1's message, which it truncates; the explorer goes on, and r1 then has
        no message: a deadlock in every single-path execution. A program aborts at r0's error
        (default handler) or may branch on its code (MPI_ERRORS_RETURN), so the deadlock stays a
        single-path claim, whatever the bounds; with a message that fits it is promoted. The
        truncation itself, the first error, is promoted (as the alternative's own 1a is L1)."""
        for long in (True, False):
            t = mk({0: [recv(T.ANY, 1, 2, 1, widx=0), recv(1, 1, 1, 1, count=2 if long else 1)],
                    1: [send(0, 1, count=2 if long else 1)], 2: [send(0, 1)]})
            rep = analyze(t, with_sp=True, reads=NeverReads())
            ends = [f for f in self.promoted(rep) if f.cls in ("1c", "1d")]
            self.assertEqual(bool(ends), not long, [str(f) for f in rep.findings])
            self.assertIn("L1+SP" if long else "L1", self.levels(rep, "1d"))
            self.assertEqual([f.error for f in self.promoted(rep) if f.cls == "1a"], ["truncate"] if long else [])

    def test_a_truncation_is_promoted_up_to_itself(self):
        """r0 (8 bytes) takes rank 2's 4-byte message instead of rank 1's 8 bytes, which r1 (4
        bytes) must then take: a truncation that follows from the change. The witness counts up
        to that error: rank 0 is judged at r1, where it stops being certain, so the bound of r0
        may end right after r1 (rank 0 goes on to MPI_Finalize in the witness). With r0's
        result read before r1 the truncation stays a single-path claim."""
        t = mk({0: [recv(T.ANY, 1, 1, 1, count=2, widx=0), recv(T.ANY, 1, 2, 1, widx=1)],
                1: [send(0, 1, count=2)], 2: [send(0, 1)]})
        r0, r1 = t.recvs[(0, 0)], t.recvs[(0, 1)]

        def trunc(rep):
            return sorted({f.level for f in rep.findings if f.cls == "1a" and f.error == "truncate"
                           and (f.recv, f.send) == ((0, 0), (2, 0))})
        self.assertEqual(trunc(analyze(t, with_sp=True)), ["L1+SP"])
        self.assertEqual(trunc(analyze(t, with_sp=True, reads=Table({(0, r0.idx): r1.idx + 1}))), ["L1"])
        self.assertEqual(trunc(analyze(t, with_sp=True, reads=ReadBounds())), ["L1+SP"])

    def test_witness_condition_with_errors(self):
        """witness_respects on a witness with a truncation: refused for its end, accepted for
        the truncation itself, refused for another kind of error."""
        from stretto import dsl, explore
        from stretto.reads import witness_respects
        t = mk({0: [recv(T.ANY, 1, 1, 1, count=2, widx=0), recv(T.ANY, 1, 2, 1, widx=1)],
                1: [send(0, 1, count=2)], 2: [send(0, 1)]})
        pos = {}
        prog = dsl.program_from_trace(t, pos)
        outs = explore.explore(prog, None, force={(0, 0): (2, 0)}, comms=t.comm_members).outcomes_with((0, 0), (2, 0))
        self.assertTrue(outs and all("truncate" in o.error_kinds for o in outs))
        for o in outs:
            self.assertIn("MPI error", witness_respects(t, o, pos, NeverReads()))
            self.assertIsNone(witness_respects(t, o, pos, NeverReads(), "truncate"))
            self.assertIn("other than", witness_respects(t, o, pos, NeverReads(), "rsend-no-receive"))

    def test_a_rank_without_finalize_blocks_promotion(self):
        """A rank that ends its trace without MPI_Finalize goes on in the program: when it
        finishes its recorded calls in the witness, nothing says it stops there."""
        raw = {0: [{"op": "init", "np": 3}, recv(T.ANY, 1, 2, 1, widx=0), recv(1, 1, 1, 1), {"op": "finalize"}],
               1: [{"op": "init", "np": 3}, send(0, 1)],
               2: [{"op": "init", "np": 3}, send(0, 1), {"op": "finalize"}]}
        t = T.Trace()
        T._build(t, raw)
        rep = analyze(t, with_sp=True, reads=NeverReads())
        self.assertEqual(self.promoted(rep), [], [str(f) for f in rep.findings])
        self.assertNotIn("L1", self.levels(rep, "1d"))


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
