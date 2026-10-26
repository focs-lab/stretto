"""Flag flips of polling calls (docs/model.md §3, flag flips): an Iprobe or Improbe that returned
false and could have returned true with a message, one that returned true and could have
returned false, and a test of a receive's request that could have found it complete. Traces are
built in memory from raw records in the tracer's format, as in test_checker."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stretto import fast, trace as T
from stretto.feasible import breakpoints, check
from stretto.matching import flip_candidates, test_flip_candidates
from stretto.report import analyze, probe_kw, wait_kw
from test_checker import (closure_equals_fixpoint, closure_orders_agree, failed_iprobe, failed_test,
                          fast_equals_generic, irecv, mk, random_probe_trace, recv, send, wait)


def iprobe(tag, msrc=None, mtag=None, src=T.ANY, pidx=None):
    """An Iprobe record: flag 1 when msrc is given, else a failed poll."""
    d = {"op": "probe", "kind": "iprobe", "src": 0 if src == T.ANY else src, "tag": 0 if tag == T.ANY else tag,
         "comm": 0, "flag": 0 if msrc is None else 1}
    if src == T.ANY:
        d["anysrc"] = 1
    if tag == T.ANY:
        d["anytag"] = 1
    if msrc is not None:
        d.update({"msrc": msrc, "mtag": mtag})
    if pidx is not None:
        d["pidx"] = pidx
    return d


def flips_of(t):
    return [(p.key, s.key if s is not None else None) for p, s in flip_candidates(t)]


class C_PollSegments(unittest.TestCase):
    def test_segments_are_runs_of_failed_polls(self):
        # a segment is a run of failed polls of any kind with nothing else between them
        t = mk({0: [failed_iprobe(1), failed_iprobe(2), send(1, 3), failed_iprobe(1), irecv(1, 4, 7), failed_test(7),
                    failed_iprobe(1), wait((7, 1, 4)), recv(T.ANY, 1, 1, 1)],
                1: [recv(0, 3, 0, 3), send(0, 4), send(0, 1)]})
        segs = {idx: seg for (rank, idx), seg in t.poll_seg.items() if rank == 0}
        # records: 0 init, 1-2 polls, 3 send, 4 poll, 5 irecv, 6-7 polls, 8 wait, 9 recv
        self.assertEqual(segs, {1: 0, 2: 0, 4: 1, 6: 2, 7: 2})

    def test_side_records_do_not_end_a_segment(self):
        t = mk({0: [failed_iprobe(1), {"op": "overflow", "what": "site table"}, failed_iprobe(1), recv(T.ANY, 1, 1, 1)],
                1: [send(0, 1)]})
        self.assertEqual(sorted(set(v for (r, _), v in t.poll_seg.items() if r == 0)), [0])

    def test_one_candidate_per_identity_and_segment(self):
        # Iprobe tag 1, Iprobe tag 2, Iprobe tag 1 again, all failed in one segment: the second
        # tag-1 record is the first one's flip, and only that is a candidate
        t = mk({0: [failed_iprobe(1), failed_iprobe(2), failed_iprobe(1), recv(T.ANY, 1, 1, 1), recv(T.ANY, 2, 1, 2)],
                1: [send(0, 1), send(0, 2)]})
        self.assertEqual(flips_of(t), [((0, 0), (1, 0)), ((0, 1), (1, 1))])


class C_ProbeFlip(unittest.TestCase):
    def test_failed_iprobe_could_see_a_message_in_flight(self):
        t = mk({0: [failed_iprobe(1), recv(T.ANY, 1, 1, 1)], 1: [send(0, 1)]})
        p, s = t.probes[(0, 0)], t.sends[(1, 0)]
        self.assertEqual(flips_of(t), [(p.key, s.key)])
        for B in breakpoints(t) + [None]:
            g = check(t, t.observed, set(), B, "L1", **probe_kw(p, s))
            self.assertTrue(g.feasible, g.reason)
            self.assertEqual(g.cut[0], p.idx + 1)        # the probing rank is cut right after it
            f = fast.Observed(t, B).check_probe(p, s)
            self.assertEqual((f.feasible, f.cut), (True, g.cut))
        rep = analyze(t, with_sp=False)
        (fl,) = rep.by_class("probe-flip")
        self.assertEqual((fl.level, fl.probe, fl.send), ("L1", p.key, s.key))
        self.assertEqual(fl.pin, "STRETTO_PIN_FLIP=0:0:1@0:1:x:0")   # rank 0, segment 0, source 1; comm 0, tag 1, no site, no probe found before
        self.assertFalse(rep.has_error())                          # a warning, as probe-alt is

    def test_message_causally_after_the_probe_is_no_candidate(self):
        # rank 1 sends only after it has received what rank 0 sends after the probe
        t = mk({0: [failed_iprobe(1), send(1, 5), recv(T.ANY, 1, 1, 1)], 1: [recv(0, 5, 0, 5), send(0, 1)]})
        p, s = t.probes[(0, 0)], t.sends[(1, 0)]
        self.assertEqual(flips_of(t), [])
        # and the check rejects it if asked: the message is never issued within the horizon
        g = check(t, t.observed, set(), None, "L1", **probe_kw(p, s))
        self.assertFalse(g.feasible)
        self.assertIn("never issued within the horizon", g.reason)
        self.assertEqual(analyze(t, with_sp=False).by_class("probe-flip"), [])

    def test_pending_earlier_receive_takes_the_message(self):
        # rank 0's Irecv(ANY, 1), posted before the probe, took rank 2's message, which rank 2
        # sends only after rank 0's send after the probe: within the horizon the Irecv stays
        # pending and takes rank 1's message, so the probe cannot see it
        t = mk({0: [irecv(T.ANY, 1, 10), failed_iprobe(1), send(2, 5), wait((10, 2, 1)), recv(1, 1, 1, 1)],
                1: [send(0, 1)], 2: [recv(0, 5, 0, 5), send(0, 1)]})
        p, s = t.probes[(0, 0)], t.sends[(1, 0)]
        self.assertIn((p.key, s.key), flips_of(t))
        for B in (0, None):
            g = check(t, t.observed, set(), B, "L1", **probe_kw(p, s))
            self.assertFalse(g.feasible)
            f = fast.Observed(t, B).check_probe(p, s)
            self.assertFalse(f.feasible)
        self.assertEqual(analyze(t, with_sp=False).by_class("probe-flip"), [])

    def test_earlier_message_of_the_channel_comes_first(self):
        # rank 1 sends tag 2 then tag 1; the probe of tag 1 fails while rank 0 has not yet
        # received the tag-2 message: the tag-1 one is the head of what the probe accepts
        t = mk({0: [failed_iprobe(1), recv(1, 2, 1, 2), recv(T.ANY, 1, 1, 1)], 1: [send(0, 2), send(0, 1)]})
        self.assertEqual(flips_of(t), [((0, 0), (1, 1))])
        # an ANY_TAG probe sees the tag-2 message first, and only that
        t = mk({0: [failed_iprobe(None), recv(1, 2, 1, 2), recv(T.ANY, 1, 1, 1)], 1: [send(0, 2), send(0, 1)]})
        self.assertEqual(flips_of(t), [((0, 0), (1, 0))])
        rep = analyze(t, with_sp=False)
        (fl,) = rep.by_class("probe-flip")
        self.assertEqual(fl.pin, "STRETTO_PIN_FLIP=0:0:1:2@0:-1:x:0")    # an ANY_TAG probe names the tag

    def test_specific_source_probe(self):
        t = mk({0: [iprobe(1, src=2), recv(T.ANY, 1, 1, 1), recv(2, 1, 2, 1)], 1: [send(0, 1)], 2: [send(0, 1)]})
        self.assertEqual(flips_of(t), [((0, 0), (2, 0))])

    def test_message_taken_before_the_probe_is_no_candidate(self):
        t = mk({0: [recv(T.ANY, 1, 1, 1), failed_iprobe(1)], 1: [send(0, 1)]})
        self.assertEqual(flips_of(t), [])


class C_ProbeUnflip(unittest.TestCase):
    def test_true_iprobe_could_return_false(self):
        t = mk({0: [iprobe(1, msrc=1, mtag=1, pidx=0), recv(1, 1, 1, 1)], 1: [send(0, 1)]})
        p = t.probes[(0, 0)]
        self.assertEqual(flips_of(t), [(p.key, None)])
        for B in (0, None):
            g = check(t, t.observed, set(), B, "L1", **probe_kw(p, None))
            self.assertTrue(g.feasible, g.reason)
            self.assertEqual(g.cut[0], p.idx + 1)
            f = fast.Observed(t, B).check_probe(p, None)
            self.assertEqual((f.feasible, f.cut), (True, g.cut))
        (u,) = analyze(t, with_sp=False).by_class("probe-unflip")
        self.assertEqual(u.pin, "STRETTO_PIN_PROBE=0:0:-1@0:1")

    def test_blocking_probe_has_no_flip(self):
        p = {"op": "probe", "kind": "probe", "src": 0, "anysrc": 1, "tag": 1, "comm": 0, "flag": 1, "msrc": 1, "mtag": 1}
        t = mk({0: [p, recv(1, 1, 1, 1)], 1: [send(0, 1)]})
        self.assertEqual(flips_of(t), [])


class C_TestFlip(unittest.TestCase):
    def test_failed_test_could_find_the_receive_complete(self):
        t = mk({0: [irecv(1, 1, 7), failed_test(7), wait((7, 1, 1))], 1: [send(0, 1)]})
        (c,) = test_flip_candidates(t)
        rec, r, s = c
        self.assertEqual((rec.idx, r.key, s.key), (2, (0, 0), (1, 0)))
        for B in (0, None):
            g = check(t, t.observed, set(), B, "L1", **wait_kw(rec, None, r, s))
            self.assertTrue(g.feasible, g.reason)
            self.assertEqual(g.cut[0], rec.idx + 1)
            f = fast.Observed(t, B).check_event(rec.rank, rec.idx, s)
            self.assertEqual((f.feasible, f.cut), (True, g.cut))
        (fl,) = analyze(t, with_sp=False).by_class("test-flip")
        self.assertEqual((fl.event, fl.pin), ((0, 2), "STRETTO_PIN_FLIP=0:0:-2@x:x:x:0"))

    def test_message_after_the_test_is_no_candidate(self):
        t = mk({0: [irecv(2, 1, 7), failed_test(7), send(2, 5), wait((7, 2, 1))], 2: [recv(0, 5, 0, 5), send(0, 1)]})
        self.assertEqual(test_flip_candidates(t), [])

    def test_testall_is_not_flipped(self):
        t = mk({0: [irecv(1, 1, 7), irecv(1, 1, 8), {"op": "wait", "kind": "testall", "flag": 0, "reqs": [7, 8], "done": []},
                    wait((7, 1, 1)), wait((8, 1, 1))], 1: [send(0, 1), send(0, 1)]})
        self.assertEqual(test_flip_candidates(t), [])


class C_FlipsFastEqualsGeneric(unittest.TestCase):
    def test_hand_written(self):
        traces = [
            mk({0: [failed_iprobe(1), recv(T.ANY, 1, 1, 1)], 1: [send(0, 1)]}),
            mk({0: [irecv(T.ANY, 1, 10), failed_iprobe(1), send(2, 5), wait((10, 2, 1)), recv(1, 1, 1, 1)],
                1: [send(0, 1)], 2: [recv(0, 5, 0, 5), send(0, 1)]}),
            mk({0: [iprobe(1, msrc=1, mtag=1, pidx=0), recv(1, 1, 1, 1)], 1: [send(0, 1)]}),
            mk({0: [irecv(1, 1, 7), failed_test(7), wait((7, 1, 1))], 1: [send(0, 1)]}),
        ]
        for t in traces:
            fast_equals_generic(self, t)
            closure_equals_fixpoint(self, t)
            closure_orders_agree(self, t)

    def test_random_runs_with_polls(self):
        """Random runs with failed Iprobes and tests (random_probe_records(polls=True)): the fast
        check equals the generic one on every flip, and the closures agree."""
        rng = random.Random(20261026)
        n = 0
        for k in range(30):
            t = random_probe_trace(rng, rng.choice([3, 4]), rounds=2, shapes=bool(k % 2), polls=True)
            n += len(flip_candidates(t)) + len(test_flip_candidates(t))
            fast_equals_generic(self, t)
            closure_equals_fixpoint(self, t)
            closure_orders_agree(self, t)
        self.assertGreater(n, 30)


if __name__ == "__main__":
    unittest.main()
