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


score = module("experiments/mbi/score.py", "score_under_test")
GOLDEN = os.path.join(ROOT, "analysis", "tests", "golden", "openmpi")


class CrashIsNoVerdict(unittest.TestCase):
    """An analysis that raises has no verdict: never a true negative on a correct program."""

    def test_a_crash_is_neither_a_positive_nor_a_negative(self):
        rs = [{"name": "a", "expect": "OK", "classes": "", "analysis": "CRASH"},
              {"name": "b", "expect": "ERROR", "classes": "1d", "analysis": "ok"},
              {"name": "c", "expect": "OK", "classes": ""},
              {"name": "d", "expect": "ERROR", "classes": "", "notes": "analysis failed: KeyError"}]
        for judge in (score.verdict, score.observed_verdict, score.predicted_verdict, score.l1_verdict):
            c = score.confusion(rs, judge)
            self.assertEqual((c["tn"] + c["tp"] + c["fp"] + c["fn"], c["crash"]), (2, 2), judge.__name__)
        c = score.confusion(rs, score.verdict)
        self.assertEqual((c["tp"], c["tn"], c["fp"], c["fn"]), (1, 1, 0, 0))
        self.assertIn("a", score.crashes_seen())
        self.assertIn("d", score.crashes_seen())

    def test_run_mbi_records_a_crash_as_crash(self):
        import shutil
        import tempfile
        run_mbi = module("experiments/mbi/run_mbi.py", "run_mbi_under_test")
        with tempfile.TemporaryDirectory() as d:
            td = os.path.join(d, "trace")
            os.makedirs(td)
            with open(os.path.join(td, "rank-0.jsonl"), "w") as fh:
                fh.write('{"r":0,"seq":0,"op":"init","np":1}\n{not json\n{"r":0,"seq":2,"op":"finalize"}\n')
            row = {"name": "broken", "np": 1}
            run_mbi.analyse(row, d, os.path.join(d, "main"), td, run_mbi.options())
            self.assertEqual(row["analysis"], "CRASH")
            self.assertEqual(row["classes"], "")
            self.assertTrue(row["notes"].startswith("analysis failed: ValueError"))
            self.assertEqual(score.verdict(row), "CRASH")
            self.assertTrue(os.path.exists(os.path.join(d, "analysis.err")))
            # a good trace, analysed with the options another script builds from run_mbi's defaults
            # (experiments/mpiprv/stretto_corpus.py once built them by hand, without --reads)
            shutil.copytree(os.path.join(GOLDEN, "04_orphan_send"), os.path.join(d, "good"))
            row = {"name": "good", "np": 3}
            run_mbi.analyse(row, d, os.path.join(d, "main"), os.path.join(d, "good"),
                            run_mbi.options(guard=True, confirm=False, sp_max_events=600, max_states=30000))
            self.assertEqual(row["analysis"], "ok")
            self.assertEqual(score.verdict(row), "ERROR")


class NinjaRerun(unittest.TestCase):
    """experiments/ninja/run.py appends to its tables; a campaign run again must not count twice."""

    def write(self, d, name, header, lines):
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            fh.write(header + "\n" + "".join(x + "\n" for x in lines))
        return path

    def test_the_last_campaign_counts(self):
        import tempfile
        tables = module("experiments/tables.py", "tables_under_test")
        h = "label,np,mode,setting,run,result,seconds,reached,value,commit"
        first = ["hypre,4,random,ratio=10% delay=50000us,1,FINISHED,1,,,a", "hypre,4,random,ratio=10% delay=50000us,2,TIMEOUT,60,,,a",
                 "hypre,4,app,learning run (system-centric),0,TIMEOUT,60,,,a", "hypre,4,app,learning run (system-centric),0,FINISHED,5,,,a",
                 "hypre,4,app,,1,FINISHED,5,,,a", "hypre,4,app,,2,FINISHED,5,,,a"]
        again = ["hypre,4,random,ratio=10% delay=50000us,1,TIMEOUT,60,,,b", "hypre,4,random,ratio=10% delay=50000us,2,TIMEOUT,60,,,b",
                 "hypre,4,app,learning run (system-centric),0,FINISHED,5,,,b", "hypre,4,app,,1,TIMEOUT,60,,,b",
                 "hypre,4,app,,2,FINISHED,5,,,b"]
        with tempfile.TemporaryDirectory() as d:
            once = tables.ninja_runs_rows(self.write(d, "once.csv", h, first))
            self.assertEqual(len(once), len(first))             # the learning run retried belongs to its campaign
            twice = tables.ninja_runs_rows(self.write(d, "twice.csv", h, first + again))
            self.assertEqual([r["commit"] for r in twice], ["b"] * len(again))
            hc = "program,expect,mode,setting,runs,first_failure,outcome,learn,seconds,commit"
            rs = tables.ninja_corpus_rows(self.write(d, "mbi.csv", hc, ["p,ERROR,random,r,3,,,,1,a", "q,OK,random,r,30,,,,1,a",
                                                                         "p,ERROR,random,r,1,1,TIMEOUT,,1,b"]))
            self.assertEqual(sorted((r["program"], r["commit"]) for r in rs), [("p", "b"), ("q", "a")])


def bash(script: str) -> int:
    """Status of a bash script run with experiments/harness.sh sourced."""
    import subprocess
    return subprocess.run(["bash", "-c", f'R="{ROOT}"; source "$R/experiments/harness.sh"; {script}'],
                          capture_output=True, text=True).returncode


class ShellHarness(unittest.TestCase):
    """The checks the shell drivers make before and after a run with a preloaded library."""

    def test_a_missing_library_fails(self):
        self.assertEqual(bash(f'need_file "{ROOT}/README.md"'), 0)
        self.assertNotEqual(bash(f'need_file "{ROOT}/README.md" /nonexistent/libstretto-pin.so'), 0)

    def test_a_run_without_the_banner_fails(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as fh:
            fh.write("[stretto-pin] rank 0: loaded (replay library)\nresult 1\n")
        try:
            self.assertEqual(bash(f'preload_ok "{fh.name}"'), 0)
            self.assertNotEqual(bash('preload_ok /nonexistent.log'), 0)
            self.assertNotEqual(bash('preload_ok_in "ERROR: ld.so: object libstretto-pin.so cannot be preloaded: ignored."'), 0)
            self.assertEqual(bash(f'preload_ok_in "$(cat {fh.name})"'), 0)
        finally:
            os.unlink(fh.name)


if __name__ == "__main__":
    unittest.main()
