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


if __name__ == "__main__":
    unittest.main()
