"""The replay classifier and the pin-applied check, without running MPI."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rematch.confirm import classify_rc, expected_outcomes, pins_applied  # noqa: E402
from rematch.report import Finding  # noqa: E402

APPLIED = ("[rematch-pin] rank 0: 2 pin(s) active\n[rematch-pin] rank 0: applied widx=0 src=2 (world 2) tag=5\n"
           "[rematch-pin] rank 0: applied widx=1 src=1 (world 1) tag=5\n[rematch-pin] rank 1: applied probe pidx=0\n")


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
        self.assertTrue(pins_applied("REMATCH_PIN=0:0:2", APPLIED))
        self.assertTrue(pins_applied("REMATCH_PIN=0:0:2,0:1:1:5", APPLIED))
        self.assertFalse(pins_applied("REMATCH_PIN=0:0:2,0:2:1", APPLIED))      # widx 2 never reached
        self.assertFalse(pins_applied("REMATCH_PIN=0:1:1", APPLIED.replace("widx=1", "widx=10")))
        self.assertFalse(pins_applied("REMATCH_PIN=0:0:2", ""))

    def test_probe_pins(self):
        self.assertTrue(pins_applied("REMATCH_PIN_PROBE=1:0:2", APPLIED))
        self.assertFalse(pins_applied("REMATCH_PIN_PROBE=1:1:2", APPLIED))


if __name__ == "__main__":
    unittest.main()
