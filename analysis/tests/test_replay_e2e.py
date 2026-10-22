"""Replays of the idioms, end to end: the replay library and the tracer as built (make -C replay,
make -C tracer, make -C idioms), the idiom traces of scripts/run_all.sh (traces/<MPI>/), and
mpirun. They skip when one of those is missing, or fail under STRETTO_REQUIRE_TRACES=1 (the
setting of experiments/run_remaining.sh tests, which builds and traces first).

   cd analysis && python3 -m unittest tests.test_replay_e2e"""
import glob
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from stretto import confirm as C  # noqa: E402

MPI = os.environ.get("MPI", "openmpi")
REQUIRE = os.environ.get("STRETTO_REQUIRE_TRACES") == "1"
PINLIB = os.path.join(ROOT, "replay", "bin", MPI, "libstretto-pin.so")
TRACER = os.path.join(ROOT, "tracer", "bin", MPI, "libstretto-trace.so")


def idiom(tc, n: int):
    """(executable, trace directory) of idiom n as built and traced here, or skip (fail under
    STRETTO_REQUIRE_TRACES=1)."""
    d = next(iter(glob.glob(os.path.join(ROOT, "idioms", f"{n:02d}_*"))), None)
    exe = os.path.join(d, "bin", MPI, "main") if d else ""
    tdir = os.path.join(ROOT, "traces", MPI, os.path.basename(d)) if d else ""
    missing = [p for p in (PINLIB, TRACER, exe) if not os.path.exists(p)]
    if not glob.glob(os.path.join(tdir, "rank-*.jsonl")):
        missing.append(tdir)
    if shutil.which("mpirun.openmpi") is None and not os.path.exists("/usr/bin/mpirun.openmpi"):
        missing.append("mpirun.openmpi")
    if missing:
        msg = f"not built or traced: {missing}"
        if REQUIRE:
            tc.fail(msg)
        tc.skipTest(msg)
    return exe, tdir


class Preload(unittest.TestCase):
    """A library ld.so cannot load is ignored and the run goes on as a plain one: the harness
    must refuse it, never record that run as a replay or a baseline run."""

    def test_the_replay_library_announces_itself(self):
        exe, _ = idiom(self, 1)
        r = C.run_pinned(3, exe, [], None, timeout=60)
        self.assertRegex(r.stderr, r"(?m)^\[stretto-pin\] rank 0: loaded \(replay library\)")

    def test_an_mpich_program_runs_with_mpichs_library(self):
        """mpi="mpich" preloads the MPICH build of the replay library, through the launcher for
        MPICH (Open MPI's mpirun by default: Ubuntu's MPICH is built with PMIx)."""
        exe, _ = idiom(self, 1)
        exe = exe.replace(f"/bin/{MPI}/", "/bin/mpich/")
        lib = os.path.join(ROOT, "replay", "bin", "mpich", "libstretto-pin.so")
        if not (os.path.exists(exe) and os.path.exists(lib)):
            if REQUIRE:
                self.fail(f"not built: {exe} {lib}")
            self.skipTest("MPICH builds missing")
        r = C.run_pinned(3, exe, [], None, timeout=60, mpi="mpich")
        self.assertRegex(r.stderr, r"(?m)^\[stretto-pin\] rank 0: loaded \(replay library\)")
        self.assertEqual(C.classify_rc(r.returncode, r.stderr, r.elapsed, 60), "FINISHED")

    def test_a_missing_library_is_refused_before_the_run(self):
        exe, _ = idiom(self, 1)
        with self.assertRaisesRegex(C.HarnessError, "missing"):
            C.run_pinned(3, exe, [], None, timeout=60, preload="/nonexistent/libstretto-pin.so")

    def test_a_library_that_does_not_load_is_refused(self):
        exe, _ = idiom(self, 1)
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "libstretto-pin.so")
            with open(bad, "w") as fh:
                fh.write("not an object\n")
            with self.assertRaisesRegex(C.HarnessError, "did not announce itself"):
                C.run_pinned(3, exe, [], None, timeout=60, preload=bad, extra_env={"STRETTO_SEND_DELAY_MS": "0:1"})


class Truncation(unittest.TestCase):
    """Idiom 05: rank 0's wildcard receive pinned to rank 2, whose message is longer, fails with
    MPI_ERR_TRUNCATE inside PMPI_Recv; the replay is confirmed by that error, by the pin's
    applied line and by the replay's trace, which leaves the receive pending where rank 0
    stopped, while the control finishes."""

    def test_a_truncation_is_confirmed(self):
        from stretto import report, trace
        exe, tdir = idiom(self, 5)
        t = trace.load(tdir)
        rep = report.analyze(t)
        res = [c for c in C.confirm(rep, 3, exe, timeout=20, t=t) if c.finding.cls == "1a" and c.finding.level == "L1"]
        self.assertTrue(res)
        for c in res:
            self.assertEqual(c.finding.error, "truncate")
            self.assertEqual(c.result, "MPI-ERROR:MPI_ERR_TRUNCATE")
            self.assertTrue(c.pin_matched, c)
            self.assertEqual(c.control, "FINISHED")
            self.assertEqual(c.verdict, "confirmed", c)


class ReadMap(unittest.TestCase):
    """A first-read map is used only with a trace of a program built from the sources it was made
    from (stretto.reads.check_sources): the idiom's own source checks, an edited one does not."""

    def test_the_map_must_be_of_the_traced_sources(self):
        import hashlib
        from stretto import reads, trace
        if shutil.which("addr2line") is None:
            self.skipTest("no addr2line")
        exe, tdir = idiom(self, 4)
        t = trace.load(tdir)
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(exe))), "main.c")
        with open(src, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        rmap = {"format": "stretto-firstread 1", "sites": [], "results": [], "sources": {src: digest}}
        self.assertEqual(reads.check_sources(rmap, t), 1)
        with self.assertRaisesRegex(reads.MapMismatch, "differs"):
            reads.check_sources(dict(rmap, sources={src: "0" * 64}), t)
        with self.assertRaisesRegex(reads.MapMismatch, "no hashes"):
            reads.check_sources(dict(rmap, sources={}), t)
        with self.assertRaisesRegex(reads.MapMismatch, "another program"):
            reads.check_sources(dict(rmap, sources={"/elsewhere/other.c": digest}), t)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "map.json")
            import json
            with open(path, "w") as fh:
                json.dump(dict(rmap, sources={src: "0" * 64}), fh)
            with self.assertRaises(reads.MapMismatch):
                reads.SiteMap.load(path, t=t)
            self.assertIsNotNone(reads.SiteMap.load(path, t=t, unchecked=True))


class PinIdentity(unittest.TestCase):
    """The pins carry the traced operation's communicator, tag and call site; the replay
    library checks them where it applies a pin, reports them with every match, and ends a run
    whose pin falls on another operation (replay/pin.h)."""

    def analysed(self, n):
        from stretto import report, trace
        exe, tdir = idiom(self, n)
        t = trace.load(tdir)
        return exe, t, report.analyze(t)

    def test_an_idiom_confirms_with_identity_pins(self):
        """Idiom 04: rank 0's wildcard receive pinned to rank 2 leaves rank 1's message orphaned and
        the run deadlocked; the replay's report lines and its own trace name the pinned receive."""
        exe, t, rep = self.analysed(4)
        res = [c for c in C.confirm(rep, 3, exe, timeout=20, t=t) if c.finding.cls == "1d"]
        self.assertTrue(res)
        for c in res:
            self.assertEqual(c.verdict, "confirmed", c)
            self.assertEqual(c.misapplied, "")
            self.assertRegex(c.pin, r"STRETTO_PIN=0:0:2@0:4:[0-9a-f]+")
            # matched on an operation of the pin's identity, by the library's line or the replay's
            # trace (the tail of stderr can be mpirun's own report of the timeout's kill)
            self.assertTrue(c.pin_matched, c)

    def test_a_subcommunicator_pin_names_its_communicator(self):
        """Idiom 12: the wildcard receive is on a communicator split from MPI_COMM_WORLD, the
        rank's third (local id 2); the pin says so and the library sees the same."""
        exe, t, rep = self.analysed(12)
        r = next(r for r in t.recvs.values() if r.widx is not None)
        self.assertEqual(C.identity(t, r)[0], 2)
        tok = C.pin_token(t, r, t.observed_send(r).rank)
        rp = C.run_checked(4, exe, [], [f"STRETTO_PIN={tok}"], timeout=20)
        self.assertEqual(rp.misapplied, [])
        self.assertTrue(C.all_matched([f"STRETTO_PIN={tok}"], rp.proc.stderr, None, rp.seen))

    def test_a_pin_on_another_operation_ends_the_run(self):
        """Idiom 09: the same horizon with the first receive's identity changed, as if the replay
        had reached another wildcard receive at that ordinal (another tag, another communicator,
        another call site): the library ends the run, the replay counts nothing as matched."""
        exe, t, rep = self.analysed(9)
        r = next(r for r in t.recvs.values() if r.widx == 0 and r.rank == 0)
        src = t.observed_send(r).rank
        good = C.pin_token(t, r, src)
        c, g, s = C.identity(t, r)
        for bad in (f"0:0:{src}@{c}:{g + 1}" + (f":{s:x}" if s is not None else ""),
                    f"0:0:{src}@{c + 5}:{g}",
                    f"0:0:{src}@{c}:{g}:{(s or 0) + 8:x}"):
            pins = [f"STRETTO_PIN={bad}"]
            rp = C.run_checked(3, exe, [], pins, timeout=20)
            self.assertEqual(rp.result, f"APP-ERROR:{C.PIN_MISAPPLIED}", bad)
            self.assertTrue(rp.misapplied, bad)
            self.assertFalse(C.all_matched(pins, rp.proc.stderr, None, rp.seen), bad)
        rp = C.run_checked(3, exe, [], [f"STRETTO_PIN={good}"], timeout=20)
        self.assertEqual(rp.misapplied, [])

    def test_a_probe_pin_on_another_probe_ends_the_run(self):
        """Idiom 11: a polling loop of wildcard Iprobes; the pin of the first probe that found a
        message, with another tag, falls on that probe in the replay and ends the run."""
        exe, t, rep = self.analysed(11)
        q = next((q for q in t.probes.values() if q.pidx == 0), None)
        if q is None:
            self.skipTest("no wildcard probe found a message in this trace")
        c, g, s = C.identity(t, q)
        src = t.sends[q.seen].rank
        rp = C.run_checked(3, exe, [], [f"STRETTO_PIN_PROBE={q.rank}:0:{src}@{c}:{g + 1}"], timeout=20)
        self.assertTrue(rp.misapplied)
        rp = C.run_checked(3, exe, [], [f"STRETTO_PIN_PROBE={C.pin_token(t, q, src)}"], timeout=20)
        self.assertEqual(rp.misapplied, [])
        self.assertIn(("probe", q.rank, 0), C._reported(rp.proc.stderr, [f"STRETTO_PIN_PROBE={C.pin_token(t, q, src)}"]) | rp.seen)


if __name__ == "__main__":
    unittest.main()
