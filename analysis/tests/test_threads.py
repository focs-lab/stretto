"""Several threads calling MPI (docs/threads.md): ranks split into chains ordered by recorded
happens-before, the alternatives thread scheduling produces, rule 8 of the horizon, the observed
thread findings, and that a trace whose ranks have one chain is analysed as before. Traces are
built in memory from raw records in the tracer's format (version 3), as in test_checker."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stretto import threads as TH, trace as T
from stretto.feasible import check
from stretto.horizon import compute_cut
from stretto.matching import candidates_full, probe_candidates
from stretto.report import analyze, format_report
from test_checker import coll, irecv, mk, recv, send, wait


def th(tid, rec, label=None, after=()):
    """A record of chain tid, with a label other records' after can name."""
    d = dict(rec)
    if tid:
        d["tid"] = tid
    if label is not None:
        d["id"] = label
    if after:
        d["after"] = list(after)
    return d


def probe(src, tag, msrc, mtag, kind="probe"):
    d = {"op": "probe", "kind": kind, "src": 0 if src == T.ANY else src, "tag": 0 if tag == T.ANY else tag,
         "comm": 0, "flag": 1, "msrc": msrc, "mtag": mtag}
    if src == T.ANY:
        d["anysrc"] = 1
    if tag == T.ANY:
        d["anytag"] = 1
    return d


def mkt(ranks, level=3, issue=True, np=None):
    """A version-3 trace with the tracer's thread support: each rank's records in file order
    (which is also the order of the postings, iseq, when issue); after names labels."""
    t = T.Trace()
    np = np or len(ranks)
    raw, labels = {}, {}
    for rank, recs in ranks.items():
        out = [{"op": "init", "np": np, "version": 3, "thread": level, **({"issue": 1} if issue else {})}]
        out += [dict(r) for r in recs] + [{"op": "finalize"}]
        iseq = 0
        for q, r in enumerate(out):
            r["seq"] = q
            if "id" in r:
                labels[(rank, r.pop("id"))] = [r.get("tid", 0), q]
            if issue and (r["op"] in ("send", "recv", "irecv") or (r["op"] == "probe" and r.get("flag", 1))):
                r["iseq"] = iseq
                iseq += 1
        raw[rank] = out
    for rank, out in raw.items():
        for r in out:
            if "after" in r:
                r["after"] = [labels[(rank, x)] if isinstance(x, str) else labels[(rank, x[0])] + list(x[1:])
                              for x in r["after"]]
    T._build(t, raw)
    return t


def alts_of(t):
    alts, _ = candidates_full(t)
    return {(a.kind, a.recv, a.send) for a in alts}, alts


def accepted(t, alt, B=None):
    return check(t, alt.M, alt.changed, B, "L1").feasible


class C_SingleChain(unittest.TestCase):
    def test_version_3_without_threads_reports_as_before(self):
        """A trace of one thread per rank in format version 3 is the trace it was."""
        recs = {0: [send(1, 1), send(1, 2)], 1: [recv(T.ANY, T.ANY, 0, 1), recv(T.ANY, T.ANY, 0, 2)]}
        old = mk({r: [dict(x) for x in v] for r, v in recs.items()}, version=2)
        new = mkt({r: [dict(x) for x in v] for r, v in recs.items()})
        self.assertFalse(new.multi)
        self.assertEqual(sorted(new.ranks), sorted(old.ranks))
        self.assertEqual(old.observed, new.observed)
        self.assertEqual(format_report(old, analyze(old, with_sp=False)), format_report(new, analyze(new, with_sp=False)))


class C_Chains(unittest.TestCase):
    def two_receivers(self, ordered=False):
        """Rank 0 sends two messages with one tag; rank 1's two threads receive one each."""
        return mkt({0: [send(1, 0), send(1, 0)],
                    1: [th(0, recv(0, 0, 0, 0), "a"),
                        th(1, recv(0, 0, 0, 0), after=["a"] if ordered else ())]})

    def test_split(self):
        t = self.two_receivers()
        self.assertTrue(t.multi)
        self.assertEqual(len(t.chains[1]), 2)
        b = t.chains[1][1]
        self.assertEqual(t.proc[b], 1)
        self.assertEqual(str(b), "1.1")
        rb = t.recvs[(b, 0)]
        self.assertEqual(rb.proc, 1)
        self.assertTrue(TH.concurrent(t, t.recvs[(1, 0)], rb))

    def test_after_orders_the_chains(self):
        t = self.two_receivers(ordered=True)
        b = t.chains[1][1]
        self.assertTrue(TH.before(t, t.recvs[(1, 0)], t.recvs[(b, 0)]))
        self.assertFalse(TH.concurrent(t, t.recvs[(1, 0)], t.recvs[(b, 0)]))

    def test_concurrent_receivers_swap(self):
        t = self.two_receivers()
        kinds, alts = alts_of(t)
        swaps = [a for a in alts if a.kind == "swap"]
        self.assertEqual(len(swaps), 1, kinds)
        self.assertTrue(accepted(t, swaps[0]))
        self.assertTrue(accepted(t, swaps[0], B=0))

    def test_ordered_receivers_do_not_race(self):
        t = self.two_receivers(ordered=True)
        _, alts = alts_of(t)
        self.assertEqual([a for a in alts if accepted(t, a)], [])

    def test_two_sender_threads(self):
        """Rank 0's two threads send to rank 1, which receives twice from rank 0."""
        t = mkt({0: [th(0, send(1, 0)), th(1, send(1, 0))],
                 1: [recv(0, 0, 0, 0), recv(0, 0, 0, 0)]})
        _, alts = alts_of(t)
        ok = [a for a in alts if accepted(t, a)]
        self.assertTrue(any(a.recv == (1, 0) and a.send[0] != 0 for a in ok), [a.describe(t) for a in alts])

    def test_ordered_senders(self):
        t = mkt({0: [th(0, send(1, 0), "s"), th(1, send(1, 0), after=["s"])],
                 1: [recv(0, 0, 0, 0), recv(0, 0, 0, 0)]})
        _, alts = alts_of(t)
        self.assertEqual([a for a in alts if accepted(t, a)], [])

    def test_probe_split_truncates(self):
        """Thread A probes a message of rank 0 and receives it with the probed size; thread B's
        receive takes it first in the alternative, and A's receive gets the longer one."""
        t = mkt({0: [send(1, 0, count=2), send(1, 0, count=100)],
                 1: [th(0, probe(T.ANY, 0, 0, 0)), th(0, recv(0, 0, 0, 0, count=2)),
                     th(1, recv(T.ANY, 0, 0, 0, count=100))]})
        rep = analyze(t, with_sp=False)
        self.assertIn("1a", {f.cls for f in rep.findings}, format_report(t, rep))

    def test_probe_split_hangs(self):
        """As above, but rank 0 sends one message; thread B's receive accepts any source and took
        rank 2's in the run. Taking rank 0's leaves A's receive with nothing: a contained deadlock."""
        t = mkt({0: [send(1, 0)],
                 1: [th(0, probe(0, 0, 0, 0)), th(0, recv(0, 0, 0, 0), "ra"),
                     th(1, recv(T.ANY, 0, 2, 0), "rb")],
                 2: [send(1, 0)]})
        rep = analyze(t, with_sp=False)
        self.assertIn("1d", {f.cls for f in rep.findings}, format_report(t, rep))

    def test_mprobe_does_not_split(self):
        t = mkt({0: [send(1, 0)],
                 1: [th(0, probe(0, 0, 0, 0, kind="mprobe")), th(0, dict(recv(0, 0, 0, 0), mrecv=1)),
                     th(1, recv(T.ANY, 0, 2, 0))],
                 2: [send(1, 0)]})
        rep = analyze(t, with_sp=False)
        self.assertNotIn("1d", {f.cls for f in rep.findings}, format_report(t, rep))
        self.assertNotIn("1a", {f.cls for f in rep.findings}, format_report(t, rep))

    def test_probe_alternative_across_chains(self):
        """Both threads probe rank 0's messages: one could report the other's message."""
        t = mkt({0: [th(0, send(1, 0)), th(1, send(1, 1))],
                 1: [th(0, probe(0, T.ANY, 0, 0)), th(0, recv(0, 0, 0, 0)),
                     th(1, probe(0, T.ANY, 0, 1)), th(1, recv(0, 1, 0, 1))]})
        alts = probe_candidates(t)
        self.assertTrue(alts)


class C_Rule8(unittest.TestCase):
    def test_a_release_after_a_changed_receive_is_not_in_the_horizon(self):
        """Chain A takes a message, then releases a lock that chain B acquires before sending.
        When A's receive changes, B's send depends on A's unknown continuation."""
        t = mkt({0: [th(0, recv(T.ANY, 0, 2, 0), "ra"), th(1, send(3, 5), after=["ra"])],
                 1: [send(0, 0)], 2: [send(0, 0)], 3: [recv(0, 5, 0, 5)]})
        r = t.recvs[(0, 0)]
        s1 = next(s for s in t.sends.values() if s.proc == 1)
        M = dict(t.observed)
        M[r.key] = s1.key
        cut = compute_cut(t, M, {r.key}, None)
        b = t.chains[0][1]
        self.assertEqual(cut[b], 0)               # B waits for A before its send


    @staticmethod
    def gap_trace(ordinals: bool):
        """The main thread creates A and B, then joins A and B, with no MPI call between: the
        four synchronisation points share one gap of its records. A and B each receive one of
        rank 1's two messages."""
        k = (lambda *v: v) if ordinals else (lambda *v: ())
        return mkt({0: [th(0, send(2, 9), "m"),
                        th(1, recv(1, 0, 1, 0), "ra", after=[("m",) + k(1, 0)]),
                        th(2, recv(1, 0, 1, 0), "rb", after=[("m",) + k(2, 0)]),
                        th(0, send(2, 8),
                           after=[("ra",) + k(1, 3), ("rb",) + k(1, 4)])],
                    1: [send(0, 0), send(0, 0)], 2: [recv(0, 9, 0, 9), recv(0, 8, 0, 8)]})

    def test_creations_before_a_blocked_join_are_reached(self):
        t = C_Rule8.gap_trace(True)
        swaps = [a for a in candidates_full(t)[0] if a.kind == "swap"]
        self.assertEqual(len(swaps), 1)
        self.assertTrue(accepted(t, swaps[0]))

    def test_without_ordinals_the_gap_is_conservative(self):
        """Without the ordinals the second creation cannot be placed before the first join: the
        thread it creates is left out, and the swap's witness with it."""
        t = C_Rule8.gap_trace(False)
        swaps = [a for a in candidates_full(t)[0] if a.kind == "swap"]
        self.assertEqual(len(swaps), 1)
        self.assertFalse(accepted(t, swaps[0]))

class C_RequestReuse(unittest.TestCase):
    def test_idiom_46_requests_stay_with_their_threads(self):
        """Idiom 46 (both MPIs): two threads complete their Isends through copies of the handle,
        which the library shares or reuses between them. Before 30 Oct 2026 the tracer credited
        one thread's completion with the other's request in every run (threads-handoff)."""
        golden = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")
        for mpi in ("openmpi", "mpich"):
            d = os.path.join(golden, mpi)
            name = next((x for x in sorted(os.listdir(d)) if x.startswith("46_")), None) if os.path.isdir(d) else None
            if name is None:
                self.skipTest(f"no golden trace of idiom 46 for {mpi}")
            t = T.load(os.path.join(d, name))
            sends = [s for s in t.sends.values() if s.proc == 0]
            self.assertEqual(len(sends), 200, mpi)
            self.assertTrue(all(s.completed and s.complete_chain is None for s in sends), mpi)
            rep = analyze(t, with_sp=False)
            self.assertEqual((rep.status(), rep.findings), ("complete", []), f"{mpi}: " + format_report(t, rep))


class C_Observed(unittest.TestCase):
    def test_concurrent_collectives(self):
        t = mkt({0: [th(0, coll("bcast", 0)), th(1, coll("allreduce"))],
                 1: [coll("bcast", 0), coll("allreduce")]})
        rep = analyze(t, with_sp=False)
        self.assertIn("2-thread-collective", {f.cls for f in rep.findings}, format_report(t, rep))

    def test_ordered_collectives(self):
        t = mkt({0: [th(0, coll("bcast", 0), "b"), th(1, coll("allreduce"), after=["b"])],
                 1: [coll("bcast", 0), coll("allreduce")]})
        rep = analyze(t, with_sp=False)
        self.assertNotIn("2-thread-collective", {f.cls for f in rep.findings}, format_report(t, rep))

    def test_finalize_before_a_thread_ends(self):
        t = mkt({0: [th(1, send(1, 0))], 1: [recv(0, 0, 0, 0)]})
        rep = analyze(t, with_sp=False)
        self.assertIn("2-thread-finalize", {f.cls for f in rep.findings}, format_report(t, rep))

    def test_funneled_violation(self):
        t = mkt({0: [th(1, send(1, 0), "s")], 1: [recv(0, 0, 0, 0)]}, level=1, issue=False)
        rep = analyze(t, with_sp=False)
        self.assertIn("2-thread-level", {f.cls for f in rep.findings}, format_report(t, rep))


GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


class C_Tasks(unittest.TestCase):
    """Explicit OpenMP tasks as chains (docs/threads.md, section 12): the traced idioms 34-36 of
    both MPIs, and the record of a task the tracer does not follow."""

    def golden(self, mpi, n):
        d = os.path.join(GOLDEN, mpi)
        name = next((x for x in sorted(os.listdir(d)) if x.startswith(f"{n}_")), None) if os.path.isdir(d) else None
        if name is None:
            self.skipTest(f"no golden trace of idiom {n} for {mpi}")
        return T.load(os.path.join(d, name))

    def test_unordered_tasks_swap(self):
        """Idiom 34: two tasks without a dependence receive on one tag."""
        for mpi in ("openmpi", "mpich"):
            t = self.golden(mpi, 34)
            self.assertEqual(t.thread_limits, [])
            tasks = [c for c in t.chains[0] if c != 0]
            self.assertEqual(len(tasks), 2)
            self.assertTrue(all("~" in t.chain_name[c] for c in tasks), t.chain_name)
            self.assertTrue(TH.concurrent(t, t.recvs[(tasks[0], 0)], t.recvs[(tasks[1], 0)]))
            rep = analyze(t, with_sp=False)
            # the concurrent swap (1f, its truncating half 1a) and, in some runs, a steal variant (1f)
            self.assertEqual({f.cls for f in rep.findings if f.level == "L1"}, {"1a", "1f"}, format_report(t, rep))
            self.assertTrue(any("concurrent swap" in d for f in rep.findings for d in f.detail))
            # a chain's site ids are its process's: the tasks' receives have their call sites
            for c in tasks:
                self.assertIsNotNone(t.site_of(c, t.recvs[(c, 0)].idx), f"{mpi}: chain {c}")

    def test_ordered_tasks_do_not_race(self):
        """Idioms 35 (depend), 36 (taskgroup, taskwait, team barrier, nested task): no finding."""
        for mpi in ("openmpi", "mpich"):
            for n in (35, 36):
                t = self.golden(mpi, n)
                self.assertEqual(t.thread_limits, [])
                rep = analyze(t, with_sp=False)
                self.assertEqual(rep.findings, [], f"{mpi} {n}: " + format_report(t, rep))
                recvs = [r for r in t.recvs.values() if t.proc.get(r.rank, r.rank) == 0 and r.tag == 5]
                for a in recvs:
                    for b in recvs:
                        if a is not b:
                            self.assertFalse(TH.concurrent(t, a, b), f"{mpi} {n}: {a} {b}")

    def test_lock_roles(self):
        """Idioms 44 (a pool of workers under a mutex and a condition variable) and 45 (tasks
        taking roles under a nest lock): the two receives of one tag that the lock order leaves
        concurrent swap, and the 8-byte one takes the 400 bytes (docs/threads.md, section 14)."""
        for mpi in ("openmpi", "mpich"):
            for n in (44, 45):
                t = self.golden(mpi, n)
                self.assertEqual(t.thread_limits, [])
                rep = analyze(t, with_sp=False)
                self.assertEqual({f.cls for f in rep.findings if f.level == "L1"}, {"1a", "1f"}, f"{mpi} {n}: " + format_report(t, rep))
                f = next(f for f in rep.findings if f.cls == "1a")
                self.assertIn("concurrent swap", f.title)

    def test_detached_task_degrades(self):
        t = mkt({0: [{"op": "task", "what": "detach"}, th(0, send(1, 0)), th(1, send(1, 0))],
                 1: [recv(0, 0, 0, 0), recv(0, 0, 0, 0)]})
        rep = analyze(t, with_sp=False)
        self.assertIn("threads-tasks", rep.status())
        self.assertIn("detached", format_report(t, rep))


def pool(seed, k=3, n=5, nonblocking=False):
    """The racy phase of MPICH's mt_* tests: k threads of rank 0 send n messages each with one
    tag, k threads of rank 1 receive n each (blocking, or nonblocking with a wait at the end),
    the calls of the threads interleaved at random in the order MPI saw them."""
    import random
    rnd = random.Random(seed)

    def interleave(chains):
        out, pos = [], [0] * len(chains)
        while any(p < len(c) for p, c in zip(pos, chains)):
            i = rnd.choice([i for i, c in enumerate(chains) if pos[i] < len(c)])
            out.append(chains[i][pos[i]])
            pos[i] += 1
        return out
    sends = [[th(tid, send(1, 0)) for _ in range(n)] for tid in range(k)]
    if nonblocking:
        recvs = [[th(tid, irecv(0, 0, 100 * tid + i)) for i in range(n)]
                 + [th(tid, wait(*[(100 * tid + i, 0, 0) for i in range(n)], kind="waitall"))] for tid in range(k)]
    else:
        recvs = [[th(tid, recv(0, 0, 0, 0)) for _ in range(n)] for tid in range(k)]
    return mkt({0: interleave(sends), 1: interleave(recvs)})


def thread_traces():
    """The multithreaded golden traces (idioms 15 on) and random thread pools."""
    out = []
    for mpi in ("openmpi", "mpich"):
        d = os.path.join(GOLDEN, mpi)
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else ():
            t = T.load(os.path.join(d, name))
            if t.multi:
                out.append((f"{mpi}/{name}", t))
    for seed in range(4):
        out.append((f"pool {seed}", pool(seed)))
        out.append((f"pool {seed} nonblocking", pool(seed, nonblocking=True)))
    return out


class C_Scale(unittest.TestCase):
    """What lets the L1 check finish on the 1 024-iteration mt_* traces (docs/threads.md,
    section 10) must not change a verdict: the orientation edges reduced up to reachability, by
    a walk or by bisection, against the edges written out in full; the order filter against the
    generic check; the clocks of the observed happens-before against the graph's search."""

    @classmethod
    def setUpClass(cls):
        cls.traces = thread_traces()

    def verdicts(self, t):
        from stretto.feasible import breakpoints
        from stretto.report import probe_kw
        items = [(a.M, a.changed, {}) for a in candidates_full(t)[0]]
        items += [(t.observed, set(), probe_kw(p, s)) for p, s in probe_candidates(t)]
        items.append((t.observed, set(), {}))
        out = []
        for M, changed, kw in items:
            for B in breakpoints(t) + [None]:
                for mode in ("L1", "SP"):
                    v = check(t, M, changed, B, mode, **kw)
                    out.append((v.feasible, v.ok, v.reason))
        return out

    def test_reduced_orientation_edges(self):
        try:
            for name, t in self.traces:
                TH.FULL_ORIENTATION = True
                full = self.verdicts(t)
                TH.FULL_ORIENTATION, TH.FAST_WINDOWS = False, False
                walked = self.verdicts(t)
                TH.FAST_WINDOWS = True
                fast = self.verdicts(t)
                self.assertEqual(full, walked, name)
                self.assertEqual(full, fast, name)
        finally:
            TH.FULL_ORIENTATION, TH.FAST_WINDOWS = False, True

    def test_order_filter_rejects_only_what_the_check_rejects(self):
        from stretto.feasible import breakpoints
        from stretto.report import probe_kw
        n = 0
        for name, t in self.traces:
            f = TH.OrderFilter(t)
            for a in candidates_full(t)[0]:
                if f.violation(a):
                    n += 1
                    for B in breakpoints(t) + [None]:
                        self.assertFalse(check(t, a.M, a.changed, B, "L1").feasible, (name, a.describe(t), B))
            for p, s in probe_candidates(t):
                if f.probe_violation(p, s):
                    n += 1
                    self.assertFalse(check(t, t.observed, set(), None, "L1", **probe_kw(p, s)).feasible, (name, str(p)))
        self.assertGreater(n, 0)

    def test_observed_clocks(self):
        from stretto.matching import build_graph
        for name, t in self.traces:
            g = build_graph(t, t.observed, cut=None, B=None, thread_orient=False)
            reach = TH.observed_reaches(t)
            nodes = [n for n in g.nodes if n[0] >= 0 and n[2] != "m"]
            for a in nodes[::3]:
                rs = g.reach_set(a)
                for b in g.nodes:
                    self.assertEqual(reach(a, b), b in rs, (name, a, b))


if __name__ == "__main__":
    unittest.main()
