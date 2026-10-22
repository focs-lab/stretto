"""The statistics of the last section of docs/results.md (experiments/tables.py, "Statistics"),
on small inputs the tests write themselves."""
import importlib.util
import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
