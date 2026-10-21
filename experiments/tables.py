#!/usr/bin/env python3
"""Compose docs/results.md from the experiment CSVs, the paper's numbers
(paper/generated/numbers.tex, one macro per number, and paper/generated/rq2.tex, the body of
the effectiveness table), and the README's results with their charts (experiments/figures.py).
Re-run after any experiment."""
from __future__ import annotations
import collections
import csv
import os
import re
import statistics
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RES = os.path.join(ROOT, "experiments", "results")
sys.path.insert(0, os.path.join(ROOT, "experiments", "mbi"))
import score  # noqa: E402  (the one definition of verdicts)
sys.path.insert(0, os.path.join(ROOT, "experiments"))
import figures  # noqa: E402  (the README's charts and results block)

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
           "A program's verdict is ERROR iff a finding of class 1a/1c/1d/2-buffering/2-collective/2-blocked/3-local/abort is",
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
    # the same corpus traced again with the buffer guard on (run_mbi.py --guard), which adds 3-local
    grs = score.compiled(rows(os.path.join(RES, "guard", os.path.basename(path))))
    if grs:
        cg = score.confusion(grs, score.verdict)
        out.append(conf_row("Stretto with the buffer guard (`STRETTO_GUARD=1`), a separate run", cg))
        lc = [r for r in grs if r.get("detail") == "LocalConcurrency"]
        found = [r for r in lc if "3-local" in r.get("classes", "").split()]
        stray = [r for r in grs if "3-local" in r.get("classes", "").split() and r.get("detail") != "LocalConcurrency"]
        out += ["", f"Buffer guard run: {len(grs)} programs (rows by code commit: {commits(grs)}); 3-local in {len(found)} of the "
                f"{len(lc)} local-concurrency programs and in {len(stray)} others.", ""]
        if key:
            CONF[(key, "guard")] = cg
            num(f"{key}foneguard", cg["f1"])
            num(f"n{key}fpguard", cg["fp"])
            num(f"n{key}fnguard", cg["fn"])
            num(f"n{key}LC", len(lc))
            num(f"n{key}LCguard", len(found))
    if key:
        num(f"n{key}progs", len(rs))
        num(f"n{key}correct", sum(1 for r in rs if r["expect"] == "OK"))
        for label, judge in (("union", score.verdict), ("observed", score.observed_verdict), ("predicted", score.predicted_verdict),
                             ("MUST", score.must_verdict), ("MUSTsym", score.must_verdict_hangs)):
            if label.startswith("MUST") and not any(r.get("must") for r in rs):
                continue
            c = CONF[(key, label)] = score.confusion(rs, judge)
            num(f"{key}fone{label}", c["f1"])
            num(f"n{key}tp{label}", c["tp"])
        num(f"n{key}fp", CONF[(key, "union")]["fp"])
        num(f"n{key}errors", sum(1 for r in rs if r["expect"] == "ERROR"))
        num(f"n{key}fnunion", CONF[(key, "union")]["fn"])
    if any(r.get("must") for r in rs):
        kinds = collections.Counter(score.must_kind(r) for r in rs)
        out.append(conf_row("MUST 1.9.2 (same source, its own run under MPICH)", score.confusion(rs, score.must_verdict)))
        out.append(conf_row("MUST 1.9.2, a timeout or a run without a report counted as an error", score.confusion(rs, score.must_verdict_hangs)))
        out += ["", "MUST verdicts: " + ", ".join(f"{k or 'none'} {v}" for k, v in sorted(kinds.items())) + ". In the first MUST row a run",
                "that timed out, crashed or did not compile counts as no error reported. A Stretto run that the harness",
                "killed at the timeout is reported as a blocked rank (2-blocked) and one whose rank stopped before MPI_Finalize",
                "as an abort, both errors, so the second MUST row credits MUST's timeouts and its runs that ended without a",
                "report (RE) the same way; the two rows bracket the comparison. Both tools ran each program once, with",
                "the same timeout; MUST under MPICH, Stretto under Open MPI, so they did not see the same run."]
        failed = [r["name"] for r in rs if score.analysis_failed(r)]
        if failed:
            out += ["", f"Analysis failed on {len(failed)} program(s), counted as no error: {', '.join(failed)}."]
        inv = [r for r in rs if r["expect"] == "ERROR" and score.verdict(r) == "ERROR" and score.must_verdict(r) != "ERROR"]
        inv_pred = [r for r in inv if score.predicted_verdict(r) == "ERROR" and score.observed_verdict(r) != "ERROR"]
        conf = [r for r in inv if r.get("confirmed") and int(r["confirmed"]) > 0]
        conf_pred = [r for r in inv_pred if r.get("confirmed") and int(r["confirmed"]) > 0]
        if key:
            num(f"n{key}CleanPred", len(inv_pred))
            num(f"n{key}CleanTemplates", len({"_".join(r["name"].split("_")[:2]) for r in inv_pred}))
            num(f"n{key}CleanConfirmed", len(conf_pred))
            num(f"n{key}OnlyStretto", len(inv))
            num(f"n{key}OnlyStrettoConfirmed", len(conf))
        out += ["", f"Labelled errors Stretto reports and MUST does not on the same program: **{len(inv)}**, of which",
                f"predicted on a run that showed nothing itself: **{len(inv_pred)}**. With at least one finding replay-confirmed",
                f"(a pinned rerun with every pin matched reaches the predicted outcome and its control finishes): **{len(conf)}** of the",
                f"{len(inv)}, **{len(conf_pred)}** of the {len(inv_pred)} predicted ones."]
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
            # which programs the inconclusive ones belong to: MBI's racing loops labelled
            # erroneous can hang without any change, and some recorded runs did
            inc = [r for r in judged if r["verdict"] == "inconclusive"]
            prog = {r["name"]: r for r in rs}
            num(f"n{key}ReplayInconclusiveLoop", sum(1 for r in inc if "_Loop_" in r["program"]
                                                     and prog.get(r["program"], {}).get("expect") == "ERROR"))
            num(f"n{key}ReplayInconclusiveHung", sum(1 for r in inc if prog.get(r["program"], {}).get("run", "").startswith("TIMEOUT")))
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
            if r["setting"] == "openmpi-forced-rendezvous" and path_ == "persistent":
                num("BpersOMPI", int(r["eager_max_bytes"]))          # persistent sends skip the inline path
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
    (experiments/hypre/campaign.sh: predict.csv and stress.csv; predict-2.11.0.csv, the fix)."""
    d = os.path.join(RES, "hypre")
    pr = rows(os.path.join(d, "predict.csv"))
    fixed = rows(os.path.join(d, "predict-2.11.0.csv"))
    st = rows(os.path.join(d, "stress.csv"))
    if not pr and not st:
        return "_no results_\n"
    out = []
    if pr:
        out += ["Traced runs of `ij -solver 8 -pout 0`; for each, every probe alternative of the request exchanges (tag 222;",
                "on 2.11.0, the fix, tags 221 and 222), replayed with the pins of its whole horizon and with its control",
                "(`experiments/hypre/predict.py`). An alternative is *cross* if the probe would take a request of the other",
                "exchange (the race), *same* if another neighbour's request of the same exchange; rows from before that",
                f"classification selected requests of a different size and count as cross. Rows by code commit: {commits(pr + fixed)}.", "",
                "| version | ranks | kind | traced runs | runs with one | runs with a hanging replay | alternatives | replay hangs | replay finishes | other replay results | required pins matched | controls finish | finished with the control's residual |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for version, vrows, prefix in (("2.10.1", pr, "Hypre"), ("2.11.0", fixed, "HypreFixed")):
            for r in vrows:
                r.setdefault("kind", "cross")
                r["kind"] = r["kind"] or "cross"
            for np_ in sorted({r["np"] for r in vrows}, key=int):
                sel = [r for r in vrows if r["np"] == np_]
                runs = {r["run"] for r in sel}
                bad = {r["run"] for r in sel if (r.get("replay") or "").startswith("traced run")}
                k = f"{prefix}{'Four' if np_ == '4' else 'Eight' if np_ == '8' else np_}"
                num(f"n{k}Runs", len(runs) - len(bad))
                for kind, macro in (("cross", ""), ("same", "Local")):
                    alts = [r for r in sel if r.get("pin") and r["kind"] == kind]
                    if kind == "same" and not alts:
                        continue
                    hang = [r for r in alts if r.get("replay") == "TIMEOUT"]
                    fin = [r for r in alts if r.get("replay") == "FINISHED"]
                    other = collections.Counter(r.get("replay") for r in alts if r.get("replay") not in ("TIMEOUT", "FINISHED"))
                    matched = [r for r in alts if r.get("matched") == "True"]
                    ctl = [r for r in alts if r.get("control") == "FINISHED"]
                    same_res = [r for r in fin if r.get("residual") and r.get("residual") == r.get("control_residual")]
                    res_known = "residual" in sel[0]
                    with_one = {r["run"] for r in alts}
                    hang_runs = {r["run"] for r in hang}
                    res_col = f"{len(same_res)}/{len(fin)}" if res_known and fin else "-" if res_known else "not recorded"
                    other_col = ", ".join(f"{n} {res}" for res, n in sorted(other.items())) or "0"
                    out.append(f"| {version} | {np_} | {kind} | {len(runs) - len(bad)} (+{len(bad)} failed) | {len(with_one)} | {len(hang_runs)} | "
                               f"{len(alts)} | {len(hang)} | {len(fin)} | {other_col} | {len(matched)}/{len(alts)} | {len(ctl)}/{len(alts)} | {res_col} |")
                    num(f"n{k}{macro}{'Exposed' if kind == 'cross' else 'Runs'}", len(with_one))
                    num(f"n{k}{macro}HangRuns", len(hang_runs))
                    num(f"n{k}{macro}Alts", len(alts))
                    num(f"n{k}{macro}Hang", len(hang))
                    num(f"n{k}{macro}Finish", len(fin))
                    num(f"n{k}{macro}Other", sum(other.values()))
                    num(f"n{k}{macro}Matched", len(matched))
                    num(f"n{k}{macro}Controls", len(ctl))
                    num(f"n{k}{macro}ControlsFailed", len(alts) - len(ctl))
                    if res_known and fin:
                        num(f"n{k}{macro}SameResidual", len(same_res))
            # over both process counts, for the abstract and the introduction
            ok = [r for r in vrows if not (r.get("replay") or "").startswith("traced run")]
            cross = [r for r in ok if r.get("pin") and r["kind"] == "cross"]
            num(f"n{prefix}Traced", len({(r["np"], r["run"]) for r in ok}))
            num(f"n{prefix}Exposed", len({(r["np"], r["run"]) for r in cross}))
            num(f"n{prefix}CrossAlts", len(cross))
            num(f"n{prefix}CrossHang", sum(1 for r in cross if r.get("replay") == "TIMEOUT"))
        sites = collections.Counter(r.get("site") for r in pr if r.get("pin") and r.get("site"))
        if sites:
            num("nHypreSites", len(sites))
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
                    "| ranks | probe | kind | alternatives | replay hangs | replay finishes | other |", "|---|---|---|---|---|---|---|"]
            for (np_, rank, idx, kind), c in sorted(by_probe.items()):
                out.append(f"| {np_} | {rank}:{idx} | {kind} | {sum(c.values())} | {c['TIMEOUT']} | {c['FINISHED']} | "
                           f"{sum(c.values()) - c['TIMEOUT'] - c['FINISHED']} |")
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


MG_REFERENCE = {"S": 0.5307707005734e-04, "A": 0.2433365309069e-05}   # NPB 3.3.1's verification values


def npbmg():
    """A second documented bug: NPB MG 2.4.1's wildcard receives can take a message of another
    exchange (experiments/npbmg/run.sh: predict-2.4.1.csv, predict-3.3.1.csv, stress.csv)."""
    d = os.path.join(RES, "npbmg")
    pr, fixed, st = (rows(os.path.join(d, f)) for f in ("predict-2.4.1.csv", "predict-3.3.1.csv", "stress.csv"))
    if not pr:
        return "_no results_\n"

    def rel(norm, cls):
        try:
            return abs(float(norm) - MG_REFERENCE[cls]) / MG_REFERENCE[cls]
        except (ValueError, KeyError):
            return None

    out = ["Traced runs of MG (class " + "/".join(sorted({r["class"] for r in pr})) + ", MPICH build); every alternative the",
           "analysis accepts is replayed with the pins of its horizon and with its control (`experiments/npbmg/predict.py`).",
           "A replay *changes the result* when its residual norm differs from its control's; MG 2.4.1's own check accepts",
           "an absolute error of 1e-8 against the reference, later versions a relative error of 1e-8, which these columns",
           f"apply. Rows by code commit: {commits(pr + fixed + st)}.", "",
           "| version | ranks | traced runs | runs with one | alternatives | replayed | pins matched | finished | result changed | fails the relative check | largest relative change | controls finished | controls with the traced run's norm |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for version, vrows, prefix in (("2.4.1", pr, "MG"), ("3.3.1", fixed, "MGFixed")):
        for np in sorted({int(r["np"]) for r in vrows}):
            rs = [r for r in vrows if int(r["np"]) == np]
            runs = {r["run"] for r in rs}
            exposed = {r["run"] for r in rs if r.get("alternatives") and int(r["alternatives"]) > 0}
            rep = [r for r in rs if r.get("replay") and r.get("pin")]
            changed = [r for r in rep if r.get("norm") and r["norm"] != r.get("control_norm")]
            fails = [r for r in rep if (rel(r.get("norm"), r["class"]) or 0) > 1e-8]
            big = max((rel(r.get("norm"), r["class"]) or 0 for r in changed), default=0)
            same_ctl = [r for r in rep if r.get("control_norm") and r["control_norm"] == r.get("traced_norm")]
            alts = sum(int(r["alternatives"]) for r in {r["run"]: r for r in rs if r.get("alternatives")}.values())
            out.append(f"| {version} | {np} | {len(runs)} | {len(exposed)} | {alts} | {len(rep)} | {sum(r.get('matched') == 'True' for r in rep)} "
                       f"| {sum(r.get('replay') == 'FINISHED' for r in rep)} | {len(changed)} | {len(fails)} | {big:.1e} "
                       f"| {sum(r.get('control') == 'FINISHED' for r in rep)} | {len(same_ctl)} |")
        rep = [r for r in vrows if r.get("replay") and r.get("pin")]
        num(f"n{prefix}Traced", len({(r["np"], r["run"]) for r in vrows}))
        num(f"n{prefix}Exposed", len({(r["np"], r["run"]) for r in vrows if r.get("alternatives") and int(r["alternatives"]) > 0}))
        num(f"n{prefix}Alts", sum(int(r["alternatives"]) for r in {(r["np"], r["run"]): r for r in vrows if r.get("alternatives")}.values()))
        if version == "2.4.1":
            num("nMGReplayed", len(rep))
            num("nMGMatched", sum(r.get("matched") == "True" for r in rep))
            num("nMGFinished", sum(r.get("replay") == "FINISHED" for r in rep))
            num("nMGChanged", sum(1 for r in rep if r.get("norm") and r["norm"] != r.get("control_norm")))
            num("nMGFailRel", sum(1 for r in rep if (rel(r.get("norm"), r["class"]) or 0) > 1e-8))
            num("nMGFailAbs", sum(1 for r in rep if (rel(r.get("norm"), r["class"]) or 0) * MG_REFERENCE[r["class"]] > 1e-8))
            num("nMGControls", sum(r.get("control") == "FINISHED" for r in rep))
            num("nMGControlsSame", sum(1 for r in rep if r.get("control_norm") and r["control_norm"] == r.get("traced_norm")))
            num("nMGRanks", "/".join(str(n) for n in sorted({int(r["np"]) for r in vrows})))
    if st:
        out += ["", "Untraced runs of MG 2.4.1, plain and with random send and receive delays from the replay library; a run",
                "*changes the result* when its residual norm differs from the plain runs' (every plain run printed the same).", "",
                "| ranks | setting | runs | finished | result changed | fails the relative check |", "|---|---|---|---|---|---|"]
        plain = {r["norm"] for r in st if r["setting"] == "plain" and r.get("norm")}
        base = plain.pop() if len(plain) == 1 else None
        for np in sorted({int(r["np"]) for r in st}):
            for setting in ("plain", "delays"):
                rs = [r for r in st if int(r["np"]) == np and r["setting"] == setting]
                ch = [r for r in rs if base and r.get("norm") and r["norm"] != base]
                fl = [r for r in rs if (rel(r.get("norm"), r["class"]) or 0) > 1e-8]
                out.append(f"| {np} | {setting} | {len(rs)} | {sum(r['result'] == 'FINISHED' for r in rs)} | {len(ch)} | {len(fl)} |")
        num("nMGStressRuns", len(st))
        num("nMGStressChanged", sum(1 for r in st if base and r.get("norm") and r["norm"] != base))
    return "\n".join(out) + "\n"


def app_sample(app="amg", prefix="AMG"):
    """A sample of an application's alternatives replayed with horizon pins and controls
    (replay_sample.py, experiments/<app>/run.sh sample)."""
    rs = rows(os.path.join(RES, app, "replay_sample.csv"))
    if not rs:
        return ""
    c = collections.Counter((r.get("replay"), r.get("control")) for r in rs)
    matched = sum(1 for r in rs if r.get("matched") == "True")
    num(f"n{prefix}sample", len(rs))
    num(f"n{prefix}sampleFinish", sum(1 for r in rs if r.get("replay") == "FINISHED"))
    num(f"n{prefix}sampleMatched", matched)
    num(f"n{prefix}sampleControls", sum(1 for r in rs if r.get("control") == "FINISHED"))
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
              ("predicted", "\\tool, predicted"), ("guard", "\\tool with the buffer guard"), ("MUST", "\\MUST, a timeout not counted"), ("MUSTsym", "\\MUST, a timeout or failed run counted as an error"),
              ("stress", "random delays, up to \\n{key}StressRuns runs"))
    for key, title in (("MBI", "MBI"), ("MBB", "\\MBB level~2")):
        if not any((key, l) in CONF for l, _ in labels):
            continue
        body.append(f"\\multicolumn{{6}}{{@{{}}l}}{{\\emph{{{title}, \\n{key}progs programs}}}} \\\\")
        for l, text in labels:
            c = CONF.get((key, l))
            if l == "guard" and c == CONF.get((key, "union")):    # the guard changed no verdict: no row
                continue
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


# fallbacks, printed as provisional, for numbers whose source is missing or predates its macro
STATIC = {"TraceOverhead": "0.99--1.07", "nAMGalts": "3\\,263", "AMGtime": "13.2", "nNekAlts": "10\\,000", "nNekRejected": "8\\,759",
          "NekRecords": "10.4", "NekMinutes": "63", "NekGB": "33"}


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
    """E4 from the timed leg: per row the median over its repetitions, with the range of the
    slowdown; NPB's own timer where the log has one."""
    rs = rows(os.path.join(RES, "e4", "e4.csv"))
    if not rs:
        return "_no results_\n"
    groups = collections.OrderedDict()
    for r in rs:
        groups.setdefault((r["benchmark"], r["class"], r["np"]), []).append(r)
    reps = max(len(g) for g in groups.values())

    def med(g, key):
        v = sorted(float(r[key]) for r in g if r.get(key))
        return statistics.median(v) if v else None

    def rng(g, key):
        v = sorted(float(r[key]) for r in g if r.get(key))
        return f"{v[0]:.2f}-{v[-1]:.2f}" if len(v) > 1 else (f"{v[0]:.2f}" if v else "-")

    out = ["Plain, traced and analysis times from the timed leg (`experiments/timing_leg.sh`, log in",
           "`experiments/results/timing-leg.log`) on the second machine, announced to the other lanes, the",
           f"analysis one process; {reps} repetition(s) of the whole set, each row the median over them. The",
           "wall times include the launcher's start-up; NPB's own timer covers the benchmark's timed section.",
           f"Rows by code commit: {commits(rs)}.", "",
           "| benchmark | class | np | plain (s) | traced (s) | slowdown | NPB plain (s) | NPB traced (s) | NPB slowdown (range) | trace (MB) | events | wildcards | analysis (s) | findings |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    npb_sd, wall_sd = [], []
    for (b, c, n), g in groups.items():
        pl, tr, an = med(g, "plain_s"), med(g, "traced_s"), med(g, "analysis_s")
        pn, tn = med(g, "plain_npb_s"), med(g, "traced_npb_s")
        sd = tr / pl if pl else None
        nsd = med(g, "npb_slowdown")
        wall_sd.append(sd)
        if nsd is not None and pn is not None and pn >= 1.0:
            npb_sd.append(nsd)
        f = g[0]
        cell = lambda x, fmt: (fmt.format(x) if x is not None else "-")
        out.append(f"| {b} | {c} | {n} | {cell(pl, '{:.2f}')} | {cell(tr, '{:.2f}')} | {cell(sd, '{:.2f}')} | {cell(pn, '{:.2f}')} | "
                   f"{cell(tn, '{:.2f}')} | {cell(nsd, '{:.3f}')} ({rng(g, 'npb_slowdown')}) | {f['trace_MB']} | {f['events']} | {f['wildcards']} | "
                   f"{cell(an, '{:.1f}')} | {f['findings']} {f['classes']} |")
    if "rep" in rs[0]:
        num("nEfourReps", reps)
        if npb_sd:
            num("TraceOverhead", f"{min(npb_sd):.2f}--{max(npb_sd):.2f}")
        wall = [x for x in wall_sd if x is not None]
        if wall:
            num("TraceOverheadWall", f"{min(wall):.2f}--{max(wall):.2f}")
        lu = groups.get(("lu", "A", "16"))
        if lu:
            num("LuAnalysis", f"{med(lu, 'analysis_s'):.0f}")
            num("LuEvents", f"{int(lu[0]['events']) / 1e6:.2f}")
        out += ["", "NPB slowdown range in the text: rows whose plain timed section lasts at least 1 s."]
    return "\n".join(out) + "\n"


def e5():
    """E5 reads only committed files: summary8.txt (trace header, findings by class, analysis time
    and memory), confirm8.txt (the pinned replay) and must8-<layout>/verdict.txt (MUST)."""
    p = os.path.join(RES, "amg", "summary8.txt")
    if not os.path.exists(p):
        return "_no results_\n"
    summary = open(p).read().strip()
    m = re.search(r"(\d+) sends, (\d+) receives, (\d+) wildcard receives, (\d+) probes", summary)
    if m:
        num("nAMGsends", int(m.group(1)))
        num("nAMGwild", int(m.group(3)))
        num("nAMGprobes", int(m.group(4)))
    line = next((ln for ln in summary.splitlines() if ln.startswith("findings by class:")), "")
    found = {k: int(v) for k, v in re.findall(r"(\S+) (\d+)", line.split(":", 1)[-1])}
    if found:
        num("nAMGalts", found.get("probe-alt", 0))
        num("nAMGrecvAlts", sum(v for k, v in found.items() if k != "probe-alt"))
    # a timing claim only when the timed leg wrote the summary (timing_leg.sh marks it)
    m = re.search(r"analysis ([0-9.]+) s, (\d+) KB", summary)
    if m and "timed leg:" in summary:
        num("AMGtime", f"{float(m.group(1)):.1f}")
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


LEVEL_KEY = {"L1": "one", "L1+SP": "sp", "observed": "obs"}
CLASS_KEY = {"1a": "A", "1b": "B", "1c": "C", "1d": "D", "1e": "E", "1f": "F", "2-blocked": "Blocked",
             "2-buffering": "Buffering", "2-collective": "Collective"}


def e3():
    f = os.path.join(RES, "e3.md")
    if os.path.exists(f):
        text = open(f).read()
    else:
        try:
            text = subprocess.run([sys.executable, os.path.join(ROOT, "experiments", "e3_levels.py")], capture_output=True, text=True, timeout=600).stdout
        except Exception as ex:  # noqa: BLE001
            text = f"_failed: {ex}_"
    # findings and programs per corpus, level and class, e.g. \nMBIspDfind and \nMBIspDprogs
    for corpus, level, cls, n, progs in re.findall(r"^\| (mbi|mbb) \| (\S+) \| (\S+) \| (\d+) \| (\d+) \|$", text, re.M):
        if level in LEVEL_KEY and cls in CLASS_KEY:
            k = f"n{corpus.upper()}{LEVEL_KEY[level]}{CLASS_KEY[cls]}"
            num(k + "find", int(n))
            num(k + "progs", int(progs))
    return text + "\n"


def nekbone():
    """Nekbone's communication suite (experiments/nekbone/run.sh): summary8.txt only."""
    p = os.path.join(RES, "nekbone", "summary8.txt")
    if not os.path.exists(p):
        return "_no results_\n"
    summary = open(p).read()
    line = next((ln for ln in summary.splitlines() if ln.startswith("findings by class:")), "")
    found = {k: int(v) for k, v in re.findall(r"(\S+) (\d+)", line.split(":", 1)[-1])}
    if found:
        num("nNekAlts", sum(found.values()))
    m = re.search(r"rejected alternatives: (\d+)", summary)
    rejected = int(m.group(1)) if m else 0
    if m:
        num("nNekRejected", rejected)
    m = re.search(r"records: (\d+)", summary)
    if m:
        num("NekRecords", f"{int(m.group(1)) / 1e6:.1f}")
    m = re.search(r"analysis ([0-9.]+) s, (\d+) KB", summary)
    if m:
        num("NekMinutes", f"{float(m.group(1)) / 60:.0f}")
        num("NekGB", f"{int(m.group(2)) * 1024 / 1e9:.0f}")   # /usr/bin/time's %M is in KiB
        judged = sum(found.values()) + rejected
        if judged:
            # every alternative judged, accepted or rejected, is one check over the whole graph
            num("NekPerAlt", f"{float(m.group(1)) / judged:.2f}")
    return "\n".join(["Nekbone (test/nek_comm, `PPLIST=NEKCOMM`), 8 ranks, MPICH build, the program whose ping-pong,",
                      "message-volume and tree-reduction tests reach the `MPI_ANY_SOURCE` wrappers of `comm_mpi.f`",
                      "(`experiments/nekbone/run.sh`, analysis with `--no-sp`, one process, outside a timed leg, so",
                      "the time is indicative). Every finding is a",
                      "1f at the binary-tree global operation `gp2` (`comm_mpi.f` line 994, `study/sites.csv`), which only",
                      "the suite's `gp2_test` calls (the solver's `gop` is an `MPI_Allreduce`): the inner nodes 0 and 4",
                      "receive their children's contributions with one tag and `MPI_ANY_SOURCE`, so the children can arrive",
                      "in either order; the ping-pong tests, whose tags make the sender unique, produce no finding. Rejected",
                      "alternatives are the candidate senders the checker refuses, counted by the reason it gives.", "",
                      "```", open(p).read().strip(), "```", ""])


def guard():
    """The buffer guard's own checks (experiments/guard/run.sh): its cases, MPI-BugBench's
    local-concurrency programs under both MPI implementations, and applications with the guard
    off and on. The corpus rows with the guard are in E1."""
    d = os.path.join(RES, "guard")
    if not os.path.isdir(d):
        return "_no results_\n"
    def read(f):
        p = os.path.join(d, f)
        return open(p).read() if os.path.exists(p) else ""
    out = []
    lc = re.findall(r"^(openmpi|mpich): 3-local in (\d+) of (\d+) programs built$", read("lc.txt"), re.M)
    if lc:
        out += ["MPI-BugBench local-concurrency programs traced with the guard (`lc.txt`): "
                + "; ".join(f"{m}: 3-local in {a} of {b} programs built" for m, a, b in lc) + "."]
        for m, a, b in lc:
            tag = {"openmpi": "OMPI", "mpich": "MPICH"}[m]
            num(f"nGuardLC{tag}found", int(a))
            num(f"nGuardLC{tag}built", int(b))
    sc = re.findall(r"^(openmpi|mpich): (\d+) of (\d+) cases as expected$", read("scenarios.txt"), re.M)
    if sc:
        out += ["Two-rank cases that each name the guard record they must give (`scenarios.txt`): "
                + "; ".join(f"{m}: {a} of {b} as expected" for m, a, b in sc) + "."]
        num("nGuardCases", int(sc[0][2]))
    runs = re.findall(r"^(\S+)\s+(\S+)\s+guard=(\d) rep=\d+ rc=(\d+)\s+wall=\s*[\d.]+s npb=(\S+)\s+records=\d+\s+guard=\[([^]]*)\]",
                      read("apps.txt") + read("apps-cma.txt"), re.M)
    if runs:
        bad = [r for r in runs if r[3] != "0" or r[5]]
        lu = {g: sorted(float(r[4]) for r in runs if r[0] == "lu.A" and r[2] == g) for g in ("0", "1")}
        out += [f"Applications at 4 ranks (`apps.txt`, `apps-cma.txt`): {len(runs)} runs of {', '.join(sorted({r[0] for r in runs}))}; "
                f"{len(bad)} ended with a nonzero status or a guard record."]
        num("nGuardAppRuns", len(runs))
        num("nGuardAppBad", len(bad))
        if lu["0"] and lu["1"]:
            out += [f"lu.A by NPB's own timer: {lu['0'][0]:.2f} to {lu['0'][-1]:.2f} s with the guard off, "
                    f"{lu['1'][0]:.2f} to {lu['1'][-1]:.2f} s on (shared processors, not a timed leg)."]
            num("GuardLuOff", f"{lu['0'][0]:.2f}--{lu['0'][-1]:.2f}")
            num("GuardLuOn", f"{lu['1'][0]:.2f}--{lu['1'][-1]:.2f}")
    return "\n\n".join(out) + "\n"


def leg_commits():
    cs = sorted({r["commit"] for r in rows(os.path.join(RES, "e4", "e4.csv")) if r.get("commit")})
    return ", ".join(cs) or "unknown"


def main():
    doc = ["# Results", "", "Generated by `experiments/tables.py` from the CSV files under `experiments/results/`; each table",
           "names the code commits its rows were produced with (the `commit` column). The corpus runs, their replays,",
           "E2, E6 and the E3 and E7 inputs come from `experiments/run_remaining.sh` on the second, shared machine",
           "(the container of `experiments/container/`, 12 cores of one NUMA node); the HYPRE campaign from",
           "`experiments/hypre/campaign.sh` and Nekbone from `experiments/nekbone/run.sh` there. The MUST verdicts",
           "are carried over from MUST 1.9.2 runs under MPICH in MPI-BugBench's own container, with the same timeout",
           "per program. The E4 times, the AMG trace and its analysis time come from one announced timed leg at commit",
           f"{leg_commits()} (`experiments/timing_leg.sh`, the host otherwise idle), and AMG's replay sample from that",
           "trace; the leg's times are the only timing claims.",
           "", "## E1: predictions vs benchmark labels", "",
           e1("MPI Bugs Initiative (MBI): message-race, tag-matching, probe and buffering programs", os.path.join(RES, "mbi", "mbi.csv"), "MBI"),
           e1("MPI-BugBench level 2: point-to-point and collective call-ordering, missing-call, local-concurrency and correct programs", os.path.join(RES, "mbb", "mbb.csv"), "MBB"),
           stress("MBI", os.path.join(RES, "mbi", "mbi.csv"), "MBI"),
           stress("MPI-BugBench level 2", os.path.join(RES, "mbb", "mbb.csv"), "MBB"),
           "## E2: buffering ablation", "", e2(),
           "## E3: level ablation", "", e3(),
           "## E4: tracer overhead and analysis cost (NPB 3.4.3, MPICH build)", "", e4(),
           "## E5: real application (AMG)", "", e5() + app_sample("amg", "AMG"),
           "## E5b: real application (Nekbone)", "", nekbone() + app_sample("nekbone", "Nek"),
           "## E5c: a real bug (HYPRE 2.10.1, ParaSails)", "", hypre(),
           "### How often one trace exposes it, and how often testing hits it", "", hypre_campaign(),
           "## E5d: a second documented bug (NPB MG 2.4.1, a message of another exchange)", "", npbmg(),
           "## E6: polynomial checker vs exhaustive explorer", "", e6(),
           "## E7: the value skeleton on MBI (level L2)", "", e7(),
           "## E8: the buffer guard (local concurrency, class 3-local)", "", guard()]
    path = os.path.join(ROOT, "docs", "results.md")
    open(path, "w").write("\n".join(doc))
    print("wrote", path)
    write_paper_numbers()
    figures.readme(ROOT, NUM)


if __name__ == "__main__":
    main()
