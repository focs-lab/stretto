"""When a buffer-guard finding changes data, by threshold (stretto.buffers, docs/model.md section
7). Traces are built in memory in the tracer's format, with the guard's records where the tracer
writes them: an access before the call that follows it, an overlap before a blocking call's own
records or after a nonblocking one's."""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest

from stretto import buffers
from stretto.report import analyze, local_findings
from test_checker import irecv, mk, recv, send, wait

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

N = 100      # ints: 400-byte messages


def access(req, kind="write", off=0):
    return {"op": "guard", "what": "access", "req": req, "kind": kind, "off": off}


def finding(t):
    f = [x for x in local_findings(t)]
    assert len(f) == 1, [str(x) for x in f]
    return f[0]


def verdicts(t):
    """{B: verdict} of the only guard finding's consequence."""
    from stretto.report import local_consequence
    ops = {(x.proc, x.req): x for x in list(t.sends.values()) + list(t.recvs.values()) if x.req is not None}
    o = buffers.Orders(t)
    rec = t.guards[0]
    cons = local_consequence(t, rec, ops.get((rec.rank, rec.raw.get("req"))), ops, lambda: o)
    return dict(cons.profile)


class SendWrites(unittest.TestCase):
    def test_write_before_a_token_corrupts_below_the_size_in_every_execution(self):
        """Idiom 37's shape: the receive is posted after a token sent after the write, so a
        rendezvous reads the buffer after the write; a copy in the call reads it before."""
        t = mk({0: [send(1, 1, N, mode="isend", req=0), access(0), send(1, 2), wait((0, None, None))],
                1: [recv(0, 2, 0, 2), recv(0, 1, 0, 1, count=N)]})
        v = verdicts(t)
        self.assertEqual(v[0], buffers.EVERY)
        self.assertEqual(v[4], buffers.EVERY)
        self.assertEqual(v[400], buffers.COPIED)
        self.assertEqual(v[None], buffers.COPIED)
        f = finding(t)
        self.assertEqual(f.B, "B < 400")
        self.assertTrue(any("every execution" in d for d in f.detail), f.detail)

    def test_write_unordered_with_the_receive_corrupts_in_some_executions(self):
        t = mk({0: [send(1, 1, N, mode="isend", req=0), access(0), wait((0, None, None))],
                1: [recv(0, 1, 0, 1, count=N)]})
        v = verdicts(t)
        self.assertEqual((v[0], v[400]), (buffers.SOME, buffers.COPIED))
        self.assertEqual(finding(t).B, "B < 400")

    def test_write_after_the_receive_completed_is_harmless(self):
        """The receiver acknowledges after its receive completes and the sender writes after the
        acknowledgement: every byte has been read before the write, whatever the protocol."""
        t = mk({0: [send(1, 1, N, mode="isend", req=0), recv(1, 3, 1, 3), access(0), wait((0, None, None))],
                1: [recv(0, 1, 0, 1, count=N), send(0, 3)]})
        v = verdicts(t)
        self.assertTrue(all(x in (buffers.HARMLESS, buffers.COPIED) for x in v.values()), v)
        f = finding(t)
        self.assertEqual(f.B, "")
        self.assertIn("3-local", {f.cls})                 # still erroneous: MPI owns the buffer

    def test_buffered_send_is_copied_at_every_threshold(self):
        t = mk({0: [send(1, 1, N, mode="ibsend", req=0), access(0), wait((0, None, None))],
                1: [recv(0, 1, 0, 1, count=N)]})
        self.assertEqual(set(verdicts(t).values()), {buffers.COPIED})
        self.assertEqual(finding(t).B, "")

    def test_synchronous_send_is_never_taken_as_copied(self):
        t = mk({0: [send(1, 1, N, mode="issend", req=0), access(0), wait((0, None, None))],
                1: [recv(0, 1, 0, 1, count=N)]})
        self.assertEqual(set(verdicts(t).values()), {buffers.SOME})
        self.assertEqual(finding(t).B, "any B")

    def test_read_of_a_send_buffer_has_no_consequence(self):
        t = mk({0: [send(1, 1, N, mode="isend", req=0), access(0, "read"), wait((0, None, None))],
                1: [recv(0, 1, 0, 1, count=N)]})
        self.assertEqual(finding(t).B, "")

    def test_rendezvous_edges_of_other_sends_order_the_write(self):
        """Below the size of a second, synchronising message the write is ordered after the
        receive's completion (harmless); between the two sizes it is not: the verdict depends
        on B through the graph, not only through the copy."""
        small = 10
        t = mk({0: [send(1, 1, N, mode="isend", req=0), send(1, 4, small), access(0), wait((0, None, None))],
                1: [recv(0, 1, 0, 1, count=N), recv(0, 4, 0, 4, count=small)]})
        v = verdicts(t)
        self.assertEqual(v[0], buffers.HARMLESS)          # the 40-byte send completes after its match
        self.assertEqual(v[40], buffers.SOME)
        self.assertEqual(v[400], buffers.COPIED)
        self.assertEqual(v[None], buffers.COPIED)


class ReceiveAccesses(unittest.TestCase):
    def test_access_before_the_message_is_sent_is_overwritten_in_every_execution(self):
        t = mk({0: [irecv(1, 1, 0, count=N), access(0), send(1, 2), wait((0, 1, 1))],
                1: [recv(0, 2, 0, 2), send(0, 1, N)]})
        v = verdicts(t)
        self.assertEqual(set(v.values()), {buffers.EVERY})
        self.assertEqual(finding(t).B, "any B")

    def test_access_racing_the_message_at_every_threshold(self):
        t = mk({0: [irecv(1, 1, 0, count=N), access(0), wait((0, 1, 1))],
                1: [send(0, 1, N)]})
        self.assertEqual(set(verdicts(t).values()), {buffers.SOME})


class Overlaps(unittest.TestCase):
    def test_receive_into_a_pending_send_buffer(self):
        """An Isend left pending and an Irecv into the same buffer whose message the peer sends
        after taking the first: the peer's receive completes before its send is issued, so the
        first transfer ends before the second starts at every B (SLATE ttmqr's shape)."""
        t = mk({0: [send(1, 1, N, mode="isend", req=0), irecv(1, 2, 1, count=N),
                    {"op": "guard", "what": "overlap", "req": 0, "call": "MPI_Irecv", "with": 1},
                    wait((0, None, None), (1, 1, 2), kind="waitall")],
                1: [recv(0, 1, 0, 1, count=N), send(0, 2, N)]})
        v = verdicts(t)
        self.assertTrue(all(x in (buffers.HARMLESS,) for x in v.values()), v)
        self.assertEqual(finding(t).B, "")

    def test_receive_into_a_pending_send_buffer_unordered(self):
        t = mk({0: [send(1, 1, N, mode="isend", req=0), irecv(2, 2, 1, count=N),
                    {"op": "guard", "what": "overlap", "req": 0, "call": "MPI_Irecv", "with": 1},
                    wait((0, None, None), (1, 2, 2), kind="waitall")],
                1: [recv(0, 1, 0, 1, count=N)], 2: [send(0, 2, N)]})
        v = verdicts(t)
        self.assertEqual(v[400], buffers.HARMLESS)        # copied in the Isend, before the Irecv
        self.assertEqual(v[0], buffers.SOME)
        self.assertEqual(finding(t).B, "B < 400")


class Profiles(unittest.TestCase):
    def test_bisection_judges_every_breakpoint_as_a_full_scan(self):
        t = mk({0: [send(1, 1, N, mode="isend", req=0), send(1, 4, 1000), send(1, 5, 7), access(0), wait((0, None, None))],
                1: [recv(0, 1, 0, 1, count=N), recv(0, 4, 0, 4, count=1000), recv(0, 5, 0, 5, count=7)]})
        o = buffers.Orders(t)
        x = t.sends[(0, 0)]
        a = buffers.Access([t.guards[0].issue], [t.guards[0].complete], True)

        def judge(B):
            return buffers.COPIED if buffers.copied(x, B) else buffers.verdict(o, B, buffers.window(t, x, B), a)
        full = [(B, judge(B)) for B in [0] + t.message_sizes() + [None]]
        self.assertEqual(buffers.profile(t, o, judge), full)

    def test_ranges(self):
        self.assertEqual(buffers.ranges([(0, "a"), (4, "a"), (400, "b"), (None, "b")]), [("B < 400", "a"), ("B >= 400", "b")])
        self.assertEqual(buffers.ranges([(0, "a"), (None, "a")]), [("any B", "a")])
        self.assertEqual(buffers.ranges([(0, "a"), (4, "b"), (400, "a"), (None, "a")]),
                         [("B < 4", "a"), ("4 <= B < 400", "b"), ("B >= 400", "a")])


class Report(unittest.TestCase):
    def test_threshold_in_the_text_report(self):
        t = mk({0: [send(1, 1, N, mode="isend", req=0), access(0), send(1, 2), wait((0, None, None))],
                1: [recv(0, 2, 0, 2), recv(0, 1, 0, 1, count=N)]})
        f = [x for x in analyze(t).findings if x.cls == "3-local"]
        self.assertEqual([x.B for x in f], ["B < 400"])
        self.assertIn("[B < 400]", str(f[0]))





def ranged(rec, lo, hi):
    d = dict(rec)
    d["buf"], d["dense"] = [lo, hi], 1
    return d


class Races(unittest.TestCase):
    """L1 buffer races from the records' address ranges (STRETTO_BUFS=1): two operations of a
    process on shared bytes, one a receive, whose intervals no happens-before orders."""

    def _trace(self, ordered, base2=0x1000):
        from test_threads import mkt, th
        return mkt({0: [th(0, ranged(irecv(1, 1, 0, count=N), 0x1000, 0x1190), "r"),
                        th(0, {"op": "wait", "kind": "wait", "reqs": [0], "done": [{"req": 0, "msrc": 1, "mtag": 1, "mcount": N}]}, "w"),
                        th(1, ranged(send(1, 2, N, mode="isend", req=1), base2, base2 + 0x190), after=["w"] if ordered else ()),
                        th(1, wait((1, None, None)))],
                    1: [send(0, 1, N), recv(0, 2, 0, 2, count=N)]})

    def test_threads_without_synchronisation_race(self):
        t = self._trace(ordered=False)
        found, harmless = buffers.races(t)
        self.assertEqual((len(found), harmless), (1, 0))
        self.assertEqual(found[0].consequence.B, "any B")
        f = [x for x in analyze(t, with_sp=False).findings if x.cls == "3-race"]
        self.assertEqual([x.level for x in f], ["L1"])

    def test_synchronised_threads_do_not_race(self):
        self.assertEqual(buffers.races(self._trace(ordered=True)), ([], 0))

    def test_disjoint_buffers_do_not_race(self):
        self.assertEqual(buffers.races(self._trace(ordered=False, base2=0x2000)), ([], 0))

    def test_freed_send_then_receive_of_the_reply_is_harmless(self):
        """SLATE ttmqr's shape: an Isend freed at once, then an Irecv into the same tile, whose
        message the peer sends after receiving the tile. The intervals overlap (the program cannot
        know when the freed send ends), but the reply is sent after the first message has been
        received, so its transfer starts after the first one ends, at every threshold."""
        t = mk({0: [ranged(send(1, 1, N, mode="isend", req=0), 0x1000, 0x1190), {"op": "reqfree", "req": 0},
                    ranged(irecv(1, 1, 1, count=N), 0x1000, 0x1190), {"op": "wait", "kind": "wait", "reqs": [1], "done": [{"req": 1, "msrc": 1, "mtag": 1, "mcount": N}]}],
                1: [recv(0, 1, 0, 1, count=N), send(0, 1, N)]})
        self.assertEqual(buffers.races(t), ([], 1))
        rep = analyze(t, with_sp=False)
        self.assertFalse([x for x in rep.findings if x.cls == "3-race"])
        self.assertTrue(any("harmless" in n for n in rep.notes), rep.notes)

    def test_freed_send_then_receive_from_a_third_rank_races_below_the_size(self):
        t = mk({0: [ranged(send(1, 1, N, mode="isend", req=0), 0x1000, 0x1190), {"op": "reqfree", "req": 0},
                    ranged(irecv(2, 1, 1, count=N), 0x1000, 0x1190), wait((1, 2, 1))],
                1: [recv(0, 1, 0, 1, count=N)], 2: [send(0, 1, N)]})
        found, _ = buffers.races(t)
        self.assertEqual([r.consequence.B for r in found], ["B < 400"])


class Idioms(unittest.TestCase):
    """Idioms 37 and 38, traced with the guard and message sums, and 39 and 40, traced with the
    buffer ranges (their files env)."""

    def _finding(self, n):
        from stretto import trace
        from test_idioms import need_trace
        t = trace.load(need_trace(self, n))
        f = [x for x in analyze(t, with_sp=False).findings if x.cls == "3-local"]
        self.assertEqual(len(f), 1, [str(x) for x in f])
        return t, f[0]

    def test_37_write_corrupts_below_the_size(self):
        t, f = self._finding(37)
        self.assertEqual(f.B, "B < 4000")
        self.assertTrue(any("every execution" in d for d in f.detail), f.detail)
        s = t.sends[(0, 0)]
        r = t.recvs[next(rk for rk, sk in t.observed.items() if sk == s.key)]
        self.assertIsNotNone(s.sum)
        self.assertEqual(s.sum, r.sum)      # traced at the default limit (4 040 bytes): copied in the call

    def test_38_write_races_the_message_at_every_threshold(self):
        _, f = self._finding(38)
        self.assertEqual(f.B, "any B")
        self.assertTrue(any("some executions" in d for d in f.detail), f.detail)


    def _races(self, n):
        from stretto import trace
        from test_idioms import need_trace
        t = trace.load(need_trace(self, n))
        return [x for x in analyze(t, with_sp=False).findings if x.cls == "3-race"]

    def test_39_threads_race_on_a_buffer(self):
        f = self._races(39)
        self.assertEqual([(x.level, x.B) for x in f], [("L1", "any B")])

    def test_40_synchronised_threads_do_not(self):
        self.assertEqual(self._races(40), [])


class Replays(unittest.TestCase):
    """Idioms 37 and 38 run again under the tracer with the eager limit just below and at the
    message's 4 000 bytes (experiments/guard/thresholds.py): the sums show the message carry the
    write below the limit and not at it (37), and the program's write corrupt the message at it
    and be overwritten below it (38; both orders, as predicted for some executions)."""

    def _replay(self, n, mpi, B):
        sys.path.insert(0, os.path.join(ROOT, "experiments", "guard"))
        import thresholds as TH
        from test_replay_e2e import REQUIRE
        exe = next(iter(glob.glob(os.path.join(ROOT, "idioms", f"{n}_*", "bin", mpi, "main"))), "")
        lib = os.path.join(ROOT, "tracer", "bin", mpi, "libstretto-trace.so")
        missing = [p for p in (exe, lib, "/usr/bin/mpirun.openmpi") if not p or not os.path.exists(p)]
        if missing:
            if REQUIRE:
                self.fail(f"not built: {missing}")
            self.skipTest(f"not built: {missing}")
        margs, env = TH.settings(mpi, B)
        res, t = TH.traced(mpi, 2, exe, [], 60, margs, env)
        self.assertEqual(res, "FINISHED")
        x = t.sends[(0, 0)] if n == 37 else t.recvs[(0, 0)]
        return TH.outcome(t, x)

    def test_37_openmpi(self):
        self.assertEqual((self._replay(37, "openmpi", 3999), self._replay(37, "openmpi", 4000)), ("changed", "intact"))

    def test_37_mpich(self):
        self.assertEqual((self._replay(37, "mpich", 3999), self._replay(37, "mpich", 4000)), ("changed", "intact"))

    def test_38_openmpi(self):
        self.assertEqual((self._replay(38, "openmpi", 3999), self._replay(38, "openmpi", 4000)), ("intact", "changed"))


if __name__ == "__main__":
    unittest.main()
