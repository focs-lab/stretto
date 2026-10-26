"""Happens-before selection of alternatives (stretto.hb) next to the check: the paper's
counterexample, in which selection at the completion clock accepts an alternative no
execution has, and a pending receive whose message is sent after its posting, which selection
at the posting clock rejects although an execution takes it. The explorer over the trace as a
straight-line program is the ground truth."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from stretto import dsl, explore, hb, trace as T  # noqa: E402
from stretto.matching import observed_hb  # noqa: E402
from stretto.report import analyze  # noqa: E402
from test_checker import mk, recv, send, wait  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
TRACES = os.path.join(ROOT, "traces", "openmpi")
if not os.path.isdir(TRACES):
    TRACES = os.path.join(HERE, "golden", "openmpi")


def irecv(src, tag, req, widx=None):
    d = {"op": "irecv", "src": 0 if src == T.ANY else src, "tag": tag, "comm": 0, "count": 1, "tsize": 4, "req": req}
    if src == T.ANY:
        d["anysrc"] = 1
    if widx is not None:
        d["widx"] = widx
    return d


def realised(t, rk, sk) -> bool:
    """Some execution of the trace as a straight-line program, with infinite buffering, matches
    receive rk to send sk."""
    res = explore.explore(dsl.program_from_trace(t), None, force={rk: sk}, comms=t.comm_members)
    return bool(res.outcomes_with(rk, sk))


def accepted(t, generic=False):
    return {(op, at, sk) for (op, at, sk), v in hb.judge(t, generic).items() if v.accepted}


def reported(t):
    rep = analyze(t, with_sp=False)
    out = set()
    for f in rep.findings:
        if f.level != "L1" or f.send is None:
            continue
        if f.probe is not None:
            if f.cls == "probe-alt":          # flag flips are no alternative of the probe's message
                out.add(("probe", f.probe, f.send))
        elif f.recv is not None:
            out.add(("recv", f.recv, f.send))
    return out


def stolen_after_later_receive():
    """The paper's counterexample (section "The constraint graph"). Rank 0 posts Irecv r(ANY),
    then Recv r3(ANY), which takes rank 3's message, sends x to rank 2 and waits for r, which
    takes rank 1's message; rank 2 receives x and sends sC to rank 0, which nobody receives."""
    return mk({
        0: [irecv(T.ANY, 0, 10, widx=0), recv(T.ANY, 0, 3, 0, widx=1), send(2, 5), wait((10, 1, 0))],
        1: [send(0, 0)],
        2: [recv(0, 5, 0, 5), send(0, 0)],
        3: [send(0, 0)],
    })


def reply_to_pending_receive():
    """Rank 0 posts Irecv r(ANY), sends x to rank 1 and waits for r, which takes rank 2's
    message; a later Recv(ANY) takes rank 1's reply, sent after rank 1 received x."""
    return mk({
        0: [irecv(T.ANY, 0, 10, widx=0), send(1, 5), wait((10, 2, 0)), recv(T.ANY, 0, 1, 0, widx=1)],
        1: [recv(0, 5, 0, 5), send(0, 0)],
        2: [send(0, 0)],
    })


class CompletionClockUnsound(unittest.TestCase):
    """r <- sC: r is posted before r3 and accepts rank 3's message, so r is matched before r3
    completes, and sC is sent only after r3 completes. At the completion clock sC is
    concurrent with r's wait, so the rule selects the steal; no execution has it."""

    def setUp(self):
        self.t = stolen_after_later_receive()
        self.r, self.sC = (0, 0), (2, 0)

    def test_rules(self):
        t = self.t
        self.assertIn((self.r, self.sC), hb.receive_candidates(t, hb.COMP))
        self.assertNotIn((self.r, self.sC), hb.receive_candidates(t, hb.POST))

    def test_no_execution_has_it(self):
        self.assertFalse(realised(self.t, self.r, self.sC))

    def test_check_rejects(self):
        for generic in (False, True):
            v = hb.judge(self.t, generic)[("recv", self.r, self.sC)]
            self.assertFalse(v.accepted, f"generic={generic}: {v}")
        self.assertNotIn(("recv", self.r, self.sC), reported(self.t))

    def test_compare_row(self):
        row = next(p for p in hb.compare(self.t) if p.op == "recv" and p.at == self.r and p.send == self.sC)
        self.assertEqual((row.head, row.post, row.comp, row.stretto.accepted), (True, False, True, False))
        self.assertEqual(row.kind, "nonblocking, pending across a send")


class PostingClockIncomplete(unittest.TestCase):
    """r <- s1: rank 1 sends s1 after receiving x, which rank 0 sends after posting r, so the
    posting clock rejects it; r is still pending then, and s1 can arrive before rank 2's
    message."""

    def setUp(self):
        self.t = reply_to_pending_receive()
        self.r, self.s1 = (0, 0), (1, 0)

    def test_rules(self):
        t = self.t
        self.assertNotIn((self.r, self.s1), hb.receive_candidates(t, hb.POST))
        self.assertIn((self.r, self.s1), hb.receive_candidates(t, hb.COMP))

    def test_an_execution_has_it(self):
        self.assertTrue(realised(self.t, self.r, self.s1))

    def test_check_accepts(self):
        for generic in (False, True):
            v = hb.judge(self.t, generic)[("recv", self.r, self.s1)]
            self.assertTrue(v.accepted, f"generic={generic}: {v}")
        self.assertIn(("recv", self.r, self.s1), reported(self.t))


class Idiom14(unittest.TestCase):
    """The counterexample as an MPI program (idioms/14_pending_wildcard_overtaken), whose last
    receive takes sC in the run."""

    def test_traced_run(self):
        d = os.path.join(TRACES, "14_pending_wildcard_overtaken")
        if not os.path.isdir(d):
            if os.environ.get("STRETTO_REQUIRE_TRACES") == "1":
                self.fail("idiom 14 not traced (STRETTO_REQUIRE_TRACES=1)")
            self.skipTest("idiom 14 not traced")
        t = T.load(d)
        r, s1, s3, sC = (0, 0), (1, 0), (3, 0), (2, 0)
        self.assertEqual((t.observed[r], t.observed[(0, 1)], t.observed[(0, 2)]), (s1, s3, sC))
        self.assertIn((r, sC), hb.receive_candidates(t, hb.COMP))
        self.assertNotIn((r, sC), hb.receive_candidates(t, hb.POST))
        verdicts = hb.judge(t)
        self.assertFalse(verdicts[("recv", r, sC)].accepted)
        self.assertTrue(verdicts[("recv", r, s3)].accepted)
        self.assertFalse(realised(t, r, sC))


class Relation(unittest.TestCase):
    def traces(self):
        out = [stolen_after_later_receive(), reply_to_pending_receive()]
        for d in sorted(os.listdir(TRACES)) if os.path.isdir(TRACES) else []:
            out.append(T.load(os.path.join(TRACES, d)))
        return out

    def test_contained_in_the_constraint_graph(self):
        """Every hb edge is a path of the observed constraint graph at B = inf, so a send hb
        places after a receive's node is after it in observed_hb as well."""
        for t in self.traces():
            c, reach = hb.Clocks(t), observed_hb(t)
            for r in t.recvs.values():
                for a in {r.issue_node(), c.reference(r, hb.COMP)}:
                    for s in t.sends.values():
                        if c.before(a, s):
                            self.assertTrue(reach(a, s.issue_node()), f"{a} before {s}")

    def test_exact_on_program_order_and_messages(self):
        t = stolen_after_later_receive()
        c = hb.Clocks(t)
        # records: rank 0 init, Irecv r, Recv r3, Send x, Wait r; rank 2 init, Recv x, Send sC
        x, sC = t.sends[(0, 0)], t.sends[(2, 0)]
        self.assertTrue(c.before((0, 1, "i"), x))          # program order
        self.assertTrue(c.before((0, 2, "c"), sC))         # r3, then x and rank 2's receive
        self.assertFalse(c.before((0, 4, "c"), sC))        # the wait of r is concurrent with sC
        self.assertTrue(c.before((3, 1, "i"), sC))         # rank 3's message reached r3
        self.assertFalse(c.before((1, 1, "i"), sC))        # rank 1's reached only the wait

    def test_rules_coincide_at_blocking_receives(self):
        for t in self.traces():
            c = hb.Clocks(t)
            for rk, sk in hb.heads(t):
                r, s = t.recvs[rk], t.sends[sk]
                if r.blocking:
                    self.assertEqual(c.selects(r, s, hb.POST), c.selects(r, s, hb.COMP), f"{r} {s}")
                else:
                    self.assertLessEqual(c.selects(r, s, hb.POST), c.selects(r, s, hb.COMP), f"{r} {s}")

    def test_judge_is_the_report(self):
        """judge() states the verdicts report.analyze gives, with either check."""
        for t in self.traces():
            if hb.declines(t):
                continue
            self.assertEqual(accepted(t), reported(t))
            self.assertEqual(accepted(t, generic=True), reported(t))

    def test_every_head_is_judged(self):
        for t in self.traces():
            rows = hb.compare(t)
            if rows is None:
                continue
            self.assertTrue(all(p.stretto is not None for p in rows))
            self.assertTrue(all(not p.post or p.comp for p in rows))


if __name__ == "__main__":
    unittest.main()
