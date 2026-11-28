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
NUMBERS = os.path.join(ROOT, "experiments", "generated", "numbers.tex")


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


class MbiWithReexecution(unittest.TestCase):
    """run_mbi.py --rerun: level L2 by re-execution in the corpus harness, and its verdict."""

    def test_an_l2_abort_is_an_error_and_a_departure_is_not(self):
        rs = [{"name": "a", "expect": "ERROR", "classes": "1e 1f", "l2": "abort diverge"},
              {"name": "b", "expect": "OK", "classes": "1e 1f", "l2": "diverge"},
              {"name": "c", "expect": "ERROR", "classes": "1d", "l2": ""},
              {"name": "d", "expect": "OK", "classes": "", "l2": "abort", "analysis": "CRASH"}]
        self.assertEqual([score.verdict(r) for r in rs], ["OK", "OK", "ERROR", "CRASH"])
        self.assertEqual([score.l2_verdict(r) for r in rs], ["ERROR", "OK", "ERROR", "CRASH"])
        c = score.confusion(rs, score.l2_verdict)
        self.assertEqual((c["tp"], c["fp"], c["fn"], c["tn"], c["crash"]), (2, 0, 0, 1, 1))

    def test_the_summary_scores_the_same_run_without_l2(self):
        """An L2 deadlock is class 1d: without L2 the row keeps only the other levels' classes."""
        summ = module("experiments/mbi/l2_summary.py", "l2_summary_under_test")
        rs = [{"name": "a", "expect": "ERROR", "build": "ok", "classes": "1d 1f", "levels": "L1:1f L2:1d", "l2": "deadlock",
               "commit": "c"},
              {"name": "b", "expect": "ERROR", "build": "ok", "classes": "1d 1f", "levels": "L1+SP:1d L1:1f L2:1d",
               "l2": "deadlock", "commit": "c"},
              {"name": "c", "expect": "ERROR", "build": "ok", "classes": "1e 1f", "levels": "L1:1f L2:1e", "l2": "abort",
               "commit": "c"},
              {"name": "d", "expect": "OK", "build": "ok", "classes": "1f", "levels": "L1:1f", "l2": "", "commit": "c"}]
        self.assertEqual(summ.without_l2(rs[0])["classes"], "1f")
        s = summ.summary(rs)
        self.assertEqual((s["reported"], s["reported_l2"], s["false_alarms"], s["false_alarms_l2"]), (1, 3, 0, 0))
        self.assertEqual(s["only_l2"], "a c")

    def test_run_mbi_rerun_reports_the_payload_check(self):
        """The E7 program MessageRace_Recv_Send_nok (rank 0 aborts unless the last payload is
        3), traced in a run that finished, with Python ranks for the shim (test_rerun): L1 flags
        the race only, L2 by re-execution finds the abort; without --rerun the row has no l2."""
        import tempfile
        import types
        import test_rerun as R
        from stretto import rerun
        run_mbi = module("experiments/mbi/run_mbi.py", "run_mbi_rerun")
        run_mbi.trace = types.SimpleNamespace(load=lambda d: R.trace_mbi((1, 2, 3)))
        run_mbi.rerun_twin = lambda t, exe, np, args: rerun.Oracle(t, R.runner(R.PROGRAMS_MBI, np),
                                                                   max_runs=args.rerun_max_runs).program()
        self.assertIn("--rerun", run_mbi.parser().format_help())
        with tempfile.TemporaryDirectory() as d:
            row = {"name": "MessageRace_Recv_Send_nok", "np": 4, "expect": "ERROR"}
            run_mbi.analyse(row, d, os.path.join(d, "main"), d, run_mbi.options(rerun=True))
            self.assertEqual(row["analysis"], "ok")
            self.assertEqual(score.verdict(row), "OK")
            self.assertIn("abort", row["l2"].split())
            self.assertEqual(score.l2_verdict(row), "ERROR")
            self.assertGreater(row["rerun_runs"], 0)
            self.assertEqual(row["rerun_unreliable"], "")
            plain = {"name": "MessageRace_Recv_Send_nok", "np": 4, "expect": "ERROR"}
            run_mbi.analyse(plain, d, os.path.join(d, "main"), d, run_mbi.options())
            self.assertNotIn("l2", plain)
            self.assertEqual(plain["levels"], row["levels"].replace("L2:1e", "").strip())

    def test_run_mbi_rerun_needs_out(self):
        import tempfile
        run_mbi = module("experiments/mbi/run_mbi.py", "run_mbi_rerun_out")
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "MessageRace_x.c"), "w").close()
            run_mbi.CORPORA["mbi"] = d
            argv = sys.argv
            try:
                sys.argv = ["run_mbi.py", "--corpus", "mbi", "--rerun", "--work", d]
                self.assertEqual(run_mbi.main(), 2)
            finally:
                sys.argv = argv


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


class ReportOutput(unittest.TestCase):
    """The text report's status line and the JSON a program reads (README, "Exit status and output")."""

    def test_status_schema_and_ids(self):
        from stretto import report, trace
        t = trace.load(os.path.join(GOLDEN, "04_orphan_send"))
        rep = report.analyze(t)
        self.assertEqual(rep.status(), "complete")
        text = report.format_report(t, rep).splitlines()
        self.assertEqual(text[0], "status: complete")
        self.assertTrue(text[1].startswith("trace: "))                 # the rest as before
        j = report.to_json(t, rep)
        self.assertEqual((j["schema"], j["status"], j["degraded"]), (2, "complete", []))
        self.assertTrue(j["analysis_commit"])
        ids = [f["id"] for f in j["findings"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i.startswith(f["cls"] + "-") for i, f in zip(ids, j["findings"])))
        # stable: the same trace gives the same ids, and a title's wording does not enter them
        self.assertEqual(ids, [f["id"] for f in report.to_json(t, report.analyze(t))["findings"]])
        for f in rep.findings:
            if f.recv is not None:
                f.title = "reworded " + f.title
        self.assertEqual(ids, report.finding_ids(rep))

    def test_a_trace_outside_the_model_is_degraded(self):
        from stretto import report, trace as T
        t = T.Trace()
        T._build(t, {0: [{"op": "init", "np": 2}, {"op": "unmodelled", "call": "MPI_Win_allocate_shared"}, {"op": "finalize"}],
                     1: [{"op": "init", "np": 2}, {"op": "finalize"}]})
        rep = report.analyze(t)
        self.assertEqual(rep.status(), "degraded:unmodelled")
        self.assertTrue(report.format_report(t, rep).startswith("status: degraded:unmodelled; unmodelled: "))
        self.assertEqual(report.to_json(t, rep)["degraded"][0]["reason"], "unmodelled")
        t = T.Trace()
        T._build(t, {0: [{"op": "init", "np": 3}, {"op": "finalize"}], 1: [{"op": "init", "np": 3}, {"op": "finalize"}]})
        self.assertEqual(report.analyze(t).status(), "degraded:missing-ranks")


def bash(script: str) -> int:
    """Status of a bash script run with experiments/harness.sh sourced."""
    import subprocess
    return subprocess.run(["bash", "-c", f'R="{ROOT}"; source "$R/experiments/harness.sh"; {script}'],
                          capture_output=True, text=True).returncode


class HarnessErrors(unittest.TestCase):
    """A run the harness could not complete or judge is no verdict of the tool's."""

    def test_a_killed_launcher_is_a_hang_only_after_the_timeout(self):
        def out(script):
            import subprocess
            return subprocess.run(["bash", "-c", f'R="{ROOT}"; source "$R/experiments/harness.sh"; {script}'],
                                  capture_output=True, text=True).stdout.strip()
        self.assertEqual(out("run_result 0 3 60"), "FINISHED")
        self.assertEqual(out("run_result 124 60 60"), "TIMEOUT")
        self.assertEqual(out("run_result 137 65 60"), "TIMEOUT")       # timeout's -k kill of a launcher ignoring SIGTERM
        self.assertEqual(out("run_result 137 12 60"), "rc=137")        # a memory cap or a kill: not a hang
        self.assertEqual(out("run_result 15 3 60"), "rc=15")

    def test_must_without_a_run_has_no_verdict(self):
        rs = [{"name": "a", "expect": "ERROR", "must": "MUST:HARNESS-ERROR:docker exited with status 125"},
              {"name": "b", "expect": "ERROR", "must": "MUST:RE:abort"},
              {"name": "c", "expect": "OK", "must": "MUST:OK"}]
        self.assertEqual(score.must_verdict(rs[0]), "HARNESS-ERROR")
        self.assertEqual(score.must_verdict_hangs(rs[0]), "HARNESS-ERROR")
        c = score.confusion(rs, score.must_verdict_hangs)
        self.assertEqual((c["tp"], c["tn"], c["harness"]), (1, 1, 1))
        self.assertEqual(score.must_column_judge("must1112")({"must1112": rs[0]["must"]}), "HARNESS-ERROR")


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


class EagerTcp(unittest.TestCase):
    """The TCP setting of experiments/b_impl.py, which E2 and the stress baselines reuse, runs on
    the loopback unless STRETTO_TCP_IF names another interface: two nodes reach each other
    through none of the loopbacks (docs/cluster.md)."""

    def test_the_interface(self):
        sys.path.insert(0, os.path.join(ROOT, "experiments"))
        import b_impl
        old = os.environ.pop("STRETTO_TCP_IF", None)
        try:
            self.assertIn("btl_tcp_if_include lo", " ".join(b_impl.tcp(64)))
            os.environ["STRETTO_TCP_IF"] = "eth0"
            margs = " ".join(b_impl.tcp(64))
            self.assertIn("btl_tcp_if_include eth0", margs)
            self.assertIn("btl_tcp_eager_limit 64", margs)
            self.assertIn("btl_tcp_if_include ib0", " ".join(b_impl.tcp(64, "ib0")))
        finally:
            os.environ.pop("STRETTO_TCP_IF", None)
            if old is not None:
                os.environ["STRETTO_TCP_IF"] = old

    def test_the_launcher_and_compilers_of_a_job(self):
        """In a multi-node job the launcher is the job's (srun's front end) and the compiler
        runs inside the image."""
        keys = ("STRETTO_MPIRUN", "STRETTO_MPICC_MPICH")
        old = {k: os.environ.pop(k, None) for k in keys}
        try:
            m = module("experiments/b_impl.py", "b_impl_defaults")
            self.assertEqual((m.MPIRUN, m.MPICC["mpich"]), ("/usr/bin/mpirun.openmpi", ["/usr/bin/mpicc.mpich"]))
            os.environ.update({"STRETTO_MPIRUN": "/x/srun-mpirun", "STRETTO_MPICC_MPICH": "apptainer exec a.sif mpicc.mpich"})
            m = module("experiments/b_impl.py", "b_impl_job")
            self.assertEqual(m.MPIRUN, "/x/srun-mpirun")
            self.assertEqual(m.MPICC["mpich"], ["apptainer", "exec", "a.sif", "mpicc.mpich"])
            self.assertEqual(m.MPICC["openmpi"], ["/usr/bin/mpicc.openmpi"])
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


class ClusterJob(unittest.TestCase):
    """experiments/cluster/job.py: the job's nodes from what a scheduler writes, and the
    environment every launch of the job runs under; srun-mpirun, the launcher's front end."""

    def setUp(self):
        self.job = module("experiments/cluster/job.py", "cluster_job_under_test")

    def test_hosts_and_tasks(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for text in ("n1\nn1\nn1\nn2\nn2\nn2\n",          # one line per slot
                         "n1\nn2\n",                          # scontrol show hostnames
                         "n1 slots=4\n# a comment\nn2 slots=4\n"):   # an Open MPI hostfile
                p = os.path.join(d, "hosts")
                with open(p, "w") as fh:
                    fh.write(text)
                self.assertEqual(self.job.read_hosts(p), ["n1", "n2"])
        self.assertEqual([self.job.tasks_per_node(x) for x in ("8", "8(x2)", "8,4", "")], [8, 8, 8, 1])

    def test_the_environment(self):
        job = {"mpirun": self.job.SRUN_MPIRUN, "ppn": 8, "tmp": "/shared/out/tmp", "hostfile": "", "launcher": "srun",
               "pmi": "pmix", "node_exec": "apptainer exec --bind /scratch x.sif", "net": "ib0"}
        env = self.job.mpi_env(job)
        self.assertEqual(env["STRETTO_MPIRUN"], self.job.SRUN_MPIRUN)
        self.assertEqual((env["STRETTO_SRUN_MPI"], env["STRETTO_PPN"]), ("pmix", "8"))
        self.assertEqual(env["STRETTO_NODE_EXEC"], "apptainer exec --bind /scratch x.sif")
        self.assertEqual(env["STRETTO_MPICC_MPICH"], "apptainer exec --bind /scratch x.sif /usr/bin/mpicc.mpich")
        self.assertEqual(env["OMPI_MCA_rmaps_base_mapping_policy"], "node")
        self.assertEqual(self.job.mpi_env(job, "slot")["OMPI_MCA_rmaps_base_mapping_policy"], "slot")
        # in blocks: np / nodes consecutive ranks on each node; more ranks than cores bind to hardware threads
        block = self.job.mpi_env(dict(job, nodes=["n1", "n2"]), "block", 17, "hwthread")
        self.assertEqual(block["OMPI_MCA_rmaps_base_mapping_policy"], "ppr:9:node")
        self.assertEqual((block["OMPI_MCA_hwloc_base_binding_policy"], block["OMPI_MCA_hwloc_base_use_hwthreads_as_cpus"]),
                         ("hwthread", "1"))
        self.assertNotIn("OMPI_MCA_hwloc_base_use_hwthreads_as_cpus", env)
        # the harness's files on the shared file system, MPI's session directory node-local
        self.assertEqual((env["TMPDIR"], env["OMPI_MCA_orte_tmpdir_base"]), ("/shared/out/tmp", "/tmp"))
        self.assertEqual((env["OMPI_MCA_btl_tcp_if_include"], env["STRETTO_TCP_IF"], env["UCX_NET_DEVICES"]),
                         ("ib0", "ib0", "ib0"))
        self.assertNotIn("OMPI_MCA_orte_default_hostfile", env)
        native = self.job.mpi_env(dict(job, launcher="mpirun", mpirun="/opt/ompi/bin/mpirun", node_exec="", net=""))
        self.assertEqual(native["STRETTO_MPIRUN"], "/opt/ompi/bin/mpirun")
        self.assertNotIn("STRETTO_SRUN_MPI", native)
        self.assertNotIn("STRETTO_MPICC_OPENMPI", native)
        self.assertNotIn("OMPI_MCA_btl_tcp_if_include", native)
        # natively on a subnet: Open MPI takes the subnet, UCX the node's interface on it; the
        # compilers are the system's Open MPI and mpich.sh's MPICH
        subnet = self.job.mpi_env(dict(job, launcher="mpirun", mpirun="/usr/bin/mpirun", node_exec="",
                                       mpich_prefix="/opt/mpich", net="10.0.0.0/8", net_iface="eth1"))
        self.assertEqual((subnet["OMPI_MCA_btl_tcp_if_include"], subnet["OMPI_MCA_oob_tcp_if_include"],
                          subnet["UCX_NET_DEVICES"]), ("10.0.0.0/8", "10.0.0.0/8", "eth1"))
        self.assertEqual((subnet["STRETTO_MPICC_OPENMPI"], subnet["STRETTO_MPICC_MPICH"]),
                         ("/usr/bin/mpicc.openmpi", "/opt/mpich/bin/mpicc.mpich"))
        self.assertEqual(self.job.iface_of("ib0"), "ib0")
        self.assertEqual(self.job.iface_of("127.0.0.0/8"), "lo")

    def test_the_front_end_of_srun(self):
        """srun-mpirun turns the harness's mpirun lines into srun's: the ranks' variables go to
        env(1) in front of the program (inside the image's prefix), never into srun's own
        environment; the placement by node uses as many nodes as ranks."""
        import shutil
        import subprocess
        import tempfile
        if shutil.which("bash") is None:
            self.skipTest("no bash")
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "srun")
            with open(fake, "w") as fh:
                fh.write('#!/bin/sh\necho "LD_PRELOAD=${LD_PRELOAD:-}"\nprintf "%s\\n" "$@"\n')
            os.chmod(fake, 0o755)
            env = dict(os.environ, STRETTO_SRUN=fake, SLURM_JOB_NUM_NODES="2", STRETTO_PPN="8",
                       STRETTO_NODE_EXEC="apptainer exec x.sif", STRETTO_SRUN_MPI="pmix")
            env.pop("LD_PRELOAD", None)
            env.pop("OMPI_MCA_rmaps_base_mapping_policy", None)
            out = subprocess.run([os.path.join(ROOT, "experiments", "cluster", "srun-mpirun"), "-np", "3", "--map-by", "node",
                                  "-x", "LD_PRELOAD=/lib/t.so", "-x", "STRETTO_PIN=0:0:2,1:0:0", "--mca", "btl", "self,tcp",
                                  "./main", "a b"], env=env, capture_output=True, text=True, check=True).stdout.splitlines()
            self.assertEqual(out[0], "LD_PRELOAD=")
            self.assertEqual(out[1:], ["--mpi=pmix", "-n", "3", "-N", "2", "--distribution=cyclic", "--cpu-bind=cores",
                                       "--kill-on-bad-exit=1", "apptainer", "exec", "x.sif", "/usr/bin/env",
                                       "LD_PRELOAD=/lib/t.so", "STRETTO_PIN=0:0:2,1:0:0", "OMPI_MCA_btl=self,tcp",
                                       "./main", "a b"])
            out = subprocess.run([os.path.join(ROOT, "experiments", "cluster", "srun-mpirun"), "-np", "16", "./main"],
                                 env=env, capture_output=True, text=True, check=True).stdout.splitlines()
            self.assertEqual(out[4:8], ["-N", "2", "--distribution=block", "--cpu-bind=cores"])
            v = subprocess.run([os.path.join(ROOT, "experiments", "cluster", "srun-mpirun"), "--version"],
                               env=env, capture_output=True, text=True).stdout
            self.assertIn("Open MPI", v)        # stretto.confirm then passes variables as -x NAME=VALUE


if __name__ == "__main__":
    unittest.main()
