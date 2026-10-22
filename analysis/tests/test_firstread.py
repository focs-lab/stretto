"""The static first-read analysis (stretto.firstread, docs/firstread.md) on small C programs.

Each test compiles a program with clang-18 and the Open MPI headers and checks, for one call
that delivers a receive's result, which later MPI call sites the map certifies as reached only
before the result is read. The tests that need clang are skipped where it is missing (the
experiment image has none; experiments/container/Dockerfile.firstread adds it); the reader of
the IR is also tested on a fixed IR text, which needs nothing."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stretto.firstread import analyse_ir, ir  # noqa: E402
from stretto.firstread.__main__ import compile_ir, mpi_flags  # noqa: E402

HAVE_CLANG = shutil.which("clang-18") is not None and shutil.which("mpicc.openmpi") is not None

HEAD = "#include <mpi.h>\n#include <stdio.h>\n#include <stdlib.h>\n"


def analyse_c(src: str) -> dict:
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "prog.c")
        with open(path, "w") as fh:
            fh.write(HEAD + src)
        return analyse_ir([(path, compile_ir("clang-18", path, mpi_flags("openmpi"), d))])


def sites(m: dict, call: str):
    """Site ids of the calls to `call`, in source order."""
    return [s["id"] for s in sorted(m["sites"], key=lambda s: (s["line"], s["col"])) if s["call"] == call]


def entry(m: dict, complete: int, post="same"):
    post = complete if post == "same" else post
    hits = [r for r in m["results"] if r["complete"] == complete and r["post"] == post]
    assert len(hits) == 1, (complete, post, m["results"])
    return hits[0]


@unittest.skipUnless(HAVE_CLANG, "clang-18 and the Open MPI headers are needed")
class FirstReadOnPrograms(unittest.TestCase):
    def test_result_never_read(self):
        """A receive in a loop whose buffer and status nobody reads: every later call is free,
        the loop's own receive and MPI_Finalize included."""
        m = analyse_c("""
int main(int argc, char **argv) {
  int buf = 0, x = 1; MPI_Status st;
  MPI_Init(&argc, &argv);
  for (int i = 0; i < 4; i++)
    MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, &st);
  MPI_Send(&x, 1, MPI_INT, 0, 0, MPI_COMM_WORLD);
  MPI_Finalize();
  return 0;
}""")
        r = entry(m, sites(m, "MPI_Recv")[0])
        self.assertEqual(set(r["objects"]), {"buf", "st"})
        self.assertTrue(set(sites(m, "MPI_Recv") + sites(m, "MPI_Send") + sites(m, "MPI_Finalize")) <= set(r["free"]))
        self.assertNotIn(sites(m, "MPI_Init")[0], r["free"])          # not reachable after the receive

    def test_read_in_the_loop(self):
        """The buffer is read inside the loop: the next iteration's receive and everything after
        the read are no longer free."""
        m = analyse_c("""
int main(int argc, char **argv) {
  int buf = 0, x = 1, sum = 0;
  MPI_Init(&argc, &argv);
  for (int i = 0; i < 4; i++) {
    MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
    MPI_Barrier(MPI_COMM_WORLD);
    sum += buf;
  }
  MPI_Send(&x, 1, MPI_INT, 0, 0, MPI_COMM_WORLD);
  MPI_Finalize();
  return sum;
}""")
        r = entry(m, sites(m, "MPI_Recv")[0])
        self.assertEqual(r["objects"], ["buf"])
        self.assertEqual(r["free"], [], "the barrier is reachable after the read of the previous iteration")

    def test_read_only_on_one_branch(self):
        """A call reachable both before and after a read is not free; one before it is."""
        m = analyse_c("""
int main(int argc, char **argv) {
  int buf = 0, x = 1;
  MPI_Init(&argc, &argv);
  MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  MPI_Barrier(MPI_COMM_WORLD);
  if (argc > 1)
    x = buf;
  MPI_Send(&x, 1, MPI_INT, 0, 0, MPI_COMM_WORLD);
  MPI_Finalize();
  return 0;
}""")
        r = entry(m, sites(m, "MPI_Recv")[0])
        self.assertIn(sites(m, "MPI_Barrier")[0], r["free"])
        self.assertNotIn(sites(m, "MPI_Send")[0], r["free"])
        self.assertNotIn(sites(m, "MPI_Finalize")[0], r["free"])

    def test_forward_is_a_read(self):
        """Sending the received buffer on reads it: the send is not free, a call before it is."""
        m = analyse_c("""
int main(int argc, char **argv) {
  int buf = 0;
  MPI_Init(&argc, &argv);
  MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  MPI_Barrier(MPI_COMM_WORLD);
  MPI_Send(&buf, 1, MPI_INT, 1, 0, MPI_COMM_WORLD);
  MPI_Finalize();
  return 0;
}""")
        r = entry(m, sites(m, "MPI_Recv")[0])
        self.assertEqual(r["free"], [sites(m, "MPI_Barrier")[0]])

    def test_receiving_again_is_a_write(self):
        """Receiving into the same buffer again writes it and reads nothing."""
        m = analyse_c("""
int main(int argc, char **argv) {
  int buf = 0;
  MPI_Init(&argc, &argv);
  MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  MPI_Recv(&buf, 1, MPI_INT, 1, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  if (buf) MPI_Barrier(MPI_COMM_WORLD);
  MPI_Finalize();
  return 0;
}""")
        r1, r2 = sites(m, "MPI_Recv")
        self.assertEqual(entry(m, r1)["free"], [r2])

    def test_status_read(self):
        """Reading MPI_SOURCE from the status is a read; so is MPI_Get_count on it."""
        m = analyse_c("""
int main(int argc, char **argv) {
  int buf = 0, n = 0, x = 1; MPI_Status st;
  MPI_Init(&argc, &argv);
  MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, &st);
  MPI_Barrier(MPI_COMM_WORLD);
  MPI_Send(&x, 1, MPI_INT, st.MPI_SOURCE, 0, MPI_COMM_WORLD);
  MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, &st);
  MPI_Barrier(MPI_COMM_WORLD);
  MPI_Get_count(&st, MPI_INT, &n);
  MPI_Barrier(MPI_COMM_WORLD);
  MPI_Finalize();
  return 0;
}""")
        recv1, recv2 = sites(m, "MPI_Recv")
        b1, b2, b3 = sites(m, "MPI_Barrier")
        self.assertEqual(entry(m, recv1)["free"], [b1])
        self.assertEqual(entry(m, recv2)["free"], [b2])

    def test_waitany_index_read(self):
        """The index a Waitany returns is a result: its read ends the free sites of the
        Waitany alone, and of each (Irecv, Waitany) pair."""
        m = analyse_c("""
int main(int argc, char **argv) {
  int a = 0, b = 0, idx = -1, x = 1; MPI_Request q[2];
  MPI_Init(&argc, &argv);
  MPI_Irecv(&a, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, &q[0]);
  MPI_Irecv(&b, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, &q[1]);
  MPI_Waitany(2, q, &idx, MPI_STATUS_IGNORE);
  MPI_Barrier(MPI_COMM_WORLD);
  if (idx == 0) x = 2;
  MPI_Send(&x, 1, MPI_INT, 0, 0, MPI_COMM_WORLD);
  MPI_Finalize();
  return 0;
}""")
        w = sites(m, "MPI_Waitany")[0]
        ia, ib = sites(m, "MPI_Irecv")
        bar = sites(m, "MPI_Barrier")[0]
        self.assertEqual(entry(m, w, None)["free"], [bar])
        self.assertEqual(entry(m, w, None)["objects"], ["idx"])
        self.assertEqual(entry(m, w, ia)["free"], [bar])
        self.assertEqual(set(entry(m, w, ia)["objects"]), {"a", "idx"})

    def test_irecv_wait_pair(self):
        """A nonblocking receive is followed from its wait, with the Irecv's buffer and the
        wait's status: the buffer is read after the second barrier."""
        m = analyse_c("""
int main(int argc, char **argv) {
  int buf = 0, y = 0; MPI_Request q; MPI_Status st;
  MPI_Init(&argc, &argv);
  MPI_Irecv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, &q);
  MPI_Wait(&q, &st);
  MPI_Barrier(MPI_COMM_WORLD);
  MPI_Barrier(MPI_COMM_WORLD);
  y = buf;
  MPI_Finalize();
  return y;
}""")
        p, w = sites(m, "MPI_Irecv")[0], sites(m, "MPI_Wait")[0]
        b1, b2 = sites(m, "MPI_Barrier")
        e = entry(m, w, p)
        self.assertEqual(set(e["objects"]), {"buf", "st"})
        self.assertEqual(e["free"], [b1, b2])
        self.assertIn(sites(m, "MPI_Finalize")[0], entry(m, w, None)["free"])   # the status alone is never read

    def test_escape_falls_back_to_h1(self):
        """A buffer whose address is stored, passed to a function of the program or returned
        can be read where the analysis does not look: no site is free."""
        m = analyse_c("""
int *keep;
void use(int *p) { if (*p) MPI_Barrier(MPI_COMM_WORLD); }
int main(int argc, char **argv) {
  int a = 0, b = 0, c = 0;
  MPI_Init(&argc, &argv);
  keep = &a;
  MPI_Recv(&a, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  MPI_Recv(&b, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  use(&b);
  MPI_Recv(&c, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  MPI_Finalize();
  return 0;
}""")
        ra, rb, rc = sites(m, "MPI_Recv")
        for r in (ra, rb):
            e = entry(m, r)
            self.assertEqual(e["free"], [])
            self.assertIn("escapes", e["why"])
        self.assertIn(sites(m, "MPI_Finalize")[0], entry(m, rc)["free"])

    def test_absolute_address_is_an_escape(self):
        """MPI_Get_address hands out the buffer's address: a struct type built from it lets a
        send of MPI_BOTTOM forward the received value without naming the buffer, so the send
        must not be free. Before 22 Oct 2026 the call counted as a read at the point it was made
        (before the receive here) and the send was certified free."""
        m = analyse_c("""
int main(int argc, char **argv) {
  int buf = 0; MPI_Aint a; MPI_Datatype dt; int one = 1;
  MPI_Init(&argc, &argv);
  MPI_Get_address(&buf, &a);
  MPI_Type_create_struct(1, &one, &a, (MPI_Datatype[]){MPI_INT}, &dt);
  MPI_Type_commit(&dt);
  MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  MPI_Send(MPI_BOTTOM, 1, dt, 1, 0, MPI_COMM_WORLD);
  MPI_Finalize();
  return 0;
}""")
        e = entry(m, sites(m, "MPI_Recv")[0])
        self.assertEqual(e["free"], [])
        self.assertIn("escapes", e["why"])

    def test_persistent_and_attached_buffers_escape(self):
        """A buffer given to a persistent request is read at every MPI_Start, which does not
        name it; an attached buffer at every buffered send; a window's memory by other
        processes. Each makes the receive into that buffer H1."""
        for setup in ("MPI_Request q; MPI_Send_init(&buf, 1, MPI_INT, 1, 0, MPI_COMM_WORLD, &q);",
                      # MPI 4.0: Open MPI 4.1's header does not declare it
                      "int MPI_Allreduce_init(const void *, void *, int, MPI_Datatype, MPI_Op, MPI_Comm, MPI_Info, "
                      "MPI_Request *); MPI_Request q; "
                      "MPI_Allreduce_init(&buf, &x, 1, MPI_INT, MPI_SUM, MPI_COMM_WORLD, MPI_INFO_NULL, &q);",
                      "MPI_Buffer_attach(&buf, sizeof buf);",
                      "MPI_Win w; MPI_Win_create(&buf, sizeof buf, 1, MPI_INFO_NULL, MPI_COMM_WORLD, &w);"):
            m = analyse_c("""
int main(int argc, char **argv) {
  int buf = 0, x = 0;
  MPI_Init(&argc, &argv);
  %s
  MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  MPI_Barrier(MPI_COMM_WORLD);
  MPI_Finalize();
  return x;
}""" % setup)
            e = entry(m, sites(m, "MPI_Recv")[0])
            self.assertEqual(e["free"], [], setup)
            self.assertIn("escapes", e["why"], setup)

    def test_global_and_heap_buffers_fall_back(self):
        m = analyse_c("""
int g;
int main(int argc, char **argv) {
  int *h = malloc(sizeof(int));
  MPI_Init(&argc, &argv);
  MPI_Recv(&g, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  MPI_Recv(h, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  MPI_Recv(MPI_BOTTOM, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  MPI_Finalize();
  return 0;
}""")
        whys = [entry(m, r)["why"] for r in sites(m, "MPI_Recv")]
        self.assertIn("global", whys[0])
        self.assertIn("pointer", whys[1])
        self.assertIn("constant", whys[2])

    def test_calls_of_a_helper(self):
        """A helper without access to the buffer: its MPI calls are free (and the read of the
        buffer in main's return comes after MPI_Finalize)."""
        m = analyse_c("""
static void step(void) { MPI_Barrier(MPI_COMM_WORLD); }
int main(int argc, char **argv) {
  int buf = 0;
  MPI_Init(&argc, &argv);
  MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  step();
  MPI_Finalize();
  return buf;
}""")
        r = entry(m, sites(m, "MPI_Recv")[0])
        self.assertEqual(set(r["free"]), {sites(m, "MPI_Barrier")[0], sites(m, "MPI_Finalize")[0]})

    def test_helper_called_twice(self):
        """Context-insensitive returns: a helper called before and after the read has its calls
        reached after the read, so none of them is free."""
        m = analyse_c("""
static void step(void) { MPI_Barrier(MPI_COMM_WORLD); }
int main(int argc, char **argv) {
  int buf = 0, y;
  MPI_Init(&argc, &argv);
  MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  step();
  y = buf;
  step();
  MPI_Finalize();
  return y;
}""")
        r = entry(m, sites(m, "MPI_Recv")[0])
        self.assertEqual(r["free"], [])

    def test_callbacks_reach_address_taken_functions(self):
        """A function whose address is taken may run from inside any call into code outside the
        program: its MPI calls are reachable after every such call, here after the read."""
        m = analyse_c("""
static void cb(void) { MPI_Barrier(MPI_COMM_WORLD); }
int main(int argc, char **argv) {
  int buf = 0, y;
  MPI_Init(&argc, &argv);
  atexit(cb);
  MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  y = buf;
  printf("%d\\n", y);
  MPI_Finalize();
  return 0;
}""")
        r = entry(m, sites(m, "MPI_Recv")[0])
        self.assertNotIn(sites(m, "MPI_Barrier")[0], r["free"])
        self.assertNotIn(sites(m, "MPI_Finalize")[0], r["free"])

    def test_nonlocal_jumps_fall_back(self):
        m = analyse_c("""
#include <setjmp.h>
jmp_buf env;
int main(int argc, char **argv) {
  int buf = 0;
  MPI_Init(&argc, &argv);
  if (setjmp(env)) { MPI_Finalize(); return 1; }
  MPI_Recv(&buf, 1, MPI_INT, MPI_ANY_SOURCE, 0, MPI_COMM_WORLD, MPI_STATUS_IGNORE);
  MPI_Finalize();
  return 0;
}""")
        e = entry(m, sites(m, "MPI_Recv")[0])
        self.assertEqual(e["free"], [])
        self.assertIn("non-locally", e["why"])


IR = r'''
%struct.S = type { i32, i32 }
@g = global ptr @f, align 8

define dso_local i32 @f(ptr noundef %0) #0 !dbg !5 {
  %2 = alloca i32, align 4
  %3 = alloca %struct.S, align 4
  %4 = getelementptr inbounds %struct.S, ptr %3, i32 0, i32 1
  %5 = call i32 @MPI_Recv(ptr noundef %2, i32 1, ptr @t, i32 -1, i32 0, ptr @c, ptr noundef %3), !dbg !9
  br label %6

6:                                                ; preds = %1
  %7 = load i32, ptr %4, align 4, !dbg !10
  switch i32 %7, label %8 [
    i32 0, label %9
  ]

8:
  %10 = call i32 @MPI_Barrier(ptr @c), !dbg !11
  br label %9

9:
  ret i32 0
}

declare i32 @MPI_Recv(ptr, i32, ptr, i32, i32, ptr, ptr)
declare i32 @MPI_Barrier(ptr)

!3 = !DIFile(filename: "a.c", directory: "/src")
!5 = distinct !DISubprogram(name: "f", scope: !3, file: !3, line: 1, unit: !2)
!9 = !DILocation(line: 4, column: 3, scope: !5)
!10 = !DILocation(line: 5, column: 7, scope: !5)
!11 = !DILocation(line: 6, column: 5, scope: !5)
'''


class IrReader(unittest.TestCase):
    def test_reader(self):
        m = ir.parse(IR, "a.ll")
        self.assertIn("%struct.S", m.types)
        self.assertEqual([f.name for f in m.functions], ["@f"])
        f = m.functions[0]
        self.assertEqual([b.name for b in f.blocks], ["<entry>", "6", "8", "9"])
        self.assertEqual(f.blocks[1].insts[1].op, "switch")
        self.assertEqual(f.blocks[1].insts[1].succs, ["8", "9"])
        call = f.blocks[0].insts[3]
        self.assertEqual((call.callee, len(call.args), call.result), ("@MPI_Recv", 7, "%5"))
        self.assertEqual(m.location(call.dbg), ("/src/a.c", 4, 3, "f"))
        self.assertIn("@f", m.global_refs)

    def test_status_field_read_through_a_gep(self):
        """The status is read through a pointer to one of its fields, computed before the call:
        the barrier lies after that read."""
        res = analyse_ir([("a.ll", IR)])
        recv = next(s["id"] for s in res["sites"] if s["call"] == "MPI_Recv")
        e = next(r for r in res["results"] if r["complete"] == recv)
        self.assertEqual(e["free"], [])


if __name__ == "__main__":
    unittest.main()
