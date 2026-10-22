"""Keeping the traces a rerun records (stretto.keep, KEEP_TRACES): one compressed tar per trace
directory, a manifest line for each, no archive ever overwritten, nothing at all when
KEEP_TRACES is not set; and the shell drivers' keep_trace (experiments/harness.sh)."""
import concurrent.futures
import csv
import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from stretto import keep, report, trace  # noqa: E402

GOLDEN = os.path.join(HERE, "golden", "openmpi")


def manifest(base):
    with open(os.path.join(base, keep.MANIFEST), newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def unpack(archive, into):
    with tarfile.open(archive) as tar:
        tar.extractall(into, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return into


class Keep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stretto-keep-test-")
        self.base = os.path.join(self.tmp, "kept")
        self.trace = os.path.join(self.tmp, "trace")
        shutil.copytree(os.path.join(GOLDEN, "04_orphan_send"), self.trace)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, value):
        return mock.patch.dict(os.environ, {keep.ENV: value})

    def test_unset_does_nothing(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(keep.ENV, None)
            self.assertFalse(keep.enabled())
            self.assertIsNone(keep.keep(self.trace, "hypre/predict", "np4-run01"))
        with self.env(""):
            self.assertIsNone(keep.keep(self.trace, "hypre/predict", "np4-run01"))
        self.assertFalse(os.path.exists(self.base))
        self.assertEqual(sorted(os.listdir(self.tmp)), ["trace"])

    def test_an_archive_and_its_manifest_line(self):
        with self.env(self.base):
            path = keep.keep(self.trace, "hypre/predict", "np4-run01", notes={"replay.txt": "pins:\nSTRETTO_PIN=0:0:2\n"},
                             commit="abc1234")
        self.assertEqual(path, os.path.join(self.base, "hypre", "predict", "np4-run01.tar.xz"))
        out = unpack(path, os.path.join(self.tmp, "out"))
        names = sorted(os.listdir(os.path.join(out, "np4-run01")))
        self.assertEqual(names, ["rank-0.jsonl", "rank-1.jsonl", "rank-2.jsonl", "replay.txt"])
        for f in names[:3]:
            with open(os.path.join(out, "np4-run01", f), "rb") as a, open(os.path.join(self.trace, f), "rb") as b:
                self.assertEqual(a.read(), b.read())
        rows = manifest(self.base)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(list(r), keep.FIELDS)
        self.assertEqual((r["campaign"], r["name"], r["archive"], r["commit"]),
                         ("hypre/predict", "np4-run01", os.path.join("hypre", "predict", "np4-run01.tar.xz"), "abc1234"))
        self.assertEqual(r["source"], self.trace)          # outside the repository: absolute
        self.assertEqual(int(r["files"]), 3)
        self.assertEqual(int(r["bytes"]), sum(os.path.getsize(f) for f in glob.glob(os.path.join(self.trace, "*"))))
        self.assertEqual(int(r["archive_bytes"]), os.path.getsize(path))
        self.assertEqual(r["sha256"], sha256(path))
        self.assertFalse([f for f in os.listdir(os.path.dirname(path)) if f.endswith(".part")])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)

    def test_the_kept_trace_analyses_as_the_original(self):
        with self.env(self.base):
            path = keep.keep(self.trace, "corpus", "04_orphan_send")
        out = os.path.join(unpack(path, os.path.join(self.tmp, "out")), "04_orphan_send")
        a, b = trace.load(self.trace), trace.load(out)
        self.assertEqual(report.format_report(a, report.analyze(a), show_infeasible=True),
                         report.format_report(b, report.analyze(b), show_infeasible=True))

    def test_no_archive_is_overwritten(self):
        with self.env(self.base):
            first = keep.keep(self.trace, "amg", "trace8")
            digest = sha256(first)
            with open(os.path.join(self.trace, "rank-0.jsonl"), "a") as fh:
                fh.write("\n")
            second = keep.keep(self.trace, "amg", "trace8")
            third = keep.keep(self.trace, "amg", "trace8")
        self.assertEqual([os.path.basename(p) for p in (first, second, third)],
                         ["trace8.tar.xz", "trace8~2.tar.xz", "trace8~3.tar.xz"])
        self.assertEqual(sha256(first), digest)
        self.assertEqual([r["archive"] for r in manifest(self.base)], [os.path.join("amg", p) for p in
                                                                       ("trace8.tar.xz", "trace8~2.tar.xz", "trace8~3.tar.xz")])
        # the second archive unpacks under its own name, beside the first
        out = unpack(second, os.path.join(self.tmp, "out"))
        self.assertEqual(os.listdir(out), ["trace8~2"])

    def test_drivers_keeping_at_once(self):
        with self.env(self.base):
            with concurrent.futures.ThreadPoolExecutor(8) as ex:
                paths = list(ex.map(lambda i: keep.keep(self.trace, "valid/hypre", "np8-run03" if i % 2 else f"np8-run{i:02d}"),
                                    range(16)))
        self.assertEqual(len(set(paths)), 16)
        rows = manifest(self.base)
        self.assertEqual(len(rows), 16)
        self.assertEqual(sorted(r["archive"] for r in rows), sorted(os.path.relpath(p, self.base) for p in paths))
        for p in paths:
            with tarfile.open(p) as tar:
                self.assertEqual(len([m for m in tar.getnames() if m.endswith(".jsonl")]), 3)

    def test_a_relative_directory_is_the_repository_s(self):
        with self.env("artifact/out/traces"):
            self.assertEqual(keep.directory(), os.path.join(ROOT, "artifact", "out", "traces"))

    def test_names(self):
        self.assertEqual(keep.campaign_of(os.path.join(ROOT, "experiments", "results", "hypre", "predict.csv")), "hypre/predict")
        self.assertEqual(keep.campaign_of(os.path.join(ROOT, "experiments", "results", "valid", "nekbone.csv")), "valid/nekbone")
        self.assertEqual(keep.campaign_of(os.path.join(ROOT, "experiments", "mpiprv", "work", "x.csv")),
                         "experiments/mpiprv/work/x")
        self.assertEqual(keep.campaign_of("/elsewhere/scratch/mbi.csv"), "mbi")
        self.assertEqual(keep.safe("ex2 thresh=0.01 nlevels=2"), "ex2_thresh=0.01_nlevels=2")
        self.assertEqual(keep.safe("../x"), "_._x")
        self.assertLessEqual(len(keep.safe("x" * 400)), 150)
        self.assertNotEqual(keep.safe("x" * 400), keep.safe("x" * 399))
        with self.env(self.base):
            path = keep.keep(self.trace, "petsc/scan", "ex2 thresh=0.01/np4 run 1")
        self.assertEqual(os.path.relpath(path, self.base), os.path.join("petsc", "scan", "ex2_thresh=0.01_np4_run_1.tar.xz"))

    def test_a_trace_that_cannot_be_kept_stops_the_driver(self):
        blocker = os.path.join(self.tmp, "file")
        open(blocker, "w").close()
        with self.env(blocker):
            with self.assertRaises(keep.KeepError):
                keep.keep(self.trace, "amg", "trace8")

    def test_a_run_without_a_trace_directory_keeps_nothing(self):
        with self.env(self.base):
            self.assertIsNone(keep.keep(os.path.join(self.tmp, "none"), "amg", "trace8"))


def bash(script: str, **env):
    return subprocess.run(["bash", "-c", f'R="{ROOT}"; source "$R/experiments/harness.sh"; {script}'],
                          capture_output=True, text=True, env={**os.environ, **env})


class ShellKeep(unittest.TestCase):
    """keep_trace, the shell drivers' helper: silent, since some drivers' output is a table."""

    def test_keep_trace(self):
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in os.environ.items() if k != keep.ENV}
            # unset: Python does not even start (the python3 defined here fails if it does)
            r = subprocess.run(["bash", "-c", f'source "{ROOT}/experiments/harness.sh"; python3() {{ return 9; }}; '
                                              f'keep_trace "{GOLDEN}/04_orphan_send" e4/e4 x'],
                               capture_output=True, text=True, env=env)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
            r = bash(f'keep_trace "{GOLDEN}/04_orphan_send" e4/e4 lu.A.16.rep1', KEEP_TRACES=d)
            self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
            self.assertTrue(os.path.exists(os.path.join(d, "e4", "e4", "lu.A.16.rep1.tar.xz")))
            self.assertEqual(len(manifest(d)), 1)
            open(os.path.join(d, "blocker"), "w").close()
            r = bash(f'keep_trace "{GOLDEN}/04_orphan_send" e4/e4 x || echo stopped', KEEP_TRACES=os.path.join(d, "blocker"))
            self.assertEqual(r.stdout, "stopped\n")
            self.assertIn("KEEP-ERROR", r.stderr)


if __name__ == "__main__":
    unittest.main()
