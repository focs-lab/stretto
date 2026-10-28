"""The replay classifier and the pin-applied check, without running MPI."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stretto.confirm import (PIN_INLINE_MAX, all_matched, changed_pin, classify_rc, control_pin, expected_outcomes,  # noqa: E402
                             horizon_pins, judge_replay, parse_pins, pin_assignment, pins_applied, pins_matched,
                             required_matches, strict_verdict, strip_identity, trace_matches, unpinned_violation)
from stretto import confirm as C  # noqa: E402
from stretto import trace as T  # noqa: E402
from stretto.matching import candidates_full  # noqa: E402
from stretto.report import Finding  # noqa: E402
from test_checker import failed_iprobe, mk, recv, send, wait  # noqa: E402
from test_hb import irecv, reply_to_pending_receive, stolen_after_later_receive  # noqa: E402

APPLIED = ("[stretto-pin] rank 0: 2 pin(s) active\n[stretto-pin] rank 0: applied widx=0 src=2 (world 2) tag=5\n"
           "[stretto-pin] rank 0: applied widx=1 src=1 (world 1) tag=5\n[stretto-pin] rank 1: applied probe pidx=0\n")


def bare(pins):
    """The pins without the identities their tokens carry."""
    return [strip_identity(p) for p in pins]


class Classify(unittest.TestCase):
    def test_exit_statuses(self):
        self.assertEqual(classify_rc(0, ""), "FINISHED")
        self.assertEqual(classify_rc(124, ""), "TIMEOUT")
        self.assertEqual(classify_rc(137, ""), "KILLED")           # SIGKILL: a memory cap or a kill, not a hang
        self.assertEqual(classify_rc(137, "", 12.0, 60), "KILLED")  # killed before the timeout: not a hang
        self.assertEqual(classify_rc(137, "", 65.2, 60), "TIMEOUT")  # timeout's own -k kill of a job that ignored SIGTERM
        self.assertEqual(classify_rc(1, "*** MPI_ERR_TRUNCATE: message truncated"), "MPI-ERROR:MPI_ERR_TRUNCATE")
        self.assertEqual(classify_rc(1, "the application printed 'truncated' itself"), "APP-ERROR:1")
        self.assertEqual(classify_rc(139, ""), "SIGNAL:11")

    def test_only_a_hang_with_applied_pins_confirms_a_deadlock(self):
        self.assertEqual(expected_outcomes(Finding("1", "1d", "L1+SP", "")), {"TIMEOUT"})
        self.assertEqual(expected_outcomes(Finding("1", "1f", "L1", "")), set())

    def test_of_class_1a_only_a_truncation_is_confirmable(self):
        """MPI reports a message longer than its receive; neither library reports a type
        signature mismatch or a ready send with no receive posted."""
        trunc = Finding("1", "1a", "L1", "recv(0, 0) <- send(2, 0)", error="truncate")
        self.assertEqual(expected_outcomes(trunc), {"MPI-ERROR:MPI_ERR_TRUNCATE"})
        self.assertEqual(expected_outcomes(Finding("1", "1a", "L1", "", error="type")), set())
        self.assertEqual(expected_outcomes(Finding("1", "1a", "L1+SP", "", error="rsend-no-receive")), set())
        # findings made before the field: by their title
        self.assertEqual(C.error_of(Finding("1", "1a", "L1+SP", "truncation error in some executions after ...")), "truncate")
        self.assertEqual(C.error_of(Finding("1", "1a", "L1+SP", "ready-mode send with no posted receive in ...")), "rsend-no-receive")
        self.assertEqual(C.error_of(Finding("1", "1a", "L1", "r <- s", ["type signature mismatch: ..."])), "type")

    def test_a_truncation_ends_the_run_inside_the_receive(self):
        """The receive that takes the longer message fails inside the call: it prints no
        'matched', and the run's trace leaves it pending where its rank stopped."""
        pins = ["STRETTO_PIN=0:0:1@0:5,0:1:2@0:5"]
        ch, other = ("recv", 0, 1), ("recv", 0, 0)
        applied = "[stretto-pin] rank 0: applied widx=1 src=2 (world 2) tag=5 id=0:5:x\n"
        # with the replay's trace: the changed receive pending at the end, the earlier one matched
        self.assertTrue(C.truncation_shown(ch, pins, "", {other, ch}, {other}, {ch}))
        self.assertFalse(C.truncation_shown(ch, pins, "", {other, ch}, set(), {ch}))          # the earlier one never matched
        self.assertFalse(C.truncation_shown(ch, pins, applied, {other, ch}, {other}, set()))   # the trace shows it did not stop there
        self.assertFalse(C.truncation_shown(ch, pins, "", {other, ch}, {other, ch}, {ch}))    # it matched: no truncation there
        # without a trace: the library's applied line, of the pin's identity
        self.assertTrue(C.truncation_shown(ch, pins, applied, {other, ch}, {other}, None))
        self.assertFalse(C.truncation_shown(ch, pins, applied.replace("id=0:5:x", "id=0:6:x"), {other, ch}, {other}, None))


class PinsApplied(unittest.TestCase):
    def test_every_pin_of_the_spec_must_be_reported(self):
        self.assertTrue(pins_applied("STRETTO_PIN=0:0:2", APPLIED))
        self.assertTrue(pins_applied("STRETTO_PIN=0:0:2,0:1:1:5", APPLIED))
        self.assertFalse(pins_applied("STRETTO_PIN=0:0:2,0:2:1", APPLIED))      # widx 2 never reached
        self.assertFalse(pins_applied("STRETTO_PIN=0:1:1", APPLIED.replace("widx=1", "widx=10")))
        self.assertFalse(pins_applied("STRETTO_PIN=0:0:2", ""))

    def test_probe_pins(self):
        self.assertTrue(pins_applied("STRETTO_PIN_PROBE=1:0:2", APPLIED))
        self.assertFalse(pins_applied("STRETTO_PIN_PROBE=1:1:2", APPLIED))


class PinsMatched(unittest.TestCase):
    """'applied' is printed when the source argument is rewritten; only 'matched', printed when
    the pinned receive completes, shows that the forced match happened (an impossible forced
    match also hangs, with every pin applied)."""

    def test_applied_is_not_matched(self):
        self.assertFalse(pins_matched("STRETTO_PIN=0:0:2", APPLIED))
        matched = APPLIED + "[stretto-pin] rank 0: matched widx=0 src=2\n"
        self.assertTrue(pins_matched("STRETTO_PIN=0:0:2", matched))
        self.assertFalse(pins_matched("STRETTO_PIN=0:0:2,0:1:1", matched))      # widx 1 applied, never matched
        self.assertTrue(pins_matched("STRETTO_PIN_PROBE=1:0:2", APPLIED))        # a probe pin reports when it found its message

    def test_control_pins_the_observed_senders(self):
        t = T.Trace()
        T._build(t, {0: [{"op": "init", "np": 3},
                         {"op": "recv", "src": 0, "anysrc": 1, "tag": 1, "comm": 0, "count": 1, "tsize": 4, "msrc": 1, "mtag": 1, "mcount": 1, "widx": 0},
                         {"op": "recv", "src": 0, "anysrc": 1, "tag": 1, "comm": 0, "count": 1, "tsize": 4, "msrc": 2, "mtag": 1, "mcount": 1, "widx": 1},
                         {"op": "finalize"}],
                     1: [{"op": "init", "np": 3}, {"op": "send", "mode": "std", "dst": 0, "tag": 1, "comm": 0, "count": 1, "tsize": 4}, {"op": "finalize"}],
                     2: [{"op": "init", "np": 3}, {"op": "send", "mode": "std", "dst": 0, "tag": 1, "comm": 0, "count": 1, "tsize": 4}, {"op": "finalize"}]})
        self.assertEqual(control_pin(t, "STRETTO_PIN=0:0:2,0:1:1"), "STRETTO_PIN=0:0:1@0:1,0:1:2@0:1")   # with their identity
        self.assertIsNone(control_pin(t, "STRETTO_PIN=0:7:2"))                  # no such receive: nothing to pin


class HorizonPins(unittest.TestCase):
    """A replay forces the alternative's whole horizon: every wildcard receive and probe posted
    in it is pinned to the sender it took in the run, the changed one to the alternative's."""

    def trace(self):
        rec = lambda **kw: {"op": "recv", "src": 0, "anysrc": 1, "tag": 1, "comm": 0, "count": 1, "tsize": 4, "mtag": 1, "mcount": 1, **kw}
        send = {"op": "send", "mode": "std", "dst": 0, "tag": 1, "comm": 0, "count": 1, "tsize": 4}
        t = T.Trace()
        T._build(t, {0: [{"op": "init", "np": 4}, rec(msrc=1, widx=0), rec(msrc=2, widx=1), rec(msrc=3, widx=2), {"op": "finalize"}],
                     1: [{"op": "init", "np": 4}, send, {"op": "finalize"}],
                     2: [{"op": "init", "np": 4}, send, {"op": "finalize"}],
                     3: [{"op": "init", "np": 4}, send, {"op": "finalize"}]})
        return t

    def test_earlier_wildcards_pinned_to_their_observed_senders(self):
        t = self.trace()
        # the second wildcard receive takes rank 3's message instead of rank 2's
        f = Finding("1", "1f", "L1", "", recv=(0, 1), send=(3, 0))
        alt, ctl = horizon_pins(t, f)
        self.assertEqual(alt, ["STRETTO_PIN=0:0:1@0:1,0:1:3@0:1"])        # rank 0 is cut right after the changed receive
        self.assertEqual(bare(ctl), ["STRETTO_PIN=0:0:1,0:1:2"])
        self.assertTrue(pins_matched(alt[0], "[stretto-pin] rank 0: matched widx=0 src=1 id=0:1:x\n"
                                             "[stretto-pin] rank 0: matched widx=1 src=3 id=0:1:x\n"))
        self.assertFalse(pins_matched(alt[0], "[stretto-pin] rank 0: matched widx=1 src=3 id=0:1:x\n"))
        # a report from an operation of another identity is not the pin's, nor one without an identity
        self.assertFalse(pins_matched(alt[0], "[stretto-pin] rank 0: matched widx=0 src=1 id=0:7:x\n"
                                              "[stretto-pin] rank 0: matched widx=1 src=3 id=0:1:x\n"))
        self.assertFalse(pins_matched(alt[0], "[stretto-pin] rank 0: matched widx=0 src=1\n[stretto-pin] rank 0: matched widx=1 src=3\n"))
        self.assertTrue(pins_matched(bare(alt)[0], "[stretto-pin] rank 0: matched widx=0 src=1\n[stretto-pin] rank 0: matched widx=1 src=3\n"))

    def test_receive_left_without_a_message_is_not_pinned(self):
        """MBI's MessageRace_Gather_Send_Irecv_nok: rank 1 posts Irecv r(ANY, any tag), joins a
        gather, posts Irecv r'(from 0, any tag), then waits for r and r'. In the run r took rank
        2's message and r' rank 0's. The steal r <- rank 0 leaves r' without a message, so the
        alternative pins r only (pinning r' to rank 0 asks for a match the alternative
        excludes, and the replay could never show it), and only r, which completes inside the
        horizon, must show its match. The control pins both to what they took."""
        coll = {"op": "coll", "name": "gather", "comm": 0, "root": 0, "count": 1, "tsize": 4}
        send = {"op": "send", "mode": "std", "dst": 1, "tag": 0, "comm": 0, "count": 1, "tsize": 4}
        irecv = lambda **kw: {"op": "irecv", "src": 0, "tag": 0, "anytag": 1, "comm": 0, "count": 1, "tsize": 4, **kw}
        wait = lambda q, src: {"op": "wait", "kind": "wait", "reqs": [q], "done": [{"req": q, "msrc": src, "mtag": 0, "mcount": 1}]}
        init, fin = {"op": "init", "np": 4}, {"op": "finalize"}
        t = T.Trace()
        T._build(t, {0: [init, coll, send, fin], 2: [init, coll, send, fin], 3: [init, coll, fin],
                     1: [init, irecv(anysrc=1, req=3, widx=0), coll, irecv(req=4, widx=1), wait(3, 2), wait(4, 0), fin]})
        f = Finding("1", "1d", "L1+SP", "", recv=(1, 0), send=(0, 0))
        alt, ctl = horizon_pins(t, f)
        self.assertEqual(alt, ["STRETTO_PIN=1:0:0:0@0:-1"])                 # an ANY_TAG receive: tag -1
        self.assertEqual(bare(ctl), ["STRETTO_PIN=1:0:2:0,1:1:0:0"])
        req = required_matches(t, f)
        self.assertEqual(req, {("recv", 1, 0)})
        self.assertTrue(all_matched(alt, "[stretto-pin] rank 1: matched widx=0 src=0 id=0:-1:x\n", req))
        self.assertFalse(all_matched(alt, "[stretto-pin] rank 1: applied widx=0 id=0:-1:x\n", req))

    def test_flag_flip_is_pinned_in_its_horizon(self):
        """A probe-flip changes a failed Iprobe, which no probe pin names: the alternative takes
        the finding's flip pin, the control the recorded false, and the alternative must show
        the forced call returning true ("matched flip"), not only the pin applied."""
        from stretto.report import analyze
        t = mk({0: [failed_iprobe(1), recv(T.ANY, 1, 1, 1)], 1: [send(0, 1)]})
        (fl,) = analyze(t, with_sp=False).by_class("probe-flip")
        self.assertEqual(fl.pin, "STRETTO_PIN_FLIP=0:0:1@0:1:x:0")
        alt, ctl = horizon_pins(t, fl)
        self.assertEqual(alt, ["STRETTO_PIN_FLIP=0:0:1@0:1:x:0"])     # rank 0 is cut after the probe: its receive is outside
        self.assertEqual(ctl, ["STRETTO_PIN_FLIP=0:0:-1@0:1:x:0"])
        req = required_matches(t, fl)
        self.assertEqual(req, {("flip", 0, 0)})
        self.assertTrue(all_matched(alt, "[stretto-pin] rank 0: applied flip seg=0 src=1 (world 1) tag=1 id=0:1:x\n"
                                         "[stretto-pin] rank 0: matched flip seg=0 src=1 tag=1 id=0:1:x\n", req))
        # applied but never returned true: the flip did not happen
        self.assertFalse(all_matched(alt, "[stretto-pin] rank 0: applied flip seg=0 src=1 (world 1) tag=1 id=0:1:x\n", req))
        self.assertFalse(all_matched(alt, "", req))
        self.assertFalse(all_matched(alt, "[stretto-pin] rank 0: matched flip seg=0 src=1 tag=1 id=0:7:x\n", req))
        # the control's flip is the recorded false
        self.assertTrue(all_matched(ctl, "[stretto-pin] rank 0: applied flip seg=0 flag=0 id=0:1:x\n"))
        self.assertFalse(all_matched(ctl, ""))

    def test_receive_alternative_has_no_flip_pin(self):
        t = self.trace()
        f = Finding("1", "1f", "L1", "", recv=(0, 1), send=(3, 0))
        alt, ctl = horizon_pins(t, f)
        self.assertEqual(alt, ["STRETTO_PIN=0:0:1@0:1,0:1:3@0:1"])
        self.assertEqual(bare(ctl), ["STRETTO_PIN=0:0:1,0:1:2"])
        self.assertEqual(required_matches(t, f), {("recv", 0, 0), ("recv", 0, 1)})



class RejectedAlternatives(unittest.TestCase):
    """experiments/e8_hb.py replays alternatives the check rejects, to test the rejection, and
    must not count a pinned run that is no run of the program. The paper's counterexample
    (test_hb.stolen_after_later_receive): rank 0 posts Irecv r(ANY), Recv r3(ANY) takes rank
    3's message, rank 0 sends x to rank 2, waits for r (rank 1's message); rank 2 then sends sC.
    The steal r <- sC is rejected: r is matched before r3 completes, sC follows r3."""

    def test_pins_over_the_closure(self):
        t = stolen_after_later_receive()
        f = Finding("1", "1f", "L1", "", recv=(0, 0), send=(2, 0))
        self.assertIsNone(horizon_pins(t, f))
        alt, ctl = horizon_pins(t, f, infeasible_ok=True)
        self.assertEqual(bare(alt), ["STRETTO_PIN=0:0:2,0:1:3"])
        self.assertEqual(bare(ctl), ["STRETTO_PIN=0:0:1,0:1:3"])
        self.assertEqual(changed_pin(t, f), ("recv", 0, 0))
        self.assertIn(("recv", 0, 0), required_matches(t, f, infeasible_ok=True))

    def test_a_swap_pins_both_receives(self):
        """Two pending Irecv(ANY) completed by one Waitall: r2 <- s1 is a swap (r1 takes s2)."""
        t = mk({0: [irecv(T.ANY, 0, 10, widx=0), irecv(T.ANY, 0, 11, widx=1), wait((10, 1, 0), (11, 2, 0), kind="waitall")],
                1: [send(0, 0)], 2: [send(0, 0)]})
        a = next(a for a in candidates_full(t)[0] if a.recv == (0, 1) and a.send == (1, 0) and a.kind == "swap")
        f = Finding("1", "1f", "L1", "", recv=a.recv, send=a.send)
        alt, ctl = horizon_pins(t, f, M=a.M, changed=a.changed)
        self.assertEqual(bare(alt), ["STRETTO_PIN=0:0:2,0:1:1"])
        self.assertEqual(bare(ctl), ["STRETTO_PIN=0:0:1,0:1:2"])

    def test_a_pinned_run_that_no_run_of_the_program_is(self):
        """With r pinned to rank 2, r3 can take rank 3's message while r is pending, and the
        replay matches every pin. Its own trace keeps r's original envelope and has a cycle;
        the recorded run has none."""
        replay = mk({
            0: [irecv(T.ANY, 0, 10, widx=0), recv(T.ANY, 0, 3, 0, widx=1), send(2, 5), wait((10, 2, 0))],
            1: [send(0, 0)],
            2: [recv(0, 5, 0, 5), send(0, 0)],
            3: [send(0, 0)],
        })
        self.assertEqual(replay.observed[(0, 0)], (2, 0))
        self.assertIsNotNone(unpinned_violation(replay))
        self.assertIsNotNone(unpinned_violation(replay, finished=False))
        self.assertIsNone(unpinned_violation(stolen_after_later_receive()))

    def test_a_pinned_run_of_the_program(self):
        """test_hb.reply_to_pending_receive with r <- s1 forced: a run of the program."""
        replay = mk({
            0: [irecv(T.ANY, 0, 10, widx=0), send(1, 5), wait((10, 1, 0)), recv(T.ANY, 0, 2, 0, widx=1)],
            1: [recv(0, 5, 0, 5), send(0, 0)],
            2: [send(0, 0)],
        })
        self.assertIsNone(unpinned_violation(replay))
        self.assertIsNone(unpinned_violation(reply_to_pending_receive()))

    def test_a_pending_receive_may_have_matched_unseen(self):
        """An Irecv(ANY) that never completed while a later Recv(ANY) took rank 2's message: the
        Irecv may have taken rank 1's message without its wait returning, whether the run
        finished or not. Pinned to rank 1 it may have; pinned to rank 2 it had nothing left to
        take, and the Recv overtook it."""
        cut = mk({0: [irecv(T.ANY, 0, 10, widx=0), recv(T.ANY, 0, 2, 0, widx=1)], 1: [send(0, 0)], 2: [send(0, 0)]})
        self.assertIsNone(unpinned_violation(cut))
        self.assertIsNone(unpinned_violation(cut, finished=False))
        self.assertIsNone(unpinned_violation(cut, pins="STRETTO_PIN=0:0:1"))
        self.assertIn("receiver order", unpinned_violation(cut, pins="STRETTO_PIN=0:0:2"))
        self.assertIn("receiver order", unpinned_violation(cut, finished=False, pins=["STRETTO_PIN=0:0:2"]))


def unfinished(raw, done=()):
    """A trace whose ranks not in done end without MPI_Finalize (a hung or killed run)."""
    t = T.Trace()
    T._build(t, {r: [{"op": "init", "np": len(raw)}] + recs + ([{"op": "finalize"}] if r in done else [])
                 for r, recs in raw.items()})
    return t


def begin(call, src=None, tag=None, widx=None, **kw):
    """The record of a call a rank was still in when the run ended (the tracer's begin record)."""
    d = {"op": "begin", "call": call, "comm": 0, **kw}
    if src is not None:
        d.update(src=0 if src == T.ANY else src, tag=0 if tag == T.ANY else tag, count=1, tsize=4)
        if src == T.ANY:
            d["anysrc"] = 1
        if tag == T.ANY:
            d["anytag"] = 1
    if widx is not None:
        d["widx"] = widx
    return d


def wild_irecv(src, tag, req, widx):
    d = {"op": "irecv", "src": 0 if src == T.ANY else src, "tag": 0 if tag == T.ANY else tag, "comm": 0, "count": 1, "tsize": 4,
         "req": req, "widx": widx}
    if src == T.ANY:
        d["anysrc"] = 1
    if tag == T.ANY:
        d["anytag"] = 1
    return d


class HungReplays(unittest.TestCase):
    """A replay of a predicted hang ends in a timeout. Its trace is judged up to where it
    stopped, and its end too: a receive or probe still waiting there that, without its pin,
    takes a message nobody took would have taken it in the program, and the hang is the pin's."""

    def test_a_pinned_receive_waits_beside_a_message_it_takes_unpinned(self):
        t = unfinished({0: [begin("recv", T.ANY, 0, widx=0)], 1: [send(0, 0)], 2: []}, done=(1, 2))
        why = unpinned_violation(t, finished=False, hung=True, pins="STRETTO_PIN=0:0:2")
        self.assertIn("end of the run", why)
        self.assertIn("pinned to rank 2", why)
        # unpinned, the replay itself should have matched them: no reading explains its end
        self.assertTrue(unpinned_violation(t, finished=False, hung=True).startswith("undetermined"))
        # a run that failed is judged up to where it stopped, not at its end
        self.assertIsNone(unpinned_violation(t, finished=False, pins="STRETTO_PIN=0:0:2"))

    def test_a_hang_of_the_program(self):
        """MBI's MessageRace_Gather_Send_Irecv_nok, its steal replayed: Irecv r(ANY, any tag)
        pinned to rank 0 takes rank 0's message, and the Irecv from rank 0 that follows waits
        forever; rank 2's message is left, which only r would take, and r has completed."""
        t = unfinished({1: [wild_irecv(T.ANY, T.ANY, 3, 0), wild_irecv(0, T.ANY, 4, 1), wait((3, 0, 0)),
                            begin("wait", kind="wait", reqs=[4])],
                        0: [send(1, 0)], 2: [send(1, 0)]}, done=(0, 2))
        self.assertIsNone(unpinned_violation(t, finished=False, hung=True, pins="STRETTO_PIN=1:0:0:0"))

    def test_a_pending_receive_read_both_ways(self):
        """Rank 0 posts Irecv r(ANY, tag 0), receives from rank 1, then waits for rank 2, which
        never sends. Pinned to rank 1, r took rank 1's first message unseen and the Recv its
        second (the reading that leaves r unmatched contradicts the replay: r would have taken
        the second); pinned to rank 2, r took nothing and the Recv overtook it."""
        t = unfinished({0: [wild_irecv(T.ANY, 0, 10, 0), recv(1, 0, 1, 0), begin("recv", 2, 0)],
                        1: [send(0, 0), send(0, 0)], 2: []}, done=(1, 2))
        self.assertIsNone(unpinned_violation(t, finished=False, hung=True, pins="STRETTO_PIN=0:0:1"))
        self.assertIn("receiver order", unpinned_violation(t, finished=False, hung=True, pins="STRETTO_PIN=0:0:2"))

    def test_a_pinned_probe_waits_beside_a_message(self):
        t = unfinished({0: [begin("probe", T.ANY, 0, kind="probe")], 1: [send(0, 0)], 2: []}, done=(1, 2))
        why = unpinned_violation(t, finished=False, hung=True, pins="STRETTO_PIN_PROBE=0:0:2")
        self.assertIn("end of the run", why)
        self.assertIn("probe(0, 0)", why)
        self.assertTrue(unpinned_violation(t, finished=False, hung=True).startswith("undetermined"))

    def test_too_many_readings(self):
        t = unfinished({0: [wild_irecv(T.ANY, 0, 10 + i, i) for i in range(4)], 1: [send(0, 0) for _ in range(4)],
                        2: [send(0, 0) for _ in range(4)]}, done=(1, 2))
        self.assertTrue(unpinned_violation(t, finished=False, hung=True, limit=8).startswith("undetermined"))
        self.assertIsNone(unpinned_violation(t, finished=False, hung=True))

    def test_judged_from_the_replay_directory(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(judge_replay(d, "TIMEOUT")[0], "unknown")
            for rank, recs in {0: [{"op": "init", "np": 3}, begin("recv", T.ANY, 0, widx=0)],
                               1: [{"op": "init", "np": 3}, send(0, 0), {"op": "finalize"}],
                               2: [{"op": "init", "np": 3}, {"op": "finalize"}]}.items():
                with open(os.path.join(d, f"rank-{rank}.jsonl"), "w") as fh:
                    fh.write("".join(json.dumps(r) + "\n" for r in recs))
            valid, why = judge_replay(d, "TIMEOUT", ["STRETTO_PIN=0:0:2"])
            self.assertEqual(valid, "False")
            self.assertIn("end of the run", why)
            self.assertEqual(judge_replay(d, "APP-ERROR:1", ["STRETTO_PIN=0:0:2"]), ("True", ""))


class TraceMatches(unittest.TestCase):
    """A replay's own trace says which pins it took; mpirun can cut the report lines apart."""

    def test_pins_taken_in_the_replays_trace(self):
        probe = {"op": "probe", "kind": "probe", "src": 0, "anysrc": 1, "tag": 3, "comm": 0, "flag": 1, "msrc": 1, "mtag": 3, "pidx": 0}
        t = mk({0: [recv(T.ANY, 0, 2, 0, widx=0), recv(T.ANY, T.ANY, 1, 5, widx=1), probe, recv(1, 3, 1, 3)],
                1: [send(0, 5), send(0, 3)], 2: [send(0, 0)]})
        pins = ["STRETTO_PIN=0:0:2,0:1:1:5", "STRETTO_PIN_PROBE=0:0:1"]
        self.assertEqual(trace_matches(t, pins), {("recv", 0, 0), ("recv", 0, 1), ("probe", 0, 0)})
        self.assertEqual(trace_matches(t, ["STRETTO_PIN=0:0:1,0:1:1:7", "STRETTO_PIN_PROBE=0:0:2"]), set())
        self.assertFalse(all_matched(pins, ""))
        self.assertTrue(all_matched(pins, "", None, trace_matches(t, pins)))
        self.assertTrue(all_matched(pins, "", {("recv", 0, 1)}, {("recv", 0, 1)}))

    def test_judge_replay_hands_the_taken_pins_back(self):
        with tempfile.TemporaryDirectory() as d:
            for rank, recs in {0: [recv(T.ANY, 0, 1, 0, widx=0)], 1: [send(0, 0)]}.items():
                with open(os.path.join(d, f"rank-{rank}.jsonl"), "w") as fh:
                    fh.write("".join(json.dumps(r) + "\n" for r in [{"op": "init", "np": 2}] + recs + [{"op": "finalize"}]))
            seen = set()
            self.assertEqual(judge_replay(d, "FINISHED", "STRETTO_PIN=0:0:1", seen), ("True", ""))
            self.assertEqual(seen, {("recv", 0, 0)})


def on_a_dup(tag=7, site_offset=0x1234):
    """Rank 0 receives with ANY_SOURCE and tag on a duplicate of MPI_COMM_WORLD (its local id 2),
    from a call site at site_offset in /x/main; rank 1 sends it."""
    world = lambda r: {"op": "comm", "name": "world", "parent": 0, "id": 0, "ranks": [0, 1], "gord": 0}
    self_ = lambda r: {"op": "comm", "name": "self", "parent": 0, "id": 1, "ranks": [r], "gord": 0}
    dup = {"op": "comm", "name": "dup", "parent": 0, "id": 2, "ranks": [0, 1], "gord": 1}
    t = T.Trace()
    T._build(t, {0: [{"op": "init", "np": 2}, world(0), self_(0), dup,
                     {"op": "site", "id": 0, "obj": "/x/main", "addr": 4096 + site_offset, "base": 4096},
                     {"op": "recv", "src": 0, "anysrc": 1, "tag": tag, "comm": 2, "count": 1, "tsize": 4, "msrc": 1, "mtag": tag,
                      "mcount": 1, "widx": 0, "site": 0},
                     {"op": "finalize"}],
                 1: [{"op": "init", "np": 2}, world(1), self_(1), dup,
                     {"op": "send", "mode": "std", "dst": 0, "tag": tag, "comm": 2, "count": 1, "tsize": 4}, {"op": "finalize"}]})
    return t


class PinIdentity(unittest.TestCase):
    """A pin names an operation by its ordinal over every communicator, tag and call site; it
    carries the traced operation's identity, and a report or a trace record of another
    operation at that ordinal does not count as the pin taken (replay/pin.h)."""

    def test_the_token_carries_communicator_tag_and_site(self):
        t = on_a_dup()
        r = t.recvs[(0, 0)]
        self.assertEqual(C.identity(t, r), (2, 7, 0x1234))
        self.assertEqual(C.pin_token(t, r, 1), "0:0:1@2:7:1234")
        self.assertEqual(C.with_identity(t, ["STRETTO_PIN=0:0:1"]), ["STRETTO_PIN=0:0:1@2:7:1234"])
        self.assertEqual(C.with_identity(t, ["STRETTO_PIN=0:0:1@x:7"]), ["STRETTO_PIN=0:0:1@x:7"])

    def test_both_formats_parse(self):
        toks = C.pin_tokens(["STRETTO_PIN=0:0:1,1:3:2:5@2:-1:ff", "STRETTO_PIN_PROBE=2:4:0@x:9"])
        self.assertEqual([(k.kind, k.rank, k.idx, k.src, k.tag, k.ident) for k in toks],
                         [("recv", 0, 0, 1, None, None), ("recv", 1, 3, 2, 5, (2, -1, 255)), ("probe", 2, 4, 0, None, (None, 9, None))])
        self.assertEqual(parse_pins("STRETTO_PIN=1:3:2:5@2:-1:ff"), ({(1, 3): (2, 5)}, {}))
        self.assertEqual(strip_identity("STRETTO_PIN=0:0:1@2:7:1234,0:1:1@2:7"), "STRETTO_PIN=0:0:1,0:1:1")

    def test_a_report_counts_only_from_the_pinned_operation(self):
        pin = "STRETTO_PIN=0:0:1@2:7:1234"
        self.assertTrue(pins_matched(pin, "[stretto-pin] rank 0: matched widx=0 src=1 id=2:7:1234\n"))
        self.assertFalse(pins_matched(pin, "[stretto-pin] rank 0: matched widx=0 src=1 id=0:7:1234\n"))   # another communicator
        self.assertFalse(pins_matched(pin, "[stretto-pin] rank 0: matched widx=0 src=1 id=2:8:1234\n"))   # another tag
        self.assertFalse(pins_matched(pin, "[stretto-pin] rank 0: matched widx=0 src=1 id=2:7:99\n"))     # another call site
        self.assertFalse(pins_matched(pin, "[stretto-pin] rank 0: matched widx=0 src=1\n"))               # an old library's line
        self.assertTrue(pins_matched("STRETTO_PIN=0:0:1@x:7", "[stretto-pin] rank 0: matched widx=0 src=1 id=5:7:99\n"))
        self.assertTrue(pins_matched("STRETTO_PIN=0:0:1", "[stretto-pin] rank 0: matched widx=0 src=1 id=5:3:99\n"))
        self.assertTrue(pins_applied(pin, "[stretto-pin] rank 0: applied widx=0 src=1 (world 1) tag=7 id=2:7:1234\n"))
        self.assertFalse(pins_applied(pin, "[stretto-pin] rank 0: applied widx=0 src=1 (world 1) tag=7 id=2:7:1235\n"))
        probe = "STRETTO_PIN_PROBE=1:0:2@0:-1:40"
        self.assertTrue(pins_matched(probe, "[stretto-pin] rank 1: applied probe pidx=0 src=2 tag=3 id=0:-1:40\n"))
        self.assertFalse(pins_matched(probe, "[stretto-pin] rank 1: applied probe pidx=0 src=2 tag=3 id=0:3:40\n"))

    def test_the_replays_trace_must_show_the_pinned_operation(self):
        replay = on_a_dup()
        self.assertEqual(trace_matches(replay, ["STRETTO_PIN=0:0:1@2:7:1234"]), {("recv", 0, 0)})
        self.assertEqual(C.trace_misapplied(replay, ["STRETTO_PIN=0:0:1@2:7:1234"]), [])
        # the trace recorded a receive with tag 8 there: the replay diverged before the pin
        self.assertEqual(trace_matches(replay, ["STRETTO_PIN=0:0:1@2:8:1234"]), set())
        why = C.trace_misapplied(replay, ["STRETTO_PIN=0:0:1@2:8:1234"])
        self.assertEqual(len(why), 1)
        self.assertIn("id=2:7:1234", why[0])
        moved = on_a_dup(site_offset=0x2000)
        self.assertEqual(trace_matches(moved, ["STRETTO_PIN=0:0:1@2:7:1234"]), set())
        self.assertEqual(trace_matches(moved, ["STRETTO_PIN=0:0:1"]), {("recv", 0, 0)})     # an old pin is not checked

    def test_a_misapplied_pin_is_reported(self):
        line = ("[stretto-pin] rank 0: pin widx=3 misapplied: the run's operation has id=0:5:1a0, the trace's id=2:7:1234\n"
                "[stretto-pin] rank 1: probe pin pidx=0 misapplied: the run's operation has id=0:5:1a0, the trace's id=0:-1:x\n")
        self.assertEqual(len(C.pins_misapplied(line)), 2)

        class Run:
            stderr = ""
        self.assertEqual(C.misapplied_pins(Run(), "TIMEOUT"), [])
        self.assertEqual(len(C.misapplied_pins(Run(), f"APP-ERROR:{C.PIN_MISAPPLIED}")), 1)   # the line lost, the exit code kept


class Launcher(unittest.TestCase):
    """confirm launches a program with the launcher of the MPI it was built with."""

    def fake(self, d, name, version):
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            fh.write(f"#!/bin/sh\necho '{version}'\n")
        os.chmod(path, 0o755)
        return path

    def test_the_launcher_follows_the_mpi(self):
        saved = {k: os.environ.pop(k, None) for k in ("STRETTO_MPIRUN", "STRETTO_MPIRUN_MPICH", "STRETTO_MPIRUN_OPENMPI")}
        try:
            with tempfile.TemporaryDirectory() as d:
                hydra = self.fake(d, "mpiexec.hydra", "HYDRA build details:\n    Version: 4.2.0")
                orte = self.fake(d, "mpirun", "mpirun (Open MPI) 4.1.6")
                os.environ["STRETTO_MPIRUN_MPICH"] = hydra
                os.environ["STRETTO_MPIRUN_OPENMPI"] = orte
                self.assertEqual(C.launcher("mpich"), (hydra, "hydra"))
                self.assertEqual(C.launcher("openmpi"), (orte, "openmpi"))
                self.assertEqual(C.launcher_env("hydra", "STRETTO_PIN", "0:0:1@0:5"), ["-genv", "STRETTO_PIN", "0:0:1@0:5"])
                self.assertEqual(C.launcher_env("openmpi", "STRETTO_PIN", "0:0:1@0:5"), ["-x", "STRETTO_PIN=0:0:1@0:5"])
                os.environ["STRETTO_MPIRUN_MPICH"] = os.path.join(d, "missing")
                with self.assertRaises(C.HarnessError):
                    C.launcher("mpich")
            del os.environ["STRETTO_MPIRUN_MPICH"], os.environ["STRETTO_MPIRUN_OPENMPI"]
            self.assertEqual(C.DEFAULT_LAUNCHERS["mpich"], "/usr/bin/mpirun.openmpi")   # Ubuntu's MPICH speaks PMIx
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class Verdicts(unittest.TestCase):
    def test_strict_verdict(self):
        self.assertEqual(strict_verdict("confirmed", "True"), "confirmed")
        self.assertEqual(strict_verdict("confirmed", "False"), "pins only")
        self.assertEqual(strict_verdict("confirmed", "unknown"), "undetermined")
        self.assertEqual(strict_verdict("confirmed", ""), "undetermined")
        self.assertEqual(strict_verdict("inconclusive", "False"), "inconclusive")
        self.assertEqual(strict_verdict("n/a", ""), "n/a")

    def test_parse_pins(self):
        self.assertEqual(parse_pins(["STRETTO_PIN=0:1:2,1:0:3:7", "STRETTO_PIN_PROBE=2:4:0"]),
                         ({(0, 1): (2, None), (1, 0): (3, 7)}, {(2, 4): (0, None)}))
        self.assertEqual(parse_pins("STRETTO_PIN=0:0:1"), ({(0, 0): (1, None)}, {}))
        self.assertEqual(parse_pins(None), ({}, {}))


class SampleRows(unittest.TestCase):
    """experiments/replay_sample.py --from-csv replays the alternatives an earlier sample names."""

    def test_findings_of_sample_rows(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "experiments"))
        from replay_sample import finding_of
        f = finding_of("iprobe(5, 501) src=ANY tag=2000 could observe send(6, 580)->5 tag=2000 40B instead of send(1, 573)->5 tag=2000 144B",
                       "probe-alt", "STRETTO_PIN_PROBE=5:62:6")
        self.assertEqual((f.probe, f.send, f.recv, f.level, f.pin), ((5, 501), (6, 580), None, "L1", "STRETTO_PIN_PROBE=5:62:6"))
        f = finding_of("recv(4, 4344) src=ANY tag=9172 cap=28576B <- send(6, 2896)->4 tag=9172 28576B (observed send(5, 1448)->4 "
                       "tag=9172 28576B); recv(4, 4345) src=ANY tag=9172 cap=28", "1f")
        self.assertEqual((f.recv, f.send, f.probe, f.cls), ((4, 4344), (6, 2896), None, "1f"))
        self.assertIsNone(finding_of("deadlock in some executions after recv(0, 0)"))


class PinFile(unittest.TestCase):
    """A spec longer than one environment string may be (128 KiB on Linux) reaches the ranks
    as NAME=@file; the replay library reads the file."""

    def test_short_specs_stay_inline(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(pin_assignment("STRETTO_PIN=0:0:2", d), "STRETTO_PIN=0:0:2")
            self.assertEqual(os.listdir(d), [])

    def test_long_specs_go_through_a_file(self):
        spec = ",".join(f"{i % 8}:{i // 8}:{(i + 1) % 8}" for i in range(40000))
        self.assertGreater(len(spec), PIN_INLINE_MAX)
        with tempfile.TemporaryDirectory() as d:
            a = pin_assignment("STRETTO_PIN_PROBE=" + spec, d)
            name, _, value = a.partition("=")
            self.assertEqual(name, "STRETTO_PIN_PROBE")
            self.assertTrue(value.startswith("@" + d))
            with open(value[1:]) as fh:
                self.assertEqual(fh.read(), spec)

    def test_many_reported_pins(self):
        n = 20000
        pin = "STRETTO_PIN=" + ",".join(f"0:{i}:1" for i in range(n))
        out = "".join(f"[stretto-pin] rank 0: matched widx={i} src=1\n" for i in range(n))
        self.assertTrue(pins_matched(pin, out))
        self.assertFalse(pins_matched(pin + ",0:20000:1", out))
        self.assertTrue(all_matched([pin], out, {("recv", 0, i) for i in range(0, n, 7)}))


if __name__ == "__main__":
    unittest.main()
