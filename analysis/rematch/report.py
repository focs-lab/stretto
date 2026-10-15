"""Classify feasible alternatives. See docs/analysis.md section B for the taxonomy."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import dsl, explore
from .feasible import check, feasible_profile, observed_feasible_at, profile_summary
from .matching import Alternative, candidates_full, coll_shape, observed_hb, probe_candidates
from .trace import ANY, Key, Probe, Recv, Send, Trace


@dataclass
class Finding:
    family: str                 # '1' message race, '2' deadlock/ordering, 'waitany', 'probe'
    cls: str                    # '1a'..'1f', '2-buffering', '2-collective', 'waitany-order', 'probe-alt'
    level: str                  # 'L1', 'L1+SP', 'observed'
    title: str
    detail: List[str] = field(default_factory=list)
    B: str = ""                 # human range of thresholds at which the finding is realisable
    recv: Optional[Key] = None
    send: Optional[Key] = None
    pin: Optional[str] = None   # ready-to-use REMATCH_PIN spec for replay

    def __str__(self):
        b = f" [{self.B}]" if self.B else ""
        head = f"[{self.cls:>12}] {self.level:<6} {self.title}{b}"
        return head + "".join(f"\n              - {d}" for d in self.detail) + (f"\n              - replay: {self.pin}" if self.pin else "")


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)
    infeasible: List[Tuple[str, str]] = field(default_factory=list)  # (alt description, reason)
    sp_explored: bool = True
    notes: List[str] = field(default_factory=list)

    def by_class(self, cls: str) -> List[Finding]:
        return [f for f in self.findings if f.cls == cls]

    def classes(self):
        return sorted({f.cls for f in self.findings})

    def levels(self):
        """Classes qualified by the level that established them, e.g. 'L1:1f', 'observed:1c'."""
        return sorted({f"{f.level}:{f.cls}" for f in self.findings})


def _fmt_B(B: Optional[int]) -> str:
    return "inf" if B is None else str(B)


def pin_spec(r: Recv, s: Send) -> Optional[str]:
    if r.widx is None:
        return None
    spec = f"{r.rank}:{r.widx}:{s.rank}"
    if r.tag == ANY:
        spec += f":{s.tag}"
    return "REMATCH_PIN=" + spec


def classify(r: Recv, s: Send, s_obs: Optional[Send]) -> Tuple[str, str]:
    if s.bytes > r.capacity:
        return "1a", f"count mismatch: {s.bytes}B into a {r.capacity}B receive (MPI_ERR_TRUNCATE)"
    if s.tsig is not None and r.tsig is not None and s.tsig != r.tsig:
        return "1a", f"type signature mismatch: send {s.tsig} vs receive {r.tsig}"
    if s_obs is not None and r.tag == ANY and s.tag != s_obs.tag:
        return "1b", f"cross-purpose: tag {s.tag} instead of observed tag {s_obs.tag} (another logical channel)"
    if s_obs is not None and s.rank != s_obs.rank:
        return "1f", f"same shape as the observed message but from rank {s.rank} instead of {s_obs.rank}"
    return "1f", "same shape as the observed message"


def analyze(t: Trace, with_sp: bool = True, max_states: int = 300_000, sp_max_events: int = 2000) -> Report:
    rep = Report()
    rep.notes.extend(t.warnings)
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
                cands = [s for s in t.sends.values() if r.accepts(s) and s.key not in t.observed.values()]
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

    # ---- observed: ranks killed while spinning in a polling loop (a livelock, not a blocked call)
    for rank in t.truncated:
        recs = t.ranks.get(rank, [])
        if not recs:
            continue
        last = recs[-1].raw
        if last.get("op") in ("probe", "wait") and last.get("flag") == 0:
            what = f"{last.get('kind')} src={last.get('src')} tag={last.get('tag')}" if last.get("op") == "probe" else f"{last.get('kind')} on requests {last.get('reqs')}"
            rep.findings.append(Finding("2", "2-blocked", "observed",
                                        f"rank {rank} was spinning in {what} when the run ended",
                                        ["a polling loop whose condition never became true: the run cannot finish"]))

    # ---- observed: messages sent but never received (pending at MPI_Finalize is erroneous)
    cancelled = {(rec.rank, rec.raw.get("req")) for recs in t.ranks.values() for rec in recs if rec.op == "cancel"}
    received = set(t.observed.values())
    for s in sorted(t.sends.values(), key=lambda s: s.key):
        if s.key in received or s.dst in t.missing or (s.req is not None and (s.rank, s.req) in cancelled):
            continue
        if s.dst not in t.ranks:
            continue
        rep.findings.append(Finding("1", "1c", "observed", f"{s} was never received in the observed run",
                                    ["a message left unmatched: pending communication at MPI_Finalize, or a receive that never came"]))

    # ---- family 2: observed collective ordering
    for cs in t.coll_sets():
        names = sorted({c.name for c in cs})
        members = t.comm_ranks.get(cs[0].comm, [])
        if len(names) > 1:
            rep.findings.append(Finding("2", "2-collective", "observed",
                                        f"collective #{cs[0].ordinal} on comm {cs[0].comm}: ranks disagree {names}"))
        elif members and len(cs) != len(members) and all(c.completed for c in cs):
            rep.findings.append(Finding("2", "2-collective", "observed",
                                        f"collective {cs[0].name} #{cs[0].ordinal} on comm {cs[0].comm}: only {len(cs)} of {len(members)} ranks issued it"))

    # ---- family 2: is the observed run itself buffering-dependent?
    prof_obs = feasible_profile(t, t.observed, set(), mode="SP", strong_only=True)
    minB_obs, at_inf, _ = profile_summary(prof_obs)
    if not at_inf:
        rep.findings.append(Finding("2", "2-buffering", "observed",
                                    "observed matching infeasible even with infinite buffering (trace inconsistent or unsupported operations)"))
    elif minB_obs is None or minB_obs > 0:
        v = observed_feasible_at(t, 0)
        cyc = " -> ".join(f"r{n[0]}#{n[1]}{n[2]}" for n in (v.cycle or []))
        bound = minB_obs if minB_obs is not None else "the largest message"
        rep.findings.append(Finding("2", "2-buffering", "observed",
                                    f"observed run deadlocks on any implementation with eager threshold < {bound} bytes",
                                    [f"cycle at B=0: {cyc}" if cyc else f"infeasible at B=0: {v.reason}"],
                                    B=f"B < {bound}"))

    # ---- family 1: alternatives at wildcard receives
    alts, pruned = candidates_full(t)
    rep.infeasible.extend((d, why) for d, why in pruned)
    sp_targets: List[Tuple[Key, Key, Finding]] = []
    for alt in alts:
        prof = feasible_profile(t, alt.M, alt.changed, mode="L1")
        minB, at_inf, brange = profile_summary(prof)
        if minB is None and not at_inf:
            v = check(t, alt.M, alt.changed, None, "L1")
            rep.infeasible.append((alt.describe(t) + f" [{alt.kind}]", v.reason))
            continue
        r, s = t.recvs[alt.recv], t.sends[alt.send]
        s_obs = t.observed_send(r)
        cls, why = classify(r, s, s_obs)
        details = [why,
                   f"L1: the continuation of rank {r.rank} after {r} is undetermined by the trace (horizon)"]
        if alt.kind == "swap":
            details.append(f"swap variant: {t.recvs[alt.stolen_from]} then takes {s_obs}; certified at L1 only up to the first changed receive")
        elif alt.stolen_from is not None:
            details.append(f"steal variant: {t.recvs[alt.stolen_from]} is left without {s}")
        f = Finding("1", cls, "L1", alt.describe(t), details, B=brange, recv=alt.recv, send=alt.send,
                    pin=pin_spec(r, s))
        rep.findings.append(f)
        if alt.kind == "steal" or not any(x[0] == alt.recv and x[1] == alt.send for x in sp_targets):
            sp_targets.append((alt.recv, alt.send, f))

    # ---- single-path consequences via the explorer (MOPPER's setting)
    if with_sp and sp_targets:
        nevents = sum(len(recs) for recs in t.ranks.values())
        if nevents > sp_max_events:
            rep.sp_explored = False
            rep.notes.append(f"L1+SP explorer skipped: {nevents} events > {sp_max_events}")
        else:
            prog = dsl.program_from_trace(t)
            for rk, sk, f in sp_targets:
                for B in (0, None):
                    try:
                        res = explore.explore(prog, B, force={rk: sk}, max_states=max_states, comms=t.comm_ranks)
                    except RuntimeError:
                        rep.sp_explored = False
                        f.detail.append(f"L1+SP at B={_fmt_B(B)}: state space too large, not explored")
                        continue
                    except explore.ExploreError as e:
                        f.detail.append(f"L1+SP at B={_fmt_B(B)}: not explored ({e})")
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
                    tail = f"after {t.recvs[rk]} <- {t.sends[sk]} (single-path assumption, at B={_fmt_B(B)})"
                    if "deadlock" in kinds:
                        rep.findings.append(Finding("1", "1d", "L1+SP", f"deadlock {tail}", B=f"B={_fmt_B(B)}", recv=rk, send=sk))
                    if orphans:
                        rep.findings.append(Finding("1", "1c", "L1+SP", f"orphan send(s) {['s%d.%d' % k for k in orphans]} {tail}", B=f"B={_fmt_B(B)}", recv=rk, send=sk))
                    if "truncate" in kinds:
                        rep.findings.append(Finding("1", "1a", "L1+SP", f"truncation error {tail}", B=f"B={_fmt_B(B)}", recv=rk, send=sk))
            uniq, out = set(), []
            for f in rep.findings:
                sig = (f.cls, f.level, f.title.split(" (single-path")[0])
                if sig in uniq:
                    continue
                uniq.add(sig)
                out.append(f)
            rep.findings = out

    # ---- probe alternatives
    for p, s in probe_candidates(t):
        v = check(t, t.observed, set(), None, "L1", changed_probes={p.key})
        if not v.feasible:
            rep.infeasible.append((f"{p} sees {s}", v.reason))
            continue
        seen = t.sends[p.seen]
        cls, why = classify_probe(p, s, seen)
        pin = f"REMATCH_PIN_PROBE={p.rank}:{p.pidx}:{s.rank}" + (f":{s.tag}" if p.tag == ANY else "") if p.pidx is not None else None
        rep.findings.append(Finding("probe", cls, "L1", f"{p} could observe {s} instead of {seen}",
                                    [why, f"L1: a receive whose source or count is taken from this probe's status changes with it; continuation of rank {p.rank} undetermined"],
                                    B="any B", pin=pin))

    # ---- Waitany completion-order alternatives
    hb = observed_hb(t)
    for rec in t.waitany:
        raw = rec.raw
        reqs = raw.get("reqs", [])
        done = raw.get("done", [])
        if not done or len([q for q in reqs if q is not None and q >= 0]) < 2:
            continue
        for j, q in enumerate(reqs):
            if q is None or q < 0 or j == raw.get("idx"):
                continue
            r_j = t.recv_by_req(rec.rank, q)
            if r_j is None or not r_j.completed:
                continue
            s_j = t.observed_send(r_j)
            if s_j is None or hb(rec.complete, s_j.issue_node()):
                continue
            v = check(t, t.observed, set(), None, "SP", extra_edges=[(s_j.issue_node(), rec.complete)])
            if not v.feasible:
                rep.infeasible.append((f"Waitany rank {rec.rank} #{rec.idx} index {j}", v.reason))
                continue
            rep.findings.append(Finding("waitany", "waitany-order", "L1",
                                        f"Waitany at rank {rec.rank} #{rec.idx} could complete index {j} ({r_j} <- {s_j}) instead of {raw.get('idx')}",
                                        [f"L1: continuation of rank {rec.rank} is undetermined if it depends on the index"], B="any B"))
    return rep


def classify_probe(p: Probe, s: Send, seen: Send) -> Tuple[str, str]:
    if p.tag == ANY and s.tag != seen.tag:
        return "probe-alt", f"cross-purpose: tag {s.tag} instead of observed tag {seen.tag}"
    if s.bytes != seen.bytes:
        return "probe-alt", f"different size: {s.bytes}B instead of {seen.bytes}B (a count taken from this probe would differ)"
    return "probe-alt", f"same shape, from rank {s.rank} instead of {seen.rank}"


def format_report(t: Trace, rep: Report, show_infeasible: bool = False) -> str:
    lines = [f"trace: {t.np} ranks, {len(t.sends)} sends, {len(t.recvs)} receives, "
             f"{sum(1 for r in t.recvs.values() if r.wildcard)} wildcard receives, {len(t.probes)} probes, "
             f"{len(t.colls)} collective records, {len(t.comm_ranks)} communicators"]
    for n in rep.notes:
        lines.append(f"note: {n}")
    if not rep.findings:
        lines.append("no findings")
    for f in rep.findings:
        lines.append(str(f))
    if show_infeasible and rep.infeasible:
        lines.append("rejected alternatives:")
        for d, why in rep.infeasible:
            lines.append(f"  x {d}: {why}")
    return "\n".join(lines)
