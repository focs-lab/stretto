"""The replay classifier and the pin-applied check, without running MPI."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stretto.confirm import (PIN_INLINE_MAX, all_matched, classify_rc, control_pin, expected_outcomes, horizon_pins,  # noqa: E402
                             pin_assignment, pins_applied, pins_matched, required_matches)
from stretto import trace as T  # noqa: E402
from stretto.report import Finding  # noqa: E402

APPLIED = ("[stretto-pin] rank 0: 2 pin(s) active\n[stretto-pin] rank 0: applied widx=0 src=2 (world 2) tag=5\n"
           "[stretto-pin] rank 0: applied widx=1 src=1 (world 1) tag=5\n[stretto-pin] rank 1: applied probe pidx=0\n")


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
        self.assertEqual(expected_outcomes(Finding("1", "1a", "L1", "")), {"MPI-ERROR:MPI_ERR_TRUNCATE", "MPI-ERROR:MPI_ERR_TYPE"})
        self.assertEqual(expected_outcomes(Finding("1", "1f", "L1", "")), set())


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
        self.assertEqual(control_pin(t, "STRETTO_PIN=0:0:2,0:1:1"), "STRETTO_PIN=0:0:1,0:1:2")
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
        self.assertEqual(alt, ["STRETTO_PIN=0:0:1,0:1:3"])        # rank 0 is cut right after the changed receive
        self.assertEqual(ctl, ["STRETTO_PIN=0:0:1,0:1:2"])
        self.assertTrue(pins_matched(alt[0], "[stretto-pin] rank 0: matched widx=0 src=1\n[stretto-pin] rank 0: matched widx=1 src=3\n"))
        self.assertFalse(pins_matched(alt[0], "[stretto-pin] rank 0: matched widx=1 src=3\n"))

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
        self.assertEqual(alt, ["STRETTO_PIN=1:0:0:0"])
        self.assertEqual(ctl, ["STRETTO_PIN=1:0:2:0,1:1:0:0"])
        req = required_matches(t, f)
        self.assertEqual(req, {("recv", 1, 0)})
        self.assertTrue(all_matched(alt, "[stretto-pin] rank 1: matched widx=0 src=0\n", req))
        self.assertFalse(all_matched(alt, "[stretto-pin] rank 1: applied widx=0\n", req))



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
