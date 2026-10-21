"""Level L2 by re-execution (stretto.rerun), analysis side. The ranks are small Python programs
run by FakeShim, which behaves as rerun/libstretto-rerun.so does: it serves results from the
script, writes every call in the shim's record format and stops where the script ends. Traces
are built in memory, with payloads, as in test_checker; the real shim runs in the experiments."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "experiments", "mbi"))

from stretto import dsl, explore, rerun, trace as T  # noqa: E402
from stretto.dsl import ANY, Lazy  # noqa: E402
from stretto.report import analyze, skeleton_divergence  # noqa: E402
import idioms_dsl as I  # noqa: E402
import twins  # noqa: E402


def hexint(v):
    return int(v).to_bytes(4, "little", signed=True).hex()


# ---------------------------------------------------------------- traces with payloads

def mk(raw_ranks, finalize=None):
    """A trace from raw records; every rank gets init and, unless finalize says otherwise
    for it, a finalize record."""
    t = T.Trace()
    np = len(raw_ranks)
    for rank in raw_ranks:
        end = [{"op": "finalize"}] if (finalize is None or rank in finalize) else []
        raw_ranks[rank] = [{"op": "init", "np": np}] + raw_ranks[rank] + end
    T._build(t, raw_ranks)
    return t


def send(dst, tag, value, mode="std", req=None):
    d = {"op": "send", "mode": mode, "dst": dst, "tag": tag, "comm": 0, "count": 1, "tsize": 4, "data": hexint(value)}
    if req is not None:
        d["req"] = req
    return d


def recv(src, tag, msrc, mtag):
    d = {"op": "recv", "src": -1 if src == ANY else src, "tag": -1 if tag == ANY else tag, "comm": 0,
         "count": 1, "tsize": 4, "msrc": msrc, "mtag": mtag, "mcount": 1}
    if src == ANY:
        d["anysrc"] = 1
    if tag == ANY:
        d["anytag"] = 1
    return d


def irecv(src, tag, req):
    d = recv(src, tag, None, None)
    for k in ("msrc", "mtag", "mcount"):
        del d[k]
    d.update(op="irecv", req=req)
    return d


def wait(req, msrc=None, mtag=None):
    done = {"req": req}
    if msrc is not None:
        done.update(msrc=msrc, mtag=mtag, mcount=1)
    return {"op": "wait", "kind": "wait", "reqs": [req], "done": [done]}


def bcast(root, din=None, dout=None):
    d = {"op": "coll", "name": "bcast", "comm": 0, "root": root, "count": 1, "tsize": 4}
    if din is not None:
        d["din"] = hexint(din)
    if dout is not None:
        d["dout"] = hexint(dout)
    return d


# ---------------------------------------------------------------- the shim, in Python

class _Stop(Exception):
    pass


class _Exit(Exception):
    def __init__(self, rc):
        self.rc = rc


class FakeShim:
    """What rerun.c does for a rank written in Python."""

    def __init__(self, rank, np, script):
        self.rank, self.np = rank, np
        self.results = [line.split() for line in script if not line.startswith("C ")]
        self.colls = [line.split() for line in script if line.startswith("C ")]
        self.log, self.h, self.ires, self.icoll, self.next_req, self.reqs = [], 0, 0, 0, 0, {}

    def _rec(self, op, **kw):
        self.log.append(json.dumps({"h": self.h, "op": op, **kw}))

    def _envelope(self, src, tag):
        d = {"src": -1, "anysrc": 1} if src == ANY else {"src": src}
        d.update({"tag": -1, "anytag": 1} if tag == ANY else {"tag": tag})
        return d

    def _take(self, kind):
        if self.ires >= len(self.results):
            self._rec("stop")
            raise _Stop()
        r = self.results[self.ires]
        self.ires += 1
        assert r[0] == kind, (r, kind)
        return r

    @staticmethod
    def _msg(r):
        _, src, tag, _, data = r
        return int(src), int(tag), None if data == "-" else int.from_bytes(bytes.fromhex(data), "little", signed=True)

    def send(self, dst, tag, value, mode="std"):
        self._rec("send", mode=mode, dst=dst, tag=tag, comm=0, count=1, tsize=4, data=hexint(value))

    def isend(self, dst, tag, value):
        q = self.next_req
        self.next_req += 1
        self.reqs[q] = "send"
        self._rec("send", mode="isend", dst=dst, tag=tag, comm=0, count=1, tsize=4, req=q, data=hexint(value))
        return q

    def recv(self, src, tag):
        self._rec("recv", **self._envelope(src, tag), comm=0, count=1, tsize=4)
        r = self._take("R")
        self.h += 1
        return self._msg(r)

    def irecv(self, src, tag):
        q = self.next_req
        self.next_req += 1
        self.reqs[q] = "recv"
        self._rec("irecv", **self._envelope(src, tag), comm=0, count=1, tsize=4, req=q)
        return q

    def wait(self, q):
        self._rec("wait", kind="wait", reqs=[q])
        if self.reqs.pop(q) == "recv":
            r = self._take("R")
            self.h += 1
            return self._msg(r)
        return None

    def bcast(self, value, root):
        self._rec("coll", name="bcast", comm=0, root=root, count=1, tsize=4)
        if self.icoll >= len(self.colls):
            self._rec("unknown", call="MPI_Bcast", why="more collectives than the recorded run has")
            raise _Stop()
        _, name, croot, din, dout = self.colls[self.icoll]
        self.icoll += 1
        if name != "bcast" or int(croot) != root:
            self._rec("unknown", call="MPI_Bcast", why="another collective")
            raise _Stop()
        if self.rank == root:
            if din != hexint(value):
                self._rec("unknown", call="MPI_Bcast", why="the contribution differs")
                raise _Stop()
            return value
        return int.from_bytes(bytes.fromhex(dout), "little", signed=True)

    def iprobe(self):
        self._rec("unknown", call="MPI_Iprobe", why="not served by the re-execution shim")
        raise _Stop()

    def abort(self):                 # abort() from the C library: SIGABRT, no record
        raise _Exit(-6)

    def finalize(self):
        self._rec("finalize")


def runner(programs, np):
    """A rerun.Runner over Python rank programs; .calls lists every (rank, script) run."""
    def run(rank, script):
        run.calls.append((rank, tuple(script)))
        shim = FakeShim(rank, np, script)
        rc = 0
        try:
            programs[rank](shim)
        except _Stop:
            pass
        except _Exit as e:
            rc = e.rc
        return rerun.parse_log(shim.log, rc)
    run.calls = []
    return run


# ---------------------------------------------------------------- the programs

def master01(mpi):                    # idiom 01: the reply goes to MPI_SOURCE
    for i in range(2):
        src, _, _ = mpi.recv(ANY, 7)
        mpi.send(src, 7, 100 + i)
    mpi.finalize()


def worker01(mpi):
    mpi.send(0, 7, 10 * mpi.rank)
    mpi.recv(0, 7)
    mpi.finalize()


def trace01(data=True):
    t = mk({0: [recv(ANY, 7, 1, 7), send(1, 7, 100), recv(ANY, 7, 2, 7), send(2, 7, 101)],
            1: [send(0, 7, 10), recv(0, 7, 0, 7)],
            2: [send(0, 7, 20), recv(0, 7, 0, 7)]})
    if not data:
        for s in t.sends.values():
            s.data = None
    return t


PROGRAMS01 = {0: master01, 1: worker01, 2: worker01}
STEAL01 = ((0, 0), (2, 0))            # the master's first receive takes rank 2's result


def master06(mpi):                    # idiom 06: the collective depends on MPI_SOURCE
    src, _, _ = mpi.recv(ANY, 6)
    if src == 1:
        mpi.recv(2, 6)
        mpi.bcast(42, 0)
    else:
        mpi.send(src, 6, 0)
    mpi.finalize()


def worker06(mpi):
    mpi.send(0, 6, mpi.rank)
    mpi.bcast(None, 0)
    mpi.finalize()


def trace06():
    return mk({0: [recv(ANY, 6, 1, 6), recv(2, 6, 2, 6), bcast(0, din=42)],
               1: [send(0, 6, 1), bcast(0, dout=42)],
               2: [send(0, 6, 2), bcast(0, dout=42)]})


def mbi_master(mpi):                  # MBI MessageRace_Recv_Send_nok: abort unless the last payload is 3
    v = None
    for _ in range(3):
        _, _, v = mpi.recv(ANY, ANY)
    if v != 3:
        mpi.abort()
    mpi.finalize()


def mbi_sender(mpi):
    mpi.send(0, 42, mpi.rank)
    mpi.finalize()


def trace_mbi(order=(1, 2, 3), finalize=None):
    return mk({0: [recv(ANY, ANY, s, 42) for s in order],
               1: [send(0, 42, 1)], 2: [send(0, 42, 2)], 3: [send(0, 42, 3)]}, finalize=finalize)


PROGRAMS_MBI = {0: mbi_master, 1: mbi_sender, 2: mbi_sender, 3: mbi_sender}


def outcomes(prog, t, force, B):
    return explore.explore(prog, B, force=force, comms=t.comm_members).outcomes_with(*next(iter(force.items())))


UNKNOWN = (("unknown",), False)


def verdict(outs):
    """The kinds of the executions with the forced pair, and whether every one aborts."""
    return tuple(sorted({o.kind for o in outs})), all(o.kind == "abort" for o in outs)


# ---------------------------------------------------------------- tests

class Divergence(unittest.TestCase):

    def test_fewer_collectives_of_an_undetermined_rank_are_no_divergence(self):
        t = trace06()
        o = explore.Outcome("unknown", frozenset(), frozenset(), ncoll=((0, 0), (1, 1), (2, 1)), unknown=((0, "x"),))
        self.assertEqual(skeleton_divergence(t, o), [])
        o.unknown = ()
        self.assertEqual(skeleton_divergence(t, o), ["rank 0 issues 0 collective(s) instead of 1"])


class ParseLog(unittest.TestCase):

    def test_endings(self):
        rec = json.dumps({"h": 0, "op": "send", "mode": "std", "dst": 1, "tag": 1, "comm": 0, "count": 1, "tsize": 4})
        self.assertEqual(rerun.parse_log([rec, '{"h":0,"op":"stop"}'], 0).status, "stop")
        self.assertEqual(rerun.parse_log([rec, '{"h":0,"op":"finalize"}'], 0).status, "finalize")
        u = rerun.parse_log([rec, '{"h":0,"op":"unknown","call":"MPI_Iprobe","why":"not served"}'], 0)
        self.assertEqual((u.status, u.why), ("unknown", "MPI_Iprobe: not served"))
        self.assertEqual(rerun.parse_log([rec], -6).why, "SIGABRT")
        self.assertEqual(rerun.parse_log([rec], 3).why, "exit(3) without MPI_Finalize")
        self.assertEqual(rerun.parse_log([rec], None, timed_out=True, timeout=5).status, "unknown")
        self.assertEqual(rerun.parse_log([], 0).status, "unknown")
        self.assertEqual(rerun.parse_log([rec, '{"h":0,"op":"st'], -11).why, "SIGSEGV")


class MasterWorker(unittest.TestCase):
    """Idiom 01: the single-path deadlock at B=0 is spurious, the twin finishes."""

    def setUp(self):
        self.t = trace01()
        self.run = runner(PROGRAMS01, 3)
        self.prog = rerun.Oracle(self.t, self.run).program()

    def test_reproduces_the_hand_written_twin(self):
        rk, sk = STEAL01
        for B in (0, None):
            auto = outcomes(self.prog, self.t, {rk: sk}, B)
            hand = outcomes(I.idiom01(), self.t, {rk: sk}, B)
            self.assertEqual(verdict(auto), verdict(hand), f"B={B}")
            self.assertEqual(verdict(auto), (("done",), False))
            self.assertTrue(all(skeleton_divergence(self.t, o) for o in auto))

    def test_report(self):
        rep = analyze(self.t, twin=self.prog, twin_label="re-execution")
        l2 = {(f.cls, f.recv, f.send) for f in rep.findings if f.level == "L2"}
        sp = {(f.cls, f.recv, f.send) for f in rep.findings if f.level == "L1+SP"}
        self.assertIn(("1e",) + STEAL01, l2)
        self.assertIn(("1d",) + STEAL01, sp)               # the spurious single-path deadlock
        self.assertNotIn(("1d",) + STEAL01, l2)            # which L2 does not confirm
        self.assertTrue(any("(L2 re-execution)" in f.title for f in rep.findings))

    def test_ranks_are_validated_once_and_runs_cached(self):
        rk, sk = STEAL01
        outcomes(self.prog, self.t, {rk: sk}, 0)
        n = len(self.run.calls)
        outcomes(self.prog, self.t, {rk: sk}, None)
        self.assertEqual(len(self.run.calls), n)             # B=inf reuses every run of B=0
        full = (("R", 1, 7, 4, hexint(10)),), (("R", 2, 7, 4, hexint(20)),)
        self.assertIn((0, full), self.prog.oracle.runs)      # the validation run of the master

    def test_only_the_changed_rank(self):
        """live={0}: the workers get other payloads than recorded and are not rerun, so no
        execution is classified; the departure of the master's reply stands."""
        prog = rerun.Oracle(self.t, self.run, live={0}).program()
        rk, sk = STEAL01
        for B in (0, None):
            outs = outcomes(prog, self.t, {rk: sk}, B)
            self.assertEqual(verdict(outs), UNKNOWN)
            self.assertTrue(all(skeleton_divergence(self.t, o) for o in outs))
        rep = analyze(self.t, twin=prog, twin_label="re-execution")
        self.assertFalse(any(f.level == "L2" and f.cls == "1d" for f in rep.findings))
        self.assertFalse(any("dismissed" in d for f in rep.findings for d in f.detail))

    def test_without_payloads_nothing_beyond_the_trace(self):
        t = trace01(data=False)
        prog = rerun.Oracle(t, runner(PROGRAMS01, 3)).program()
        outs = outcomes(prog, t, dict([STEAL01]), None)
        self.assertEqual(verdict(outs), UNKNOWN)
        self.assertIn("payload", " ".join(w for o in outs for _, w in o.unknown))


class CollectiveAfterAnySource(unittest.TestCase):
    """Idiom 06: the master skips the Bcast; the workers hang in it."""

    def test_reproduces_the_hand_written_twin(self):
        t = trace06()
        prog = rerun.Oracle(t, runner({0: master06, 1: worker06, 2: worker06}, 3)).program()
        rk, sk = (0, 0), (2, 0)
        for B in (0, None):
            auto = outcomes(prog, t, {rk: sk}, B)
            hand = outcomes(I.idiom06(), t, {rk: sk}, B)
            self.assertEqual(verdict(auto), verdict(hand), f"B={B}")
            self.assertEqual(verdict(auto), (("deadlock",), False))
            div = {d for o in auto for d in skeleton_divergence(t, o)}
            self.assertIn("rank 0 issues an extra send #0 to 2 tag 6", div)
        rep = analyze(t, twin=prog, twin_label="re-execution")
        self.assertTrue(any(f.level == "L2" and f.cls == "1d" and f.recv == rk for f in rep.findings))

    def test_a_changed_contribution_is_not_served(self):
        def master(mpi):
            src, _, _ = mpi.recv(ANY, 6)
            if src == 1:
                mpi.recv(2, 6)
            mpi.bcast(42 if src == 1 else 7, 0)      # the root broadcasts something else
            mpi.finalize()
        t = trace06()
        prog = rerun.Oracle(t, runner({0: master, 1: worker06, 2: worker06}, 3)).program()
        outs = outcomes(prog, t, {(0, 0): (2, 0)}, None)
        self.assertEqual(verdict(outs), UNKNOWN)


class PayloadCheck(unittest.TestCase):
    """The E7 programs: the automatic skeleton gives the hand-written twin's verdict on every
    L1 alternative, without the twin's assumption that the payload is the sender's rank."""

    def check(self, t):
        prog = rerun.Oracle(t, runner(PROGRAMS_MBI, 4)).program()
        hand = twins.payload_check_twin(4, "Recv", "Send", 42)
        rep = analyze(t, with_sp=False)
        alts = [(f.recv, f.send) for f in rep.findings if f.level == "L1" and f.recv is not None]
        self.assertTrue(alts)
        seen = set()
        for rk, sk in alts:
            for B in (0, None):
                a, h = verdict(outcomes(prog, t, {rk: sk}, B)), verdict(outcomes(hand, t, {rk: sk}, B))
                self.assertEqual(a, h, f"{rk} <- {sk} at B={B}")
                seen.add(a)
        return seen

    def test_finished_run(self):
        seen = self.check(trace_mbi((1, 2, 3)))
        self.assertIn((("abort",), True), seen)

    def test_aborted_run(self):
        """The recorded run aborted (the master's trace stops after its receives) and rank 2
        was killed before MPI_Finalize: the ends of both traces come from re-executions, and
        rank 2's send is issued once."""
        t = trace_mbi((1, 3, 2), finalize={1, 3})
        self.check(t)
        prog = rerun.Oracle(t, runner(PROGRAMS_MBI, 4)).program()
        res = explore.explore(prog, None, comms=t.comm_members)
        self.assertEqual(res.kinds(), {"abort", "done"})
        for o in res.outcomes:
            self.assertEqual(sum(1 for key, *_ in o.sent if key[0] == 2), 1)


class Reproduction(unittest.TestCase):
    """A rank whose re-execution does not repeat its trace is not trusted."""

    def test_validation_fails(self):
        def master(mpi):
            for i in range(2):
                src, _, _ = mpi.recv(ANY, 7)
                mpi.send(src, 7, 999)                  # not the recorded payload
            mpi.finalize()
        t = trace01()
        oracle = rerun.Oracle(t, runner({**PROGRAMS01, 0: master}, 3))
        outs = outcomes(oracle.program(), t, dict([STEAL01]), None)
        self.assertEqual(verdict(outs), UNKNOWN)
        self.assertIn("payload", oracle.unreliable[0])

    def test_runs_that_disagree(self):
        count = [0]

        def master(mpi):                               # the first receive's tag changes after one run
            count[0] += 1
            for i in range(2):
                src, _, _ = mpi.recv(ANY, 7 if count[0] == 1 or i else 8)
                mpi.send(src, 7, 100 + i)
            mpi.finalize()
        t = trace01()
        oracle = rerun.Oracle(t, runner({**PROGRAMS01, 0: master}, 3))
        outs = outcomes(oracle.program(), t, dict([STEAL01]), None)
        self.assertEqual(verdict(outs), UNKNOWN)
        self.assertIn("disagree", oracle.unreliable[0])

    def test_a_call_the_shim_does_not_serve(self):
        def master(mpi):
            for i in range(2):
                src, _, _ = mpi.recv(ANY, 7)
                if src == 2 and i == 0:
                    mpi.iprobe()
                mpi.send(src, 7, 100 + i)
            mpi.finalize()
        t = trace01()
        outs = outcomes(rerun.Oracle(t, runner({**PROGRAMS01, 0: master}, 3)).program(), t, dict([STEAL01]), None)
        self.assertEqual(verdict(outs), UNKNOWN)
        self.assertIn("MPI_Iprobe", " ".join(w for o in outs for _, w in o.unknown))


class TracePath(unittest.TestCase):
    """Unchanged ranks follow their trace: no run for them, and the same ops as the
    single-path program."""

    def test_the_recorded_order_needs_no_run(self):
        t = trace01()
        run = runner(PROGRAMS01, 3)
        prog = rerun.Oracle(t, run).program()
        self.assertEqual(prog[1][0], dsl.Send(0, 7, 4, 0, None, "std", hexint(10)))
        self.assertEqual(prog[1][1], dsl.Recv(0, 7, 4, 0, None, "m0"))
        self.assertIsInstance(prog[1][2], Lazy)
        res = explore.explore(prog, None, force=dict(t.observed), comms=t.comm_members)
        self.assertEqual([o.matching for o in res.outcomes], [frozenset(t.observed.items())])
        self.assertEqual(res.kinds(), {"done"})
        self.assertEqual(run.calls, [])

    def test_every_order_finishes(self):
        t = trace01()
        res = explore.explore(rerun.Oracle(t, runner(PROGRAMS01, 3)).program(), 0, comms=t.comm_members)
        self.assertEqual(res.kinds(), {"done"})
        self.assertEqual(len({o.matching for o in res.outcomes}), 2)


if __name__ == "__main__":
    unittest.main()
