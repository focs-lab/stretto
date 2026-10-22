"""Classify feasible alternatives. See docs/analysis.md section B for the taxonomy."""
from __future__ import annotations
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from . import dsl, explore
from . import fast
from .feasible import Verdict, breakpoints, check, feasible_profile, observed_feasible_at, profile_summary
from .matching import candidates_full, observed_hb, observed_ready_errors, probe_candidates
from .trace import ANY, Key, Probe, Recv, Send, Trace
from . import types
from .deadlock import contained_deadlock
from .reads import _cidx, erroneous, witness_respects


# classes that make a program's verdict ERROR (the warnings 1b, 1f, probe-alt and
# waitany-order do not); experiments/mbi/score.py uses the same set
ERROR_CLASSES = frozenset({"1a", "1c", "1d", "2-buffering", "2-collective", "2-blocked", "2-ready", "3-local", "abort"})


@dataclass
class Finding:
    family: str                 # '1' message race, '2' deadlock/ordering, '3' local concurrency, 'run', 'waitany', 'probe'
    cls: str                    # '1a'..'1f', '2-buffering', '2-collective', '2-blocked', '2-ready', '3-local', 'abort', 'waitany-order', 'probe-alt'
    level: str                  # 'L1', 'L1+SP', 'observed'
    title: str
    detail: List[str] = field(default_factory=list)
    B: str = ""                 # human range of thresholds at which the finding is realisable
    recv: Optional[Key] = None
    send: Optional[Key] = None
    pin: Optional[str] = None   # ready-to-use STRETTO_PIN spec for replay
    certainty: str = ""         # L1+SP: 'all' (every execution with the pair) or 'some'
    probe: Optional[Key] = None  # probe alternatives: the probe whose report changes (send = what it reports)
    site: Optional[str] = None   # call site of the changed receive or probe (object+offset), if traced
    error: str = ""              # 1a: which error, "truncate" (a message longer than the receive, MPI_ERR_TRUNCATE),
                                 # "type" (a type signature mismatch) or "rsend-no-receive" (a ready send with no
                                 # posted receive); only the first has an error MPI reports (stretto.confirm)

    def __str__(self):
        b = f" [{self.B}]" if self.B else ""
        head = f"[{self.cls:>12}] {self.level:<6} {self.title}{b}"
        return head + "".join(f"\n              - {d}" for d in self.detail) + (f"\n              - replay: {self.pin}" if self.pin else "")


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)
    infeasible: List[Tuple[str, str]] = field(default_factory=list)  # (alt description, reason)
    notes: List[str] = field(default_factory=list)
    # what the analysis could not do, as (reason, detail): the report is then incomplete, and
    # "no error found" does not mean that none can occur (README, "Exit status and output")
    degraded: List[Tuple[str, str]] = field(default_factory=list)

    def degrade(self, reason: str, detail: str = "") -> None:
        if all(r != reason for r, _ in self.degraded):
            self.degraded.append((reason, detail))

    def status(self) -> str:
        """"complete", or "degraded:" and the reasons, comma-separated: missing-ranks,
        tracer-overflow, unmatched-completions, creation-unknown, guard-off, threads, unmodelled,
        ready-send, explorer-skipped, explorer-incomplete, unconfirmed-rejections."""
        return "complete" if not self.degraded else "degraded:" + ",".join(r for r, _ in self.degraded)

    def by_class(self, cls: str) -> List[Finding]:
        return [f for f in self.findings if f.cls == cls]

    def classes(self):
        return sorted({f.cls for f in self.findings})

    def levels(self):
        """Classes qualified by the level that established them, e.g. 'L1:1f', 'observed:1c'."""
        return sorted({f"{f.level}:{f.cls}" for f in self.findings})

    def has_error(self) -> bool:
        return any(f.cls in ERROR_CLASSES for f in self.findings)

    def by_site(self) -> List[Tuple[str, Dict[str, int]]]:
        """Findings about a changed receive or probe grouped by its call site: one line of the
        report per site and class, however many ranks and senders the site has."""
        groups: Dict[str, Dict[str, int]] = {}
        for f in self.findings:
            if f.site is not None and f.level != "observed":
                g = groups.setdefault(f.site, {})
                k = f"{f.level}:{f.cls}"
                g[k] = g.get(k, 0) + 1
        return sorted(groups.items(), key=lambda kv: -sum(kv[1].values()))


def _fmt_B(B: Optional[int]) -> str:
    return "inf" if B is None else str(B)


def _pin_token(r: Recv, s: Send) -> Optional[str]:
    if r.widx is None:
        return None
    spec = f"{r.rank}:{r.widx}:{s.rank}"
    if r.tag == ANY:
        spec += f":{s.tag}"
    return spec


def pin_spec(r: Recv, s: Send) -> Optional[str]:
    tok = _pin_token(r, s)
    return None if tok is None else "STRETTO_PIN=" + tok


def pin_from_execution(t: Trace, o: explore.Outcome) -> Optional[str]:
    """Pins for every wildcard receive of one explorer execution, so that a replay realises
    that execution and not only its first changed pair."""
    toks = [_pin_token(t.recvs[rk], t.sends[sk]) for rk, sk in o.order if rk in t.recvs and t.recvs[rk].wildcard]
    toks = [x for x in toks if x is not None]
    return "STRETTO_PIN=" + ",".join(toks) if toks else None


def classify(r: Recv, s: Send, s_obs: Optional[Send]) -> Tuple[str, str]:
    if s.bytes > r.capacity:
        return "1a", f"count mismatch: {s.bytes}B into a {r.capacity}B receive (MPI_ERR_TRUNCATE)"
    if types.compatible(s.tsig, s.count, r.tsig, r.count) is False:
        return "1a", f"type signature mismatch: send {s.count} x {s.tsig} vs receive {r.count} x {r.tsig}"
    if s_obs is not None and r.tag == ANY and s.tag != s_obs.tag:
        return "1b", f"cross-purpose: tag {s.tag} instead of observed tag {s_obs.tag} (another logical channel)"
    if s_obs is not None and s.rank != s_obs.rank:
        return "1f", f"same shape as the observed message but from rank {s.rank} instead of {s_obs.rank}"
    return "1f", "same shape as the observed message"


def analyze(t: Trace, with_sp: bool = True, max_states: int = 300_000, sp_max_events: int = 2000,
            generic: bool = False, full_profile: bool = False, twin=None, changes: int = 1,
            twin_label: str = "twin", reads=None) -> Report:
    """Alternatives are judged by the indexed checker (stretto.fast), each profiled at the
    two ends of the B range and bisected between them (exact by theorem T2); full_profile=True
    judges every breakpoint, generic=True uses the generic checker (feasible.check) instead
    of the indexed one. Both exist for differential testing and are slower. twin: a DSL
    program (level L2) that encodes the program's metadata dependencies, written by hand or
    built by re-executing the ranks (stretto.rerun, twin_label "re-execution"); its
    exploration under each L1 alternative yields the L2 findings (1e, abort, deadlock), and an
    alternative after which every execution finishes with the recorded calls is dismissed.
    changes=2 also enumerates alternatives that re-match two wildcard receives on different
    ranks, judged by the generic checker at every breakpoint. reads: read bounds
    (stretto.reads, docs/firstread.md); an alternative whose changed receive is followed by
    records the program issues before it can read the result is judged with those records in
    the horizon (rule H1') and, if that fails, with rule H1, and a deadlock contained in the
    H1' horizon is an L1 finding; a consequence the single-path explorer finds is an L1
    finding when one of its witness executions respects the bounds (reads.witness_respects)."""
    rep = Report()
    rep.notes.extend(t.warnings)
    # what makes the trace an incomplete record of the run's communication (trace.Trace.incomplete:
    # missing-ranks, unmatched-completions, the message table's tracer-overflow, ...); no
    # alternative is predicted from it, below
    for reason, detail in t.incomplete:
        rep.degrade(reason, detail)
    if any("overflowed" in w for w in t.warnings):
        # the site or guard table: call sites stop being named, or the guard stops following buffers
        rep.degrade("tracer-overflow", "a table of the tracer filled up: later records of its kind may be incomplete")
    if t.truncated:
        rep.notes.append(f"ranks without a finalize record (run killed or hung): {t.truncated}")
    for rec in t.blocked_calls():
        rep.notes.append(f"rank {rec.rank} was blocked in {rec.raw.get('call')} at record #{rec.idx} when the run ended")
    if t.bad_lines:
        rep.notes.append(f"{t.bad_lines} unparsable trace lines skipped")

    # ---- observed: calls the run was blocked in when it ended (deadlock or kill)
    for rec in t.blocked_calls():
        call = rec.raw.get("call")
        what = f"rank {rec.rank} blocked in {call}"
        detail = []
        if call == "recv":
            r = next((x for x in t.recvs.values() if x.rank == rec.rank and x.idx == rec.idx), None)
            if r is not None:
                what = f"rank {rec.rank} blocked in {r}"
                taken = set(t.observed.values())
                cands = [s for s in t.sends.values() if r.accepts(s) and s.key not in taken]
                if cands:
                    detail.append("compatible unconsumed send(s) exist: " + ", ".join(str(s) for s in cands[:3])
                                  + "; the run ended before they were delivered (ordering, buffering or a kill)")
                else:
                    detail.append("no compatible unconsumed send in the trace: the receive can never complete")
        elif call == "wait":
            detail.append("requests: " + str(rec.raw.get("reqs")))
        elif call == "coll":
            detail.append(f"collective {rec.raw.get('name')} on comm {rec.raw.get('comm')}: not all ranks reached it")
        elif call == "send":
            detail.append(f"blocking send to {rec.raw.get('dst')} tag {rec.raw.get('tag')} never matched (rendezvous or no receive)")
        rep.findings.append(Finding("2", "2-blocked", "observed", what + " when the run ended", detail))

    # ---- observed: ranks killed while spinning in a polling loop (a livelock, not a blocked call),
    # and ranks that stopped outside MPI before MPI_Finalize (the run aborted)
    blocked = {rec.rank for rec in t.blocked_calls()}
    for rank in t.truncated:
        recs = [r for r in t.ranks.get(rank, []) if r.op != "guard"]
        if not recs:
            continue
        last = recs[-1].raw or {}            # point-to-point records drop their dictionary after loading
        if last.get("op") in ("probe", "wait") and last.get("flag") == 0:
            what = f"{last.get('kind')} src={last.get('src')} tag={last.get('tag')}" if last.get("op") == "probe" else f"{last.get('kind')} on requests {last.get('reqs')}"
            rep.findings.append(Finding("2", "2-blocked", "observed",
                                        f"rank {rank} was spinning in {what} when the run ended",
                                        ["a polling loop whose condition never became true: the run cannot finish"]))
        elif recs[-1].op == "abort":
            rep.findings.append(Finding("run", "abort", "observed", f"rank {rank} called MPI_Abort" + (f" with code {last['code']}" if "code" in last else ""),
                                        ["the program ended the run on its own check"]))
        elif rank not in blocked:
            rep.findings.append(Finding("run", "abort", "observed", f"rank {rank} stopped outside MPI before MPI_Finalize",
                                        [f"its last record is #{recs[-1].idx} ({recs[-1].op}): the program aborted, crashed or "
                                         "exited without finalising, or the run was killed while the rank computed"]))

    # ---- observed: messages sent but never received (pending at MPI_Finalize is erroneous)
    received = set(t.observed.values())
    freed = [r for r in t.recvs.values() if r.freed]
    for s in sorted(t.sends.values(), key=lambda s: s.key):
        if s.key in received or s.dst in t.missing or s.cancelled:
            continue
        if any(r.accepts(s) for r in freed):
            continue                         # a freed receive takes it, unobserved
        if s.dst not in t.ranks:
            continue
        rep.findings.append(Finding("1", "1c", "observed", f"{s} was never received in the observed run",
                                    ["a message left unmatched: pending communication at MPI_Finalize, or a receive that never came"]))

    # ---- observed: buffers of pending operations misused (family 3, the tracer's buffer guard)
    rep.findings.extend(local_findings(t))
    off = sorted(r for r, on in t.guard.items() if not on)
    if off:
        rep.notes.append(f"the buffer guard was asked for but off on ranks {off} (MPI_THREAD_MULTIPLE): "
                         "no local-concurrency findings from them")
        rep.degrade("guard-off", f"the buffer guard was off on ranks {off}")

    # ---- family 2: observed collective ordering
    untraced = 0
    for cs in t.coll_sets():
        names = sorted({c.name for c in cs})
        members = t.comm_members.get(cs[0].comm, [])
        if len(names) > 1:
            rep.findings.append(Finding("2", "2-collective", "observed",
                                        f"collective #{cs[0].ordinal} on comm {cs[0].comm}: ranks disagree {names}"))
        elif members and len(cs) != len(members) and all(c.completed for c in cs):
            if set(members) - {c.rank for c in cs} <= set(t.missing):
                untraced += 1            # the ranks without a trace file: the trace is incomplete, not the program
                continue
            rep.findings.append(Finding("2", "2-collective", "observed",
                                        f"collective {cs[0].name} #{cs[0].ordinal} on comm {cs[0].comm}: only {len(cs)} of {len(members)} ranks issued it"))
    if untraced:
        rep.notes.append(f"{untraced} collective(s) lack only ranks without a trace file: the trace is incomplete, "
                         "so they are not reported as 2-collective")

    # ---- a rank whose records come from several threads (MPI_THREAD_MULTIPLE, SERIALIZED, or a
    # misused FUNNELED): its call order depends on the threads' scheduling, not only on its
    # results, so its program order is not total and no alternative can be certified from it
    multi = [rank for rank, n in t.threads.items() if n > 1]
    if multi:
        if t.thread_level >= 3:
            rep.notes.append(f"MPI_THREAD_MULTIPLE with several calling threads on ranks {multi}: per-rank program order is not total, alternatives are not predicted (observed findings only)")
        else:
            rep.notes.append(f"several threads called MPI on ranks {multi} (thread level {t.thread_level}): per-rank program "
                             "order is not total, alternatives are not predicted (observed findings only)")
        rep.degrade("threads", f"several calling threads on ranks {multi} (thread level {t.thread_level}): alternatives not predicted")
        return rep
    # ---- communication outside the modelled fragment (one-sided, neighbourhood collectives,
    # dynamic processes, MPI-4 calls the tracer does not model): the trace is not a complete
    # record of the run's communication, which theorem T1 assumes
    if t.unmodelled:
        calls = sorted({c for _, c in t.unmodelled})
        ranks = sorted({r for r, _ in t.unmodelled})
        rep.notes.append(f"calls outside the modelled fragment on ranks {ranks}: {', '.join(calls)}; "
                         "alternatives are not predicted (observed findings only)")
        rep.degrade("unmodelled", f"calls outside the modelled fragment ({', '.join(calls)}): alternatives not predicted")
        return rep
    # ---- a trace that is not a complete record of the run's communication (a rank without a
    # file, messages from senders that were not traced, a tracer table that overflowed): the
    # happens-before lacks edges, which theorem T1 needs
    if t.incomplete:
        rep.notes.append("the trace is incomplete: " + "; ".join(d for _, d in t.incomplete)
                         + "; alternatives are not predicted (observed findings only)")
        return rep
    # ---- family 2: ready sends the run issued before their receive was posted (erroneous). The
    # observed graph is then cyclic at every threshold: no B0 for theorem T2, no vector clocks,
    # and the pruning's argument needs the graph acyclic, so the report stops at the observed
    # findings
    certain, others = observed_ready_errors(t)
    if certain or others:
        rep.findings.extend(ready_findings(certain, others))
        rep.notes.append("a ready send of the run was issued before the receive it is matched to was posted (2-ready): "
                         "every execution with the recorded matching is erroneous and the model realises the run at no "
                         "threshold; alternatives are not predicted (observed findings only)")
        rep.degrade("ready-send", "a ready send issued before its receive was posted: alternatives not predicted")
        return rep

    # ---- profiles over the breakpoints: the observed run and every alternative (receive,
    # probe, completion order) judged at each threshold, threshold-major so that one
    # Observed(t, B) serves every check at B (stretto.fast), or the generic checker on request
    ob_inf = fast.Observed(t, None) if not generic else None     # also the profiles' B = inf
    hb = ob_inf.reaches if not generic else observed_hb(t)
    alts, pruned = candidates_full(t, hb)
    rep.infeasible.extend((d, why) for d, why in pruned)
    probes = probe_candidates(t, hb)
    waits = _completion_alternatives(t, hb)
    pr = _profiles_generic(t, alts, probes, waits, reads) if generic else _profiles_fast(t, alts, probes, waits, full_profile, reads, ob_inf)
    rep.notes.extend(pr.notes)
    if any("not confirmed by the generic check" in n for n in pr.notes):
        rep.degrade("unconfirmed-rejections", f"above {CONFIRM_LIMIT} records the generic check does not confirm the fast "
                    "check's rejections, which may include alternatives the rules accept")
    if reads is not None:
        n = sum(1 for a in alts if reads.extends(t, a.changed, a.M))
        rep.notes.append(f"read bounds: {n} of {len(alts)} alternatives keep records of the changed rank after the "
                         "changed receive completes (rule H1', docs/firstread.md)")

    # ---- family 2: is the observed run itself buffering-dependent?
    prof_obs = pr.obs
    minB_obs, at_inf, _ = profile_summary(prof_obs)
    # thresholds at which the observed run is realisable over every edge (from B0 of theorem
    # T2 on): an alternative's interval is stated over these, so that a deadlock of unrelated
    # ranks at small B (which the whole-prefix acyclicity check sees) is not mistaken for a
    # property of the alternative
    realisable = {B for B, ok in pr.real.items() if ok} or set(pr.real)
    unrealisable = sorted((B for B in pr.real if B not in realisable), key=lambda B: (B is None, B))
    deadlocked = [B for B in unrealisable if not prof_obs.get(B, False)]
    ordered = [B for B in unrealisable if prof_obs.get(B, False)]
    if ordered:
        rep.notes.append("the observed run is realisable only from B=" + _fmt_B(min(realisable, key=lambda B: (B is None, B)))
                         + " when its probes report what they reported and collectives of unknown shape synchronise: "
                         "below that it need not deadlock, and no interval is stated at B in "
                         + ",".join(_fmt_B(B) for B in ordered))
    elif not any(pr.real.values()) and any(prof_obs.values()):
        rep.notes.append("the observed run is realisable at no B when its probes report what they reported and "
                         "collectives of unknown shape synchronise: intervals are stated without theorem T2's hypothesis")
    if not at_inf:
        rep.findings.append(Finding("2", "2-buffering", "observed",
                                    "observed matching infeasible even with infinite buffering (trace inconsistent or unsupported operations)"))
    elif minB_obs is None or minB_obs > 0:
        v = pr.obs0
        cyc = " -> ".join(f"r{n[0]}#{n[1]}{n[2]}" for n in (v.cycle or []))
        bound = minB_obs if minB_obs is not None else "the largest message"
        rep.findings.append(Finding("2", "2-buffering", "observed",
                                    f"observed run deadlocks on any implementation with eager threshold < {bound} bytes",
                                    [f"cycle at B=0: {cyc}" if cyc else f"infeasible at B=0: {v.reason}"],
                                    B=f"B < {bound}"))

    # ---- family 1: alternatives at wildcard receives
    sp_targets: List[Tuple[Key, Key, Finding]] = []
    sp_pairs: Set[Tuple[Key, Key]] = set()
    bps_all = breakpoints(t) + [None]
    floors = {f for f in {0, observed_floor(bps_all, pr.real)} if realisable == set(bps_all[f:])}

    def summarise(prof) -> Tuple[Optional[int], bool, str]:
        """profile_summary of a profile over the realisable thresholds (Profile.summary when
        they are those from the profile's floor on)."""
        if isinstance(prof, Profile) and prof.floor in floors:
            return prof.summary()
        return profile_summary({B: ok for B, ok in prof.items() if B in realisable})

    for i, alt in enumerate(alts):
        minB, at_inf, brange = summarise(pr.alt[i])
        if minB is None and not at_inf:
            rep.infeasible.append((alt.describe(t) + f" [{alt.kind}]", pr.alt_reason[i]))
            continue
        r, s = t.recvs[alt.recv], t.sends[alt.send]
        s_obs = t.observed_send(r)
        cls, why = classify(r, s, s_obs)
        details = [why,
                   f"L1: the continuation of rank {r.rank} after {r} is undetermined by the trace (horizon)"]
        if deadlocked:
            details.append("thresholds at which the observed run itself deadlocks are left out: B in "
                           + ",".join(_fmt_B(B) for B in deadlocked) + " (see 2-buffering)")
        if ordered:
            details.append("thresholds at which the observed run is not realisable are left out: B in "
                           + ",".join(_fmt_B(B) for B in ordered) + " (see the notes)")
        if alt.kind == "swap":
            details.append(f"swap variant: {t.recvs[alt.stolen_from]} then takes {s_obs}; certified at L1 only up to the first changed receive")
        elif alt.stolen_from is not None:
            details.append(f"steal variant: {t.recvs[alt.stolen_from]} is left without {s}")
        ext = reads is not None and reads.extends(t, alt.changed, alt.M)
        if ext:
            details.append("read bounds: " + "; ".join(
                f"rank {R} reads no result of the changed receive " + (f"before its record #{q}" if q < len(t.ranks[R]) else "in the rest of its trace")
                for R, q in sorted(reads.rule1(t, alt.changed, alt.M).items())))
        f = Finding("1", cls, "L1", alt.describe(t), details, B=brange, recv=alt.recv, send=alt.send,
                    pin=pin_spec(r, s), error=("truncate" if why.startswith("count mismatch") else "type") if cls == "1a" else "")
        rep.findings.append(f)
        # a deadlock contained in the horizon (theorem T7): possible only when a changed rank
        # stops before rule 1 cuts it, i.e. at a nonblocking changed receive, or inside the
        # window that read bounds keep after a changed receive (rule H1'). A changed receive
        # that errs on its new message (1a) ends the window at once (reads.ReadBounds.recv),
        # and the deadlock holds only if its rank stops before completing it: the call that
        # completes it returns the error, where the program aborts or may branch
        errs = [t.recvs[k] for k in alt.changed if alt.M.get(k) in t.sends and erroneous(t.recvs[k], t.sends[alt.M[k]])]
        if any(not t.recvs[k].blocking for k in alt.changed) or ext:
            held = []
            windowed = False
            for B in dict.fromkeys([minB, None] if at_inf else [minB]):
                v, rule1 = None, None
                if ext:
                    v = check(t, alt.M, alt.changed, B, "L1", bounds=reads)
                    rule1 = reads.rule1(t, alt.changed, alt.M)
                if v is None or not v.feasible:
                    v, rule1 = check(t, alt.M, alt.changed, B, "L1"), None
                if v.feasible:
                    D = contained_deadlock(t, alt.M, alt.changed, v.cut, v.issued, B, rule1)
                    if D and any(x.rank not in D or v.cut[x.rank] > _cidx(x) for x in errs):
                        D = None
                    if D:
                        held.append((B, D))
                        windowed = windowed or rule1 is not None
            if held:
                D = held[-1][1]
                rep.findings.append(Finding(
                    "1", "1d", "L1", f"deadlock after {r} takes {s}, contained in the horizon",
                    ["; ".join(f"rank {R} stops at {op}" for R, op in D.items()),
                     "every rank that could complete these operations stops too or has terminated, so the deadlock "
                     "holds for every program consistent with the trace up to them (theorem T7, no single-path assumption)",
                     "judged at B in " + ",".join(_fmt_B(B) for B, _ in held)]
                    + (["the horizon keeps the records the changed rank issues before it can read the receive's result "
                        "(rule H1', read bounds)"] if windowed else []),
                    B="B in " + ",".join(_fmt_B(B) for B, _ in held), recv=alt.recv, send=alt.send,
                    pin=pin_spec(r, s), certainty="all"))
        if alt.kind == "steal" or (alt.recv, alt.send) not in sp_pairs:
            sp_targets.append((alt.recv, alt.send, f))
            sp_pairs.add((alt.recv, alt.send))

    # ---- single-path consequences via the explorer (MOPPER's setting)
    if with_sp and sp_targets:
        nevents = sum(len(recs) for recs in t.ranks.values())
        if nevents > sp_max_events:
            rep.notes.append(f"L1+SP explorer skipped: {nevents} events > {sp_max_events}")
            rep.degrade("explorer-skipped", f"{nevents} events > {sp_max_events}: no L1+SP consequence was looked for")
        else:
            positions: Dict[int, List[int]] = {}
            prog = dsl.program_from_trace(t, positions)
            # baseline: what the straight-line program does without the forced match. A
            # deadlock or an orphan that every baseline execution has is the observed run's
            # own (a hang, a rendezvous below B, a send nobody receives), not the alternative's.
            base: Dict[Optional[int], Optional[explore.Result]] = {}
            for B in (0, None):
                try:
                    base[B] = explore.explore(prog, B, max_states=max_states, comms=t.comm_members)
                except (explore.StateCapReached, explore.ExploreError) as e:
                    base[B] = None
                    rep.notes.append(f"L1+SP baseline at B={_fmt_B(B)} not explored ({e}): consequences are not compared with the observed run")
                    rep.degrade("explorer-incomplete", "the single-path explorer did not finish an exploration")
            for rk, sk, f in sp_targets:
                for B in (0, None):
                    try:
                        res = explore.explore(prog, B, force={rk: sk}, max_states=max_states, comms=t.comm_members)
                    except explore.StateCapReached:
                        rep.notes.append(f"L1+SP explorer hit the state cap ({max_states}) for {t.recvs[rk]} <- {t.sends[sk]} at B={_fmt_B(B)}")
                        f.detail.append(f"L1+SP at B={_fmt_B(B)}: state space too large, not explored")
                        rep.degrade("explorer-incomplete", "the single-path explorer did not finish an exploration")
                        continue
                    except explore.ExploreError as e:
                        f.detail.append(f"L1+SP at B={_fmt_B(B)}: not explored ({e})")
                        rep.degrade("explorer-incomplete", "the single-path explorer did not finish an exploration")
                        continue
                    outs = res.outcomes_with(rk, sk)
                    if not outs:
                        blocked = any(o.kind == "force-blocked" for o in res.outcomes)
                        f.detail.append(f"L1+SP at B={_fmt_B(B)}: " + ("the forced match is never enabled in the straight-line program" if blocked else "no execution with this match (model gap or checker bug: see docs/model.md)"))
                        continue
                    kinds = sorted({o.kind for o in outs} | {k for o in outs for k in o.error_kinds})
                    orphans = sorted({k for o in outs for k in o.orphans})
                    msg = f"L1+SP at B={_fmt_B(B)}: outcomes {kinds}"
                    if orphans:
                        msg += f"; orphan sends {['s%d.%d' % k for k in orphans]}"
                    f.detail.append(msg)
                    tail = f"after {t.recvs[rk]} <- {t.sends[sk]} (single-path assumption)"
                    b = base.get(B)
                    base_outs = b.outcomes if b is not None else []
                    base_deadlock = bool(base_outs) and all(o.kind == "deadlock" for o in base_outs)
                    base_orphans = set.intersection(*(set(o.orphans) for o in base_outs)) if base_outs else set()

                    def certainty(pred) -> str:
                        return "all" if all(pred(o) for o in outs) else "some"

                    def witness(pred) -> Optional[str]:
                        return next((pin_from_execution(t, o) for o in outs if pred(o)), None)

                    def respecting(pred, claim: Optional[str] = None) -> Optional[explore.Outcome]:
                        """A witness of pred that every program consistent with the trace and its
                        read bounds also has (reads.witness_respects): the consequence is then an
                        L1 finding, with that witness's pins. claim: the MPI error the consequence
                        is, which the witness must meet before any other; None for its end, which
                        no error may precede."""
                        if reads is None:
                            return None
                        return next((o for o in outs if pred(o) and witness_respects(t, o, positions, reads, claim) is None), None)

                    def promote(cls: str, what: str, w: explore.Outcome, c: str, error: str = "") -> None:
                        rep.findings.append(Finding(
                            "1", cls, "L1", f"{what} in an execution of every program consistent with the trace and its read bounds, "
                            f"after {t.recvs[rk]} <- {t.sends[sk]}",
                            [f"single-path witness at B={_fmt_B(B)}: every rank stops before the read bound of each of its results "
                             "that differ from the trace, so the program issues the trace's calls up to there (docs/firstread.md)",
                             f"under the single-path assumption: in {'every execution' if c == 'all' else 'some executions'} with the change"],
                            B=f"B={_fmt_B(B)}", recv=rk, send=sk, pin=pin_from_execution(t, w), certainty="some", error=error))

                    dead = [o for o in outs if o.kind == "deadlock"]
                    if dead and base_deadlock:
                        f.detail.append(f"L1+SP at B={_fmt_B(B)}: the deadlock is the observed run's own (every execution without the change deadlocks too), not attributed")
                    elif dead:
                        c = certainty(lambda o: o.kind == "deadlock")
                        w = respecting(lambda o: o.kind == "deadlock")
                        if w is not None:
                            promote("1d", "deadlock", w, c)
                        else:
                            rep.findings.append(Finding("1", "1d", "L1+SP", f"deadlock in {'every execution' if c == 'all' else 'some executions'} {tail}",
                                                        B=f"B={_fmt_B(B)}", recv=rk, send=sk, pin=witness(lambda o: o.kind == "deadlock"), certainty=c))
                    new_orphans = [k for k in orphans if k not in base_orphans]
                    if orphans and not new_orphans:
                        f.detail.append(f"L1+SP at B={_fmt_B(B)}: the orphan sends are the observed run's own, not attributed")
                    elif new_orphans:
                        oset = set(new_orphans)
                        c = certainty(lambda o: oset <= set(o.orphans))
                        w = respecting(lambda o: oset & set(o.orphans))
                        if w is not None:
                            promote("1c", f"orphan send(s) {['s%d.%d' % k for k in sorted(oset & set(w.orphans))]}", w, c)
                        else:
                            rep.findings.append(Finding("1", "1c", "L1+SP", f"orphan send(s) {['s%d.%d' % k for k in new_orphans]} in {'every execution' if c == 'all' else 'some executions'} {tail}",
                                                        B=f"B={_fmt_B(B)}", recv=rk, send=sk, pin=witness(lambda o: oset & set(o.orphans)), certainty=c))
                    for err, what in (("truncate", "truncation error"), ("rsend-no-receive", "ready-mode send with no posted receive")):
                        if err in kinds and not (base_outs and all(err in o.error_kinds for o in base_outs)):
                            c = certainty(lambda o, err=err: err in o.error_kinds)
                            w = respecting(lambda o, err=err: err in o.error_kinds, err)
                            if w is not None:
                                promote("1a", what, w, c, err)
                            else:
                                rep.findings.append(Finding("1", "1a", "L1+SP", f"{what} in {'every execution' if c == 'all' else 'some executions'} {tail}",
                                                            B=f"B={_fmt_B(B)}", recv=rk, send=sk, pin=witness(lambda o, err=err: err in o.error_kinds), certainty=c,
                                                            error=err))
            # one finding per consequence, with the thresholds it holds at merged into its B
            uniq, out = {}, []
            for f in rep.findings:
                sig = (f.cls, f.level, f.title.split(" (single-path")[0])
                if sig in uniq:
                    g = uniq[sig]
                    if f.B and f.B not in g.B.split(","):
                        g.B = g.B + "," + f.B.replace("B=", "")
                    continue
                uniq[sig] = f
                out.append(f)
            rep.findings = out

    # ---- two-change alternatives (steals on two ranks), the next rung of the ladder
    if changes >= 2:
        from .matching import candidates_k
        for alt in candidates_k(t, 2, hb):
            # every breakpoint: T2 is proved for single-change alternatives only
            prof = {B: ok for B, ok in feasible_profile(t, alt.M, alt.changed, mode="L1", full=True).items() if B in realisable}
            minB, at_inf, brange = profile_summary(prof)
            if minB is None and not at_inf:
                rep.infeasible.append((alt.describe(t) + " [k2]", check(t, alt.M, alt.changed, None, "L1").reason))
                continue
            pairs = " and ".join(f"{t.recvs[rk]} <- {t.sends[alt.M[rk]]}" for rk in sorted(alt.changed))
            rep.findings.append(Finding("1", "1f", "L1", f"two changes: {pairs}",
                                        ["two wildcard receives on different ranks re-matched together; each is a kernel head of its source",
                                         f"L1: the continuation of ranks {sorted(rk[0] for rk in alt.changed)} after the changed receives is undetermined"],
                                        B=brange, recv=alt.recv, send=alt.send))

    # ---- level L2: the twin program (metadata dependencies encoded) under each alternative.
    # An execution that reaches a rank whose continuation is undetermined (outcome 'unknown',
    # only a re-executed twin has them) supports no deadlock and no dismissal, and counts
    # against "every execution"; what happened in it before, an abort or a departure from the
    # trace's calls, stands.
    if twin is not None and sp_targets:
        for rk, sk, f in sp_targets:
            for B in (0, None):
                try:
                    res = explore.explore(twin, B, force={rk: sk}, max_states=max_states, comms=t.comm_members)
                except (explore.StateCapReached, explore.ExploreError) as e:
                    f.detail.append(f"L2 at B={_fmt_B(B)}: {twin_label} not explored ({e})")
                    rep.degrade("explorer-incomplete", "the single-path explorer did not finish an exploration")
                    continue
                outs = res.outcomes_with(rk, sk)
                if not outs:
                    f.detail.append(f"L2 at B={_fmt_B(B)}: the {twin_label} never realises this match")
                    continue
                kinds = sorted({o.kind for o in outs} | {k for o in outs for k in o.error_kinds})
                f.detail.append(f"L2 at B={_fmt_B(B)}: {twin_label} outcomes {kinds}")
                why = sorted({f"rank {r}: {w}" for o in outs for r, w in o.unknown})
                if why:
                    f.detail.append(f"L2 at B={_fmt_B(B)}: undetermined continuations: " + "; ".join(why[:3]))
                tail = f"after {t.recvs[rk]} <- {t.sends[sk]} (L2 {twin_label})"
                every = lambda pred: "every execution" if all(pred(o) for o in outs) else "some executions"
                div = [d for o in outs for d in skeleton_divergence(t, o)]
                if div:
                    rep.findings.append(Finding("1", "1e", "L2", f"the continuation diverges from the trace in {every(lambda o: bool(skeleton_divergence(t, o)))} {tail}",
                                                sorted(set(div))[:6], B=f"B={_fmt_B(B)}", recv=rk, send=sk))
                if any(o.kind == "abort" for o in outs):
                    aborts = sorted({e for o in outs for e in o.errors if e.startswith("abort:")})
                    rep.findings.append(Finding("1", "1e", "L2", f"the program aborts on its own check in {every(lambda o: o.kind == 'abort')} {tail}",
                                                aborts[:3], B=f"B={_fmt_B(B)}", recv=rk, send=sk))
                if any(o.kind == "deadlock" for o in outs):
                    rep.findings.append(Finding("1", "1d", "L2", f"deadlock in {every(lambda o: o.kind == 'deadlock')} {tail}",
                                                B=f"B={_fmt_B(B)}", recv=rk, send=sk, pin=pin_from_execution(t, next(o for o in outs if o.kind == "deadlock"))))
                if not div and all(o.kind == "done" and not o.orphans for o in outs):
                    f.detail.append(f"L2 at B={_fmt_B(B)}: dismissed: every execution of the {twin_label} with this match "
                                    "finishes, receives every message and does not depart from the trace's calls")
        uniq, out = {}, []
        for f in rep.findings:
            sig = (f.cls, f.level, f.title.split(" (L2 ")[0]) if f.level == "L2" else id(f)
            if sig in uniq:
                g = uniq[sig]
                if f.B and f.B not in g.B.split(","):
                    g.B = g.B + "," + f.B.replace("B=", "")
                continue
            uniq[sig] = f
            out.append(f)
        rep.findings = out

    # ---- probe alternatives
    for j, (p, s) in enumerate(probes):
        minB, at_inf, brange = summarise(pr.probe[j])
        if minB is None and not at_inf:
            rep.infeasible.append((f"{p} sees {s}", pr.probe_reason[j]))
            continue
        seen = t.sends[p.seen]
        cls, why = classify_probe(p, s, seen)
        pin = f"STRETTO_PIN_PROBE={p.rank}:{p.pidx}:{s.rank}" + (f":{s.tag}" if p.tag == ANY else "") if p.pidx is not None else None
        rep.findings.append(Finding("probe", cls, "L1", f"{p} could observe {s} instead of {seen}",
                                    [why, f"L1: a receive whose source or count is taken from this probe's status changes with it; continuation of rank {p.rank} undetermined"],
                                    B=brange, pin=pin, probe=p.key, send=s.key))

    # ---- completion-order alternatives (Waitany/Testany/Waitsome/Testsome): another request
    # completes first; the record's outcome changes, so the horizon cuts after it
    for k, (rec, j, r_j, s_j) in enumerate(waits):
        minB, at_inf, brange = summarise(pr.wait[k])
        kind = rec.raw.get("kind")
        if minB is None and not at_inf:
            rep.infeasible.append((f"{kind} rank {rec.rank} #{rec.idx} index {j}", pr.wait_reason[k]))
            continue
        rep.findings.append(Finding("waitany", "waitany-order", "L1",
                                    f"{kind} at rank {rec.rank} #{rec.idx} could complete index {j} ({r_j} <- {s_j}) first",
                                    [f"L1: continuation of rank {rec.rank} is undetermined if it depends on the index",
                                     "no replay mechanism for completion order: not confirmable by pin"], B=brange))
    for f in rep.findings:
        x = t.probes.get(f.probe) if f.probe is not None else t.recvs.get(f.recv) if f.recv is not None else None
        if x is not None:
            f.site = t.site_label(x.rank, x.idx)
    return rep


def ready_findings(certain: List[Tuple[Recv, Send]], others: List[Send]) -> List[Finding]:
    """Observed 2-ready findings from matching.observed_ready_errors: one per ready send whose
    receive every execution with the recorded matching posts after the send's issue, and one for
    a set of ready sends of which at least one is issued before its receive."""
    why = ("MPI 4.1 §3.4: a ready-mode send may start only once its matching receive is posted; otherwise the "
           "program is erroneous (Open MPI and MPICH usually deliver it as a standard send, so the run may finish)")
    out = []
    for r, s in certain:
        out.append(Finding("2", "2-ready", "observed", f"ready send {s} was issued before {r}, the receive that takes it, was posted",
                           [why, "every execution with the recorded matching posts the receive after it issues the send "
                            "(program order, delivery, matching order, collectives)"], recv=r.key, send=s.key))
    if others:
        out.append(Finding("2", "2-ready", "observed", "of the ready sends " + ", ".join(str(s) for s in others)
                           + ", at least one was issued before the receive that takes it was posted",
                           [why, "in every execution with the recorded matching one of these receives is posted after the "
                            "issue of its ready send: their ready-mode orders close a cycle"]))
    return out


def _completion_alternatives(t: Trace, hb) -> List[Tuple]:
    """(record, index, receive, send) for every request a Waitany-like record could have
    completed instead of the one(s) it did."""
    out = []
    for rec in t.waitany:
        raw = rec.raw
        reqs = raw.get("reqs", [])
        done = {d.get("req") for d in raw.get("done", [])}
        if not done or len([q for q in reqs if q is not None and q >= 0]) < 2:
            continue
        for j, q in enumerate(reqs):
            if q is None or q < 0 or q in done:
                continue
            r_j = t.recv_by_req(rec.rank, q)
            if r_j is None or not r_j.completed:
                continue
            s_j = t.observed_send(r_j)
            if s_j is None or hb(rec.complete, s_j.issue_node()):
                continue
            out.append((rec, j, r_j, s_j))
    return out


def _operation(x) -> str:
    if isinstance(x, Send):
        return f"{x.mode} to {x.dst} tag {x.tag} ({x.bytes} B)"
    if isinstance(x, Recv):
        src = "ANY" if x.src == ANY else x.src
        tag = "ANY" if x.tag == ANY else x.tag
        return f"{'imrecv' if x.mrecv else 'irecv'} from {src} tag {tag} ({x.capacity} B)"
    return f"{x.name} on comm {x.comm}"


def local_findings(t: Trace) -> List[Finding]:
    """Family 3 (local concurrency), from the tracer's guard records: the program touched a
    buffer that a pending nonblocking operation owns (between the call, or a persistent
    request's Start, and the completion), or two pending operations own overlapping bytes and
    one writes them. Observed: the run did it; nothing is predicted about other runs. One
    finding per operation and kind of misuse."""
    ops, starts = {}, {}
    for x in list(t.sends.values()) + list(t.recvs.values()) + t.colls:
        if x.req is not None:
            ops[(x.rank, x.req)] = x
    for rank in {g.rank for g in t.guards}:   # an unmodelled operation the guard follows (a partitioned Start) names its request
        for rec in t.ranks[rank]:
            if rec.op == "unmodelled" and rec.raw.get("req") is not None:
                starts[(rank, rec.raw["req"])] = rec.raw.get("call", "?").replace("Start of a ", "")
    out, seen = [], set()
    for rec in t.guards:
        g = rec.raw
        what, req = g.get("what"), g.get("req")
        key = (rec.rank, req, what, g.get("kind"), g.get("with"), g.get("call"))
        if key in seen:
            continue
        seen.add(key)
        x = ops.get((rec.rank, req))
        op = _operation(x) if x is not None else starts.get((rec.rank, req), f"operation (request {req})")
        role = "send " if isinstance(x, Send) else "receive " if isinstance(x, Recv) else ""
        where = t.site_label(rec.rank, rec.idx)
        if what == "access":
            verb = "wrote to" if g.get("kind") == "write" else "read"
            title = f"rank {rec.rank} {verb} the {role}buffer of its pending {op} before the operation completed"
            detail = [f"{g.get('kind', 'access')} at byte {g.get('off')} of the buffer" + (f", by the instruction at {where}" if where else ""),
                      "MPI owns the buffer from the call to the completion: a send may carry either value; "
                      "a receive may overwrite the write, or the program reads data still arriving"]
        elif what == "modified":
            title = f"rank {rec.rank} changed the send buffer of its pending {op} before the operation completed"
            detail = ["the buffer's checksum at the completion differs from the one at the call; the write itself was "
                      "not caught (its page was not protected, as on the stack, or another access had opened it)"]
        elif what == "overlap":
            other = g.get("call", "a call") + (f" (request {g['with']})" if g.get("with") is not None else "")
            title = f"rank {rec.rank}: the buffer of {other} overlaps the {role}buffer of its pending {op}"
            detail = ["two operations own the same bytes and at least one of them writes them: the result depends "
                      "on the order in which MPI moves the data" + (f"; the call is at {where}" if where else "")]
        else:
            continue
        if x is not None:
            at = t.site_label(x.rank, x.idx)
            detail.append(f"the operation was issued at record #{x.idx}" + (f" ({at})" if at else ""))
        out.append(Finding("3", "3-local", "observed", title, detail, site=where))
    return out


class Profile(Mapping):
    """A feasibility profile over the breakpoints bps as _profiles_fast computes it: the
    thresholds below the floor infeasible (not stated), the judged ones with their verdicts,
    and the others filled by theorem T2, all with one value when the two ends agree (const) or
    feasible from the first feasible one of a bisection on (first, an index into the sizes
    between the floor and infinity). It reads as the dictionary the profile used to be, in the
    same order: the thresholds below the floor, the judged ones in the order judged, then the
    rest in ascending order. A dictionary over every breakpoint for every alternative was the
    largest allocation of the analysis on AMG's trace at 64 processes (half a million probe
    alternatives over 810 breakpoints)."""
    __slots__ = ("bps", "pos", "floor", "judged", "const", "first")

    def __init__(self, bps: List[Optional[int]], pos: Dict[Optional[int], int], floor: int):
        self.bps, self.pos, self.floor = bps, pos, floor
        self.judged: Dict[Optional[int], bool] = {}
        self.const: Optional[bool] = None
        self.first: Optional[int] = None

    def __getitem__(self, B: Optional[int]) -> bool:
        v = self.judged.get(B)
        if v is not None:
            return v
        i = self.pos[B]
        if i < self.floor:
            return False
        if self.const is not None:
            return self.const
        if self.first is not None and i < len(self.bps) - 1:
            return i - self.floor - 1 >= self.first
        raise KeyError(B)

    def _rest(self):
        if self.const is None and self.first is None:
            return []
        return [B for B in self.bps[self.floor + 1:-1] if B not in self.judged]

    def __iter__(self):
        # 0 is a breakpoint twice when the trace has empty messages; a dictionary holds it once
        yield from dict.fromkeys(self.bps[:self.floor] + list(self.judged) + self._rest())

    def __len__(self) -> int:
        return len(dict.fromkeys(self.bps[:self.floor] + list(self.judged) + self._rest()))

    def summary(self) -> Tuple[Optional[int], bool, str]:
        """feasible.profile_summary of the profile from its floor on, the realisable thresholds
        when the observed run is realisable from B0 on, without going over every breakpoint:
        the judged thresholds are few and the others follow const or first. A profile that
        is not an interval is listed in full, as profile_summary does."""
        bps, judged, floor, pos = self.bps, self.judged, self.floor, self.pos
        lo, hi = floor + 1, len(bps) - 1          # the sizes between the floor and inf: [lo, hi)
        at_inf = judged[None]
        js = sorted((pos[B], v) for B, v in judged.items() if B is not None)

        def unjudged(i: int, stop: int, step: int) -> Optional[int]:
            """The first index from i on, towards stop, whose threshold was not judged."""
            while i != stop:
                if bps[i] not in judged:
                    return i
                i += step
            return None

        # the least feasible finite threshold; with const every size takes the value of the
        # floor's, judged, and with first the sizes from lo + first on are feasible
        cands = [i for i, v in js if v]
        up = down = None
        if self.first is not None:
            cut = lo + self.first
            up, down = unjudged(cut, hi, 1), unjudged(cut - 1, lo - 1, -1)
            if up is not None:
                cands.append(up)
        if not cands:
            return (None, False, "never") if not at_inf else (None, True, "only with infinite buffering")
        m = min(cands)
        # an infeasible finite threshold above it, and one anywhere
        later = any(i > m and not v for i, v in js) or (down is not None and down > m)
        anyfalse = later or down is not None or any(not v for _, v in js)
        if not later and at_inf:
            return (bps[m], True, "any B") if not anyfalse else (bps[m], True, f"B >= {bps[m]}")
        return profile_summary({B: ok for B, ok in self.items() if pos[B] >= floor})


@dataclass
class _Profiles:
    obs: Mapping                                   # the observed run over guaranteed edges (2-buffering)
    obs0: object                                   # Verdict of the observed run at B = 0
    alt: List[Mapping]                             # profiles: threshold -> feasible
    alt_reason: List[str]                          # why rejected at B = inf
    probe: List[Mapping]
    probe_reason: List[str]
    wait: List[Mapping]
    wait_reason: List[str]
    notes: List[str] = field(default_factory=list)  # profiles that had to be judged in full
    # the observed run over every edge: realisable from B0 of theorem T2 on, where the
    # profiles start (a probe's report or a collective of unknown shape can raise B0 above
    # the threshold below which the run certainly deadlocks)
    real: Mapping = field(default_factory=dict)


def bisect_profile(bps: List[Optional[int]], pos: Dict[Optional[int], int], verdict, value, floor: int = 0,
                   full: bool = False, nonmonotone=None) -> Tuple[Profile, Dict[Optional[int], Verdict]]:
    """The profile over bps[floor:] (pos: the index of each breakpoint in bps) from
    verdict(B); value(Verdict) is the bool profiled. Returns it and the verdicts actually
    computed, by threshold. floor is the index of the least breakpoint at which the observed run
    is realisable: theorem T2 holds from there on, so the bisection starts there and the
    thresholds below are not stated (false in the profile). Feasible at the floor and
    infeasible at B = inf contradicts T2; nonmonotone() is then called and the item is judged at
    every breakpoint (the exact profile). full judges every breakpoint, as for an item T2 does
    not cover (a swap re-matches two receives) and for --full-profile."""
    prof = Profile(bps, pos, floor)
    seen: Dict[Optional[int], Verdict] = {}

    def judge(B: Optional[int]) -> bool:
        v = seen[B] = verdict(B)
        ok = prof.judged[B] = bool(value(v))
        return ok

    if full:
        for B in bps[floor:]:
            judge(B)
        return prof, seen
    lo, hi = judge(bps[floor]), judge(None)
    sizes = bps[floor + 1:-1]
    if lo == hi:
        prof.const = lo
        return prof, seen
    if lo and not hi:
        if nonmonotone is not None:
            nonmonotone()
        for B in sizes:
            judge(B)
        return prof, seen
    a, b, first = 0, len(sizes) - 1, len(sizes)     # the smallest feasible breakpoint
    while a <= b:
        m = (a + b) // 2
        if judge(sizes[m]):
            first, b = m, m - 1
        else:
            a = m + 1
    prof.first = first
    return prof, seen


CONFIRM_LIMIT = 200_000     # records up to which the generic check confirms the fast check's cycle rejections


def _profiles_fast(t: Trace, alts, probes, waits, full: bool = False, reads=None,
                   ob_inf: Optional[fast.Observed] = None) -> _Profiles:
    """The indexed checker (stretto.fast). Each item is judged at the two ends of the B range
    and, when the verdicts differ, bisected over the breakpoints between them: theorem T2
    (docs/model.md) makes the feasibility of a single-change alternative monotone in B, so
    the profile is an interval and the bisection exact. full=True judges every breakpoint
    instead; the tests and the E2 harness compare the two. Observed(t, B) is built on the
    first check at B and serves every later one at that threshold. With read bounds, an
    alternative that rule H1 rejects is judged again with the longer window of rule H1' (the
    generic check); either verdict is sound, so the profile is their union. H1' alone need not
    be monotone (later receives of the window become judged as B grows), but its horizon
    contains H1's with every node, edge and obligation of it, so H1' accepts only what the
    generic H1 check accepts: where the generic check confirms the fast check's rejections,
    the union is H1's profile, which T2 makes monotone. ob_inf: the Observed(t, None) the caller
    built for the enumeration, whose graph and clocks then serve B = inf too."""
    bps = breakpoints(t) + [None]
    ix = t.fast_indexes()
    obs: Dict[Optional[int], fast.Observed] = {} if ob_inf is None or ob_inf.ix is not ix else {None: ob_inf}
    # the fast check decides reachability in the observed graph, which also holds events beyond
    # an alternative's horizon, so a rejection by a cycle can be one the rules do not make
    # (proof appendix, conservative step 2); on traces up to CONFIRM_LIMIT records the generic
    # check, which meets theorem T6, confirms each such rejection
    confirm_rejections = sum(len(v) for v in t.ranks.values()) <= CONFIRM_LIMIT
    unconfirmed = [0]

    def at(B: Optional[int]) -> fast.Observed:
        ob = obs.get(B)
        if ob is None:
            ob = obs[B] = fast.Observed(t, B, ix)
        return ob

    pos = {B: i for i, B in enumerate(bps)}

    def profile(verdict, value, what: str = "", floor: int = 0, full_item: bool = False,
                notes: Optional[List[str]] = None) -> Tuple[Profile, Dict[Optional[int], Verdict]]:
        """bisect_profile over these breakpoints; a profile that is not monotone in B gets a
        note, in notes if given (else pr.notes)."""
        def note():
            (pr.notes if notes is None else notes).append(
                f"profile of {what} is not monotone in B (feasible at B={bps[floor]}, infeasible at inf), judged at every breakpoint; theorem T2 does not cover it")
        return bisect_profile(bps, pos, verdict, value, floor, full or full_item, note)

    pr = _Profiles({}, None, [None] * len(alts), [""] * len(alts), [None] * len(probes), [""] * len(probes),
                   [None] * len(waits), [""] * len(waits))
    pr.obs, seen = profile(lambda B: at(B).observed_verdict(), lambda v: v.ok, "the observed run")
    pr.obs0 = seen[0]
    pr.real = profile(lambda B: at(B).observed_verdict(strong=False), lambda v: v.ok, "the observed run over every edge")[0]
    floor = observed_floor(bps, pr.real)
    for i, a in enumerate(alts):
        def alt_h1(B, a=a):
            fv = at(B).check_alternative(a)
            if fv is None:
                return check(t, a.M, a.changed, B, "L1")   # a swap, a nonblocking receive, a cyclic observed graph
            if not fv.feasible and fv.reason == "constraint cycle":
                if confirm_rejections:
                    g = check(t, a.M, a.changed, B, "L1")
                    if g.feasible:
                        pr.notes.append(f"{a.describe(t)} at B={'inf' if B is None else B}: the fast check's cycle lies outside the horizon; the generic check accepts")
                    return g
                unconfirmed[0] += 1
            return fv

        def alt_at(B, a=a, h1=alt_h1, ext=reads is not None and reads.extends(t, a.changed, a.M)):
            v = h1(B)
            if ext and not v.feasible:
                g = check(t, a.M, a.changed, B, "L1", bounds=reads)     # rule H1'
                if g.feasible:
                    return g
            return v
        # T2 covers steals at blocking receives: a swap re-matches two receives, and at a
        # nonblocking receive the judged receives can change with B (proof appendix)
        exact = a.kind == "steal" and t.recvs[a.recv].blocking
        pr.alt[i], seen = profile(alt_at, lambda v: v.feasible, a.describe(t), floor, not exact)
        pr.alt_reason[i] = seen[None].reason
    def probe_at(B, p, s):
        fv = at(B).check_probe(p, s)
        return fv if fv is not None else check(t, t.observed, set(), B, "L1", changed_probes={p.key}, probe_alt={p.key: s.key})

    def wait_at(B, rec, s_j):
        fv = at(B).check_event(rec.rank, rec.idx, s_j)
        return fv if fv is not None else check(t, t.observed, set(), B, "L1", extra_edges=[(s_j.issue_node(), rec.complete)],
                                               changed_events={(rec.rank, rec.idx)}, must_issue=[s_j.key])

    # last to first, so that at each threshold the probes of a rank come latest first and each
    # closure continues the previous one (fast.Observed.probe_closure); notes in list order
    probe_notes: List[List[str]] = [[] for _ in probes]
    for j in reversed(range(len(probes))):
        p, s = probes[j]
        pr.probe[j], seen = profile(lambda B, p=p, s=s: probe_at(B, p, s), lambda v: v.feasible,
                                    f"{p} sees {s}", floor, notes=probe_notes[j])
        pr.probe_reason[j] = seen[None].reason
    for n in probe_notes:
        pr.notes.extend(n)
    for k, (rec, j, r_j, s_j) in enumerate(waits):
        pr.wait[k], seen = profile(lambda B, rec=rec, s_j=s_j: wait_at(B, rec, s_j),
                                   lambda v: v.feasible, f"completion order of rank {rec.rank} #{rec.idx} index {j}", floor)
        pr.wait_reason[k] = seen[None].reason
    if unconfirmed[0]:
        pr.notes.append(f"{unconfirmed[0]} rejection(s) by the fast check's reachability not confirmed by the generic check "
                        f"(trace above {CONFIRM_LIMIT} records): they may include alternatives the rules accept")
    return pr


def observed_floor(bps: List[Optional[int]], obs: Dict[Optional[int], bool]) -> int:
    """Index in bps of the least breakpoint at which the observed run is realisable (B0 of
    theorem T2), or 0 when it is realisable nowhere (the report then says so)."""
    for i, B in enumerate(bps):
        if obs.get(B):
            return i
    return 0


def _profiles_generic(t: Trace, alts, probes, waits, reads=None) -> _Profiles:
    """The generic checker (feasible.check), profiling each item from the observed run's
    threshold B0 to infinity and bisecting between them (T2); swaps in full. With read
    bounds, an alternative's profile is the union of its profiles under rules H1 and H1'."""
    pr = _Profiles(feasible_profile(t, t.observed, set(), mode="SP", full=False, strong_only=True),
                   observed_feasible_at(t, 0), [], [], [], [], [], [],
                   real=feasible_profile(t, t.observed, set(), mode="SP", full=False))
    bps = breakpoints(t) + [None]
    B0 = bps[observed_floor(bps, pr.real)]
    for a in alts:
        full = not (a.kind == "steal" and t.recvs[a.recv].blocking)
        prof = feasible_profile(t, a.M, a.changed, mode="L1", full=full, floor=B0)
        if reads is not None and reads.extends(t, a.changed, a.M):
            p2 = feasible_profile(t, a.M, a.changed, mode="L1", full=full, floor=B0, bounds=reads)
            prof = {B: ok or p2.get(B, False) for B, ok in prof.items()}
        pr.alt.append(prof)
        pr.alt_reason.append(check(t, a.M, a.changed, None, "L1").reason)
    for p, s in probes:
        kw = dict(changed_probes={p.key}, probe_alt={p.key: s.key})
        pr.probe.append(feasible_profile(t, t.observed, set(), mode="L1", full=False, floor=B0, **kw))
        pr.probe_reason.append(check(t, t.observed, set(), None, "L1", **kw).reason)
    for rec, j, r_j, s_j in waits:
        kw = dict(extra_edges=[(s_j.issue_node(), rec.complete)], changed_events={(rec.rank, rec.idx)}, must_issue=[s_j.key])
        pr.wait.append(feasible_profile(t, t.observed, set(), mode="L1", full=False, floor=B0, **kw))
        pr.wait_reason.append(check(t, t.observed, set(), None, "L1", **kw).reason)
    return pr


def skeleton_divergence(t: Trace, o: explore.Outcome) -> List[str]:
    """How an execution of the twin departs from the trace's call sequence: a send to another
    destination or with another tag at the same position, or a different number of
    collectives on a rank. Empty when the single-path assumption holds on this execution. A
    rank whose continuation is undetermined (outcome.unknown) may still issue collectives, so
    only more of them than the trace has counts there."""
    out = []
    for key, dst, tag, comm in o.sent:
        s = t.sends.get(key)
        if s is None:
            out.append(f"rank {key[0]} issues an extra send #{key[1]} to {dst} tag {tag}")
        elif (s.dst, s.tag) != (dst, tag):
            out.append(f"rank {key[0]}'s send #{key[1]} goes to {dst} tag {tag} instead of {s.dst} tag {s.tag}")
    per_rank = {}
    for c in t.colls:
        per_rank[c.rank] = per_rank.get(c.rank, 0) + 1
    open_ranks = {r for r, _ in o.unknown}
    for rank, n in o.ncoll:
        m = per_rank.get(rank, 0)
        if n > m or (n < m and rank not in open_ranks):
            out.append(f"rank {rank} issues {n} collective(s) instead of {m}")
    return out


def classify_probe(p: Probe, s: Send, seen: Send) -> Tuple[str, str]:
    if p.tag == ANY and s.tag != seen.tag:
        return "probe-alt", f"cross-purpose: tag {s.tag} instead of observed tag {seen.tag}"
    if s.bytes != seen.bytes:
        return "probe-alt", f"different size: {s.bytes}B instead of {seen.bytes}B (a count taken from this probe would differ)"
    return "probe-alt", f"same shape, from rank {s.rank} instead of {seen.rank}"


def summary(t: Trace) -> str:
    return (f"trace: {t.np} ranks, {len(t.sends)} sends, {len(t.recvs)} receives, "
            f"{sum(1 for r in t.recvs.values() if r.wildcard)} wildcard receives, {len(t.probes)} probes, "
            f"{len(t.colls)} collective records, {len(t.comm_ranks)} communicators")


JSON_SCHEMA = 2    # 1: the output before it had a schema, a status and finding ids


def finding_ids(rep: Report) -> List[str]:
    """A stable id per finding: its class and a hash of what it is about (family, class, level,
    the changed receive, send and probe, certainty and error, and, for a finding about none of
    them, its title), so that the id stays when a title's wording changes; a second finding
    with the same content gets "-2", and so on."""
    import hashlib
    seen: Dict[str, int] = {}
    out = []
    for f in rep.findings:
        keyed = f.recv is not None or f.send is not None or f.probe is not None
        what = [f.family, f.cls, f.level, f.recv, f.send, f.probe, f.certainty, f.error, None if keyed else f.title]
        h = f"{f.cls}-" + hashlib.sha1(json.dumps(what, default=list).encode()).hexdigest()[:10]
        seen[h] = seen.get(h, 0) + 1
        out.append(h if seen[h] == 1 else f"{h}-{seen[h]}")
    return out


def to_json(t: Trace, rep: Report) -> dict:
    """Everything the text report shows: the summary line, notes, findings and the rejected
    alternatives with their reasons; and what a program reading it needs: the schema version,
    the commit of the analysis code, the status (complete, or degraded:<reasons>, with each
    reason's detail) and an id per finding (README, "Exit status and output")."""
    from .version import analysis_commit
    return {"schema": JSON_SCHEMA, "analysis_commit": analysis_commit(), "status": rep.status(),
            "degraded": [{"reason": r, "detail": d} for r, d in rep.degraded],
            "summary": summary(t), "notes": rep.notes,
            "findings": [{"id": i, **f.__dict__} for i, f in zip(finding_ids(rep), rep.findings)],
            "infeasible": [{"alternative": d, "reason": why} for d, why in rep.infeasible]}


def format_report(t: Trace, rep: Report, show_infeasible: bool = False) -> str:
    lines = [f"status: {rep.status()}" + "".join(f"; {r}: {d}" for r, d in rep.degraded), summary(t)]
    for n in rep.notes:
        lines.append(f"note: {n}")
    if not rep.findings:
        lines.append("no findings")
    for f in rep.findings:
        lines.append(str(f))
    sites = rep.by_site()
    if sites:
        lines.append(f"findings by call site of the changed receive or probe ({len(sites)} sites):")
        for site, counts in sites:
            lines.append(f"  {site}: " + ", ".join(f"{k} {n}" for k, n in sorted(counts.items())))
    if show_infeasible and rep.infeasible:
        lines.append("rejected alternatives:")
        for d, why in rep.infeasible:
            lines.append(f"  x {d}: {why}")
    return "\n".join(lines)
