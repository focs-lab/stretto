"""The statistics of the last section of docs/results.md (experiments/tables.py, "Statistics"),
on small inputs the tests write themselves."""
import importlib.util
import os
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def module(path: str, name: str):
    """Import a script of the repository by its path."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tables = module("experiments/tables.py", "tables_stats")


class Wilson(unittest.TestCase):
    """Wilson's score interval: the proportions whose score test accepts k/n."""

    def test_each_inner_bound_solves_the_score_equation(self):
        z = tables.Z95
        for k, n in ((25, 40), (11, 40), (8, 40), (15, 20), (1, 3), (1437, 1440), (1, 2)):
            for p in tables.wilson(k, n):
                self.assertAlmostEqual((k / n - p) ** 2, z * z * p * (1 - p) / n, places=12, msg=(k, n, p))

    def test_the_bound_at_no_success_or_no_failure_is_exact(self):
        z2 = tables.Z95 ** 2
        for n in (1, 20, 40, 80, 320):
            lo, hi = tables.wilson(0, n)
            self.assertEqual(lo, 0.0)
            self.assertAlmostEqual(hi, z2 / (n + z2), places=12)
            lo, hi = tables.wilson(n, n)
            self.assertEqual(hi, 1.0)
            self.assertAlmostEqual(lo, n / (n + z2), places=12)

    def test_it_holds_the_rate_and_is_symmetric(self):
        for k, n in ((0, 5), (3, 7), (25, 40), (40, 40)):
            lo, hi = tables.wilson(k, n)
            self.assertTrue(0 <= lo <= k / n <= hi <= 1)
            lo2, hi2 = tables.wilson(n - k, n)
            self.assertAlmostEqual(lo, 1 - hi2, places=12)
            self.assertAlmostEqual(hi, 1 - lo2, places=12)
        self.assertLess(tables.wilson(50, 80)[1] - tables.wilson(50, 80)[0], tables.wilson(25, 40)[1] - tables.wilson(25, 40)[0])

    def test_no_proportion_no_interval(self):
        for k, n in ((0, 0), (5, 4), (-1, 4)):
            with self.assertRaises(ValueError):
                tables.wilson(k, n)

    def test_the_intervals_as_written(self):
        text = lambda k, n: "[%s, %s]" % tables.bounds_text(*tables.wilson(k, n))   # noqa: E731
        self.assertEqual(text(25, 40), "[0.47, 0.76]")
        self.assertEqual(text(11, 40), "[0.16, 0.43]")
        self.assertEqual(text(15, 20), "[0.53, 0.89]")
        self.assertEqual(text(0, 40), "[0, 0.088]")
        self.assertEqual(text(0, 80), "[0, 0.046]")
        self.assertEqual(text(0, 30), "[0, 0.11]")
        self.assertEqual(text(20, 20), "[0.84, 1]")
        self.assertEqual(text(1, 40), "[0.0044, 0.13]")
        self.assertEqual(tables.bounds_text(0.9939, 0.99929), ("0.9939", "0.99929"))
        self.assertEqual(tables.bounds_text(0.0996, 0.5), ("0.100", "0.50"))

    def test_the_macros_sit_next_to_the_count(self):
        self.assertEqual(tables.ci("nTestHang", 25, 40), "[0.47, 0.76]")
        self.assertEqual((tables.NUM["nTestHangCI"], tables.NUM["nTestHangCILo"], tables.NUM["nTestHangCIHi"]),
                         ("[0.47, 0.76]", "0.47", "0.76"))

    def test_a_missing_or_impossible_count_gets_no_interval(self):
        saved = dict(tables.NUM)
        try:
            tables.NUM.clear()
            tables.num("nHypreFourExposed", 15)
            tables.num("nHypreFourRuns", 20)
            tables.num("nHypreEightExposed", 21)          # more than its runs
            tables.num("nHypreEightRuns", 20)
            tables.num("nDealTraced", 20)                 # no count of exposed runs
            text = "\n".join(tables._intervals())
            self.assertIn("| HYPRE 2.10.1, 4 processes | 15 of 20 | [0.53, 0.89] | `nHypreFourExposedCI` |", text)
            self.assertIn("| HYPRE 2.10.1, 8 processes | 21 of 20 | none: the count exceeds the runs | |", text)
            self.assertNotIn("deal.II", text)
            self.assertNotIn("nHypreEightExposedCI", tables.NUM)
        finally:
            tables.NUM.clear()
            tables.NUM.update(saved)


class DistinctPins(unittest.TestCase):
    """The alternatives behind replays: a pin names a receive or probe by rank and ordinal, and the
    source it is pinned to; the same pin from another trace of the same configuration is the same
    alternative."""

    def test_a_pin_as_the_tables_record_it(self):
        self.assertEqual(tables.pin_id("STRETTO_PIN_PROBE=2:3:4"), ("probe", 2, 3, 4))
        self.assertEqual(tables.pin_id("STRETTO_PIN=14:371:6"), ("recv", 14, 371, 6))

    def test_replays_of_one_alternative_count_once(self):
        rs = [{"np": "16", "run": run, "pin": pin} for run in ("1", "2", "3")
              for pin in ("STRETTO_PIN=4:22:12", "STRETTO_PIN=4:23:12", "STRETTO_PIN=4:22:11")]
        self.assertEqual(tables.distinct_pins(rs, "np"), 3)
        self.assertEqual(tables.distinct_pins(rs, "np", "run"), 9)     # the run in the scope: one per trace

    def test_scope_kind_and_source_tell_alternatives_apart(self):
        rs = [{"np": "4", "pin": "STRETTO_PIN_PROBE=1:3:2"}, {"np": "8", "pin": "STRETTO_PIN_PROBE=1:3:2"},
              {"np": "4", "pin": "STRETTO_PIN=1:3:2"}, {"np": "4", "pin": "STRETTO_PIN_PROBE=1:3:3"},
              {"np": "4", "pin": ""}, {"np": "4"}]
        self.assertEqual(tables.distinct_pins(rs, "np"), 4)
        self.assertEqual(tables.distinct_pins(rs), 3)

    def test_only_replayed_rows_count(self):
        self.assertTrue(tables._replayed({"pin": "STRETTO_PIN=1:2:3", "replay": "TIMEOUT"}))
        for r in ({"pin": "STRETTO_PIN=1:2:3", "replay": "no horizon"}, {"pin": "", "replay": "FINISHED"},
                  {"pin": "STRETTO_PIN=1:2:3", "replay": ""}, {"pin": "STRETTO_PIN=1:2:3", "replay": "traced run TIMEOUT"}):
            self.assertFalse(tables._replayed(r), r)


class PinLog(unittest.TestCase):
    """The pinned runs of a campaign table: a replay and its control are two runs, an alternative
    without a horizon is not replayed, and a run counts as matched only when its log says so."""

    def test_replays_and_controls_alike(self):
        rs = [{"pin": "STRETTO_PIN=1:2:3", "replay": "TIMEOUT", "matched": "True", "control": "FINISHED", "control_matched": "True"},
              {"pin": "STRETTO_PIN=1:2:4", "replay": "FINISHED", "matched": "False", "control": "FINISHED", "control_matched": ""},
              {"pin": "STRETTO_PIN=1:2:5", "replay": "no horizon", "matched": "", "control": "", "control_matched": ""},
              {"pin": "STRETTO_PIN=1:2:6", "replay": "APP-ERROR:15", "matched": "False", "control": "", "control_matched": ""},
              {"pin": "", "replay": "traced run TIMEOUT"}]
        self.assertEqual(tables.pinned_runs(rs), [("TIMEOUT", True), ("FINISHED", True), ("FINISHED", False), ("FINISHED", False),
                                                  ("APP-ERROR:15", False)])

    def test_by_campaign_and_over_all(self):
        h = "np,run,pin,kind,replay,matched,differs,control,control_matched,commit"
        tabs = {"dealii/predict-9.3.0-rc1.csv": ["4,1,STRETTO_PIN_PROBE=0:52:2,cross,FINISHED,True,True,FINISHED,True,a",
                                                 "4,1,STRETTO_PIN_PROBE=0:52:3,cross,FINISHED,False,True,FINISHED,False,a"],
                "hypre3/predict.csv": ["4,1,STRETTO_PIN_PROBE=1:0:0,other,TIMEOUT,False,,FINISHED,True,b"]}
        saved = dict(tables.NUM)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(tables, "RES", d):
            for name, lines in tabs.items():
                os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
                with open(os.path.join(d, name), "w") as fh:
                    fh.write(h + "\n" + "".join(x + "\n" for x in lines))
            try:
                tables.NUM.clear()
                text = "\n".join(tables._pin_log_all())
                got = {k: tables._count(k) for k in tables.NUM if k.startswith("nPin") or k == "nDealWrongConfirmed"}
            finally:
                tables.NUM.clear()
                tables.NUM.update(saved)
        self.assertEqual(got, {"nPinRunsDeal": 4, "nPinUnmatchedDeal": 2, "nPinUnmatchedFinishedDeal": 2, "nPinUnmatchedHungDeal": 0,
                               "nPinUnmatchedEndedDeal": 2, "nPinRunsHypreNew": 2, "nPinUnmatchedHypreNew": 1,
                               "nPinUnmatchedFinishedHypreNew": 0, "nPinUnmatchedHungHypreNew": 1, "nPinUnmatchedEndedHypreNew": 1,
                               "nPinRunsAll": 6, "nPinUnmatchedAll": 3, "nPinUnmatchedFinishedAll": 2, "nPinUnmatchedHungAll": 1,
                               "nPinUnmatchedEndedAll": 3, "nDealWrongConfirmed": 1})
        self.assertIn("| **all** | 6 | 3 | 2 | 1 | 0 |", text)


if __name__ == "__main__":
    unittest.main()
