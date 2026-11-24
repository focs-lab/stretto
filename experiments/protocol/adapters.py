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

usage: adapters.py annotate --kind KIND <script table> <block table> [--set column=value ...]
       [--program-column name|program] [--must-column must1112]
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


def stress(r, a):
    s = bool((r.get("first_failure") or "").strip())
    return rows.outcome(s, s)


def must(r, a):
    import score
    k = score.must_kind({"must": r.get(a.must_column) or ""})
    if k in ("RE", "HARNESS-ERROR", "CE", ""):
        return rows.outcome(False, False, failed=True)
    return rows.outcome(k == "TIMEOUT", k == "ERROR")


def corpus(r, a):
    import score
    if score.crashed(r):
        return rows.outcome(False, False, failed=True)
    return rows.outcome(score.observed_verdict(r) == "ERROR", score.verdict(r) == "ERROR")


def series(r, a):
    v = (r.get("result") or r.get("outcome") or "").lower()
    if v in ("ok", "pass"):
        return rows.outcome(False, False)
    if v in ("wrong", "truncate"):
        return rows.outcome(True, True)
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


RULES = {"stress": stress, "must": must, "corpus": corpus, "series": series, "traced": traced}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["annotate"])
    ap.add_argument("--kind", required=True, choices=sorted(RULES))
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--set", nargs="*", default=[], help="column=value for every row (host, cpus, jobs, timeout, seed, commit)")
    ap.add_argument("--must-column", default="must1112")
    a = ap.parse_args(argv)
    extra = dict(kv.partition("=")[::2] for kv in a.set)
    extra.setdefault("host", rows.host())
    extra.setdefault("commit", os.environ.get("STRETTO_COMMIT", ""))
    n = rows.annotate(a.src, a.dst, extra, outcome_of=lambda r: RULES[a.kind](r, a))
    print("%d rows of %s appended to %s" % (n, a.src, a.dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
