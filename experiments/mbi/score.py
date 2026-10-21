#!/usr/bin/env python3
"""Scoring of a corpus run (MBI or MPI-BugBench harness CSV), the one place that defines
what a verdict is. experiments/tables.py imports it; on its own it prints the summary.

A program's Stretto verdict is ERROR iff a finding of an error class is present:
  error classes   : 1a 1c 1d 2-buffering 2-collective 2-blocked 3-local abort
  warning classes : 1b 1f probe-alt waitany-order   (reported, never counted as errors)
Findings are qualified by the level that established them ("levels" column: observed:2-blocked,
L1:1f, L1+SP:1d, ...), which separates what any observational tool sees in the run from
what Stretto predicts about other runs:
  observed  : observed:1c, observed:2-blocked, observed:2-collective, observed:3-local,
              observed:abort (the run itself; 3-local only from runs traced with the buffer
              guard, STRETTO_GUARD=1; abort when a rank stopped outside MPI before MPI_Finalize)
  predicted : L1:1a (a mismatch certain from the trace), L1+SP:1a/1c/1d (consequences
              under the single-path assumption), observed:2-buffering (the finished run
              would deadlock below a threshold)
usage: score.py results.csv [--md]
"""
from __future__ import annotations
import collections
import csv
import os
import sys
from typing import Dict, Iterable, List

ERROR = {"1a", "1c", "1d", "2-buffering", "2-collective", "2-blocked", "3-local", "abort"}
WARN = {"1b", "1f", "probe-alt", "waitany-order"}
OBSERVED = {"observed:1c", "observed:2-blocked", "observed:2-collective", "observed:3-local", "observed:abort"}
PREDICTED = {"L1:1a", "L1+SP:1a", "L1+SP:1c", "L1+SP:1d", "observed:2-buffering"}   # 2-buffering on finished runs only
FLAGGED = {"L1:1a", "L1:1b", "L1:1f"}            # a wildcard alternative found from the trace alone
CERTIFIED = {"L1:1a", "L1+SP:1a", "L1+SP:1c", "L1+SP:1d"}   # an error consequence of a predicted alternative


def rows(path: str) -> List[dict]:
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def compiled(rs: Iterable[dict]) -> List[dict]:
    return [r for r in rs if r.get("build") != "CE"]


def levels(r: dict) -> set:
    return set(r.get("levels", "").split())


def verdict(r: dict) -> str:
    """ERROR iff any error-class finding (observed or predicted)."""
    return "ERROR" if set(r.get("classes", "").split()) & ERROR else "OK"


def finished(r: dict) -> bool:
    return (r.get("run") or "").startswith("FINISHED")


def observed_verdict(r: dict) -> str:
    """What the run itself showed. A 2-buffering finding on a run that did not finish is the
    hang itself, so it is observed, not predicted."""
    lv = levels(r)
    if "observed:2-buffering" in lv and not finished(r):
        return "ERROR"
    return "ERROR" if lv & OBSERVED else "OK"


def predicted_verdict(r: dict) -> str:
    """Claims about other runs: 1a, L1+SP 1c/1d, and 2-buffering on a run that finished."""
    lv = levels(r)
    if not finished(r):
        lv = lv - {"observed:2-buffering"}
    return "ERROR" if lv & PREDICTED else "OK"


def analysis_failed(r: dict) -> bool:
    return (r.get("notes") or "").startswith("analysis failed")


def flagged(r: dict) -> bool:
    return bool(levels(r) & FLAGGED)


def certified(r: dict) -> bool:
    return bool(levels(r) & CERTIFIED)


def must_kind(r: dict) -> str:
    """OK | ERROR | TIMEOUT | RE | CE | '' from the MUST column ("MUST:ERROR:..." etc.)."""
    m = r.get("must", "") or ""
    m = m[5:] if m.startswith("MUST:") else m
    return m.split(":")[0]


def must_verdict(r: dict) -> str:
    return "ERROR" if must_kind(r) == "ERROR" else "OK"


def must_verdict_hangs(r: dict) -> str:
    """MUST with a hang or a failed run the harness observed credited as an error, as
    Stretto's are: a run the harness killed (a blocked rank, 2-blocked) and a rank that
    stopped before MPI_Finalize (abort). RE is a MUST run that ended without its report."""
    return "ERROR" if must_kind(r) in ("ERROR", "TIMEOUT", "RE") else "OK"


def confusion(rs: Iterable[dict], judge) -> Dict[str, float]:
    """TP/FP/FN/TN of judge(row) in {ERROR, OK} against the label, with precision, recall, F1."""
    c = collections.Counter()
    for r in rs:
        e, v = r.get("expect"), judge(r)
        c["tp" if e == "ERROR" and v == "ERROR" else "fn" if e == "ERROR" else "fp" if v == "ERROR" else "tn"] += 1
    tp, fp, fn, tn = c["tp"], c["fp"], c["fn"], c["tn"]
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": prec, "recall": rec, "f1": f1}


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
    for name, judge in (("union", verdict), ("observed", observed_verdict), ("predicted", predicted_verdict)):
        c = confusion(rs, judge)
        print(f"{name:<9} TP={c['tp']} FP={c['fp']} FN={c['fn']} TN={c['tn']} precision={c['precision']:.2f} recall={c['recall']:.2f} F1={c['f1']:.2f}")
    if any(r.get("must") for r in rs):
        c = confusion(rs, must_verdict)
        kinds = collections.Counter(must_kind(r) for r in rs)
        print(f"MUST      TP={c['tp']} FP={c['fp']} FN={c['fn']} TN={c['tn']} F1={c['f1']:.2f}  verdict kinds {dict(kinds)}")
        c = confusion(rs, must_verdict_hangs)
        print(f"MUST+hang TP={c['tp']} FP={c['fp']} FN={c['fn']} TN={c['tn']} F1={c['f1']:.2f}  (a timeout counts as an error, as for Stretto)")
    failed = [r["name"] for r in rs if analysis_failed(r)]
    if failed:
        print(f"analysis failed (counted as no error): {len(failed)}: {', '.join(failed)}")
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--md" in sys.argv))
