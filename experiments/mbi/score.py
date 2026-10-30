#!/usr/bin/env python3
"""Scoring of a corpus run (MBI or MPI-BugBench harness CSV), the one place that defines
what a verdict is. experiments/tables.py imports it; on its own it prints the summary.

A program's Stretto verdict is ERROR iff a finding of an error class is present:
  error classes   : 1a 1c 1d 2-buffering 2-collective 2-blocked 2-ready 3-local 3-race abort
  warning classes : 1b 1f probe-alt waitany-order probe-flip probe-unflip test-flip   (reported, never counted as errors)
Findings are qualified by the level that established them ("levels" column: observed:2-blocked,
L1:1f, L1+SP:1d, ...), which separates what any observational tool sees in the run from
what Stretto predicts about other runs:
  observed  : observed:1c, observed:2-blocked, observed:2-collective, observed:2-ready,
              observed:3-local, observed:abort (the run itself; 2-ready when it issued a ready
              send before its receive was posted; 3-local only from runs traced with the buffer
              guard, STRETTO_GUARD=1; abort when a rank stopped outside MPI before MPI_Finalize)
  predicted : L1:1a (a mismatch certain from the trace), L1+SP:1a/1c/1d (consequences
              under the single-path assumption), L1:1c/1d (a deadlock contained in the horizon,
              or, with read bounds, a consequence whose witness respects them:
              docs/firstread.md), observed:2-buffering (the finished run would deadlock below a
              threshold)
  L1 only   : L1:1a/1c/1d, the error consequences that hold without the single-path assumption
A program whose analysis crashed has no verdict (CRASH), and neither has a run that the harness
itself could not complete or judge (HARNESS-ERROR: no trace, a tool that did not start, a
container that failed): a confusion matrix counts such rows apart, as neither a positive nor a
negative, so that a crash on a correct program never reads as a true negative. Scoring a table
with a crash fails (score.main exits with status 1; experiments/tables.py too, after writing).
usage: score.py results.csv [--md]
"""
from __future__ import annotations
import collections
import csv
import os
import sys
from typing import Dict, Iterable, List

ERROR = {"1a", "1c", "1d", "2-buffering", "2-collective", "2-blocked", "2-ready", "3-local", "3-race", "abort",
         "2-thread-collective", "2-thread-wait", "2-thread-finalize", "2-thread-level"}   # the last four: docs/threads.md
WARN = {"1b", "1f", "probe-alt", "waitany-order", "probe-flip", "probe-unflip", "test-flip"}
OBSERVED = {"observed:1c", "observed:2-blocked", "observed:2-collective", "observed:2-ready", "observed:3-local",
            "observed:abort", "observed:2-thread-collective", "observed:2-thread-wait", "observed:2-thread-finalize",
            "observed:2-thread-level"}
PREDICTED = {"L1:1a", "L1:1c", "L1:1d", "L1+SP:1a", "L1+SP:1c", "L1+SP:1d", "observed:2-buffering"}   # 2-buffering on finished runs only
FLAGGED = {"L1:1a", "L1:1b", "L1:1f"}            # a wildcard alternative found from the trace alone
CERTIFIED = {"L1:1a", "L1:1c", "L1:1d", "L1+SP:1a", "L1+SP:1c", "L1+SP:1d"}   # an error consequence of a predicted alternative
L1_ONLY = {"L1:1a", "L1:1c", "L1:1d"}            # an error consequence without the single-path assumption
CRASH = "CRASH"                  # the analysis did not run to its end
HARNESS = "HARNESS-ERROR"        # the run, or the harness around a tool, failed: the tool gave no verdict
NO_VERDICT = (CRASH, HARNESS)
# every row a confusion matrix met without a verdict, (verdict, name): the caller fails on a crash
SEEN_WITHOUT_VERDICT: set = set()


def rows(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return list(csv.DictReader(fh))


def compiled(rs: Iterable[dict]) -> List[dict]:
    return [r for r in rs if r.get("build") != "CE"]


def levels(r: dict) -> set:
    return set(r.get("levels", "").split())


def crashed(r: dict) -> bool:
    """The analysis of the row crashed: the harness wrote CRASH into its analysis column (or, in
    tables written before the column, "analysis failed" into its notes)."""
    return r.get("analysis") == CRASH or analysis_failed(r)


def verdict(r: dict) -> str:
    """ERROR iff any error-class finding (observed or predicted); CRASH if the analysis crashed."""
    if crashed(r):
        return CRASH
    return "ERROR" if set(r.get("classes", "").split()) & ERROR else "OK"


def finished(r: dict) -> bool:
    return (r.get("run") or "").startswith("FINISHED")


def observed_verdict(r: dict) -> str:
    """What the run itself showed. A 2-buffering finding on a run that did not finish is the
    hang itself, so it is observed, not predicted."""
    if crashed(r):
        return CRASH
    lv = levels(r)
    if "observed:2-buffering" in lv and not finished(r):
        return "ERROR"
    return "ERROR" if lv & OBSERVED else "OK"


def predicted_verdict(r: dict) -> str:
    """Claims about other runs: 1a, L1+SP 1c/1d, and 2-buffering on a run that finished."""
    if crashed(r):
        return CRASH
    lv = levels(r)
    if not finished(r):
        lv = lv - {"observed:2-buffering"}
    return "ERROR" if lv & PREDICTED else "OK"


def l1_verdict(r: dict) -> str:
    """Claims about other runs that hold for every program consistent with the trace (and,
    with read bounds, with them): 1a, and 1c/1d at level L1."""
    if crashed(r):
        return CRASH
    return "ERROR" if levels(r) & L1_ONLY else "OK"


def analysis_failed(r: dict) -> bool:
    return (r.get("notes") or "").startswith("analysis failed")


def flagged(r: dict) -> bool:
    return bool(levels(r) & FLAGGED)


def certified(r: dict) -> bool:
    return bool(levels(r) & CERTIFIED)


def must_kind(r: dict) -> str:
    """OK | ERROR | TIMEOUT | RE | CE | HARNESS-ERROR | '' from the MUST column ("MUST:ERROR:..."
    etc.; scripts/must_run.sh)."""
    m = r.get("must", "") or ""
    m = m[5:] if m.startswith("MUST:") else m
    return m.split(":")[0]


def must_verdict(r: dict) -> str:
    """ERROR iff MUST reported one; HARNESS-ERROR when the harness had no MUST run to judge
    (docker failed, the container was killed before the timeout): no verdict."""
    k = must_kind(r)
    return HARNESS if k == HARNESS else "ERROR" if k == "ERROR" else "OK"


def must_verdict_hangs(r: dict) -> str:
    """MUST with a hang or a failed run the harness observed credited as an error, as
    Stretto's are: a run the harness killed (a blocked rank, 2-blocked) and a rank that
    stopped before MPI_Finalize (abort). RE is a MUST run that ended without its report; a
    harness error is no MUST run at all, and no verdict."""
    k = must_kind(r)
    return HARNESS if k == HARNESS else "ERROR" if k in ("ERROR", "TIMEOUT", "RE") else "OK"


# Other MUST versions and configurations, each in a column of its own in the corpus table
# (experiments/mbi/run_must.py --column): MUST 1.11.2, and MUST 1.11.2 with its ThreadSanitizer
# analysis, where a data race counts as an error (scripts/must_run.sh). The column "must" stays 1.9.2's.
MUST_COLUMNS = (("must1112", "MUST 1.11.2"), ("must1112tsan", "MUST 1.11.2 with ThreadSanitizer"))


def must_column_judge(col: str, hangs: bool = False):
    """must_verdict, or must_verdict_hangs with hangs=True, of the MUST column col."""
    def judge(r: dict) -> str:
        return (must_verdict_hangs if hangs else must_verdict)({"must": r.get(col) or ""})
    return judge


def confusion(rs: Iterable[dict], judge) -> Dict[str, float]:
    """TP/FP/FN/TN of judge(row) in {ERROR, OK} against the label, with precision, recall, F1.
    A row judged CRASH or HARNESS-ERROR is in none of the four: it is counted under crash or
    harness, and remembered in SEEN_WITHOUT_VERDICT."""
    c = collections.Counter()
    for r in rs:
        e, v = r.get("expect"), judge(r)
        if v in NO_VERDICT:
            c["crash" if v == CRASH else "harness"] += 1
            SEEN_WITHOUT_VERDICT.add((v, r.get("name") or r.get("program") or "?"))
            continue
        c["tp" if e == "ERROR" and v == "ERROR" else "fn" if e == "ERROR" else "fp" if v == "ERROR" else "tn"] += 1
    tp, fp, fn, tn = c["tp"], c["fp"], c["fn"], c["tn"]
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": prec, "recall": rec, "f1": f1,
            "crash": c["crash"], "harness": c["harness"]}


def crashes_seen() -> List[str]:
    """The rows scored so far whose analysis crashed, sorted."""
    return sorted(name for v, name in SEEN_WITHOUT_VERDICT if v == CRASH)


def noise(rs: Iterable[dict]) -> Dict[str, float]:
    """Reports per program (the SC-W'24 noise view): findings per program, mean and max, and
    programs with any warning."""
    rs = list(rs)
    n = [int(r.get("findings") or 0) for r in rs]
    return {"programs": len(rs), "mean_findings": sum(n) / len(n) if n else 0.0, "max_findings": max(n) if n else 0,
            "with_warnings": sum(1 for r in rs if set(r.get("classes", "").split()) & WARN)}


def replay_rows(csv_path: str) -> List[dict]:
    """Per-finding replay results (replays.csv next to the corpus table), empty if absent."""
    return rows(os.path.join(os.path.dirname(os.path.abspath(csv_path)), "replays.csv"))


def main(path: str, md: bool = False) -> int:
    rs = compiled(rows(path))
    for name, judge in (("union", verdict), ("observed", observed_verdict), ("predicted", predicted_verdict), ("L1 only", l1_verdict)):
        c = confusion(rs, judge)
        print(f"{name:<9} TP={c['tp']} FP={c['fp']} FN={c['fn']} TN={c['tn']} precision={c['precision']:.2f} recall={c['recall']:.2f} F1={c['f1']:.2f}")
    if any(r.get("must") for r in rs):
        c = confusion(rs, must_verdict)
        kinds = collections.Counter(must_kind(r) for r in rs)
        print(f"MUST      TP={c['tp']} FP={c['fp']} FN={c['fn']} TN={c['tn']} F1={c['f1']:.2f}  verdict kinds {dict(kinds)}")
        c = confusion(rs, must_verdict_hangs)
        print(f"MUST+hang TP={c['tp']} FP={c['fp']} FN={c['fn']} TN={c['tn']} F1={c['f1']:.2f}  (a timeout counts as an error, as for Stretto)")
    failed = [r["name"] for r in rs if crashed(r)]
    if failed:
        print(f"CRASH: the analysis failed on {len(failed)} program(s), left out of every row above: {', '.join(failed)}")
    race = [r for r in rs if r.get("detail") == "MessageRace"]
    if race:
        clean = [r for r in race if r.get("run") == "FINISHED"]
        print(f"message-race programs={len(race)}: clean runs={len(clean)}, flagged={sum(map(flagged, race))} "
              f"(clean {sum(map(flagged, clean))}/{len(clean)}), certified={sum(map(certified, race))} "
              f"(clean {sum(map(certified, clean))}/{len(clean)})")
    ok = [r for r in rs if r.get("expect") == "OK"]
    nz = noise(ok)
    print(f"correct programs={len(ok)}: with warnings={nz['with_warnings']}, findings per program mean={nz['mean_findings']:.1f} max={nz['max_findings']}")
    rp = replay_rows(path)
    if rp:
        c = collections.Counter((r["cls"], r["verdict"]) for r in rp)
        print("replays per finding: " + ", ".join(f"{k[0]} {k[1]}={v}" for k, v in sorted(c.items())))
    if md:
        print("\n| label | detail | run | classes | count |\n|---|---|---|---|---|")
        c = collections.Counter((r["expect"], r["detail"], r["run"].split(":")[0], r["classes"]) for r in rs)
        for k, v in sorted(c.items()):
            print(f"| {k[0]} | {k[1]} | {k[2]} | {k[3]} | {v} |")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--md" in sys.argv))
