"""Synchronisation through atomics (docs/threads.md, section 17): the tracer's edges from
atomic operations, fences, volatile accesses and annotations, and the analysis of the idioms
that use them (48-53).

The tracer tests compile a program of two threads per pattern with ThreadSanitizer's
instrumentation (gcc, and clang-18 where the image has it), link it with the stub runtime
(tracer/tsan_rt.c), run it on one rank under the tracer as built (make -C tracer) and check
in the loaded trace whether thread A's record before its release precedes thread B's record
after its acquire. They skip when the tracer or mpirun is missing, or fail under
STRETTO_REQUIRE_TRACES=1. The idiom tests read the traces of scripts/run_all.sh
(traces/<MPI>) when they exist, else the golden copies of both MPIs.

   cd analysis && python3 -m unittest tests.test_atomics"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
from stretto import trace as T  # noqa: E402
from stretto.report import analyze, format_report  # noqa: E402

REQUIRE = os.environ.get("STRETTO_REQUIRE_TRACES") == "1"
TRACER = os.path.join(ROOT, "tracer", "bin", "openmpi", "libstretto-trace.so")
MPICC = "/usr/bin/mpicc.openmpi"
MPIRUN = "/usr/bin/mpirun.openmpi"
CLANG = shutil.which("clang-18")

# Thread A: a record (MPI_Barrier on its own duplicate of MPI_COMM_SELF), then a release of
# the pattern; thread B: the matching acquire, which waits until it sees A's value, then a
# record. Pattern names are argv[1]; "edge" says whether A's record precedes B's in the
# recorded happens-before.
PROGRAM = r"""
#include <mpi.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>
#include "%(root)s/tracer/stretto_sync.h"
static MPI_Comm ca, cb, cc;
static const char *pat;
static atomic_int x;
static volatile int v;
static int plain;
static __int128 wide;
static void *thread_a(void *arg) {
    (void)arg;
    MPI_Barrier(ca);
    if (!strcmp(pat, "release")) atomic_store_explicit(&x, 1, memory_order_release);
    else if (!strcmp(pat, "relaxed")) atomic_store_explicit(&x, 1, memory_order_relaxed);
    else if (!strcmp(pat, "fences")) { atomic_thread_fence(memory_order_release); atomic_store_explicit(&x, 1, memory_order_relaxed); }
    else if (!strcmp(pat, "rmw")) atomic_fetch_add_explicit(&x, 1, memory_order_release);
    else if (!strcmp(pat, "sequence")) atomic_store_explicit(&x, 1, memory_order_release);
    else if (!strcmp(pat, "seqcst")) atomic_store(&x, 1);
    else if (!strcmp(pat, "volatile")) v = 1;
    else if (!strcmp(pat, "annotate")) { STRETTO_HAPPENS_BEFORE(&plain); __atomic_store_n(&plain, 1, __ATOMIC_RELAXED); }
    else if (!strcmp(pat, "wide")) { __int128 z = 0; __atomic_compare_exchange_n(&wide, &z, (__int128)1, 0, __ATOMIC_RELEASE, __ATOMIC_RELAXED); }
    return NULL;
}
static void *thread_c(void *arg) {    /* "sequence": a relaxed RMW continues A's release sequence */
    (void)arg;
    while (atomic_load_explicit(&x, memory_order_relaxed) != 1) sched_yield();
    atomic_fetch_add_explicit(&x, 1, memory_order_relaxed);
    return NULL;
}
static void *thread_b(void *arg) {
    (void)arg;
    if (!strcmp(pat, "release") || !strcmp(pat, "rmw")) while (!atomic_load_explicit(&x, memory_order_acquire)) sched_yield();
    else if (!strcmp(pat, "relaxed")) while (!atomic_load_explicit(&x, memory_order_relaxed)) sched_yield();
    else if (!strcmp(pat, "fences")) { while (!atomic_load_explicit(&x, memory_order_relaxed)) sched_yield(); atomic_thread_fence(memory_order_acquire); }
    else if (!strcmp(pat, "sequence")) while (atomic_load_explicit(&x, memory_order_acquire) != 2) sched_yield();
    else if (!strcmp(pat, "seqcst")) { int one = 1; while (!atomic_compare_exchange_weak(&x, &one, 2)) { one = 1; sched_yield(); } }
    else if (!strcmp(pat, "volatile")) while (!v) sched_yield();
    else if (!strcmp(pat, "annotate")) { while (!__atomic_load_n(&plain, __ATOMIC_RELAXED)) sched_yield(); STRETTO_HAPPENS_AFTER(&plain); }
    else if (!strcmp(pat, "wide")) while (__atomic_load_n(&wide, __ATOMIC_ACQUIRE) != 1) sched_yield();
    MPI_Barrier(cb);
    return NULL;
}
int main(int argc, char **argv) {
    int provided;
    pat = argc > 1 ? argv[1] : "release";
    MPI_Init_thread(&argc, &argv, MPI_THREAD_MULTIPLE, &provided);
    MPI_Comm_dup(MPI_COMM_SELF, &ca);
    MPI_Comm_dup(MPI_COMM_SELF, &cb);
    MPI_Comm_dup(MPI_COMM_SELF, &cc);
    pthread_t a, b, c;
    pthread_create(&a, NULL, thread_a, NULL);
    pthread_create(&b, NULL, thread_b, NULL);
    if (!strcmp(pat, "sequence")) { pthread_create(&c, NULL, thread_c, NULL); pthread_join(c, NULL); }
    pthread_join(a, NULL);
    pthread_join(b, NULL);
    MPI_Finalize();
    return 0;
}
"""

# pattern -> does A's record precede B's
PATTERNS = {"release": True, "relaxed": False, "fences": True, "rmw": True, "sequence": True, "seqcst": True,
            "volatile": True, "annotate": True, "wide": True}


def stub(d):
    """The stub runtime, built into d."""
    out = os.path.join(d, "libstretto-tsan.so")
    subprocess.run(["/usr/bin/gcc", "-O1", "-fPIC", "-mcx16", "-DSTRETTO_TSAN_STUB", "-shared", "-o", out,
                    os.path.join(ROOT, "tracer", "tsan_rt.c")], check=True)
    return out


def build(tc, d, compiler):
    """The program compiled with `compiler`'s instrumentation into d/prog."""
    src = os.path.join(d, "prog.c")
    with open(src, "w") as fh:
        fh.write(PROGRAM % {"root": ROOT})
    obj, exe = os.path.join(d, "prog.o"), os.path.join(d, "prog")
    env = dict(os.environ)
    if compiler == "gcc":
        flags = ["-fsanitize=thread", "--param=tsan-instrument-func-entry-exit=0", "--param=tsan-distinguish-volatile=1"]
    else:
        env["OMPI_CC"] = CLANG
        # volatile accesses are calls only with memory accesses instrumented (which do nothing)
        flags = ["-fsanitize=thread", "-mllvm", "-tsan-instrument-func-entry-exit=0", "-mllvm", "-tsan-distinguish-volatile=1"]
    subprocess.run([MPICC, "-O1", "-mcx16", "-pthread", *flags, "-c", "-o", obj, src], check=True, env=env)
    subprocess.run([MPICC, "-pthread", "-o", exe, obj, f"-L{d}", "-lstretto-tsan", f"-Wl,-rpath,{d}"], check=True, env=env)
    return exe


def run(exe, pat, tdir, extra=()):
    env = ["-x", f"LD_PRELOAD={TRACER}", "-x", f"STRETTO_TRACE_DIR={tdir}", *extra]
    r = subprocess.run([MPIRUN, "--oversubscribe", "-np", "1", *env, exe, pat], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=120, text=True)
    return r


def read_init(path):
    with open(path) as fh:
        return [x for x in fh if '"op":"init"' in x]


def a_before_b(tc, tdir):
    t = T.load(tdir)
    names = {t.chain_name[c]: c for c in t.chains.get(0, [])}
    a, b = names.get("0.0"), names.get("0.1")
    tc.assertIsNotNone(a, t.chain_name)
    tc.assertIsNotNone(b, t.chain_name)
    ra = next(r for r in t.ranks[a] if r.op == "coll")
    rb = next(r for r in t.ranks[b] if r.op == "coll")
    return t.hb.before(a, ra.idx, b, rb.idx), t


class Edges(unittest.TestCase):
    """Which patterns give the tracer an edge from A's release to B's acquire."""

    @classmethod
    def setUpClass(cls):
        missing = [p for p in (TRACER, MPICC, MPIRUN) if not os.path.exists(p)]
        cls.missing = missing
        cls.tmp = tempfile.mkdtemp(prefix="stretto-atomics-")
        if not missing:
            stub(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def need(self):
        if self.missing:
            msg = f"not built: {self.missing}"
            if REQUIRE:
                self.fail(msg)
            self.skipTest(msg)

    def patterns(self, compiler):
        self.need()
        d = os.path.join(self.tmp, compiler)
        os.makedirs(d, exist_ok=True)
        os.symlink(os.path.join(self.tmp, "libstretto-tsan.so"), os.path.join(d, "libstretto-tsan.so"))
        exe = build(self, d, compiler)
        for pat, want in PATTERNS.items():
            with self.subTest(compiler=compiler, pattern=pat):
                tdir = os.path.join(d, pat)
                r = run(exe, pat, tdir)
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                got, t = a_before_b(self, tdir)
                self.assertEqual(got, want, f"{pat}: edges {t.hb.edges}")
        # recording off: the operations are performed, nothing is recorded
        tdir = os.path.join(d, "off")
        r = run(exe, "release", tdir, ["-x", "STRETTO_ATOMICS=0"])
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        got, t = a_before_b(self, tdir)
        self.assertFalse(got)
        init = read_init(os.path.join(tdir, "rank-0.jsonl"))
        self.assertNotIn('"atomics"', init[0])
        init = read_init(os.path.join(d, "release", "rank-0.jsonl"))
        self.assertIn('"atomics":1', init[0])

    def test_gcc(self):
        self.patterns("gcc")

    @unittest.skipUnless(CLANG, "clang-18 is not in this image (experiments/container/Dockerfile.firstread has it)")
    def test_clang(self):
        self.patterns("clang")

    def test_untraced_runs_on_the_stub(self):
        """Without the tracer the program runs on the stub runtime, which performs every
        operation (the patterns' loops end)."""
        self.need()
        d = os.path.join(self.tmp, "plain")
        os.makedirs(d, exist_ok=True)
        os.symlink(os.path.join(self.tmp, "libstretto-tsan.so"), os.path.join(d, "libstretto-tsan.so"))
        exe = build(self, d, "gcc")
        for pat in PATTERNS:
            r = subprocess.run([MPIRUN, "--oversubscribe", "-np", "1", exe, pat], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=120, text=True)
            self.assertEqual(r.returncode, 0, f"{pat}: {r.stderr[-2000:]}")


GOLDEN = os.path.join(HERE, "golden")


def idiom_trace(tc, mpi, n):
    for base in (os.path.join(ROOT, "traces", mpi), os.path.join(GOLDEN, mpi)):
        name = next((x for x in sorted(os.listdir(base)) if x.startswith(f"{n}_")), None) if os.path.isdir(base) else None
        if name and any(f.startswith("rank-") for f in os.listdir(os.path.join(base, name))):
            return T.load(os.path.join(base, name))
    if REQUIRE:
        tc.fail(f"no trace of idiom {n} for {mpi}")
    tc.skipTest(f"no trace of idiom {n} for {mpi}")


class Idioms(unittest.TestCase):
    """Idioms 48-53 (both MPIs): an order carried by atomics, a volatile flag or an annotation
    is in the happens-before, and the alternatives it rules out are gone; where the atomic
    comes too early to order the calls, the alternative stays."""
    EXPECTED = {48: set(), 49: set(), 50: {"L1:1f"}, 51: {"L1:1f"}, 52: set(), 53: set()}

    def test_expected(self):
        for mpi in ("openmpi", "mpich"):
            for n, want in self.EXPECTED.items():
                with self.subTest(mpi=mpi, idiom=n):
                    t = idiom_trace(self, mpi, n)
                    rep = analyze(t, with_sp=False)
                    got = {f"{f.level}:{f.cls}" for f in rep.findings}
                    self.assertEqual(got, want, format_report(t, rep))
                    self.assertEqual(rep.status(), "complete")
                    if n != 53:                      # 53 needs no instrumentation
                        self.assertTrue(t.atomics, f"{mpi} {n}: no init record with atomics:1")

    def test_ordered_calls(self):
        """48 and 53: the sends of rank 0's two threads are ordered; 49: the receives; 52: the
        two allreduces of every rank."""
        for mpi in ("openmpi", "mpich"):
            for n, op in ((48, "send"), (53, "send"), (49, "recv"), (52, "coll")):
                with self.subTest(mpi=mpi, idiom=n):
                    t = idiom_trace(self, mpi, n)
                    for rank in ((0,) if n != 52 else range(t.np)):
                        recs = [r for c in t.chains[rank] for r in t.ranks[c] if r.op == op]
                        self.assertEqual(len(recs), 2, recs)
                        a, b = recs
                        self.assertTrue(t.hb.before(a.rank, a.idx, b.rank, b.idx) or t.hb.before(b.rank, b.idx, a.rank, a.idx),
                                        f"{mpi} {n} rank {rank}: {a} and {b} are concurrent")


if __name__ == "__main__":
    unittest.main()
