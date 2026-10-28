#!/usr/bin/env python3
"""Compose docs/results.md from the experiment CSVs, the paper's numbers
(experiments/generated/numbers.tex, one macro per number, and experiments/generated/rq2.tex, the body of
the effectiveness table), and the README's results with their charts (experiments/figures.py).
Re-run after any experiment.

The statistics of the last section of docs/results.md ("Statistics") have macros of their own:
  <count>CI, <count>CILo, <count>CIHi   the Wilson 95% interval of the proportion whose count macro
                                        is <count> ('[0.47, 0.76]', and each bound alone); a bound
                                        that is exactly 0 or 1 is written so, any other with the
                                        decimals (at least two) that give its distance to 0 or 1
                                        two significant digits ('[0, 0.046]', '[0.0044, 0.13]')
  n<campaign>...DistinctPins            the distinct pinned alternatives behind a campaign's replays
  nPin...All, nPin...<campaign>         the pin log over every application campaign, and by campaign
  n<corpus>Union...                     the labelled errors each tool and each union of baselines finds
  n<corpus>Sign<baseline>Stretto/Other  the labelled errors Stretto finds and a baseline misses, and the
  <corpus>Sign<baseline>P               reverse, with the exact two-sided sign test's p-value
  nEfourLong, nEfourLongNames           the E4 configurations the overhead range is taken over
The repeated stress passes on MBI (section E1, "Repeated stress passes") have theirs:
  nRepsStress{Always,Sometimes,Never}   the labelled errors every pass, some passes and no pass makes fail;
  nRepsStressStretto{...}               the same of Stretto's traced runs, the corpus run and the reps
  nRepsStressPass...                    each pass against Stretto's corpus run: the range over the passes
                                        of the split and its p-value, and nRepsStressPassSig, the passes
                                        against which Stretto's lead is significant at 5%
  nRepsStressPair..., RepsStressPair... the same over every pair of a traced run and a pass
  nRepsStressFPProgs, ...FPPasses       the correct programs some pass makes fail, and the passes that do
  nRepsStressAsSingle                   the passes that make exactly the single pass's labelled errors fail
  nRepsStressRateStretto/Other,         the labelled errors whose detection rate is higher with Stretto's
  RepsStressMeanStretto/Other,          traced runs and with the passes, the labelled errors one traced run
  RepsStressRateP                       and one pass find on average, and the paired permutation test's p"""
from __future__ import annotations
import collections
import csv
import glob
import json
import math
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
NUM: dict = {}       # macro name -> text, written to experiments/generated/numbers.tex
CONF: dict = {}      # (corpus, row label) -> confusion counts, for experiments/generated/rq2.tex
EFOUR: list = []     # E4's configurations with their NPB timer medians, for the statistics section


def num(name: str, value) -> None:
    NUM[name] = value if isinstance(value, str) else (f"{value:.2f}" if isinstance(value, float) else f"{value:,}".replace(",", "\\,"))


Z95 = 1.959963984540054      # the standard normal distribution's 0.975 quantile: two-sided 95%


def wilson(k: int, n: int, z: float = Z95) -> tuple:
    """Wilson's score interval of the proportion k of n: the p whose score test accepts k/n,
    |k/n - p| <= z * sqrt(p * (1 - p) / n). It stays inside [0, 1], and at k = 0 or k = n,
    where the normal approximation's is empty, its bound there is exactly 0 or 1."""
    if n <= 0 or not 0 <= k <= n:
        raise ValueError(f"no proportion of {k} in {n}")
    z2 = z * z
    centre = (k + z2 / 2) / (n + z2)
    half = z * math.sqrt(k * (n - k) / n + z2 / 4) / (n + z2)
    return (0.0 if k == 0 else max(0.0, centre - half), 1.0 if k == n else min(1.0, centre + half))


def sign_test(b: int, c: int) -> float:
    """The exact two-sided sign test of a paired comparison with b discordant pairs one way and
    c the other (McNemar's exact test; the pairs alike carry no information): the probability
    of a split at least as uneven as b to c if each pair went either way with probability 1/2."""
    if b < 0 or c < 0:
        raise ValueError(f"no counts {b} and {c}")
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def paired_permutation(d: list) -> float:
    """The exact two-sided paired permutation test of integer differences d (a sign-flip test):
    the probability, with each difference's sign flipped at random, of a sum at least as far from
    0 as sum(d). Exact over every sign vector, by counting the sums one difference at a time. With
    differences of -1, 0 and 1 it is sign_test of the 1s against the -1s."""
    a = [abs(x) for x in d if x]
    obs = abs(sum(d))
    if not a or obs == 0:
        return 1.0
    ways = {0: 1}
    for x in a:
        nxt = collections.Counter()
        for t, w in ways.items():
            nxt[t + x] += w
            nxt[t - x] += w
        ways = nxt
    return min(1.0, sum(w for t, w in ways.items() if abs(t) >= obs) / 2 ** len(a))


def p_text(p: float) -> str:
    """A p-value with two significant digits, in positional notation down to 1e-6 (0.077,
    0.000061) and in scientific notation below (9.1e-13)."""
    if p >= 1:
        return "1"
    if p >= 1e-6:
        return f"{p:.{max(2, 1 - math.floor(math.log10(p)))}f}"
    mantissa, exponent = f"{p:.1e}".split("e")
    return f"{mantissa}e{int(exponent)}"


def bound_text(x: float) -> str:
    """A bound of an interval of proportions as text: an exact 0 or 1 as such, any other with as
    many decimals, at least two, as give its distance to 0 or 1, whichever is nearer, two
    significant digits (0.47, 0.046, 0.99929)."""
    if x in (0, 1):
        return str(int(x))
    return f"{x:.{max(2, 1 - math.floor(math.log10(min(x, 1 - x))))}f}"


def bounds_text(lo: float, hi: float) -> tuple:
    return bound_text(lo), bound_text(hi)


def ci(name: str, k: int, n: int) -> str:
    """The macros of the Wilson 95% interval of k of n, named after the count's macro name:
    name + 'CI' ('[0.47, 0.76]'), name + 'CILo' and name + 'CIHi'. Returns the interval as text."""
    lo, hi = bounds_text(*wilson(k, n))
    num(name + "CILo", lo)
    num(name + "CIHi", hi)
    num(name + "CI", f"[{lo}, {hi}]")
    return f"[{lo}, {hi}]"


def pin_id(pin: str) -> tuple:
    """A pinned alternative as the campaign tables record it: 'STRETTO_PIN_PROBE=2:3:4' is
    ('probe', 2, 3, 4), the probe of rank 2 with ordinal 3 among that rank's probes, pinned to
    source 4; STRETTO_PIN=r:i:s pins the receive of rank r with ordinal i the same way."""
    var, _, spec = pin.partition("=")
    rank, ordinal, source = (int(x) for x in spec.split(":"))
    return ("probe" if var == "STRETTO_PIN_PROBE" else "recv", rank, ordinal, source)


def distinct_pins(rs, *scope) -> int:
    """The distinct alternatives the rows rs pin: rows with the same pin_id and the same values
    of the scope columns (the program, its version, the process count) pin the same one."""
    return len({(*(r.get(c) for c in scope), pin_id(r["pin"])) for r in rs if r.get("pin")})


def commits(rs) -> str:
    """The code commits a table's rows were produced with, most rows first; a row analysed again
    by other code than the run's names that code too (its analysis_commit)."""
    def key(r):
        c, a = r.get("commit") or "unrecorded", r.get("analysis_commit")
        return f"{c}, analysed at {a}" if a and a != c else c
    c = collections.Counter(key(r) for r in rs)
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
           "A program's verdict is ERROR iff a finding of class 1a/1c/1d/2-buffering/2-collective/2-blocked/2-ready/3-local/abort is",
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
        num(f"n{key}notBuilt", len(ce))
        num(f"n{key}correct", sum(1 for r in rs if r["expect"] == "OK"))
        num(f"n{key}correctWarned", score.noise(r for r in rs if r["expect"] == "OK")["with_warnings"])
        # the true positives the run did not show, by what predicts them: a consequence of an
        # alternative (1a, or 1c/1d under the single-path assumption) or a buffering threshold alone
        pred_only = [r for r in rs if r["expect"] == "ERROR" and score.predicted_verdict(r) == "ERROR"
                     and score.observed_verdict(r) != "ERROR"]
        num(f"n{key}tpPredOnly", len(pred_only))
        num(f"n{key}tpPredSP", sum(1 for r in pred_only if score.certified(r)))
        num(f"n{key}tpPredBuf", sum(1 for r in pred_only if not score.certified(r)))
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
        out += must_versions(rs, key)
        out += ["", "MUST verdicts: " + ", ".join(f"{k or 'none'} {v}" for k, v in sorted(kinds.items())) + ". In the first MUST row a run",
                "that timed out, crashed or did not compile counts as no error reported. A Stretto run that the harness",
                "killed at the timeout is reported as a blocked rank (2-blocked) and one whose rank stopped before MPI_Finalize",
                "as an abort, both errors, so the second MUST row credits MUST's timeouts and its runs that ended without a",
                "report (RE) the same way; the two rows bracket the comparison. Both tools ran each program once, with",
                "the same timeout; MUST under MPICH, Stretto under Open MPI, so they did not see the same run."]
        out += must_versions_notes(rs, key, path)
        failed = [r["name"] for r in rs if score.crashed(r)]
        if failed:
            out += ["", f"Analysis failed on {len(failed)} program(s) (CRASH), left out of every row above: {', '.join(failed)}."]
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


def reps():
    """MBI run again and again (experiments/run_remaining.sh reps): a racing program's run shows
    one schedule, so one run's verdicts are samples; here every program has as many."""
    d = os.path.join(RES, "reps")
    paths = sorted((p for p in (os.listdir(d) if os.path.isdir(d) else []) if re.fullmatch(r"mbi-\d+\.csv", p)),
                   key=lambda p: int(p[4:-4]))
    runs = [{r["name"]: r for r in score.compiled(rows(os.path.join(d, p)))} for p in paths]
    if not runs:
        return "_no results_\n"
    n = len(runs)
    names = sorted(set.intersection(*(set(b) for b in runs)))
    err = [p for p in names if runs[0][p]["expect"] == "ERROR"]
    ok = [p for p in names if runs[0][p]["expect"] == "OK"]
    judges = (("Stretto (union)", "Union", score.verdict), ("Stretto, observed in the run", "Obs", score.observed_verdict),
              ("Stretto, predicted", "Pred", score.predicted_verdict))
    out = [f"MBI's programs of E1 run {n} times each with the same harness (`experiments/run_remaining.sh reps`) and",
           "analysed with the current analysis; MUST and replays are not rerun. One run per program is one schedule:",
           f"these runs say how often each verdict holds. Programs in every run: {len(names)}, {len(err)} labelled errors.",
           f"Rows by code commit: {commits([r for b in runs for r in b.values()])}.", "",
           "| verdict | F1 min | F1 median | F1 max | TP | FP | FN |", "|---|---|---|---|---|---|---|"]
    num("nRepsRuns", n)
    num("nRepsProgs", len(names))
    num("nRepsErrors", len(err))
    for label, key, judge in judges:
        cs = [score.confusion([b[p] for p in names], judge) for b in runs]
        f1 = sorted(c["f1"] for c in cs)
        rng = lambda k: f"{min(c[k] for c in cs)}–{max(c[k] for c in cs)}"
        out.append(f"| {label} | {f1[0]:.2f} | {statistics.median(f1):.2f} | {f1[-1]:.2f} | {rng('tp')} | {rng('fp')} | {rng('fn')} |")
        num(f"Reps{key}FoneMin", f1[0])
        num(f"Reps{key}FoneMax", f1[-1])
        num(f"nReps{key}FPmax", max(c["fp"] for c in cs))
    # per labelled error: in how many runs the run itself failed, and in how many Stretto reported it
    k_obs = {p: sum(score.observed_verdict(b[p]) == "ERROR" for b in runs) for p in err}
    k_uni = {p: sum(score.verdict(b[p]) == "ERROR" for b in runs) for p in err}
    clean = [(p, b) for p in err for b in runs if score.observed_verdict(b[p]) != "ERROR"]
    clean_pred = [(p, b) for p, b in clean if score.predicted_verdict(b[p]) == "ERROR"]
    per_run = [sum(1 for p in err if score.observed_verdict(b[p]) != "ERROR" and score.predicted_verdict(b[p]) == "ERROR") for b in runs]
    vary = [p for p in err if 0 < k_obs[p] < n or 0 < k_uni[p] < n]
    num("nRepsUnionAlways", sum(1 for p in err if k_uni[p] == n))
    num("nRepsUnionNever", sum(1 for p in err if k_uni[p] == 0))
    num("nRepsObsAlways", sum(1 for p in err if k_obs[p] == n))
    num("nRepsObsNever", sum(1 for p in err if k_obs[p] == 0))
    num("nRepsObsSometimes", sum(1 for p in err if 0 < k_obs[p] < n))
    num("nRepsCleanRuns", len(clean))
    num("nRepsCleanPredicted", len(clean_pred))
    missed = [(p, b) for p, b in clean if score.predicted_verdict(b[p]) != "ERROR"]
    num("nRepsCleanMissed", len(missed))
    num("nRepsCleanMissedProgs", len({p for p, _ in missed}))
    # the value-check programs of E7, which only the value level decides
    e7 = os.path.join(RES, "e7.md")
    l2 = set(re.findall(r"^\| (\w+) \|", open(e7).read(), re.M)) if os.path.exists(e7) else set()
    num("nRepsCleanMissedNotLtwo", len({p for p, _ in missed} - l2))
    num("nRepsCleanPredMin", min(per_run))
    num("nRepsCleanPredMax", max(per_run))
    num("nRepsUnionAboveObs", sum(1 for p in err if k_uni[p] > k_obs[p]))
    num("nRepsUnionBelowObs", sum(1 for p in err if k_uni[p] < k_obs[p]))
    fp_ever = [p for p in ok if any(score.verdict(b[p]) == "ERROR" for b in runs)]
    num("nRepsFPever", len(fp_ever))
    out += ["", f"Labelled errors by the number of runs, out of {n}, in which the run itself failed (observed) and in which",
            "Stretto reported the error (union):", "",
            "| | in every run | in some runs | in no run |", "|---|---|---|---|",
            f"| the run failed (observed) | {sum(1 for p in err if k_obs[p] == n)} | {sum(1 for p in err if 0 < k_obs[p] < n)} | {sum(1 for p in err if k_obs[p] == 0)} |",
            f"| Stretto reported it | {sum(1 for p in err if k_uni[p] == n)} | {sum(1 for p in err if 0 < k_uni[p] < n)} | {sum(1 for p in err if k_uni[p] == 0)} |",
            "", f"Runs of a labelled-error program that showed nothing themselves: {len(clean)}; Stretto predicted the error from",
            f"{len(clean_pred)} of them ({min(per_run)} to {max(per_run)} programs per round of runs); the other {len(missed)} are runs of "
            + ", ".join(sorted({p for p, _ in missed})) + ". Correct programs reported as an",
            f"error in some run: {len(fp_ever)}" + (f" ({', '.join(fp_ever)})" if fp_ever else "") + ".",
            "", "Labelled errors whose outcome depends on the run (the run failed or Stretto reported the error in some runs",
            "and not in others); *predicted from clean runs* counts the runs that finished without showing the error in which",
            "Stretto predicted it:", "",
            "| program | the run failed | Stretto reported it | predicted from clean runs |", "|---|---|---|---|"]
    for p in vary:
        c = [b for b in runs if score.observed_verdict(b[p]) != "ERROR"]
        out.append(f"| {p} | {k_obs[p]}/{n} | {k_uni[p]}/{n} | "
                   f"{sum(score.predicted_verdict(b[p]) == 'ERROR' for b in c)}/{len(c)} |")
    return "\n".join(out) + "\n"


def stress_reps():
    """The stress baseline on MBI passed again and again (experiments/run_remaining.sh
    stressreps: reps/stress-mbi-<k>.csv) against Stretto's traced runs, the corpus run and the
    repetitions of reps(). Scored as the single comparison of _baselines(): a labelled error is
    found by a stress pass when one of its runs did not finish, and by a traced run when
    Stretto's verdict (observed or predicted) is ERROR. Empty without the passes."""
    d = os.path.join(RES, "reps")
    files = os.listdir(d) if os.path.isdir(d) else []
    passes = sorted((p for p in files if re.fullmatch(r"stress-mbi-\d+\.csv", p)), key=lambda p: int(p[11:-4]))
    main_path = os.path.join(RES, "mbi", "mbi.csv")
    if not passes or not os.path.exists(main_path):
        return ""
    st = [{r["program"]: r for r in rows(os.path.join(d, p))} for p in passes]
    reps_ = sorted((p for p in files if re.fullmatch(r"mbi-\d+\.csv", p)), key=lambda p: int(p[4:-4]))
    traced = [{r["name"]: r for r in score.compiled(rows(main_path))}] + \
             [{r["name"]: r for r in score.compiled(rows(os.path.join(d, p)))} for p in reps_]
    # the labelled errors every pass ran and every traced run built
    err = sorted(p for p in set.intersection(*(set(s) for s in st), *(set(b) for b in traced))
                 if st[0][p]["expect"] == "ERROR")
    ok = sorted(p for p in set.intersection(*(set(s) for s in st)) if st[0][p]["expect"] == "OK")
    m, n = len(st), len(traced)
    k_st = {p: sum(bool(s[p]["first_failure"]) for s in st) for p in err}
    k_tr = {p: sum(score.verdict(b[p]) == "ERROR" for b in traced) for p in err}
    S = {p for p in err if score.verdict(traced[0][p]) == "ERROR"}     # the corpus run, as _baselines() uses
    seeds = sorted({r.get("seed") or "none" for s in st for r in s.values()})
    seed_text = f"{seeds[0]} to {seeds[-1]}" if all(x.isdigit() for x in seeds) and \
        [int(x) for x in seeds] == list(range(int(seeds[0]), int(seeds[-1]) + 1)) else ", ".join(seeds)
    # the single pass (stress.csv), for how far one pass stands for the others
    single = {r["program"]: r for r in rows(os.path.join(RES, "mbi", "stress.csv"))}
    num("nRepsStressPasses", m)
    num("nRepsStressTraced", n)
    num("nRepsStressErr", len(err))
    for who, k, tot in (("", k_st, m), ("Stretto", k_tr, n)):
        num(f"nRepsStress{who}Always", sum(k[p] == tot for p in err))
        num(f"nRepsStress{who}Never", sum(k[p] == 0 for p in err))
        num(f"nRepsStress{who}Sometimes", sum(0 < k[p] < tot for p in err))
    out = [f"The stress baseline on MBI passed {m} more times (`experiments/run_remaining.sh stressreps`,",
           "`reps/stress-mbi-<k>.csv`) at the settings of the single pass: up to 30 runs per program with random delays at",
           "the default limits, stopping at the first run that does not finish; pass k draws its delays from the seed",
           f"20261020 + k (the rows' seeds: {seed_text}).",
           f"Rows by code commit: {commits([r for s in st for r in s.values()])}. Stretto's side is its {n} traced runs, the",
           f"corpus run and the {n - 1} repetitions above, each scored by its verdict, observed or predicted. Labelled errors in",
           f"every pass and every traced run: {len(err)}.", "",
           "| labelled errors | in every pass or run | in some | in none |", "|---|---|---|---|",
           f"| made to fail by a stress pass (of {m}) | {sum(k_st[p] == m for p in err)} | {sum(0 < k_st[p] < m for p in err)} | {sum(k_st[p] == 0 for p in err)} |",
           f"| reported by Stretto from a traced run (of {n}) | {sum(k_tr[p] == n for p in err)} | {sum(0 < k_tr[p] < n for p in err)} | {sum(k_tr[p] == 0 for p in err)} |",
           "", "Each pass against Stretto's corpus run, as the single comparison (\"The baselines together\"): the errors",
           "Stretto finds and the pass misses, the reverse, and the exact two-sided sign test of that split.", "",
           "| pass | seed | errors made to fail | correct programs failing | Stretto only | the pass only | p |",
           "|---|---|---|---|---|---|---|"]
    per = []
    for p_name, s in zip(passes, st):
        T = {p for p in err if s[p]["first_failure"]}
        b, c = len(S - T), len(T - S)
        per.append((b, c, sign_test(b, c)))
        fp = sum(1 for p in ok if s[p]["first_failure"])
        seed = next(iter(s.values())).get("seed", "")
        out.append(f"| {p_name[11:-4]} | {seed} | {len(T)} | {fp} | {b} | {c} | {p_text(per[-1][2])} |")
    sig = [x for x in per if x[0] > x[1] and x[2] < 0.05]
    fails = [(p, p_name[11:-4], s[p]) for p_name, s in zip(passes, st) for p in ok if s[p]["first_failure"]]
    num("nRepsStressFPPasses", len({k for _, k, _ in fails}))
    num("nRepsStressFPProgs", len({p for p, _, _ in fails}))
    if fails:
        out += ["", f"Correct programs a pass makes fail: {len({p for p, _, _ in fails})}, in {len({k for _, k, _ in fails})} of the {m} passes ("
                + "; ".join(f"{p} in pass {k}, run {r['first_failure']}, {r['outcome']}" for p, k, r in fails) + ")."]
    if all(p in single for p in err):
        T1 = {p for p in err if single[p]["first_failure"]}
        alike = sum({p for p in err if s[p]["first_failure"]} == T1 for s in st)
        num("nRepsStressAsSingle", alike)
        out += ["", f"Passes that make exactly the labelled errors of the single pass (`stress.csv`, {len(T1)}) fail: "
                f"{alike} of {m} (`nRepsStressAsSingle`)."]
    ps = sorted(x[2] for x in per)
    num("nRepsStressPassFoundMin", min(sum(bool(s[p]["first_failure"]) for p in err) for s in st))
    num("nRepsStressPassFoundMax", max(sum(bool(s[p]["first_failure"]) for p in err) for s in st))
    num("nRepsStressPassStrettoMin", min(x[0] for x in per))
    num("nRepsStressPassStrettoMax", max(x[0] for x in per))
    num("nRepsStressPassOtherMin", min(x[1] for x in per))
    num("nRepsStressPassOtherMax", max(x[1] for x in per))
    num("RepsStressPassPMin", p_text(ps[0]))
    num("RepsStressPassPMedian", p_text(statistics.median(ps)))
    num("RepsStressPassPMax", p_text(ps[-1]))
    num("nRepsStressPassSig", len(sig))
    # every traced run against every pass: the corpus run is one sample of Stretto's side too
    Ts = [{p for p in err if s[p]["first_failure"]} for s in st]
    Ss = [{p for p in err if score.verdict(b[p]) == "ERROR"} for b in traced]
    pairs = [[(len(Si - T), len(T - Si), sign_test(len(Si - T), len(T - Si))) for T in Ts] for Si in Ss]
    flat = sorted(x[2] for row in pairs for x in row)
    pair_sig = sum(x[0] > x[1] and x[2] < 0.05 for row in pairs for x in row)
    num("nRepsStressPairs", len(flat))
    num("nRepsStressPairSig", pair_sig)
    num("RepsStressPairPMin", p_text(flat[0]))
    num("RepsStressPairPMedian", p_text(statistics.median(flat)))
    num("RepsStressPairPMax", p_text(flat[-1]))
    # The pooled test: the program is the unit, since the runs of one program share its code and
    # are not independent of each other, while programs are. Each labelled error has a detection
    # rate on each side, Stretto's (traced runs that report it, of n) and stress testing's (passes
    # that make it fail, of m), and the test is the exact paired permutation (sign-flip) test of
    # the per-program differences of the rates: their sum is how many more labelled errors one
    # traced run finds than one stress pass, in expectation, and under the null hypothesis that
    # the two are alike on every program each difference is as likely negative as positive. It
    # weighs a program by how far its rates differ (11 of 11 against 9 of 10 counts less than
    # against 0 of 10), needs no model of how rates vary between programs, and with one run on
    # each side it is the single comparison's sign test.
    hi = [p for p in err if k_tr[p] * m > k_st[p] * n]
    lo = [p for p in err if k_tr[p] * m < k_st[p] * n]
    mean_tr = sum(k_tr.values()) / n
    mean_st = sum(k_st.values()) / m
    pooled = paired_permutation([k_tr[p] * m - k_st[p] * n for p in err])
    num("nRepsStressRateStretto", len(hi))
    num("nRepsStressRateOther", len(lo))
    num("RepsStressMeanStretto", mean_tr)
    num("RepsStressMeanOther", mean_st)
    num("RepsStressRateP", p_text(pooled))
    rng_ = lambda xs: f"{min(xs)}" if min(xs) == max(xs) else f"{min(xs)}–{max(xs)}"    # noqa: E731
    out += ["", f"Stretto's lead is significant at 5% (p < 0.05, Stretto ahead) against {len(sig)} of the {m} passes; p ranges",
            f"from {p_text(ps[0])} to {p_text(ps[-1])} (median {p_text(statistics.median(ps))}) "
            f"(`nRepsStressPassSig`, `RepsStressPassPMin`, `RepsStressPassPMedian`, `RepsStressPassPMax`).", "",
            f"The corpus run is one sample of Stretto's side as a pass is of stress testing's: each of the {n} traced runs",
            f"against each of the {m} passes, the same split and test (ranges over the passes):", "",
            "| traced run | errors reported | Stretto only | the pass only | p | passes against which the lead is significant |",
            "|---|---|---|---|---|---|"]
    for label, Si, row in zip(["corpus run"] + [f"repetition {p[4:-4]}" for p in reps_], Ss, pairs):
        out.append(f"| {label} | {len(Si)} | {rng_([x[0] for x in row])} | {rng_([x[1] for x in row])} | "
                   + (p_text(min(x[2] for x in row)) if len({x[2] for x in row}) == 1 else
                      f"{p_text(min(x[2] for x in row))}–{p_text(max(x[2] for x in row))}")
                   + f" | {sum(x[0] > x[1] and x[2] < 0.05 for x in row)} |")
    out += ["", f"Of the {len(flat)} pairs, the lead is significant in {pair_sig}; p ranges from {p_text(flat[0])} to {p_text(flat[-1])}",
            f"(median {p_text(statistics.median(flat))}) (`nRepsStressPairs`, `nRepsStressPairSig`, `RepsStressPairPMin`,",
            "`RepsStressPairPMedian`, `RepsStressPairPMax`).", "",
            "Pooled over the passes and runs, by program: each labelled error's detection rate with Stretto (traced runs",
            f"that report it, of {n}) against stress testing's (passes that make it fail, of {m}), and the exact paired",
            "permutation test of the per-program differences (each difference's sign flipped at random; with one run on",
            "each side it is the single comparison's sign test). The program is the unit, as runs of one program are not",
            f"independent. One traced run finds {mean_tr:.2f} of the {len(err)} labelled errors on average, one stress pass",
            f"{mean_st:.2f}; Stretto's rate is the higher for {len(hi)} programs and stress testing's for {len(lo)}:",
            f"p = {p_text(pooled)} (`RepsStressMeanStretto`, `RepsStressMeanOther`, `nRepsStressRateStretto`,",
            "`nRepsStressRateOther`, `RepsStressRateP`). The sign test over the programs whose rates differ, which counts",
            f"only which rate is the higher, gives p = {p_text(sign_test(len(hi), len(lo)))}.", "",
            f"Labelled errors some pass or some traced run misses (the other {sum(k_st[p] == m and k_tr[p] == n for p in err)} are found by every one):", "",
            "| program | stress passes that make it fail | Stretto's traced runs that report it |", "|---|---|---|"]
    out += [f"| {p} | {k_st[p]}/{m} | {k_tr[p]}/{n} |" for p in err if not (k_st[p] == m and k_tr[p] == n)]
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
    # the recorded runs infeasible at B=0, by how the run ended: realisable at no threshold, or
    # finished (a corpus run's outcome; an idiom's, whether it was replayed as finished), or not
    run = {f"experiments/results/{c}/{r['name']}/trace": r["run"] for c in ("mbi", "mbb")
           for r in rows(os.path.join(RES, c, f"{c}.csv"))}
    replayed = {r["trace"] for r in rows(os.path.join(RES, "e2_replay.csv"))}
    obsbuf = [r for r in obs if r["B0"] == "False"]
    never = [r for r in obsbuf if r["at_inf"] != "True"]
    fin = [r for r in obsbuf if r not in never and (run[r["trace"]].startswith("FINISHED") if r["trace"] in run else r["trace"] in replayed)]
    num("nEtwoObsBufNever", len(never))
    num("nEtwoObsBufFinished", len(fin))
    num("nEtwoObsBufStopped", len(obsbuf) - len(never) - len(fin))
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
        bm = rows(os.path.join(RES, "b_impl_mpich.csv"))
        if bm:
            out += [f"| {r['setting']} | {r.get('path', 'send')} | `{r['mpirun_args'] or '-'}` | {r['eager_max_bytes']} | {r['rendezvous_min_bytes']} |" for r in bm]
            out += ["", "MPICH 4.2.0 (`b_impl_mpich.csv`) is Ubuntu's ch4:ucx build; -1: no exchange hung up to the largest",
                    f"size tried. Its rows name the code commit {commits(bm)}."]
            for r in bm:
                if r["setting"] == "mpich-default" and (r.get("path") or "send") == "send":
                    num("BimplMPICH", int(r["eager_max_bytes"]))     # MPICH's default eager payload
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


# Open MPI's fatal error handler ends the job with the error class as its exit status, so a
# run whose error message the harness did not capture is recorded as APP-ERROR:<class>; the
# classes of Open MPI 4.1.6's mpi.h the stress tables meet
OMPI_ERROR_CLASS = {13: "MPI_ERR_ARG", 15: "MPI_ERR_TRUNCATE", 16: "MPI_ERR_OTHER", 17: "MPI_ERR_INTERN"}


def fp_outcomes(fp, by, traced="the traced run") -> str:
    """How the correct programs a stress table fails end, against how their traced run ended."""
    if not fp:
        return ""
    alike = [r for r in fp if by[r["program"]].get("run") == r["outcome"]]
    out = f", {len(alike)} of them ending as {traced} did (column `run` of the corpus table)"
    for r in fp:
        if r in alike:
            continue
        out += f"; {r['program']} ends with {r['outcome']}, {traced} with {by[r['program']].get('run') or 'nothing recorded'}"
        m = re.fullmatch(r"APP-ERROR:(\d+)", r["outcome"])
        if m and int(m.group(1)) in OMPI_ERROR_CLASS:
            out += (f" ({m.group(1)} is the error class of {OMPI_ERROR_CLASS[int(m.group(1))]}, the exit status of Open MPI's"
                    " fatal error handler, whose message was not captured)")
    return out


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
    hung = [r for r in found + fp if r["outcome"] == "TIMEOUT"]
    other = collections.Counter(r["outcome"] for r in found + fp if r["outcome"] != "TIMEOUT")
    tp, fn_ = len(found), len(err) - len(found)
    fpn, tn = len(fp), len(ok) - len(fp)
    prec = tp / (tp + fpn) if tp + fpn else 0.0
    rec = tp / (tp + fn_) if tp + fn_ else 0.0
    c = {"tp": tp, "fp": fpn, "fn": fn_, "tn": tn, "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0}
    # Stretto's verdicts from one run on the same programs: the baseline may run a subset of
    # the corpus (stress_baseline.py --select), whose score differs from the whole corpus's
    cs = score.confusion([by[r["program"]] for r in err + ok], score.verdict)
    if key:
        CONF[(key, "stress")] = c
        num(f"{key}fonestress", c["f1"])
        num(f"{key}foneunionstress", cs["f1"])
        num(f"n{key}StressRuns", runs)
        num(f"n{key}StressProgs", len(err) + len(ok))
        num(f"n{key}StressErr", len(err))
        num(f"n{key}StressFound", len(found))
        num(f"n{key}StressLater", len(later))
        num(f"n{key}StressMedian", med if isinstance(med, str) else int(med))
        num(f"n{key}StressPred", len(pred))
        num(f"n{key}StressPredMissed", len(pred_missed))
        num(f"n{key}StressFP", len(fp))
        # the errors Stretto predicts from a clean run and MUST does not report (CleanPred in
        # e1): how many of them stress testing makes fail as well
        clean = {n for n, r in by.items() if r["expect"] == "ERROR" and score.must_verdict(r) != "ERROR"
                 and score.predicted_verdict(r) == "ERROR" and score.observed_verdict(r) != "ERROR"}
        num(f"n{key}CleanPredStress", sum(1 for r in found if r["program"] in clean))
    names = lambda sel: " (" + ", ".join(r["program"] for r in sel) + ")" if sel else ""    # noqa: E731
    return "\n".join([
        f"### Stress-testing baseline: {name}", "",
        f"Up to {runs} runs per program with random send and receive delays per rank, stopping at the first run that",
        f"does not finish (`experiments/stress_baseline.py`, `stress.csv`). Programs: {len(st)}. Rows by code commit: {commits(st)}.", "",
        f"Labelled-error programs: {len(err)}. Stress testing makes {len(found)} fail (median run of the first failure {med};",
        f"{len(later)} only after the first run). Stretto reports {len(rem)} from one run, {len(pred)} of them predicted from a",
        f"run that finished; stress testing never makes {len(pred_missed)} of those fail{names(pred_missed)}.",
        f"Correct programs that fail: {len(fp)}{names(fp)}{fp_outcomes(fp, by)}.",
        f"Of the {len(found) + len(fp)} failures, {len(hung)} are hangs killed at the timeout"
        + (" and the others end with " + ", ".join(f"{o} ({n})" for o, n in sorted(other.items())) if other else "") + ".", "",
        "| | TP | FP | FN | TN | F1 |", "|---|---|---|---|---|---|",
        f"| Stretto, observed or predicted, on these programs | {cs['tp']} | {cs['fp']} | {cs['fn']} | {cs['tn']} | {cs['f1']:.2f} |",
        f"| random delays, default limits | {c['tp']} | {c['fp']} | {c['fn']} | {c['tn']} | {c['f1']:.2f} |", ""])


def stress_eager(name, path, key=None):
    """The stress-testing baseline again with the eager threshold forced down in every run
    (stress_baseline.py --eager: stress-eager.csv), against the same baseline at the default
    limits (stress.csv)."""
    d = os.path.dirname(path)
    se = rows(os.path.join(d, "stress-eager.csv"))
    if not se:
        return ""
    st = {r["program"]: r for r in rows(os.path.join(d, "stress.csv"))}
    by = {r["name"]: r for r in score.compiled(rows(path))}
    err = [r for r in se if r["expect"] == "ERROR" and r["program"] in by]
    ok = [r for r in se if r["expect"] == "OK" and r["program"] in by]
    found = [r for r in err if r["first_failure"]]
    later = [r for r in found if int(r["first_failure"]) > 1]
    fp = [r for r in ok if r["first_failure"]]
    runs = max(int(r["runs"]) for r in se)
    eager = ", ".join(sorted({r["eager_bytes"] for r in se}))
    args = ", ".join(sorted({f"`{r['mpirun_args']}`" for r in se}))
    hung = [r for r in found + fp if r["outcome"] == "TIMEOUT"]
    other = collections.Counter(r["outcome"] for r in found + fp if r["outcome"] != "TIMEOUT")
    # the errors Stretto predicts from one run that the runs at the default limits never make
    # fail (the buffering programs on MBI), and how many of them the forced limit exposes; E2's
    # replays mark the ones that send to their own rank untestable below their bound
    pred = [r for r in err if score.predicted_verdict(by[r["program"]]) == "ERROR"]
    missed = [r for r in pred if r["program"] in st and not st[r["program"]]["first_failure"]]
    missed_found = [r for r in missed if r["first_failure"]]
    still = [r for r in missed if not r["first_failure"]]
    untestable = {r["trace"].split("/")[-2] for r in rows(os.path.join(RES, "e2_replay.csv")) if r["verdict"] == "untestable"}
    still_self = [r for r in still if r["program"] in untestable]
    new = [r for r in err if r["first_failure"] and r["program"] in st and not st[r["program"]]["first_failure"]]
    lost = [r for r in err if not r["first_failure"] and r["program"] in st and st[r["program"]]["first_failure"]]
    # against Stretto's verdict from one run: the errors only one of the two reports
    only_stretto = [r for r in err if not r["first_failure"] and score.verdict(by[r["program"]]) == "ERROR"]
    only_stress = [r for r in found if score.verdict(by[r["program"]]) != "ERROR"]
    c = {"tp": len(found), "fp": len(fp), "fn": len(err) - len(found), "tn": len(ok) - len(fp)}
    prec = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 0.0
    rec = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 0.0
    c["f1"] = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    if key:
        CONF[(key, "stresseager")] = c
        num(f"{key}fonestresseager", c["f1"])
        num(f"n{key}StressEagerRuns", runs)
        num(f"n{key}StressEagerBytes", eager)
        num(f"n{key}StressEagerErr", len(err))
        num(f"n{key}StressEagerFound", len(found))
        num(f"n{key}StressEagerLater", len(later))
        num(f"n{key}StressEagerFP", len(fp))
        num(f"n{key}StressEagerHung", len(hung))
        num(f"n{key}StressEagerNew", len(new))
        num(f"n{key}StressEagerLost", len(lost))
        num(f"n{key}StressEagerBufFound", len(missed_found))
        num(f"n{key}StressEagerPredMissed", len(still))
        num(f"n{key}StressEagerPredMissedSelf", len(still_self))
        num(f"n{key}StressEagerOnlyStretto", len(only_stretto))
        num(f"n{key}StressEagerOnlyStress", len(only_stress))

    def outcome(r):
        if not r:
            return "not run"
        return f"fails in run {r['first_failure']} ({r['outcome']})" if r["first_failure"] else f"finishes all {r['runs']} runs"
    names = lambda sel: " (" + ", ".join(r["program"] for r in sel) + ")" if sel else ""    # noqa: E731
    # the correct programs that fail at either setting, and whether they end alike at both
    fp_both = [r for r in fp if r["program"] in st and st[r["program"]]["first_failure"]]
    fp_alike = [r for r in fp_both if st[r["program"]]["outcome"] == r["outcome"]]
    fp_default = [r for r in ok if not r["first_failure"] and r["program"] in st and st[r["program"]]["first_failure"]]
    if key and st:
        num(f"n{key}StressEagerFPBoth", len(fp_both))
        num(f"n{key}StressEagerFPDefault", len(fp_default))
    if st:
        against = (f"Against the default limits: {len(new)} of these do not fail there, and {len(lost)} that fail there never fail"
                   f" here{names(lost)}. Of the {len(missed)} errors Stretto predicts that the default limits never expose,"
                   + (f" {len(missed_found)} fail and {len(still)} still never fail{names(still)}, {len(still_self)} of them sending to"
                      " their own rank (untestable below their bound in E2)." if still else f" all {len(missed_found)} fail."))
        against_ok = (f" At the default limits {len(fp_both)} of them fail too, {len(fp_alike)} ending the same way, and"
                      f" {len(fp_default)} other correct programs fail{names(fp_default)}." if fp or fp_default else "")
    else:
        against = "The baseline at the default limits has no table for this corpus, so there is nothing to compare with."
        against_ok = ""
    out = [f"### Stress-testing baseline with the eager limit forced down: {name}", "",
           f"The same baseline (up to {runs} runs per program, the same random delays, seed and timeout) with Open MPI's eager",
           f"threshold forced down to {eager} payload bytes in every run (`stress_baseline.py --eager`, `stress-eager.csv`):",
           f"{args}.",
           "This is the setting E2 replays a buffering finding with below its bound, at the least eager limit the TCP",
           "transport accepts; the shared-memory transport is left out because it sends up to 256 bytes inline whatever its",
           "limit, and the self transport does the same for a send to the sending rank.",
           f"Programs: {len(se)}. Rows by code commit: {commits(se)}.", "",
           f"Labelled-error programs: {len(err)}. Forced down, stress testing makes {len(found)} fail ({len(later)} only after the first run).",
           against,
           f"Correct programs that fail: {len(fp)}{names(fp)}{fp_outcomes(fp, by, 'the traced run at the default limits')}."
           + against_ok,
           f"Of the {len(found) + len(fp)} failures, {len(hung)} are hangs killed at the timeout"
           + (" and the others end with " + ", ".join(f"{o} ({n})" for o, n in sorted(other.items())) if other else "") + ".",
           f"Against Stretto's verdict from one run: {len(only_stretto)} errors Stretto reports are never made to fail{names(only_stretto)},",
           f"and {len(only_stress)} made to fail Stretto does not report{names(only_stress)}.", "",
           "| | TP | FP | FN | TN | F1 |", "|---|---|---|---|---|---|",
           f"| random delays, eager limit forced down | {c['tp']} | {c['fp']} | {c['fn']} | {c['tn']} | {c['f1']:.2f} |", ""]
    diff = [r for r in se if r["program"] in by and r["program"] in st and bool(r["first_failure"]) != bool(st[r["program"]]["first_failure"])]
    if diff:
        out += ["Programs whose outcome the forced limit changes:", "",
                "| program | label | default limits | eager limit forced down |", "|---|---|---|---|"]
        out += [f"| {r['program']} | {r['expect']} | {outcome(st[r['program']])} | {outcome(r)} |" for r in diff]
        out.append("")
    return "\n".join(out)


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
    # the buffering profile of the cross alternative in that trace: a request probe (tag 222)
    # that could report a request of another size, feasible from a threshold on
    rep = os.path.join(d, "report4.txt")
    prof = re.findall(r"^\[ *probe-alt *\].* tag=222 (\d+)B instead of .* tag=222 (\d+)B \[B >= (\d+)\]$",
                      open(rep).read() if os.path.exists(rep) else "", re.M)
    cross = sorted({int(b) for new, old, b in prof if new != old})
    if cross:
        num("BHypreCross", cross[0])
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
        num("nMGStressRanks", " and ".join(str(n) for n in sorted({int(r["np"]) for r in st})))
    return "\n".join(out) + "\n"


def dealii():
    """A third documented bug: deal.II 9.3.0-rc1's RemotePointEvaluation can probe a message of
    the next call (experiments/dealii/run.sh: predict-9.3.0-rc1.csv, predict-9.3.0.csv, stress.csv;
    experiments/dealii/changes.py: changes.csv)."""
    d = os.path.join(RES, "dealii")
    pr, fixed, st = (rows(os.path.join(d, f)) for f in ("predict-9.3.0-rc1.csv", "predict-9.3.0.csv", "stress.csv"))
    if not pr:
        return "_no results_\n"
    tests = "/".join(sorted({r["test"] for r in pr + fixed}))
    out = [f"Traced runs of deal.II's test `{tests}` (MPICH build); every probe alternative on the tag of",
           "RemotePointEvaluation is replayed with the pins of its horizon and with its control (`experiments/dealii/predict.py`).",
           "An alternative is *cross* if the probe would see a message of another call (the race), *same* if another",
           "rank's message of the same call. A replay *changes the output* when the test's log differs from its",
           f"control's; the expected log is the test's own `.output` file. Rows by code commit: {commits(pr + fixed + st)}.", "",
           "| version | ranks | kind | traced runs | traced runs as expected | runs with one | alternatives | replayed | pins matched | finished | output changed | differs from the expected | controls finished | controls as expected |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for version, vrows, prefix in (("9.3.0-rc1", pr, "Deal"), ("9.3.0", fixed, "DealFixed")):
        ok = [r for r in vrows if not (r.get("replay") or "").startswith("traced run")]
        per_run = {(r["np"], r["run"]): r for r in ok}
        for np_ in sorted({r["np"] for r in vrows}, key=int):
            runs = {k: r for k, r in per_run.items() if k[0] == np_}
            failed = len({r["run"] for r in vrows if r["np"] == np_}) - len(runs)
            for kind, macro in (("cross", ""), ("same", "Same")):
                rep = [r for r in ok if r["np"] == np_ and r.get("pin") and r.get("kind") == kind]
                done = [r for r in rep if r.get("replay") and r["replay"] != "no horizon"]
                alts = sum(int(r["alternatives" if kind == "cross" else "same_alternatives"] or 0) for r in runs.values())
                out.append(f"| {version} | {np_} | {kind} | {len(runs)}" + (f" (+{failed} failed)" if failed else "") +
                           f" | {sum(r.get('traced_expected') == 'True' for r in runs.values())} | {len({r['run'] for r in rep})} | {alts} "
                           f"| {len(done)} | {sum(r.get('matched') == 'True' for r in done)} | {sum(r.get('replay') == 'FINISHED' for r in done)} "
                           f"| {sum(r.get('differs_control') == 'True' for r in done)} | {sum(r.get('differs') == 'True' for r in done)} "
                           f"| {sum(r.get('control') == 'FINISHED' for r in done)} | {sum(r.get('control_expected') == 'True' for r in done)} |")
        cross = [r for r in ok if r.get("pin") and r.get("kind") == "cross" and r.get("replay") not in (None, "", "no horizon")]
        same = [r for r in ok if r.get("pin") and r.get("kind") == "same" and r.get("replay") not in (None, "", "no horizon")]
        num(f"n{prefix}Traced", len(per_run))
        num(f"n{prefix}TracedExpected", sum(r.get("traced_expected") == "True" for r in per_run.values()))
        num(f"n{prefix}Exposed", len({(r["np"], r["run"]) for r in ok if r.get("pin") and r.get("kind") == "cross"}))
        num(f"n{prefix}Alts", sum(int(r.get("alternatives") or 0) for r in per_run.values()))
        num(f"n{prefix}SameAlts", sum(int(r.get("same_alternatives") or 0) for r in per_run.values()))
        num(f"n{prefix}OtherAlts", sum(int(r.get("other_alternatives") or 0) for r in per_run.values()))
        if version == "9.3.0-rc1":
            # A clean run is one whose traced run printed the expected output: a traced run that
            # already took the next call's message shows the race and predicts nothing new
            clean = {k for k, r in per_run.items() if r.get("traced_expected") == "True"}
            cross_clean = [r for r in cross if (r["np"], r["run"]) in clean]
            num("nDealClean", len(clean))
            num("nDealCleanExposed", len({(r["np"], r["run"]) for r in ok
                                          if r.get("pin") and r.get("kind") == "cross"} & clean))
            num("nDealCleanReplayed", len(cross_clean))
            num("nDealCleanFinished", sum(r.get("replay") == "FINISHED" for r in cross_clean))
            num("nDealCleanWrong", sum(r.get("differs") == "True" for r in cross_clean))
            num("nDealReplayed", len(cross))
            num("nDealMatched", sum(r.get("matched") == "True" for r in cross))
            num("nDealFinished", sum(r.get("replay") == "FINISHED" for r in cross))
            num("nDealChanged", sum(r.get("differs_control") == "True" for r in cross))
            num("nDealWrong", sum(r.get("differs") == "True" for r in cross))
            num("nDealControls", sum(r.get("control") == "FINISHED" for r in cross))
            num("nDealControlsSame", sum(r.get("control_expected") == "True" for r in cross))
            num("nDealSameReplayed", len(same))
            num("nDealSameChanged", sum(r.get("differs_control") == "True" for r in same))
            num("nDealRanks", "/".join(sorted({r["np"] for r in pr}, key=int)))
            lines = [int(r["lines"]) for r in cross if r.get("differs") == "True" and r.get("lines")]
            if lines:
                num("nDealLinesMin", min(lines))
                num("nDealLinesMax", max(lines))
    others = sorted({int(r["other_alternatives"]) for r in pr + fixed if r.get("other_alternatives")})
    if others:
        out += ["", f"Every traced run also has {others[0]}" + (f" to {others[-1]}" if len(others) > 1 else "") +
                " alternatives of probes on other tags (the consensus algorithms and cell exchanges that set up the",
                "triangulation and the cache), which are not replayed."]
    if st:
        out += ["", "Untraced runs of the same test on 9.3.0-rc1, plain and with random send and receive delays of 0 to 20 ms",
                "per rank from the replay library (no pin); a run *differs* when its log differs from the expected one.", "",
                "| ranks | setting | runs | finished | differs from the expected |", "|---|---|---|---|---|"]
        for np_ in sorted({r["np"] for r in st}, key=int):
            for setting in ("plain", "delays"):
                rs = [r for r in st if r["np"] == np_ and r["setting"] == setting]
                out.append(f"| {np_} | {setting} | {len(rs)} | {sum(r['result'] == 'FINISHED' for r in rs)} | {sum(r['differs'] == 'True' for r in rs)} |")
        num("nDealStressRuns", len(st))
        num("nDealStressChanged", sum(r["differs"] == "True" for r in st))
        num("nDealStressPlainChanged", sum(r["differs"] == "True" for r in st if r["setting"] == "plain"))
        num("nDealStressDelayChanged", sum(r["differs"] == "True" for r in st if r["setting"] == "delays"))
        num("nDealStressPlainRuns", sum(r["setting"] == "plain" for r in st))
        num("nDealStressDelayRuns", sum(r["setting"] == "delays" for r in st))
    ch = rows(os.path.join(d, "changes.csv"))
    if ch:
        out += ["", "Where the logs of 9.3.0-rc1 differ from the expected one (`experiments/dealii/changes.py`). The test prints, per rank,",
                "one line per point and object with one column per call (avg, min, max, insert), so the changed lines name the",
                "ranks and the changed columns the calls whose values were wrong. Every call sends the same values, so a call is",
                "right whenever it took one message from each sender. A probe of call k that takes a sender's message of call k+1",
                "makes its own rank's call k wrong, and one later call of the same object, in which the rank takes the other",
                "sender twice: k+1, or the object's last call if the sender that ran ahead stays ahead. Rows by code commit:",
                commits(ch) + ".", "",
                "| runs | logs that differ | the probing rank's values in two consecutive calls | other ranks' values | two wrong calls per changed rank and object | consecutive ones |",
                "|---|---|---|---|---|---|"]

        def at_probe(r):
            """The pinned rank's values are wrong in exactly two consecutive calls of one object."""
            calls = sorted((int(o), ("avg", "min", "max", "insert").index(c)) for rank, o, c in
                           (e.split(":") for e in r["evaluations"].split()) if rank == r["pinned_rank"])
            return len(calls) == 2 and calls[0][0] == calls[1][0] and calls[1][1] - calls[0][1] == 1

        def elsewhere(r):
            return any(x != r["pinned_rank"] for x in r["ranks"].split())

        for kind, title in (("cross", "cross replays"), ("same", "same replays"), ("control", "controls"),
                            ("stress-plain", "untraced, plain"), ("stress-delays", "untraced, with delays")):
            rs = [r for r in ch if r["kind"] == kind]
            if not rs and kind.startswith("stress"):
                continue
            dash = kind.startswith("stress")
            out.append(f"| {title} | {len(rs)} | {'-' if dash else sum(map(at_probe, rs))} | {'-' if dash else sum(map(elsewhere, rs))} "
                       f"| {sum(r['pairs'] == 'True' for r in rs)} | {sum(r['consecutive'] == 'True' for r in rs)} |")
        cr = [r for r in ch if r["kind"] == "cross"]
        num("nDealChangedAtProbe", sum(map(at_probe, cr)))
        num("nDealChangedElsewhere", sum(map(elsewhere, cr)))
        num("nDealSameElsewhere", sum(1 for r in ch if r["kind"] == "same" and elsewhere(r) and not at_probe(r)))
        num("nDealControlsElsewhere", sum(1 for r in ch if r["kind"] == "control" and elsewhere(r) and not at_probe(r)))
        num("nDealStressPairs", sum(1 for r in ch if r["kind"].startswith("stress") and r["pairs"] == "True"))
    return "\n".join(out) + "\n"


def _new_rows(rs, driver, build):
    """The rows of a HYPRE 3.2.0 campaign table in one form: experiments/hypre3/predict.py's
    (kind cross/same/other, the run's cross count in a column) and experiments/scout.py's (kind
    cross-phase/cross-tag/same, the run's counts in its findings, the level count in the driver
    name: ex2-parasails2-dense is two levels with a threshold, ex2-parasails2 the level count
    without one)."""
    out = []
    for r in rs:
        if "findings" in r:
            m = re.search(r"parasails(\d)", r["driver"])
            levels, ok = (int(m.group(1)) if m else 0), r.get("traced") == "FINISHED"
            cross = sum(int(n) for n in re.findall(r"kind:cross-[a-z]+=(\d+)", r.get("findings") or ""))
            kind = "cross" if (r.get("kind") or "").startswith("cross") else r.get("kind") or ""
        else:
            levels, ok = int(r["levels"]), not (r.get("replay") or "").startswith("traced run")
            cross, kind = int(r.get("cross") or 0), r.get("kind") or ""
        out.append({**r, "drv": r.get("driver", ""), "driver": driver, "build": build, "np": int(r["np"]), "levels": levels,
                    "ok": ok, "cross": cross, "kind": kind if r.get("pin") else ""})
    return out


def hypre_new():
    """E5f: a new bug in a current release. HYPRE 3.2.0's ParaSails sends the row requests of
    every sparsity-pattern level with one tag and takes them with a wildcard probe
    (experiments/hypre3/run.sh: predict.csv, predict-fix.csv, stress[-fix].csv, ninja[-fix].csv),
    and PETSc 3.26.0's unmodified KSP tutorial ex2 reaches it (experiments/petsc/run.sh:
    parasails.csv, parasails-default-m32.csv, stress.csv, ninja.csv, scan.csv)."""
    d, p = os.path.join(RES, "hypre3"), os.path.join(RES, "petsc")
    pr, fx, st, sf = (rows(os.path.join(d, f)) for f in ("predict.csv", "predict-fix.csv", "stress.csv", "stress-fix.csv"))
    nj, nf = (ninja_runs_rows(os.path.join(d, f)) for f in ("ninja.csv", "ninja-fix.csv"))
    ps, pm, pst, scan = (rows(os.path.join(p, f)) for f in ("parasails.csv", "parasails-default-m32.csv", "stress.csv", "scan.csv"))
    pnj = ninja_runs_rows(os.path.join(p, "ninja.csv"))
    if not (pr or ps):
        return "_no results_\n"
    # (title, macro prefix, rows): hypre's ex5 with -sai_levels, its fixed variant, PETSc's ex2
    # with the threshold given, and the level count without a threshold: a first pass at -m 32
    # (parasails-default-m32.csv) and, once run.sh parasails runs it, ex2-parasails2-nothresh
    nothresh = [r for r in ps if "nothresh" in r["driver"]]
    groups = [("hypre's `ex5` with `-sai_levels`, HYPRE 3.2.0", "HypreNew", _new_rows(pr, "ex5", "3.2.0")),
              ("the same, even levels' requests under tag 227 (the fix)", "HypreNewFix", _new_rows(fx, "ex5", "3.2.0-fix")),
              ("PETSc 3.26.0's `ex2 -m 48 -n 48`, `-pc_hypre_parasails_thresh 0.01`", "Petsc",
               _new_rows([r for r in ps if id(r) not in {id(x) for x in nothresh}], "ex2", "3.26.0")),
              ("PETSc's `ex2`, `-pc_hypre_parasails_nlevels` without a threshold", "PetscNoThresh", _new_rows(pm + nothresh, "ex2", "3.26.0"))]
    groups = [g for g in groups if g[2]]
    every = pr + fx + st + sf + nj + nf + ps + pm + pst + pnj + scan
    out = ["ParaSails builds the sparsity pattern of its approximate inverse one level at a time. Each level Isends its row",
           "requests with tag 221, counts the requests each rank will get with an Allreduce, and takes them with",
           "`MPI_Probe(MPI_ANY_SOURCE, 221)` and a receive from the probed source. Every level uses the one tag, and nothing",
           "collective lies between a rank's last probe of level L and a neighbour's requests of level L+1, so with two or",
           "more levels a probe can take the next level's request and the setup hangs. HYPRE 2.11.0's fix of the race of E5c",
           "gave the pruned-rows and the stored-rows exchanges tags of their own but kept one tag for every level. An",
           "alternative is *cross* if the probe would take a request sent after another number of collectives than the one",
           "it took (another level's), *same* if another rank's request of its level, *other* on any other tag. Every",
           "alternative is replayed with the pins of its horizon and with its control, both under the tracer, which judges",
           "each a run of the program without its pins or not (`experiments/hypre3/predict.py`, `experiments/scout.py`).",
           "The fixed variant sends the requests of even levels under a tag of their own (227). Runs at 4 and 8",
           f"processes, Open MPI, its default eager limits. Rows by code commit: {commits(every)}.", "",
           "| driver | levels | ranks | traced runs | with a cross alternative | cross alternatives (per trace) | replays hang | "
           "pins matched | replays runs of the program | controls finish | controls runs of the program | realisable from |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    word = {4: "Four", 8: "Eight"}
    for title, prefix, rs in groups:
        runs = {(r["drv"], r["np"], r["levels"], r["run"]): r for r in rs}
        for np_, lv in sorted({(r["np"], r["levels"]) for r in rs}):
            tr = [r for k, r in runs.items() if k[1:3] == (np_, lv)]
            ok = [r for r in tr if r["ok"]]
            exposed = [r for r in ok if r["cross"] > 0]
            per = sorted({r["cross"] for r in exposed})
            alts = [r for r in rs if (r["np"], r["levels"]) == (np_, lv) and r["kind"] == "cross"]
            hang = [r for r in alts if r.get("replay") == "TIMEOUT"]
            matched = [r for r in alts if r.get("matched") == "True"]
            valid = [r for r in alts if r.get("run_valid") == "True"]
            ctl = [r for r in alts if r.get("control") == "FINISHED"]
            ctl_valid = [r for r in alts if r.get("control_valid") == "True"]
            bs = sorted({r["B"] for r in alts if r.get("B")}, key=lambda b: int(re.sub(r"\D", "", b) or 0))
            b_col = ", ".join(bs) if bs else ("not recorded" if alts and "B" not in alts[0] else "-")
            per_col = "" if not per else f" ({per[0]} each)" if len(per) == 1 else f" ({per[0]}–{per[-1]})"
            dash = (lambda x: x) if alts else (lambda x: "-")
            lv_col = f"{lv} (not passed to HYPRE)" if prefix == "PetscNoThresh" else lv
            out.append(f"| {title} | {lv_col} | {np_} | {len(ok)}" + (f" (+{len(tr) - len(ok)} failed)" if len(tr) > len(ok) else "") +
                       f" | {len(exposed)} | {len(alts)}{per_col} | {dash(len(hang))} | {dash(len(matched))} | {dash(len(valid))} "
                       f"| {dash(len(ctl))} | {dash(len(ctl_valid))} | {b_col} |")
            if lv >= 2 and np_ in word and prefix in ("HypreNew", "Petsc"):    # the release's two drivers
                k = f"n{prefix}{word[np_]}"
                num(k + "Runs", len(ok))
                num(k + "Exposed", len(exposed))
                num(k + "Alts", len(alts))
                if len(per) == 1:
                    num(k + "PerTrace", per[0])
                num(k + "Hang", len(hang))
                num(k + "Finish", sum(1 for r in alts if r.get("replay") == "FINISHED"))
                num(k + "Matched", len(matched))
                num(k + "Valid", len(valid))
                num(k + "Controls", len(ctl))
                num(k + "ControlsValid", len(ctl_valid))
        ok = [r for r in runs.values() if r["ok"]]
        cross = [r for r in rs if r["kind"] == "cross"]
        num(f"n{prefix}Traced", len(ok))
        num(f"n{prefix}TracedFailed", len(runs) - len(ok))
        num(f"n{prefix}Exposed", sum(1 for r in ok if r["cross"] > 0))
        num(f"n{prefix}TwoTraced", sum(1 for r in ok if r["levels"] >= 2))
        num(f"n{prefix}TwoExposed", sum(1 for r in ok if r["levels"] >= 2 and r["cross"] > 0))
        num(f"n{prefix}OneTraced", sum(1 for r in ok if r["levels"] == 1))
        num(f"n{prefix}OneExposed", sum(1 for r in ok if r["levels"] == 1 and r["cross"] > 0))
        num(f"n{prefix}CrossAlts", len(cross))
        num(f"n{prefix}CrossHang", sum(1 for r in cross if r.get("replay") == "TIMEOUT"))
        num(f"n{prefix}CrossMatched", sum(1 for r in cross if r.get("matched") == "True"))
        num(f"n{prefix}CrossValid", sum(1 for r in cross if r.get("run_valid") == "True"))
        num(f"n{prefix}CrossControls", sum(1 for r in cross if r.get("control") == "FINISHED"))
        num(f"n{prefix}CrossControlsValid", sum(1 for r in cross if r.get("control_valid") == "True"))
    # both drivers of the release together, for the abstract and the introduction
    rel = [g[2] for g in groups if g[1] in ("HypreNew", "Petsc")]
    two = [r for rs in rel for r in {(r["drv"], r["np"], r["levels"], r["run"]): r for r in rs}.values() if r["ok"] and r["levels"] >= 2]
    cross = [r for rs in rel for r in rs if r["kind"] == "cross"]
    num("nHypreNewAllTraced", len(two))
    num("nHypreNewAllExposed", sum(1 for r in two if r["cross"] > 0))
    num("nHypreNewAllCrossAlts", len(cross))
    num("nHypreNewAllCrossHang", sum(1 for r in cross if r.get("replay") == "TIMEOUT"))
    # the realisable threshold: ex2's cross alternatives are realisable from the size of the
    # pruned-rows reply (tag 223) the neighbour sends before it leaves the level, which must be
    # buffered; predict.csv of ex5 predates the B column, so its reply size comes from the
    # fixed variant's traces, whose messages are the release's but for the tag of even levels
    def reply_bytes(rs):
        return sorted({int(r["bytes"]) for r in rs if r.get("tag") == "223" and "ReceiveReplyPrunedRows" in (r.get("site") or "")
                       and r.get("bytes") and r["levels"] >= 2})
    by = {g[1]: g[2] for g in groups}
    pb = sorted({int(m) for r in by.get("Petsc", []) if r["kind"] == "cross" for m in re.findall(r"B >= (\d+)", r.get("B") or "")})
    notes = []
    if pb:
        num("BPetscCross", pb[0])
        notes.append(f"`ex2`'s cross alternatives are realisable from B >= {', '.join(map(str, pb))} bytes")
    if reply_bytes(by.get("Petsc", [])):
        rb = reply_bytes(by["Petsc"])
        num("BPetscReply", rb[0])
        notes.append(f"its pruned-rows replies (tag 223, `ReceiveReplyPrunedRows`) have {', '.join(map(str, rb))} bytes")
    if reply_bytes(by.get("HypreNewFix", [])):
        rb = reply_bytes(by["HypreNewFix"])
        num("BHypreNewReply", rb[0])
        notes.append(f"`ex5`'s have {', '.join(map(str, rb))} bytes in every two-level trace of the fixed variant, whose "
                     "messages are the release's but for the tag of even levels (`predict.csv` predates the `B` and `bytes` columns)")
    if notes:
        out += ["", "A cross alternative needs the neighbour that sends the next level's request to have left the level, after",
                "the Waitall of its replies, one of which goes to the probing rank, which has not yet posted its receive: that",
                "reply must be buffered. " + "; ".join(notes) + "."]
    # the first pass with the level count and no threshold, against its own one-level traces
    nt = list({(r["drv"], r["np"], r["levels"], r["run"]): r for r in _new_rows(pm, "ex2", "3.26.0")}.values())
    if nt:
        one = {(r["np"], r["findings"]) for r in nt if r["ok"] and r["levels"] == 1}
        two_ok = [r for r in nt if r["ok"] and r["levels"] >= 2]
        same = sum(1 for r in two_ok if (r["np"], r["findings"]) in one)
        nfail = [r for r in nt if not r["ok"]]
        out += ["", "PETSc 3.26.0 passes ParaSails' level count to HYPRE only together with the threshold (`HYPRE_ParaSailsSetParams`",
                "inside the `if` of `-pc_hypre_parasails_thresh` in `PCSetFromOptions_HYPRE_ParaSails`), so the level count alone",
                f"runs one level: {same} of the {len(two_ok)} analysed traces of a first pass (`parasails-default-m32.csv`, `ex2 -m 32",
                "-n 32`) with `-pc_hypre_parasails_nlevels 2` and no threshold have the findings of a one-level trace at their",
                "process count."
                + (f" {len(nfail)} traced run{'s' if len(nfail) > 1 else ''} of that pass ended with "
                   + ", ".join(sorted({"%s (%s records, %s)" % (r["traced"], r["records"], r["findings"]) for r in nfail}))
                   + (" and are" if len(nfail) > 1 else " and is") + " not analysed." if nfail else "")]
        num("nPetscNoThreshSameAsOne", same)
    # every other alternative replayed: same-level requests and other probes
    out += ["", "The other alternatives, replayed the same way (*no horizon*: an alternative whose horizon the replay cannot pin, not",
            "replayed):", "", "| driver | kind | alternatives | no horizon | replays finish | hang | other | pins matched | "
            "replays runs of the program | controls finish | controls runs of the program |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for title, prefix, rs in groups:
        for kind in ("same", "other"):
            alts = [r for r in rs if r["kind"] == kind]
            if not alts:
                continue
            nh = [r for r in alts if r.get("replay") == "no horizon"]
            done = [r for r in alts if r.get("replay") not in (None, "", "no horizon")]
            res = collections.Counter(r["replay"] for r in done if r["replay"] not in ("FINISHED", "TIMEOUT"))
            out.append(f"| {title} | {kind} | {len(alts)} | {len(nh)} | {sum(r['replay'] == 'FINISHED' for r in done)} "
                       f"| {sum(r['replay'] == 'TIMEOUT' for r in done)} | {', '.join(f'{n} {k}' for k, n in sorted(res.items())) or 0} "
                       f"| {sum(r.get('matched') == 'True' for r in done)} | {sum(r.get('run_valid') == 'True' for r in done)} "
                       f"| {sum(r.get('control') == 'FINISHED' for r in done)} | {sum(r.get('control_valid') == 'True' for r in done)} |")
    # untraced runs, plain and with the replay library's random delays before calls
    # (macro prefix, driver, levels, row) per untraced run
    srs = [("HypreNew", "`ex5`, HYPRE " + r["version"], int(r["levels"]), r) for r in st] + \
          [("HypreNewFix", "`ex5`, HYPRE " + r["version"], int(r["levels"]), r) for r in sf] + \
          [("Petsc", "`ex2`, PETSc " + r["version"], int(re.search(r"parasails(\d)", r["driver"]).group(1)), r) for r in pst]
    if srs:
        out += ["", "Untraced runs, plain and with random send and receive delays of 0 to 50 ms per rank from the replay library",
                "(no pin), 30 s each, the budget and delays of E5c. The delays are sleeps before a call, so they cannot make a",
                "request issued before the level's Allreduce arrive after one issued after it, which the race needs. The PETSc",
                f"runs were stopped after the two-level ones and part of the one-level ones. Rows by code commit: {commits(st + sf + pst)}.", "",
                "| driver | levels | ranks | setting | runs | finished | timed out | other |", "|---|---|---|---|---|---|---|---|"]
        keys = sorted({(t, lv, int(r["np"]), r["setting"]) for _, t, lv, r in srs}, key=lambda k: (k[0], -k[1], k[2], k[3] != "plain"))
        for t, lv, np_, setting in keys:
            sel = [r for _, t_, l_, r in srs if (t_, l_, int(r["np"]), r["setting"]) == (t, lv, np_, setting)]
            f_ = sum(r["result"] == "FINISHED" for r in sel)
            to = sum(r["result"] == "TIMEOUT" for r in sel)
            out.append(f"| {t} | {lv} | {np_} | {setting} | {len(sel)} | {f_} | {to} | {len(sel) - f_ - to} |")
        for prefix in ("HypreNew", "HypreNewFix", "Petsc"):
            sel = [(r, lv) for p_, _, lv, r in srs if p_ == prefix]
            two = [r for r, lv in sel if lv >= 2]
            num(f"n{prefix}StressRuns", len(sel))
            num(f"n{prefix}StressHang", sum(r["result"] == "TIMEOUT" for r, _ in sel))
            num(f"n{prefix}StressTwoRuns", len(two))
            num(f"n{prefix}StressTwoHang", sum(r["result"] == "TIMEOUT" for r in two))
            num(f"n{prefix}StressTwoDelayRuns", sum(r["setting"] == "delays" for r in two))
            num(f"n{prefix}StressNotFinished", sum(r["result"] != "FINISHED" for r, _ in sel))
        two = [r for p_, _, lv, r in srs if lv >= 2 and p_ in ("HypreNew", "Petsc")]
        num("nHypreNewAllStressRuns", len(two))
        num("nHypreNewAllStressHang", sum(r["result"] == "TIMEOUT" for r in two))
    if nj or nf:
        out += ["", "NINJA (E10) on `ex5` at two levels, the release and the fixed variant, 30 s per run; a run that hangs in",
                "the ParaSails setup is killed before ParaSails reports its levels (*of which in the setup*). Rows by code",
                f"commit: {commits(nj + nf)}.", ""]
        out += _ninja_runs(nj + nf, lambda label: "HypreNewFix" if "-fix" in label else "HypreNew", phases=True)
        for prefix, rs in (("HypreNew", nj), ("HypreNewFix", nf)):
            runs = [r for r in rs if r["run"] != "0"]
            num(f"nNinja{prefix}Runs", len(runs))
            num(f"nNinja{prefix}Hang", sum(r["result"] == "TIMEOUT" for r in runs))
            num(f"nNinja{prefix}HangSetup", sum(r["result"] == "TIMEOUT" and not r.get("reached") for r in runs))
    if pnj:
        out += ["", f"NINJA on PETSc's `ex2` at two levels with the threshold 0.01, 30 s per run. Rows by code commit: {commits(pnj)}.", ""]
        out += _ninja_runs(pnj, lambda label: "Petsc")
        runs = [r for r in pnj if r["run"] != "0"]
        for mode, key in (("random", "Random"), ("system", "System"), ("app", "App")):
            sel = [r for r in runs if r["mode"] == mode]
            num(f"nNinjaPetsc{key}Runs", len(sel))
            num(f"nNinjaPetsc{key}Hang", sum(r["result"] == "TIMEOUT" for r in sel))
        app = collections.Counter(r["result"] for r in runs if r["mode"] == "app" and r["result"] not in ("FINISHED", "TIMEOUT"))
        if app:
            res, n = app.most_common(1)[0]
            num("nNinjaPetscAppAborted", n)
            num("NinjaPetscAppStatus", res.split(":")[-1])
            out += ["", f"Application-centric mode ended {n} of {sum(r['mode'] == 'app' for r in runs)} runs with {res} after its",
                    "learning run finished (Open MPI's error handler exits with the error class; not investigated)."]
    if scan:
        out += ["", "The reach of the race (`experiments/petsc/run.sh scan`, analysis only, no replay): two traced runs per",
                "ParaSails threshold (0.01, 0.05 and 0.1, PETSc's default), level count and symmetry setting, on `ex2` and on",
                "`ex45`'s 3D seven-point Laplacian (`-da_grid_x 16 -da_grid_y 16 -da_grid_z 16`), at 4 and 8 processes,",
                "every setting. *Realisable from*: the thresholds of the cross",
                f"alternatives of every trace (any B: realisable at every threshold). Rows by code commit: {commits(scan)}.", "",
                "| driver | ranks | levels | thresholds | symmetry | traced runs | with a cross alternative | cross alternatives per trace | realisable from |",
                "|---|---|---|---|---|---|---|---|---|"]
        sc = []
        for r in scan:
            m = re.match(r"(\S+) thresh=(\S+) nlevels=(\d+) sym=(\S+)", r["driver"])
            if m:
                cross = sum(int(n) for n in re.findall(r"kind:cross-[a-z]+=(\d+)", r.get("findings") or ""))
                sc.append({**r, "exe": m.group(1), "thresh": m.group(2), "levels": int(m.group(3)), "sym": m.group(4),
                           "np": int(r["np"]), "ok": r.get("traced") == "FINISHED", "cross": cross})
        for exe, np_, lv in sorted({(r["exe"], r["np"], r["levels"]) for r in sc}):
            sel = [r for r in sc if (r["exe"], r["np"], r["levels"]) == (exe, np_, lv)]
            ok = [r for r in sel if r["ok"]]
            per = sorted({r["cross"] for r in ok if r["cross"]})
            bs = sorted({b for r in ok for b in (r.get("cross_B") or "").split(";") if b},
                        key=lambda b: int(re.sub(r"\D", "", b) or 0))
            out.append(f"| `{exe}` | {np_} | {lv} | {', '.join(sorted({r['thresh'] for r in sel}))} | {', '.join(sorted({r['sym'] for r in sel}))} "
                       f"| {len(ok)}" + (f" (+{len(sel) - len(ok)} failed)" if len(sel) > len(ok) else "") +
                       f" | {sum(1 for r in ok if r['cross'])} | {'-' if not per else per[0] if len(per) == 1 else f'{per[0]}–{per[-1]}'} "
                       f"| {', '.join(bs) or '-'} |")
        ok = [r for r in sc if r["ok"]]
        multi = [r for r in ok if r["levels"] >= 2]
        bounds = sorted({int(b) for r in multi for b in re.findall(r"B >= (\d+)", r.get("cross_B") or "")})
        num("nPetscScanTraced", len(ok))
        num("nPetscScanMultiTraced", len(multi))
        num("nPetscScanMultiExposed", sum(1 for r in multi if r["cross"]))
        num("nPetscScanOneTraced", sum(1 for r in ok if r["levels"] == 1))
        num("nPetscScanOneExposed", sum(1 for r in ok if r["levels"] == 1 and r["cross"]))
        num("nPetscScanAnyB", sum(1 for r in multi if "any B" in (r.get("cross_B") or "").split(";")))
        if bounds:
            num("BPetscScanMin", bounds[0])
            num("BPetscScanMax", bounds[-1])
        anyb = sorted({r["exe"] for r in multi if "any B" in (r.get("cross_B") or "").split(";")})
        if anyb:
            out += ["", f"On {', '.join(f'`{e}`' for e in anyb)} some cross alternatives of every multi-level trace are realisable at",
                    "every threshold; the scan does not replay, and its rows do not record which probes those alternatives belong to."]
    return "\n".join(out) + "\n"


HIST_NP_WORD = {3: "Three", 4: "Four", 8: "Eight"}
HIST_ERRORS = ("1a", "1b", "1c", "1d", "2-buffering")


def _counts(text: str) -> dict:
    """'1d/L1+SP=6;1f/L1=9' -> {'1d/L1+SP': 6, '1f/L1': 9}."""
    return {k: int(v) for k, v in (x.split("=") for x in (text or "").split(";") if x)}


def _clean(r) -> bool:
    return r.get("traced") == "FINISHED" and r.get("traced_ok") == "True"


def _tally(rs, key) -> str:
    """'TIMEOUT 28, FINISHED 1': how many rows have each value of key, most first."""
    return ", ".join(f"{k or '-'} {n}" for k, n in collections.Counter(r.get(key) for r in rs).most_common()) or "-"


def hist_predict(prefix: str, rs: list, exposed, classified: bool, fixed: bool = False) -> list:
    """The table rows (one per process count) and the macros of one version's prediction table
    from experiments/history_predict.py. exposed(run row) says whether a clean run predicts the
    bug; classified, whether the campaign named the kinds of the alternatives (--classify).
    Macros, with n<prefix> (n<prefix>Fix for the fix) and, per process count, the same followed
    by Three, Four or Eight: Traced, Clean, Exposed, ErrorRuns (clean runs with a finding of an
    error class); for the findings stretto confirm replayed (role confirm) Replayed, ReplayHang,
    ReplayFail, Matched, ControlsFinish, Confirmed, Strict; for a classified campaign CrossAlts,
    SameAlts and, of the replayed alternatives (role replay, kind cross), CrossReplayed,
    CrossFail (did not finish, or printed a wrong result), CrossTruncate, CrossMatched,
    CrossValid, CrossControlsOk, CrossControlsValid, CrossConfirmed (failed with every required pin
    matched while the control finished with correct output) and CrossInconclusive (the control
    did not)."""
    p = prefix + ("Fix" if fixed else "")
    out = []
    for np_ in [None] + sorted({int(r["np"]) for r in rs}):
        sel = [r for r in rs if np_ is None or int(r["np"]) == np_]
        runs = {(r["np"], r["run"]): r for r in sel}
        clean = [r for r in runs.values() if _clean(r)]
        exp = [r for r in clean if exposed(r)]
        errs = [r for r in clean if any(k.split("/")[0] in HIST_ERRORS and not k.endswith("/observed") for k in _counts(r.get("findings")))]
        conf = [r for r in sel if r.get("role") == "confirm"]
        cross = [r for r in sel if r.get("role") == "replay" and r.get("kind") == "cross"]
        cross_alts = sum(_counts(r.get("kinds")).get("cross", 0) for r in clean)
        same_alts = sum(_counts(r.get("kinds")).get("same", 0) for r in clean)
        failed = [r for r in cross if r.get("replay") != "FINISHED" or r.get("replay_ok") != "True"]
        ctl_ok = [r for r in cross if r.get("control") == "FINISHED" and r.get("control_ok") == "True"]
        s = p + ("" if np_ is None else HIST_NP_WORD[np_])
        num(f"n{s}Traced", len(runs))
        num(f"n{s}Clean", len(clean))
        num(f"n{s}Exposed", len(exp))
        num(f"n{s}ErrorRuns", len(errs))
        if conf:
            num(f"n{s}Replayed", len(conf))
            num(f"n{s}ReplayHang", sum(r.get("replay") == "TIMEOUT" for r in conf))
            num(f"n{s}ReplayFail", sum(r.get("replay") != "FINISHED" for r in conf))
            num(f"n{s}Matched", sum(r.get("matched") == "True" for r in conf))
            num(f"n{s}ControlsFinish", sum(r.get("control") == "FINISHED" for r in conf))
            num(f"n{s}Confirmed", sum(r.get("verdict") == "confirmed" for r in conf))
            num(f"n{s}Strict", sum(r.get("strict") == "confirmed" for r in conf))
        if classified:
            num(f"n{s}CrossAlts", cross_alts)
            num(f"n{s}SameAlts", same_alts)
            num(f"n{s}CrossReplayed", len(cross))
            num(f"n{s}CrossFail", len(failed))
            num(f"n{s}CrossTruncate", sum(r.get("replay") == "MPI-ERROR:MPI_ERR_TRUNCATE" for r in cross))
            num(f"n{s}CrossMatched", sum(r.get("matched") == "True" for r in cross))
            num(f"n{s}CrossValid", sum(r.get("run_valid") == "True" for r in cross))
            num(f"n{s}CrossControlsOk", len(ctl_ok))
            num(f"n{s}CrossControlsValid", sum(r.get("control_valid") == "True" for r in cross))
            num(f"n{s}CrossConfirmed", sum(r in ctl_ok and r in failed and r.get("matched") == "True" for r in cross))
            num(f"n{s}CrossInconclusive", len(cross) - len(ctl_ok))
        if np_ is None:
            continue
        findings = collections.Counter()
        for r in clean:
            findings.update(_counts(r.get("findings")))
        crossed = (f"| {cross_alts} / {same_alts} | {len(cross)} | {_tally(cross, 'replay') if cross else '-'} | {len(failed)} "
                   f"| {sum(r.get('matched') == 'True' for r in cross)} | {sum(r.get('run_valid') == 'True' for r in cross)} "
                   f"| {len(ctl_ok)} |") if classified else "| - | - | - | - | - | - | - |"
        out.append(f"| {rs[0]['version']} | {np_} | {len(runs)} | {len(clean)} | {len(exp)} "
                   f"| {', '.join(f'{k} {n}' for k, n in sorted(findings.items())) or '-'} "
                   f"| {len(conf)} | {_tally(conf, 'replay') if conf else '-'} | {sum(r.get('matched') == 'True' for r in conf)} "
                   f"| {sum(r.get('control') == 'FINISHED' for r in conf)} | {sum(r.get('strict') == 'confirmed' for r in conf)} "
                   + crossed)
    return out


HIST_PREDICT_HEAD = ["| version | ranks | traced runs | clean | clean runs that predict it | findings of the clean runs (class/level) "
                     "| error findings replayed | replay results | pins matched | controls finished | confirmed, replay a run of the program "
                     "| cross / same alternatives | cross replayed | cross replay results | failed or wrong output | pins matched "
                     "| runs of the program | controls finished with correct output |",
                     "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]


def hist_stress(prefix: str, rs: list, fixed: bool = False) -> list:
    """The table rows and macros of one version's untraced runs (experiments/history_stress.py):
    n<prefix>[Fix]Stress{,Plain,Delay,Eager}{Runs,Fail,Hang}, a failure being a run that did not
    finish or whose output was wrong."""
    p = prefix + ("Fix" if fixed else "") + "Stress"
    out = []
    for setting, word in ((None, ""), ("plain", "Plain"), ("delays", "Delay"), ("eager", "Eager")):
        sel = [r for r in rs if setting is None or r["setting"] == setting]
        if not sel:
            continue
        num(f"n{p}{word}Runs", len(sel))
        num(f"n{p}{word}Fail", sum(r["result"] != "FINISHED" or r["ok"] != "True" for r in sel))
        num(f"n{p}{word}Hang", sum(r["result"] == "TIMEOUT" for r in sel))
    for label in dict.fromkeys(r["label"] for r in rs):
        for np_ in sorted({int(r["np"]) for r in rs if r["label"] == label}):
            for setting in ("plain", "delays", "eager"):
                sel = [r for r in rs if r["label"] == label and int(r["np"]) == np_ and r["setting"] == setting]
                if not sel:
                    continue
                bad = [r for r in sel if r["result"] != "FINISHED" or r["ok"] != "True"]
                out.append(f"| {sel[0]['version']} | {label} | {np_} | {setting} | {len(sel)} | {len(bad)} "
                           f"| {sum(r['result'] == 'TIMEOUT' for r in sel)} | {_tally(bad, 'result') if bad else '-'} |")
    return out


HIST_NOTES = {
    "cabana": ["The deadlock is an L1+SP finding: the L1 findings (1f) are reorderings of the round-1 counts, and under the",
               "single-path assumption the explorer finds executions in which a later round-1 wildcard receive takes a round-2",
               "id and the plan hangs (1d), leaving a count unreceived (1c). `stretto confirm` replays each distinct set of pins",
               "and class once, with the explorer's witness; a replay whose own trace could not be judged (`unknown`) counts as",
               "confirmed by its pins only. `Test10NoTopo-repeat2` is the same test run twice in one run (`--gtest_repeat=2`)."],
    "stempy": ["The cross alternatives are the bug at L1 (class 1f: the 8-byte size fits the map's buffer). At 4 ranks the",
               "explorer also turns them into truncations (1a, L1+SP), which `stretto confirm` does not confirm: it expects",
               "MPI_ERR_TRUNCATE, and the run ends earlier, in the harness's MPI_Abort(3) after cereal's exception, so the",
               "single-path assumption (the program goes on receiving) does not hold here. At 8 ranks the explorer is off (it",
               "had not finished one trace in 15 minutes). Every failed replay's output has cereal's \"Failed to read\"."],
    "mfem332": ["The analysis reports the cross alternative as class 1f (the other mesh's row fits the receive's buffer); its",
                "consequence, the truncation of a longer row (MPI_ERR_TRUNCATE) or the abort of \"integer set not found\"",
                "(APP-ERROR:1), shows in the replay. The explorer is off: it hits its state cap on these traces. At 8 ranks the",
                "race strikes after the horizon too, which alone is pinned: some controls fail and some replays leave a pin",
                "unmatched, so the 8-rank replays are weaker evidence; most traced runs fail there as well."],
}
HIST_NOTES["dealii8929"] = [
    "The cross alternatives are flag flips (probe-flip, L1): a failed `MPI_Iprobe(MPI_ANY_SOURCE, 12)` of one call's loop after",
    "its `MPI_Ibarrier` that could return another rank's request of the next call. The driver, `experiments/dealii8929/nbx_calls.cc`,",
    "calls `ConsensusAlgorithm_NBX` three times back to back. The replay forces the flip (`STRETTO_PIN_FLIP`, its poll named by the",
    "rank's calls before it) and the recorded matching over the horizon. Every replay printed the wrong answer the alternative",
    "predicts, for the predicted pair of ranks: the next call's request answered from the earlier call (checked on the kept",
    "outputs of the campaign, not in the table). Its control forces the",
    "recorded false once and pins nothing after the horizon, so under the replay library the race also strikes on its own,",
    "mostly in other ranks' loops: a replay counts as confirmed only where its control printed the expected output, and the",
    "others are inconclusive. Untraced runs rarely show the race. Campaign of 28 Oct 2026 (apollo, node 0, 12 processors):",
    "`TIMEOUT=5 JOBS=1`.",
]
HIST_STRESS_HEAD =["| version | driver | ranks | setting | runs | failed | hung | how the failures ended |", "|---|---|---|---|---|---|---|---|"]


def history():
    """E5g: five documented bugs from the histories of Cabana, stempy, MFEM, deal.II and LAMMPS,
    each predicted from clean traced runs of the version with the bug and replayed, tested by
    untraced runs, and the same on the fix (experiments/{cabana,stempy,mfem332,dealii8929,lammps}/run.sh;
    experiments/history_predict.py, history_stress.py, lammps/predict.py)."""
    out = []
    cases = [
        ("cabana", "Cabana", "Cabana `CommunicationPlan::createFromImports`, a round-1 wildcard receive takes a round-2 id (hang; PR #835)",
         "fbbcc37f", "38170631", ["predict-{v}.csv"], lambda r: any(k.startswith("1d/") for k in _counts(r.get("findings")))),
        ("stempy", "Stempy", "stempy 3.3.5 `gatherEvents`, the map receive takes another worker's size (cereal exception; issue #292)",
         "3.3.5", "3.3.6", ["predict-{v}-np4.csv", "predict-{v}-np8.csv"], lambda r: _counts(r.get("kinds")).get("cross", 0) > 0),
        ("mfem332", "MfemGroup", "MFEM 3.3.2 `GroupTopology::Create`, a receive takes the next mesh's group row (truncation or abort; c52c6243b)",
         "3.3.2", "3.4", ["predict-{v}.csv"], lambda r: _counts(r.get("kinds")).get("cross", 0) > 0),
        ("dealii8929", "DealNbx", "deal.II `ConsensusAlgorithm_NBX` (October 2019), a probe of one call takes the next call's request (wrong answers; issue #8929)",
         "2c1b27fb", "f9ea3396", ["predict-{v}.csv"], lambda r: _counts(r.get("kinds")).get("cross", 0) > 0),
    ]
    for d, prefix, title, bug, fix, tables, exposed in cases:
        pr = [r for t in tables for r in rows(os.path.join(RES, d, t.format(v=bug)))]
        fx = [r for t in tables for r in rows(os.path.join(RES, d, t.format(v=fix)))]
        st, sf = (rows(os.path.join(RES, d, f"stress-{v}.csv")) for v in (bug, fix))
        if not pr:
            continue
        out += [f"### {title}", "", f"`experiments/{d}/run.sh`; rows by code commit: {commits(pr + fx + st + sf)}.", ""]
        classified = d != "cabana"      # Cabana's campaign confirms its error findings and classifies nothing
        out += HIST_PREDICT_HEAD + hist_predict(prefix, pr, exposed, classified) + \
            (hist_predict(prefix, fx, exposed, classified, fixed=True) if fx else [])
        num(f"n{prefix}Ranks", "/".join(str(n) for n in sorted({int(r["np"]) for r in pr})))
        out += ["", *HIST_NOTES[d]]
        if d == "dealii8929":
            for np_ in sorted({int(r["np"]) for r in pr}):
                cross = [r for r in pr if int(r["np"]) == np_ and r.get("role") == "replay" and r.get("kind") == "cross"]
                ok = [r for r in cross if r.get("control") == "FINISHED" and r.get("control_ok") == "True"]
                conf = [r for r in ok if r.get("matched") == "True" and (r.get("replay") != "FINISHED" or r.get("replay_ok") != "True")]
                out.append(f"At {np_} ranks: {len(conf)} of {len(cross)} replays confirmed, {len(cross) - len(ok)} inconclusive "
                           "(the control printed wrong answers).")
        if st or sf:
            out += ["", "Untraced runs, plain and with random send and receive delays of 0 to 20 ms per rank from the replay library.", ""]
            out += HIST_STRESS_HEAD + (hist_stress(prefix, st) if st else []) + (hist_stress(prefix, sf, fixed=True) if sf else [])
        out.append("")
    out += lammps_history()
    if not out:
        return "_no results_\n"
    return "\n".join(["Each bug is reproduced from the project's own history: the version with the bug, its fix, and a driver that",
                      "reaches the code (the project's own test or input where there is one). A run is *clean* when it exits with",
                      "status 0 and prints the correct result; each clean traced run is analysed, its findings of an error class are",
                      "replayed by `stretto confirm` (the pins of the alternative's horizon or of the explorer's witness, and a",
                      "control that pins the recorded matching), and its *cross* alternatives (a receive that would take a message of",
                      "another round, call or kind, `experiments/<case>/classify.py`) are replayed with the pins of their whole",
                      "horizon and with their control (`experiments/history_predict.py`). Replays and controls run under the tracer,",
                      "which judges each a run of the program without its pins.", ""] + out) + "\n"


def lammps_history() -> list:
    """LAMMPS Irregular::create_atom (experiments/lammps/run.sh): predict-<v>.csv from
    experiments/lammps/predict.py, stress-<v>.csv. Macros n<Lammps|LammpsFix>{Traced,Clean,
    Exposed}, BLammps (the bound), n<...>{Default,Below,At}{Runs,Finish,Hang}, and the stress
    macros of hist_stress."""
    pr, fx, st, sf = (rows(os.path.join(RES, "lammps", f)) for f in
                      ("predict-3a2da51a.csv", "predict-411c069b.csv", "stress-3a2da51a.csv", "stress-411c069b.csv"))
    if not pr:
        return []
    out = ["### LAMMPS `Irregular::create_atom`, blocking count sends before the wildcard receives (deadlock without eager buffering; 411c069b)", "",
           "`experiments/lammps/run.sh`: traced runs analysed at L1, then untraced runs with Open MPI's default settings and",
           "on the TCP transport with the eager threshold just below and at the 2-buffering finding's bound",
           "(`experiments/lammps/predict.py`); the fix, which has no such finding, is rerun at the bound of the version with",
           f"the bug. Rows by code commit: {commits(pr + fx + st + sf)}.", "",
           "| version | ranks | traced runs | finished | with a 2-buffering finding | bound | cycle's call sites | default: finished | below the bound: hung | at the bound: finished |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    bounds = set()
    for vrows, p in ((pr, "Lammps"), (fx, "LammpsFix")):
        if not vrows:
            continue
        for np_ in [None] + sorted({int(r["np"]) for r in vrows}):
            sel = [r for r in vrows if np_ is None or int(r["np"]) == np_]
            tr = [r for r in sel if r["kind"] == "traced"]
            fin = [r for r in tr if r["result"] == "FINISHED"]
            exp = [r for r in fin if int(r.get("buffering") or 0) > 0]
            s = p + ("" if np_ is None else HIST_NP_WORD[np_])
            num(f"n{s}Traced", len(tr))
            num(f"n{s}Clean", len(fin))
            num(f"n{s}Exposed", len(exp))
            reruns = {}
            for kind, word in (("default", "Default"), ("below", "Below"), ("at", "At")):
                k = [r for r in sel if r["kind"] == kind]
                reruns[kind] = k
                num(f"n{s}{word}Runs", len(k))
                num(f"n{s}{word}Finish", sum(r["result"] == "FINISHED" for r in k))
                num(f"n{s}{word}Hang", sum(r["result"] == "TIMEOUT" for r in k))
            if np_ is None:
                continue
            for r in exp:
                m = re.search(r"B < (\d+)", r.get("bound") or "")
                if m and p == "Lammps":
                    bounds.add(int(m.group(1)))
            sites_ = "; ".join(sorted({x for r in exp for x in (r.get("cycle_sites") or "").split("; ") if x})) or "-"
            out.append(f"| {sel[0]['version']} | {np_} | {len(tr)} | {len(fin)} | {len(exp)} | {'; '.join(sorted({r.get('bound') or '' for r in exp})) or '-'} "
                       f"| {sites_} | {sum(r['result'] == 'FINISHED' for r in reruns['default'])} of {len(reruns['default'])} "
                       f"| {sum(r['result'] == 'TIMEOUT' for r in reruns['below'])} of {len(reruns['below'])} "
                       f"| {sum(r['result'] == 'FINISHED' for r in reruns['at'])} of {len(reruns['at'])} |")
    if len(bounds) == 1:
        num("BLammps", bounds.pop())
    if st or sf:
        out += ["", "Untraced runs, plain, with random send and receive delays of 0 to 20 ms per rank from the replay library, and",
                "with Open MPI's eager threshold forced down to 0 payload bytes (`eager`: the TCP transport, as stress testing on",
                "the corpora with the limit forced down).", ""]
        out += HIST_STRESS_HEAD + (hist_stress("Lammps", st) if st else []) + (hist_stress("Lammps", sf, fixed=True) if sf else [])
    return out + [""]


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


def pin_log():
    """What the log of pinned matches decided in the application replays (HYPRE, MG, and the
    AMG and Nekbone samples): pinned runs, replays and controls alike, that left a pin
    unmatched, and how those runs ended."""
    runs = []
    for f in ("hypre/predict.csv", "hypre/predict-2.11.0.csv", "npbmg/predict-2.4.1.csv", "npbmg/predict-3.3.1.csv",
              "amg/replay_sample.csv", "nekbone/replay_sample.csv"):
        for r in rows(os.path.join(RES, f)):
            if r.get("pin"):
                runs += [(r.get("replay"), r.get("matched")), (r.get("control"), r.get("control_matched"))]
    runs = [(res, m) for res, m in runs if res]
    unmatched = [res for res, m in runs if m != "True"]
    ended = [res for res in unmatched if res in ("TIMEOUT", "FINISHED")]
    num("nPinRuns", len(runs))
    num("nPinUnmatched", len(unmatched))
    num("nPinUnmatchedEnded", len(ended))
    other = collections.Counter(res for res in unmatched if res not in ("TIMEOUT", "FINISHED"))
    return (f"\nPin logs of the application replays (HYPRE, MG, AMG and Nekbone samples), replays and controls: {len(runs)} pinned "
            f"runs, {len(unmatched)} with a pin unmatched, of which {len(ended)} hung or finished"
            + (" (the others: " + ", ".join(f"{n} {res}" for res, n in sorted(other.items())) + ")" if other else "") + ".\n")


# the corpora whose stress-testing rows the paper's effectiveness table shows; the body has no
# room for MPI-BugBench's, which are in docs/results.md and the text's macros
RQ2_STRESS = ("MBI",)


def write_paper_numbers():
    """experiments/generated/numbers.tex: one macro per number of this run (macros not computed here
    keep their provisional values from STATIC); experiments/generated/rq2.tex: the effectiveness table body."""
    gen = os.path.join(ROOT, "experiments", "generated")
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
              ("stress", "random delays, up to \\n{key}StressRuns runs"),
              ("stresseager", "random delays, eager limit forced down"))
    for key, title in (("MBI", "MBI"), ("MBB", "\\MBB level~2")):
        if not any((key, l) in CONF for l, _ in labels):
            continue
        body.append(f"\\multicolumn{{6}}{{@{{}}l}}{{\\emph{{{title}, \\n{key}progs programs}}}} \\\\")
        for l, text in labels:
            c = CONF.get((key, l))
            if l == "guard" and c == CONF.get((key, "union")):    # the guard changed no verdict: no row
                continue
            if l == "stresseager" and (key, "stress") not in CONF:  # shown next to the default limits only
                continue
            if l.startswith("stress") and key not in RQ2_STRESS:
                continue
            if c:
                label = text.replace("{key}", key)
                if l.startswith("stress"):      # the baseline runs a subset of the corpus (stress_baseline.py --select)
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
           "whole matching, beyond the horizon too, which the single-change checker does not claim. An execution in which",
           "a ready send inside the cut, or one issued before a compared pair can match, is not taken by a receive",
           "posted before its issue is erroneous and counts for none of these.", "",
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
    EFOUR.clear()
    rs = rows(os.path.join(RES, "e4", "e4.csv"))
    if not rs:
        return "_no results_\n"
    # rows whose plain or traced run did not finish, or whose analysis failed, time nothing
    # (tables written before e4_overhead.sh recorded how its runs ended have no such columns)
    failed = [r for r in rs if r.get("plain_result", "FINISHED") != "FINISHED" or r.get("traced_result", "FINISHED") != "FINISHED"
              or r.get("analysis", "ok") != "ok"]
    rs = [r for r in rs if r not in failed]
    if not rs:
        return f"_no row whose runs and analysis succeeded ({len(failed)} failed)_\n"
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
           f"Rows by code commit: {commits(rs)}." + (f" Left out, a run or its analysis failed: {len(failed)} ("
                                                      + ", ".join(f"{r['benchmark']}.{r['class']}.{r['np']}" for r in failed) + ")."
                                                      if failed else ""), "",
           "| benchmark | class | np | plain (s) | traced (s) | slowdown | NPB plain (s) | NPB traced (s) | NPB slowdown (range) | trace (MB) | events | wildcards | analysis (s) | findings |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    npb_sd, wall_sd = [], []
    for (b, c, n), g in groups.items():
        pl, tr, an = med(g, "plain_s"), med(g, "traced_s"), med(g, "analysis_s")
        pn, tn = med(g, "plain_npb_s"), med(g, "traced_npb_s")
        sd = tr / pl if pl else None
        nsd = med(g, "npb_slowdown")
        wall_sd.append(sd)
        timed = nsd is not None and pn is not None and pn >= 1.0     # a configuration of the range in the text
        if timed:
            npb_sd.append(nsd)
        EFOUR.append({"config": f"{b}.{c}.{n}", "plain": pn, "traced": tn, "slowdown": nsd, "range": rng(g, "npb_slowdown"),
                      "timed": timed})
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


VALUE_CHECKS = {"MessageRace_Recv_Send_nok", "MessageRace_Recv_Isend_nok", "MessageRace_Irecv_Send_nok", "MessageRace_Irecv_Isend_nok"}


def reads_section():
    """E3b: the MBI run analysed again with read bounds (docs/firstread.md): the first-read
    maps, the levels that change, every verdict with and without the bounds, E6 with read
    bounds, and the replays of the findings the bounds promote."""
    base = score.compiled(rows(os.path.join(RES, "mbi", "mbi.csv")))
    rd = score.compiled(rows(os.path.join(RES, "reads", "mbi.csv")))
    if not rd:
        return "_no results_\n"
    by = {r["name"]: r for r in base}
    maps = sorted(glob.glob(os.path.join(RES, "reads", "mbi", "*.json")))
    entries = free = 0
    why = collections.Counter()
    for p in maps:
        for e in json.load(open(p))["results"]:
            entries += 1
            free += bool(e["free"])
            if "why" in e:
                why[e["why"]] += 1
    with_map = [r for r in rd if r.get("reads") == "map"]
    num("nReadsMaps", len(maps))
    num("nReadsProgs", len(with_map))
    num("nReadsEntries", entries)
    num("nReadsEntriesFree", free)
    out = [f"The corpus run of E1 analysed again from its traces, each program with the first-read map of its",
           f"source (`experiments/mbi/reads_maps.py`, clang 18 in the image of `experiments/container/Dockerfile.firstread`;",
           f"`reanalyze.py --reads`). {len(maps)} maps, {len(with_map)} of the {len(rd)} programs analysed with one; the maps",
           f"have {entries} entries (a call that delivers a receive's result, with its posting call), {free} with free sites"
           + (", the others empty because " + "; ".join(f"{k} ({v})" for k, v in why.most_common()) if why else ", none empty")
           + f". Rows by code commit: {commits(rd)}.", ""]
    # verdicts with and without the bounds
    out += ["| verdict | without read bounds: TP / FP / FN / TN | F1 | with read bounds: TP / FP / FN / TN | F1 |", "|---|---|---|---|---|"]
    for label, key, judge in (("union", "Union", score.verdict), ("observed in the run", "Observed", score.observed_verdict),
                              ("predicted", "Predicted", score.predicted_verdict),
                              ("certified at L1 only (no single-path assumption)", "LOne", score.l1_verdict)):
        a, b = score.confusion(base, judge), score.confusion(rd, judge)
        out.append(f"| {label} | {a['tp']} / {a['fp']} / {a['fn']} / {a['tn']} | {a['f1']:.2f} | {b['tp']} / {b['fp']} / {b['fn']} / {b['tn']} | {b['f1']:.2f} |")
        num(f"nReadsFone{key}Base", a["f1"])
        num(f"nReadsFone{key}", b["f1"])
        num(f"nReadsTp{key}Base", a["tp"])
        num(f"nReadsTp{key}", b["tp"])
    # programs whose error consequences now hold at L1
    moved, kept_sp = [], []
    for r in rd:
        b = by.get(r["name"])
        if b is None:
            continue
        before, after = score.levels(b), score.levels(r)
        gain = sorted((after & score.L1_ONLY) - (before & score.L1_ONLY))
        if gain:
            moved.append((r, gain, sorted(after & {"L1+SP:1a", "L1+SP:1c", "L1+SP:1d"})))
        elif after & {"L1+SP:1a", "L1+SP:1c", "L1+SP:1d"}:
            kept_sp.append(r)
    new_fp = [r for r in rd if r["expect"] == "OK" and score.verdict(r) == "ERROR" and score.verdict(by.get(r["name"], {})) != "ERROR"]
    union_changed = [r for r in rd if score.verdict(r) != score.verdict(by.get(r["name"], {}))]
    value = [r for r in rd if r["name"] in VALUE_CHECKS]
    num("nReadsMoved", len(moved))
    num("nReadsMovedClean", sum(1 for r, _, _ in moved if score.finished(r)))
    num("nReadsMovedErr", sum(1 for r, _, _ in moved if r["expect"] == "ERROR"))
    num("nReadsMovedC", sum(1 for _, g, _ in moved if "L1:1c" in g))
    num("nReadsMovedD", sum(1 for _, g, _ in moved if "L1:1d" in g))
    num("nReadsMovedAll", sum(1 for _, _, sp in moved if not sp))
    num("nReadsKeptSP", len(kept_sp))
    num("nReadsNewFP", len(new_fp))
    num("nReadsUnionChanged", len(union_changed))
    num("nReadsValue", len(value))
    num("nReadsValueMoved", sum(1 for r in value if score.levels(r) & score.L1_ONLY))
    out += ["", f"Programs with an error consequence certified at L1 with the bounds and not without: **{len(moved)}** "
            f"({sum(1 for r, _, _ in moved if score.finished(r))} of them with a clean observed run), every one labelled erroneous: "
            f"{sum(1 for r, _, _ in moved if r['expect'] == 'ERROR')}. Programs that keep a single-path consequence: {len(kept_sp)}. "
            f"New false positives: {len(new_fp)}; programs whose union verdict changes: {len(union_changed)}. Of the {len(value)} "
            f"programs whose error is a check of the received value, which they read before `MPI_Finalize`, "
            f"{sum(1 for r in value if not score.levels(r) & score.L1_ONLY)} keep no L1 consequence (E7 decides them at L2).", "",
            "| program | observed run | levels without read bounds | levels with read bounds |", "|---|---|---|---|"]
    for r, _, _ in sorted(moved, key=lambda x: x[0]["name"]):
        out.append(f"| {r['name']} | {r['run'].split(':')[0]} | {by[r['name']].get('levels', '')} | {r.get('levels', '')} |")
    hp = os.path.join(RES, "reads", "hypre-2.10.1-parasails.json")
    if os.path.exists(hp):
        es = json.load(open(hp))["results"]
        num("nReadsHypreEntries", len(es))
        num("nReadsHypreFree", sum(1 for e in es if e["free"]))
        out += ["", f"HYPRE 2.10.1 (`distributed_ls/ParaSails/ParaSails.c` with the MPI wrappers of `utilities/mpistubs.c`, MPICH "
                f"headers): {len(es)} entries, {sum(1 for e in es if e['free'])} with free sites. ParaSails calls MPI through the "
                "wrappers, which hand the status and the buffer to MPI from their parameters, so every bound there is rule H1 and "
                "the analysis of a HYPRE trace does not change."]
    # E6 with read bounds
    e6 = [(kind, rows(os.path.join(RES, f"e6_reads_{kind}.csv"))) for kind in ("never", "random")]
    e6 = [(k, rs) for k, rs in e6 if rs]
    if e6:
        out += ["", "E6 with read bounds (`e6_checker_vs_explorer.py --reads never|random`): the checker judges with rule H1',",
                "the rest of the changed rank kept up to a bound (never read, or a random bound per receive), against the",
                "exhaustive explorer of the straight-line program, which reads nothing, so every bound is sound for it.", "",
                "| bounds | traces | pairs (alternative, B) | with a window after the receive | agree | unsound | incomplete | skipped | contained deadlocks confirmed | not confirmed |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        tot = collections.Counter()
        for kind, rs in e6:
            c = collections.Counter(r["kind"] for r in rs)
            t7 = collections.Counter(r.get("l1_deadlock", "") for r in rs)
            win = sum(1 for r in rs if r.get("window") == "True")
            out.append(f"| {kind} | {len({r['trace'] for r in rs})} | {len(rs)} | {win} | {c['agree']} | {c['UNSOUND']} | {c['incomplete']} | {c['skip']} | {t7['ok']} | {t7['UNSOUND']} |")
            tot.update({"pairs": len(rs), "win": win, "unsound": c["UNSOUND"], "ok": t7["ok"], "bad": t7["UNSOUND"]})
        num("nReadsEsixPairs", tot["pairs"])
        num("nReadsEsixWindows", tot["win"])
        num("nReadsEsixUnsound", tot["unsound"])
        num("nReadsEsixLdead", tot["ok"])
        num("nReadsEsixLdeadUnsound", tot["bad"])
        out += ["", f"Rows by code commit: {commits([r for _, rs in e6 for r in rs])}."]
    # replays of the findings the bounds promote
    rp = rows(os.path.join(RES, "reads", "replays.csv"))
    if rp:
        old = {(r["program"], r["cls"], r["pin"]): r["verdict"] for r in score.replay_rows(os.path.join(RES, "mbi", "mbi.csv"))}
        l1 = [r for r in rp if r["level"] == "L1" and r["cls"] in ("1c", "1d") and r["verdict"] != "n/a"]
        c = collections.Counter(r["verdict"] for r in l1)
        before = collections.Counter(old.get((r["program"], r["cls"], r["pin"]), "not replayed") for r in l1)
        num("nReadsReplayFindings", len(l1))
        num("nReadsReplayConfirmed", c["confirmed"])
        num("nReadsReplayInconclusive", c["inconclusive"])
        num("nReadsReplayNotConfirmed", c["not-confirmed"])
        num("nReadsReplaySamePinConfirmed", before["confirmed"])
        out += ["", f"Replays of the L1 consequences (1c, 1d) of the {len({r['program'] for r in l1})} programs whose levels change, each "
                "with the pins of its witness (`run_mbi.py --replay-only --reads`, the rule of E1): "
                f"{c['confirmed']} confirmed, {c['inconclusive']} inconclusive, {c['not-confirmed']} not confirmed, of {len(l1)}. "
                f"With the same pins in the E1 replays: {before['confirmed']} confirmed, {before['inconclusive']} inconclusive, "
                f"{before['not-confirmed']} not confirmed, {before['not replayed']} not replayed there (the E1 replay of a "
                "consequence that holds in every single-path execution pins the horizon, not a witness). Rows by code commit: "
                f"{commits(rp)}."]
    return "\n".join(out) + "\n"


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
        num("nNekChecked", judged)
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
    # the runs of the two single-copy modes are counted together only when they come from one
    # commit on one host (the header line of experiments/guard/run.sh's tables)
    origin = {f: re.search(r"; commit (\S+); host (\S+);", read(f)) for f in ("apps.txt", "apps-cma.txt") if read(f)}
    if len({m.groups() if m else None for m in origin.values()}) > 1:
        raise SystemExit("tables.py: guard/apps.txt and guard/apps-cma.txt come from different runs ("
                         + "; ".join(f"{f}: commit {m.group(1)}, host {m.group(2)}" if m else f"{f}: no header"
                                     for f, m in origin.items())
                         + "); run experiments/guard/run.sh apps again in both modes")
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


HB_CORPORA = (("mbi", "MBI", "MBI"), ("mbb", "MPI-BugBench level 2", "MBB"), ("idioms", "idioms", "Idioms"),
              ("amg", "AMG, 8 ranks", "AMG"), ("hypre", "HYPRE 2.10.1, 4 and 8 ranks", "Hypre"),
              ("npbmg", "NPB MG 2.4.1, class S, 16 and 32 ranks", "MG"))


def e8_hb():
    """Happens-before selection of alternatives against the check (experiments/e8_hb.py:
    e8_hb_counts.csv, the pairs per program, kind and verdicts; e8_hb.csv, one row per
    disagreement with its replay; e8_hb_traces.csv, one row per trace)."""
    cnt = rows(os.path.join(RES, "e8_hb_counts.csv"))
    rs = rows(os.path.join(RES, "e8_hb.csv"))
    ts = rows(os.path.join(RES, "e8_hb_traces.csv"))
    if not cnt:
        return "_no results_\n"
    yes = lambda r, k: r.get(k) == "True"   # noqa: E731

    def cells(sel):
        """Counts of e8_hb_counts.csv rows (weighted by their pairs) or of single pairs."""
        n = lambda cond: sum(int(r.get("pairs") or 1) for r in sel if cond(r))   # noqa: E731
        return {"pairs": n(lambda r: True),
                "post_both": n(lambda r: yes(r, "hb_post") and yes(r, "stretto")),
                "post_only": n(lambda r: yes(r, "hb_post") and not yes(r, "stretto")),
                "post_missed": n(lambda r: yes(r, "stretto") and not yes(r, "hb_post")),
                "comp_both": n(lambda r: yes(r, "hb_comp") and yes(r, "stretto")),
                "comp_only": n(lambda r: yes(r, "hb_comp") and not yes(r, "stretto")),
                "comp_missed": n(lambda r: yes(r, "stretto") and not yes(r, "hb_comp")),
                "neither": n(lambda r: not yes(r, "hb_comp") and not yes(r, "stretto"))}

    def line(label, c, extra=""):
        return (f"| {label} | {c['pairs']} | {c['post_both']} | {c['post_only']} | {c['post_missed']} | {c['comp_both']} | "
                f"{c['comp_only']} | {c['comp_missed']} | {c['neither']} |{extra}")

    real = [r for r in cnt if r["corpus"] != "synthetic"]           # counts
    syn = [r for r in cnt if r["corpus"] == "synthetic"]
    dis = [r for r in rs if r["corpus"] != "synthetic"]             # one row per disagreement
    syn_dis = [r for r in rs if r["corpus"] == "synthetic"]
    real_ts = [t for t in ts if t["corpus"] != "synthetic"]
    ok_ts = [t for t in real_ts if t["status"] == "ok"]
    out = ["Every pair (wildcard receive or probe, send) that a happens-before rule selects or the analysis enumerates,",
           "judged three ways (`experiments/e8_hb.py`, definitions in `analysis/stretto/hb.py`): rule (a) selects a",
           "send that is the non-overtaking head of its source and not causally after the receive's *posting*",
           "(DAMPI takes a later clock, at the wait or at a later receive matched after it); rule (b) the same at the",
           "receive's *completion*; the check is the report's verdict",
           "(feasible at some threshold at which the observed run is realisable). Happens-before is the run's causal",
           "order (program order, message to the completion that returned it, probes, collectives by shape), with",
           "exact vector clocks. The rules coincide at blocking receives and probes; they differ only at a",
           "nonblocking receive whose rank sends or enters a collective while it is pending. *Only (a)*: the rule",
           "selects the pair and the check rejects it; *check only*: the check accepts it and the rule does not.",
           f"Rows by code commit: {commits(cnt + rs)}.", ""]
    out += ["| corpus | traces | records, max | wildcard receives | blocking | nonblocking, nothing sent while pending | "
            "nonblocking, pending across a send | nonblocking, never completed | wildcard probes | not analysed |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    isum = lambda sel, k: sum(int(t.get(k) or 0) for t in sel)   # noqa: E731
    for c, label, _ in HB_CORPORA:
        sel = [t for t in real_ts if t["corpus"] == c]
        if not sel:
            continue
        good = [t for t in sel if t["status"] == "ok"]
        bad = collections.Counter(t["status"].split(":")[0] for t in sel if t["status"] != "ok")
        out.append(f"| {label} | {len(good)} | {max((int(t['records']) for t in good if t.get('records')), default=0)} | "
                   f"{isum(good, 'wildcard_recvs')} | {isum(good, 'blocking')} | {isum(good, 'nonblocking_nothing_sent_while_pending')} | "
                   f"{isum(good, 'nonblocking_pending_across_a_send')} | {isum(good, 'nonblocking_never_completed')} | "
                   f"{isum(good, 'wildcard_probes')} | {', '.join(f'{k} {v}' for k, v in sorted(bad.items())) or '0'} |")
    head = ["| | pairs | (a) and check | only (a) | check only, not (a) | (b) and check | only (b) | check only, not (b) | neither |",
            "|---|---|---|---|---|---|---|---|---|"]
    out += ["", "Pairs by corpus:", ""] + head
    for c, label, k in HB_CORPORA:
        sel = [r for r in real if r["corpus"] == c]
        if not sel and not any(t["corpus"] == c for t in ok_ts):
            continue
        cc = cells(sel)
        out.append(line(label, cc))
        num(f"nHB{k}Pairs", cc["pairs"])
        num(f"nHB{k}PostOnly", cc["post_only"])
        num(f"nHB{k}PostMissed", cc["post_missed"])
        num(f"nHB{k}CompOnly", cc["comp_only"])
        num(f"nHB{k}CompMissed", cc["comp_missed"])
    tot = cells(real)
    out.append(line("**all real traces**", tot))
    for name, v in tot.items():
        num("nHB" + "".join(w.capitalize() for w in name.split("_")), v)
    num("nHBTraces", len(ok_ts))
    num("nHBRecvPairs", cells([r for r in real if r["op"] == "recv"])["pairs"])
    num("nHBProbePairs", cells([r for r in real if r["op"] == "probe"])["pairs"])
    num("nHBPendingRecvs", isum(ok_ts, "nonblocking_pending_across_a_send"))
    out += ["", "Pairs by kind of receive, all real traces (a probe is one call: (a) and (b) coincide):", ""] + head
    for kind in ("blocking", "nonblocking, nothing sent while pending", "nonblocking, pending across a send",
                 "nonblocking, never completed"):
        sel = [r for r in real if r["op"] == "recv" and r["kind"] == kind]
        if sel:
            out.append(line(kind, cells(sel)))
    sel = [r for r in real if r["op"] == "probe"]
    if sel:
        out.append(line("probes", cells(sel)))
    by_prog = collections.OrderedDict()
    for r in real:
        by_prog.setdefault((r["corpus"], r["program"]), []).append(r)
    split = [(k, cells(v)) for k, v in by_prog.items() if any(x["class"] for x in v)]
    out += ["", f"Programs with a disagreement ({len(split)} of {len(ok_ts)}; every other program agrees under both rules):", ""]
    out += [h.replace("| |", "| program |", 1) if i == 0 else h for i, h in enumerate(head)]
    out += [line(f"{c}/{p}", cc) for (c, p), cc in split]
    num("nHBProgramsSplit", len(split))
    missed = collections.Counter(r["hb_miss"] for r in dis if yes(r, "stretto") and not yes(r, "hb_post"))
    rejected = collections.Counter(r["stretto_reason"].split(":")[0] for r in dis if yes(r, "hb_comp") and not yes(r, "stretto"))
    out += ["", "Why rule (a) misses the pairs only the check accepts: " +
            (", ".join(f"{k} {v}" for k, v in missed.most_common()) or "none") + ". Why the check rejects the pairs rule (b) "
            "selects: " + (", ".join(f"{k} {v}" for k, v in rejected.most_common()) or "none") + "."]
    gen = [r for r in real if r.get("stretto_generic") in ("True", "False")]
    differ = [r for r in gen if r["stretto_generic"] != r["stretto"]]
    num("nHBGenericPairs", cells(gen)["pairs"])
    num("nHBGenericDiffer", cells(differ)["pairs"])
    out += [f"The generic check judged as well the {cells(gen)['pairs']} pairs of the {sum(1 for t in ok_ts if t.get('generic') == 'yes')} "
            f"traces of up to 20,000 records and differs from the fast checks on {cells(differ)['pairs']}"
            + (" (" + ", ".join(sorted({f"{r['corpus']}/{r['program']}" for r in differ})) + ")" if differ else "") + "."]
    # replays of the disagreements
    rp = [r for r in dis if r.get("arbiter") == "replay"]
    if rp:
        ev = collections.Counter((r["class"], r["evidence"]) for r in rp)
        out += ["", "Replays of the disagreements (up to 30 per program and class, chosen at random with a fixed seed), each",
                "pinned over its horizon with a control pinned to the observed senders, both recorded by the tracer. *Realised*:",
                "the changed receive's pin and the pins of the receives completed in the horizon matched, the run finished, and",
                "its own trace is a run of the program without pins (*run did not finish*: the changed pin matched in a run of",
                "the program that then hung or failed); *pins only*: it matched, but only because the pin rewrote the wildcard (a",
                "later receive took a message the pending original accepts); *not realised*: the pin never matched while the",
                "control's pins all matched in a finished run of the program; *control failed*: the pin never matched and the",
                "control shows nothing (it did not finish, did not match, or is itself no run of the program).", "",
                "| corpus | class | disagreements | replayed | " + " | ".join(EVIDENCE) + " | controls finished, pins matched |",
                "|---|---|---|---|" + "---|" * (len(EVIDENCE) + 1)]
        for c, label, _ in HB_CORPORA:
            for cls in ("hb-only", "stretto-only"):
                sel = [r for r in rp if r["class"] == cls and r["corpus"] == c]
                n = sum(1 for r in dis if r["class"] == cls and r["corpus"] == c)
                if not n:
                    continue
                e = collections.Counter(r["evidence"] for r in sel)
                out.append(f"| {label} | {'only (b), check rejects' if cls == 'hb-only' else 'check only, not (a)'} | {n} | {len(sel)} | "
                           + " | ".join(str(e[x]) for x in EVIDENCE)
                           + f" | {sum(1 for r in sel if r['control'] == 'FINISHED' and r['control_matched'] == 'True')} |")
        other = sorted({e for _, e in ev} - set(EVIDENCE))
        if other:
            out += ["", "Other replay outcomes: " + ", ".join(f"{c} {e} {ev[(c, e)]}" for c, e in sorted(ev) if e in other) + "."]
        num("nHBReplayed", len(rp))
        for cls, k in (("hb-only", "HBOnly"), ("stretto-only", "StrettoOnly")):
            sel = [r for r in rp if r["class"] == cls]
            num(f"nHBReplay{k}", len(sel))
            num(f"nHBReplay{k}Realised", sum(1 for r in sel if r["evidence"].startswith("realised")))
            num(f"nHBReplay{k}NotRealised", sum(1 for r in sel if r["evidence"] == "not realised"))
            num(f"nHBReplay{k}PinsOnly", sum(1 for r in sel if r["evidence"] == "pins only"))
            num(f"nHBReplay{k}ControlFailed", sum(1 for r in sel if r["evidence"] == "control failed"))
        ex = []
        for (cls, e), _ in sorted(ev.items()):
            for r in [r for r in rp if r["class"] == cls and r["evidence"] == e][:2]:
                why = r["stretto_reason"] if cls == "hb-only" else r["hb_miss"]
                ex.append(f"| {cls} | {e} | {r['corpus']}/{r['program']} | `{r['desc']}` | {r['kind']} | {why} | "
                          f"{r['replay']} / {r['control']} |")
        out += ["", "Examples, two per class and outcome:", "",
                "| class | outcome | program | pair | receive | why the other side disagrees | replay / control |",
                "|---|---|---|---|---|---|---|"] + ex
    if syn:
        c = cells(syn)
        decided = collections.Counter((r["class"], r["evidence"]) for r in syn_dis)
        progs = len({r["program"] for r in syn} | {t["program"] for t in ts if t["corpus"] == "synthetic"})
        out += ["", f"Synthetic traces: {progs} random programs from E6's generator, all with sends, receives and waits",
                "interleaved, each traced from an execution the explorer chose; with no program to rerun, the explorer over",
                "the trace as a straight-line program decides each disagreement with E6's criteria. *Realised*: some execution",
                "agrees with the single-change alternative on every receive its horizon judges, matched alike or unmatched",
                "alike; *realised, judged pairs only*: some execution has every pair the alternative judges, while a receive it",
                "leaves unmatched takes a message (what a replay pinned over the horizon tests); *realised with other matches",
                "changed*: executions match the receive to the send, but only with another receive matched differently,",
                "which no single-change alternative states; *not realised*: no execution matches them. An execution in which a",
                "ready send inside the horizon, or one issued before a compared pair can match, is not taken by a receive",
                "posted before its issue, which is erroneous and which the check excludes, counts only when no other does, and",
                "the verdict says so.", ""] + head + [line("synthetic", c), "",
                "Explorer verdicts: " + (", ".join(f"{cls} {e} {n}" for (cls, e), n in sorted(decided.items())) or "none") + "."]
        ex = [r for r in syn_dis if r["class"] == "hb-only"][:3] + [r for r in syn_dis if r["class"] == "stretto-only"][:2]
        if ex:
            out += ["", "| program | class | explorer | pair | receive | why the other side disagrees |", "|---|---|---|---|---|---|"]
            out += [f"| {r['program']} | {r['class']} | {r['evidence']} | `{r['desc']}` | {r['kind']} | "
                    f"{r['stretto_reason'] if r['class'] == 'hb-only' else r['hb_miss']} |" for r in ex]
        num("nHBSynPrograms", progs)
        num("nHBSynPairs", c["pairs"])
        num("nHBSynCompOnly", c["comp_only"])
        num("nHBSynPostMissed", c["post_missed"])
        num("nHBSynCompOnlyNotRealised", decided[("hb-only", "not realised")])
        num("nHBSynCompOnlyRealised", decided[("hb-only", "realised")])
        num("nHBSynCompOnlyOther", decided[("hb-only", "realised with other matches changed")])
        num("nHBSynCompOnlyPairs", decided[("hb-only", "realised, judged pairs only")])
        num("nHBSynCompOnlyReady", sum(n for (cls, e), n in decided.items() if cls == "hb-only" and e.endswith("ready send before its receive")))
        num("nHBSynPostMissedPairs", decided[("stretto-only", "realised, judged pairs only")])
        num("nHBSynPostMissedOther", decided[("stretto-only", "realised with other matches changed")])
        num("nHBSynPostMissedRealised", decided[("stretto-only", "realised")])
        num("nHBSynPostMissedNotRealised", decided[("stretto-only", "not realised")])
    return "\n".join(out) + "\n"


EVIDENCE = ("realised", "realised, run did not finish", "realised, other pins unmatched", "realised, truncation error",
            "pins only", "not realised", "control failed")

# the campaigns whose replays were run again under the tracer (experiments/results/valid/), with
# what a confirmation is in each: the claim its replays make
VALID = (("mbi", "MBI (E1), every replay", "MBI"),
         ("reads", "MBI with read bounds (E3b), every replay", "Reads"),
         ("npbmg", "NPB MG 2.4.1 (E5d), a sample", "MG"),
         ("dealii", "deal.II 9.3.0-rc1 (E5e), cross alternatives, a sample", "Deal"),
         ("hypre", "HYPRE 2.10.1 (E5c), cross alternatives, a sample", "Hypre"),
         ("amg", "AMG (E5), the sample of E5", "AMG"),
         ("nekbone", "Nekbone (E5b), the sample of E5b", "Nek"))


def _valid_confirmed(name, r) -> bool:
    """Is the replay of row r a confirmation under the rule of its campaign (every required pin
    matched, the predicted outcome reached, the control finished)? The predicted outcome is the
    finding's in MBI (the verdict column), a hang for HYPRE's cross alternatives, another
    residual than the control's for MG, other values than the expected ones for deal.II, and a
    run that finishes for the samples of AMG and Nekbone, whose alternatives are warnings."""
    yes = lambda k: r.get(k) == "True"   # noqa: E731
    if name in ("mbi", "reads"):
        return r.get("verdict") == "confirmed"
    ok = yes("matched") and r.get("control") == "FINISHED"
    if name == "hypre":
        return ok and r.get("replay") == "TIMEOUT"
    if name == "npbmg":
        return ok and r.get("replay") == "FINISHED" and bool(r.get("norm")) and r["norm"] != r.get("control_norm")
    if name == "dealii":
        return ok and r.get("replay") == "FINISHED" and yes("differs")
    return ok and r.get("replay") == "FINISHED" and yes("control_matched")


def _why_kind(v: str) -> str:
    """The kind of violation a replay's trace shows (stretto.confirm.unpinned_violation)."""
    for prefix, kind in (("end of the run", "the end: a waiting operation takes, unpinned, a message nobody took"),
                         ("receiver order", "receiver-side order: an earlier receive takes the message"),
                         ("non-overtaking", "sender-side order (non-overtaking)"), ("probe order", "a probe's order"),
                         ("constraint cycle", "a constraint cycle"), ("trace unreadable", "trace unreadable"),
                         ("undetermined", "undetermined")):
        if v.startswith(prefix):
            return kind
    return v.split(":")[0]


def valid():
    """The replays behind the confirmation claims run again with the tracer preloaded, which
    applies the pins and records each run; its own trace is then judged as a run of the program
    without the pins (stretto.confirm.unpinned_violation, judge_replay). experiments/results/valid/:
    mbi.csv and reads.csv (run_mbi.py --replay-only --replays-out), npbmg.csv, dealii.csv,
    hypre.csv (the campaigns' predict.py --sample), amg.csv and nekbone.csv (replay_sample.py
    --from-csv)."""
    d = os.path.join(RES, "valid")
    got = [(name, label, key, rows(os.path.join(d, f"{name}.csv"))) for name, label, key in VALID]
    got = [g for g in got if g[3]]
    if not got:
        return "_no results_\n"
    out = ["The replays behind every confirmation claim are run again with the tracer preloaded: it applies the pins as",
           "the replay library does and records the run with the original envelopes, and the replay's own trace is judged",
           "as a run of the program without the pins (`stretto.confirm.unpinned_violation`): MPI's matching order on its",
           "matches (non-overtaking on both sides, probes) and an acyclic constraint graph. A receive still pending where",
           "the run stopped is read as unmatched or as matched to a message nobody else took, and the run passes if some",
           "reading does. A replay that hangs is judged up to where it stopped and at its end: a receive or probe still",
           "waiting there that would, without its pin, take a message nobody took is no state the program stays in. The",
           "controls are judged the same way. A confirmation under the *old rule* needs every required pin matched, the",
           "predicted outcome and a control that finished; the *new rule* also needs the replay to be a run of the",
           "program. The MBI campaigns replay every finding again (rows, one per finding and class); the application",
           "campaigns replay a random sample of alternatives of new traced runs (the earlier traces were not kept),",
           "with the seed in the table, and AMG and Nekbone the same alternatives of the same trace as their samples.",
           "A required pin counts as matched when the replay library reports it or, since 13f2174, when the replay's own",
           "trace shows the pinned receive or probe took its pinned source (`stretto.confirm.trace_matches`).",
           f"Rows by code commit: {commits([r for g in got for r in g[3]])}.", "",
           "| campaign | alternatives to draw from | replays | runs of the program | not runs | undetermined | controls that are runs | confirmed, old rule | confirmed, new rule |",
           "|---|---|---|---|---|---|---|---|---|"]
    tot = collections.Counter()
    bad = []
    for name, label, key, rs in got:
        if name in ("mbi", "reads"):
            rep = rs
            # the same findings and pins as the replays the claims were made from?
            before = collections.Counter((r["program"], r["cls"], r["pin"]) for r in rows(os.path.join(RES, name, "replays.csv")))
            now = collections.Counter((r["program"], r["cls"], r["pin"]) for r in rs)
            same = sum((before & now).values())
            pool = f"{len(rs)} findings, {same} of the {sum(before.values())} replayed for the claims"
            num(f"nValid{key}SamePins", same)
            num(f"nValid{key}Committed", sum(before.values()))
            num(f"nValid{key}CommittedConfirmed", sum(1 for r in rows(os.path.join(RES, name, "replays.csv")) if r.get("verdict") == "confirmed"))
        else:
            rep = [r for r in rs if r.get("pin") and r.get("replay") not in (None, "", "no horizon")]
            if name in ("amg", "nekbone"):
                pool = f"the sample of {len(rep)}"
            else:
                per_run = {(r.get("np"), r.get("run")): r for r in rs if not (r.get("replay") or "").startswith("traced run")}
                n = sum(int(r.get("alternatives") or 0) for r in per_run.values())
                seeds = sorted({r.get("seed") for r in rs if r.get("seed")})
                pool = f"{n} in {len(per_run)} traced runs" + (f", seed {', '.join(seeds)}" if seeds else "")
                num(f"nValid{key}Pool", n)
                num(f"nValid{key}Traced", len(per_run))
        runs = sum(1 for r in rep if r.get("run_valid") == "True")
        notr = [r for r in rep if r.get("run_valid") == "False"]
        und = sum(1 for r in rep if r.get("run_valid") not in ("True", "False"))
        ctl = [r for r in rep if r.get("control") not in (None, "")]
        ctl_runs = sum(1 for r in ctl if r.get("control_valid") == "True")
        old = [r for r in rep if _valid_confirmed(name, r)]
        new = [r for r in old if r.get("run_valid") == "True"]
        out.append(f"| {label} | {pool} | {len(rep)} | {runs} | {len(notr)} | {und} | {ctl_runs} of {len(ctl)} | {len(old)} | {len(new)} |")
        hung = [r for r in rep if (r.get("result") or r.get("replay")) == "TIMEOUT"]
        for m, v in (("Replays", len(rep)), ("Runs", runs), ("NotRuns", len(notr)), ("Undetermined", und), ("Controls", len(ctl)),
                     ("ControlRuns", ctl_runs), ("ConfOld", len(old)), ("ConfNew", len(new)), ("Hung", len(hung)),
                     ("HungRuns", sum(1 for r in hung if r.get("run_valid") == "True"))):
            num(f"nValid{key}{m}", v)
            tot[m] += v
        bad += [(label, r) for r in notr]
        bad += [(label + ", control", {**r, "violation": r.get("control_violation", ""), "result": r.get("control"),
                                       "replay": r.get("control")}) for r in ctl if r.get("control_valid") == "False"]
    for m, v in tot.items():
        num(f"nValid{m}", v)
    out.append(f"| **all** | | {tot['Replays']} | {tot['Runs']} | {tot['NotRuns']} | {tot['Undetermined']} | "
               f"{tot['ControlRuns']} of {tot['Controls']} | {tot['ConfOld']} | {tot['ConfNew']} |")
    # MBI: the same findings and pins as the committed replays, whose verdicts the run may change
    # (a control that hangs this time, a replay that crashes)
    diff = []
    for name, label, key, rs in got:
        if name not in ("mbi", "reads"):
            continue
        before = {(r["program"], r["cls"], r["pin"]): r for r in rows(os.path.join(RES, name, "replays.csv"))}
        for r in rs:
            b = before.get((r["program"], r["cls"], r["pin"]))
            if b and b.get("verdict") != r.get("verdict"):
                diff.append(f"| {label} | {r['program']} | {r['cls']} | {r['pin']} | {b.get('verdict')} ({b.get('result')}, control "
                            f"{b.get('control') or '-'}) | {r.get('verdict')} ({r.get('result')}, control {r.get('control') or '-'}) |")
    num("nValidVerdictChanged", len(diff))
    if diff:
        out += ["", "Replays of MBI whose verdict under the old rule differs from that of the committed replay with the same finding "
                    "and pins (the outcome of one run of a racing program is one schedule):", "",
                "| campaign | program | class | pin | committed | this run |", "|---|---|---|---|---|---|"] + diff
    out += ["", f"Replays that hung, the predicted outcome of 1c, 1d and HYPRE's cross alternatives, and were judged at their end "
                f"as well: {tot['Hung']}, of which {tot['HungRuns']} are runs of the program."]
    if not bad and not tot["Undetermined"]:
        out += ["", "Every replay and every control is a run of the program without its pins: no pin forced a match or a hang "
                    "that the program cannot have, so no confirmation rests on the pins alone."]
    if bad:
        kinds = collections.Counter((label, _why_kind(r.get("violation", ""))) for label, r in bad)
        out += ["", "Replays and controls that are no run of the program, by what their trace shows:", "",
                "| campaign | violation | runs |", "|---|---|---|"]
        out += [f"| {label} | {kind} | {n} |" for (label, kind), n in sorted(kinds.items())]
        out += ["", "Each of them (the first 40):", "", "| campaign | replay | result | what its trace shows |", "|---|---|---|---|"]
        for label, r in bad[:40]:
            what = r.get("program") or r.get("finding", "")[:60] or f"np {r.get('np')} run {r.get('run')}"
            out.append(f"| {label} | {what} `{r.get('pin', '')[:80]}` | {r.get('result') or r.get('replay') or ''} | "
                       f"{r.get('violation', '').replace('|', '/')[:240]} |")
    return "\n".join(out) + "\n"


MUST_TAGS = {"must1112": "MUSTeleven", "must1112tsan": "MUSTeleventsan"}   # macro names of score.MUST_COLUMNS


def must_versions(rs, key):
    """E1 rows of the MUST columns besides 1.9.2's (score.MUST_COLUMNS): MUST 1.11.2, plain and
    with its ThreadSanitizer analysis, each bracketed as 1.9.2 is."""
    out = []
    for col, name in score.MUST_COLUMNS:
        if not any(r.get(col) for r in rs):
            continue
        c = score.confusion(rs, score.must_column_judge(col))
        cs = score.confusion(rs, score.must_column_judge(col, hangs=True))
        out.append(conf_row(f"{name} (same source, its own run under MPICH)", c))
        out.append(conf_row(f"{name}, a timeout or a run without a report counted as an error", cs))
        if key:
            tag = MUST_TAGS[col]
            for label, cc in ((tag, c), (tag + "sym", cs)):
                num(f"{key}fone{label}", cc["f1"])
                num(f"n{key}tp{label}", cc["tp"])
                num(f"n{key}fp{label}", cc["fp"])
    return out


def must_versions_notes(rs, key, path):
    """What the other MUST columns report, against MUST 1.9.2's on the same programs."""
    out = []
    for col, name in score.MUST_COLUMNS:
        if not any(r.get(col) for r in rs):
            continue
        judge = score.must_column_judge(col)
        kinds = collections.Counter(score.must_kind({"must": r.get(col) or ""}) for r in rs)
        gained = [r for r in rs if r["expect"] == "ERROR" and judge(r) == "ERROR" and score.must_verdict(r) != "ERROR"]
        lost = [r for r in rs if r["expect"] == "ERROR" and judge(r) != "ERROR" and score.must_verdict(r) == "ERROR"]
        fps = [r for r in rs if r["expect"] == "OK" and judge(r) == "ERROR"]
        races = [r for r in rs if "data race" in (r.get(col) or "")]
        by = lambda sel: ", ".join(f"{d or 'none'} {n}" for d, n in sorted(collections.Counter(r.get("detail") or "-" for r in sel).items()))
        side = rows(os.path.join(os.path.dirname(path), col + ".csv"))    # run_must.py's own table, with the commit
        out += ["", f"{name} verdicts (rows by code commit: {commits(side)}): " + ", ".join(f"{k or 'none'} {v}" for k, v in sorted(kinds.items())) + "."
                f" Labelled errors it reports and MUST 1.9.2 does not: {len(gained)}" + (f" ({by(gained)})" if gained else "")
                + f"; the reverse: {len(lost)}" + (f" ({by(lost)})" if lost else "") + f". Correct programs it reports: {len(fps)}"
                + (f" ({', '.join(r['name'] for r in fps)})" if fps else "") + "."
                + (f" Programs with a data race reported: {len(races)}" + (f" ({by(races)})" if races else "") + "." if col.endswith("tsan") else "")]
        if key:
            tag = MUST_TAGS[col]
            num(f"n{key}{tag}Gained", len(gained))
            num(f"n{key}{tag}Lost", len(lost))
            num(f"n{key}{tag}Timeout", kinds.get("TIMEOUT", 0))
            if col.endswith("tsan"):
                num(f"n{key}{tag}Races", len(races))
    if any(r.get(col) for r in rs for col, _ in score.MUST_COLUMNS):
        out += ["", "MUST 1.11.2 runs from `experiments/must/Dockerfile` (MPI-BugBench's MUST image with 1.11.2: Debian 12,",
                "MPICH 4.0.2, clang 14), the same harness and timeout as 1.9.2 (`scripts/must_run.sh`). With ThreadSanitizer the",
                "program is compiled with `must-tsan-cc --wrap-target mpicc.mpich` and run with `--must:tsan`; a data race counts",
                "as an error (MPI-BugBench's harness maps MUST_WARNING_DATARACE to no error class). As MUST's own tests do for",
                "clang 14 and later, the run sets MUST_DISABLE_TSAN_ONREPORT=1, so the races are in ThreadSanitizer's report",
                "rather than MUST's: with the hook on, the runs hung after the first race."]
    return out


def must_tsan_local():
    """MUST 1.11.2 with ThreadSanitizer against the buffer guard on MPI-BugBench's
    local-concurrency programs, both under MPICH (guard: lc.txt; MUST: the column of mbb.csv, its
    commit from run_must.py's own table, mbb/must1112tsan.csv)."""
    mbb = {r["name"]: r for r in rows(os.path.join(RES, "mbb", "mbb.csv"))}
    lc = [r for r in mbb.values() if r.get("detail") == "LocalConcurrency"]
    if not any(r.get("must1112tsan") for r in lc):
        return ""
    names = {r["name"] for r in lc}
    side = {r["name"]: r for r in rows(os.path.join(RES, "mbb", "must1112tsan.csv")) if r["name"] in names}
    p = os.path.join(RES, "guard", "lc.txt")
    text = open(p).read() if os.path.exists(p) else ""
    guard = {m.group(1): "3-local" in m.group(2) for m in re.finditer(r"^mpich\s+(\S+)\s+(.*)$", text, re.M)}
    gcommit = (re.search(r"; commit (\w+);", text) or re.search(r"(unknown)", "unknown")).group(1)
    yes = lambda v: "ERROR" if score.must_verdict({"must": v or ""}) == "ERROR" else (score.must_kind({"must": v or ""}) or "-")
    out = ["", "### MUST 1.11.2 with ThreadSanitizer on the local-concurrency programs", "",
           "MPI-BugBench's local-concurrency programs (the program touches a buffer that a pending nonblocking operation owns),",
           "under MPICH: the buffer guard (`lc.txt`) against MUST's ThreadSanitizer analysis, which since 1.10 models a pending",
           "operation's buffer accesses as a concurrent thread, and plain MUST runs. A race or a report counts.", "",
           "| program | buffer guard | MUST 1.11.2 with ThreadSanitizer | MUST 1.11.2 | MUST 1.9.2 |", "|---|---|---|---|---|"]
    both = tsan_only = guard_only = 0
    for r in sorted(lc, key=lambda r: r["name"]):
        g = guard.get(r["name"])
        t = score.must_column_judge("must1112tsan")(r) == "ERROR"
        both += bool(g and t)
        tsan_only += bool(t and not g)
        guard_only += bool(g and not t)
        out.append(f"| {r['name']} | {'3-local' if g else 'not built' if g is None else '-'} | {yes(r.get('must1112tsan'))} "
                   f"| {yes(r.get('must1112'))} | {yes(r.get('must'))} |")
    found_t = sum(1 for r in lc if score.must_column_judge("must1112tsan")(r) == "ERROR")
    found_g = sum(1 for r in lc if guard.get(r["name"]))
    out += ["", f"Programs: {len(lc)}. The guard reports {found_g}, MUST with ThreadSanitizer {found_t}; both {both}, only the guard "
            f"{guard_only}, only MUST {tsan_only}. The guard run's code commit: {gcommit}"
            + (f"; MUST's rows: {commits(side.values())}." if side else ".")]
    num("nTsanLC", len(lc))
    num("nTsanLCfound", found_t)
    num("nTsanLCguard", found_g)
    num("nTsanLCboth", both)
    num("nTsanLCguardOnly", guard_only)
    num("nTsanLCtsanOnly", tsan_only)
    return "\n".join(out) + "\n"


def ninja_runs_rows(path: str) -> list:
    """The rows of a table of NINJA runs (experiments/ninja/run.py runs), the last campaign of
    each program, process count, mode and setting only. run.py appends to its table, so a
    rerun of a campaign (artifact/reproduce.sh rq2, rq6) adds a second run 1, 2, ... after the
    first: a campaign starts again where a group's run number falls back (or repeats, but for
    run 0, the learning run, which is retried)."""
    def key(r):
        return (r["label"], r["np"], r["mode"], r.get("setting", "") if r["mode"] == "random" else "")
    rs = rows(path)
    last: dict = {}
    for i, r in enumerate(rs):
        g = last.setdefault(key(r), {"start": i, "prev": None})
        run = int(r["run"])
        if g["prev"] is not None and (run < g["prev"] or (run == g["prev"] and run > 0)):
            g["start"] = i                   # a later campaign of the group
        g["prev"] = run
    return [r for i, r in enumerate(rs) if i >= last[key(r)]["start"]]


def ninja_corpus_rows(path: str) -> list:
    """The rows of a table of NINJA corpus runs (run.py corpus): the last row of each program,
    mode and setting, run.py appending to its table as above."""
    rs = rows(path)
    last = {(r["program"], r["mode"], r.get("setting", "")): i for i, r in enumerate(rs)}
    return [r for i, r in enumerate(rs) if last[(r["program"], r["mode"], r.get("setting", ""))] == i]


def _ninja_mode(r) -> str:
    """A NINJA row's mode as text, the random noise with its ratio and delay."""
    if r["mode"] == "random":
        m = re.match(r"ratio=([\d.]+)% delay=(\d+)us( as shipped)?", r.get("setting", ""))
        if m:
            d = int(m.group(2))
            return f"random, {m.group(1)}% of sends by {d / 1e6:g} s" + (" (as shipped)" if m.group(3) else "")
    return {"plain": "none (plain runs)", "system": "system-centric", "app": "application-centric"}.get(r["mode"], r["mode"])


def _ninja_key(r, delays) -> str:
    """The macro name of a mode; with two random settings, the shorter delay is RandomShort."""
    if r["mode"] == "random":
        d = re.search(r"delay=(\d+)us", r.get("setting", ""))
        if len(delays) > 1 and d:
            return "RandomShort" if int(d.group(1)) == min(delays) else "RandomLong"
        return "Random"
    return {"plain": "Plain", "system": "System", "app": "App"}.get(r["mode"], r["mode"])


def _ninja_runs(rs, prefix, phases=False):
    """One table row per program, process count and mode of a fixed number of runs under NINJA
    (experiments/ninja/run.py runs); run 0 is application-centric mode's learning run."""
    delays = sorted({int(m.group(1)) for r in rs if r["mode"] == "random" for m in [re.search(r"delay=(\d+)us", r["setting"])] if m})
    groups = collections.OrderedDict()
    for r in rs:
        groups.setdefault((r["label"], int(r["np"]), r["mode"], r["setting"] if r["mode"] == "random" else ""), []).append(r)
    head = "| program | ranks | noise | runs | finished | hung | " + ("of which in the setup | " if phases else "") + \
           "other | " + ("first hang in the setup (run) | its rate" if phases else "first hang (run) | hang rate") + " | learning run |"
    out = [head, "|" + "---|" * (head.count("|") - 1)]
    for (label, np_, mode, _), g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], ["plain", "random", "system", "app"].index(kv[0][2]), kv[0][3])):
        learn = [r for r in g if r["run"] == "0"]
        runs = sorted((r for r in g if r["run"] != "0"), key=lambda r: int(r["run"]))
        hung = [r for r in runs if r["result"] == "TIMEOUT"]
        setup = [r for r in hung if not r.get("reached")]           # killed before the setup completed
        fin = [r for r in runs if r["result"] == "FINISHED"]
        other = collections.Counter(r["result"] for r in runs if r["result"] not in ("TIMEOUT", "FINISHED"))
        hits = setup if phases else hung                            # the hangs of the race
        first = min((int(r["run"]) for r in hits), default=None)
        rate = len(hits) / len(runs) if runs else 0.0
        out.append(f"| {label} | {np_} | {_ninja_mode(g[0])} | {len(runs)} | {len(fin)} | {len(hung)} | "
                   + (f"{len(setup)} | " if phases else "")
                   + f"{', '.join(f'{n} {k}' for k, n in sorted(other.items())) or 0} | {first or '-'} | {rate:.2f} | "
                   + (", ".join(r["result"] for r in learn) or "-") + " |")
        k = f"nNinja{prefix(label)}{'Four' if np_ == 4 else 'Eight' if np_ == 8 else np_}{_ninja_key(g[0], delays)}"
        num(k + "Runs", len(runs))
        num(k + "Hang", len(hung))
        num(k + "First", first if first else "--")
        num(k.replace("nNinja", "Ninja", 1) + "Rate", rate)
        if phases:
            num(k + "HangSetup", len(setup))
    return out


def ninja():
    """E10: NINJA's noise injection on the programs Stretto predicts bugs in
    (experiments/ninja/campaign.sh): its ParaSails replica, HYPRE, the MBI corpus, NPB MG."""
    d = os.path.join(RES, "ninja")
    cal, hy, mg = (ninja_runs_rows(os.path.join(d, f)) for f in ("calibrate.csv", "hypre.csv", "mg.csv"))
    mb = ninja_corpus_rows(os.path.join(d, "mbi.csv"))
    if not (cal or hy or mb or mg):
        return "_no results_\n"
    out = ["NINJA v1.0.2 (`experiments/ninja/build.sh`), the noise injector of the paper that reported the HYPRE ParaSails race",
           "(PPoPP 2017), loaded with `LD_PRELOAD`; it delays sends from a helper thread and keeps their order per destination.",
           "Modes: *random* (NIN_PATTERN=1, a share of the sends delayed by a fixed time; NINJA as shipped reseeds before every",
           "draw with a value that overflows to one constant, so that it delays no send below a ratio of 100%; the runs use a",
           "build that takes the low bits instead), *system-centric* (NIN_MODE=0: delays from a model of the network queue),",
           "and *application-centric* (NIN_MODE=1: from one system-centric learning run, NINJA learns the interval between",
           "groups of sends with the same tag and delays the earlier group to a random fifth of the ranks by 1.2 times it).",
           "Every run in its own directory, one at a time for the replica, HYPRE and MG; budget and timeout as for the stress",
           "baseline (`experiments/ninja/run.py`, `experiments/ninja/campaign.sh`)."]
    if cal:
        out += ["", "### Calibration: NINJA's ParaSails replica",
                "", "NINJA's replica of the ParaSails race (`test/ninja_test_hypre_parasails.c`; two request exchanges with one tag,",
                "a one-second pause between them, which as shipped needs 64 processes; here the copy without that check, and",
                "`replica-fixed`, the same with the second exchange on its own tag as in HYPRE 2.11.0), 5 iterations per run, 60 s",
                f"each. Rows by code commit: {commits(cal)}.", ""]
        out += _ninja_runs(cal, lambda label: "ReplicaFixed" if label.endswith("fixed") else "Replica")
    if hy:
        out += ["", "### HYPRE 2.10.1 and 2.11.0",
                "", "`ij -solver 8 -pout 0` as in E5c, 60 s per run. A run that hangs in the ParaSails setup is killed before",
                "ij prints its setup times (*of which in the setup*); a run killed later was slow, not hung in the race.",
                f"Rows by code commit: {commits(hy)}.", ""]
        out += _ninja_runs(hy, lambda label: "HypreFixed" if label.endswith("2.11.0") else "Hypre", phases=True)
        bug = [r for r in hy if r["label"].endswith("2.10.1") and r["run"] != "0"]
        num("nNinjaHypreRuns", len(bug))
        num("nNinjaHypreHang", sum(1 for r in bug if r["result"] == "TIMEOUT" and not r.get("reached")))
        fixed = [r for r in hy if r["label"].endswith("2.11.0") and r["run"] != "0"]
        num("nNinjaHypreFixedRuns", len(fixed))
        num("nNinjaHypreFixedHang", sum(1 for r in fixed if r["result"] == "TIMEOUT" and not r.get("reached")))
    if mb:
        by = {r["name"]: r for r in score.compiled(rows(os.path.join(RES, "mbi", "mbi.csv")))}
        st = {r["program"]: r for r in rows(os.path.join(RES, "mbi", "stress.csv"))}
        out += ["", "### MBI", "", "The programs of the stress baseline, up to 30 runs per program and mode (in application-centric mode",
                "the learning run is the first), stopping at the first that does not finish, 60 s each. The last columns: of the",
                "programs Stretto predicts from a run that finished, how many the mode never makes fail; and of the programs",
                f"stress testing never makes fail, how many the mode does. Rows by code commit: {commits(mb)}.", "",
                "A failure is any run that does not finish, as in the stress baseline; some are segmentation faults of NINJA",
                "itself (*of them crashes*: errors made to fail by one, and correct programs failing by one), and the second F1",
                "does not count them.", "",
                "| noise | labelled errors | made to fail | median first failure | after the first run | correct programs failing | of them crashes | F1 | F1, crashes not counted | Stretto's predicted errors missed | stress misses found |",
                "|---|---|---|---|---|---|---|---|---|---|---|"]
        delays = sorted({int(m.group(1)) for r in mb if r["mode"] == "random" for m in [re.search(r"delay=(\d+)us", r["setting"])] if m})
        groups = collections.OrderedDict()
        for r in mb:
            if r["program"] in by:
                groups.setdefault((r["mode"], r["setting"]), []).append(r)
        for (mode, _), g in sorted(groups.items(), key=lambda kv: (["plain", "random", "system", "app"].index(kv[0][0]), kv[0][1])):
            err = [r for r in g if r["expect"] == "ERROR"]
            ok = [r for r in g if r["expect"] == "OK"]
            found = [r for r in err if r["first_failure"]]
            fp = [r for r in ok if r["first_failure"]]
            firsts = sorted(int(r["first_failure"]) for r in found)
            med = firsts[len(firsts) // 2] if firsts else "-"
            later = [r for r in found if int(r["first_failure"]) > 1]
            pred = [r for r in err if score.predicted_verdict(by[r["program"]]) == "ERROR"]
            missed = [r for r in pred if not r["first_failure"]]
            stress_missed = [r for r in err if r["program"] in st and not st[r["program"]]["first_failure"]]
            new = [r for r in stress_missed if r["first_failure"]]
            segv = lambda sel: [r for r in sel if r["outcome"] in ("SIGNAL:11", "APP-ERROR:-11")]

            def f1_of(tp, fn_, fpn):
                prec = tp / (tp + fpn) if tp + fpn else 0.0
                rec = tp / (tp + fn_) if tp + fn_ else 0.0
                return 2 * prec * rec / (prec + rec) if prec + rec else 0.0
            tp, fpn = len(found), len(fp)
            f1 = f1_of(tp, len(err) - tp, fpn)
            tp_s, fp_s = tp - len(segv(found)), fpn - len(segv(fp))
            f1_s = f1_of(tp_s, len(err) - tp_s, fp_s)
            out.append(f"| {_ninja_mode(g[0])} | {len(err)} | {tp} | {med} | {len(later)} | {fpn} | {len(segv(found))} / {len(segv(fp))} "
                       f"| {f1:.2f} | {f1_s:.2f} | {len(missed)} of {len(pred)} | {len(new)} of {len(stress_missed)} |")
            k = f"nNinjaMBI{_ninja_key(g[0], delays)}"
            num(k + "Found", tp)
            num(k + "FP", fpn)
            num(k + "Later", len(later))
            num(k + "PredMissed", len(missed))
            num(k + "StressMissedFound", len(new))
            num(k.replace("nNinja", "Ninja", 1) + "fone", f1)
            num(k.replace("nNinja", "Ninja", 1) + "foneNoCrash", f1_s)
            num(k + "Crashes", len(segv(found)) + len(segv(fp)))
            if mode == "app":
                num("nNinjaMBIAppLearnFailed", sum(1 for r in g if r.get("learn") and not r["learn"].startswith("FINISHED")))
        num("nNinjaMBIerrors", len([r for r in by.values() if r["expect"] == "ERROR"]))
    if mg:
        out += ["", "### NPB MG 2.4.1", "", "MG class S as in E5d, 120 s per run; a run *changes the result* when its residual norm differs",
                "from the plain runs' (every plain run printed the same), and fails the relative check of later versions when it is",
                f"more than 1e-8 off the reference. Rows by code commit: {commits(mg)}.", "",
                "| ranks | noise | runs | finished | result changed | fails the relative check |", "|---|---|---|---|---|---|"]
        plain = {r["value"] for r in mg if r["mode"] == "plain" and r.get("value")}
        base = plain.pop() if len(plain) == 1 else None
        ref = MG_REFERENCE["S"]
        rel = lambda v: abs(float(v) - ref) / ref if v else 0.0
        delays = sorted({int(m.group(1)) for r in mg if r["mode"] == "random" for m in [re.search(r"delay=(\d+)us", r["setting"])] if m})
        groups = collections.OrderedDict()
        for r in mg:
            if r["run"] != "0":
                groups.setdefault((int(r["np"]), r["mode"], r["setting"]), []).append(r)
        for (np_, mode, _), g in sorted(groups.items(), key=lambda kv: (kv[0][0], ["plain", "random", "system", "app"].index(kv[0][1]), kv[0][2])):
            ch = [r for r in g if base and r.get("value") and r["value"] != base]
            fl = [r for r in g if rel(r.get("value")) > 1e-8]
            out.append(f"| {np_} | {_ninja_mode(g[0])} | {len(g)} | {sum(r['result'] == 'FINISHED' for r in g)} | {len(ch)} | {len(fl)} |")
            k = f"nNinjaMG{ {16: 'Sixteen', 32: 'ThirtyTwo'}.get(np_, '') }{_ninja_key(g[0], delays)}"
            num(k + "Runs", len(g))
            num(k + "Changed", len(ch))
            num(k + "FailRel", len(fl))
    return "\n".join(out) + "\n"


SCALE_APPS = (("hypre", "Hypre", "HYPRE 2.10.1, ParaSails"), ("amg", "AMG", "AMG"), ("lu", "Lu", "NPB LU, class A"))
NP_WORD = {4: "Four", 8: "Eight", 16: "Sixteen", 32: "ThirtyTwo", 64: "SixtyFour", 128: "HundredTwentyEight"}


def scale():
    """E11: HYPRE, AMG and NPB LU traced beyond eight processes, each trace analysed under
    /usr/bin/time and a sample of its alternatives replayed with controls (experiments/scale/run.sh:
    scale.csv, one row per trace; samples/<app>-<np>.csv, the sample's rows; samples/<app>-<np>.time),
    and compared with scale-b7c886f.csv, the campaign before the analysis changes of 22 Oct 2026."""
    d = os.path.join(RES, "scale")
    rs = rows(os.path.join(d, "scale.csv"))
    if not rs:
        return "## E11: scale\n\n_no results_\n"
    nps = sorted({int(r["np"]) for r in rs})

    def classes(r):
        """'1f 8;probe-alt 187' -> {'1f': 8, 'probe-alt': 187}"""
        return {k: int(v) for k, v in (c.strip().rsplit(" ", 1) for c in (r.get("findings") or "").split(";") if c.strip())}

    def analysed(r):
        """The analysis printed its trace header and /usr/bin/time no failure (a nonzero status or a signal)."""
        p = os.path.join(d, "samples", f"{r['app']}-{r['np']}.time")
        t = open(p).read() if os.path.exists(p) else ""
        return bool(r.get("wildcard_receives") and r.get("analysis_s")) and "Command" not in t

    def sample(r):
        """The sample's rows that were replayed (replay_sample.py skips an alternative without a horizon)."""
        return [x for x in rows(os.path.join(d, "samples", f"{r['app']}-{r['np']}.csv")) if x.get("replay") not in (None, "", "no horizon")]

    out = [f"## E11: scale ({nps[0]} to {nps[-1]} processes)", "",
           "HYPRE 2.10.1's `ij -solver 8 -pout 0` (ParaSails, MPICH build), AMG with 16^3 points per process (`-problem 1`,",
           "Open MPI) and NPB LU class A (MPICH build), each traced once per process count; each trace analysed in one",
           "process under `/usr/bin/time` (`--no-sp`), and a random sample of its alternatives replayed with the pins of",
           "their horizon and a control (`experiments/scale/run.sh`, `experiments/replay_sample.py`). Process counts above",
           "the container's processors (12 hardware threads, 6 cores, in the committed campaign) oversubscribe it, so these",
           "runs measure the analysis, which runs in one process, not the traced program's speed. They ran outside a timed",
           "leg, with other containers on the machine: the times show the cost and are not timing claims. Peak memory is",
           "the analysis's maximum resident set. *Per record* is the analysis time over the trace's records, *per",
           "alternative* the time over the alternatives reported, *per alternative and record* that over the records too.",
           f"Rows by code commit: {commits(rs)}.", "",
           "| application | processes | traced run | records | wildcard receives | probes | findings by class | analysis (s) "
           "| peak memory (GB) | per record (µs) | per alternative (ms) | per alternative and record (ns) | replays "
           "| pins matched | finished | controls finished | control pins matched |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    done = [r for r in rs if analysed(r)]
    replayed = []
    by_app = collections.defaultdict(dict)
    per_rec = collections.defaultdict(list)       # µs per record, per application
    per_alt_rec = collections.defaultdict(list)   # ns per alternative and record
    pts = collections.defaultdict(list)           # (records, alternatives, seconds, peak KiB) per application
    for app, key, label in SCALE_APPS:
        for r in sorted((r for r in rs if r["app"] == app), key=lambda r: int(r["np"])):
            np_ = int(r["np"])
            c = classes(r)
            alts = sum(c.values())
            secs, gb, recs = float(r["analysis_s"]), int(r["peak_kb"]) * 1024 / 1e9, int(r["records"])   # %M is in KiB
            per = secs / alts * 1e3 if alts else None
            per_rec[key].append(secs / recs * 1e6)
            if per:
                per_alt_rec[key].append(per / recs * 1e6)
            s = sample(r)
            for x in s:
                x["app"], x["np"] = app, np_
            replayed += s
            out.append(f"| {label} | {np_} | {r['traced_run']} | {recs} | {r['wildcard_receives']} | {r['probes']} | "
                       + (", ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: -kv[1])) or "none")
                       + f" | {secs:.1f} | {gb:.1f} | {secs / recs * 1e6:.1f} | {f'{per:.1f}' if per else '-'} "
                       f"| {f'{per / recs * 1e6:.1f}' if per else '-'} | {r['sample']} | {r['matched']} | {r['finished']} "
                       f"| {r['controls_finished']} | {sum(x.get('control_matched') == 'True' for x in s)} |")
            k = f"Scale{key}{NP_WORD[np_]}"
            num(k + "Time", round(secs))
            num(k + "GB", f"{gb:.1f}")
            num(k + "Records", f"{recs / 1e6:.2f}")
            if int(r["wildcard_receives"] or 0) or int(r["probes"] or 0):     # LU has none
                num(f"n{k}Wild", int(r["wildcard_receives"] or 0))
                num(f"n{k}Alts", alts)
                num(f"n{k}ProbeAlts", c.get("probe-alt", 0))
            if per:
                num(k + "PerAlt", f"{per:.1f}")
            by_app[key][np_] = (secs, recs)
            pts[key].append((recs, alts, secs, int(r["peak_kb"])))
    for key, b in by_app.items():
        lo, hi = b[min(b)], b[max(b)]
        num(f"Scale{key}TimeRatio", f"{hi[0] / lo[0]:.1f}")       # from the fewest processes to the most
        num(f"Scale{key}RecordsRatio", f"{hi[1] / lo[1]:.1f}")
    num("nScaleNpMin", nps[0])
    num("nScaleNpMax", nps[-1])
    num("nScaleTraces", len(rs))
    num("nScaleTracedFinished", sum(r["traced_run"] == "FINISHED" for r in rs))
    num("nScaleAnalysed", len(done))
    num("nScaleSample", sum(int(r["sample"] or 0) for r in rs))
    num("nScaleSampleMatched", sum(int(r["matched"] or 0) for r in rs))
    num("nScaleSampleFinish", sum(int(r["finished"] or 0) for r in rs))
    num("nScaleSampleControls", sum(int(r["controls_finished"] or 0) for r in rs))
    num("nScaleSampleControlsMatched", sum(x.get("control_matched") == "True" for x in replayed))
    sampled = collections.Counter(x["cls"] for x in replayed)
    out += ["", f"Traced runs that finished: {sum(r['traced_run'] == 'FINISHED' for r in rs)} of {len(rs)}; analyses that finished: "
            f"{len(done)} of {len(rs)}. Sampled replays: {len(replayed)} ("
            + ", ".join(f"{k} {v}" for k, v in sorted(sampled.items())) + f"); with every pin matched: "
            f"{sum(x.get('matched') == 'True' for x in replayed)}; finished: {sum(x.get('replay') == 'FINISHED' for x in replayed)}; "
            f"controls finished: {sum(x.get('control') == 'FINISHED' for x in replayed)}, with every pin matched: "
            f"{sum(x.get('control_matched') == 'True' for x in replayed)}. A trace without wildcard receives or probes (LU) has no "
            "alternative to sample."]

    def what(x) -> str:
        """What a sampled alternative is: a probe alternative of HYPRE's request exchanges (tag 222)
        is the ParaSails race of E5c, whose replays hang."""
        m = re.match(r"\S*probe\(\d+, \d+\) src=ANY tag=(\d+) ", x.get("finding") or "")
        if x["app"] == "hypre" and x.get("cls") == "probe-alt" and m and m.group(1) == "222":
            return "a request probe of ParaSails (tag 222), the race of E5c"
        return f"a {x.get('cls')} alternative"
    unfinished = [x for x in replayed if x.get("replay") != "FINISHED"]
    failed = [x for x in replayed if x.get("control") != "FINISHED"]
    num("nScaleSampleUnfinished", len(unfinished))
    num("nScaleSampleUnfinishedRequest", sum(what(x).startswith("a request probe") for x in unfinished))
    num("nScaleSampleControlsFailed", len(failed))
    num("nScaleSampleControlsStatusFifteen", sum(x.get("control") == "APP-ERROR:15" for x in failed))
    out += ["", "Replays that did not finish: "
            + ("; ".join(f"{x['replay']}, {x['app']} at {x['np']} processes, {what(x)}" for x in unfinished) or "none")
            + ". Controls that did not finish: "
            + ("; ".join(f"{x['control']}, {x['app']} at {x['np']} processes, the control of {what(x)}" for x in failed) or "none")
            + "." + (" Status 15 is the unexplained exit of some of HYPRE's replays and controls in E5c, which plain runs "
                     "launched the same way never took."
                     if any(x["app"] == "hypre" and x.get("control") == "APP-ERROR:15" for x in failed) else "")]
    rng = lambda xs, fmt: f"{min(xs):{fmt}} to {max(xs):{fmt}}"   # noqa: E731
    kb_rec = {k: [kb * 1024 / 1e3 / recs for recs, _, _, kb in v] for k, v in pts.items()}    # kB per record
    amg = pts.get("AMG", [])
    if per_rec.get("Lu") and len(amg) >= 2:
        # least squares, seconds ~ a * records + b * alternatives, over AMG's traces
        sxx, sxy, syy = (sum(p * q for p, q in pairs) for pairs in
                         ([(r, r) for r, _, _, _ in amg], [(r, a) for r, a, _, _ in amg], [(a, a) for _, a, _, _ in amg]))
        sxs, sys_ = sum(r * s for r, _, s, _ in amg), sum(a * s for _, a, s, _ in amg)
        det = sxx * syy - sxy * sxy
        a_rec, b_alt = (sxs * syy - sys_ * sxy) / det, (sys_ * sxx - sxs * sxy) / det
        dev = max(abs(a_rec * r + b_alt * a - s) / s for r, a, s, _ in amg)
        num("ScaleAMGFitRecord", f"{a_rec * 1e6:.0f}")        # µs per record
        num("ScaleAMGFitAlt", f"{b_alt * 1e3:.2f}")           # ms per alternative
        num("ScaleAMGFitDev", f"{dev * 100:.0f}")             # % of the largest deviation from the fit
        lo, hi = min(amg), max(amg)
        per_lo, per_hi = lo[2] / lo[1] * 1e3, hi[2] / hi[1] * 1e3
        out += ["", f"Where the time goes. LU has no alternative: its analysis costs {rng(per_rec['Lu'], '.0f')} µs and "
                f"{rng(kb_rec['Lu'], '.2f')} kB per record at every process count. AMG's alternatives grow faster than its trace",
                f"({lo[1]} to {hi[1]} as its records grow {hi[0] / lo[0]:.1f}-fold), and the time per alternative "
                f"{'falls' if per_hi < per_lo else 'grows'} ({per_lo:.2f} to {per_hi:.2f} ms)"
                + (": the analysis is about linear in the records plus the alternatives," if dev < 0.1 else
                   "; a time linear in the records plus the alternatives fits it only roughly,"),
                f"{a_rec * 1e6:.0f} µs per record and {b_alt * 1e3:.2f} ms per alternative by a least-squares fit of AMG's "
                f"{len(amg)} traces (each within {dev * 100:.0f}%),",
                f"and its peak memory is {rng(kb_rec['AMG'], '.1f')} kB per record. Theorem T4 bounds a check by a quasi-linear function",
                "of the trace; the fast checks of probe alternatives cost far less (docs/model.md, §2: closures in the Lamport",
                "order of the run, chained over a rank's probes)."]
    # the campaign before the analysis changes of 22 Oct 2026, if its rows are kept
    old = {(r["app"], int(r["np"])): r for r in rows(os.path.join(d, "scale-b7c886f.csv"))}
    if old:
        out += ["", f"Before the analysis changes of 22 Oct 2026 (the campaign at {commits(list(old.values()))}; each campaign traced its",
                "own runs, so the traces differ a little): analysis time, peak memory and time per alternative, before and now.", "",
                "| application | processes | records before / now | alternatives before / now | analysis (s) before / now "
                "| peak memory (GB) before / now | per alternative (ms) before / now |", "|---|---|---|---|---|---|---|"]
        for app, key, label in SCALE_APPS:
            for r in sorted((r for r in rs if r["app"] == app), key=lambda r: int(r["np"])):
                o = old.get((app, int(r["np"])))
                if o is None:
                    continue
                ao, an = sum(classes(o).values()), sum(classes(r).values())
                po = f"{float(o['analysis_s']) / ao * 1e3:.2f}" if ao else "-"
                pn = f"{float(r['analysis_s']) / an * 1e3:.2f}" if an else "-"
                out.append(f"| {label} | {r['np']} | {o['records']} / {r['records']} | {ao} / {an} | {float(o['analysis_s']):.1f} / "
                           f"{float(r['analysis_s']):.1f} | {int(o['peak_kb']) * 1024 / 1e9:.1f} / {int(r['peak_kb']) * 1024 / 1e9:.1f} "
                           f"| {po} / {pn} |")
    return "\n".join(out) + "\n"


def e9():
    """MPI-PRV (experiments/mpiprv/): its own evaluation rerun from its artifact image, correct
    programs that ask it for reorderings no execution has, the check of its Adept Kernel
    report, its marker arms on Stretto's corpora, and Stretto on its applications."""
    d = os.path.join(RES, "mpiprv")
    if not os.path.isdir(d):
        return "_no results_\n"
    def R(f):
        return rows(os.path.join(d, f))
    every = []
    out = ["MPI-PRV (\"Efficient Predictive Monitoring of Message Passing Interface Programs\", ISSTA 2026) from its",
           "artifact image, pinned by digest (`experiments/mpiprv/README.md`), in a container with four processors of",
           "the second machine, outside any timed leg: its times are indicative. Stretto's tracer, an LD_PRELOAD library",
           "built for the same Open MPI 4.1.6, runs inside the same image."]

    rq1 = R("rq1.csv")
    if rq1:
        every += rq1
        rounds = sorted({r["rep"] for r in rq1}, key=int)
        props = sorted({r["prop"] for r in rq1}, key=lambda p: int(p[4:]))
        full = [k for k in rounds if all(r["result"] == "PASS" for r in rq1 if r["rep"] == k)]
        num("nPRVrqOneRounds", len(rounds))
        num("nPRVrqOneFull", len(full))
        num("nPRVrqOneProps", len(props))
        out += ["", "### Its own evaluation, rerun", "",
                f"RQ1 (`RQ1/TestallProp.sh`, the ten properties, each on its own buggy program), {len(rounds)} rounds: every "
                f"property reported in {len(full)} of them.", "",
                "| property | program | rounds reported | reported sequence (first round) |", "|---|---|---|---|"]
        for p in props:
            rs = [r for r in rq1 if r["prop"] == p]
            out.append(f"| {p} | {rs[0]['program']} | {sum(r['result'] == 'PASS' for r in rs)} of {len(rs)} | `{rs[0]['matched']}` |")

    ap_ = R("adept-prv.csv")
    if ap_:
        every += ap_
        def related(seq):
            # in the stencil, rank r receives only from r - 1 and r + 1
            m = re.match(r"p(\d+)_Irecv p\d+_Use p(\d+)_Isend", seq or "")
            return "-" if not m else ("yes" if abs(int(m.group(1)) - int(m.group(2))) == 1 else "no")
        out += ["", "Its motivating example (Adept Kernel MPI, `stencil.c`, the marker `usea0()` before the interior loop; "
                "pattern `Irecv Use Isend`, 4 ranks): `MPI-PRV.sh` analyses the trace shipped with the example; the other "
                "rows are fresh runs of the instrumented kernel shipped with it. The last column says whether the reported "
                "send goes to the reported receiving rank (in this stencil rank r receives only from r - 1 and r + 1).", "",
                "| trace | events | reported | sequence | send to the receiving rank |", "|---|---|---|---|---|"]
        for r in ap_:
            out.append(f"| {r['run']} | {r['events']} | {r['result']} | `{r['matched']}` | {related(r['matched'])} |")
        num("nPRVadeptReports", sum(r["result"] == "PASS" for r in ap_))
        num("nPRVadeptTraces", len(ap_))
        num("nPRVadeptRelated", sum(related(r["matched"]) == "yes" for r in ap_))

    rq2 = R("rq2.csv")
    if rq2:
        every += rq2
        ok = [r for r in rq2 if r["result"] in ("matched", "none")]
        num("nPRVrqTwoConfigs", len(ok))
        num("nPRVrqTwoMatched", sum(r["result"] == "matched" for r in ok))
        num("nPRVrqTwoAbsent", sum(1 for r in ok if r.get("absent")))
        out += ["", "RQ2/RQ3 (`RQ2-3/MPI-PRV_result.sh`): MPI-PRV on the trace shipped for each application and process "
                "count, with that application's pattern. The artifact's scripts write the result column of their table as "
                "a constant (√, whatever the analyser prints); `reported` is what the analyser printed. `absent` lists the "
                "pattern's events that never occur in the trace.", "",
                "| application | np | pattern | events | absent | reported | analysis (s), here | analysis (s), artifact's table |",
                "|---|---|---|---|---|---|---|---|"]
        for r in rq2:
            out.append(f"| {r['app']} | {r['np']} | {r['pattern']} | {r['events']} | {r.get('absent', '')} | {r['result']} | "
                       f"{r['analysis_s']} | {r['artifact_s']} |")
        out += ["", f"Reported in {sum(r['result'] == 'matched' for r in ok)} of {len(ok)} configurations."]

    pr = R("probes.csv")
    if pr:
        every += pr
        what = {"wild_irecv-src1": "MPI_Irecv from the sender, MPI_Wait, marker", "wild_irecv-any": "the same, MPI_ANY_SOURCE",
                "wild_recv-src1": "MPI_Recv from the sender, marker", "wild_recv-any": "the same, MPI_ANY_SOURCE",
                "order-barrier-BA": "A, MPI_Barrier / MPI_Barrier, B", "order-barrier-AB": "the same, the observed order (control)",
                "order-sendrecv-BA": "A, MPI_Send / MPI_Recv, B", "order-sendrecv-AB": "the same, the observed order (control)",
                "unrelated": "rank 0 receives from 1 and uses; later rank 2 sends to 3",
                "lc-error": "MPI-BugBench receive-mpi_irecv-001, marker at its labelled access (error)",
                "lc-twin": "its twin: the access and marker after MPI_Wait (correct)",
                "lc-twin-loaded": "the twin, with four busy loops on the same processors"}
        out += ["", "### Correct programs that ask for a reordering no execution has (`experiments/mpiprv/probes.sh`)", "",
                "Each program is correct; `expected` is *none* when no execution has the pattern's order, and *matched* "
                "for the controls, whose observed order is the pattern.", "",
                "| case | program | pattern | expected | reported | sequence |", "|---|---|---|---|---|---|"]
        cases = list(dict.fromkeys(r["case"] for r in pr))
        for c in cases:
            rs = [r for r in pr if r["case"] == c]
            seqs = sorted({r["matched"] for r in rs if r["matched"]})
            out.append(f"| {c} | {what.get(c, rs[0]['source'])} | {rs[0]['pattern']} | {rs[0]['expected']} | "
                       f"{sum(r['result'] == 'matched' for r in rs)} of {len(rs)} | {', '.join(f'`{s}`' for s in seqs)} |")
            key = "nPRVprobe" + re.sub(r"[^A-Za-z]", "", c.title())
            num(key, sum(r["result"] == "matched" for r in rs))
            num(key + "Runs", len(rs))

    ad = R("adept.csv")
    if ad:
        every += ad
        out += ["", "### The Adept Kernel report (`experiments/mpiprv/adept.sh`)", "",
                "The example's sources built without instrumentation; `fixed` completes the two halo sends the original "
                "never waits for, `mutant` is the fixed kernel with its waits moved after the edge updates (the error the "
                "report describes, put in on purpose); `delay` runs make one rank sleep before each MPI_Isend. The result is "
                "the hash of a0 and a1 on every rank at their free(); `after marker` and `after loop` count the halo sends "
                "posted after the receiving rank's marker, and after its whole interior loop, out of all halo sends.", "",
                "| run | command | result | same result as normal | sends after marker | after loop | of | "
                "delayed rank's sends after loop | of | guard records |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for r in ad:
            out.append(f"| {r['run']} | `{r['command']}` | {r['result_id']} | {r['same_result_as_normal']} | "
                       f"{r['sends_after_marker']} | {r['sends_after_loop']} | {r['send_pairs']} | "
                       f"{r.get('delayed_sends_after_loop', '-')} | {r.get('delayed_sends', '-')} | "
                       f"{r['guard_records'] if r['guard_records'] != '-' else ''} {r['guard_kinds']} |")
        by = {r["run"]: r for r in ad}
        for k, key in (("fixed-delay1", "DelayOne"), ("fixed-delay2", "DelayTwo"), ("mutant-delay1", "MutantDelay")):
            if k in by:
                num(f"nPRVadept{key}Late", by[k]["sends_after_loop"])
                num(f"nPRVadept{key}Pairs", by[k]["send_pairs"])
                if by[k].get("delayed_sends", "-") != "-":
                    num(f"nPRVadept{key}DelayedLate", by[k]["delayed_sends_after_loop"])
                    num(f"nPRVadept{key}Delayed", by[k]["delayed_sends"])
        num("nPRVadeptFixedSame", sum(1 for r in ad if r["run"].startswith("fixed") and r["same_result_as_normal"] == "True"))
        num("nPRVadeptFixedRuns", sum(1 for r in ad if r["run"].startswith("fixed")))
        num("nPRVadeptOrigSame", sum(1 for r in ad if r["run"].startswith("overlapped") and r["same_result_as_normal"] == "True"))
        num("nPRVadeptOrigRuns", sum(1 for r in ad if r["run"].startswith("overlapped")))
        num("nPRVadeptMutantSame", sum(1 for r in ad if r["run"].startswith("mutant") and r["same_result_as_normal"] == "True"))
        if "guard" in by:
            num("nPRVadeptGuard", by["guard"]["guard_records"])
        if "guard-fixed" in by:
            num("nPRVadeptGuardFixed", by["guard-fixed"]["guard_records"])
        p = os.path.join(d, "adept-guard.txt")
        if os.path.exists(p):
            out += ["", "Guard records by site (`adept-guard.txt`; count, site, function and source line):", "", "```"]
            out += [l.rstrip() for l in open(p) if re.match(r"(# guard(-fixed)?:|\d+ \S+\+0x)", l)]
            out += ["```"]

    co = R("corpus.csv")
    if co:
        every += co
        st = {r["program"]: r for r in R("stretto-corpus.csv")}
        every += list(st.values())
        built = [r for r in co if r.get("build") == "ok"]
        progs = {r["program"]: r for r in built}
        insc = [p for p, r in progs.items() if r["scope"] == "in"]
        outsc = sorted(p for p, r in progs.items() if r["scope"] != "in")
        notbuilt = sorted({r["program"] for r in co if r.get("build") != "ok"})
        num("nPRVcorpus", len(progs))
        num("nPRVinScope", len(insc))
        num("nPRVoutScope", len(outsc))
        out += ["", "### MPI-PRV on Stretto's corpora (`experiments/mpiprv/run.py`)", "",
                f"Programs run: {len(progs)}; outside MPI-PRV's declared scope (probes, message races, buffering), listed and "
                f"not scored: {len(outsc)} ({', '.join(outsc) or '-'}); not built with Open MPI 4.1.6: {len(notbuilt)}. "
                f"Rows by code commit: {commits(co)}.", "",
                "Arm a: the shipped properties whose events are all MPI calls (1, 6, 7, 9), program unchanged. Arm b: the "
                "buffer properties (2, 3, 8, with both members of the paper's unions) with markers inserted by a script "
                "that does not read the labels. Arm c: the same properties with markers at the labelled lines and at the "
                "moved lines of the twins, an upper bound. Twins are the local-concurrency errors corrected (the labelled "
                "access moved after the completing call), labelled correct. `a+b` is the tool run with every shipped "
                "property and the blind markers: an error if either arm reports one.", ""]
        V = {arm: {r["program"]: r["verdict"] for r in built if r["arm"] == arm} for arm in "abc"}
        V["a+b"] = {p: "ERROR" if "ERROR" in (V["a"].get(p), V["b"].get(p))
                    else score.HARNESS if score.HARNESS in (V["a"].get(p), V["b"].get(p)) else "OK" for p in progs}
        V["S"] = {p: r["verdict"] for p, r in st.items() if r.get("build") == "ok"}
        lc_dom = [p for p in insc if progs[p]["expect"] == "OK" or progs[p]["detail"] == "LocalConcurrency"]
        for title, ps, tag in (("All programs in scope", insc, ""),
                               ("Local-concurrency errors and the correct programs (the domain of the buffer properties)",
                                lc_dom, "LC")):
            out += [f"{title}: {len(ps)} programs, {sum(progs[p]['expect'] == 'ERROR' for p in ps)} labelled errors.", "",
                    "| | TP | FP | FN | TN | precision | recall | F1 |", "|---|---|---|---|---|---|---|---|"]
            for arm, label in (("a", "MPI-PRV, arm a (no markers)"), ("b", "MPI-PRV, arm b (blind markers)"),
                               ("c", "MPI-PRV, arm c (markers at the labels)"), ("a+b", "MPI-PRV, arms a and b"),
                               ("S", "Stretto with the buffer guard (`stretto_corpus.py`)")):
                rs = [{"expect": progs[p]["expect"], "verdict": V[arm][p], "program": p} for p in ps if p in V[arm]]
                if not rs:
                    continue
                c = score.confusion(rs, lambda r: r["verdict"])
                out.append(conf_row(label, c))
                if c["harness"] or c["crash"]:
                    out.append(f"| ({label}: {c['harness']} program(s) without a verdict, a run without a trace or a "
                               f"failed analyser, and {c['crash']} analysis crash(es), left out of the row) | | | | | | | |")
                A = {"a+b": "AB", "S": "Stretto"}.get(arm, arm.upper()) + tag
                num(f"PRVfone{A}", c["f1"])
                for k in ("tp", "fp", "fn", "tn"):
                    num(f"nPRV{k}{A}", c[k])
            out.append("")
        # which patterns arm a's reports come from, on labelled errors and on correct programs
        by_pat = collections.Counter((m.split(":")[0], r["expect"]) for r in built if r["arm"] == "a" and r["scope"] == "in"
                                     and r["verdict"] == "ERROR" for m in r["matched"].split(" | ") if m)
        if by_pat:
            out.append("Arm a's reports by pattern (labelled errors / correct programs): " + "; ".join(
                f"{p} {by_pat[(p, 'ERROR')]} / {by_pat[(p, 'OK')]}" for p in sorted({p for p, _ in by_pat})) + ".")
            out.append("")
        def group(r):
            if r["program"].startswith("twin:"):
                return "twins of the local-concurrency errors (correct)"
            return f"{'MPI-BugBench' if r['corpus'] == 'mbb' else 'MBI'} {r['detail'] if r['expect'] == 'ERROR' else 'correct'}"
        groups = sorted({group(progs[p]) for p in insc})
        out += ["", "By group, programs in scope reported as errors (a / b / c; Stretto with the guard):", "",
                "| group | programs | arm a | arm b | arm c | Stretto | not recorded by the instrumentation |",
                "|---|---|---|---|---|---|---|"]
        for g in groups:
            ps = [p for p in insc if group(progs[p]) == g]
            cnt = {arm: sum(1 for r in built if r["program"] in ps and r["arm"] == arm and r["verdict"] == "ERROR") for arm in "abc"}
            sv = sum(1 for p in ps if st.get(p, {}).get("verdict") == "ERROR")
            calls = collections.Counter(c for p in ps for c in (progs[p]["outside_model"] or "").split())
            out.append(f"| {g} | {len(ps)} | {cnt['a']} | {cnt['b']} | {cnt['c']} | {sv if st else '-'} | "
                       f"{', '.join(f'{k} ({v})' for k, v in calls.most_common(6))} |")
        fps = sorted({(r["program"], r["arm"], r["matched"]) for r in built if r["scope"] == "in" and r["expect"] == "OK"
                      and r["verdict"] == "ERROR"})
        if fps:
            out += ["", "Correct programs reported as errors:", "", "| program | arm | reported |", "|---|---|---|"]
            out += [f"| {p} | {a} | `{m.replace('|', ';')}` |" for p, a, m in fps]
        num("nPRVtwinFP", len({p for p, a, _ in fps if p.startswith("twin:") and a == "c"}))
        num("nPRVtwins", sum(1 for p in insc if p.startswith("twin:")))

    apps = R("apps.csv")
    if apps:
        every += apps
        ran = [r for r in apps if r.get("run", "").startswith("rc=")]
        rq2t = {(r["app"], r["np"]): r for r in rq2} if rq2 else {}
        num("nPRVapps", len({r["app"] for r in ran}))
        num("nPRVappConfigs", len(ran))
        num("nPRVappsWild", len({r["app"] for r in ran if int(r.get("wildcards") or 0) > 0}))
        num("nPRVappsAlts", sum(int(r.get("alternatives") or 0) for r in ran))
        num("nPRVappsGuardLocal", sum(1 for r in ran if int(r.get("guard_local") or 0) > 0))
        reps = R("apps-replays.csv")
        num("nPRVappsReplays", len(reps))
        num("nPRVappsReplayFinished", sum(1 for r in reps if r.get("replay") == "FINISHED" and r.get("matched") == "True"
                                          and r.get("control") == "FINISHED"))
        num("nPRVappsReplayHang", sum(1 for r in reps if r.get("replay") == "TIMEOUT" and r.get("matched") == "True"
                                      and r.get("control") == "FINISHED"))

        def counts(text):          # "1d:confirmed*2 1c*3" -> {"1d:confirmed": 2, "1c": 3}
            return {k: int(v) for k, v in re.findall(r"(\S+)\*(\d+)", text or "")}

        def hangs(item):           # a replay under rendezvous that hangs as predicted
            res = item.split(": ", 1)[-1]
            return res.startswith("TIMEOUT") and "unmatched" not in res and ("control-" not in res or res.endswith("control-FINISHED"))
        heat = next((r for r in apps if r["app"] == "Heat" and r.get("run", "").startswith("rc=")), None)
        if heat:
            num("nPRVheatDeadlock", counts(heat.get("classes")).get("1d", 0))
            num("nPRVheatDeadlockConfirmed", counts(heat.get("confirmed")).get("1d:confirmed", 0))
            num("nPRVheatAlts", int(heat.get("alternatives") or 0))
            num("nPRVheatReplayHang", counts(heat.get("replay_results")).get("TIMEOUT/matched/control-FINISHED", 0))
        rdv = [(r["app"], x.strip()) for r in apps for col in ("rendezvous", "guard_rendezvous")
               for x in (r.get(col) or "").split(";") if x.strip()]
        num("nPRVappsRendezvous", len(rdv))
        num("nPRVappsRendezvousHang", sum(1 for _, x in rdv if hangs(x)))
        num("nPRVdtgRendezvous", sum(1 for a, _ in rdv if a == "DTG"))
        num("nPRVdtgRendezvousHang", sum(1 for a, x in rdv if a == "DTG" and hangs(x)))
        out += ["", "### Stretto on MPI-PRV's applications (`experiments/mpiprv/apps.py`)", "",
                "The artifact's binaries (built with its instrumentation, which only appends its events to a file), its "
                "command lines and process counts up to 24 ranks, traced by Stretto inside its image; replays are every "
                "L1 alternative (30 at most), each with the pins of its horizon and a control; `confirmed` replays the "
                "error findings with a pin (`stretto confirm`, Open MPI's default eager limits); `under rendezvous` "
                "replays the predictions that need B = 0 or B below a message size, with every message above 8 bytes "
                "sent by rendezvous (replay result / pins / control). Stretto's analysis time includes the single-path "
                "explorer, bounded as in the corpus harness (600 events, 30 000 states per alternative); MPI-PRV's is its "
                "analysis of the artifact's own trace of the same configuration, for its one pattern (table above).", "",
                "| application | np | run | records | wildcard receives | classes | alternatives | replays | confirmed | "
                "under rendezvous | "
                "analysis (s) | MPI-PRV (s) | with the guard: run, classes, confirmed, under rendezvous |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in apps:
            m = rq2t.get((r["app"], str(r["np"])), {})
            out.append(f"| {r['app']} | {r['np']} | {r.get('run', '')} | {r.get('records', '')} | {r.get('wildcards', '')} | "
                       f"{r.get('classes', '')} | {r.get('alternatives', '')} | {r.get('replay_results', '')} | "
                       f"{r.get('confirmed', '')} | {r.get('rendezvous', '')} | {r.get('analysis_s', '')} | "
                       f"{m.get('analysis_s', '')} | "
                       f"{r.get('guard_run', '')} {r.get('guard_classes', '')} {r.get('guard_confirmed', '')} "
                       f"{r.get('guard_rendezvous', '')} |")
    out.insert(4, f"Rows by code commit: {commits(every)}.")
    return "\n".join(out) + "\n"


# The proportions of the evaluation that get a Wilson 95% interval, by the macros of their counts,
# in groups: (title, ((count, out of, what is counted), ...)). NINJA's are added by name
# (_ninja_proportions), and those whose macros a run did not write are left out.
PROPORTIONS = (
    ("Traced runs with a cross alternative (the race predicted from one run)", (
        ("nHypreFourExposed", "nHypreFourRuns", "HYPRE 2.10.1, 4 processes"),
        ("nHypreEightExposed", "nHypreEightRuns", "HYPRE 2.10.1, 8 processes"),
        ("nHypreExposed", "nHypreTraced", "HYPRE 2.10.1, both process counts"),
        ("nHypreFixedExposed", "nHypreFixedTraced", "HYPRE 2.11.0, the fix"),
        ("nHypreNewTwoExposed", "nHypreNewTwoTraced", "HYPRE 3.2.0 `ex5`, two levels"),
        ("nPetscTwoExposed", "nPetscTwoTraced", "PETSc 3.26.0 `ex2`, two levels"),
        ("nHypreNewAllExposed", "nHypreNewAllTraced", "HYPRE 3.2.0, `ex5` and `ex2` at two levels"),
        ("nHypreNewFixTwoExposed", "nHypreNewFixTwoTraced", "HYPRE 3.2.0 `ex5` with the fix, two levels"),
        ("nMGExposed", "nMGTraced", "NPB MG 2.4.1"),
        ("nMGFixedExposed", "nMGFixedTraced", "NPB MG 3.3.1, the fix"),
        ("nDealExposed", "nDealTraced", "deal.II 9.3.0-rc1"),
        ("nDealCleanExposed", "nDealClean", "deal.II 9.3.0-rc1, clean runs only"),
        ("nDealFixedExposed", "nDealFixedTraced", "deal.II 9.3.0, the fix"))),
    ("Untraced runs, plain and with the replay library's random delays (stress testing)", (
        ("nHypreStressDelayHang", "nHypreStressDelayRuns", "HYPRE 2.10.1, runs with delays that hang"),
        ("nHypreStressHang", "nHypreStressRuns", "HYPRE 2.10.1, runs that hang"),
        ("nHypreNewStressTwoDelayHang", "nHypreNewStressTwoDelayRuns", "HYPRE 3.2.0 `ex5`, two levels, runs with delays that hang"),
        ("nPetscStressTwoDelayHang", "nPetscStressTwoDelayRuns", "PETSc 3.26.0 `ex2`, two levels, runs with delays that hang"),
        ("nHypreNewAllStressDelayHang", "nHypreNewAllStressDelayRuns", "HYPRE 3.2.0, `ex5` and `ex2` at two levels, runs with delays that hang"),
        ("nHypreNewAllStressHang", "nHypreNewAllStressRuns", "HYPRE 3.2.0, `ex5` and `ex2` at two levels, runs that hang"),
        ("nHypreNewFixStressHang", "nHypreNewFixStressRuns", "HYPRE 3.2.0 `ex5` with the fix, runs that hang"),
        ("nMGStressChanged", "nMGStressRuns", "NPB MG 2.4.1, runs whose residual changes"),
        ("nDealStressPlainChanged", "nDealStressPlainRuns", "deal.II 9.3.0-rc1, plain runs that print wrong values"),
        ("nDealStressDelayChanged", "nDealStressDelayRuns", "deal.II 9.3.0-rc1, runs with delays that print wrong values"),
        ("nDealStressChanged", "nDealStressRuns", "deal.II 9.3.0-rc1, runs that print wrong values"))),
    ("Replay samples and controls", (
        ("nAMGsampleFailed", "nAMGsample", "AMG: sampled alternatives whose replay did not finish with its pins matched, "
                                           "or whose control did not finish"),
        ("nNeksampleFailed", "nNeksample", "Nekbone: the same"),
        ("nDealControlsSame", "nDealControls", "deal.II 9.3.0-rc1: controls of the cross replays that print the expected values"))))

NINJA_PROGRAMS = (("Hypre", "HYPRE 2.10.1"), ("HypreFixed", "HYPRE 2.11.0, the fix"), ("HypreNew", "HYPRE 3.2.0 `ex5`, two levels"),
                  ("HypreNewFix", "HYPRE 3.2.0 `ex5` with the fix, two levels"), ("Petsc", "PETSc 3.26.0 `ex2`, two levels"))
NINJA_NOISE = (("App", "application-centric noise"), ("System", "system-centric noise"), ("Random", "random noise"),
               ("RandomShort", "random noise, the shorter delay"), ("RandomLong", "random noise, the longer delay"), ("Plain", "no noise"))


def _ninja_proportions() -> tuple:
    """NINJA's rates (E10, E5f) by the macro names _ninja_runs, hypre_new and ninja() give them:
    the runs that hang (in the setup, where the table tells) per program, process count and
    noise, and over a program's modes and process counts; MG's runs whose residual changes."""
    progs, noises = [p for p, _ in NINJA_PROGRAMS], [k for k, _ in NINJA_NOISE]
    per = []
    for k in NUM:
        m = re.fullmatch(r"nNinja(HypreNewFix|HypreNew|HypreFixed|Hypre|Petsc)(Four|Eight)([A-Za-z]+)Runs", k)
        if m and m.group(3) in noises:
            base = k[:-len("Runs")]
            hang = base + "HangSetup" if base + "HangSetup" in NUM else base + "Hang"
            what = (f"{dict(NINJA_PROGRAMS)[m.group(1)]}, {4 if m.group(2) == 'Four' else 8} processes, "
                    f"{dict(NINJA_NOISE)[m.group(3)]}: runs that hang" + (" in the setup" if hang.endswith("Setup") else ""))
            per.append(((progs.index(m.group(1)), m.group(2) == "Eight", noises.index(m.group(3))), (hang, k, what)))
    out = [p for _, p in sorted(per)]
    out += [("nNinjaHypreHang", "nNinjaHypreRuns", "HYPRE 2.10.1, every noise and process count: runs that hang in the setup"),
            ("nNinjaHypreFixedHang", "nNinjaHypreFixedRuns", "HYPRE 2.11.0, the fix, every noise and process count: runs that hang in the setup"),
            ("nNinjaHypreNewHangSetup", "nNinjaHypreNewRuns", "HYPRE 3.2.0 `ex5`, every noise and process count: runs that hang in the setup"),
            ("nNinjaHypreNewFixHangSetup", "nNinjaHypreNewFixRuns",
             "HYPRE 3.2.0 `ex5` with the fix, every noise and process count: runs that hang in the setup")]
    out += [(f"nNinjaPetsc{key}Hang", f"nNinjaPetsc{key}Runs", f"PETSc 3.26.0 `ex2`, {text}, both process counts: runs that hang")
            for key, text in NINJA_NOISE if key in ("App", "System", "Random")]
    out += [(f"nNinjaMG{word}{key}Changed", f"nNinjaMG{word}{key}Runs", f"NPB MG 2.4.1, {np_} processes, {text}: "
             "runs whose residual changes") for np_, word in ((16, "Sixteen"), (32, "ThirtyTwo")) for key, text in NINJA_NOISE]
    return tuple(out)


def _count(name: str):
    """The value of a count macro as an int; None if this run did not write it."""
    try:
        return int(NUM[name].replace("\\,", ""))
    except (KeyError, ValueError):
        return None


def _stats_counts() -> None:
    """The counts the intervals need that the sections above do not write: the untraced runs with
    delays of E5c and E5f (the plain runs cannot change the schedule but by chance), and the
    sampled alternatives of E5 and E5b whose replay or control failed."""
    st = rows(os.path.join(RES, "hypre", "stress.csv"))
    if st:
        delayed = [r for r in st if r["setting"] == "delays"]
        num("nHypreStressDelayRuns", len(delayed))
        num("nHypreStressDelayHang", sum(r["result"] == "TIMEOUT" for r in delayed))
    two = []
    for prefix, rs in (("HypreNew", [(int(r["levels"]), r) for r in rows(os.path.join(RES, "hypre3", "stress.csv"))]),
                       ("Petsc", [(int(m.group(1)), r) for r in rows(os.path.join(RES, "petsc", "stress.csv"))
                                  for m in [re.search(r"parasails(\d)", r["driver"])] if m])):
        sel = [r for lv, r in rs if lv >= 2 and r["setting"] == "delays"]
        if sel:
            num(f"n{prefix}StressTwoDelayHang", sum(r["result"] == "TIMEOUT" for r in sel))
            two += sel
    if two:
        num("nHypreNewAllStressDelayRuns", len(two))
        num("nHypreNewAllStressDelayHang", sum(r["result"] == "TIMEOUT" for r in two))
    for app, prefix in (("amg", "AMG"), ("nekbone", "Nek")):
        rs = rows(os.path.join(RES, app, "replay_sample.csv"))
        if rs:
            num(f"n{prefix}sampleFailed", sum(1 for r in rs if not (r.get("replay") == "FINISHED" and r.get("matched") == "True"
                                                                    and r.get("control") == "FINISHED")))


def _intervals() -> list:
    out = ["### Wilson 95% intervals", "",
           "Each proportion of k in n below has Wilson's score interval at 95%: the proportions p whose score test",
           "accepts k/n, |k/n - p| <= 1.96 sqrt(p(1 - p)/n). It takes the n runs (traced runs, untraced runs, sampled",
           "alternatives) as independent trials of one setting, as each starts the program afresh. The replays of one",
           "alternative are not independent trials (the distinct alternatives below), so the replay counts get none.",
           "Its macro is the count's with `CI` appended, and `CILo` and `CIHi` are its bounds alone."]
    for title, props in PROPORTIONS + (("NINJA (E10, E5f)", _ninja_proportions()),):
        lines = []
        for k_name, n_name, what in props:
            k, n = _count(k_name), _count(n_name)
            if k is None or not n:
                continue
            if not 0 <= k <= n:
                lines.append(f"| {what} | {k} of {n} | none: the count exceeds the runs | |")
                continue
            lines.append(f"| {what} | {k} of {n} | {ci(k_name, k, n)} | `{k_name}CI` |")
        if lines:
            out += ["", f"{title}:", "", "| | k of n | 95% interval | macro |", "|---|---|---|---|"] + lines
    return out


def _replayed(r) -> bool:
    """A campaign row that pinned an alternative and replayed it (not one without a horizon to
    pin, nor the row of a traced run that failed)."""
    res = r.get("replay") or ""
    return bool(r.get("pin")) and res not in ("", "no horizon") and not res.startswith("traced run")


def _distinct() -> list:
    """The distinct pinned alternatives behind the replays of E5c to E5f."""
    out = ["", "### Distinct alternatives behind the replays", "",
           "A replay pins one alternative: the receive (`STRETTO_PIN`) or probe (`STRETTO_PIN_PROBE`) it changes, named by",
           "its rank and its ordinal among that rank's receives or probes, and the source it is pinned to (the `pin` column",
           "of the campaign tables; the other pins of its horizon follow from the trace). Replays pin the same alternative",
           "when these agree in the same program, version and process count (in E5f, driver and level count too). An",
           "ordinal names the same call in two traces as far as the rank's calls come in the same order, as in the traces",
           "of one configuration here. Replays of one alternative from different traces are separate runs, but they test",
           "the same alternative, so a count of replays counts it once per trace that has it.", "",
           "| campaign | processes | kind | replays | traced runs with one | distinct alternatives | replays per alternative | macro |",
           "|---|---|---|---|---|---|---|---|"]
    total = {}

    def row(label, nps, kind, rs, macro, *scope):
        per = collections.Counter((*(r.get(c) for c in scope), pin_id(r["pin"])) for r in rs)
        runs = len({(*(r.get(c) for c in scope), r.get("run")) for r in rs})
        num(macro, len(per))
        total[macro] = (len(rs), len(per))
        lo, hi = min(per.values()), max(per.values())
        out.append(f"| {label} | {nps} | {kind} | {len(rs)} | {runs} | {len(per)} | {lo if lo == hi else f'{lo}–{hi}'} | `{macro}` |")

    hy = [r for r in rows(os.path.join(RES, "hypre", "predict.csv")) if _replayed(r)]
    kind = lambda r: r.get("kind") or "cross"     # noqa: E731  (rows before the classification: cross, as in E5c)
    for np_, word in (("4", "Four"), ("8", "Eight")):
        for k, m in (("cross", ""), ("same", "Local")):
            sel = [r for r in hy if r["np"] == np_ and kind(r) == k]
            if sel:
                row("HYPRE 2.10.1 (E5c)", np_, k, sel, f"nHypre{word}{m}DistinctPins", "np")
            if sel and k == "cross":      # the probes they change, whatever the source
                num(f"nHypre{word}DistinctProbes", len({pin_id(r["pin"])[1:3] for r in sel}))
    cross = [r for r in hy if kind(r) == "cross"]
    if cross:
        row("HYPRE 2.10.1 (E5c)", "/".join(sorted({r["np"] for r in cross}, key=int)), "cross", cross, "nHypreDistinctPins", "np")
    mg_all = rows(os.path.join(RES, "npbmg", "predict-2.4.1.csv"))
    mg = [r for r in mg_all if _replayed(r)]
    if mg:
        row("NPB MG 2.4.1 (E5d)", "/".join(sorted({r["np"] for r in mg}, key=int)), "-", mg, "nMGDistinctPins", "np")
    dl = [r for r in rows(os.path.join(RES, "dealii", "predict-9.3.0-rc1.csv")) if _replayed(r)]
    for k, m in (("cross", ""), ("same", "Same")):
        sel = [r for r in dl if r.get("kind") == k]
        if sel:
            row("deal.II 9.3.0-rc1 (E5e)", "/".join(sorted({r["np"] for r in sel}, key=int)), k, sel, f"nDeal{m}DistinctPins", "np")
    ps = rows(os.path.join(RES, "petsc", "parasails.csv"))
    new = []
    for prefix, label, rs in (("HypreNew", "HYPRE 3.2.0 `ex5` (E5f)", _new_rows(rows(os.path.join(RES, "hypre3", "predict.csv")), "ex5", "3.2.0")),
                              ("Petsc", "PETSc 3.26.0 `ex2` (E5f)", _new_rows([r for r in ps if "nothresh" not in r["driver"]], "ex2", "3.26.0"))):
        cross = [r for r in rs if r["kind"] == "cross" and r["levels"] >= 2 and _replayed(r)]
        for np_ in sorted({r["np"] for r in cross}):
            row(label, np_, "cross, two levels", [r for r in cross if r["np"] == np_], f"n{prefix}{NP_WORD[np_]}DistinctPins",
                "driver", "drv", "np", "levels")
        if cross:
            row(label, "/".join(str(n) for n in sorted({r["np"] for r in cross})), "cross, two levels", cross,
                f"n{prefix}DistinctPins", "driver", "drv", "np", "levels")
        new += cross
    if new:
        row("HYPRE 3.2.0, `ex5` and `ex2` (E5f)", "/".join(str(n) for n in sorted({r["np"] for r in new})), "cross, two levels", new,
            "nHypreNewAllDistinctPins", "driver", "drv", "np", "levels")
    said = [f"{total[m][0]} {what} test {total[m][1]} alternatives" for m, what in
            (("nHypreDistinctPins", "cross replays of HYPRE 2.10.1"), ("nMGDistinctPins", "replays of NPB MG"),
             ("nDealDistinctPins", "cross replays of deal.II"), ("nHypreNewAllDistinctPins", "cross replays of HYPRE 3.2.0"))
            if m in total]
    if said:
        out += ["", "So the " + (", the ".join(said[:-1]) + ", and the " if len(said) > 1 else "") + said[-1] + "."]
    if mg:
        # MG's traced runs, and what the replays of each alternative print
        traces = {}
        for r in mg_all:
            t = traces.setdefault((r["np"], r["run"]), (r.get("records"), r.get("alternatives"), r.get("traced_norm"), set()))
            if r.get("pin"):
                t[3].add(pin_id(r["pin"]))
        same_traces = len({(k[0], *t[:3], frozenset(t[3])) for k, t in traces.items()})
        outcomes = len({(r["np"], pin_id(r["pin"]), r["replay"], r.get("norm") or "") for r in mg})
        fin = [r for r in mg if r["replay"] == "FINISHED" and r.get("norm")]
        changed = [r for r in fin if r["norm"] != r.get("traced_norm")]
        by_pin = collections.defaultdict(set)
        for r in fin:
            by_pin[(r["np"], pin_id(r["pin"]))].add(r["norm"] != r.get("traced_norm"))
        mixed = sum(1 for v in by_pin.values() if len(v) > 1)
        no_ctl = [r for r in mg if r.get("norm") and r["norm"] != r.get("control_norm") and r["norm"] == r.get("traced_norm")]
        ctl = [r for r in mg if r.get("control") == "FINISHED"]
        ctl_traced = sum(1 for r in ctl if r.get("control_norm") == r.get("traced_norm"))
        num("nMGDistinctTraces", same_traces)
        num("nMGDistinctOutcomes", outcomes)
        num("nMGChangedTraced", len(changed))
        num("nMGDistinctChanged", distinct_pins(changed, "np"))
        num("nMGDistinctMixed", mixed)
        out += ["", f"NPB MG 2.4.1: the {len(traces)} traced runs "
                + ("have the same number of records, the same alternatives and the same residual" if same_traces == 1 else
                   f"fall into {same_traces} kinds by their number of records, their alternatives and their residual")
                + f" (`nMGDistinctTraces` = {same_traces}). Their {len(mg)} replays "
                f"have {outcomes} distinct outcomes (alternative, result, residual; `nMGDistinctOutcomes`). {len(changed)} replays "
                f"finished with a residual other than the traced run's (`nMGChangedTraced`), which "
                + (f"every control that finished printed ({ctl_traced})" if ctl_traced == len(ctl) else
                   f"{ctl_traced} of the {len(ctl)} controls that finished printed")
                + f"; they pin {distinct_pins(changed, 'np')} distinct alternatives (`nMGDistinctChanged`), "
                f"{mixed} of which change it in some finished replays and not in others (`nMGDistinctMixed`)."
                + (f" E5d's count of replays that change the result compares with the control's residual, so it also counts "
                   f"{len(no_ctl)} replay{'s' if len(no_ctl) > 1 else ''} whose residual is the traced run's and whose control "
                   "printed none." if no_ctl else "")]
    return out


# The application campaigns' tables of pinned runs: (macro key, campaign, tables). The first four
# are the ones pin_log reads.
PIN_CAMPAIGNS = (("Hypre", "HYPRE 2.10.1 and 2.11.0 (E5c)", ("hypre/predict.csv", "hypre/predict-2.11.0.csv")),
                 ("MG", "NPB MG 2.4.1 and 3.3.1 (E5d)", ("npbmg/predict-2.4.1.csv", "npbmg/predict-3.3.1.csv")),
                 ("AMG", "AMG, the sample of E5", ("amg/replay_sample.csv",)),
                 ("Nek", "Nekbone, the sample of E5b", ("nekbone/replay_sample.csv",)),
                 ("Deal", "deal.II 9.3.0-rc1 and 9.3.0 (E5e)", ("dealii/predict-9.3.0-rc1.csv", "dealii/predict-9.3.0.csv")),
                 ("HypreNew", "HYPRE 3.2.0 `ex5` and its fixed variant (E5f)", ("hypre3/predict.csv", "hypre3/predict-fix.csv")),
                 ("Petsc", "PETSc 3.26.0 `ex2` (E5f)", ("petsc/parasails.csv", "petsc/parasails-default-m32.csv")))


def pinned_runs(rs) -> list:
    """The pinned runs of a campaign table's rows, replays and controls alike, as (result, every
    required pin matched); an alternative that was not replayed has none."""
    out = []
    for r in rs:
        if _replayed(r):
            out += [(r.get("replay"), r.get("matched") == "True"), (r.get("control"), r.get("control_matched") == "True")]
    return [(res, m) for res, m in out if res]


def _pin_log_all() -> list:
    """The pin log over every application campaign, by campaign (pin_log reads the first four)."""
    out = ["", "### The pin log of every application campaign", "",
           "The pinned runs of every application campaign, replays and controls alike, with the runs that left a",
           "required pin unmatched and how they ended. A run with a pin unmatched that finished or hung did not test its",
           "alternative, whatever it printed: the log overrules its outcome. The pin log at the end of E5d (`nPinRuns`)",
           "reads the first four campaigns only.", "",
           "| campaign | pinned runs | a pin unmatched | of which finished | hung | other | rows by code commit | macros |",
           "|---|---|---|---|---|---|---|---|"]
    tot = collections.Counter()
    for key, label, files in PIN_CAMPAIGNS:
        rs = [r for f in files for r in rows(os.path.join(RES, f))]
        runs = pinned_runs(rs)
        if not runs:
            continue
        un = [res for res, m in runs if not m]
        c = {"Runs": len(runs), "Unmatched": len(un), "UnmatchedFinished": un.count("FINISHED"), "UnmatchedHung": un.count("TIMEOUT")}
        c["UnmatchedEnded"] = c["UnmatchedFinished"] + c["UnmatchedHung"]
        for k, v in c.items():
            num(f"nPin{k}{key}", v)
            tot[k] += v
        other = collections.Counter(res for res in un if res not in ("FINISHED", "TIMEOUT"))
        out.append(f"| {label} | {len(runs)} | {len(un)} | {c['UnmatchedFinished']} | {c['UnmatchedHung']} | "
                   f"{', '.join(f'{n} {res}' for res, n in sorted(other.items())) or 0} | {commits([r for r in rs if _replayed(r)])} "
                   f"| `nPinRuns{key}`, `nPinUnmatched{key}`, ... |")
    if not tot:
        return []
    for k, v in tot.items():
        num(f"nPin{k}All", v)
    out.append(f"| **all** | {tot['Runs']} | {tot['Unmatched']} | {tot['UnmatchedFinished']} | {tot['UnmatchedHung']} | "
               f"{tot['Unmatched'] - tot['UnmatchedEnded']} | | `nPinRunsAll`, `nPinUnmatchedAll`, `nPinUnmatchedEndedAll`, ... |")
    # deal.II's cross replays print wrong values; the ones that confirm the race by the rule of E1
    cross = [r for r in rows(os.path.join(RES, "dealii", "predict-9.3.0-rc1.csv")) if _replayed(r) and r.get("kind") == "cross"]
    if cross:
        wrong = [r for r in cross if r.get("differs") == "True"]
        conf = [r for r in cross if _valid_confirmed("dealii", r)]
        num("nDealWrongConfirmed", len(conf))
        out += ["", f"deal.II 9.3.0-rc1: of the {len(wrong)} cross replays that print wrong values, {len(conf)} had every required pin "
                f"matched and a control that finished (`nDealWrongConfirmed`), the confirmation rule of E1 and E8c."]
    return out


def baselines_found(path) -> dict:
    """Which labelled errors of a corpus each tool finds, over the ones the stress baseline ran
    (stress.csv next to the corpus table): Stretto by its verdict from one run (observed or
    predicted, as E1's F1 counts it), MUST 1.9.2 under E1's two scorings (a timeout or failed run
    not counted, and counted), and the stress baseline at the default limits (a run that did not
    finish). Empty without a stress table or MUST verdicts."""
    by = {r["name"]: r for r in score.compiled(rows(path))}
    err = [(by[r["program"]], bool(r["first_failure"])) for r in rows(os.path.join(os.path.dirname(path), "stress.csv"))
           if r["expect"] == "ERROR" and r["program"] in by]
    if not err or not any(r.get("must") for r, _ in err):
        return {}
    return {"Errors": {r["name"] for r, _ in err},
            "Stretto": {r["name"] for r, _ in err if score.verdict(r) == "ERROR"},
            "MUST": {r["name"] for r, _ in err if score.must_verdict(r) == "ERROR"},
            "MUSTsym": {r["name"] for r, _ in err if score.must_verdict_hangs(r) == "ERROR"},
            "Stress": {r["name"] for r, failed in err if failed}}


BASELINE_CORPORA = (("MBI", "MBI", "mbi"), ("MBB", "MPI-BugBench level 2", "mbb"))


def _baselines() -> list:
    """The baselines together on the corpora, and Stretto against each (paired sign tests)."""
    got = [(key, title, f) for key, title, c in BASELINE_CORPORA for f in [baselines_found(os.path.join(RES, c, f"{c}.csv"))] if f]
    if not got:
        return []
    table = collections.OrderedDict()      # (what, macro suffix) -> count per corpus
    names = []
    for key, title, f in got:
        S, T = f["Stretto"], f["Stress"]
        cells = [("labelled errors compared (the ones the stress baseline ran)", "Errors", f["Errors"]),
                 ("found by Stretto (from one run, observed or predicted)", "Stretto", S),
                 ("found by MUST 1.9.2", "MUST", f["MUST"]),
                 ("found by MUST 1.9.2, a timeout or failed run counted", "MUSTsym", f["MUSTsym"]),
                 ("found by the stress baseline at the default limits", "Stress", T),
                 ("found by MUST 1.9.2 or the stress baseline", "MUSTStress", f["MUST"] | T),
                 ("found by MUST 1.9.2 (timeouts counted) or the stress baseline", "MUSTsymStress", f["MUSTsym"] | T),
                 ("found by Stretto and by neither MUST 1.9.2 nor the stress baseline", "StrettoNeither", S - (f["MUST"] | T)),
                 ("the same, MUST's timeouts counted", "StrettoNeitherMUSTsym", S - (f["MUSTsym"] | T)),
                 ("found by MUST 1.9.2 or the stress baseline and not by Stretto", "NotStretto", (f["MUST"] | T) - S),
                 ("the same, MUST's timeouts counted", "NotStrettoMUSTsym", (f["MUSTsym"] | T) - S)]
        for what, sfx, v in cells:
            num(f"n{key}Union{sfx}", len(v))
            table.setdefault((what, sfx), {})[key] = len(v)
        for what, v in (("found by Stretto and by neither baseline", S - (f["MUST"] | T)),
                        ("found by a baseline and not by Stretto", (f["MUST"] | T) - S)):
            if v:
                names.append(f"{title}, {what}: " + (", ".join(sorted(v)) if len(v) <= 20 else f"{len(v)} programs") + ".")
    out = ["", "### The baselines together", "",
           "Which labelled errors each tool finds, over the ones the stress baseline ran (on MBI, every one): Stretto by its",
           "verdict from one run, observed or predicted, as E1's F1 counts it; MUST 1.9.2 under E1's two scorings; and the",
           "stress baseline at the default limits, an error found when a run does not finish. A program found by one tool",
           "and missed by another is what a single comparison of F1 scores does not show.", "",
           "| labelled errors | " + " | ".join(t for _, t, _ in got) + " | macro |", "|---|" + "---|" * len(got) + "---|"]
    for (what, sfx), per in table.items():
        out.append(f"| {what} | " + " | ".join(str(per.get(k, "-")) for k, _, _ in got) + f" | `n<corpus>Union{sfx}` |")
    if names:
        out += [""] + names
    out += ["", "Stretto against each baseline on the same labelled errors: the errors one finds and the other misses, and",
            "the exact two-sided sign test of that split (the errors both find or both miss carry no information), the",
            "probability of a split at least as uneven if each such error were as likely to go either way.", "",
            "| corpus | against | Stretto only | the baseline only | p | macros |", "|---|---|---|---|---|---|"]
    for key, title, f in got:
        S = f["Stretto"]
        for b, what in (("Stress", "the stress baseline at the default limits"), ("MUST", "MUST 1.9.2"),
                        ("MUSTsym", "MUST 1.9.2, a timeout or failed run counted")):
            x, y = len(S - f[b]), len(f[b] - S)
            p = p_text(sign_test(x, y))
            num(f"n{key}Sign{b}Stretto", x)
            num(f"n{key}Sign{b}Other", y)
            num(f"{key}Sign{b}P", p)
            out.append(f"| {title} | {what} | {x} | {y} | {p} | `n{key}Sign{b}Stretto`, `n{key}Sign{b}Other`, `{key}Sign{b}P` |")
    return out


def _efour() -> list:
    """The configurations of E4 that its overhead range (TraceOverhead) is taken over."""
    if not EFOUR:
        return []
    timed = [x for x in EFOUR if x["timed"]]
    rest = [x for x in EFOUR if not x["timed"]]
    num("nEfourConfigs", len(EFOUR))
    num("nEfourLong", len(timed))
    num("nEfourLongNames", ", ".join(x["config"] for x in timed))
    s = lambda x: "-" if x is None else f"{x:.2f}"     # noqa: E731
    out = ["", "### E4: the configurations of the overhead range", "",
           "E4's NPB slowdown range (`TraceOverhead`) runs from the least to the greatest median over the configurations",
           f"whose plain timed section lasts at least 1 s by NPB's own timer: {len(timed)} of the {len(EFOUR)} configurations",
           "(`nEfourLong`, `nEfourLongNames`). Each row is the median over the repetitions of the timed leg.", "",
           "| configuration | NPB plain (s) | NPB traced (s) | NPB slowdown, median (range over the repetitions) |", "|---|---|---|---|"]
    out += [f"| {x['config']} | {s(x['plain'])} | {s(x['traced'])} | {x['slowdown']:.3f} ({x['range']}) |" for x in timed]
    plain = [x["plain"] for x in rest if x["plain"] is not None]
    sd = [x["slowdown"] for x in rest if x["slowdown"] is not None]
    none = [x for x in rest if x["slowdown"] is None]
    if rest:
        out += ["", f"The other {len(rest)} configurations' plain timed sections last "
                + (f"{min(plain):.2f} to {max(plain):.2f} s by the median (NPB prints hundredths of a second)" if plain else "an unknown time")
                + (f", and their median slowdowns range from {min(sd):.3f} to {max(sd):.3f}" if sd else "")
                + (f"; {len(none)} have no NPB slowdown" + (", their plain timed section printing 0.00 s"
                                                           if all(x["plain"] == 0 for x in none) else "") if none else "") + "."]
    return out


def statistics_section() -> str:
    """The statistics of the evaluation, from the same tables as the sections above, and some
    from their macros: run after them (the macro names: the module docstring)."""
    _stats_counts()
    out = ["## Statistics", "",
           "Statistics of the evaluation, from the tables of the sections above. Their macros are named after the counts",
           "they qualify (the docstring of `experiments/tables.py`).", ""]
    out += _intervals()
    out += _distinct()
    out += _pin_log_all()
    out += _baselines()
    out += _efour()
    return "\n".join(out) + "\n"


def leg_commits():
    cs = sorted({r["commit"] for r in rows(os.path.join(RES, "e4", "e4.csv")) if r.get("commit")})
    return ", ".join(cs) or "unknown"


def main():
    doc = ["# Results", "", "Generated by `experiments/tables.py` from the CSV files under `experiments/results/`; each table",
           "names the code commits its rows were produced with (the `commit` column). The corpus runs, their replays,",
           "E2, E6 and the E3 and E7 inputs come from `experiments/run_remaining.sh` on the second, shared machine",
           "(the container of `experiments/container/`, 12 cores of one NUMA node); the HYPRE campaign from",
           "`experiments/hypre/campaign.sh`, the NPB MG and deal.II campaigns from `experiments/npbmg/run.sh` and",
           "`experiments/dealii/run.sh`, the HYPRE 3.2.0 and PETSc campaigns (E5f) from `experiments/hypre3/run.sh` and",
           "`experiments/petsc/run.sh`, the five bugs of projects' histories (E5g) from `experiments/cabana/`, `stempy/`,",
           "`mfem332/`, `dealii8929/` and `lammps/run.sh`, Nekbone from `experiments/nekbone/run.sh` and the scale campaign (E11)",
           "from `experiments/scale/run.sh` there. The MUST verdicts",
           "are carried over from MUST 1.9.2 runs under MPICH in MPI-BugBench's own container, with the same timeout",
           "per program, and those of MUST 1.11.2 from the image of `experiments/must/`; NINJA's runs come from",
           "`experiments/ninja/campaign.sh`. The E4 times, the AMG trace and its analysis time come from one announced timed leg at commit",
           f"{leg_commits()} (`experiments/timing_leg.sh`, the host otherwise idle), and AMG's replay sample from that",
           "trace; the leg's times are the only timing claims. The replays of E8c, every confirmation claim's replays run again",
           "under the tracer, come from the commands of \"Replays as runs of the program\" in `experiments/RUNBOOK.md`.",
           "", "## E1: predictions vs benchmark labels", "",
           e1("MPI Bugs Initiative (MBI): message-race, tag-matching, probe and buffering programs", os.path.join(RES, "mbi", "mbi.csv"), "MBI"),
           e1("MPI-BugBench level 2: point-to-point and collective call-ordering, missing-call, local-concurrency and correct programs", os.path.join(RES, "mbb", "mbb.csv"), "MBB"),
           stress("MBI", os.path.join(RES, "mbi", "mbi.csv"), "MBI"),
           stress_eager("MBI", os.path.join(RES, "mbi", "mbi.csv"), "MBI"),
           stress("MPI-BugBench level 2", os.path.join(RES, "mbb", "mbb.csv"), "MBB"),
           stress_eager("MPI-BugBench level 2", os.path.join(RES, "mbb", "mbb.csv"), "MBB"),
           "### Repeated runs: how often each verdict holds (MBI)", "", reps(),
           *[y for x in [stress_reps()] if x for y in ("### Repeated stress passes against repeated runs (MBI)", "", x)],
           "## E2: buffering ablation", "", e2(),
           "## E3: level ablation", "", e3(),
           "## E3b: read bounds (MBI, docs/firstread.md)", "", reads_section(),
           "## E4: tracer overhead and analysis cost (NPB 3.4.3, MPICH build)", "", e4(),
           "## E5: real application (AMG)", "", e5() + app_sample("amg", "AMG"),
           "## E5b: real application (Nekbone)", "", nekbone() + app_sample("nekbone", "Nek"),
           "## E5c: a real bug (HYPRE 2.10.1, ParaSails)", "", hypre(),
           "### How often one trace exposes it, and how often testing hits it", "", hypre_campaign(),
           "## E5d: a second documented bug (NPB MG 2.4.1, a message of another exchange)", "", npbmg() + pin_log(),
           "## E5e: a third documented bug (deal.II 9.3.0-rc1, a probe takes a message of the next call)", "", dealii(),
           "## E5f: a new bug in a current release (HYPRE 3.2.0 ParaSails, a probe takes the next level's request)", "", hypre_new(),
           "## E5g: five documented bugs from projects' histories (Cabana, stempy, MFEM 3.3.2, deal.II's NBX, LAMMPS)", "", history(),
           "## E6: polynomial checker vs exhaustive explorer", "", e6(),
           "## E7: the value skeleton on MBI (level L2)", "", e7(),
           "## E8: the buffer guard (local concurrency, class 3-local)", "", guard() + must_tsan_local(),
           "## E8b: happens-before candidate selection against the check", "", e8_hb(),
           "## E8c: confirming replays judged as runs of the program", "", valid(),
           "## E9: MPI-PRV", "", e9(),
           "## E10: NINJA (noise injection)", "", ninja(),
           scale(),
           statistics_section()]
    path = os.path.join(ROOT, "docs", "results.md")
    open(path, "w").write("\n".join(doc))
    print("wrote", path)
    write_paper_numbers()
    figures.readme(ROOT, NUM)
    # a crashed analysis has no verdict: the tables leave it out, and the scoring fails
    crashed = score.crashes_seen()
    if crashed:
        print(f"tables.py: the analysis crashed on {len(crashed)} scored row(s), left out of the confusion matrices: "
              + ", ".join(crashed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
