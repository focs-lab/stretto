"""Level L2 without a hand-written twin: the program is its own skeleton.

The model's program gives each rank a function from the results its MPI calls have returned to
its next call (docs/model.md §2b). A trace records that function on the recorded results only.
This module evaluates it on other results by re-executing the rank: the binary runs alone,
without mpirun, under the re-execution shim (rerun/libstretto-rerun.so), which answers each MPI
call from a script of results and writes down every call the rank makes, with the payload of
every send. The trace supplies the recorded messages (a trace made with STRETTO_PAYLOAD), the
explorer supplies the choices.

`Oracle(t, runner).program()` is a DSL program (stretto.dsl) for stretto.explore and for
report.analyze(t, twin=..., twin_label="re-execution"). Each rank follows its recorded calls for
as long as its results are the recorded ones (the trace is a cache of the program), and is
re-executed from the first result that differs: a dsl.Lazy op at every result point compares
what the rank got with what it got in the run and computes the continuation. Where the
continuation is not determined, the rank stops at dsl.Unknown:
  - a call the shim does not serve (probes, tests, communicator creation, ...), or one that the
    trace path does not model at L2 (a probe or a test in the recorded calls);
  - a message whose payload the trace did not record, or a collective whose contribution
    differs from the recorded one (its result is then not the recorded one);
  - a rank outside `live` whose results differ (live = {changed rank} is the minimal variant:
    only the changed rank is re-executed, and every other rank is known only on its recorded
    path);
  - a rank whose re-execution does not reproduce its trace: before its first re-execution
    every rank is run with all its recorded results and must issue the recorded calls (with
    the recorded payloads) and end as the trace ends, and every later run must repeat the calls
    of the shorter histories it shares;
  - a run that times out.

Premises: (R1) a rank's calls, their arguments and the payloads it sends are a function of the
results its MPI calls return, given the same executable, arguments, input files and
environment; (R2) the shim returns what MPI would; (R3) a recorded payload is what a receive
unpacks; (R4) a collective's result depends only on the contributions its shape requires (P1).
Under them every execution of the explorer over this program is an execution of the traced
program, past the horizon too, as far as no Unknown is reached.
"""
from __future__ import annotations
import json
import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .dsl import ANY, Abort, Coll, Lazy, Meta, Recv, Send, Unknown, Wait, Waitany
from .trace import Trace, WORLD

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# A result entry, in the order a rank consumes them: ("R", src, tag, nbytes, data) a receive
# completes with this message (src a world rank, data the packed payload in hex or None), or
# ("I", index) a Waitany returns this index. A point is the entries of one call; a history the
# points of a rank's result-bearing calls, in call order.
Entry = tuple
Point = Tuple[Entry, ...]
History = Tuple[Point, ...]

_MODE = {"ssend": "ssend", "issend": "ssend", "bsend": "bsend", "ibsend": "bsend", "rsend": "rsend", "irsend": "rsend"}
_NONBLOCKING = ("isend", "issend", "ibsend", "irsend")


@dataclass
class RunLog:
    """What one re-execution did: the shim's records, and how the process ended. status is
    stop (it needs a result the script does not have), finalize, abort (MPI_Abort, a signal,
    an exit without MPI_Finalize), or unknown (a call the shim does not serve, a timeout, no
    log)."""
    records: List[dict]
    status: str
    why: str = ""
    returncode: Optional[int] = None
    stderr: str = ""


Runner = Callable[[int, List[str]], RunLog]


# ------------------------------------------------------------------ the real runner

def parse_log(lines: Sequence[str], returncode: Optional[int], timed_out: bool = False,
              timeout: float = 0, stderr: str = "") -> RunLog:
    """The shim's log and the process's exit status as a RunLog."""
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            break                       # a record cut by a crash: what follows is lost too
    last = records[-1].get("op") if records else None
    if last == "stop":
        return RunLog(records, "stop", returncode=returncode, stderr=stderr)
    if last == "unknown":
        return RunLog(records, "unknown", f"{records[-1].get('call', '?')}: {records[-1].get('why', '')}".rstrip(": "),
                      returncode, stderr)
    if last == "abort":
        return RunLog(records, "abort", records[-1].get("why", "MPI_Abort"), returncode, stderr)
    if last == "finalize":
        return RunLog(records, "finalize", returncode=returncode, stderr=stderr)
    if timed_out:
        return RunLog(records, "unknown", f"the re-execution ran longer than {timeout:g} s", returncode, stderr)
    if not records:
        return RunLog(records, "unknown", "the shim wrote no log (did the program start, was the shim preloaded?)",
                      returncode, stderr)
    if returncode is not None and returncode < 0:
        try:
            name = signal.Signals(-returncode).name
        except ValueError:
            name = f"signal {-returncode}"
        return RunLog(records, "abort", name, returncode, stderr)
    return RunLog(records, "abort", f"exit({returncode}) without MPI_Finalize", returncode, stderr)


class ShimRunner:
    """Runs one rank of the executable alone under the re-execution shim. The process gets the
    environment of the analysis without any launcher variables (it initialises MPI as a
    singleton), stdin from /dev/null, and cwd (by default a fresh empty directory, so that
    files the rank writes go nowhere; a program that reads input files by relative path needs
    the traced run's directory, where the rank's writes then land too)."""

    def __init__(self, exe: str, args: Sequence[str] = (), np: int = 1, mpi: str = "openmpi",
                 timeout: float = 20, cwd: Optional[str] = None, lib: Optional[str] = None):
        self.exe, self.args, self.np, self.mpi, self.timeout, self.cwd = os.path.abspath(exe), list(args), np, mpi, timeout, cwd
        self.lib = os.path.abspath(lib or os.path.join(ROOT, "rerun", "bin", mpi, "libstretto-rerun.so"))
        self.runs = 0
        self._setarch: Optional[List[str]] = None

    def _no_aslr(self) -> List[str]:
        """setarch -R where the kernel and the sandbox allow it (Docker's default seccomp
        profile does not): addresses then do not differ between the runs."""
        if self._setarch is None:
            self._setarch = []
            exe = shutil.which("setarch")
            if exe:
                arch = os.uname().machine
                ok = subprocess.run([exe, arch, "-R", "true"], capture_output=True).returncode == 0
                self._setarch = [exe, arch, "-R"] if ok else []
        return self._setarch

    def __call__(self, rank: int, script: List[str]) -> RunLog:
        self.runs += 1
        with tempfile.TemporaryDirectory(prefix="stretto-rerun-") as d:
            script_path, log_path = os.path.join(d, "script"), os.path.join(d, "log")
            work = os.path.join(d, "cwd")
            os.mkdir(work)
            with open(script_path, "w") as fh:
                fh.write("".join(line + "\n" for line in script))
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("OMPI_", "PMIX_", "PMI_", "HYDRA_", "MPIR_", "OPAL_", "PRTE_", "STRETTO_"))
                   and k != "LD_PRELOAD"}
            env.update(LD_PRELOAD=self.lib, STRETTO_RERUN_RANK=str(rank), STRETTO_RERUN_NP=str(self.np),
                       STRETTO_RERUN_SCRIPT=script_path, STRETTO_RERUN_LOG=log_path)
            if self.mpi == "openmpi":
                env["OMPI_MCA_ess_singleton_isolated"] = "1"     # no orted for a singleton
            timed_out, rc, err = False, None, ""
            try:
                r = subprocess.run(self._no_aslr() + [self.exe, *self.args], cwd=self.cwd or work, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   timeout=self.timeout, start_new_session=True)
                rc, err = r.returncode, r.stderr.decode(errors="replace")[-2000:]
            except subprocess.TimeoutExpired:
                timed_out = True
            lines = open(log_path).read().split("\n") if os.path.exists(log_path) else []
        return parse_log(lines, rc, timed_out, self.timeout, err)


# ------------------------------------------------------------------ the program

@dataclass
class _Seg:
    """A piece of a rank's program between two result points: its DSL ops, the calls in the
    normalised form both the trace and the shim's log give (for the reproduction checks), and
    the result point that ends it: where the explorer binds the results (loc) and what the
    trace recorded there (None: the call never completed in the run). The last piece of a
    rank's trace path has no point and ends with how the trace ends (end)."""
    ops: List[object] = field(default_factory=list)
    calls: List[tuple] = field(default_factory=list)
    loc: Optional[tuple] = None
    recorded: Optional[Point] = None
    end: str = ""        # finalize | truncated | abort:<why> | unknown:<why>


def _locate(env: dict, loc: tuple) -> Optional[Point]:
    """The results of a point as the explorer bound them: ("recv", var) is a receive's
    metadata and payload, ("any", index var, (var or None per request)) a Waitany's index and,
    if it completed a receive, that receive's."""
    out: List[Entry] = []
    for step in loc:
        if step[0] == "recv":
            m = env.get(step[1])
            if not isinstance(m, Meta):
                return None
            out.append(("R", m.src, m.tag, m.bytes, m.data))
        else:
            i = env.get(step[1])
            if not isinstance(i, int):
                return None
            out.append(("I", i))
            var = step[2][i] if 0 <= i < len(step[2]) else None
            if var is not None:
                m = env.get(var)
                if not isinstance(m, Meta):
                    return None
                out.append(("R", m.src, m.tag, m.bytes, m.data))
    return tuple(out)


def _same_calls(a: List[tuple], b: List[tuple]) -> Optional[str]:
    """None if two call lists agree, else what differs. The last field of a send is its
    payload, compared where both sides have it."""
    for i, (x, y) in enumerate(zip(a, b)):
        if x[0] == "send" and y[0] == "send":
            if x[:-1] != y[:-1] or (x[-1] is not None and y[-1] is not None and x[-1] != y[-1]):
                return f"call {i}: {x[:-1]} vs {y[:-1]}" + ("" if x[:-1] != y[:-1] else " (payload)")
        elif x != y:
            return f"call {i}: {x} vs {y}"
    if len(a) != len(b):
        return f"{len(a)} calls vs {len(b)}"
    return None


class ReexecProgram(dict):
    """{rank: [ops]} whose ops are computed during the exploration; tells the explorer when a
    ready-mode send appeared (stretto.explore runs unreduced then)."""
    oracle: "Oracle"

    def dynamic_rsend(self) -> bool:
        return self.oracle.saw_rsend


class Oracle:
    """The traced program as a DSL program whose ranks are re-executed where their results
    leave the recorded ones (module docstring). live: the ranks that may be re-executed (None:
    every rank). runs: a cache of re-executions, {(rank, history): RunLog}, which oracles
    over the same trace and runner may share. max_runs: a budget of runs (None: no limit);
    past it a continuation that needs a new run is Unknown."""

    def __init__(self, t: Trace, runner: Runner, live: Optional[Set[int]] = None,
                 runs: Optional[Dict[Tuple[int, History], RunLog]] = None, max_runs: Optional[int] = None):
        self.t, self.runner, self.live, self.max_runs = t, runner, live, max_runs
        self.runs = runs if runs is not None else {}
        self.segs: Dict[int, List[_Seg]] = {rank: self._trace_segments(rank) for rank in sorted(t.ranks)}
        self._ops: Dict[tuple, Tuple[object, ...]] = {}
        self._calls: Dict[Tuple[int, History], List[tuple]] = {}
        self.unreliable: Dict[int, str] = {}    # rank -> why its re-execution is not trusted
        self._validated: Set[int] = set()
        self.saw_rsend = any(s.ready_mode for s in t.sends.values())
        self.reexecuted: Set[Tuple[int, History]] = set()    # (rank, history) whose continuation came from a run
        for rank, segs in self.segs.items():
            prefix: History = ()
            for seg in segs:
                self._calls[(rank, prefix)] = seg.calls
                if seg.loc is None or seg.recorded is None:
                    break
                prefix = prefix + (seg.recorded,)

    def program(self) -> ReexecProgram:
        prog = ReexecProgram()
        for rank in sorted(self.t.ranks):
            prog[rank] = list(self._trace_from(rank, 0))
        prog.oracle = self
        return prog

    # ---- the trace path
    def _entry(self, r) -> Optional[Entry]:
        s = self.t.observed_send(r)
        return None if s is None else ("R", s.rank, s.tag, s.bytes, s.data)

    def _trace_segments(self, rank: int) -> List[_Seg]:
        """The rank's recorded calls cut at its result points, as dsl.program_from_trace
        writes them (same ops, so the explorer numbers sends and receives as trace.py does),
        with every receive bound to m<ordinal> and every Waitany index to w<point>."""
        t = self.t
        sends = {s.idx: s for s in t.sends_of(rank)}
        recvs = {r.idx: r for r in t.recvs_of(rank)}
        colls = {c.idx: c for c in t.colls if c.rank == rank}
        segs: List[_Seg] = []
        cur = _Seg()

        def close(loc, recorded):
            nonlocal cur
            cur.loc, cur.recorded = loc, recorded
            segs.append(cur)
            cur = _Seg()

        def var_of(req):
            r = t.recv_by_req(rank, req) if req is not None and req >= 0 else None
            return None if r is None else f"m{r.key[1]}"

        for rec in t.ranks.get(rank, []):
            raw = rec.raw
            blocked = rec.op == "blocked"
            call = raw.get("call") if blocked else rec.op
            if call == "send":
                s = sends.get(rec.idx)
                if s is None:
                    continue
                cur.ops.append(Send(s.dst, s.tag, s.bytes, s.comm, None if s.blocking else f"q{s.req}",
                                    _MODE.get(s.mode, "std"), s.data))
                cur.calls.append(("send", s.mode, s.dst, s.tag, s.comm, s.bytes, s.req, s.data))
            elif call in ("recv", "irecv"):
                r = recvs.get(rec.idx)
                if r is None:
                    continue
                var = f"m{r.key[1]}"
                cur.ops.append(Recv(r.src, r.tag, r.capacity, r.comm, None if r.blocking else f"q{r.req}", var))
                cur.calls.append(("recv" if r.blocking else "irecv", r.src, r.tag, r.comm, r.capacity, r.req))
                if r.blocking:
                    e = self._entry(r) if r.completed else None
                    close((("recv", var),), None if e is None else (e,))
            elif call == "wait":
                kind = raw.get("kind")
                if kind not in ("wait", "waitall", "waitany"):
                    # a test's flag, a Waitsome's set, a status poll: results the DSL does not have
                    cur.end = f"unknown:MPI_{kind.capitalize() if kind else 'Test'}: completion flags and sets are not modelled at L2"
                    break
                reqs = list(raw.get("reqs") or [])
                cur.calls.append(("wait", kind, tuple(-1 if q is None else q for q in reqs)))
                done = [d.get("req") for d in raw.get("done", []) if d.get("req") is not None and d["req"] >= 0]
                if kind == "waitany":
                    if not blocked and not done:
                        continue                          # every request null: returns MPI_UNDEFINED
                    names = tuple(f"q{q}" if q is not None and q >= 0 else f"null{j}" for j, q in enumerate(reqs))
                    wvar = f"w{len(segs)}"
                    cur.ops.append(Waitany(names, wvar))
                    loc = (("any", wvar, tuple(var_of(q) for q in reqs)),)
                    recorded = None
                    if not blocked and raw.get("idx") is not None and 0 <= raw["idx"] < len(reqs):
                        entries = [("I", raw["idx"])]
                        v = var_of(reqs[raw["idx"]])
                        if v is not None:
                            e = self._entry(t.recv_by_req(rank, reqs[raw["idx"]]))
                            entries = None if e is None else entries + [e]
                        recorded = None if entries is None else tuple(entries)
                    close(loc, recorded)
                    continue
                waited = [q for q in reqs if q is not None and q >= 0] if blocked else done
                for q in waited:
                    cur.ops.append(Wait(f"q{q}"))
                rvars = [(q, var_of(q)) for q in waited if var_of(q) is not None]
                if rvars:
                    entries = None if blocked else [self._entry(t.recv_by_req(rank, q)) for q, _ in rvars]
                    recorded = None if entries is None or any(e is None for e in entries) else tuple(entries)
                    close(tuple(("recv", v) for _, v in rvars), recorded)
            elif call == "coll":
                c = colls[rec.idx]
                cur.ops.append(Coll(c.name, c.comm, f"q{c.req}" if c.req is not None else None, c.root))
                cur.calls.append(("coll", c.name, c.comm, c.root))
            elif call == "probe":
                cur.end = "unknown:a probe: what it reports is not modelled at L2"
                break
            elif rec.op in ("cancel", "reqfree", "unmodelled"):
                cur.end = f"unknown:{rec.op} {raw.get('call', '') if raw else ''}: not modelled at L2".replace("  ", " ")
                break
            elif rec.op == "abort":
                cur.end = "abort:MPI_Abort"
                break
            elif rec.op == "finalize":
                cur.end = "finalize"
                break
        cur.end = cur.end or "truncated"
        segs.append(cur)
        return segs

    def _trace_from(self, rank: int, k: int) -> Tuple[object, ...]:
        """The ops of the rank's k-th trace segment and what follows it."""
        key = ("trace", rank, k)
        if key not in self._ops:
            seg = self.segs[rank][k]
            ops = list(seg.ops)
            if seg.loc is not None:
                ops.append(Lazy(key, lambda env, rank=rank, k=k: self._after_trace_point(rank, k, env)))
            elif seg.end == "truncated":
                # the trace stops here without MPI_Finalize (killed, or ended by abort() or a
                # signal, which the tracer does not see): what the rank does next is learnt by
                # running it on every recorded result, after the calls of this piece
                ops.extend(self._reexec(rank, tuple(s.recorded for s in self.segs[rank][:k]),
                                        "the trace ends without MPI_Finalize", skip=len(seg.calls)))
            elif seg.end.startswith("abort:"):
                ops.append(Abort(seg.end[len("abort:"):]))
            elif seg.end.startswith("unknown:"):
                ops.append(Unknown(seg.end[len("unknown:"):]))
            self._ops[key] = tuple(ops)
        return self._ops[key]

    def _after_trace_point(self, rank: int, k: int, env: dict) -> Tuple[object, ...]:
        segs = self.segs[rank]
        actual = _locate(env, segs[k].loc)
        if actual is not None and actual == segs[k].recorded:
            return self._trace_from(rank, k + 1)
        if actual is None:
            return (Unknown("the explorer bound no result at a result point"),)
        return self._reexec(rank, tuple(s.recorded for s in segs[:k]) + (actual,))

    # ---- re-execution
    def _script(self, rank: int, hist: History) -> Optional[List[str]]:
        """The shim's script for a history, None if a payload it needs is not recorded. The
        rank's collectives on MPI_COMM_WORLD come first, as recorded: the shim serves the k-th
        one's result if the rank's k-th call is the same collective with the same contribution."""
        lines = []
        for c in (c for c in self.t.colls if c.rank == rank and c.comm == WORLD and c.req is None):
            lines.append(f"C {c.name} {-1 if c.root is None else c.root} {c.din or '-'} {c.dout or '-'}")
        for point in hist:
            for e in point:
                if e[0] == "R":
                    _, src, tag, nbytes, data = e
                    if data is None and nbytes > 0:
                        return None
                    lines.append(f"R {src} {tag} {nbytes} {data or '-'}")
                else:
                    lines.append(f"I {e[1]}")
        return lines

    def _run(self, rank: int, hist: History) -> Optional[RunLog]:
        key = (rank, hist)
        if key not in self.runs:
            script = self._script(rank, hist)
            if script is None:
                return None
            if self.max_runs is not None and len(self.runs) >= self.max_runs:
                return RunLog([], "unknown", f"the budget of {self.max_runs} re-executions is spent")
            self.runs[key] = self.runner(rank, script)
        return self.runs[key]

    def _validate(self, rank: int) -> None:
        """Before the first re-execution of a rank: run it on all its recorded results; it must
        issue the recorded calls and end as the trace ends (where the trace says how it ended)."""
        self._validated.add(rank)
        segs = self.segs[rank]
        hist: History = ()
        for seg in segs[:-1]:
            if seg.recorded is None:
                break
            hist = hist + (seg.recorded,)
        complete = len(hist) == len(segs) - 1
        log = self._run(rank, hist)
        if log is None:
            self.unreliable[rank] = "the trace has no payloads for its messages (trace with STRETTO_PAYLOAD)"
            return
        if not log.records:
            self.unreliable[rank] = log.why or "its re-execution wrote no calls"
            return
        # a trace that stops without MPI_Finalize ends inside the last piece: the run goes on
        why = self._check_prefix(rank, hist, log, len(hist) + 1, open_last=complete and segs[-1].end == "truncated")
        if why is None and complete:
            end = segs[-1].end
            if end == "finalize" and log.status != "finalize":
                why = f"the trace ends with MPI_Finalize, the re-execution with {log.status} {log.why}".rstrip()
            elif end.startswith("abort:") and log.status != "abort":
                why = f"the trace ends with MPI_Abort, the re-execution with {log.status} {log.why}".rstrip()
        if why is not None:
            self.unreliable[rank] = f"its re-execution does not reproduce the trace ({why}{_ending(log)})"

    def _check_prefix(self, rank: int, hist: History, log: RunLog, upto: int, open_last: bool = False) -> Optional[str]:
        """Compare the calls of a run with h < upto against those known for the shorter
        histories (the trace's, or an earlier run's); None if they agree. open_last: the
        known calls of the last piece are a prefix of the run's (a trace cut short)."""
        got = self._calls_by_h(log)
        last = min(upto, len(hist) + 1)
        for j in range(last):
            want = self._calls.get((rank, hist[:j]))
            if want is None:
                continue
            have = got.get(j, [])
            if open_last and j == last - 1:
                have = have[:len(want)]
            why = _same_calls(want, have)
            if why is not None:
                return f"after {j} result(s): {why}"
        return None

    @staticmethod
    def _calls_by_h(log: RunLog) -> Dict[int, List[tuple]]:
        out: Dict[int, List[tuple]] = {}
        for rec in log.records:
            c = _norm(rec)
            if c is not None:
                out.setdefault(rec.get("h", 0), []).append(c)
        return out

    def _reexec(self, rank: int, hist: History, why: str = "", skip: int = 0) -> Tuple[object, ...]:
        """The continuation of the rank after the results of hist: its calls in the run of the
        shim on hist that come after the last result (and after the first skip of them, which
        the trace path has given already), then a Lazy op for the next result."""
        key = ("rerun", rank, hist, skip)
        if key in self._ops:
            return self._ops[key]
        if self.live is not None and rank not in self.live:
            out: Tuple[object, ...] = (Unknown(why or f"rank {rank}'s results differ from the trace and it is not re-executed"),)
        else:
            if rank not in self._validated:
                self._validate(rank)
            if rank in self.unreliable:
                out = (Unknown(f"rank {rank}: {self.unreliable[rank]}"),)
            else:
                log = self._run(rank, hist)
                if log is None:
                    out = (Unknown("a message whose payload the trace does not have"),)
                elif not log.records:
                    out = (Unknown(log.why or "the re-execution wrote no calls"),)
                else:
                    out = self._ops_from_log(rank, hist, log, skip)
        self._ops[key] = out
        return out

    def _ops_from_log(self, rank: int, hist: History, log: RunLog, skip: int = 0) -> Tuple[object, ...]:
        n = len(hist)
        why = self._check_prefix(rank, hist, log, n + (1 if skip else 0), open_last=bool(skip))
        if why is not None:
            self.unreliable[rank] = f"two re-executions disagree ({why}{_ending(log)})"
            return (Unknown(f"rank {rank}: {self.unreliable[rank]}"),)
        self.reexecuted.add((rank, hist))
        ops: List[object] = []
        calls: List[tuple] = []
        loc: Optional[tuple] = None
        nrecv = 0
        req_var: Dict[int, Optional[str]] = {}     # request id -> receive variable (None: a send)
        for rec in log.records:
            op, h = rec.get("op"), rec.get("h", 0)
            if op in ("stop", "unknown", "abort", "finalize"):
                break
            c = _norm(rec)
            if h == n and c is not None:
                calls.append(c)
            mine = h == n and len(calls) > skip
            if op == "send":
                if rec.get("procnull"):
                    continue
                req = rec.get("req")
                if req is not None:
                    req_var[req] = None
                if mine:
                    mode = rec.get("mode", "std")
                    if mode in ("rsend", "irsend"):
                        self.saw_rsend = True
                    ops.append(Send(rec["dst"], rec["tag"], rec["count"] * rec["tsize"], WORLD,
                                    f"q{req}" if mode in _NONBLOCKING else None, _MODE.get(mode, "std"), rec.get("data")))
            elif op in ("recv", "irecv"):
                if rec.get("procnull"):
                    continue
                var = f"m{nrecv}"
                nrecv += 1
                src = ANY if rec.get("anysrc") else rec["src"]
                tag = ANY if rec.get("anytag") else rec["tag"]
                req = rec.get("req")
                if req is not None:
                    req_var[req] = var
                if mine:
                    ops.append(Recv(src, tag, rec["count"] * rec["tsize"], WORLD, f"q{req}" if op == "irecv" else None, var))
                    if op == "recv":
                        loc = (("recv", var),)
            elif op == "wait":
                if not mine:
                    continue
                kind = rec.get("kind")
                reqs = list(rec.get("reqs") or [])
                if kind == "waitany":
                    if rec.get("undefined"):
                        continue
                    wvar = f"w{h}"
                    ops.append(Waitany(tuple(f"q{q}" if q >= 0 else f"null{j}" for j, q in enumerate(reqs)), wvar))
                    loc = (("any", wvar, tuple(req_var.get(q) if q >= 0 else None for q in reqs)),)
                else:
                    live = [q for q in reqs if q >= 0]
                    ops.extend(Wait(f"q{q}") for q in live)
                    rv = [req_var.get(q) for q in live if req_var.get(q) is not None]
                    if rv:
                        loc = tuple(("recv", v) for v in rv)
            elif op == "coll":
                if mine:
                    root = rec.get("root", -1)
                    ops.append(Coll(rec["name"], WORLD, None, None if root is None or root < 0 else root))
        self._calls[(rank, hist)] = calls
        if log.status == "stop":
            if loc is None:
                return tuple(ops) + (Unknown("the re-execution stopped at a call that takes no result"),)
            ops.append(Lazy(("rerun", rank, hist),
                            lambda env, rank=rank, hist=hist, loc=loc: self._after_run_point(rank, hist, loc, env)))
        elif log.status == "abort":
            ops.append(Abort(log.why or "abort"))
        elif log.status == "unknown":
            ops.append(Unknown(log.why or "re-execution"))
        return tuple(ops)

    def _after_run_point(self, rank: int, hist: History, loc: tuple, env: dict) -> Tuple[object, ...]:
        actual = _locate(env, loc)
        if actual is None:
            return (Unknown("the explorer bound no result at a result point"),)
        return self._reexec(rank, hist + (actual,))


def _ending(log: RunLog) -> str:
    """How a run that is compared ended, if not in the ordinary way."""
    return f"; the run ended: {log.status} {log.why}".rstrip() if log.status in ("unknown", "abort") else ""


def _norm(rec: dict) -> Optional[tuple]:
    """A shim record in the normalised form of _Seg.calls; None for records that are not calls."""
    op = rec.get("op")
    if op == "send":
        if rec.get("procnull"):
            return None
        return ("send", rec.get("mode", "std"), rec["dst"], rec["tag"], WORLD, rec["count"] * rec["tsize"],
                rec.get("req"), rec.get("data"))
    if op in ("recv", "irecv"):
        if rec.get("procnull"):
            return None
        return (op, ANY if rec.get("anysrc") else rec["src"], ANY if rec.get("anytag") else rec["tag"], WORLD,
                rec["count"] * rec["tsize"], rec.get("req"))
    if op == "wait":
        return ("wait", rec.get("kind"), tuple(rec.get("reqs") or []))
    if op == "coll":
        root = rec.get("root", -1)
        return ("coll", rec["name"], WORLD, None if root is None or root < 0 else root)
    return None
