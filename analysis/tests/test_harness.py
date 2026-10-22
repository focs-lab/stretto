"""The experiment harness around the tool: the claims check (artifact/claims.py) and the
scripts that turn runs into table rows (experiments/). Each test runs the code on small inputs
it writes itself, or on the committed tables."""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))


def module(path: str, name: str):
    """Import a script of the repository by its path."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


claims = module("artifact/claims.py", "claims_under_test")
NUMBERS = os.path.join(ROOT, "paper", "generated", "numbers.tex")


class Claims(unittest.TestCase):
    """A claim holds only on numbers that exist and on something to check."""

    def setUp(self):
        self.n = claims.load(NUMBERS)

    def verdicts(self, n):
        return {text.split(" (numbers missing")[0].split(" (nothing to check")[0]: (text, holds)
                for _, text, holds in claims.claims(n)}

    def test_every_claim_holds_on_the_committed_numbers(self):
        bad = [text for _, text, holds in claims.claims(self.n) if not holds]
        self.assertEqual(bad, [])

    def test_a_missing_number_fails_the_claim(self):
        n = dict(self.n)
        del n["nEsixUnsound"]
        text, holds = self.verdicts(n)["no accepted alternative is unsound against the exhaustive explorer"]
        self.assertFalse(holds)
        self.assertIn("numbers missing: nEsixUnsound", text)

    def test_a_claim_about_nothing_fails(self):
        # the fixed HYPRE with no trace at all must not read as "no trace has a cross alternative"
        n = dict(self.n, nHypreFixedTraced="0")
        text, holds = self.verdicts(n)["HYPRE 2.11.0, the fix: no trace has a cross alternative"]
        self.assertFalse(holds)
        self.assertIn("nothing to check: nHypreFixedTraced = 0", text)
        n = dict(self.n)
        del n["nEsixPairs"]
        self.assertFalse(self.verdicts(n)["no accepted alternative is unsound against the exhaustive explorer"][1])

    def test_an_existence_claim_needs_no_count(self):
        n = dict(self.n, nPRVfpA="0")
        self.assertFalse(self.verdicts(n)["MPI-PRV's properties on MPI calls alone report correct programs"][1])

    def test_a_fixed_number_that_differs_fails(self):
        import io
        import tempfile
        from contextlib import redirect_stdout
        with open(NUMBERS) as fh:
            text = fh.read()
        text = text.replace(r"\newcommand{\nMBIprogs}{115\xspace}", r"\newcommand{\nMBIprogs}{114\xspace}")
        self.assertIn(r"\nMBIprogs}{114", text)
        with tempfile.NamedTemporaryFile("w", suffix=".tex", delete=False) as fh:
            fh.write(text)
        try:
            argv = sys.argv
            sys.argv = ["claims.py", "--rq", "2", "--against", fh.name]
            out = io.StringIO()
            with redirect_stdout(out):
                rc = claims.main()
            sys.argv = argv
            self.assertEqual(rc, 1)
            self.assertIn("1 number(s) the inputs fix differ", out.getvalue())
            sys.argv = ["claims.py", "--rq", "2", "--against", NUMBERS]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(claims.main(), 0)
        finally:
            sys.argv = argv
            os.unlink(fh.name)


if __name__ == "__main__":
    unittest.main()
