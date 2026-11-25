#!/usr/bin/env python3
"""The outcome of Section 3 (RULE.md) for the rows of the campaign scripts that keep their own
tables: the suites' (MBI, MPI-BugBench, MPI-CorrBench) and the thread campaigns' (Uintah, SLATE,
DLA-Future, PaRSEC). A block of these runs the committed script for one pass or one cell into a
table of its own; `adapters.py annotate` then appends that table's rows to the block's table with
the common columns of rows.py (host, cpus, jobs, timeout, seed, commit, superseded) and the
outcome, so that the scripts themselves keep their columns and their committed tables stay as
they are. The thread campaigns' tables, which have no commit column, get it this way.

The rules, per kind of table (S: the bug's own symptom in the run; F: the tool's report names it;
on a program or variant without the bug the same categories are written and read as false alarms):

  stress    experiments/stress_baseline.py and `ninja/run.py corpus` (program, expect, runs,
            first_failure, outcome): S = the run did not finish (first_failure set), F = S
  must      experiments/mbi/run_must.py's column (MUST:OK, MUST:ERROR:..., MUST:TIMEOUT, MUST:RE,
            MUST:HARNESS-ERROR): F = MUST reported an error (experiments/mbi/score.py must_verdict),
            S = the run hung (TIMEOUT); RE and HARNESS-ERROR are tool failures
  corpus    the corpus table of a traced pass (experiments/mbi/run_mbi.py: run, classes, levels):
            S = what the run itself showed (score.observed_verdict), F = any error-class finding,
            observed or predicted (score.verdict); an analysis that crashed is a tool failure
  series    a thread campaign's untraced series (SLATE, DLA-Future, PaRSEC: result ok, wrong, hang,
            crash; Uintah: outcome pass, truncate, fail, hang): S = the bug's symptom (wrong; for
            Uintah truncate), F = S; hang and crash, which are not these bugs' symptoms, are tool
            failures (RULE.md Section 2.1)
  traced    a thread campaign's traced cells: S = result wrong (SLATE WRONG), F = the trace's
            findings at the bug's site (SLATE in_permuteRows > 0; DLA-Future and PaRSEC
            l1_receive_findings > 0); a report that is not complete is a tool failure

On a suite (--suite, RULE.md amendment 28) a run of a program labelled erroneous that does not
finish within T shows the symptom; on a program labelled correct a timeout counts as the committed
tab:rq2 scripts count it: the delays' and NINJA's as a failing run (experiments/tables.py stress:
any first_failure), MUST's on MBI and MPI-BugBench as no report (experiments/mbi/score.py
must_verdict: TIMEOUT is not ERROR) and on CorrBench as a run without a verdict, a tool failure
(experiments/corrbench/summary.py), Stretto's by its report (score.verdict, the run's end aside).
MUST's report counts when it holds an error, a data race alone (its summary "data race...") only
on a program labelled a data race (amendment 22). Stretto's F on a suite (amendment 32) is a
finding of an error class that does more than record the run's own failure: a run whose error
findings are only an observed abort, or only observed ones on a run that ended in its own error
(an MPI error, a signal, a nonzero exit), is observed and not reported. An observed deadlock on a
run that hung counts: it names the blocked calls, as MUST's deadlock report does.
Stretto's suite rows say which cell they belong to (column cell, amendment 23): the traced runs
"stretto", corrbench/run.py's native runs and stretto_bench.sh's untraced run 0 "plain", the
buffer guard's runs "stretto-guard" (an appendix cell). Uintah's plain build: every run that does
not pass is the symptom (amendment 27). --cell-timeouts "cell=T ..." writes each row's own T (the
thread campaigns, amendment 20; MUST-vc's runs without MUST as native:<cell>).

usage: adapters.py annotate --kind KIND <script table> <block table> [--set column=value ...]
       [--must-column must1112] [--suite S] [--cell-timeouts "cell=T ..."]
Python 3.6 runs this file.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "experiments", "mbi"))
import rows  # noqa: E402


_LABELS = {}


def label_of(r, a, race=False):
    """(erroneous, datarace) of the suite program a row is of; (None, None) off a suite. The row's
    own expect column says whether it is erroneous where it has one; the data-race label, which
    needs the suite's sources (suites.labels), only when race is asked for."""
    if not getattr(a, "suite", None):
        return None, None
    name = r.get("name") or r.get("program") or ""
    if name.endswith(".c") or name.endswith(".cpp"):
        name = name.rsplit(".", 1)[0].replace("/", "__")
    if r.get("expect") in ("OK", "ERROR") and not race:
        return r["expect"] == "ERROR", None
    if r.get("label") in ("correct", "erroneous") and not race:         # CorrBench's rows
        return r["label"] == "erroneous", None
    if a.suite not in _LABELS:
        import suites
        _LABELS[a.suite] = suites.labels(a.suite)
    return _LABELS[a.suite].get(name, (r.get("expect") == "ERROR", False))


def timed_out(r, a, end, correct=None):
    """outcome() of a run that did not finish within T (amendment 28): the symptom on a program
    labelled erroneous; on a correct one `correct`, the committed count (None: the caller's own
    rule); None off a suite (the caller decides)."""
    erroneous, _ = label_of(r, a)
    if erroneous is None:
        return None
    return rows.outcome(True, end) if erroneous else correct


def stress(r, a):
    s = bool((r.get("first_failure") or "").strip())
    if s and r.get("outcome") == "TIMEOUT":
        got = timed_out(r, a, True)
        if got is not None:
            return got
    return rows.outcome(s, s)


def must(r, a):
    import score
    v = r.get(a.must_column) or ""
    k = score.must_kind({"must": v})
    if k in ("RE", "HARNESS-ERROR", "CE", ""):
        return rows.outcome(False, False, failed=True)
    f = k == "ERROR"
    if f and (v[5:] if v.startswith("MUST:") else v).startswith("ERROR:data race"):
        race = label_of(r, a, race=True)[1]
        f = race is None or bool(race)      # a data race counts on data-race programs
    if k == "TIMEOUT":
        # on a correct program: no report as tab:rq2 counts MUST on MBI and MPI-BugBench, no verdict
        # (a tool failure) as CorrBench's summary does
        corr = rows.outcome(False, False, failed=True) if (getattr(a, "suite", "") or "").startswith("corrbench") \
            else rows.outcome(False, False)
        got = timed_out(r, a, f, corr)
        if got is not None:
            return got
    return rows.outcome(k == "TIMEOUT", f)


def stretto_f(levels, end, l2=""):
    """Stretto's F on a suite program (amendment 32): an error-class finding that is not only the
    record of the run's own failure, which is an observed abort alone, or observed findings alone
    on a run that ended in its own error (end neither FINISHED nor TIMEOUT: an MPI error, a signal,
    a nonzero exit). An observed deadlock of a run that hung counts. L2 by re-execution's abort or
    deadlock is a claim about another run and counts."""
    import score
    errs = set(lv for lv in levels if lv.split(":", 1)[-1] in score.ERROR)
    if set((l2 or "").split()) & {"abort", "deadlock"}:
        errs.add("L2")
    if not errs:
        return False
    own_error = not (end or "").startswith(("FINISHED", "TIMEOUT"))
    return not (errs == {"observed:abort"} or (own_error and all(e.startswith("observed:") for e in errs)))


def corpus(r, a):
    import score
    r.setdefault("cell", "stretto")
    if score.crashed(r):
        return rows.outcome(False, False, failed=True)
    f = stretto_f(score.levels(r), r.get("run"), r.get("l2"))
    if r.get("run") == "TIMEOUT":
        got = timed_out(r, a, f)
        if got is not None:
            return got
    return rows.outcome(score.observed_verdict(r) == "ERROR", f)


def series(r, a):
    v = (r.get("result") or r.get("outcome") or "").lower()
    if v in ("ok", "pass"):
        return rows.outcome(False, False)
    if v in ("wrong", "truncate"):
        return rows.outcome(True, True)
    if v.startswith("crash") and str(r.get("rc")) == "15":
        return rows.outcome(True, True)       # Open MPI's abort of MPI_ERR_TRUNCATE whose message was lost (run.py abort_class)
    if r.get("build") == "plain" and v in ("fail", "hang"):
        return rows.outcome(True, True)       # Uintah's plain build: the garbage reduction's abort and its hang alike (amendment 27)
    return rows.outcome(False, False, failed=True)


def traced(r, a):
    status = r.get("stretto_status", "complete")
    if status and status != "complete":
        return rows.outcome(False, False, failed=True)
    s = (r.get("result") or "").lower() == "wrong"
    hits = r.get("in_permuteRows") if "in_permuteRows" in r else r.get("l1_receive_findings")
    try:
        f = int(hits or 0) > 0
    except ValueError:
        f = False
    return rows.outcome(s, f)


def corrbench(r, a):
    """experiments/corrbench/run.py: a native row is a plain run, a trace or guard row a traced
    one, judged as corpus (its result is the run's end)."""
    r["cell"] = {"native": "plain", "guard": "stretto-guard"}.get(r.get("mode"), "stretto")
    if r.get("mode") == "build":
        return rows.outcome(False, False, failed=True)
    if r.get("mode") == "native":
        s = r.get("result") != "FINISHED"
        if s and r.get("result") == "TIMEOUT":
            got = timed_out(r, a, True)
            if got is not None:
                return got
        return rows.outcome(s, s)
    return corpus(dict(r, run=r.get("result", "")), a)


def bench(r, a):
    """experiments/mustvc/stretto_bench.sh: run 0 untraced (a plain run), the others traced, F an
    error-class finding (errors > 0); a report that is not complete is a tool failure."""
    s = (r.get("result") or "") not in ("FINISHED",)
    r["cell"] = "plain" if str(r.get("run")) == "0" else "stretto"
    if r.get("result") == "BUILD-FAILED":
        return rows.outcome(False, False, failed=True)
    if str(r.get("run")) == "0":
        if r.get("result") == "TIMEOUT":
            got = timed_out(r, a, True)
            if got is not None:
                return got
        return rows.outcome(s, s)
    if (r.get("status") or "complete") != "complete":
        return rows.outcome(False, False, failed=True)
    # its classes, cls@level*count, as levels level:cls (amendment 32)
    lv = ["%s:%s" % tuple(reversed(c.split("*", 1)[0].split("@", 1))) for c in (r.get("classes") or "").split(";") if "@" in c]
    f = stretto_f(lv, r.get("result"))
    if r.get("result") == "TIMEOUT":
        got = timed_out(r, a, f)
        if got is not None:
            return got
    return rows.outcome(s, f)


def threadreplay(r, a):
    """A replay of experiments/replay_sample.py --threads (the thread campaigns' replay.sh,
    mustvc/lock_replays.sh): its verdict where the table has one, else the committed verdict of
    experiments/replay_verdicts.py (docs/threads.md, sections 16 and 19), the recorded run taken as
    right unless the row's traced run went wrong (--recorded-wrong)."""
    if r.get("verdict"):
        return r["verdict"]
    sys.path.insert(0, os.path.join(ROOT, "experiments"))
    import replay_verdicts
    return replay_verdicts.verdict(r, a.recorded_wrong or r.get("recorded") == "wrong")


def mustvc(r, a):
    """experiments/mustvc/apps.sh: a native row is a plain run; a mustvc row reports the bug when a
    report names its pair (culprit > 0); a MUST-vc run that did not end otherwise than by the
    bug's symptom is a tool failure."""
    s = (r.get("outcome") or "") in ("wrong", "truncate")
    if r.get("tool") == "native":
        return rows.outcome(s, s)
    try:
        f = int(r.get("culprit") or 0) > 0
    except ValueError:
        f = False
    failed = (r.get("result") or "") != "FINISHED" and not s
    return rows.outcome(s, f, failed=failed and not f)


def mpiprv(r, a):
    """experiments/mpiprv/stretto_apps.sh: one attempt, whose own verdict (outcome: supported,
    unsupported) stays as script_outcome (rows.annotate); an attempt that cannot instrument or trace the racing calls
    is a tool failure ("cannot run", RULE.md Section 2.6)."""
    return "tool-failure" if r.get("outcome", "") != "supported" else "missed"


def none(r, a):
    return ""


def cell_key(r):
    """The cell a thread campaign's row is of, as experiments/thread_timeouts.sh names it."""
    if r.get("cell"):
        return ("native:" if r.get("tool") == "native" else "") + r["cell"]
    if r.get("args") is not None and r.get("np"):           # Uintah's series
        return "np%s%s" % (r["np"], "" if r["args"] == "mpi-scheduler" else r["args"].replace(" ", ""))
    return ""


RULES = {"stress": stress, "must": must, "corpus": corpus, "series": series, "traced": traced, "corrbench": corrbench,
         "bench": bench, "threadreplay": threadreplay, "mustvc": mustvc, "mpiprv": mpiprv, "none": none}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["annotate"])
    ap.add_argument("--kind", required=True, choices=sorted(RULES))
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--set", nargs="*", default=[], help="column=value for every row (host, cpus, jobs, timeout, seed, commit, run)")
    ap.add_argument("--must-column", default="must1112")
    ap.add_argument("--recorded-wrong", action="store_true", help="threadreplay: the replays' traced runs went wrong")
    ap.add_argument("--suite", default=None, help="the suite the rows are of (its programs' labels, amendment 22)")
    ap.add_argument("--cell-timeouts", default="", help='"cell=T ...": each row\'s T by its cell (amendment 20)')
    a = ap.parse_args(argv)
    extra = dict(kv.partition("=")[::2] for kv in a.set)
    extra.setdefault("host", rows.host())
    extra.setdefault("commit", os.environ.get("STRETTO_COMMIT", ""))
    per_cell = dict(kv.rsplit("=", 1) for kv in a.cell_timeouts.split())

    def outcome_of(r):
        if per_cell:
            r["timeout"] = per_cell.get(cell_key(r), "")
        return RULES[a.kind](r, a)
    n = rows.annotate(a.src, a.dst, extra, outcome_of=outcome_of)
    print("%d rows of %s appended to %s" % (n, a.src, a.dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
