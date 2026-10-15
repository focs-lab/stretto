"""Explorer tests T1-T10 from the audit (docs/plan Part A.2).

T10 is the differential test: the reduced explorer must produce exactly the outcome set
of the unreduced one over the idiom twins, T1-T9 and random programs."""
import random
import unittest

from rematch.dsl import ANY, Coll, If, Irecv, Isend, Recv, Send, Wait, Waitany
from rematch.explore import ExploreError, explore

from idioms_dsl import ALL

BS = (0, 64, None)


def kinds(prog, B, **kw):
    return explore(prog, B, **kw).kinds()


class T1_RendezvousNonblocking(unittest.TestCase):
    prog = {0: [Isend(1, 1, 64, req="q"), Wait("q"), Recv(1, 2, 64)],
            1: [Isend(0, 2, 64, req="p"), Wait("p"), Recv(0, 1, 64)]}

    def test_deadlock_below_threshold(self):
        for B in (0, 63):
            self.assertEqual(kinds(self.prog, B), {"deadlock"}, B)

    def test_done_at_or_above(self):
        for B in (64, None):
            self.assertEqual(kinds(self.prog, B), {"done"}, B)


class T2_RendezvousRing(unittest.TestCase):
    blocking = {0: [Send(1, 1, 64), Recv(2, 1, 64)], 1: [Send(2, 1, 64), Recv(0, 1, 64)],
                2: [Send(0, 1, 64), Recv(1, 1, 64)]}
    nonblocking = {0: [Isend(1, 1, 64, req="a"), Wait("a"), Recv(2, 1, 64)],
                   1: [Isend(2, 1, 64, req="a"), Wait("a"), Recv(0, 1, 64)],
                   2: [Isend(0, 1, 64, req="a"), Wait("a"), Recv(1, 1, 64)]}

    def test_threshold(self):
        for B, want in ((0, {"deadlock"}), (63, {"deadlock"}), (64, {"done"}), (None, {"done"})):
            self.assertEqual(kinds(self.blocking, B), want, B)
            self.assertEqual(kinds(self.nonblocking, B), want, B)


class T3_TruncateKeepsSiblings(unittest.TestCase):
    prog = {0: [Irecv(1, 1, 4, req="a"), Irecv(ANY, 2, 8, req="b"), Wait("a"), Wait("b")],
            1: [Send(0, 1, 8)], 2: [Send(0, 2, 8)], 3: [Send(0, 2, 8)]}

    def test_truncation_keeps_siblings(self):
        res = explore(self.prog, None)
        self.assertIn("truncate", res.kinds())
        self.assertTrue(all("truncate" in o.error_kinds for o in res.outcomes))
        # the wildcard still takes either of the two tag-2 sends after the error
        self.assertEqual({o.orphans for o in res.outcomes}, {frozenset({(2, 0)}), frozenset({(3, 0)})})
        self.assertIn(((0, 1), (2, 0)), res.matches)
        self.assertIn(((0, 1), (3, 0)), res.matches)


class T4_WaitanyOverSends(unittest.TestCase):
    prog = {0: [Isend(1, 1, 4, req="c"), Isend(2, 1, 4, req="d"),
                Waitany(("c", "d"), into="i"), Waitany(("c", "d"), into="j"),
                Send(lambda e: 1 if e["i"] == e["j"] else 2, 9, 4)],
            1: [Recv(0, ANY), Recv(0, 9)], 2: [Recv(0, ANY)]}

    def test_permutation(self):
        for B in (0, None):
            res = explore(self.prog, B)
            self.assertEqual(res.kinds(), {"deadlock"}, B)          # i != j always: send goes to 2
            self.assertTrue(all((0, 2) in o.orphans for o in res.outcomes))


class T5_MixedTagFifoTwoWildcards(unittest.TestCase):
    prog = {0: [Recv(ANY, 11, into="m"), Recv(1, ANY), Recv(ANY, 22)],
            1: [Send(0, 11), Send(0, 22)], 2: [Send(0, 11), Send(0, 22)]}

    def test_exact_matchings(self):
        res = explore(self.prog, None)
        self.assertEqual(res.kinds(), {"done"})
        got = {(o.matching, o.orphans) for o in res.outcomes}
        want = {
            (frozenset({((0, 0), (2, 0)), ((0, 1), (1, 0)), ((0, 2), (2, 1))}), frozenset({(1, 1)})),
            (frozenset({((0, 0), (2, 0)), ((0, 1), (1, 0)), ((0, 2), (1, 1))}), frozenset({(2, 1)})),
            (frozenset({((0, 0), (1, 0)), ((0, 1), (1, 1)), ((0, 2), (2, 1))}), frozenset({(2, 0)})),
        }
        self.assertEqual(got, want)
        self.assertNotIn(((0, 0), (1, 1)), res.matches)
        self.assertNotIn(((0, 0), (2, 1)), res.matches)
        self.assertFalse(any(r == (0, 1) and s[0] == 2 for r, s in res.matches))


class T6_OutOfOrderCompletion(unittest.TestCase):
    def test_two_sources(self):
        prog = {0: [Irecv(1, 11, req="a"), Irecv(2, 22, req="b"), Wait("b"), Wait("a")],
                1: [Send(0, 11)], 2: [Send(0, 22)]}
        for B in (0, None):
            res = explore(prog, B)
            self.assertEqual(res.kinds(), {"done"}, B)
            self.assertEqual(res.matches, {((0, 0), (1, 0)), ((0, 1), (2, 0))})

    def test_one_source_fifo(self):
        prog = {0: [Irecv(1, 11, req="a"), Irecv(1, 22, req="b"), Wait("b"), Wait("a")],
                1: [Send(0, 11), Send(0, 22)]}
        res = explore(prog, None)
        self.assertEqual(res.kinds(), {"done"})
        self.assertNotIn(((0, 0), (1, 1)), res.matches)


class T7_CollectiveWithLiveWildcard(unittest.TestCase):
    A = {0: [Irecv(ANY, 1, 4, req="a"), Coll("bcast"), Wait("a")],
         1: [Send(0, 1, 4), Coll("bcast")], 2: [Send(0, 1, 4), Coll("barrier")]}
    Bp = {0: [Irecv(ANY, 1, 4, req="a"), Coll("bcast"), Wait("a")],
          1: [Send(0, 1, 4), Coll("bcast")], 2: [Send(0, 1, 4), Coll("bcast")]}

    def test_mismatch_keeps_matchings(self):
        res = explore(self.A, None)
        self.assertIn("collective-mismatch", res.kinds())
        self.assertTrue(all("collective-mismatch" in o.error_kinds for o in res.outcomes))
        self.assertIn(((0, 0), (1, 0)), res.matches)
        self.assertIn(((0, 0), (2, 0)), res.matches)

    def test_done_twin(self):
        res = explore(self.Bp, None)
        self.assertEqual(res.kinds(), {"done"})
        self.assertEqual(res.matches, {((0, 0), (1, 0)), ((0, 0), (2, 0))})
        self.assertEqual({o.orphans for o in res.outcomes}, {frozenset({(1, 0)}), frozenset({(2, 0)})})


class T8_WaitanyIndexSteersDestination(unittest.TestCase):
    prog = {0: [Irecv(ANY, 1, 4, req="a"), Isend(1, 2, 4, req="b"),
                Waitany(("a", "b"), into="i"), Send(lambda e: 1 if e["i"] == 0 else 2, 3, 4),
                Wait("a"), Wait("b")],
            1: [Recv(0, 2, 4), Send(0, 1, 4), Recv(0, 3, 4)],
            2: [Send(0, 1, 4), Recv(0, 3, 4)]}

    def test_both_indices_reachable(self):
        inf = explore(self.prog, None)
        dests = {s for o in inf.outcomes for r, s in o.matching if r in ((1, 1), (2, 1))}
        self.assertTrue(any(s[0] == 0 for s in dests))
        self.assertGreaterEqual(len(inf.sigs()), 2)
        zero = explore(self.prog, 0)
        self.assertTrue(zero.sigs() <= inf.sigs() or len(zero.sigs()) <= len(inf.sigs()))


class T9_CommunicatorScoping(unittest.TestCase):
    def test_disjoint_comms_do_not_fuse(self):
        prog = {0: [Coll("bcast", comm=0)], 1: [Coll("bcast", comm=1)]}
        res = explore(prog, None, comms={0: [0], 1: [1]})
        self.assertEqual(res.kinds(), {"done"})
        res = explore(prog, None, comms={0: [0, 1], 1: [0, 1]})
        self.assertEqual(res.kinds(), {"deadlock"})

    def test_unknown_comm_is_an_error(self):
        with self.assertRaises(ExploreError):
            explore({0: [Coll("barrier", comm=5)]}, None)

    def test_sub_communicator_barrier(self):
        prog = {0: [Coll("barrier", 1)], 1: [Coll("barrier", 1)], 2: [Send(3, 1)], 3: [Recv(2, 1)]}
        res = explore(prog, None, comms={1: [0, 1]})
        self.assertEqual(res.kinds(), {"done"})


class T11_ForceBlocked(unittest.TestCase):
    def test_force_starvation_is_named(self):
        prog = {0: [Recv(ANY, 7), Send(2, 7)], 1: [Send(0, 7)], 2: [Recv(0, 7), Send(0, 7)]}
        res = explore(prog, None, force={(0, 0): (2, 0)})
        self.assertEqual(res.kinds(), {"force-blocked"})
        self.assertEqual(explore(prog, None).kinds(), {"done"})


class T12_MetadataBoundAtCompletion(unittest.TestCase):
    def test_nonblocking_receive_binding(self):
        prog = {0: [Irecv(1, 1, 4, req="a", into="st"),
                    If(lambda e: "st" in e, (Send(1, 77, 4),), (Send(2, 77, 4),)),
                    Wait("a")],
                1: [Send(0, 1, 4), Recv(0, 77, 4)],
                2: [Recv(0, 77, 4)]}
        # `st` is bound only at Wait, after the If: the else branch is the only path, so the
        # send goes to rank 2 and rank 1 starves. Deterministic, whatever the interleaving.
        res = explore(prog, None)
        self.assertEqual(res.kinds(), {"deadlock"})
        prog2 = {0: [Irecv(1, 1, 4, req="a", into="st"), Wait("a"),
                     If(lambda e: e["st"].src == 1, (Send(1, 77, 4),), (Send(2, 77, 4),))],
                 1: [Send(0, 1, 4), Recv(0, 77, 4)]}
        res = explore(prog2, None)
        self.assertEqual(res.kinds(), {"done"})


class T13_NonblockingCollective(unittest.TestCase):
    def test_icoll_completes_at_wait(self):
        prog = {0: [Coll("barrier", req="c"), Send(1, 1), Wait("c")],   # nonblocking barrier
                1: [Recv(0, 1), Coll("barrier")]}
        self.assertEqual(kinds(prog, 0), {"done"})
        blocking = {0: [Coll("barrier"), Send(1, 1)], 1: [Recv(0, 1), Coll("barrier")]}
        self.assertEqual(kinds(blocking, 0), {"deadlock"})


class T14_Leaks(unittest.TestCase):
    def test_leaked_receive_reported(self):
        res = explore({0: [Irecv(1, 1, req="a")], 1: []}, None)
        self.assertEqual(res.kinds(), {"done"})
        self.assertEqual(res.outcomes[0].leaked, frozenset({(0, 0)}))


# ---------------------------------------------------------------- T10 differential

def random_program(rng: random.Random, nranks: int):
    prog = {}
    for r in range(nranks):
        ops = []
        n = rng.randint(1, 5)
        pending_reqs = []
        for k in range(n):
            kind = rng.choice(["send", "send", "recv", "recv", "irecv", "isend", "coll"])
            others = [x for x in range(nranks) if x != r]
            if kind == "send":
                ops.append(Send(rng.choice(others), rng.choice([1, 2]), rng.choice([4, 64])))
            elif kind == "isend":
                name = f"s{k}"
                ops.append(Isend(rng.choice(others), rng.choice([1, 2]), rng.choice([4, 64]), req=name))
                pending_reqs.append(name)
            elif kind == "recv":
                ops.append(Recv(rng.choice(others + [ANY]), rng.choice([1, 2, ANY]), 64))
            elif kind == "irecv":
                name = f"r{k}"
                ops.append(Irecv(rng.choice(others + [ANY]), rng.choice([1, 2, ANY]), 64, req=name))
                pending_reqs.append(name)
            else:
                ops.append(Coll(rng.choice(["barrier", "bcast"])))
            if pending_reqs and rng.random() < 0.5:
                if len(pending_reqs) >= 2 and rng.random() < 0.5:
                    ops.append(Waitany(tuple(pending_reqs)))
                    pending_reqs.pop(rng.randrange(len(pending_reqs)))
                else:
                    ops.append(Wait(pending_reqs.pop()))
        for name in pending_reqs:
            ops.append(Wait(name))
        prog[r] = ops
    return prog


class T10_Differential(unittest.TestCase):
    def _same(self, prog, B, **kw):
        red = explore(prog, B, reduce=True, max_states=400_000, **kw)
        naive = explore(prog, B, reduce=False, max_states=400_000, **kw)
        self.assertEqual(red.sigs(), naive.sigs(), f"B={B}\n{prog}")

    def test_twins(self):
        for n, mk in sorted(ALL.items()):
            twin = mk()
            if n == 10:
                continue  # 4 ranks x 3 iterations: too large for the unreduced explorer
            for B in (0, None):
                self._same(twin, B)

    def test_fixed(self):
        for cls in (T1_RendezvousNonblocking, T3_TruncateKeepsSiblings, T4_WaitanyOverSends,
                    T5_MixedTagFifoTwoWildcards, T8_WaitanyIndexSteersDestination):
            for B in BS:
                self._same(cls.prog, B)
        for B in BS:
            self._same(T2_RendezvousRing.blocking, B)
            self._same(T2_RendezvousRing.nonblocking, B)
            self._same(T7_CollectiveWithLiveWildcard.A, B)
            self._same(T7_CollectiveWithLiveWildcard.Bp, B)

    def test_random(self):
        rng = random.Random(20260905)
        for i in range(200):
            prog = random_program(rng, rng.choice([2, 3]))
            for B in BS:
                try:
                    self._same(prog, B)
                except RuntimeError:
                    pass  # state-space cap on the unreduced side


if __name__ == "__main__":
    unittest.main()
