"""Replays of the idioms, end to end: the replay library and the tracer as built (make -C replay,
make -C tracer, make -C idioms), the idiom traces of scripts/run_all.sh (traces/<MPI>/), and
mpirun. They skip when one of those is missing, or fail under STRETTO_REQUIRE_TRACES=1 (the
setting of experiments/run_remaining.sh tests, which builds and traces first).

   cd analysis && python3 -m unittest tests.test_replay_e2e"""
import glob
import os
import shutil
import subprocess
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
        self.assertEqual(set(reads.check_sources(rmap, t).values()), {src})
        # a second source of that name elsewhere is told apart by its directories; two that the
        # traced path does not tell apart, with the same content, leave no source checked
        self.assertEqual(set(reads.check_sources(dict(rmap, sources={src: digest, "/elsewhere/main.c": digest}), t).values()), {src})
        with self.assertRaisesRegex(reads.MapMismatch, "another program"):
            reads.check_sources(dict(rmap, sources={"/a/x/main.c": digest, "/a/y/main.c": digest}), t)
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


class FlipPins(unittest.TestCase):
    """STRETTO_PIN_FLIP (replay/pin.h): a failed poll named by its poll segment forced to return
    true with a given message, and the control forcing its recorded false."""

    analysed = PinIdentity.analysed

    def test_a_failed_iprobe_is_forced_to_see_the_later_message(self):
        """Idiom 11: the first polling loop spins until rank 1's message arrives; the flip of its
        failed Iprobe to rank 2's message makes it wait for that message and return it, and the
        run then takes rank 1's in the second loop."""
        exe, t, rep = self.analysed(11)
        fl = [f for f in rep.by_class("probe-flip") if f.send is not None and t.sends[f.send].rank == 2
              and t.poll_seg.get((0, t.probes[f.probe].idx)) == 0]
        if not fl:
            self.skipTest("the first loop of this trace did not fail before rank 1's message")
        f = fl[0]
        key = t.poll_key[(0, t.probes[f.probe].idx)]
        self.assertRegex(f.pin, rf"^STRETTO_PIN_FLIP=0:{key}:2@0:11:[0-9a-fx]+:0$")    # no wildcard probe found a message before
        rp = C.run_checked(3, exe, [], [f.pin], timeout=20)
        self.assertEqual(rp.result, "FINISHED", rp.proc.stderr[-2000:])
        self.assertRegex(rp.proc.stderr, rf"\[stretto-pin\] rank 0: matched flip key={key} src=2 tag=11\b")
        self.assertTrue(C.all_matched([f.pin], rp.proc.stderr, None, rp.seen))
        ctl = C.control_pin(t, f.pin)
        self.assertRegex(ctl, rf"^STRETTO_PIN_FLIP=0:{key}:-1@")
        rc = C.run_checked(3, exe, [], [ctl], timeout=20)
        self.assertEqual(rc.result, "FINISHED")
        self.assertRegex(rc.proc.stderr, rf"\[stretto-pin\] rank 0: applied flip key={key} flag=0\b")
        self.assertTrue(C.all_matched([ctl], rc.proc.stderr, None, rc.seen))

    def test_a_flip_pin_names_its_poll_however_often_the_loop_failed_before(self):
        """Idiom 11 with two flips: the first loop's failed Iprobe forced to return rank 1's
        message, so that the replay's first loop never fails, and the second loop's failed
        Iprobe forced to return rank 2's. The second poll is in the trace's second poll segment
        and in the replay's first; its key, the rank's calls before it that are not failed
        polls, is the same in both, and the library forces it there."""
        exe, t, rep = self.analysed(11)
        fl = {t.poll_seg.get((0, t.probes[f.probe].idx)): f for f in rep.by_class("probe-flip")
              if f.send is not None and t.sends[f.send].rank == (1 if t.poll_seg.get((0, t.probes[f.probe].idx)) == 0 else 2)}
        if set(fl) != {0, 1}:
            self.skipTest("this trace lacks a failed poll in one of the two loops")
        first, second = fl[0], fl[1]
        k1, k2 = (t.poll_key[(0, t.probes[f.probe].idx)] for f in (first, second))
        self.assertGreater(k2, k1)
        spec = "STRETTO_PIN_FLIP=" + ",".join(f.pin.split("=", 1)[1] for f in (first, second))
        rp = C.run_checked(3, exe, [], [spec], timeout=20)
        self.assertEqual(rp.result, "FINISHED", rp.proc.stderr[-2000:])
        self.assertRegex(rp.proc.stderr, rf"\[stretto-pin\] rank 0: matched flip key={k1} src=1 tag=11\b")
        self.assertRegex(rp.proc.stderr, rf"\[stretto-pin\] rank 0: matched flip key={k2} src=2 tag=11\b")
        self.assertNotIn("unused", rp.proc.stderr)
        self.assertTrue(C.all_matched([spec], rp.proc.stderr, None, rp.seen))

    def test_a_flip_pin_after_another_probe_count_ends_the_run(self):
        """The same flip pin naming one wildcard probe found before the call, where the replay
        has found none: the replay diverged, and the library ends it as misapplied."""
        exe, t, rep = self.analysed(11)
        fl = [f for f in rep.by_class("probe-flip") if t.poll_seg.get((0, t.probes[f.probe].idx)) == 0]
        if not fl:
            self.skipTest("no flip in the first segment of this trace")
        bad = fl[0].pin.rsplit(":", 1)[0] + ":1"
        rp = C.run_checked(3, exe, [], [bad], timeout=20)
        self.assertEqual(rp.result, f"APP-ERROR:{C.PIN_MISAPPLIED}")
        self.assertTrue(C.pins_misapplied(rp.proc.stderr))

    def test_a_true_iprobe_is_forced_to_return_false_once(self):
        """Idiom 11: the probe that found rank 1's message returns false once (STRETTO_PIN_PROBE
        with source -1); the loop's next call finds the message, and the run finishes."""
        exe, t, rep = self.analysed(11)
        (u,) = [f for f in rep.by_class("probe-unflip") if t.probes[f.probe].pidx == 0]
        rp = C.run_checked(3, exe, [], [u.pin], timeout=20)
        self.assertEqual(rp.result, "FINISHED")
        self.assertRegex(rp.proc.stderr, r"\[stretto-pin\] rank 0: applied unflip pidx=0\b")
        self.assertNotIn("unused", rp.proc.stderr)


class EnvelopePins(unittest.TestCase):
    """Idiom 32: rank 0 drains tags 21 and 22 through one Iprobe call site. A replay with rank 1
    delayed finds the tag-22 messages first; a probe pin with its envelope's ordinal (the fourth
    field of its identity) still falls on its probe, one without it on a probe of the other tag
    (VTK's particle tracker, tags 622 and 624, docs/findings.md)."""

    analysed = PinIdentity.analysed
    LATE = {"STRETTO_SEND_DELAY_MS": "1:400"}      # rank 1's tag-21 messages after rank 2's

    def unflip(self, t, rep, n):
        """The unflip of the n-th tag-21 probe that found a message."""
        fs = [f for f in rep.by_class("probe-unflip") if t.probes[f.probe].tag == 21
              and C.envelope_ordinal(t, t.probes[f.probe]) == n]
        if not fs:
            self.skipTest(f"no tag-21 probe {n} in this trace")
        return fs[0]

    def test_an_unflip_falls_on_its_envelope_when_the_tags_interleave_otherwise(self):
        exe, t, rep = self.analysed(32)
        u = self.unflip(t, rep, 1)
        q = t.probes[u.probe]
        self.assertRegex(u.pin, rf"^STRETTO_PIN_PROBE=0:{q.pidx}:-1@0:21:[0-9a-f]+:1$")
        rp = C.run_checked(3, exe, [], [u.pin], timeout=30, extra_env=self.LATE)
        self.assertEqual(rp.result, "FINISHED", rp.proc.stderr[-2000:])
        self.assertEqual(rp.misapplied, [])
        self.assertRegex(rp.proc.stderr, rf"\[stretto-pin\] rank 0: applied unflip pidx={q.pidx} id=0:21:")
        self.assertNotIn("unused", rp.proc.stderr)
        # the same pin by the rank's ordinal alone: a tag-22 probe there, the run ends misapplied
        old = u.pin.rsplit(":", 1)[0]
        rp = C.run_checked(3, exe, [], [old], timeout=30, extra_env=self.LATE)
        self.assertEqual(rp.result, f"APP-ERROR:{C.PIN_MISAPPLIED}", rp.proc.stderr[-2000:])

    def test_the_horizon_of_an_unflip_replays_with_its_control(self):
        """horizon_pins: the first tag-21 probe pinned to rank 1 and the second forced false; the
        control pins both to rank 1. Both finish, with every pin taken, delayed or not."""
        exe, t, rep = self.analysed(32)
        u = self.unflip(t, rep, 1)
        alt, ctl = C.horizon_pins(t, u)
        req = C.required_matches(t, u)
        for env in (None, self.LATE):
            ra = C.run_checked(3, exe, [], alt, timeout=30, extra_env=env)
            self.assertEqual((ra.result, ra.misapplied), ("FINISHED", []), ra.proc.stderr[-2000:])
            self.assertTrue(C.all_matched(alt, ra.proc.stderr, req, ra.seen), ra.proc.stderr[-2000:])
            rc = C.run_checked(3, exe, [], ctl, timeout=30, extra_env=env)
            self.assertEqual((rc.result, rc.misapplied), ("FINISHED", []), rc.proc.stderr[-2000:])
            self.assertTrue(C.all_matched(ctl, rc.proc.stderr, None, rc.seen), rc.proc.stderr[-2000:])

    def test_a_flip_names_the_probes_of_its_envelope(self):
        """A failed tag-22 poll after both tag-21 messages, forced to return rank 2's message:
        its pin counts the tag-22 probes found before it (none), not the rank's two."""
        exe, t, rep = self.analysed(32)

        def found_21(q):
            return sum(1 for p in t.probes.values() if p.tag == 21 and p.pidx is not None and p.idx < q.idx)
        fl = [f for f in rep.by_class("probe-flip") if t.probes[f.probe].tag == 22 and t.sends[f.send].rank == 2
              and found_21(t.probes[f.probe]) == 2]
        if not fl:
            self.skipTest("no tag-22 flip after the tag-21 messages in this trace")
        f = min(fl, key=lambda f: t.probes[f.probe].idx)
        self.assertTrue(f.pin.endswith(":0"), f.pin)
        alt, ctl = C.horizon_pins(t, f)
        ra = C.run_checked(3, exe, [], alt, timeout=30)
        self.assertEqual((ra.result, ra.misapplied), ("FINISHED", []), ra.proc.stderr[-2000:])
        self.assertTrue(C.all_matched(alt, ra.proc.stderr, C.required_matches(t, f), ra.seen), ra.proc.stderr[-2000:])
        rc = C.run_checked(3, exe, [], ctl, timeout=30)
        self.assertEqual((rc.result, rc.misapplied), ("FINISHED", []), rc.proc.stderr[-2000:])


CANCEL_C = r"""
#include <mpi.h>
#include <stdio.h>
/* Rank 0 posts three Irecv(MPI_ANY_SOURCE, 5) and cancels them: the first it waits for, the
   second it frees, the third it leaves to MPI_Finalize (as Swift/T's ADLB does at shutdown).
   Rank 1 sends one message of tag 6, which a last receive takes. */
int main(int argc, char **argv) {
    int rank, a[3], v = 1;
    MPI_Request q[3];
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    if (rank == 0) {
        for (int i = 0; i < 3; i++) MPI_Irecv(&a[i], 1, MPI_INT, MPI_ANY_SOURCE, 5, MPI_COMM_WORLD, &q[i]);
        for (int i = 0; i < 3; i++) MPI_Cancel(&q[i]);
        MPI_Wait(&q[0], MPI_STATUS_IGNORE);
        MPI_Request_free(&q[1]);
        MPI_Recv(&v, 1, MPI_INT, MPI_ANY_SOURCE, 6, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    } else if (rank == 1) {
        MPI_Send(&v, 1, MPI_INT, 0, 6, MPI_COMM_WORLD);
    }
    MPI_Finalize();
    return 0;
}
"""


class CancelOutcome(unittest.TestCase):
    """The tracer records whether a receive's cancel succeeded wherever the program lets it
    know: at its wait, at its free (which the tracer turns into a wait) and, for a receive the
    program never completes, at MPI_Finalize (docs/model.md, cancellation)."""

    def test_every_cancelled_receive_has_its_outcome(self):
        from stretto import report, trace
        mpicc = f"/usr/bin/mpicc.{MPI}"
        if not (os.path.exists(TRACER) and os.path.exists(mpicc)):
            if REQUIRE:
                self.fail(f"not built: {TRACER} {mpicc}")
            self.skipTest("tracer or MPI compiler missing")
        with tempfile.TemporaryDirectory() as d:
            src, exe, tdir = os.path.join(d, "cancel.c"), os.path.join(d, "cancel"), os.path.join(d, "trace")
            with open(src, "w") as fh:
                fh.write(CANCEL_C)
            subprocess.run([mpicc, "-O0", "-g", "-o", exe, src], check=True)
            os.mkdir(tdir)
            r = subprocess.run(["/usr/bin/mpirun.openmpi", "--oversubscribe", "-np", "2", "-x", f"LD_PRELOAD={TRACER}",
                                "-x", f"STRETTO_TRACE_DIR={tdir}", exe], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            t = trace.load(tdir)
            self.assertEqual(t.unmodelled, [])
            self.assertEqual(len(t.withdrawn(0)), 3)
            ops = [rec.op for rec in t.ranks[0]]
            self.assertIn("cancelstatus", ops)
            self.assertEqual(report.analyze(t, with_sp=False).status(), "complete")


if __name__ == "__main__":
    unittest.main()
