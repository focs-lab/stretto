"""Ground truth (explorer over the DSL twins) and soundness of the trace-based checker.
Run: cd analysis && python3 -m unittest discover -s tests. Tests that need traced idioms
skip when traces/ is absent, or fail with REMATCH_REQUIRE_TRACES=1 (set it after
scripts/run_all.sh so that a clean checkout cannot pass vacuously)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from rematch import dsl, explore, trace  # noqa: E402
from rematch.feasible import check, feasible_profile  # noqa: E402
from rematch.matching import candidates  # noqa: E402
from rematch.report import analyze  # noqa: E402
from test_checker import fast_equals_generic  # noqa: E402
import idioms_dsl as I  # noqa: E402

TRACES = os.path.join(ROOT, "traces", os.environ.get("MPI", "openmpi"))
REQUIRE = os.environ.get("REMATCH_REQUIRE_TRACES") == "1"


def need_trace(tc, n):
    """The trace directory of idiom n, or skip (fail under REMATCH_REQUIRE_TRACES=1)."""
    d = traced(n)
    if d is None:
        if REQUIRE:
            tc.fail(f"idiom {n:02d} not traced (REMATCH_REQUIRE_TRACES=1)")
        tc.skipTest(f"idiom {n:02d} not traced")
    return d


def traced(n):
    for d in sorted(os.listdir(TRACES)) if os.path.isdir(TRACES) else []:
        if d.startswith(f"{n:02d}_"):
            return os.path.join(TRACES, d)
    return None


class ExplorerGroundTruth(unittest.TestCase):
    """What the twins can do, exhaustively, at B=0 and B=inf."""

    def test_01_master_worker_symmetric(self):
        for B in (0, None):
            res = explore.explore(I.idiom01(), B)
            self.assertEqual(res.kinds(), {"done"}, f"B={B}: {[str(o) for o in res.outcomes]}")
            self.assertIn(((0, 0), (1, 0)), res.matches)
            self.assertIn(((0, 0), (2, 0)), res.matches)

    def test_02_waitany_symmetric(self):
        for B in (0, None):
            res = explore.explore(I.idiom02(), B)
            self.assertEqual(res.kinds(), {"done"})

    def test_03_head_to_head_buffering(self):
        self.assertEqual(explore.explore(I.idiom03(), 0).kinds(), {"deadlock"})
        self.assertEqual(explore.explore(I.idiom03(), 399).kinds(), {"deadlock"})
        self.assertEqual(explore.explore(I.idiom03(), 400).kinds(), {"done"})
        self.assertEqual(explore.explore(I.idiom03(), None).kinds(), {"done"})

    def test_04_orphan(self):
        for B in (0, None):
            res = explore.explore(I.idiom04(), B)
            self.assertEqual(res.kinds(), {"done", "deadlock"})
            bad = [o for o in res.outcomes if o.kind == "deadlock"]
            self.assertTrue(all(((0, 0), (2, 0)) in o.matching and (1, 0) in o.orphans for o in bad))

    def test_05_truncate(self):
        res = explore.explore(I.idiom05(), None)
        self.assertIn("truncate", res.kinds())
        self.assertIn("done", res.kinds())

    def test_06_collective_depends_on_source(self):
        for B in (0, None):
            res = explore.explore(I.idiom06(), B)
            self.assertIn("deadlock", res.kinds())
            bad = [o for o in res.outcomes if o.kind == "deadlock"]
            self.assertTrue(all(((0, 0), (2, 0)) in o.matching for o in bad))
            self.assertTrue(all(o.kind == "done" for o in res.outcomes if ((0, 0), (1, 0)) in o.matching))

    def test_07_span_only(self):
        self.assertEqual(explore.explore(I.idiom07(), 0).kinds(), {"done"})

    def test_08_cross_purpose(self):
        res = explore.explore(I.idiom08(), None)
        self.assertEqual(res.kinds(), {"done", "deadlock"})
        self.assertIn(((0, 0), (2, 0)), res.matches)

    def test_09_fifo_chain(self):
        res = explore.explore(I.idiom09(), None)
        self.assertIn(((0, 1), (1, 1)), res.matches)      # rB <- s1'
        self.assertNotIn(((0, 0), (1, 1)), res.matches)   # rA <- s1' impossible
        self.assertNotIn(((0, 1), (1, 0)), res.matches)   # rB <- s1 impossible
        self.assertIn("deadlock", res.kinds())

    def test_10_halo_no_freedom(self):
        for B in (0, None):
            res = explore.explore(I.idiom10(), B)
            self.assertEqual(res.kinds(), {"done"})
            self.assertEqual(len({o.matching for o in res.outcomes}), 1)


class CheckerSoundness(unittest.TestCase):
    """Every alternative the polynomial checker calls feasible must be realisable by the
    exhaustive explorer over the trace-as-program, at the same B. Skipped for idioms
    that have not been traced yet (run scripts/run_all.sh first)."""

    def _trace(self, n):
        return trace.load(need_trace(self, n))

    def _sound(self, n):
        t = self._trace(n)
        prog = dsl.program_from_trace(t)
        for alt in candidates(t):
            for B in (0, None):
                v = check(t, alt.M, alt.changed, B, "L1")
                if not v.feasible:
                    continue
                res = explore.explore(prog, B, force={alt.recv: alt.send})
                self.assertTrue(res.outcomes_with(alt.recv, alt.send),
                                f"idiom {n}: checker accepts {alt.describe(t)} at B={B}, explorer cannot realise it")

    def _monotone(self, n):
        """The full profile (what the report uses) is monotone in B, and the bisecting
        profile, which assumes that, agrees with it."""
        t = self._trace(n)
        sizes = [0] + t.message_sizes() + [None]
        for alt in candidates(t):
            full = feasible_profile(t, alt.M, alt.changed, "L1", full=True)
            verdicts = [full[B] for B in sizes]
            # once feasible, stays feasible for larger B
            self.assertEqual(verdicts, sorted(verdicts), f"idiom {n}: non-monotone in B for {alt.describe(t)}: {verdicts}")
            self.assertEqual(feasible_profile(t, alt.M, alt.changed, "L1", full=False), full,
                             f"idiom {n}: bisected profile differs from the full one for {alt.describe(t)}")
        # the report built on the bisecting profiles (the default) equals the one built on full profiles
        bisected, every = analyze(t, with_sp=False), analyze(t, with_sp=False, full_profile=True)
        self.assertEqual(([str(f) for f in bisected.findings], bisected.infeasible),
                         ([str(f) for f in every.findings], every.infeasible), f"idiom {n}: bisected report differs from the full one")

    def _coverage(self, n, expected_pairs):
        t = self._trace(n)
        found = {(alt.recv, alt.send) for alt in candidates(t)
                 if check(t, alt.M, alt.changed, None, "L1").feasible}
        for pair in expected_pairs:
            self.assertIn(pair, found, f"idiom {n}: expected feasible alternative {pair} not found; got {found}")


for _n in range(1, 12):
    setattr(CheckerSoundness, f"test_{_n:02d}_sound", lambda self, n=_n: self._sound(n))
    setattr(CheckerSoundness, f"test_{_n:02d}_monotone_in_B", lambda self, n=_n: self._monotone(n))
    setattr(CheckerSoundness, f"test_{_n:02d}_fast_equals_generic", lambda self, n=_n: fast_equals_generic(self, self._trace(n)))

_EXPECTED = {1: [((0, 0), (2, 0))], 4: [((0, 0), (2, 0))], 5: [((0, 0), (2, 0))],
             6: [((0, 0), (2, 0))], 8: [((0, 0), (2, 0))], 9: [((0, 1), (1, 1))]}
for _n, _pairs in _EXPECTED.items():
    setattr(CheckerSoundness, f"test_{_n:02d}_coverage", lambda self, n=_n, p=_pairs: self._coverage(n, p))


class ReportClasses(unittest.TestCase):
    """The README expectation of each traced idiom, as classes."""
    EXPECT = {1: {"1f", "1d", "1c"}, 2: {"waitany-order"}, 3: {"2-buffering"}, 4: {"1f", "1c", "1d"},
              5: {"1a", "1c", "1d"}, 6: {"1f", "1c", "1d"}, 7: set(), 8: {"1b", "1c", "1d"}, 9: {"1b", "1c", "1d"}, 10: set()}

    def _classes(self, n):
        t = trace.load(need_trace(self, n))
        rep = analyze(t)
        got = set(rep.classes())
        self.assertEqual(got, self.EXPECT[n], f"idiom {n}: classes {got}")
        if n == 9:
            self.assertEqual(len([f for f in rep.findings if f.level == "L1"]), 1)


for _n in range(1, 11):
    setattr(ReportClasses, f"test_{_n:02d}_classes", lambda self, n=_n: self._classes(n))


class LevelTwo(unittest.TestCase):
    """The twins of idioms 01 and 06 under their alternative: the continuation diverges (1e)."""

    def test_01_reply_goes_elsewhere(self):
        rep = analyze(trace.load(need_trace(self, 1)), twin=I.idiom01())
        e = [f for f in rep.findings if f.cls == "1e" and f.level == "L2"]
        self.assertTrue(e, rep.classes())
        self.assertTrue(any("goes to 2" in d for f in e for d in f.detail), [f.detail for f in e])
        self.assertFalse([f for f in rep.findings if f.cls == "1d" and f.level == "L2"], "the twin does not deadlock")

    def test_06_collective_participation(self):
        rep = analyze(trace.load(need_trace(self, 6)), twin=I.idiom06())
        e = [f for f in rep.findings if f.cls == "1e" and f.level == "L2"]
        self.assertTrue(e, rep.classes())
        self.assertTrue(any("collective" in d for f in e for d in f.detail), [f.detail for f in e])


class TwoChanges(unittest.TestCase):
    def test_idioms_with_two_wildcard_ranks(self):
        # the master-worker idiom has one wildcard rank: no two-change alternative; a trace
        # with wildcards on two ranks has them and the checker judges each
        from rematch.matching import candidates_k
        t = trace.load(need_trace(self, 1))
        self.assertEqual(candidates_k(t, 2), [])


class TraceModel(unittest.TestCase):
    def test_09_fifo_reconstruction(self):
        t = trace.load(need_trace(self, 9))
        self.assertEqual(t.observed[(0, 0)], (1, 0))
        self.assertEqual(t.observed[(0, 2)], (1, 1))
        self.assertEqual(t.observed[(0, 1)], (2, 0))


class PollingIdiom(unittest.TestCase):
    """Idiom 11: a busy Iprobe loop gives a bounded trace and a pinnable probe alternative."""

    def test_11_bounded_trace_and_probe_alternative(self):
        d = need_trace(self, 11)
        lines = sum(1 for f in os.listdir(d) if f.startswith("rank-") for _ in open(os.path.join(d, f)))
        self.assertLess(lines, 100)
        rep = analyze(trace.load(d), with_sp=False)
        alts = rep.by_class("probe-alt")
        self.assertEqual(len(alts), 1)
        self.assertEqual(alts[0].pin, "REMATCH_PIN_PROBE=0:0:2")


if __name__ == "__main__":
    unittest.main()
