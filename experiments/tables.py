#!/usr/bin/env python3
"""Compose docs/results.md from the experiment CSVs, and the paper's numbers
(paper/generated/numbers.tex, one macro per number, and paper/generated/rq2.tex, the body of
the effectiveness table). Re-run after any experiment."""
from __future__ import annotations
import collections
import csv
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RES = os.path.join(ROOT, "experiments", "results")
sys.path.insert(0, os.path.join(ROOT, "experiments", "mbi"))
import score  # noqa: E402  (the one definition of verdicts)

rows = score.rows
NUM: dict = {}       # macro name -> text, written to paper/generated/numbers.tex
CONF: dict = {}      # (corpus, row label) -> confusion counts, for paper/generated/rq2.tex


def num(name: str, value) -> None:
    NUM[name] = value if isinstance(value, str) else (f"{value:.2f}" if isinstance(value, float) else f"{value:,}".replace(",", "\\,"))


def commits(rs) -> str:
    """The code commits a table's rows were produced with, most rows first."""
    c = collections.Counter(r.get("commit") or "unrecorded" for r in rs)
    return ", ".join(f"{k} ({v})" for k, v in c.most_common())


def conf_row(label, c):
    return f"| {label} | {c['tp']} | {c['fp']} | {c['fn']} | {c['tn']} | {c['precision']:.2f} | {c['recall']:.2f} | {c['f1']:.2f} |"


def e1(name, path, key=None):
    rs = rows(path)
    if not rs:
        return f"### {name}\n\n_no results_\n"
    commit_note = f"Rows by code commit: {commits(rs)}."
    ce = [r for r in rs if r.get("build") == "CE"]
    rs = score.compiled(rs)
    skipped = f" Not compiled with Open MPI 4.1.6 and left out: {len(ce)} ({', '.join(sorted(r['name'] for r in ce))})." if ce else ""
    out = [f"### {name}", "", f"Programs: {len(rs)}.{skipped} {commit_note}", "",
           "A program's verdict is ERROR iff a finding of class 1a/1c/1d/2-buffering/2-collective/2-blocked is",
           "present; 1b, 1f, probe-alt and waitany-order are warnings and never count. The *observed* row uses",
           "only what the run itself showed (a blocked or spinning rank, a collective mismatch, a message never",
           "received), which any observational tool sees; the *predicted* row only what Stretto says about other",
           "runs (a mismatch certain from the trace, a deadlock or orphan under the single-path assumption, a",
           "finished run that would deadlock below a threshold); a 2-buffering finding on a run that did not",
           "finish is the hang itself and counts as observed. The union is the tool's verdict.", "",
           "| | TP | FP | FN | TN | precision | recall | F1 |", "|---|---|---|---|---|---|---|---|",
           conf_row("Stretto (union)", score.confusion(rs, score.verdict)),
           conf_row("Stretto, observed in the run", score.confusion(rs, score.observed_verdict)),
           conf_row("Stretto, predicted", score.confusion(rs, score.predicted_verdict))]
    if key:
        num(f"n{key}progs", len(rs))
        num(f"n{key}correct", sum(1 for r in rs if r["expect"] == "OK"))
        for label, judge in (("union", score.verdict), ("observed", score.observed_verdict), ("predicted", score.predicted_verdict),
                             ("MUST", score.must_verdict), ("MUSTsym", score.must_verdict_hangs)):
            if label.startswith("MUST") and not any(r.get("must") for r in rs):
                continue
            c = CONF[(key, label)] = score.confusion(rs, judge)
            num(f"{key}fone{label}", c["f1"])
        num(f"n{key}fp", CONF[(key, "union")]["fp"])
    if any(r.get("must") for r in rs):
        kinds = collections.Counter(score.must_kind(r) for r in rs)
        out.append(conf_row("MUST 1.9.2 (same source, its own run under MPICH)", score.confusion(rs, score.must_verdict)))
        out.append(conf_row("MUST 1.9.2, a timeout counted as an error", score.confusion(rs, score.must_verdict_hangs)))
        out += ["", "MUST verdicts: " + ", ".join(f"{k or 'none'} {v}" for k, v in sorted(kinds.items())) + ". In the first MUST row a run",
                "that timed out, crashed or did not compile counts as no error reported. A Stretto run that the harness",
                "killed at the timeout is reported as a blocked rank (2-blocked), an error, so the second MUST row credits",
                "MUST's timeouts the same way; the two rows bracket the comparison. Both tools ran each program once, with",
                "the same timeout; MUST under MPICH, Stretto under Open MPI, so they did not see the same run."]
        failed = [r["name"] for r in rs if score.analysis_failed(r)]
        if failed:
            out += ["", f"Analysis failed on {len(failed)} program(s), counted as no error: {', '.join(failed)}."]
        inv = [r for r in rs if r["expect"] == "ERROR" and score.verdict(r) == "ERROR" and score.must_verdict(r) != "ERROR"]
        inv_pred = [r for r in inv if score.predicted_verdict(r) == "ERROR" and score.observed_verdict(r) != "ERROR"]
        conf = [r for r in inv if r.get("confirmed") and int(r["confirmed"]) > 0]
        if key:
            num(f"n{key}CleanPred", len(inv_pred))
            num(f"n{key}CleanTemplates", len({"_".join(r["name"].split("_")[:2]) for r in inv_pred}))
            num(f"n{key}CleanConfirmed", sum(1 for r in inv_pred if r.get("confirmed") and int(r["confirmed"]) > 0))
        out += ["", f"Labelled errors Stretto reports and MUST does not on the same program: **{len(inv)}**, of which",
                f"predicted on a run that showed nothing itself: **{len(inv_pred)}**, replay-confirmed (a pinned rerun with",
                f"the pin applied hangs or raises the predicted MPI error): **{len(conf)}**."]
    race = [r for r in rs if r.get("detail") == "MessageRace"]
    if key:
        num(f"n{key}race", len(race))
    if race:
        clean = [r for r in race if r["run"] == "FINISHED"]
        flag = [r for r in race if score.flagged(r)]
        cert = [r for r in race if score.certified(r)]
        conf = [r for r in race if r.get("confirmed") and int(r["confirmed"]) > 0]
        out += ["", "Message-race programs (label `MessageRace`):", "",
                "| total | clean observed runs | racing receive flagged (L1) | consequence certified (L1+SP) | replay-confirmed | observed deadlock/abort |", "|---|---|---|---|---|---|",
                f"| {len(race)} | {len(clean)} | {len(flag)} (clean: {sum(1 for r in clean if r in flag)}/{len(clean)}) | {len(cert)} (clean: {sum(1 for r in clean if r in cert)}/{len(clean)}) | {len(conf)} | {len(race) - len(clean)} |"]
    wild = [r for r in rs if r["expect"] == "ERROR" and (r.get("anysrc") == "True" or r.get("anytag") == "True") and r.get("detail") != "MessageRace"]
    if wild:
        clean = [r for r in wild if r["run"] == "FINISHED"]
        flag = [r for r in wild if score.flagged(r)]
        err = [r for r in wild if score.verdict(r) == "ERROR"]
        conf = [r for r in wild if r.get("confirmed") and int(r["confirmed"]) > 0]
        mus = [r for r in wild if score.must_verdict(r) == "ERROR"]
        out += ["", "Labelled-error programs that use a wildcard (`MPI_ANY_SOURCE` or `MPI_ANY_TAG`):", "",
                "| total | clean observed runs | wildcard alternative flagged (L1) | error reported | replay-confirmed | MUST error |", "|---|---|---|---|---|---|",
                f"| {len(wild)} | {len(clean)} | {len(flag)} | {len(err)} | {len(conf)} | {len(mus)} |"]
    wl = [r for r in rs if r.get("l1_alternatives") not in (None, "") and int(r.get("wildcard_recvs") or 0) > 0]
    if wl:
        alts = sum(int(r["l1_alternatives"]) for r in wl)
        recvs = sum(int(r["wildcard_recvs"]) for r in wl)
        out += ["", f"Wildcard competition (the w ablation): {len(wl)} programs with wildcard receives, {recvs} wildcard receives,",
                f"{alts} feasible L1 alternatives, {alts / recvs:.2f} per wildcard receive on average, at most {max(int(r['l1_alternatives']) for r in wl)} in one program."]
    ok = [r for r in rs if r["expect"] == "OK"]
    nz = score.noise(ok)
    warned = [r for r in ok if set(r.get("classes", "").split()) & score.WARN]
    out += ["", f"Correct programs: {len(ok)}; with warnings (not counted as errors): {nz['with_warnings']}; findings per program",
            f"mean {nz['mean_findings']:.2f}, max {nz['max_findings']}."]
    if warned:
        c = collections.Counter(r["classes"] for r in warned)
        out += ["Warnings on correct programs by class: " + ", ".join(f"`{k}` {v}" for k, v in sorted(c.items())) + "."]
    rp = score.replay_rows(path)
    if rp:
        c = collections.Counter((r["cls"], r["verdict"]) for r in rp)
        runs = {(r["program"], r["pin"]) for r in rp if r["verdict"] != "n/a"}
        progs = {r["program"] for r in rp if r["verdict"] != "n/a"}
        matched_col = "pin_matched" in rp[0]
        rule = ("a finding is confirmed only if every pinned receive matched in the rerun, the rerun reached the predicted "
                "outcome, and a control rerun with the observed senders pinned finished; inconclusive when the control did not finish"
                if matched_col else
                "recorded before match logging and control reruns: 'confirmed' means the rerun reported its pin applied and "
                "reached the predicted outcome, which an impossible forced match also does")
        if key:
            judged = [r for r in rp if r["verdict"] != "n/a"]
            num(f"n{key}ReplayFindings", len(judged))
            num(f"n{key}ReplayRuns", len(runs))
            num(f"n{key}ReplayPrograms", len(progs))
            for v, m in (("confirmed", "Confirmed"), ("inconclusive", "Inconclusive"), ("not-confirmed", "NotConfirmed")):
                num(f"n{key}Replay{m}", sum(1 for r in judged if r["verdict"] == v))
        out += ["", f"Replays: {len(rp)} rows (one per finding and class after removing duplicate pins), {len(runs)} distinct "
                f"pinned runs, {len(progs)} programs ({rule}):", "",
                "| class | confirmed | inconclusive | not confirmed | n/a |", "|---|---|---|---|---|"]
        for cls in sorted({r["cls"] for r in rp}):
            out.append(f"| {cls} | {c[(cls, 'confirmed')]} | {c[(cls, 'inconclusive')]} | {c[(cls, 'not-confirmed')]} | {c[(cls, 'n/a')]} |")
        nc = [r for r in rp if r["verdict"] in ("not-confirmed", "inconclusive")]
        if nc:
            out += ["", "Not confirmed or inconclusive:", "", "| program | class | certainty | pin | result | pin applied | pin matched | control |",
                    "|---|---|---|---|---|---|---|---|"]
            out += [f"| {r['program']} | {r['cls']} | {r.get('certainty', '')} | {r['pin']} | {r['result']} | {r.get('pin_applied', '')} "
                    f"| {r.get('pin_matched', '')} | {r.get('control', '')} |" for r in nc]
    c = collections.Counter((r["expect"], r["detail"], r["run"].split(":")[0], r["classes"], score.must_kind(r)) for r in rs)
    out += ["", "| label | detail | observed run | Stretto classes | MUST | programs |", "|---|---|---|---|---|---|"]
    for k, v in sorted(c.items()):
        out.append(f"| {k[0]} | {k[1]} | {k[2]} | {k[3] or '-'} | {k[4] or '-'} | {v} |")
    return "\n".join(out) + "\n"


def e2():
    rs = rows(os.path.join(RES, "e2_buffering.csv"))
    if not rs:
        return "_no results_\n"
    obs = [r for r in rs if r["what"] == "observed"]
    alt = [r for r in rs if r["what"] != "observed"]
    dep = [r for r in alt if r["B0"] != r["Binf"]]
    nonmono = [r for r in rs if r["profile"].startswith("B in ")]
    num("nEtwoTraces", len(obs))
    num("nEtwoObsBuf", sum(1 for r in obs if r["B0"] == "False"))
    num("nEtwoAlts", len(alt))
    num("nEtwoBdep", len(dep))
    num("nEtwoNonmono", len(nonmono))
    out = [f"Rows by code commit: {commits(rs)}.", "",
           "Every alternative and every observed run is checked at every breakpoint (0, each message size, inf);",
           f"profiles that are not monotone in B: {len(nonmono)}.", "",
           "| traces | observed runs infeasible at B=0 (deadlock on a rendezvous implementation) | feasible alternatives | feasible at B=0 | at B=4096 | at B=inf | B-dependent |", "|---|---|---|---|---|---|---|",
           f"| {len(obs)} | {sum(1 for r in obs if r['B0'] == 'False')} | {len(alt)} | {sum(1 for r in alt if r['B0'] == 'True')} | {sum(1 for r in alt if r['B4096'] == 'True')} | {sum(1 for r in alt if r['Binf'] == 'True')} | {len(dep)} |"]
    if dep:
        out += ["", "B-dependent alternatives:", "", "| trace | alternative | profile |", "|---|---|---|"]
        out += [f"| {r['trace']} | {r['what']} | {r['profile']} |" for r in dep[:20]]
    bi = rows(os.path.join(RES, "b_impl.csv"))
    if bi:
        out += ["", "Eager threshold of the implementation (`experiments/b_impl.py`: a head-to-head exchange of n bytes finishes",
                "when the message is eager and hangs when it is rendezvous), per send path, since Open MPI sends up to 256",
                "bytes inline on the MPI_Send and MPI_Isend paths whatever the shared-memory limit is:", "",
                "| setting | path | mpirun arguments | largest eager payload (bytes) | smallest rendezvous payload |", "|---|---|---|---|---|"]
        out += [f"| {r['setting']} | {r.get('path', 'send')} | `{r['mpirun_args'] or '-'}` | {r['eager_max_bytes']} | {r['rendezvous_min_bytes']} |" for r in bi]
        for r in bi:
            path_ = r.get("path") or "send"
            if r["setting"] == "openmpi-default" and path_ == "send":
                num("BimplOMPI", int(r["eager_max_bytes"]))          # the default eager payload
            if r["setting"] == "openmpi-forced-rendezvous" and path_ == "send":
                num("BminOMPI", int(r["eager_max_bytes"]))           # the inline sends the forced limit leaves
            if r["setting"] == "openmpi-tcp-64" and path_ == "send":
                num("BtcpOMPI", int(r["eager_max_bytes"]))           # the TCP limit minus its header
    rp = rows(os.path.join(RES, "e2_replay.csv"))
    if rp:
        new = any(r["setting"] in ("below the bound", "at the bound") for r in rp)
        if new:
            dflt = [r for r in rp if r["setting"] == "default"]
            below = [r for r in rp if r["setting"] == "below the bound"]
            at = [r for r in rp if r["setting"] == "at the bound"]
            ok = lambda rs: sum(1 for r in rs if r["verdict"] == "as predicted")
            unt = [r for r in below if r["verdict"] == "untestable"]
            bad = [r for r in rp if r["verdict"] == "not as predicted"]
            num("nEtwoReplayPrograms", len(dflt))
            num("nEtwoReplayDefaultOK", ok(dflt))
            num("nEtwoReplayBelowOK", ok(below))
            num("nEtwoReplayBelowTestable", len(below) - len(unt))
            num("nEtwoReplayAtOK", ok(at))
            num("nEtwoReplayAt", len(at))
            num("nEtwoReplayBad", len(bad))
            out += ["", f"Replays of the {len(dflt)} programs whose observed run finished and is realisable only from a threshold on",
                    "(`e2_buffering.py --replay`): with the default limits the run must finish again; on the TCP transport, whose",
                    "every send path buffers the eager limit minus its header, it must hang with the threshold one byte below the",
                    "bound and finish with it at the bound. As predicted: default "
                    f"{ok(dflt)}/{len(dflt)}, below the bound {ok(below)}/{len(below) - len(unt)} ({len(unt)} untestable: self-sends,",
                    f"which the self transport sends inline up to 256 bytes), at the bound {ok(at)}/{len(at)}. Not as predicted: {len(bad)}"
                    + (" (" + ", ".join(f"{r['trace'].split('/')[-2]} {r['setting']} {r['result']}" for r in bad) + ")" if bad else "") + ".", "",
                    "| trace | bound | default | below the bound | at the bound |", "|---|---|---|---|---|"]
            by = {}
            for r in rp:
                by.setdefault((r["trace"], r["bound"]), {})[r["setting"]] = f"{r['result']} ({r['verdict']})"
            out += [f"| {k[0]} | B < {k[1]} | {v.get('default', '')} | {v.get('below the bound', '')} | {v.get('at the bound', '')} |"
                    for k, v in sorted(by.items())]
        else:
            out += ["", "Replays recorded before 20 Oct 2026 with the shared-memory limit forced down; that setting leaves the",
                    "MPI_Send and MPI_Isend paths eager up to 256 bytes and moves only persistent sends, so it is superseded by",
                    "the TCP setting (`b_impl.py`)."]
    return "\n".join(out) + "\n"


def stress(name, path, key=None):
    """The stress-testing baseline (experiments/stress_baseline.py) against Stretto's one run."""
    st = rows(os.path.join(os.path.dirname(path), "stress.csv"))
    if not st:
        return ""
    by = {r["name"]: r for r in score.compiled(rows(path))}
    err = [r for r in st if r["expect"] == "ERROR" and r["program"] in by]
    ok = [r for r in st if r["expect"] == "OK" and r["program"] in by]
    found = [r for r in err if r["first_failure"]]
    later = [r for r in found if int(r["first_failure"]) > 1]
    runs = max((int(r["runs"]) for r in st), default=0)
    rem = [r for r in err if score.verdict(by[r["program"]]) == "ERROR"]
    pred = [r for r in err if score.predicted_verdict(by[r["program"]]) == "ERROR"]
    pred_missed = [r for r in pred if not r["first_failure"]]
    fp = [r for r in ok if r["first_failure"]]
    firsts = sorted(int(r["first_failure"]) for r in found)
    med = firsts[len(firsts) // 2] if firsts else "-"
    if key:
        tp, fn_ = len(found), len(err) - len(found)
        fpn, tn = len(fp), len(ok) - len(fp)
        prec = tp / (tp + fpn) if tp + fpn else 0.0
        rec = tp / (tp + fn_) if tp + fn_ else 0.0
        CONF[(key, "stress")] = {"tp": tp, "fp": fpn, "fn": fn_, "tn": tn, "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0}
        num(f"{key}fonestress", CONF[(key, "stress")]["f1"])
        num(f"n{key}StressRuns", runs)
        num(f"n{key}StressErr", len(err))
        num(f"n{key}StressFound", len(found))
        num(f"n{key}StressLater", len(later))
        num(f"n{key}StressMedian", med if isinstance(med, str) else int(med))
        num(f"n{key}StressPred", len(pred))
        num(f"n{key}StressPredMissed", len(pred_missed))
        num(f"n{key}StressFP", len(fp))
    return "\n".join([
        f"### Stress-testing baseline: {name}", "",
        f"Up to {runs} runs per program with random send and receive delays per rank, stopping at the first run that",
        "does not finish (`experiments/stress_baseline.py`).", "",
        f"Labelled-error programs: {len(err)}. Stress testing makes {len(found)} fail (median run of the first failure {med};",
        f"{len(later)} only after the first run). Stretto reports {len(rem)} from one run, {len(pred)} of them predicted from a",
        f"run that finished; stress testing never makes {len(pred_missed)} of those fail"
        + (f" ({', '.join(r['program'] for r in pred_missed)})" if pred_missed else "") + ".",
        f"Correct programs that failed under stress (a hang under load or a delay): {len(fp)}.", ""])


def hypre():
    """The real bug: HYPRE 2.10.1's ParaSails hang (experiments/hypre/run.sh)."""
    d = os.path.join(RES, "hypre")
    rp = rows(os.path.join(d, "replays.csv"))
    summ = os.path.join(d, "summary.txt")
    if not rp or not os.path.exists(summ):
        return "_no results_\n"
    out = ["HYPRE 2.10.1, ParaSails-PCG through `test/ij -solver 8 -pout 0`, 4 ranks, MPICH build, one traced run per",
           "version; the probe alternatives of the request exchange (tag 222) replayed with a probe pin, each with a",
           "control that pins the same probe to the sender it saw (`experiments/hypre/run.sh`, `summary.txt`).", "",
           "| pin | role | result | probe found the pinned message | final residual |", "|---|---|---|---|---|"]
    out += [f"| {r['pin']} | {r['role']} | {r['result']} | {'yes' if r['probe_found'] not in ('', '0') else 'no'} | {r['residual'] or '-'} |" for r in rp]
    out += ["", "```", open(summ).read().strip(), "```", ""]
    return "\n".join(out)


def hypre_campaign():
    """How often one trace exposes HYPRE's race, and how often stress testing hits it
    (experiments/hypre/campaign.sh: predict.csv and stress.csv)."""
    d = os.path.join(RES, "hypre")
    pr = rows(os.path.join(d, "predict.csv"))
    st = rows(os.path.join(d, "stress.csv"))
    if not pr and not st:
        return "_no results_\n"
    out = []
    if pr:
        out += ["Traced runs of `ij -solver 8 -pout 0`; for each, every probe alternative of the request exchanges (tag 222),",
                "replayed with the pins of its whole horizon and with its control (`experiments/hypre/predict.py`). An alternative",
                "is *cross* if the probe would take a request of the other exchange (the race), *same* if another neighbour's",
                "request of the same exchange; rows from before that classification selected requests of a different size and",
                f"count as cross. Rows by code commit: {commits(pr)}.", "",
                "| ranks | kind | traced runs | runs with one | runs with a hanging replay | alternatives | replay hangs | replay finishes | required pins matched | controls finish | finished with the control's residual |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in pr:
            r.setdefault("kind", "cross")
            r["kind"] = r["kind"] or "cross"
        for np_ in sorted({r["np"] for r in pr}, key=int):
            sel = [r for r in pr if r["np"] == np_]
            runs = {r["run"] for r in sel}
            bad = {r["run"] for r in sel if (r.get("replay") or "").startswith("traced run")}
            k = f"Hypre{'Four' if np_ == '4' else 'Eight' if np_ == '8' else np_}"
            num(f"n{k}Runs", len(runs) - len(bad))
            for kind, macro in (("cross", ""), ("same", "Local")):
                alts = [r for r in sel if r.get("pin") and r["kind"] == kind]
                if kind == "same" and not alts:
                    continue
                hang = [r for r in alts if r.get("replay") == "TIMEOUT"]
                fin = [r for r in alts if r.get("replay") == "FINISHED"]
                matched = [r for r in alts if r.get("matched") == "True"]
                ctl = [r for r in alts if r.get("control") == "FINISHED"]
                same_res = [r for r in fin if r.get("residual") and r.get("residual") == r.get("control_residual")]
                res_known = "residual" in sel[0]
                with_one = {r["run"] for r in alts}
                hang_runs = {r["run"] for r in hang}
                res_col = f"{len(same_res)}/{len(fin)}" if res_known and fin else "-" if res_known else "not recorded"
                out.append(f"| {np_} | {kind} | {len(runs) - len(bad)} (+{len(bad)} failed) | {len(with_one)} | {len(hang_runs)} | {len(alts)} | "
                           f"{len(hang)} | {len(fin)} | {len(matched)}/{len(alts)} | {len(ctl)}/{len(alts)} | {res_col} |")
                num(f"n{k}{macro}{'Exposed' if kind == 'cross' else 'Runs'}", len(with_one))
                num(f"n{k}{macro}HangRuns", len(hang_runs))
                num(f"n{k}{macro}Alts", len(alts))
                num(f"n{k}{macro}Hang", len(hang))
                num(f"n{k}{macro}Finish", len(fin))
                num(f"n{k}{macro}Matched", len(matched))
                num(f"n{k}{macro}Controls", len(ctl))
                if res_known and fin:
                    num(f"n{k}{macro}SameResidual", len(same_res))
        sites = collections.Counter(r.get("site") for r in pr if r.get("pin") and r.get("site"))
        if sites:
            out += ["", "Changed probes by call site: " + ", ".join(f"`{s}` {n}" for s, n in sites.most_common()) + "."]
        # the pin is rank:probe ordinal:source; the probe is the same program point across runs
        # of one configuration only as far as the ranks probe in the same order, which they do here
        by_probe = collections.defaultdict(collections.Counter)
        for r in pr:
            if r.get("pin"):
                rank, idx, _src = r["pin"].split("=", 1)[1].split(":")
                by_probe[(int(r["np"]), int(rank), int(idx), r["kind"])][r.get("replay")] += 1
        if by_probe:
            out += ["", "By changed probe (rank, ordinal of its probe):", "",
                    "| ranks | probe | kind | alternatives | replay hangs | replay finishes |", "|---|---|---|---|---|---|"]
            for (np_, rank, idx, kind), c in sorted(by_probe.items()):
                out.append(f"| {np_} | {rank}:{idx} | {kind} | {sum(c.values())} | {c['TIMEOUT']} | {c['FINISHED']} |")
    if st:
        out += ["", "Untraced runs, each with a 60 s timeout: plain, and with random send and receive delays of 0 to 50 ms",
                f"per rank from the replay library (no pin). Rows by code commit: {commits(st)}.", "",
                "| ranks | setting | runs | finished | timed out | other |", "|---|---|---|---|---|---|"]
        for np_ in sorted({r["np"] for r in st}, key=int):
            for setting in ("plain", "delays"):
                sel = [r for r in st if r["np"] == np_ and r["setting"] == setting]
                if not sel:
                    continue
                f_ = sum(1 for r in sel if r["result"] == "FINISHED")
                t_ = sum(1 for r in sel if r["result"] == "TIMEOUT")
                out.append(f"| {np_} | {setting} | {len(sel)} | {f_} | {t_} | {len(sel) - f_ - t_} |")
        num("nHypreStressRuns", len(st))
        num("nHypreStressHang", sum(1 for r in st if r["result"] == "TIMEOUT"))
    return "\n".join(out) + "\n"


def amg_sample():
    """A sample of AMG's alternatives replayed with horizon pins and controls (replay_sample.py)."""
    rs = rows(os.path.join(RES, "amg", "replay_sample.csv"))
    if not rs:
        return ""
    c = collections.Counter((r.get("replay"), r.get("control")) for r in rs)
    matched = sum(1 for r in rs if r.get("matched") == "True")
    num("nAMGsample", len(rs))
    num("nAMGsampleFinish", sum(1 for r in rs if r.get("replay") == "FINISHED"))
    num("nAMGsampleMatched", matched)
    num("nAMGsampleControls", sum(1 for r in rs if r.get("control") == "FINISHED"))
    return "\n".join(["", f"A random sample of {len(rs)} alternatives replayed with the pins of their horizon and a control",
                       f"(`experiments/replay_sample.py`); required pins matched in {matched}. Rows by code commit: {commits(rs)}.", "",
                       "| replay | control | alternatives |", "|---|---|---|"] +
                      [f"| {k[0]} | {k[1]} | {v} |" for k, v in sorted(c.items(), key=str)]) + "\n"


def write_paper_numbers():
    """paper/generated/numbers.tex: one macro per number of this run (macros not computed here
    keep their provisional values from STATIC); paper/generated/rq2.tex: the effectiveness table body."""
    gen = os.path.join(ROOT, "paper", "generated")
    os.makedirs(gen, exist_ok=True)
    lines = ["% Written by experiments/tables.py from the CSV files under experiments/results/. Do not edit.", ""]
    for k in sorted(NUM):
        lines.append(f"\\newcommand{{\\{k}}}{{{NUM[k]}\\xspace}}")
    lines += ["", "% Not produced by this run: provisional values, marked as such in the draft."]
    for k, v in STATIC.items():
        if k not in NUM:
            lines.append(f"\\newcommand{{\\{k}}}{{\\prov{{{v}}}\\xspace}}")
    open(os.path.join(gen, "numbers.tex"), "w").write("\n".join(lines) + "\n")
    body = []
    labels = (("union", "\\tool, observed or predicted"), ("observed", "\\tool, observed in the run"),
              ("predicted", "\\tool, predicted"), ("MUST", "\\MUST, a timeout not counted"), ("MUSTsym", "\\MUST, a timeout counted as an error"),
              ("stress", "random delays, up to \\n{key}StressRuns runs"))
    for key, title in (("MBI", "MBI"), ("MBB", "\\MBB level~2")):
        if not any((key, l) in CONF for l, _ in labels):
            continue
        body.append(f"\\multicolumn{{6}}{{@{{}}l}}{{\\emph{{{title}, \\n{key}progs programs}}}} \\\\")
        for l, text in labels:
            c = CONF.get((key, l))
            if c:
                label = text.replace("{key}", key)
                if l == "stress":       # the baseline runs a subset of the corpus (stress_baseline.py --select)
                    label += f" ({c['tp'] + c['fp'] + c['fn'] + c['tn']} programs)"
                body.append(f"{label} & {c['tp']} & {c['fp']} & {c['fn']} & {c['tn']} & {c['f1']:.2f} \\\\")
        body.append("\\midrule")
    if body and body[-1] == "\\midrule":
        body.pop()
    open(os.path.join(gen, "rq2.tex"), "w").write("% Written by experiments/tables.py. Do not edit.\n" + "\n".join(body) + "\n")
    print("wrote", os.path.join(gen, "numbers.tex"), "and rq2.tex")


# numbers the corpus rerun does not produce: the timing leg, Nekbone and the exposure study
STATIC = {"TraceOverhead": "0.99--1.07", "nAMGalts": "3\\,263", "AMGtime": "13.2", "nNekAlts": "10\\,000", "nNekRejected": "8\\,759",
          "NekRecords": "10.4", "NekMinutes": "63", "NekGB": "33", "nProxyWild": "4", "nProxyAnySource": "3", "nProxy": "14"}


def e6():
    rs = rows(os.path.join(RES, "e6_checker_vs_explorer.csv"))
    if not rs:
        return "_no results_\n"
    out = ["The checker in L1 mode (the claim the report makes) against exhaustive exploration of the trace as a",
           "straight-line program, at B = 0 and B = inf. Unsound: the checker accepts and no execution realises the",
           "pairs of the alternative on the receives inside its cut. Incomplete: some execution agrees with the",
           "alternative on every receive inside the cut and the checker rejects. Exact: some execution realises the",
           "whole matching, beyond the horizon too, which the single-change checker does not claim.", "",
           "| traces | pairs (alternative, B) | agree | unsound | incomplete | skipped (explorer cap) | exact as well | events per trace, max | checker time | explorer time |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for label, sel in (("corpus and idiom traces", [r for r in rs if not r["trace"].startswith("synthetic/")]),
                       ("synthetic traces", [r for r in rs if r["trace"].startswith("synthetic/")])):
        if not sel:
            continue
        c = collections.Counter(r["kind"] for r in sel)
        tc = sum(float(r["t_checker_ms"]) for r in sel)
        te = sum(float(r["t_explorer_ms"]) for r in sel)
        exact = sum(1 for r in sel if r["kind"] == "agree" and r["checker"] == "True" and r.get("explorer_exact") == "True")
        traces = len({r["trace"] for r in sel})
        ev = max((int(r["events"]) for r in sel if r.get("events")), default=0)
        out.append(f"| {label} ({traces}) | {len(sel)} | {c['agree']} | {c['UNSOUND']} | {c['incomplete']} | {c['skip']} | {exact} | {ev} | {tc:.0f} ms | {te:.0f} ms |")
    c = collections.Counter(r["kind"] for r in rs)
    t7 = collections.Counter(r.get("l1_deadlock", "") for r in rs)
    num("nEsixPairs", len(rs))
    num("nEsixUnsound", c["UNSOUND"])
    num("nEsixIncomplete", c["incomplete"])
    num("nEsixMaxEvents", max((int(r["events"]) for r in rs if r.get("events")), default=0))
    num("nEsixTraces", len({r["trace"] for r in rs}))
    num("nEsixLdead", t7["ok"])
    num("nEsixLdeadUnsound", t7["UNSOUND"])
    out += ["", f"Contained deadlocks (theorem T7, class 1d at L1) among the accepted pairs: {t7['ok']} confirmed by the explorer,",
            f"{t7['UNSOUND']} not. Rows by code commit: {commits(rs)}."]
    return "\n".join(out) + "\n"


def e4():
    rs = rows(os.path.join(RES, "e4", "e4.csv"))
    if not rs:
        return "_no results_\n"
    out = ["Plain, traced and analysis times from one timed leg (`experiments/timing_leg.sh`, log in",
           "`experiments/results/timing-leg.log`) on the second machine, announced",
           "to the other lanes: 16 ranks on the container's 12 cores (24 hardware threads), the analysis",
           "one process, at commit 404927c (before b2788c5 and 2f66592). The 4-rank rows are the leg's rerun of that set after a 6 s configure",
           "step of another lane overlapped the first pass; both passes are in the log.", "",
           "| benchmark | class | np | plain (s) | traced (s) | slowdown | trace (MB) | events | wildcards | analysis (s) | findings |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rs:
        out.append(f"| {r['benchmark']} | {r['class']} | {r['np']} | {float(r['plain_s']):.2f} | {float(r['traced_s']):.2f} | {r['slowdown']} | {r['trace_MB']} | {r['events']} | {r['wildcards']} | {float(r['analysis_s']):.1f} | {r['findings']} {r['classes']} |")
    return "\n".join(out) + "\n"


def e5():
    """E5 reads only committed files: summary8.txt (trace header, findings by class, analysis time
    and memory), confirm8.txt (the pinned replay) and must8-<layout>/verdict.txt (MUST)."""
    p = os.path.join(RES, "amg", "summary8.txt")
    if not os.path.exists(p):
        return "_no results_\n"
    summary = open(p).read().strip()
    conf = os.path.join(RES, "amg", "confirm8.txt")
    c = open(conf).read().strip() if os.path.exists(conf) else ""
    must = []
    for layout in ("distributed", "central"):
        v = os.path.join(RES, "amg", f"must8-{layout}", "verdict.txt")
        if os.path.exists(v):
            must.append(f"| {layout} | `{' '.join(open(v).read().split())[:160]}` |")
    out = [f"AMG (LLNL), 8 ranks, `-P 2 2 2 -n 16 16 16 -problem 1`, traced with Open MPI (`experiments/amg/run.sh`).", "",
           "```", summary, "```", ""]
    if c:
        out += ["Replay of the first probe alternative with its pin (`confirm8.txt`):", "", "```", c, "```", ""]
    if must:
        out += ["MUST 1.9.2 on the same AMG build and arguments (`experiments/amg/must.sh`, 1800 s), by layout:", "",
                "| layout | verdict |", "|---|---|", *must, ""]
    return "\n".join(out)


def e7():
    f = os.path.join(RES, "e7.md")
    if not os.path.exists(f):
        return "_no results_\n"
    return ("The four MBI message-race programs whose outcome depends on a received value (the payload is the\n"
            "sender's rank and the program aborts unless the last message came from rank 3), analysed with a\n"
            "hand-written L2 twin that writes the check as a branch on the source of the last message\n"
            "(`experiments/mbi/twins.py`). L1 flags the race; the single-path explorer, which has no branch, cannot\n"
            "certify a consequence; the twin yields the abort.\n\n" + open(f).read() + "\n")


def e3():
    f = os.path.join(RES, "e3.md")
    if os.path.exists(f):
        return open(f).read() + "\n"
    try:
        out = subprocess.run([sys.executable, os.path.join(ROOT, "experiments", "e3_levels.py")], capture_output=True, text=True, timeout=600).stdout
    except Exception as ex:  # noqa: BLE001
        out = f"_failed: {ex}_"
    return out + "\n"


def nekbone():
    """Nekbone's communication suite (experiments/nekbone/run.sh): summary8.txt only."""
    p = os.path.join(RES, "nekbone", "summary8.txt")
    if not os.path.exists(p):
        return "_no results_\n"
    return "\n".join(["Nekbone (test/nek_comm, `PPLIST=NEKCOMM`), 8 ranks, MPICH build, the program whose ping-pong,",
                      "message-volume and tree-reduction tests reach the `MPI_ANY_SOURCE` wrappers of `comm_mpi.f`",
                      "(`experiments/nekbone/run.sh`, analysis with `--no-sp`, one process, run on the second machine",
                      "while another lane's jobs ran on the host, so the time is an upper bound). Every finding is a",
                      "1f at the binary-tree global operation `gp2` (`comm_mpi.f` line 994, `study/sites.csv`), which only",
                      "the suite's `gp2_test` calls (the solver's `gop` is an `MPI_Allreduce`): the inner nodes 0 and 4",
                      "receive their children's contributions with one tag and `MPI_ANY_SOURCE`, so the children can arrive",
                      "in either order; the ping-pong tests, whose tags make the sender unique, produce no finding. Rejected",
                      "alternatives are the candidate senders the checker refuses.", "",
                      "```", open(p).read().strip(), "```", ""])


def main():
    doc = ["# Results", "", "Generated by `experiments/tables.py` from the CSV files under `experiments/results/`; each table",
           "names the code commits its rows were produced with (the `commit` column). The corpus runs, their replays,",
           "E2, E6, the E3 and E7 inputs, the AMG trace and its replay sample come from `experiments/run_remaining.sh`",
           "on the second, shared machine (the container of `experiments/container/`, 12 cores of one NUMA node);",
           "the HYPRE campaign from `experiments/hypre/campaign.sh` there. The MUST verdicts are carried over from",
           "MUST 1.9.2 runs under MPICH in MPI-BugBench's own container, with the same timeout per program. The E4",
           "times and the AMG analysis time come from one announced timed leg at commit 404927c",
           "(`experiments/timing_leg.sh`); they are the only timing claims, and they predate the current tracer.",
           "", "## E1: predictions vs benchmark labels", "",
           e1("MPI Bugs Initiative (MBI): message-race, tag-matching, probe and buffering programs", os.path.join(RES, "mbi", "mbi.csv"), "MBI"),
           e1("MPI-BugBench level 2: point-to-point and collective call-ordering, missing-call, local-concurrency and correct programs", os.path.join(RES, "mbb", "mbb.csv"), "MBB"),
           stress("MBI", os.path.join(RES, "mbi", "mbi.csv"), "MBI"),
           stress("MPI-BugBench level 2", os.path.join(RES, "mbb", "mbb.csv"), "MBB"),
           "## E2: buffering ablation", "", e2(),
           "## E3: level ablation", "", e3(),
           "## E4: tracer overhead and analysis cost (NPB 3.4.3, MPICH build)", "", e4(),
           "## E5: real application (AMG)", "", e5() + amg_sample(),
           "## E5b: real application (Nekbone)", "", nekbone(),
           "## E5c: a real bug (HYPRE 2.10.1, ParaSails)", "", hypre(),
           "### How often one trace exposes it, and how often testing hits it", "", hypre_campaign(),
           "## E6: polynomial checker vs exhaustive explorer", "", e6(),
           "## E7: the value skeleton on MBI (level L2)", "", e7()]
    path = os.path.join(ROOT, "docs", "results.md")
    open(path, "w").write("\n".join(doc))
    print("wrote", path)
    write_paper_numbers()


if __name__ == "__main__":
    main()
