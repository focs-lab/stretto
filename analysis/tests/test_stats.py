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


class SignTest(unittest.TestCase):
    """The exact two-sided sign test of a paired comparison, from its discordant pairs."""

    def test_known_values(self):
        self.assertAlmostEqual(tables.sign_test(12, 4), 2 * (1 + 16 + 120 + 560 + 1820) / 2 ** 16, places=15)
        self.assertAlmostEqual(tables.sign_test(15, 0), 2 / 2 ** 15, places=15)
        self.assertAlmostEqual(tables.sign_test(9, 1), 2 * 11 / 2 ** 10, places=15)
        self.assertEqual(tables.p_text(tables.sign_test(12, 4)), "0.077")
        self.assertEqual(tables.p_text(tables.sign_test(15, 0)), "0.000061")
        self.assertEqual(tables.p_text(tables.sign_test(41, 0)), "9.1e-13")
        self.assertEqual(float(tables.p_text(tables.sign_test(41, 0))), 9.1e-13)

    def test_even_splits_and_none(self):
        for b, c in ((0, 0), (3, 3), (4, 5), (1, 0)):
            self.assertEqual(tables.sign_test(b, c), 1.0, (b, c))
        self.assertEqual(tables.p_text(1.0), "1")
        self.assertEqual(tables.sign_test(7, 2), tables.sign_test(2, 7))
        with self.assertRaises(ValueError):
            tables.sign_test(-1, 2)

    def test_it_falls_with_the_imbalance(self):
        ps = [tables.sign_test(20 - c, c) for c in range(11)]
        self.assertEqual(ps, sorted(ps))
        self.assertLess(ps[0], 1e-5)


class Baselines(unittest.TestCase):
    """Which labelled errors each tool finds, by E1's scoring, over the ones the stress baseline ran."""

    def test_union_of_must_and_stress(self):
        h = "name,expect,detail,build,run,classes,levels,must"
        corpus = ["a,ERROR,,,FINISHED,2-buffering,observed:2-buffering,MUST:ERROR",
                  "b,ERROR,,,TIMEOUT,2-blocked,observed:2-blocked,MUST:TIMEOUT",
                  "c,ERROR,,,FINISHED,,,MUST:OK",
                  "d,ERROR,,,FINISHED,1c,L1+SP:1c,MUST:OK",
                  "e,ERROR,,CE,FINISHED,,,MUST:OK",
                  "f,OK,,,FINISHED,,,MUST:OK",
                  "g,ERROR,,,FINISHED,,,MUST:OK"]
        stress = ["a,ERROR,30,,,", "b,ERROR,1,1,TIMEOUT,", "c,ERROR,2,2,TIMEOUT,", "d,ERROR,30,,,", "e,ERROR,1,1,TIMEOUT,",
                  "f,OK,30,,,"]                    # g: not run by the stress baseline
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "x.csv"), "w") as fh:
                fh.write(h + "\n" + "".join(x + "\n" for x in corpus))
            with open(os.path.join(d, "stress.csv"), "w") as fh:
                fh.write("program,expect,runs,first_failure,outcome,commit\n" + "".join(x + "\n" for x in stress))
            f = tables.baselines_found(os.path.join(d, "x.csv"))
        self.assertEqual(f, {"Errors": {"a", "b", "c", "d"}, "Stretto": {"a", "b", "d"}, "MUST": {"a"},
                             "MUSTsym": {"a", "b"}, "Stress": {"b", "c"}})
        self.assertEqual(f["Stretto"] - (f["MUST"] | f["Stress"]), {"d"})
        self.assertEqual((f["MUST"] | f["Stress"]) - f["Stretto"], {"c"})


class EfourRange(unittest.TestCase):
    """E4's overhead range is taken over the configurations whose plain timed section lasts at least
    1 s; the statistics count exactly those."""

    def test_the_range_and_its_configurations(self):
        h = "benchmark,class,np,rep,plain_s,traced_s,slowdown,plain_npb_s,traced_npb_s,npb_slowdown,trace_MB,events,wildcards,analysis_s,findings,classes,commit"
        lines = ["lu,A,4,1,12.3,12.5,1.02,7.70,7.90,1.026,68,1,0,8,0,,a", "lu,A,4,2,12.4,12.6,1.02,7.80,8.00,1.020,68,1,0,8,0,,a",
                 "ep,A,4,1,5.5,5.5,1.00,1.20,1.30,1.090,1,1,0,0.1,0,,a", "ep,A,4,2,5.5,5.5,1.00,0.90,1.00,1.110,1,1,0,0.1,0,,a",
                 "is,S,4,1,4.4,4.4,1.00,0.00,0.00,,1,1,0,0.1,0,,a", "is,S,4,2,4.4,4.4,1.00,0.00,0.00,,1,1,0,0.1,0,,a",
                 "cg,S,4,1,4.4,4.5,1.01,0.02,0.05,2.500,4,1,0,0.5,0,,a", "cg,S,4,2,4.4,4.5,1.01,0.02,0.05,2.500,4,1,0,0.5,0,,a"]
        saved = dict(tables.NUM)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(tables, "RES", d):
            os.makedirs(os.path.join(d, "e4"))
            with open(os.path.join(d, "e4", "e4.csv"), "w") as fh:
                fh.write(h + "\n" + "".join(x + "\n" for x in lines))
            try:
                tables.NUM.clear()
                tables.e4()
                text = "\n".join(tables._efour())
                got = {k: tables.NUM[k] for k in ("TraceOverhead", "nEfourConfigs", "nEfourLong", "nEfourLongNames")}
            finally:
                tables.NUM.clear()
                tables.NUM.update(saved)
        # ep.A.4's median plain time is 1.05 s, one of its runs below 1 s; lu.A.4's 7.75 s; the S classes less than 1 s
        self.assertEqual(got, {"TraceOverhead": "1.02--1.10", "nEfourConfigs": "4", "nEfourLong": "2", "nEfourLongNames": "lu.A.4, ep.A.4"})
        self.assertIn("| lu.A.4 | 7.75 | 7.95 | 1.023 (1.02-1.03) |", text)
        self.assertIn("1 have no NPB slowdown, their plain timed section printing 0.00 s.", text)


if __name__ == "__main__":
    unittest.main()
