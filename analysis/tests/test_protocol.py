"""The uniform campaign's harness (experiments/protocol): the outcome categories of RULE.md
Section 3, the common columns, the timeout rule of Section 2.1, the plan of Sections 4-6, the
scheduler's placement on disjoint hardware threads and its resumption, and the runner's symptom
and MUST judgements on made-up runs."""
import csv
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
P = os.path.join(ROOT, "experiments", "protocol")
sys.path.insert(0, P)
sys.path.insert(0, os.path.join(ROOT, "experiments"))
import calibrate  # noqa: E402
import plan  # noqa: E402
import rows  # noqa: E402
import schedule  # noqa: E402


class Outcome(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(rows.outcome(False, True), "predicted")
        self.assertEqual(rows.outcome(True, True), "observed-reported")
        self.assertEqual(rows.outcome(True, False), "observed-not-reported")
        self.assertEqual(rows.outcome(False, False), "missed")
        # a tool failure decides before S and F
        self.assertEqual(rows.outcome(True, True, failed=True), "tool-failure")
        self.assertTrue(rows.revealed("predicted") and rows.revealed("observed-reported"))
        self.assertFalse(rows.revealed("observed-not-reported") or rows.revealed("missed") or rows.revealed("tool-failure"))

    def test_wilson(self):
        lo, hi = rows.wilson(0, 40)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.0876, places=3)          # RULE.md Section 3: 8.8%
        self.assertAlmostEqual(rows.wilson(0, 80)[1], 0.0458, places=3)
        self.assertAlmostEqual(rows.wilson(40, 40)[0], 0.912, places=3)
        lo, hi = rows.wilson(20, 40)
        self.assertAlmostEqual((hi - lo) / 2, 0.15, places=2)

    def test_cpuset(self):
        self.assertEqual(rows.cpuset([41, 0, 1, 2, 40]), "0-2,40-41")
        self.assertEqual(rows.parse_cpuset("0-2,40"), [0, 1, 2, 40])
        self.assertEqual(rows.parse_cpuset(rows.cpuset(range(18))), list(range(18)))

    def test_common_columns(self):
        c = rows.common(60, seed="104", jobs=3, cpus="0-3", commit="abc1234", category="missed")
        self.assertEqual(list(c), rows.COMMON)
        self.assertEqual((c["timeout"], c["seed"], c["jobs"], c["commit"], c["superseded"]), (60, "104", 3, "abc1234", ""))


class Tables(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "b.csv")

    def test_append_refuses_other_columns(self):
        rows.append(self.t, [{"a": 1, "b": 2}], ["a", "b"])
        rows.append(self.t, [{"a": 3, "b": 4}], ["a", "b"])
        self.assertEqual([r["a"] for r in rows.read(self.t)], ["1", "3"])
        with self.assertRaises(ValueError):
            rows.append(self.t, [{"a": 1}], ["a"])

    def test_supersede_keeps_rows(self):
        f = ["run", "result", "superseded"]
        rows.append(self.t, [{"run": i, "result": "x", "superseded": ""} for i in (1, 2, 3)], f)
        self.assertEqual(rows.supersede(self.t, lambda r: r["run"] in ("2", "3"), "disk full 24 Nov"), 2)
        self.assertEqual(rows.supersede(self.t, lambda r: r["run"] == "3", "again"), 0)   # the first reason stays
        got = rows.read(self.t)
        self.assertEqual(len(got), 3)
        self.assertEqual([r["superseded"] for r in got], ["", "disk full 24 Nov", "disk full 24 Nov"])
        self.assertEqual([r["run"] for r in rows.current(got)], ["1"])

    def test_annotate(self):
        src = os.path.join(self.d, "own.csv")
        rows.append(src, [{"program": "p", "found": "yes"}], ["program", "found"])
        n = rows.annotate(src, self.t, {"host": "h", "cpus": "0-1", "jobs": 1, "timeout": 60, "commit": "c"},
                          outcome_of=lambda r: rows.outcome(False, r["found"] == "yes"))
        self.assertEqual(n, 1)
        r = rows.read(self.t)[0]
        self.assertEqual((r["host"], r["timeout"], r["outcome"], r["superseded"]), ("h", "60", "predicted", ""))


class Adapters(unittest.TestCase):
    """The outcome of the rows of the scripts that keep their own tables."""

    def setUp(self):
        import adapters
        self.ad = adapters
        self.a = type("A", (), {"must_column": "must1112"})()

    def test_stress_and_series(self):
        self.assertEqual(self.ad.stress({"first_failure": "3"}, self.a), "observed-reported")
        self.assertEqual(self.ad.stress({"first_failure": ""}, self.a), "missed")
        self.assertEqual(self.ad.series({"result": "wrong"}, self.a), "observed-reported")
        self.assertEqual(self.ad.series({"outcome": "truncate"}, self.a), "observed-reported")     # Uintah
        self.assertEqual(self.ad.series({"result": "ok"}, self.a), "missed")
        self.assertEqual(self.ad.series({"result": "hang"}, self.a), "tool-failure")

    def test_must_and_traced(self):
        self.assertEqual(self.ad.must({"must1112": "MUST:ERROR:DEADLOCK"}, self.a), "predicted")
        self.assertEqual(self.ad.must({"must1112": "MUST:TIMEOUT"}, self.a), "observed-not-reported")
        self.assertEqual(self.ad.must({"must1112": "MUST:RE"}, self.a), "tool-failure")
        self.assertEqual(self.ad.traced({"result": "WRONG", "stretto_status": "complete", "in_permuteRows": "57"}, self.a),
                         "observed-reported")
        self.assertEqual(self.ad.traced({"result": "ok", "stretto_status": "complete", "l1_receive_findings": "2"}, self.a),
                         "predicted")
        self.assertEqual(self.ad.traced({"result": "ok", "stretto_status": "degraded: x"}, self.a), "tool-failure")


class External(unittest.TestCase):
    """The jobs the committed scripts run: their rows' outcomes and the scheduler's specs."""

    def setUp(self):
        import adapters
        import external
        self.ad, self.ex = adapters, external
        self.a = type("A", (), {"must_column": "must1112", "recorded_wrong": False})()
        self.ctx = {"exp_image": "e", "must_image": "m", "binding": external.BINDING, "machine": {}, "commit": "c",
                    "owner": "1:1", "select": ".", "limit": 4}

    def test_more_outcomes(self):
        self.assertEqual(self.ad.corrbench({"mode": "native", "result": "TIMEOUT"}, self.a), "observed-reported")
        self.assertEqual(self.ad.bench({"run": "1", "result": "FINISHED", "status": "complete", "errors": "2",
                                        "classes": "3-race@L1*2"}, self.a),
                         "predicted")
        self.assertEqual(self.ad.threadreplay({"verdict": "confirmed"}, self.a), "confirmed")
        self.assertEqual(self.ad.threadreplay({"diverged": "rank 0 chain 0"}, self.a), "inconclusive")   # replay_verdicts.py
        self.assertEqual(self.ad.mustvc({"tool": "mustvc", "outcome": "ok", "result": "FINISHED", "culprit": "1"}, self.a),
                         "predicted")
        self.assertEqual(self.ad.mustvc({"tool": "mustvc", "outcome": "hang", "result": "TIMEOUT", "culprit": ""}, self.a),
                         "tool-failure")
        self.assertEqual(self.ad.mpiprv({"outcome": "unsupported"}, self.a), "tool-failure")
        d = tempfile.mkdtemp()
        src, dst = os.path.join(d, "s.csv"), os.path.join(d, "d.csv")
        rows.append(src, [{"app": "x", "outcome": "unsupported"}], ["app", "outcome"])
        rows.annotate(src, dst, {}, outcome_of=lambda r: self.ad.mpiprv(r, self.a))
        r = rows.read(dst)[0]
        self.assertEqual((r["script_outcome"], r["outcome"]), ("unsupported", "tool-failure"))   # the script's own column kept

    def test_annotate_overrides_the_run(self):
        d = tempfile.mkdtemp()
        src, dst = os.path.join(d, "s.csv"), os.path.join(d, "d.csv")
        rows.append(src, [{"cell": "x", "run": "1", "result": "wrong"}], ["cell", "run", "result"])
        rows.annotate(src, dst, {"run": "7", "protocol_job": "k", "commit": "c"}, outcome_of=lambda r: "missed")
        r = rows.read(dst)[0]
        self.assertEqual((r["run"], r["protocol_job"], r["commit"], r["outcome"]), ("7", "k", "c", "missed"))
        self.assertEqual(rows.header_of(dst)[-len(rows.COMMON):], rows.COMMON)

    def test_suite_specs(self):
        j = {"subject": "mbi", "mpi": "openmpi", "tool": "delays", "setting": "D50", "run": 3, "config": "115 programs",
             "external": "suite", "block": "mbi/openmpi-delays-D50"}
        s = self.ex.spec(j, self.ctx)
        self.assertEqual(s["needs"], [("mbi/openmpi-build", "mbi|115 programs|-|build|corpus|0")])
        self.assertIn("--seed %d" % (self.ex.SUITE_SEED + 3), s["cmd"])
        self.assertEqual(s["kind"], "stress")
        s = self.ex.spec(dict(j, tool="stretto", setting="traced"), self.ctx)
        self.assertIn("run_mbi.py --corpus mbi", s["cmd"])
        self.assertEqual(s["needs"], [])
        s = self.ex.spec(dict(j, tool="must", setting="1.11.2"), self.ctx)
        self.assertEqual((s["image"], s["kind"]), ("m", "must"))

    def test_suite_calibration_specs(self):
        j = {"subject": "mbi", "mpi": "openmpi", "tool": "delays", "setting": "D50", "run": 1, "config": "115 programs",
             "external": "suite", "block": "mbi/openmpi-calibrate", "calibration": True}
        s = self.ex.spec(j, self.ctx)
        self.assertIn("--timeout %d" % self.ex.SUITE_CAL_CAP, s["cmd"])
        self.assertIn("--correct", s["cmd"])                                       # the programs labelled correct
        self.assertEqual(s["extra"][:2], ["--set", "calibrated=delays:D50"])
        self.assertIn("seed=%d" % (self.ex.SUITE_SEED + 1), s["extra"])          # the pass's seed recorded (amendment 22)
        s = self.ex.spec(dict(j, tool="stretto", setting="traced", block="mbi/openmpi-stretto-traced", calibration=False,
                              T=70), self.ctx)
        self.assertIn("--timeout 70", s["cmd"])                                    # a pass at the suite's T
        self.assertNotIn("--correct", s["cmd"])
        s = self.ex.spec(dict(j, subject="corrbench-openmp", tool="must", setting="1.11.2-tsan", calibration=False,
                              T=40), self.ctx)
        self.assertIn("--tsan", s["cmd"])
        self.assertEqual(s["annotate"], ["--suite", "corrbench-openmp", "--must-column", "must1112tsan"])
        r = {"must1112tsan": "MUST:ERROR:Data race", "expect": "ERROR"}
        a = type("A", (), {"must_column": "must1112tsan", "recorded_wrong": False})()
        self.assertEqual(self.ad.must(r, a), self.ad.must({"must1112": r["must1112tsan"], "expect": "ERROR"}, self.a))

    def test_external_jobs_in_the_plan(self):
        pl = list(csv.DictReader(io.StringIO(plan.render(plan.plan()))))
        jobs = schedule.jobs_of(pl, r"^corrbench/mpich-", 2)
        self.assertEqual(jobs[0]["tool"], "build")               # the build first
        self.assertEqual(jobs[0]["run"], 0)
        self.assertTrue(all(j["external"] == "suite" for j in jobs))
        self.assertEqual(sorted(set(j["run"] for j in jobs if j["tool"] == "must")), [1, 2])
        kinds = set(p["runner"] for p in pl)
        for k in ("external:suite", "external:thread", "external:mustvc", "external:locks", "external:mpiprv"):
            self.assertIn(k, kinds)


class Timeouts(unittest.TestCase):
    def test_suites(self):
        d = tempfile.mkdtemp()
        f = os.path.join(d, "corrbench", "openmpi-calibrate.csv")
        fields = ["mode", "run", "seconds", "calibrated", "mpi", "commit", "superseded"]
        rs = [{"mode": "native", "run": 0, "seconds": 9.0, "calibrated": "stretto:traced", "mpi": "openmpi"},   # untraced
              {"mode": "L1", "run": 1, "seconds": 2.5, "calibrated": "stretto:traced", "mpi": "openmpi"},
              {"mode": "", "run": "", "seconds": 4.2, "calibrated": "ninja:random", "mpi": "openmpi"},
              {"mode": "", "run": "", "seconds": 1.0, "calibrated": "ninja:app", "mpi": "openmpi"},
              {"mode": "", "run": "", "seconds": 300.0, "calibrated": "ninja:app", "mpi": "openmpi"},       # capped
              {"mode": "", "run": "", "seconds": 0.4, "calibrated": "delays:D50", "mpi": "openmpi"}]
        rows.append(f, [dict(r, commit="c", superseded="") for r in rs], fields)
        out = dict((r["tool"], r) for r in calibrate.derive_suites(d, "corrbench"))
        self.assertEqual((out["stretto"]["T"], out["stretto"]["runs"]), (30, 1))
        self.assertEqual((out["ninja"]["T"], out["ninja"]["capped"]), (42, 1))       # one T over the tool's settings
        self.assertEqual(out["delays"]["T"], 30)
        a = type("A", (), {"timeout": None, "results": d, "state": d, "pool": schedule.POOL})()
        sc = schedule.Scheduler(a, [])
        j = {"external": "suite", "subject": "corrbench", "tool": "ninja", "calibration": False}
        self.assertEqual(sc.timeout_for(j), 42)
        self.assertEqual(sc.timeout_for(dict(j, tool="must")), None)                 # not calibrated: no T
        self.assertEqual(sc.timeout_for(dict(j, calibration=True)), 300)


    def test_rule(self):
        self.assertEqual(calibrate.timeout_of([0.5, 1.2, 2.0]), (30, 0))              # the 30 s floor
        self.assertEqual(calibrate.timeout_of([3.0, 12.31]), (124, 0))                # 10 x the slowest, rounded up
        self.assertEqual(calibrate.timeout_of([5.0, 1800.0]), (50, 1))                # amendment 5: from those that ended
        self.assertEqual(calibrate.timeout_of([1800.0, 1805.0]), (1800, 2))           # none ended: the cap

    def test_derive(self):
        d = tempfile.mkdtemp()
        f = os.path.join(d, "s", "calibrate.csv")
        fields = ["subject", "config", "version", "tool", "setting", "run", "result", "seconds", "timeout", "commit",
                  "superseded", "symptom"]
        rs = [{"subject": "s", "config": "np4", "version": "fix", "tool": "plain", "setting": "-", "run": i,
               "result": "FINISHED", "seconds": 1.0 + i / 10, "timeout": 1800, "commit": "c", "superseded": "",
               "symptom": "False"} for i in range(1, 12)]
        rs[4]["superseded"] = "killed container"          # superseded rows do not count
        rs.append(dict(rs[0], tool="must", setting="1.11.2", seconds=4.0))     # one run only: left out
        rows.append(f, rs, fields)
        out, short = calibrate.derive(d, "s")
        self.assertEqual(len(out), 1)
        # the first 10 current runs: 1-4 and 6-11; the slowest 2.1 s -> T = 30
        self.assertEqual((out[0]["runs"], out[0]["slowest"], out[0]["T"]), (10, 2.1, 30))
        self.assertEqual(len(short), 1)
        t = schedule.load_timeouts(d, "s")
        self.assertEqual(t[("np4", "plain", "-")], 30)


class Plan(unittest.TestCase):
    def setUp(self):
        self.rows = plan.plan()

    def test_current(self):
        with open(os.path.join(P, "plan.csv")) as fh:
            self.assertEqual(fh.read(), plan.render(self.rows), "plan.csv is stale: run experiments/protocol/plan.py")

    def test_one_n(self):
        for r in self.rows:
            if r["runner"] == "run.py" and r["setting"] not in ("learn", "nonoise", "warmup"):
                self.assertEqual(r["n"], 40, r)
            if r["setting"] == "nonoise":
                self.assertEqual(r["n"], 10, r)
            if r["tool"] == "calibrate" and r["runner"] != "external:suite":
                self.assertEqual((r["n"], r["version"]), (10, "fix"), r)
            if r["tool"] == "calibrate" and r["runner"] == "external:suite":
                self.assertEqual((r["n"], r["block"]), (1, "%s/%s-calibrate" % (r["subject"], r["mpi"])), r)

    def test_suite_calibration_and_must_tsan(self):
        suite = [r for r in self.rows if r["runner"] == "external:suite"]
        tsan = set(r["subject"] for r in suite if r["setting"] == "1.11.2-tsan")
        self.assertEqual(tsan, {"mbi", "corrbench-openmp"})                        # amendment 16
        passes = set((r["subject"], r["mpi"], r["setting"]) for r in suite if r["tool"] not in ("build", "calibrate")
                     and not r["setting"].endswith("-budget") and r["setting"] != "L2-reexecution")
        cal = set((r["subject"], r["mpi"], r["setting"].split(":", 1)[1]) for r in suite if r["tool"] == "calibrate")
        self.assertEqual(passes, cal)                                              # every pass's setting calibrated

    def test_every_tool_on_every_configuration(self):
        apps = [r for r in self.rows if r["runner"] == "run.py" and r["setting"] not in ("learn", "nonoise", "serial", "warmup")]
        by = {}
        for r in apps:
            by.setdefault((r["subject"], r["config"]), set()).add((r["tool"], r["setting"], r["version"]))
        for k, got in by.items():
            for tool, setting, _ in plan.APP_SETTINGS:
                for v in ("bug", "fix"):
                    self.assertIn((tool, setting, v), got, k)

    def test_npbmg_32_alone_and_concurrency_check(self):
        mg = [r for r in self.rows if r["subject"] == "npbmg" and r["config"] == "np32" and r["runner"] == "run.py"]
        self.assertTrue(mg and all(r["alone"] == 1 for r in mg))
        serial = [r for r in self.rows if r["setting"] == "serial"]
        self.assertEqual(sorted(r["subject"] for r in serial), sorted(set(r["subject"] for r in serial)))
        self.assertTrue(all(r["alone"] == 1 and r["n"] == 40 and r["version"] == "bug" for r in serial))

    def test_hypre_slow_noise_is_its_own_row(self):
        slow = [r for r in self.rows if r["setting"] == "random-1.2s"]
        self.assertTrue(slow and all(r["subject"] == "hypre" and r["table"] == "appendix" for r in slow))


class Placement(unittest.TestCase):
    def test_pool_order_pairs_siblings(self):
        order = schedule.pool_order(rows.parse_cpuset(schedule.POOL))
        self.assertEqual(order[:4], [0, 40, 1, 41])
        self.assertEqual(len(order), 36)
        self.assertNotIn(18, order)
        self.assertNotIn(58, order)

    def test_disjoint_and_alone(self):
        p = schedule.Pool(rows.parse_cpuset(schedule.POOL))
        a, b = p.take(4), p.take(8)
        self.assertEqual(set(a) & set(b), set())
        self.assertIsNone(p.take(32, alone=True))          # an alone job waits for an idle machine
        self.assertIsNone(p.take(30))                       # 24 left
        p.give(a)
        p.give(b)
        self.assertTrue(p.idle())
        c = p.take(32, alone=True)
        self.assertEqual(len(c), 32)
        self.assertEqual(len(set(c)), 32)
        p.give(c)
        p.claim([0, 40])                                    # an earlier invocation's container
        self.assertNotIn(0, p.take(34))
        self.assertIsNone(p.take(1))

    def test_jobs_order_and_learning(self):
        text = plan.render(plan.plan())
        pl = list(csv.DictReader(io.StringIO(text)))
        jobs = schedule.jobs_of(pl, r"^cabana/(calibrate|ninja-learn|ninja-app|plain)$", 2)
        learn = [i for i, j in enumerate(jobs) if j["setting"] == "learn"]
        cal = [i for i, j in enumerate(jobs) if j["calibration"]]
        self.assertEqual(learn, list(range(len(learn))))                      # the learning runs first
        self.assertEqual(cal, list(range(len(learn), len(learn) + len(cal))))  # then the calibration
        self.assertTrue(all(jobs[i]["run"] == 0 for i in learn))
        first_other = len(learn) + len(cal)
        runs = [j["run"] for j in jobs if not j["calibration"] and j["setting"] != "learn"]
        self.assertEqual(runs, sorted(runs))                                  # run by run
        cal = [j for j in jobs if j["calibration"]]
        self.assertTrue(cal and all(j["version"] == "fix" for j in cal))
        self.assertIn(("plain", "-"), {(j["tool"], j["setting"]) for j in cal})


class AnalysisApart(unittest.TestCase):
    """Amendment 14: a traced run that runs alone, its analysis a one-thread job of its own."""

    def test_jobs(self):
        pl = list(csv.DictReader(io.StringIO(plan.render(plan.plan()))))
        jobs = schedule.jobs_of(pl, r"^npbmg/stretto-traced$", 2, configs="np32")
        alone = [j for j in jobs if j["alone"]]
        apart = [j for j in jobs if j["setting"] == schedule.ANALYSIS]
        self.assertEqual(len(alone), 4)
        self.assertEqual(len(apart), 4)
        self.assertTrue(all(j["defer"] for j in alone))
        self.assertTrue(all(j["cpus"] == 1 and not j["alone"] for j in apart))
        self.assertEqual(jobs[:4], alone)                       # the alone runs first, their analyses next
        jobs = schedule.jobs_of(pl, r"^npbmg/stretto-traced$", 2, configs="np16")
        self.assertFalse([j for j in jobs if j["setting"] == schedule.ANALYSIS or j.get("defer")])

    def test_deferred_row_and_its_analysis(self):
        d = tempfile.mkdtemp()
        a = type("A", (), {"timeout": None, "results": os.path.join(d, "r"), "state": os.path.join(d, "s"), "prefix": "p",
                           "repo": "/w", "commit": "c",
                           "pool": schedule.POOL})()
        pl = list(csv.DictReader(io.StringIO(plan.render(plan.plan()))))
        jobs = schedule.jobs_of(pl, r"^npbmg/stretto-traced$", 1, configs="np32")
        sc = schedule.Scheduler(a, jobs)
        t = [j for j in jobs if j.get("defer") and j["version"] == "bug"][0]
        an = [j for j in jobs if j["setting"] == schedule.ANALYSIS and j["version"] == "bug"][0]
        row = {"subject": "npbmg", "config": "np32", "version": "bug", "tool": "stretto", "setting": "traced", "run": 1,
               "result": "FINISHED", "symptom": True, "reported": False, "failure": "", "detail": '{"analysis": "deferred"}'}
        os.makedirs(os.path.dirname(sc.row_file(t)))
        with open(sc.row_file(t), "w") as fh:
            json.dump(row, fh)
        self.assertTrue(sc.collect(t, [0, 40], 1))              # kept for its analysis, not in the table
        self.assertFalse(os.path.exists(sc.table(t)))
        sc.done = sc.done_keys()
        self.assertIn((t["block"], schedule.key_of(t)), sc.done)
        self.assertTrue(sc.ready(an, []))
        self.assertIn("analyse-row", sc.command(an, [5], 2, 100))
        with open(sc.row_file(an), "w") as fh:
            json.dump(dict(row, cpus="0,40", detail='{"analysis": "apart"}', reported=True, outcome="predicted",
                           **dict((k, "") for k in rows.COMMON if k not in ("cpus", "outcome"))), fh)
        self.assertTrue(sc.collect(an, [5], 2))
        got = rows.read(sc.table(an))
        self.assertEqual((len(got), got[0]["setting"], got[0]["cpus"]), (1, "traced", "0,40"))   # the traced run's threads
        done = sc.done_keys()
        self.assertIn((an["block"], schedule.key_of(an)), done)
        self.assertIn((t["block"], schedule.key_of(t)), done)


class Symptoms(unittest.TestCase):
    def setUp(self):
        import run
        self.run = run

    def test_hang_and_wrong(self):
        hang = {"hang": True}
        wrong = {"wrong": True, "abort": r"integer set not found"}
        s = self.run.symptom_of
        self.assertEqual(s(hang, "FINISHED", True, ""), (False, ""))
        self.assertEqual(s(hang, "TIMEOUT", False, ""), (True, ""))
        self.assertEqual(s(wrong, "TIMEOUT", False, ""), (False, "timeout"))          # Section 2.1
        self.assertEqual(s(wrong, "FINISHED", False, ""), (True, ""))
        self.assertEqual(s(wrong, "APP-ERROR:1", False, "... integer set not found ..."), (True, ""))
        self.assertEqual(s(hang, "SIGNAL:6", True, "[  PASSED  ] 1 test."), (False, "SIGNAL:6"))   # NINJA's abort at exit

    def test_must_by_call_site(self):
        import json
        d = tempfile.mkdtemp()
        s = {"must_calls": r"MPI_(I)?[Pp]robe", "must_site": r"ParaSails"}
        msgs = {"messages": [{"type": "Error", "error_id": "MUST_ERROR_DEADLOCK", "from": {"call": "MPI_Probe"},
                              "references": [{"call": "MPI_Probe", "stacktrace": [{"function": "ReceiveRequest", "file": "ParaSails.c", "line": 100}]}]},
                             {"type": "Warning", "error_id": "MUST_WARNING_X", "from": {"call": "MPI_Recv"}}]}
        with open(os.path.join(d, "MUST_Output.json"), "w") as fh:
            json.dump(msgs, fh)
        hit, detail = self.run.must_report(s, d)
        self.assertTrue(hit)
        self.assertIn("MUST_WARNING_X(MPI_Recv)*1", detail)
        # a warning at the racing call and site reports nothing
        w = {"messages": [{"type": "Warning", "error_id": "MUST_WARNING_INTEGER_ZERO", "from": {"call": "MPI_Probe",
              "stacktrace": [{"function": "ReceiveRequest", "file": "ParaSails.c"}]}}]}
        d2 = tempfile.mkdtemp()
        with open(os.path.join(d2, "MUST_Output.json"), "w") as fh:
            json.dump(w, fh)
        self.assertFalse(self.run.must_report(s, d2)[0])
        self.assertIn("MUST_ERROR_DEADLOCK(MPI_Probe)*1", detail)
        s2 = dict(s, must_site=r"Elsewhere")
        self.assertFalse(self.run.must_report(s2, d)[0])          # the right call at another site is no report
        self.assertEqual(self.run.must_report(s, tempfile.mkdtemp()), (False, "no-report"))

    def test_must_ends(self):
        r = self.run.must_result
        self.assertEqual(r(0, 5, 60, "[MUST] Execution finished, inspect ..."), "FINISHED")
        self.assertEqual(r(0, 5, 60, "rank 0 (of 4), pid 2 caught signal nr 11\n[MUST] Execution finished"), "CRASHED")
        self.assertEqual(r(0, 5, 60, "rank 3 (of 4), pid 2 caught signal nr 15\n[MUST] Execution finished"), "FINISHED")
        self.assertEqual(r(1, 5, 60, "ERROR: MUST detected a deadlock, writing output."), "MUST-DEADLOCK")
        self.assertEqual(r(124, 60, 60, ""), "TIMEOUT")

    def test_lost_abort_message(self):
        self.assertEqual(self.run.abort_class("APP-ERROR:15", "openmpi"), "MPI-ERROR:MPI_ERR_TRUNCATE")
        self.assertEqual(self.run.abort_class("APP-ERROR:15", "mpich"), "APP-ERROR:15")
        self.assertEqual(self.run.abort_class("APP-ERROR:1", "openmpi"), "APP-ERROR:1")
        mfem = {"wrong": True, "abort": r"integer set not found|WRONG|MPI_ERR_TRUNCATE"}
        self.assertEqual(self.run.symptom_of(mfem, "MPI-ERROR:MPI_ERR_TRUNCATE", False, ""), (True, ""))

    def test_must_layout(self):
        self.assertEqual(self.run.must_layout(8)[0], ["--must:distributed", "--must:fanin", "4"])
        self.assertEqual(self.run.must_layout(32), ([], self.run.CENTRALISED))   # centralised: 43 > 36 threads, named

    def test_delays_are_the_committed_draws(self):
        d = self.run.delays(4, 1)
        import random
        r = random.Random(104)
        self.assertEqual(d["STRETTO_SEND_DELAY_MS"], ",".join("%d:%d" % (k, r.choice([0, 0, 0, 1, 5, 20, 50])) for k in range(4)))



class ThreadReplays(unittest.TestCase):
    """Amendment 17: the thread campaigns' replays over distinct alternatives (threadreplays.py)."""

    def setUp(self):
        import adapters
        import external
        self.ad, self.ex = adapters, external
        self.a = type("A", (), {"must_column": "must1112", "recorded_wrong": False})()
        self.ctx = {"exp_image": "e", "must_image": "m", "binding": external.BINDING, "machine": {}, "commit": "c",
                    "owner": "1:1", "select": ".", "limit": 4}
        self.rows = plan.plan()

    def test_thread_replays(self):
        rs = [r for r in self.rows if r["runner"] == "external:threadreplay"]
        self.assertEqual(set(r["subject"] for r in rs), {"slate", "parsec", "dlaf"})
        modes = dict((s, set(r["setting"] for r in rs if r["subject"] == s and r["setting"] not in
                             ("replay-trace", "replay-enumerate"))) for s in ("slate", "parsec", "dlaf"))
        self.assertEqual(modes["dlaf"], {"replay-after"})                          # its one mode
        self.assertEqual(len(modes["parsec"]), 4)
        self.assertTrue(all(r["n"] == plan.REPLAYS for r in rs if r["setting"].startswith("replay-")
                            and r["setting"] not in ("replay-trace", "replay-enumerate")))

    def test_thread_replay_specs(self):
        j = {"subject": "parsec", "tool": "stretto", "setting": "replay-before", "run": 7, "config": "the bug's replay cell",
             "external": "threadreplay", "block": "parsec/replay-before"}
        ctx = dict(self.ctx, machine={"PARSEC_BUILD": "/b", "PARSEC_LD_LIBRARY_PATH": "/w/tracer/bin"})
        s = self.ex.spec(j, ctx)
        self.assertIn("threadreplays.py replay --subject parsec --index 7 --mode before", s["cmd"])
        self.assertEqual((s["kind"], s["mounts"], s["env"]["LD_LIBRARY_PATH"]), ("threadreplay", [("/b", "/h", "ro")],
                                                                                 "/w/tracer/bin"))
        s = self.ex.spec(dict(j, setting="replay-trace", run=2), ctx)
        self.assertIn("threadreplays.py trace --subject parsec --run 2", s["cmd"])
        self.assertEqual(s["kind"], "none")
        # a replay's verdict knows its traced run went wrong from the row (SLATE's wrong factors)
        self.assertEqual(self.ad.threadreplay({"recorded": "wrong", "verdict": "confirmed"}, self.a), "confirmed")

    def test_results_and_jobs(self):
        import threadreplays as tr
        self.assertEqual(tr.result_of("slate", "rep 0 1000 2000 ok\n"), "ok")
        self.assertEqual(tr.result_of("slate", "rep 0 1000 2000 WRONG\n"), "wrong")
        self.assertEqual(tr.result_of("parsec", "summary reps 1 wrong 2\n"), "wrong")
        self.assertEqual(tr.result_of("dlaf", "MPI_ABORT\n"), "crash")
        self.assertEqual(tr.SPECS["parsec"]["env"]["STRETTO_ATOMICS"], "0")     # the atomics build, recording off
        pl = list(csv.DictReader(io.StringIO(plan.render(self.rows))))
        jobs = schedule.jobs_of(pl, r"^parsec/replay-(traces|alternatives|after)$", 2)
        got = [(j["setting"], j["run"]) for j in jobs]
        self.assertIn(("replay-enumerate", 0), got)
        self.assertEqual(sorted(r for s, r in got if s == "replay-trace"), [1, 2])
        self.assertEqual(sorted(r for s, r in got if s == "replay-after"), [1, 2])

def scheduler(d, jobs=()):
    a = type("A", (), {"timeout": None, "results": os.path.join(d, "r"), "state": os.path.join(d, "s"), "prefix": "p",
                       "repo": "/w", "commit": "c", "pool": schedule.POOL, "suite_select": ".", "suite_limit": 0,
                       "dry_run": False, "max_jobs": 0})()
    sc = schedule.Scheduler(a, list(jobs))
    sc._ctx = {"exp_image": "e", "must_image": "m", "binding": {}, "machine": {"SLATE_HOME": d}, "commit": "c",
               "owner": "1:1", "select": ".", "limit": 0}
    return sc


def plan_rows():
    return list(csv.DictReader(io.StringIO(plan.render(plan.plan()))))


REVEALED = ("predicted", "observed-reported")
# Stretto's reveals on MBI's and MPI-BugBench's committed corpus tables: (score.verdict's, as
# tab:rq2 counts them; under amendment 32)
STRETTO_SUITES = {("mbi", "ERROR"): (62, 61), ("mbi", "OK"): (0, 0), ("mbb", "ERROR"): (216, 212), ("mbb", "OK"): (4, 0)}


class Review(unittest.TestCase):
    """The amendments after the review of the harness (RULE.md Section 12)."""

    def setUp(self):
        import adapters
        self.ad = adapters
        self.a = type("A", (), {"must_column": "must1112", "recorded_wrong": False, "suite": "x"})()
        adapters._LABELS["x"] = {"dl": (True, False), "race": (False, True), "other": (False, False)}

    def test_enumeration_waits_for_every_counted_run(self):          # amendment 26
        d = tempfile.mkdtemp()
        jobs = schedule.jobs_of(plan_rows(), r"^npbmg/replay$", 2, configs="np16")
        e = [j for j in jobs if j["setting"] == "replay-enumerate"][0]
        self.assertEqual(e["counted"], 2)
        sc = scheduler(d, jobs)
        t = os.path.join(d, "r", "npbmg", "stretto-traced.csv")
        r = dict((k, "") for k in schedule.FIELDS)
        r.update(subject="npbmg", config="np16", version="bug", tool="stretto", setting="traced", run=1,
                 outcome="tool-failure", detail=json.dumps({"archive": "run01"}))
        rows.append(t, [r], schedule.FIELDS)
        self.assertFalse(sc.ready(e, []))                          # one of two, whatever its outcome
        rows.append(t, [dict(r, run=2, outcome="predicted", detail=json.dumps({"archive": "run02"}))], schedule.FIELDS)
        self.assertTrue(sc.ready(e, []))
        self.assertEqual(sc.trace_set(e), ["1:run01", "2:run02"])
        # an enumeration recorded over another set is superseded with its replays, and runs again
        rp = os.path.join(d, "r", "npbmg", "replay.csv")
        en = dict(r, setting="replay-enumerate", run=0, outcome="", detail=json.dumps({"trace_set": ["1:run01"]}))
        rows.append(rp, [en, dict(r, setting="replay", run=1, outcome="confirmed", detail="")], schedule.FIELDS)
        sc.done = sc.done_keys()
        self.assertIn((e["block"], schedule.key_of(e)), sc.done)
        sc.stale_enumerations()
        self.assertNotIn((e["block"], schedule.key_of(e)), sc.done)
        self.assertEqual(rows.current(rows.read(rp)), [])

    def test_enumeration_takes_the_counted_analyses(self):           # amendment 26
        import replays
        d = tempfile.mkdtemp()
        old = os.environ.get("PROTOCOL_STATE")
        self.addCleanup(lambda: os.environ.pop("PROTOCOL_STATE") if old is None else os.environ.update(PROTOCOL_STATE=old))
        os.environ["PROTOCOL_STATE"] = os.path.join(d, "s")
        t = os.path.join(d, "r", "npbmg", "stretto-traced.csv")
        r = dict((k, "") for k in schedule.FIELDS)
        r.update(subject="npbmg", config="np16", version="bug", tool="stretto", setting="traced")
        rows.append(t, [dict(r, run=1, outcome="tool-failure", detail=json.dumps({"archive": "run01"})),
                        dict(r, run=2, outcome="predicted", detail=json.dumps({"archive": "run02"}))], schedule.FIELDS)
        side = os.path.join(d, "s", "alternatives", "npbmg", "np16", "bug")
        os.makedirs(side)
        a = type("A", (), {"subject": "npbmg", "config": "np16", "results": os.path.join(d, "r"), "seed": "1",
                           "row": os.path.join(d, "row.json")})()
        with self.assertRaises(SystemExit):                        # a counted analysis without its sidecar
            replays.enumerate_(a)
        with open(os.path.join(side, "run02.json"), "w") as fh:
            json.dump({"findings": 1, "alternatives": [{"cls": "1f", "level": "L1", "pin": "", "pins": ["p"],
                                                         "control_pins": [], "required": None}]}, fh)
        replays.enumerate_(a)                                      # run 1, a tool failure, gives nothing and needs nothing
        got = rows.read(replays.alternatives_path(a.results, "npbmg", "np16"))
        self.assertEqual([x["run"] for x in got], ["run02"])
        with open(a.row) as fh:
            row = json.load(fh)
        self.assertEqual(json.loads(row["detail"])["trace_set"], ["1:run01", "2:run02"])
        self.assertEqual(row["timeout"], 0)

    def test_empty_trace_is_a_tool_failure(self):                     # amendment 25
        import run
        empty = tempfile.mkdtemp()
        os.environ.pop("KEEP_TRACES", None)
        for defer in (False, True):
            a = type("A", (), {"subject": "npbmg", "config": "np32", "version": "bug", "run": 1, "defer_analysis": defer,
                               "state": empty})()
            detail = {}
            got = run.traced(a, {}, "FINISHED", True, "", empty, detail)
            self.assertEqual(got, (False, "no trace"))             # struck run or not
            self.assertEqual(detail.get("analysis"), "deferred" if defer else None)
        d = tempfile.mkdtemp()
        row = {"subject": "npbmg", "config": "np32", "version": "bug", "run": 1, "symptom": True, "reported": False,
               "failure": "no trace", "detail": json.dumps({"analysis": "deferred", "trace": "empty"})}
        with open(os.path.join(d, "deferred.json"), "w") as fh:
            json.dump(row, fh)
        b = type("A", (), {"deferred": os.path.join(d, "deferred.json"), "row": os.path.join(d, "row.json"), "state": d})()
        run.analyse_row(b)                                         # the analysis job completes it: N stays 40
        with open(b.row) as fh:
            self.assertEqual(json.load(fh)["outcome"], "tool-failure")

    def test_analysis_memory(self):                                   # amendment 25
        d = tempfile.mkdtemp()
        jobs = schedule.jobs_of(plan_rows(), r"^npbmg/(plain|stretto-traced)$", 1, configs="np32")
        sc = scheduler(d, jobs)
        an = [x for x in jobs if x["setting"] == schedule.ANALYSIS][0]
        traced = [x for x in jobs if x.get("defer")][0]
        self.assertEqual(sc.memory(an), sc.memory(traced))         # as much as inside its traced run's job

    def test_stretto_cells_on_the_suites(self):                      # amendment 23
        r = {"mode": "native", "result": "FINISHED"}
        self.ad.corrbench(r, self.a)
        self.assertEqual(r["cell"], "plain")
        r = {"mode": "guard", "result": "FINISHED", "run": "1"}
        self.ad.corrbench(r, self.a)
        self.assertEqual(r["cell"], "stretto-guard")
        r = {"run": "0", "result": "FINISHED"}
        self.ad.bench(r, self.a)
        self.assertEqual(r["cell"], "plain")
        r = {"run": "1", "result": "FINISHED", "status": "complete", "errors": "0"}
        self.ad.bench(r, self.a)
        self.assertEqual(r["cell"], "stretto")

    def test_suite_timeouts_and_must(self):                           # amendments 22 and 28
        a = self.a
        # a timeout on an erroneous program is the symptom, whatever its class
        self.assertEqual(self.ad.stress({"program": "e", "expect": "ERROR", "first_failure": "1", "outcome": "TIMEOUT"}, a),
                         "observed-reported")
        # on a correct one as committed: the delays flag it (tables.py stress, any first_failure)
        self.assertEqual(self.ad.stress({"program": "c", "expect": "OK", "first_failure": "1", "outcome": "TIMEOUT"}, a),
                         "observed-reported")
        self.assertEqual(self.ad.must({"name": "e", "expect": "ERROR", "must1112": "MUST:TIMEOUT"}, a), "observed-not-reported")
        self.assertEqual(self.ad.must({"name": "c", "expect": "OK", "must1112": "MUST:TIMEOUT"}, a), "missed")  # MBI: no report
        cb = type("A", (), {"must_column": "must1112", "recorded_wrong": False, "suite": "corrbench"})()
        self.assertEqual(self.ad.must({"name": "c", "expect": "OK", "must1112": "MUST:TIMEOUT"}, cb), "tool-failure")  # no verdict
        race = "MUST:ERROR:data race (ThreadSanitizer report)"
        self.assertEqual(self.ad.must({"name": "race", "expect": "ERROR", "must1112": race}, a), "predicted")
        self.assertEqual(self.ad.must({"name": "dl", "expect": "ERROR", "must1112": race}, a), "missed")   # not a data race
        self.assertEqual(self.ad.must({"name": "dl", "expect": "ERROR", "must1112": "MUST:ERROR:Argument 2 is negative"}, a),
                         "predicted")
        self.assertEqual(self.ad.corpus({"name": "e", "expect": "ERROR", "run": "TIMEOUT"}, a), "observed-not-reported")
        self.assertEqual(self.ad.corrbench({"program": "e.c", "label": "erroneous", "expect": "ERROR", "mode": "native",
                                            "result": "TIMEOUT"}, a), "observed-reported")

    def test_committed_suite_reveals(self):                           # amendment 28 against tab:rq2's tables
        import score
        res = os.path.join(ROOT, "experiments", "results")
        for suite in ("mbi", "mbb"):
            a = type("A", (), {"must_column": "must1112", "recorded_wrong": False, "suite": suite})()
            corpus = dict((r["name"], r) for r in score.compiled(rows.read(os.path.join(res, suite, suite + ".csv"))))
            st = [r for r in rows.read(os.path.join(res, suite, "stress.csv")) if r["program"] in corpus]
            for expect in ("ERROR", "OK"):
                committed = sum(1 for r in st if r["expect"] == expect and r["first_failure"])     # tables.py stress
                got = sum(1 for r in st if r["expect"] == expect and self.ad.stress(dict(r), a) in REVEALED)
                self.assertEqual(got, committed, (suite, "delays", expect))
                mine = [r for r in corpus.values() if r["expect"] == expect]
                committed = sum(1 for r in mine if score.must_verdict({"must": r["must1112"]}) == "ERROR")
                got = sum(1 for r in mine if self.ad.must(dict(r), a) in REVEALED)
                self.assertEqual(got, committed, (suite, "MUST", expect))
                # Stretto under amendment 32: score.verdict counts every error class, observed ones
                # included; a run whose error findings only record its own failure is observed-not-reported
                got = [self.ad.corpus(dict(r), a) for r in mine]
                committed = sum(1 for r in mine if score.verdict(r) == "ERROR")
                self.assertEqual((committed, sum(1 for g in got if g in REVEALED)), STRETTO_SUITES[(suite, expect)],
                                 (suite, "Stretto", expect))
            self.assertFalse([r for r in st if self.ad.stress(dict(r), a) == "tool-failure"])
        # CorrBench's labels the same way: its committed tables' flags (experiments/corrbench/summary.py)
        import importlib.util
        spec = importlib.util.spec_from_file_location("corrbench_summary", os.path.join(ROOT, "experiments", "corrbench",
                                                                                         "summary.py"))
        cbs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cbs)
        a = type("A", (), {"must_column": "must", "recorded_wrong": False, "suite": "corrbench"})()
        cb = os.path.join(ROOT, "experiments", "corrbench")
        for label in ("erroneous", "correct"):
            ms = [r for r in rows.read(os.path.join(cb, "must.csv")) if r["label"] == label]
            committed = sum(1 for r in ms if score.must_kind({"must": r["must"]}) == "ERROR")
            self.assertEqual(sum(1 for r in ms if self.ad.must(dict(r), a) in REVEALED), committed, ("CorrBench MUST", label))
        for mpi, label, committed, revealed in (("openmpi", "erroneous", 693, 96), ("openmpi", "correct", 144, 24),
                                                ("mpich", "erroneous", 639, 96), ("mpich", "correct", 6, 3)):
            # amendment 32: the committed flag and its "failed run only" (summary.py) against F, which
            # leaves out an observed abort alone and observed findings alone on a run that ended in its
            # own error, and keeps an observed deadlock of a hung run
            ss = [r for r in rows.read(os.path.join(cb, "stretto-%s.csv" % mpi)) if r["label"] == label and r["mode"] == "trace"]
            flags = [cbs.stretto_flag(r, False) for r in ss]
            got = [self.ad.corrbench(dict(r), a) for r in ss]
            self.assertEqual(flags.count(True) + flags.count("abort"), committed, (mpi, label))
            self.assertEqual(sum(1 for g in got if g in REVEALED), revealed, ("CorrBench Stretto", mpi, label))
            self.assertFalse([1 for f, g in zip(flags, got) if f is True and g not in REVEALED])
            # with the committed summary's replacements (-again, -recheck: rqfreeb's false alarm fixed in
            # 385f919) the correct runs left are the one-sided programs that hang natively under Open MPI
            if label == "correct":
                ss = [r for r in ss if r["program"] != "correct/pt2pt/rqfreeb.c"]
                left = sorted(set(r["program"] for r in ss if self.ad.corrbench(dict(r), a) in REVEALED))
                self.assertEqual(len(left), 7 if mpi == "openmpi" else 0)
                self.assertTrue(all(p.startswith("correct/rma/") for p in left))
        # the counts the review named: 53 of MBI's erroneous programs revealed by the delays, 172 of
        # MPI-BugBench's (of them 49 and 168 by a timeout)
        mbi = [r for r in rows.read(os.path.join(res, "mbi", "stress.csv")) if r["expect"] == "ERROR" and r["first_failure"]]
        self.assertEqual((len(mbi), sum(r["outcome"] == "TIMEOUT" for r in mbi)), (53, 49))

    def test_uintah_plain_build(self):                                # amendment 27
        self.assertEqual(self.ad.series({"build": "plain", "outcome": "fail"}, self.a), "observed-reported")
        self.assertEqual(self.ad.series({"build": "plain", "outcome": "hang"}, self.a), "observed-reported")
        self.assertEqual(self.ad.series({"build": "fix", "outcome": "hang"}, self.a), "tool-failure")
        self.assertEqual(self.ad.series({"build": "fix", "outcome": "pass"}, self.a), "missed")

    def test_alternative_verdict(self):                               # amendment 24
        import threadreplays as tr
        self.assertEqual(tr.alternative_verdict(["inconclusive", "confirmed"]), ("confirmed", "1/2"))
        self.assertEqual(tr.alternative_verdict(["realised", "inconclusive"]), ("realised", "0/2"))
        self.assertEqual(tr.alternative_verdict(["inconclusive", "inconclusive"]), ("inconclusive", "0/2"))

    def test_suite_tables(self):                                      # amendment 21
        t = dict(((r["block"]), r["table"]) for r in plan_rows())
        self.assertEqual(t["corrbench-openmp/openmpi-must-1.11.2-tsan"], "main")
        self.assertEqual(t["corrbench-openmp/openmpi-must-1.11.2"], "appendix")
        self.assertEqual(t["mbi/openmpi-must-1.11.2"], "main")
        self.assertEqual(t["mbi/openmpi-must-1.11.2-tsan"], "appendix")
        self.assertEqual(t["mbb/openmpi-ninja-system-budget"], "appendix")
        self.assertEqual(t["mbb/openmpi-ninja-random-budget"], "main")
        self.assertIn("corrbench-openmp/mpich-stretto-traced", t)                          # attempted under MPICH

    def test_thread_timeouts(self):                                   # amendment 20
        import external
        pl = plan_rows()
        blocks = set(r["block"] for r in pl)
        for b in ("slate/calibrate-series-openmpi", "slate/plain-serial", "uintah/plain-serial", "mustvc/calibrate-slate"):
            self.assertIn(b, blocks)
        serial = [r for r in pl if r["block"] == "slate/plain-serial"][0]
        self.assertEqual((serial["alone"], serial["n"]), ("1", "40"))
        d = tempfile.mkdtemp()
        cal = os.path.join(d, "r", "slate", "calibrate-series-openmpi.csv")
        rows.append(cal, [{"name": c, "variant": "fixed", "run": k, "seconds": 2.5 + k, "rc": 0,
                           "calibrated": "slate/series-openmpi", "commit": "c", "superseded": ""}
                          for c in external.THREAD_CELLS["slate/series"] for k in range(1, 11)],
                    ["name", "variant", "run", "seconds", "rc", "calibrated", "commit", "superseded"])
        out = calibrate.derive_threads(os.path.join(d, "r"))
        self.assertEqual(set(r["T"] for r in out), {125})          # 10 x 12.5 s
        jobs = schedule.jobs_of(pl, r"^slate/(series-openmpi|plain-serial|calibrate-series-openmpi)$", 1)
        sc = scheduler(d, jobs)
        series = [j for j in jobs if j["block"] == "slate/series-openmpi"][0]
        t = sc.timeout_for(series)
        self.assertEqual(sorted(t), sorted(external.THREAD_CELLS["slate/series"]))
        ser = [j for j in jobs if j["block"] == "slate/plain-serial"][0]
        self.assertEqual(sc.timeout_for(ser), {"wide-2x2": 125})
        ctx = {"exp_image": "e", "must_image": "m", "binding": external.BINDING, "machine": {"SLATE_HOME": "/h"},
               "commit": "c", "owner": "1:1", "select": ".", "limit": 4}
        s = external.spec(dict(ser, T=sc.timeout_for(ser)), ctx)
        self.assertEqual((s["env"]["ONLY"], s["env"]["VARIANTS"], s["env"]["TIMEOUTS"]), ("wide-2x2", "plain", "wide-2x2=125"))
        c = [j for j in jobs if j["calibration"]][0]
        self.assertEqual(sc.timeout_for(c), calibrate.CALIBRATION_CAP)
        s = external.spec(dict(c, T=sc.timeout_for(c)), ctx)
        self.assertEqual((s["env"]["CAP"], s["env"]["VARIANTS"], s["kind"]), ("1800", "fixed", "none"))
        self.assertIn("calibrated=slate/series-openmpi", s["extra"])
        cmd = sc.command(series, [0], 1, t)
        self.assertIn("--cell-timeouts", cmd[-1])                  # each row its cell's T, never "script"
        self.assertNotIn("timeout=script", cmd[-1])
        # a cell without its T blocks the job
        os.remove(os.path.join(d, "r", "thread-timeouts.csv"))
        self.assertIsNone(scheduler(d, jobs).timeout_for(series))
        # rows get their cell's T
        src, dst = os.path.join(d, "src.csv"), os.path.join(d, "dst.csv")
        rows.append(src, [{"cell": "wide-2x2", "variant": "plain", "result": "ok"},
                          {"mpi": "openmpi", "build": "plain", "np": "4", "args": "-nthreads 2", "outcome": "pass"}],
                    ["cell", "variant", "result", "mpi", "build", "np", "args", "outcome"])
        self.ad.main(["annotate", "--kind", "series", src, dst, "--cell-timeouts", "wide-2x2=125 np4-nthreads2=40",
                      "--set", "commit=c"])
        self.assertEqual([r["timeout"] for r in rows.read(dst)], ["125", "40"])


class Fixes(unittest.TestCase):
    """The amendments after the review of the fixes (RULE.md Section 13)."""

    def test_which_kill_is_a_tool_failure(self):                      # amendment 33
        import run
        d = tempfile.mkdtemp()
        ev = os.path.join(d, "memory.events")
        with open(ev, "w") as fh:
            fh.write("low 0\nhigh 0\nmax 3\noom 2\noom_kill 5\n")
        self.assertEqual(run.oom_kills(ev), 2)                          # the container's own limit: oom, not oom_kill
        jobs = schedule.jobs_of(plan_rows(), r"^npbmg/(delays-D50|replay|calibrate)$", 1, configs="np16")
        sc = scheduler(d, jobs)
        sc.a.timeout = 99
        sc.done = set()

        class P:
            def __init__(self, rc):
                self.returncode = rc

            def poll(self):
                return self.returncode
        delays = [j for j in jobs if j["tool"] == "delays" and j["version"] == "bug"][0]
        log = os.path.join(sc.a.state, "rows", schedule.safe(delays["block"]), schedule.safe(schedule.key_of(delays)) + ".log")
        os.makedirs(os.path.dirname(log))

        def ends(rc, oom, text):
            with open(log, "w") as fh:
                fh.write(text)
            sc.ended = lambda name: (rc, oom)
            sc.running = {"k": (delays, P(rc), [0], 1)}
            todo = []
            sc.reap(todo)
            return todo
        # Docker's OOMKilled alone is a harness failure: run again, listed in reruns.tsv, no row
        self.assertEqual(ends(137, True, ""), [delays])
        with open(os.path.join(sc.a.results, "reruns.tsv")) as fh:
            self.assertIn("OOMKilled", fh.read())
        self.assertFalse(os.path.exists(sc.table(delays)))
        # run.py's own judgement of a kill that is not the container's limit: run again
        sc.reruns = {}
        self.assertEqual(ends(1, False, "SystemExit: HARNESS-RERUN: the run was killed"), [delays])
        # a harness error of its own (an exception): not run again, its block stops
        sc.reruns = {}
        self.assertEqual(ends(1, False, "Traceback (most recent call last):\n  KeyError: 'x'\n"), [])
        self.assertIn(delays["block"], sc.broken)
        # a replaced row stays in its table, superseded
        sc.broken = set()
        r = dict((c, "") for c in schedule.FIELDS)
        r.update(subject="npbmg", config="np16", version="bug", tool="delays", setting="D50", run=1, outcome="missed")
        rows.append(sc.table(delays), [r], schedule.FIELDS)
        self.assertTrue(sc.rerun(delays, "a test"))
        got = rows.read(sc.table(delays))
        self.assertEqual((len(got), got[0]["superseded"]), (1, "rerun: a test"))
        self.assertFalse([f for f in os.listdir(os.path.dirname(sc.row_file(delays))) if f.endswith(".superseded")])
        # a job keeps being rerun at most MAX_RERUNS times
        sc.reruns = {}
        for _ in range(schedule.MAX_RERUNS + 1):
            ends(137, False, "")
        self.assertIn(delays["block"], sc.broken)
        # a killed calibration run does not break derive
        cal = [j for j in jobs if j["calibration"]]
        res = os.path.join(d, "r")
        rs = []
        for k, j in enumerate(cal[:1] * 10, 1):
            r = dict((c, "") for c in schedule.FIELDS)
            r.update(subject="npbmg", config="np16", version="fix", tool=j["tool"], setting=j["setting"], run=k,
                     result="FINISHED", seconds=2.0, timeout=1800)
            rs.append(r)
        rs[3].update(result="KILLED", seconds="")
        rows.append(os.path.join(res, "npbmg", "calibrate.csv"), rs, schedule.FIELDS)
        out, short = calibrate.derive(res, "npbmg")
        self.assertEqual((out[0]["T"], out[0]["capped"]), (30, 1))      # left out of T, as a capped run

    def test_uintah_cells(self):                                     # amendment 30
        import external
        committed = set()
        for r in rows.read(os.path.join(ROOT, "experiments", "uintah", "series.csv")):
            committed.add("np%s%s" % (r["np"], "" if r["args"] == "mpi-scheduler" else r["args"].replace(" ", "")))
        self.assertEqual(set(external.THREAD_CELLS["uintah/series"]), committed)
        settings = set("np%s%s" % (x.split(" ", 1)[0], x.split(" ", 1)[1].replace(" ", "") if " " in x else "")
                       for x in external.UINTAH_SETTINGS.split(";"))
        self.assertEqual(settings, committed)

    def test_must_waits_for_its_warmup(self):                         # amendment 31
        d = tempfile.mkdtemp()
        jobs = schedule.jobs_of(plan_rows(), r"^npbmg/(calibrate|must-1.11.2|must-warmup)$", 1, configs="np16",
                                tools="must")
        sc = scheduler(d, jobs)
        sc.done = set()
        cal = [j for j in jobs if j["calibration"] and j["tool"] == "must"][0]
        run = [j for j in jobs if not j["calibration"] and j["setting"] == "1.11.2"][0]
        warm = [j for j in jobs if j["setting"] == "warmup"][0]
        self.assertFalse(sc.ready(cal, []))
        self.assertFalse(sc.ready(run, []))
        self.assertTrue(sc.ready(warm, []))
        sc.done.add((warm["block"], schedule.key_of(warm)))
        self.assertTrue(sc.ready(cal, []) and sc.ready(run, []))

    def test_unselected_adopted_alone_job_blocks(self):               # review item 12
        d = tempfile.mkdtemp()
        jobs = schedule.jobs_of(plan_rows(), r"^hypre/plain$", 1, configs="np4")
        sc = scheduler(d, jobs)
        sc.a.timeout = 99
        sc.adopted = {"p-other": (None, [0, 40], True)}                 # another selection's alone job, by its label
        cmd = sc.command(jobs[0], [1], 1, 99)
        self.assertIn("protocol.alone=0", cmd)
        self.assertTrue(sc.blocked_by_alone(jobs[0]))                   # nothing starts beside it
        sc.adopted = {"p-other": (None, [0, 40], False)}
        self.assertFalse(sc.blocked_by_alone(jobs[0]))
        self.assertTrue(sc.blocked_by_alone(dict(jobs[0], alone=True)))  # an alone job waits for an idle machine

    def test_warmup_checks_mustrun_processes(self):                   # amendment 1
        import run
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "must_temp", "src"))
        with open(os.path.join(d, "must_temp", "src", "layout.xml"), "w") as fh:
            fh.write('<levels>\n<level order="0" size="32" place-name="mpi_app">\n<level order="1" size="1" '
                     'place-name="mpi_place">\n</levels>\n')
        self.assertEqual(run.must_procs(d), 33)
        run.check_must_procs(d, "0-16,40-55")                           # 33 threads
        with self.assertRaises(SystemExit):
            run.check_must_procs(d, "0-15,40-55")


class Passes(unittest.TestCase):
    """A suite pass's table counts only when its script ended well and has every program."""

    def test_partial_table_is_no_row(self):
        import external
        d = tempfile.mkdtemp()
        jobs = schedule.jobs_of(plan_rows(), r"^mbi/openmpi-delays-D50$", 1)
        sc = scheduler(d, jobs)
        cmd = sc.command(jobs[0], [0], 1, 60)
        script = cmd[-1]
        # annotated (a row) only when the script's status is 0; its old table removed first
        self.assertIn("rc=$?; if [ $rc -eq 0 ] && [ -s ", script)
        self.assertLess(script.index("rm -f "), script.index("( "))
        # the pass's cmd ends with the check of its programs, against the build for a tool that runs it
        sp = external.spec(dict(jobs[0], T=60), sc.ctx())
        self.assertIn("suites.py check --suite mbi", sp["cmd"])
        self.assertIn("--built", sp["cmd"])
        st = external.spec(dict(jobs[0], tool="stretto", setting="traced", T=60), sc.ctx())
        self.assertIn("suites.py check --suite mbi", st["cmd"])
        self.assertNotIn("--built", st["cmd"])
        cal = external.spec(dict(jobs[0], calibration=True), sc.ctx())
        self.assertIn("--correct", cal["cmd"].split("suites.py check")[1])

    def test_missing_programs(self):
        import suites
        rows_ = [{"program": "a", "outcome": ""}, {"name": "b"}, {"program": "coll/c.c"}]
        self.assertEqual(suites.missing(["a", "b", "coll__c", "d"], rows_), ["d"])
        self.assertEqual(suites.missing(["a"], rows_), [])

    def test_no_row_after_a_dead_script(self):
        # a script that died with a traceback stops its block; one that was killed runs again
        d = tempfile.mkdtemp()
        jobs = schedule.jobs_of(plan_rows(), r"^mbi/openmpi-delays-D50$", 1)
        sc = scheduler(d, jobs)
        sc.a.timeout = 60
        sc.done = set()
        j = jobs[0]
        log = os.path.join(sc.a.state, "rows", schedule.safe(j["block"]), schedule.safe(schedule.key_of(j)) + ".log")
        os.makedirs(os.path.dirname(log))

        class P:
            def __init__(self, rc):
                self.returncode = rc

            def poll(self):
                return self.returncode
        for rc, text, again in ((3, "HARNESS-ERROR: stress.csv has 3 of the pass's 4 programs", False),
                                (1, "Traceback (most recent call last):\n", False), (137, "", True)):
            with open(log, "w") as fh:
                fh.write(text)
            sc.ended = lambda name, rc=rc: (rc, False)
            sc.running = {"k": (j, P(rc), [0], 1)}
            sc.broken, sc.reruns, todo = set(), {}, []
            sc.reap(todo)
            self.assertEqual((todo == [j], j["block"] in sc.broken), (again, not again), (rc, text))
            self.assertFalse(os.path.exists(sc.table(j)))


class CannotRun(unittest.TestCase):
    """Amendment 34: a cell whose fixed version never ends in calibration cannot run."""

    def calibration(self, d, seconds):
        r0 = dict((c, "") for c in schedule.FIELDS)
        rs = [dict(r0, subject="npbmg", config="np16", version="fix", tool="plain", setting="-", run=k, timeout=1800,
                   result="TIMEOUT" if s >= 1800 else "FINISHED", seconds=s, commit="c") for k, s in enumerate(seconds, 1)]
        rows.append(os.path.join(d, "r", "npbmg", "calibrate.csv"), rs, schedule.FIELDS)

    def test_three_capped_runs(self):
        d = tempfile.mkdtemp()
        self.calibration(d, [1800.4, 1801.0, 1800.2])
        jobs = schedule.jobs_of(plan_rows(), r"^npbmg/(calibrate|plain)$", 10, configs="np16", tools="plain:-")
        sc = scheduler(d, jobs)
        cal4 = [j for j in jobs if j["calibration"] and j["run"] == 4][0]
        self.assertEqual(sc.calibration_state(cal4), "stop")         # no more calibration runs
        out, short = calibrate.derive(os.path.join(d, "r"), "npbmg")
        self.assertEqual((out[0]["T"], out[0]["cannot_run"]), ("", calibrate.CANNOT_RUN))
        self.assertEqual(short, [])
        # the counted runs are rows that say so; no job starts
        counted = [j for j in jobs if not j["calibration"]]
        sc = scheduler(d, counted)
        sc.adopt_leftovers = lambda: None
        started = []
        real = schedule.subprocess.Popen
        schedule.subprocess.Popen = lambda *x, **k: started.append(x)
        try:
            sc.run()
        finally:
            schedule.subprocess.Popen = real
        self.assertEqual(started, [])
        got = rows.read(os.path.join(d, "r", "npbmg", "plain.csv"))
        self.assertEqual(len(got), len(counted))
        self.assertEqual(set((r["outcome"], r["result"], r["timeout"]) for r in got), {("cannot-run", "CANNOT-RUN", "0")})

    def test_two_capped_one_ended(self):
        d = tempfile.mkdtemp()
        self.calibration(d, [1800.4, 12.0, 1801.0, 9.0, 8.0, 11.5, 10.0, 9.5, 10.5, 11.0])
        jobs = schedule.jobs_of(plan_rows(), r"^npbmg/calibrate$", 10, configs="np16", tools="plain:-")
        sc = scheduler(d, jobs)
        self.assertIsNone(sc.calibration_state([j for j in jobs if j["run"] == 4][0]))   # the calibration goes on
        out, _ = calibrate.derive(os.path.join(d, "r"), "npbmg")
        self.assertEqual((out[0]["T"], out[0]["capped"], out[0]["cannot_run"]), (120, 2, ""))   # amendment 5: from the 8 ended

    def test_thread_cells(self):
        import external
        d = tempfile.mkdtemp()
        res = os.path.join(d, "r")
        rs = []
        for k in (1, 2, 3):
            for c in external.MUSTVC_CELLS["uintah"]:
                rs.append({"name": c, "variant": "fixed", "run": k, "seconds": 1800.1, "rc": 124,
                           "calibrated": "mustvc/uintah", "commit": "c", "superseded": ""})
                rs.append({"name": "native:" + c, "variant": "fixed", "run": k, "seconds": 4.0, "rc": 0,
                           "calibrated": "mustvc/uintah", "commit": "c", "superseded": ""})
        rows.append(os.path.join(res, "mustvc", "calibrate-uintah.csv"), rs, list(rs[0]))
        jobs = schedule.jobs_of(plan_rows(), r"^mustvc/(calibrate-uintah|uintah)$", 40)
        sc = scheduler(d, jobs)
        self.assertEqual(sc.calibration_state([j for j in jobs if j["calibration"] and j["run"] == 4][0]), "stop")
        out = calibrate.derive_threads(res, 10)
        self.assertTrue(all(r["cannot_run"] for r in out))          # the MUST-vc cells and their runs without MUST
        main = [j for j in jobs if not j["calibration"]][0]
        self.assertEqual(sc.timeout_for(main), {})
        reason, cells = sc.cannot_run(main)
        self.assertEqual((reason, cells), (calibrate.CANNOT_RUN, None))   # the whole job: rows that say so


class Campaign(unittest.TestCase):
    """The disk floor and campaign.sh's phases."""

    def test_disk_floor_is_a_resumable_stop(self):
        d = tempfile.mkdtemp()
        jobs = schedule.jobs_of(plan_rows(), r"^hypre/plain$", 1, configs="np4")
        sc = scheduler(d, jobs)
        sc.a.timeout, sc.a.min_free_gb = 60, 1e12          # more than any disk has
        sc.adopt_leftovers = lambda: None
        started = []
        real = schedule.subprocess.Popen
        schedule.subprocess.Popen = lambda *x, **k: started.append(x)
        try:
            rc = sc.run()
        finally:
            schedule.subprocess.Popen = real
        self.assertEqual((rc, started), (schedule.RESUMABLE, []))
        with open(os.path.join(d, "r", "journal.log")) as fh:
            self.assertIn("disk: ", fh.read())
        sc.a.min_free_gb = 0
        self.assertEqual(sc.low_disk(), "")

    def run_campaign(self, fail=None):
        import subprocess
        d = tempfile.mkdtemp()
        stub = os.path.join(d, "py")
        with open(stub, "w") as fh:
            fh.write("#!/usr/bin/env python3\nimport os, sys\n"
                     "open(os.environ['LOG'], 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
                     "sys.exit(int(os.environ.get('FAIL_RC', '0')) if os.environ.get('FAIL', '-') in ' '.join(sys.argv) else 0)\n")
        os.chmod(stub, 0o755)
        log = os.path.join(d, "calls")
        env = dict(os.environ, MACHINE="m", STRETTO_COMMIT="c", PYTHON=stub, LOG=log)
        if fail:
            env.update(FAIL=fail[0], FAIL_RC=str(fail[1]))
        p = subprocess.run([os.path.join(P, "campaign.sh"), os.path.join(d, "s"), os.path.join(d, "r"), "--runs", "1"], env=env)
        with open(log) as fh:
            calls = [ln.split()[0].split("/")[-1] + " " + " ".join(ln.split()[1:2]) for ln in fh.read().splitlines()]
        return p.returncode, calls

    def test_phases_in_order(self):
        rc, calls = self.run_campaign()
        self.assertEqual(rc, 0)
        self.assertEqual(calls, ["schedule.py --state", "calibrate.py derive", "calibrate.py derive-suites",
                                 "calibrate.py derive-threads", "schedule.py --state"])

    def test_a_stop_halts_it(self):
        rc, calls = self.run_campaign(("--blocks", schedule.RESUMABLE))     # the calibration phase stops on the disk floor
        self.assertEqual((rc, len(calls)), (schedule.RESUMABLE, 1))
        rc, calls = self.run_campaign(("derive-threads", 1))
        self.assertEqual((rc, calls[-1]), (1, "calibrate.py derive-threads"))

if __name__ == "__main__":
    unittest.main()
