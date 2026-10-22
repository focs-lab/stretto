#!/usr/bin/env python3
"""E8b: happens-before candidate selection against the check (stretto.hb).

Tools that predict or explore message races select the sends a wildcard receive could have
taken by happens-before concurrency over the run. The paper claims that this cannot decide
which alternatives are feasible once a nonblocking wildcard receive is pending, and that the
constraint graph can. Here every pair (wildcard receive or probe, send) that a happens-before
rule selects or the analysis enumerates is judged three ways, on every trace: rule (a), the
posting clock with non-overtaking (DAMPI's); rule (b), the completion clock; and the check,
with the fast checks as the report runs them and, on traces of up to --generic-limit records,
with the generic check as well. The definitions are in analysis/stretto/hb.py.

The disagreements are replayed. A pair is *hb-only* when rule (b) selects it and the check
rejects it (rule (a) may select it too), *stretto-only* when the check accepts it and rule (a)
rejects it (rule (b) may reject it too). Each is forced with the pins of its horizon
(stretto.confirm.horizon_pins; for a rejected alternative, the horizon its closure computes,
with the changed receive pinned in any case) and comes with a control that pins the same
receives to their observed senders. Both runs go through the tracer, which applies the pins
and records the run with the original envelopes: a pin turns a wildcard receive into one from
a given source, and a run in which the forced match happens only because of that rewrite (a
later receive takes a message the pending original would have taken) is no run of the
program (stretto.confirm.unpinned_violation). Evidence per replay: *realised* (the changed
pin and every pin of a receive completed in the horizon matched, the run finished, and the
replay's own trace is a run of the program; *realised, run did not finish* when the changed
pin matched and the run then hung or failed), *pins only* (it matched, but only the rewrite
allowed it: the replay's trace is no run of the program), *not realised* (the pin never
matched while the control's pins all matched in a run of the program that finished), or
*control failed* (the pin never matched and the control shows nothing: it did not finish, did
not match, or is itself no run of the program). An hb-only pair realised is an alternative the
check rejected wrongly; one not realised or realised by the pins only is evidence that the
rule is unsound. A stretto-only pair realised is the rule's incompleteness; one not realised
or realised by the pins only counts against the check.

usage: e8_hb.py trace [--attempts N]
       e8_hb.py compare [--generic-limit N] [--only CORPUS ...] [--synthetic N]
       e8_hb.py replay [--sample N] [--seed K] [--jobs J] [--timeout S] [--app-timeout S]
       e8_hb.py all
Writes experiments/results/e8_hb.csv (one row per disagreement, the replay columns filled for
the replayed ones), e8_hb_counts.csv (the number of pairs per program, kind of receive and
verdicts) and e8_hb_traces.csv (one row per trace).

Traces: the MBI and MPI-BugBench programs of the latest corpus run
(experiments/results/{mbi,mbb}/<program>/{trace,main}), the idioms (traces/openmpi with the
binaries under idioms/*/bin/openmpi) and the applications of apps(), traced by `trace` into
experiments/results/e8/<program>/trace. Nekbone is not among them: its wildcard receives are
all blocking, where the two rules coincide, and its analysis needs about 33 GB, above the cap of
the container these runs use (24 GB).
--synthetic N adds N random programs from E6's generator, all of the mixed kind (sends,
receives and waits interleaved, so that wildcards stay pending while their rank sends); with
no program to rerun, the explorer over each trace as a straight-line program decides their
disagreements (column arbiter): realised as the single-change alternative (on every judged
receive, or on its judged pairs only), realised only with other matches changed, or not
realised (explorer_evidence).
"""
from __future__ import annotations
import argparse
import collections
import concurrent.futures
import csv
import glob
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stretto import dsl, explore, hb, trace  # noqa: E402
from stretto.confirm import (all_matched, changed_pin, classify_rc, horizon_pins, required_matches,  # noqa: E402
                             need_preload, run_pinned, unpinned_violation)
from stretto.feasible import breakpoints, check  # noqa: E402
from stretto.matching import candidates_full  # noqa: E402
from stretto.report import Finding  # noqa: E402
from stretto.trace import ANY  # noqa: E402
from e6_checker_vs_explorer import judged, model_executions  # noqa: E402
from provenance import code_commit  # noqa: E402

RES = os.path.join(ROOT, "experiments", "results")
OUT = os.path.join(RES, "e8_hb.csv")
TRACES_OUT = os.path.join(RES, "e8_hb_traces.csv")
COUNTS_OUT = os.path.join(RES, "e8_hb_counts.csv")
E8 = os.path.join(RES, "e8")
HYPRE = os.environ.get("HYPRE_DIR", os.path.join(ROOT, "third_party", "apps", "hypre"))
NPB_MG = os.environ.get("NPB_MG_DIR", os.path.join(ROOT, "third_party", "apps", "npb-mg"))
MPIRUN = "/usr/bin/mpirun.openmpi"
LAUNCH_ENV = {"OMPI_MCA_rmaps_base_oversubscribe": "1", "OMPI_MCA_hwloc_base_binding_policy": "none"}


@dataclass
class Source:
    """A trace and how to rerun its program: the replays must run the traced command line from
    the traced directory, since the pins number the wildcard operations of that execution."""
    corpus: str
    program: str
    tracedir: str
    exe: str
    args: List[str] = field(default_factory=list)
    cwd: str = ROOT
    mpi: str = "openmpi"
    np: Optional[int] = None          # None: the trace's
    env: Dict[str, str] = field(default_factory=dict)
    app: bool = False


def hypre_ij(version: str) -> str:
    """test/ij of a HYPRE tree as experiments/hypre/run.sh builds it (the 2.10.1 archive has
    its sources at the top, later ones under src/)."""
    for d in (f"hypre-{version}", f"hypre-{version}/src"):
        p = os.path.join(HYPRE, d, "test", "ij")
        if os.path.exists(p):
            return p
    return os.path.join(HYPRE, f"hypre-{version}", "test", "ij")


def apps() -> List[Source]:
    """The applications, with the command lines of experiments/{amg,hypre,npbmg}/run.sh."""
    out = [Source("amg", "amg-np8", "", os.path.join(ROOT, "study", "apps", "AMG", "test", "amg"),
                  ["-P", "2", "2", "2", "-n", "16", "16", "16", "-problem", "1"], np=8, env={"OMP_NUM_THREADS": "1"}, app=True)]
    ij = hypre_ij("2.10.1")
    for np in (4, 8):
        out.append(Source("hypre", f"hypre-2.10.1-np{np}", "", ij, ["-solver", "8", "-pout", "0"], os.path.dirname(ij),
                          "mpich", np, app=True))
    mg = os.path.join(NPB_MG, "NPB2.4.1", "NPB2.4-MPI", "bin")
    for np in (16, 32):
        out.append(Source("npbmg", f"mg-2.4.1-S-np{np}", "", os.path.join(mg, f"mg.S.{np}"), [], mg, "mpich", np, app=True))
    for s in out:
        s.tracedir = os.path.join(E8, s.program, "trace")
    return out


def sources(only=None) -> List[Source]:
    out = []
    for corpus in ("mbi", "mbb"):
        for d in sorted(glob.glob(os.path.join(RES, corpus, "*", "trace"))):
            prog = os.path.dirname(d)
            out.append(Source(corpus, os.path.basename(prog), d, os.path.join(prog, "main"), cwd=prog))
    for d in sorted(glob.glob(os.path.join(ROOT, "traces", "openmpi", "*"))):
        name = os.path.basename(d)
        out.append(Source("idioms", name, d, os.path.join(ROOT, "idioms", name, "bin", "openmpi", "main")))
    out += [s for s in apps() if os.path.isdir(s.tracedir)]
    return [s for s in out if not only or s.corpus in only]


def rel(p: str) -> str:
    return os.path.relpath(p, ROOT)


# ------------------------------------------------------------------------------- trace

def step_trace(args) -> int:
    for s in apps():
        if not os.path.exists(s.exe):
            print(f"{s.program}: {s.exe} missing (see experiments/{s.corpus}/run.sh)", flush=True)
            continue
        tracer = os.path.join(ROOT, "tracer", "bin", s.mpi, "libstretto-trace.so")
        need_preload(tracer)                 # ld.so would ignore a missing tracer, and the run would leave no trace
        for attempt in range(1, args.attempts + 1):
            shutil.rmtree(s.tracedir, ignore_errors=True)
            os.makedirs(s.tracedir)
            xs = ["-x", f"LD_PRELOAD={tracer}", "-x", f"STRETTO_TRACE_DIR={s.tracedir}"]
            for k, v in s.env.items():
                xs += ["-x", f"{k}={v}"]
            cmd = ["/usr/bin/timeout", "-k", "5", str(args.app_timeout), MPIRUN, "-np", str(s.np), *xs, s.exe, *s.args]
            t0 = time.monotonic()
            r = subprocess.run(cmd, cwd=s.cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                               env={**os.environ, **LAUNCH_ENV})
            res = classify_rc(r.returncode, r.stderr, time.monotonic() - t0, args.app_timeout)
            with open(os.path.join(os.path.dirname(s.tracedir), "run.log"), "w") as fh:
                fh.write(f"{' '.join(cmd)}\nattempt {attempt}: {res}\n--- stdout\n{r.stdout}\n--- stderr\n{r.stderr}")
            print(f"{s.program}: traced run {attempt}: {res}", flush=True)
            if res == "FINISHED":
                break
    return 0


# ------------------------------------------------------------------------------ compare

def key(k) -> str:
    return f"{k[0]}:{k[1]}"


def unkey(s: str):
    a, b = s.split(":")
    return int(a), int(b)


def pin_token(x, s) -> Optional[str]:
    idx = x.pidx if hasattr(x, "pidx") else x.widx
    if idx is None:
        return None
    return f"{x.rank}:{idx}:{s.rank}" + (f":{s.tag}" if x.tag == ANY else "")


def why_hb_misses(p: hb.Pair) -> str:
    """For a pair the check accepts: why a rule does not select it."""
    if not p.head:
        return "taken in the run by an earlier receive of its rank (a swap)"
    if not p.comp:
        return "causally after the receive's completion"
    if not p.post:
        return "causally after the receive's posting"
    return ""


def steal_reason(t, p: hb.Pair) -> str:
    """The generic check at B = inf on the steal of the pair (or the probe's alternative)."""
    if p.op == "probe":
        v = check(t, t.observed, set(), None, "L1", changed_probes={p.at}, probe_alt={p.at: p.send})
    else:
        M = dict(t.observed)
        for rk, sk in t.observed.items():
            if sk == p.send and rk != p.at:
                del M[rk]
                break
        M[p.at] = p.send
        v = check(t, M, {p.at}, None, "L1")
    return "feasible" if v.feasible else v.reason


def compare_trace(t, corpus: str, program: str, tracedir: str, args, commit: str):
    """(rows, info) for one loaded trace: a row per pair, and the trace's own row."""
    t0 = time.monotonic()
    n = sum(len(v) for v in t.ranks.values())
    kinds = collections.Counter(hb.receive_kinds(t).values())
    info = {"corpus": corpus, "program": program, "trace": tracedir, "commit": commit, "records": n, "np": t.np,
            "wildcard_recvs": sum(kinds.values()),
            "wildcard_probes": sum(1 for p in t.probes.values() if p.wildcard and p.flag and p.seen is not None),
            **{k.replace(", ", "_").replace(" ", "_"): v for k, v in kinds.items()}}
    why = hb.declines(t)
    if why:
        return [], {**info, "status": f"declined: {why}"}
    try:
        pairs = hb.compare(t)
        generic = hb.judge(t, generic=True) if n <= args.generic_limit else None
    except Exception as e:  # noqa: BLE001  (counted in the trace table, not fatal)
        return [], {**info, "status": f"failed: {type(e).__name__}: {e}"[:200]}
    rows = []
    for p in pairs:
        x = t.recvs[p.at] if p.op == "recv" else t.probes[p.at]
        snd = t.sends[p.send]
        if p.op == "recv":
            o = t.observed_send(x)
            desc = f"{x} <- {snd}; observed {o if o is not None else 'none'}"
        else:
            desc = f"{x} sees {snd} instead of {t.sends[x.seen]}"
        st = p.stretto
        g = generic.get((p.op, p.at, p.send)) if generic is not None else None
        hb_only = p.comp and not st.accepted
        stretto_only = st.accepted and not p.post
        rows.append({
            "corpus": corpus, "program": program, "records": n, "op": p.op, "at": key(p.at), "send": key(p.send),
            "desc": desc, "kind": p.kind, "completed_by": hb.completed_by(t, x) if p.op == "recv" else x.kind,
            "head": p.head, "hb_post": p.post, "hb_comp": p.comp, "stretto": st.accepted,
            "stretto_generic": "" if generic is None else (g.accepted if g is not None else "not enumerated"),
            "stretto_kinds": st.kinds, "stretto_reason": st.reason, "stretto_B": st.B,
            "check_inf": steal_reason(t, p) if hb_only else "",
            "hb_miss": why_hb_misses(p) if st.accepted else "",
            "class": "hb-only" if hb_only else "stretto-only" if stretto_only else "",
            "pin": pin_token(x, snd), "commit": commit})
    c = collections.Counter(r["class"] for r in rows)
    print(f"{corpus}/{program}: {n} records, {len(pairs)} pairs, hb-only {c['hb-only']}, stretto-only {c['stretto-only']}", flush=True)
    return rows, {**info, "status": "ok", "pairs": len(pairs), "seconds": round(time.monotonic() - t0, 2),
                  "generic": "yes" if generic is not None else "no"}


def synthetic(args, commit: str):
    """Random straight-line programs from E6's generator (dsl.random_matched_program, every one
    mixed: sends, receives and waits interleaved, so that nonblocking wildcards stay pending
    while their rank sends), each with an execution the explorer chose written back as a trace.
    Each disagreement is decided by the explorer over the trace as a straight-line program at
    B = inf instead of a replay (explorer_evidence)."""
    rng = random.Random(args.seed)
    rows, traces = [], []
    made = attempts = 0
    while made < args.synthetic and attempts < args.synthetic * 20:
        attempts += 1
        prog, comms = dsl.random_matched_program(rng, rng.choice([3, 4, 4, 5]), mixed=True)
        try:
            res = explore.explore(prog, None, comms=comms, max_states=args.max_states)
        except (explore.StateCapReached, explore.ExploreError):
            continue
        done = [o for o in res.outcomes if o.kind == "done" and not o.leaked and not o.errors]
        if not done:
            continue
        t = trace.Trace()
        trace._build(t, dsl.trace_from_execution(prog, dict(rng.choice(done).matching), comms))
        if not any(r.wildcard for r in t.recvs.values()):
            continue
        made += 1
        rs, info = compare_trace(t, "synthetic", f"{made:03d}", "", args, commit)
        sp = dsl.program_from_trace(t)
        for r in rs:
            if r["class"]:
                r.update(sampled=True, arbiter="explorer", evidence=explorer_evidence(t, sp, r, args.max_states))
        rows += rs
        traces.append(info)
    return rows, traces


def explorer_evidence(t, sp, row: dict, max_states: int) -> str:
    """The explorer's verdict on a disagreement, with E6's criteria. *realised*: some execution
    agrees with the alternative (the steal, or the swap the check accepts) on every receive
    judged by its horizon, on the changed receives and on the receive a steal leaves without a
    message, matched alike or unmatched alike (E6's completeness criterion,
    e6_checker_vs_explorer.judged); *realised, judged pairs only*: some execution has every
    judged pair of the alternative, and a receive it leaves unmatched takes a message (E6's
    soundness criterion, which a replay pinned over the horizon tests too); *realised with
    other matches changed*: executions match the receive to the send only together with other
    changes, which no single-change alternative states; *not realised*: none does. An execution
    in which a ready send of the horizon, or one that must be issued before a pair it looks for is
    matched, is not taken by a receive posted before its issue (erroneous, E6's
    model_executions) counts only when no other does, and the verdict then says so."""
    f, M, changed, _ = alternative_of(t, row)
    rk, sk = f.recv, f.send
    victim = None
    if M is None:
        M = dict(t.observed)
        victim = next((x for x, y in t.observed.items() if y == sk and x != rk), None)
        if victim is not None:
            del M[victim]
        M[rk], changed = sk, {rk}
    cut = check(t, M, changed, None, "L1").cut
    keys = set(judged(t, M, cut)) | set(changed) | ({victim} if victim else set())
    try:
        res = explore.explore(sp, None, force={rk: sk}, max_states=max_states, comms=t.comm_members)
    except explore.StateCapReached:
        return "explorer state cap"
    except explore.ExploreError as e:
        return f"explorer: {e}"[:80]
    pairs = {k: M[k] for k in keys if k in M}

    def verdict(outcomes) -> str:
        outs = [{a: b for a, b in o.matching if a in t.recvs} for o in outcomes]
        if any(all(om.get(k) == M.get(k) for k in keys) for om in outs):
            return "realised"
        if any(all(om.get(k) == v for k, v in pairs.items()) for om in outs):
            return "realised, judged pairs only"
        if any(om.get(rk) == sk for om in outs):
            return "realised with other matches changed"
        return "not realised"

    # the explorer records a ready send that no receive posted before its issue takes as an error
    # and goes on; such a run is erroneous (MPI 4.1 §3.4), and the check assumes the receive is
    # posted for every ready send of the horizon and every one its pairs need
    v = verdict(model_executions(t, res.outcomes, cut, pairs, None))
    if v == "not realised":
        w = verdict(res.outcomes)
        if w != "not realised":
            return f"{w}, only with a ready send before its receive"
    return v


def step_compare(args) -> int:
    rows, traces = [], []
    commit = code_commit()
    for s in sources(args.only):
        try:
            t = trace.load(s.tracedir)
        except Exception as e:  # noqa: BLE001  (a corpus trace the loader refuses: counted, not fatal)
            traces.append({"corpus": s.corpus, "program": s.program, "trace": rel(s.tracedir), "commit": commit,
                           "status": f"load failed: {e}"[:200]})
            continue
        rs, info = compare_trace(t, s.corpus, s.program, rel(s.tracedir), args, commit)
        rows += rs
        traces.append(info)
    if args.synthetic:
        rs, ts = synthetic(args, commit)
        rows += rs
        traces += ts
    # every pair counted by its verdicts; a row of its own only for a disagreement (AMG alone
    # has seven thousand pairs)
    counts = collections.Counter(tuple(str(r[k]) for k in COUNT_KEYS[:-2]) for r in rows)
    write(args.counts_out, [dict(zip(COUNT_KEYS, k + (n, commit))) for k, n in sorted(counts.items())], COUNT_KEYS)
    split = [r for r in rows if r["class"]]
    write(args.out, split, ROW_KEYS)
    write(args.traces_out, traces, TRACE_KEYS)
    print(f"wrote {args.out} ({len(split)} disagreements of {len(rows)} pairs), {args.counts_out} and {args.traces_out} "
          f"({len(traces)} traces)")
    return 0


COUNT_KEYS = ["corpus", "program", "op", "kind", "head", "hb_post", "hb_comp", "stretto", "stretto_generic", "class", "pairs",
              "commit"]


ROW_KEYS = ["corpus", "program", "records", "op", "at", "send", "desc", "kind", "completed_by", "head", "hb_post", "hb_comp",
            "stretto", "stretto_generic", "stretto_kinds", "stretto_reason", "stretto_B", "check_inf", "hb_miss", "class", "pin",
            "sampled", "arbiter", "pins", "replay", "changed_matched", "matched", "unpinned", "control", "control_matched",
            "control_unpinned", "evidence", "commit"]
TRACE_KEYS = ["corpus", "program", "trace", "records", "np", "wildcard_recvs", "blocking", "nonblocking_pending_across_a_send",
              "nonblocking_nothing_sent_while_pending", "nonblocking_never_completed", "wildcard_probes", "pairs", "generic",
              "status", "seconds", "commit"]


def write(path: str, rows: List[dict], keys: List[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------------------- replay

def alternative_of(t, row) -> tuple:
    """(finding, M, changed, B) to pin for the row: the probe's alternative, or for a receive
    the alternative the check accepts (a swap when only the swap is feasible) at the first
    threshold it accepts, else the steal at B = inf."""
    at, sk = unkey(row["at"]), unkey(row["send"])
    if row["op"] == "probe":
        f = Finding("probe", "probe-alt", "L1", row["desc"], probe=at, send=sk)
        for B in [None] + breakpoints(t):
            if check(t, t.observed, set(), B, "L1", changed_probes={at}, probe_alt={at: sk}).feasible:
                return f, None, None, B
        return f, None, None, None
    f = Finding("1", "1f", "L1", row["desc"], recv=at, send=sk)
    if row["stretto"] == "True":
        alts = [a for a in candidates_full(t, lambda a, b: False)[0] if a.recv == at and a.send == sk]
        alts.sort(key=lambda a: a.kind != "steal")
        for B in [None] + breakpoints(t):
            for a in alts:
                if check(t, a.M, a.changed, B, "L1").feasible:
                    return f, (a.M if a.kind != "steal" else None), (a.changed if a.kind != "steal" else None), B
    return f, None, None, None


def evidence(row: dict) -> str:
    """What the replay and its control show about the pair (the module docstring). A replay
    whose own trace shows the match in a run of the program is evidence by itself; a pin that
    never matched is evidence only if the control's pins matched in a run of the program."""
    if row["replay"] == "MPI-ERROR:MPI_ERR_TRUNCATE" and row["changed_matched"] != "True":
        return "realised, truncation error"      # the pinned receive took the larger message: MPI reports no match
    if row["changed_matched"] == "True":
        if row["unpinned"].startswith("trace unreadable"):
            return "realised, replay trace unreadable"
        if row["unpinned"].startswith("undetermined"):
            return "realised, replay trace undetermined"
        if row["unpinned"] != "none":
            return "pins only"
        if row["replay"] != "FINISHED":
            return "realised, run did not finish"
        return "realised" if row["matched"] == "True" else "realised, other pins unmatched"
    if row["control"] != "FINISHED" or row["control_matched"] != "True" or row["control_unpinned"] != "none":
        return "control failed"
    return "not realised"


def prepare(t, row: dict):
    """The pins of a row's replay and control and the matches to look for, or None."""
    f, M, changed, B = alternative_of(t, row)
    hp = horizon_pins(t, f, B, infeasible_ok=True, M=M, changed=changed)
    if hp is None or not hp[0] or not hp[1]:
        return None
    return hp[0], hp[1], required_matches(t, f, B, infeasible_ok=True, M=M, changed=changed), changed_pin(t, f)


def run_one(src: Source, np: int, row: dict, prepared, timeout: int) -> dict:
    out = dict(row, sampled=True, arbiter="replay")
    if prepared is None:
        out["evidence"] = "no pins"
        return out
    alt, ctl, required, cp = prepared
    tracer = os.path.join(ROOT, "tracer", "bin", src.mpi, "libstretto-trace.so")
    out["pins"] = sum(x.count(",") + 1 for x in alt)

    def go(pins, prefix):
        d = tempfile.mkdtemp(prefix=f"e8-{prefix}-")
        try:
            r = run_pinned(np, src.exe, src.args, pins, timeout, mpi=src.mpi, cwd=src.cwd, preload=tracer,
                           extra_env={**src.env, **LAUNCH_ENV, "STRETTO_TRACE_DIR": d})
            res = classify_rc(r.returncode, r.stderr, r.elapsed, timeout)
            try:
                unp = unpinned_violation(trace.load(d), finished=res == "FINISHED", hung=res == "TIMEOUT", pins=pins) or "none"
            except Exception as e:  # noqa: BLE001
                unp = f"trace unreadable: {e}"[:120]
            return r, res, unp
        finally:
            shutil.rmtree(d, ignore_errors=True)

    ra, res, unp = go(alt, "alt")
    rc, cres, cunp = go(ctl, "ctl")
    out.update(replay=res, changed_matched=all_matched(alt, ra.stderr, {cp}) if cp else "",
               matched=all_matched(alt, ra.stderr, required), unpinned=unp,
               control=cres, control_matched=all_matched(ctl, rc.stderr), control_unpinned=cunp)
    out = {k: (str(v) if isinstance(v, bool) else v) for k, v in out.items()}
    out["evidence"] = evidence(out)
    return out


def step_replay(args) -> int:
    with open(args.out) as fh:
        rows = list(csv.DictReader(fh))
    by_src = {(s.corpus, s.program): s for s in sources()}
    groups = collections.defaultdict(list)
    for i, r in enumerate(rows):
        if r["class"] and r["corpus"] != "synthetic":      # the explorer decided those
            groups[(r["corpus"], r["program"], r["class"])].append(i)
    chosen = []
    for (corpus, program, cls), idx in sorted(groups.items()):
        rng = random.Random(f"{args.seed}:{corpus}:{program}:{cls}")
        chosen += [(corpus, program, i) for i in sorted(rng.sample(idx, min(args.sample, len(idx))))]
    print(f"replaying {len(chosen)} of {sum(len(v) for v in groups.values())} disagreements", flush=True)
    loaded: Dict[tuple, object] = {}
    jobs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for corpus, program, i in chosen:
            src = by_src.get((corpus, program))
            if src is None:
                rows[i] = dict(rows[i], sampled="True", evidence="no program")
                continue
            if (corpus, program) not in loaded:
                loaded[(corpus, program)] = trace.load(src.tracedir)
            t = loaded[(corpus, program)]
            timeout = args.app_timeout if src.app else args.timeout
            # pins are computed here, in one thread; the jobs run the replays
            jobs.append((i, pool.submit(run_one, src, src.np or t.np, rows[i], prepare(t, rows[i]), timeout)))
        for k, (i, fut) in enumerate(jobs, 1):
            rows[i] = fut.result()
            r = rows[i]
            print(f"[{k}/{len(jobs)}] {r['program']} {r['class']} {r['op']} {r['at']}<-{r['send']}: {r['evidence']} "
                  f"(replay {r.get('replay')}, changed matched {r.get('changed_matched')}, unpinned {r.get('unpinned')}; "
                  f"control {r.get('control')})", flush=True)
            if k % 20 == 0:
                write(args.out, rows, ROW_KEYS)       # a long campaign keeps what it has
    write(args.out, rows, ROW_KEYS)
    c = collections.Counter((rows[i]["class"], rows[i]["evidence"]) for _, _, i in chosen)
    for k, v in sorted(c.items()):
        print(f"{k[0]:>13} {k[1]}: {v}")
    print(f"wrote {args.out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["trace", "compare", "replay", "all"])
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--traces-out", default=TRACES_OUT)
    ap.add_argument("--counts-out", default=COUNTS_OUT)
    ap.add_argument("--only", nargs="*", help="corpora to compare: mbi mbb idioms amg hypre npbmg")
    ap.add_argument("--generic-limit", type=int, default=20000, help="records up to which the generic check judges too")
    ap.add_argument("--synthetic", type=int, default=0, help="also compare on N random programs, decided by the explorer")
    ap.add_argument("--max-states", type=int, default=300000, help="the explorer's state cap (synthetic programs)")
    ap.add_argument("--attempts", type=int, default=5, help="traced runs of an application until one finishes")
    ap.add_argument("--sample", type=int, default=30, help="replays per program and class of disagreement")
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=60, help="seconds per replay of a corpus program or idiom")
    ap.add_argument("--app-timeout", type=int, default=300, help="seconds per traced run or replay of an application")
    args = ap.parse_args()
    if args.step in ("trace", "all"):
        step_trace(args)
    if args.step in ("compare", "all"):
        step_compare(args)
    if args.step in ("replay", "all"):
        step_replay(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
