"""A lock's clock lives as long as the lock (tracer, sobj_drop): pthread_*_init and *_destroy at
an address end the object there, so that a later lock at that address neither synchronises
with the old one nor keeps its clock. Without this a program that makes a mutex per task or
future (MADNESS) keeps a clock of 8 KB per address it ever used for a lock, and a traced run of
MADNESS's test_world took more than 12 GB (experiments/madness).

Each test compiles a program of one rank and two threads, runs it under the tracer as built
(make -C tracer) and checks the loaded trace, or the program's peak memory. They skip when the
tracer or mpirun is missing, or fail under STRETTO_REQUIRE_TRACES=1.

   cd analysis && python3 -m unittest tests.test_lock_lifetime"""
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

REQUIRE = os.environ.get("STRETTO_REQUIRE_TRACES") == "1"
TRACER = os.path.join(ROOT, "tracer", "bin", "openmpi", "libstretto-trace.so")
MPICC = "/usr/bin/mpicc.openmpi"
MPIRUN = "/usr/bin/mpirun.openmpi"

# Thread A: a record, then a critical section of m (with a record inside it for "deferred"),
# then, for "renew", m destroyed and initialised again; then a flag the tracer does not see
# (an atomic of uninstrumented code). Thread B waits for the flag, takes m, then makes a
# record; for "deferred" (A's section has a record, so B's edge waits for one of B's) it
# destroys and initialises m right after its own section, before its record. "many" initialises, takes and destroys 100 000 mutexes of an
# array and prints the peak resident size in KB.
PROGRAM = r"""
#include <mpi.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <string.h>
#include <sys/resource.h>
static MPI_Comm ca, cb, cin;
static const char *pat;
static pthread_mutex_t m;
static int flag;
#define MANY 100000
static pthread_mutex_t many[MANY];
static void *thread_a(void *arg) {
    (void)arg;
    MPI_Barrier(ca);
    pthread_mutex_lock(&m);
    if (!strcmp(pat, "deferred")) MPI_Barrier(cin);
    pthread_mutex_unlock(&m);
    if (!strcmp(pat, "renew")) { pthread_mutex_destroy(&m); pthread_mutex_init(&m, NULL); }
    __atomic_store_n(&flag, 1, __ATOMIC_RELEASE);
    return NULL;
}
static void *thread_b(void *arg) {
    (void)arg;
    while (!__atomic_load_n(&flag, __ATOMIC_ACQUIRE)) sched_yield();
    pthread_mutex_lock(&m);
    pthread_mutex_unlock(&m);
    if (!strcmp(pat, "deferred")) { pthread_mutex_destroy(&m); pthread_mutex_init(&m, NULL); }
    MPI_Barrier(cb);
    return NULL;
}
int main(int argc, char **argv) {
    int provided;
    pat = argc > 1 ? argv[1] : "keep";
    MPI_Init_thread(&argc, &argv, MPI_THREAD_MULTIPLE, &provided);
    MPI_Comm_dup(MPI_COMM_SELF, &ca);
    MPI_Comm_dup(MPI_COMM_SELF, &cb);
    MPI_Comm_dup(MPI_COMM_SELF, &cin);
    pthread_mutex_init(&m, NULL);
    if (!strcmp(pat, "many")) {
        for (int i = 0; i < MANY; i++) {
            pthread_mutex_init(&many[i], NULL);
            pthread_mutex_lock(&many[i]);
            pthread_mutex_unlock(&many[i]);
            pthread_mutex_destroy(&many[i]);
        }
        MPI_Barrier(ca);
        struct rusage ru;
        getrusage(RUSAGE_SELF, &ru);
        printf("maxrss %ld\n", ru.ru_maxrss);
        MPI_Finalize();
        return 0;
    }
    pthread_t a, b;
    pthread_create(&a, NULL, thread_a, NULL);
    pthread_create(&b, NULL, thread_b, NULL);
    pthread_join(a, NULL);
    pthread_join(b, NULL);
    MPI_Finalize();
    return 0;
}
"""


class LockLifetime(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.missing = [p for p in (TRACER, MPICC, MPIRUN) if not os.path.exists(p)]
        cls.tmp = tempfile.mkdtemp(prefix="stretto-locklife-")
        cls.exe = None
        if not cls.missing:
            src = os.path.join(cls.tmp, "prog.c")
            with open(src, "w") as fh:
                fh.write(PROGRAM)
            cls.exe = os.path.join(cls.tmp, "prog")
            subprocess.run([MPICC, "-O1", "-pthread", "-o", cls.exe, src], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def need(self):
        if self.missing:
            msg = f"not built: {self.missing}"
            if REQUIRE:
                self.fail(msg)
            self.skipTest(msg)

    def run_traced(self, pat):
        tdir = os.path.join(self.tmp, pat)
        r = subprocess.run([MPIRUN, "--oversubscribe", "-np", "1", "-x", f"LD_PRELOAD={TRACER}",
                            "-x", f"STRETTO_TRACE_DIR={tdir}", "-x", "STRETTO_SYNC_ORDER=1", self.exe, pat],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return tdir, r.stdout

    def a_before_b(self, tdir):
        t = T.load(tdir)
        names = {t.chain_name[c]: c for c in t.chains.get(0, [])}
        a, b = names.get("0.0"), names.get("0.1")
        self.assertIsNotNone(a, t.chain_name)
        self.assertIsNotNone(b, t.chain_name)
        ra = next(r for r in t.ranks[a] if r.op == "coll")
        rb = next(r for r in t.ranks[b] if r.op == "coll")
        return t.hb.before(a, ra.idx, b, rb.idx)

    def test_same_lock_orders(self):
        """A's unlock and B's lock of one mutex: an edge."""
        self.need()
        tdir, _ = self.run_traced("keep")
        self.assertTrue(self.a_before_b(tdir))

    def test_renewed_lock_does_not_order(self):
        """m destroyed and initialised again between A's unlock and B's lock: no edge."""
        self.need()
        tdir, _ = self.run_traced("renew")
        self.assertFalse(self.a_before_b(tdir))
        # the sync log numbers the new object anew
        with open(os.path.join(tdir, "sync-0.txt")) as fh:
            locks = [ln.split()[0] for ln in fh if not ln.startswith("#")]
        self.assertGreaterEqual(len(set(locks)), 2, locks)

    def test_destroy_inside_deferral(self):
        """B's edge from A's section waited for a record of B's (A's section had one); B then
        destroys m before its record: the run ends normally and A still precedes B."""
        self.need()
        tdir, _ = self.run_traced("deferred")
        self.assertTrue(self.a_before_b(tdir))

    def test_many_locks_free_their_clocks(self):
        """100 000 mutexes initialised, taken and destroyed: their clocks (8 KB each, 800 MB in
        all if kept) are freed."""
        self.need()
        _, out = self.run_traced("many")
        rss = int(next(ln.split()[1] for ln in out.splitlines() if ln.startswith("maxrss")))
        self.assertLess(rss, 300 * 1024, out)


if __name__ == "__main__":
    unittest.main()
