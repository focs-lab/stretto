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


class Selection(unittest.TestCase):
    """--select means the same in every corpus script, and an empty or partial selection never
    replaces a canonical table."""

    def setUp(self):
        sys.path.insert(0, os.path.join(ROOT, "experiments"))
        import selection
        self.sel = selection

    def test_paths_and_table_names_select_alike(self):
        s = self.sel.selects
        for pat in ("^P2P/CallOrdering", "^P2P__CallOrdering", r"CallOrdering/x\.c$"):
            self.assertTrue(s(pat, "P2P__CallOrdering__x"), pat)
            self.assertTrue(s(pat, "P2P/CallOrdering/x.c"), pat)
        self.assertFalse(s("^COLL/", "P2P__CallOrdering__x"))
        self.assertTrue(s("MessageRace", "MessageRace_Loop_Isend_Recv_nok"))

    def test_the_mbb_stress_pattern_selects_the_same_programs_either_way(self):
        with open(os.path.join(ROOT, "experiments", "results", "mbb", "mbb.csv")) as fh:
            import csv
            names = [r["name"] for r in csv.DictReader(fh)]
        old = [n for n in names if self.sel.selects("^(P2P|COLL)__(CallOrdering|Correct)", n)]
        new = [n for n in names if self.sel.selects("^(P2P|COLL)/(CallOrdering|Correct)", n)]
        self.assertTrue(old)
        self.assertEqual(old, new)
        self.assertEqual(len([n for n in names if self.sel.selects(self.sel.DEFAULT_SELECT["mbb"], n)]), len(names))

    def run_main(self, mod, argv):
        saved = sys.argv
        sys.argv = argv
        try:
            return mod.main()
        except SystemExit as e:
            return e.code
        finally:
            sys.argv = saved

    def test_run_mbi_refuses_to_write_a_part_or_nothing(self):
        import tempfile
        run_mbi = module("experiments/mbi/run_mbi.py", "run_mbi_selection")
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "Only_one_ok.c"), "w").close()
            run_mbi.CORPORA["mbi"] = d
            self.assertEqual(self.run_main(run_mbi, ["run_mbi.py", "--corpus", "mbi", "--select", "Only", "--work", d]), 2)
            self.assertEqual(self.run_main(run_mbi, ["run_mbi.py", "--corpus", "mbi", "--limit", "1", "--work", d]), 2)
            out = os.path.join(d, "part.csv")
            self.assertEqual(self.run_main(run_mbi, ["run_mbi.py", "--corpus", "mbi", "--select", "Nothing", "--out", out, "--work", d]), 1)
            self.assertFalse(os.path.exists(out))
            run_mbi.CORPORA["mbi"] = os.path.join(d, "missing")
            self.assertEqual(self.run_main(run_mbi, ["run_mbi.py", "--corpus", "mbi", "--out", out, "--work", d]), 1)
            self.assertFalse(os.path.exists(out))

    def test_stress_refuses_an_empty_selection_and_missing_binaries(self):
        import tempfile
        stress = module("experiments/stress_baseline.py", "stress_under_test")
        with tempfile.TemporaryDirectory() as d:
            table = os.path.join(d, "mbi.csv")
            with open(table, "w") as fh:
                fh.write("name,expect,np,build\nA_nok,ERROR,2,ok\n")
            self.assertNotEqual(self.run_main(stress, ["stress_baseline.py", table, "--select", "B_"]), 0)
            self.assertNotEqual(self.run_main(stress, ["stress_baseline.py", table]), 0)        # A_nok/main is missing
            self.assertFalse(os.path.exists(os.path.join(d, "stress.csv")))


class Provenance(unittest.TestCase):
    """A row names the commit of the code that produced it, and says so when that code is not
    the commit (a copy without .git is checked against the files the deployment listed)."""

    def test_a_changed_copy_is_dirty(self):
        import tempfile
        prov = module("experiments/provenance.py", "provenance_under_test")
        saved = os.environ.pop("STRETTO_COMMIT", None)
        try:
            with tempfile.TemporaryDirectory() as d:
                os.makedirs(os.path.join(d, "analysis"))
                f = os.path.join(d, "analysis", "x.py")
                with open(f, "w") as fh:
                    fh.write("hello\n")
                self.assertEqual(prov.blob_hash(f), "ce013625030ba8dba906f756967f9e9ca394464a")   # git hash-object
                with open(os.path.join(d, "COMMIT"), "w") as fh:
                    fh.write("abc1234\n")
                prov.ROOT = d                      # no .git there: COMMIT, checked against COMMIT.files
                prov.code_commit.cache_clear()
                self.assertEqual(prov.code_commit(), "abc1234-unchecked")
                with open(os.path.join(d, "COMMIT.files"), "w") as fh:
                    fh.write(f"100644 blob {prov.blob_hash(f)}\tanalysis/x.py\n")
                prov.code_commit.cache_clear()
                self.assertEqual(prov.code_commit(), "abc1234")
                with open(f, "a") as fh:
                    fh.write("changed\n")
                prov.code_commit.cache_clear()
                self.assertEqual(prov.code_commit(), "abc1234-dirty")
                os.unlink(f)
                prov.code_commit.cache_clear()
                self.assertEqual(prov.code_commit(), "abc1234-dirty")
                os.environ["STRETTO_COMMIT"] = "def5678-dirty"   # what a driver found and passed on
                prov.code_commit.cache_clear()
                self.assertEqual(prov.code_commit(), "def5678-dirty")
        finally:
            os.environ.pop("STRETTO_COMMIT", None)
            if saved is not None:
                os.environ["STRETTO_COMMIT"] = saved

    def test_git_and_the_deployed_copy_agree(self):
        import shutil
        import subprocess
        import tempfile
        if shutil.which("git") is None:
            self.skipTest("no git")
        prov = module("experiments/provenance.py", "provenance_git")
        saved = os.environ.pop("STRETTO_COMMIT", None)
        try:
            with tempfile.TemporaryDirectory() as d:
                git = lambda *a: subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                                check=True, capture_output=True, text=True).stdout.strip()
                git("init", "-q")
                for rel in ("analysis/x.py", "experiments/results/y.csv"):
                    os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
                    with open(os.path.join(d, rel), "w") as fh:
                        fh.write("1\n")
                git("add", "-A")
                git("commit", "-q", "-m", "x")
                head = git("rev-parse", "--short", "HEAD")
                prov.ROOT = d
                prov.code_commit.cache_clear()
                self.assertEqual(prov.code_commit(), head)
                with open(os.path.join(d, "experiments", "results", "y.csv"), "a") as fh:
                    fh.write("a row a run wrote\n")
                prov.code_commit.cache_clear()
                self.assertEqual(prov.code_commit(), head)          # results are not code
                self.assertEqual(prov.write_commit(), head)
                shutil.rmtree(os.path.join(d, ".git"))              # the copy a deployment makes
                prov.code_commit.cache_clear()
                self.assertEqual(prov.code_commit(), head)
                with open(os.path.join(d, "analysis", "x.py"), "a") as fh:
                    fh.write("2\n")
                prov.code_commit.cache_clear()
                self.assertEqual(prov.code_commit(), head + "-dirty")
        finally:
            if saved is not None:
                os.environ["STRETTO_COMMIT"] = saved

    def test_tables_name_the_analysis_commit(self):
        tables = module("experiments/tables.py", "tables_commits")
        self.assertEqual(tables.commits([{"commit": "a"}, {"commit": "a"}]), "a (2)")      # older tables: as before
        self.assertEqual(tables.commits([{"commit": "a", "analysis_commit": "b"}, {"commit": "a", "analysis_commit": ""}]),
                         "a, analysed at b (1), a (1)")


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
