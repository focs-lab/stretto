#!/usr/bin/env python3
"""Compose docs/results.md from the experiment CSVs. Re-run after any experiment."""
from __future__ import annotations
import collections
import csv
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RES = os.path.join(ROOT, "experiments", "results")
ERROR = {"1a", "1c", "1d", "2-buffering", "2-collective", "2-blocked"}


def rows(path):
    return list(csv.DictReader(open(path))) if os.path.exists(path) else []


def verdict(classes):
    return "ERROR" if set(classes.split()) & ERROR else "OK"


def flagged(r):
    """A wildcard alternative found from the trace alone (L1)."""
    return bool(set(r.get("levels", "").split()) & {"L1:1a", "L1:1b", "L1:1f"})


def certified(r):
    """An error consequence of a predicted alternative: a certain mismatch at L1, or an orphan
    or deadlock under the single-path assumption. Observed findings do not count."""
    return bool(set(r.get("levels", "").split()) & {"L1:1a", "L1+SP:1a", "L1+SP:1c", "L1+SP:1d"})


def e1(name, path):
    rs = rows(path)
    if not rs:
        return f"### {name}\n\n_no results_\n"
    ce = [r for r in rs if r.get("build") == "CE"]
    rs = [r for r in rs if r.get("build") != "CE"]
    tp = sum(1 for r in rs if r["expect"] == "ERROR" and verdict(r["classes"]) == "ERROR")
    fp = sum(1 for r in rs if r["expect"] == "OK" and verdict(r["classes"]) == "ERROR")
    fn = sum(1 for r in rs if r["expect"] == "ERROR" and verdict(r["classes"]) == "OK")
    tn = sum(1 for r in rs if r["expect"] == "OK" and verdict(r["classes"]) == "OK")
    prec = tp / (tp + fp) if tp + fp else 0
    rec = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
    skipped = f" Not compiled with Open MPI 4.1.6 and left out: {len(ce)} ({', '.join(sorted(r['name'] for r in ce))})." if ce else ""
    out = [f"### {name}", "", f"Programs: {len(rs)}.{skipped} Rematch verdict = ERROR iff a finding of class 1a/1c/1d/2-* is present.", "",
           "| | TP | FP | FN | TN | precision | recall | F1 |", "|---|---|---|---|---|---|---|---|",
           f"| Rematch | {tp} | {fp} | {fn} | {tn} | {prec:.2f} | {rec:.2f} | {f1:.2f} |"]
    if any(r.get("must") for r in rs):
        mtp = sum(1 for r in rs if r["expect"] == "ERROR" and "ERROR" in r.get("must", ""))
        mfp = sum(1 for r in rs if r["expect"] == "OK" and "ERROR" in r.get("must", ""))
        mfn = sum(1 for r in rs if r["expect"] == "ERROR" and "ERROR" not in r.get("must", ""))
        mtn = sum(1 for r in rs if r["expect"] == "OK" and "ERROR" not in r.get("must", ""))
        mp = mtp / (mtp + mfp) if mtp + mfp else 0
        mr = mtp / (mtp + mfn) if mtp + mfn else 0
        mf = 2 * mp * mr / (mp + mr) if mp + mr else 0
        out.append(f"| MUST 1.9.2 (same source, its own run under MPICH) | {mtp} | {mfp} | {mfn} | {mtn} | {mp:.2f} | {mr:.2f} | {mf:.2f} |")
        inv = [r for r in rs if r["expect"] == "ERROR" and verdict(r["classes"]) == "ERROR" and "ERROR" not in r.get("must", "")]
        conf = [r for r in inv if r.get("confirmed") and int(r["confirmed"]) > 0]
        out += ["", f"Labelled errors Rematch reports and MUST does not on the same program: **{len(inv)}**, of which replay-confirmed (a pinned rerun hangs or raises the MPI error): **{len(conf)}**."]
    race = [r for r in rs if r.get("detail") == "MessageRace"]
    if race:
        clean = [r for r in race if r["run"] == "FINISHED"]
        flag = [r for r in race if flagged(r)]
        cert = [r for r in race if certified(r)]
        conf = [r for r in race if r.get("confirmed") and int(r["confirmed"]) > 0]
        out += ["", "Message-race programs (label `MessageRace`):", "",
                "| total | clean observed runs | racing receive flagged (L1) | consequence certified (L1+SP) | replay-confirmed | observed deadlock/abort |", "|---|---|---|---|---|---|",
                f"| {len(race)} | {len(clean)} | {len(flag)} (clean: {sum(1 for r in clean if r in flag)}/{len(clean)}) | {len(cert)} (clean: {sum(1 for r in clean if r in cert)}/{len(clean)}) | {len(conf)} | {len(race) - len(clean)} |"]
    wild = [r for r in rs if r["expect"] == "ERROR" and (r.get("anysrc") == "True" or r.get("anytag") == "True") and r.get("detail") != "MessageRace"]
    if wild:
        clean = [r for r in wild if r["run"] == "FINISHED"]
        flag = [r for r in wild if flagged(r)]
        err = [r for r in wild if verdict(r["classes"]) == "ERROR"]
        conf = [r for r in wild if r.get("confirmed") and int(r["confirmed"]) > 0]
        mus = [r for r in wild if "ERROR" in r.get("must", "")]
        out += ["", "Labelled-error programs that use a wildcard (`MPI_ANY_SOURCE` or `MPI_ANY_TAG`):", "",
                "| total | clean observed runs | wildcard alternative flagged (L1) | error reported | replay-confirmed | MUST error |", "|---|---|---|---|---|---|",
                f"| {len(wild)} | {len(clean)} | {len(flag)} | {len(err)} | {len(conf)} | {len(mus)} |"]
    c = collections.Counter((r["expect"], r["detail"], r["run"].split(":")[0], r["classes"], (r.get("must", "").split(":") + [""])[1]) for r in rs)
    out += ["", "| label | detail | observed run | Rematch classes | MUST | programs |", "|---|---|---|---|---|---|"]
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
    out = ["| traces | observed runs infeasible at B=0 (deadlock on a rendezvous implementation) | feasible alternatives | feasible at B=0 | at B=4096 | at B=inf | B-dependent |", "|---|---|---|---|---|---|---|",
           f"| {len(obs)} | {sum(1 for r in obs if r['B0'] == 'False')} | {len(alt)} | {sum(1 for r in alt if r['B0'] == 'True')} | {sum(1 for r in alt if r['B4096'] == 'True')} | {sum(1 for r in alt if r['Binf'] == 'True')} | {len(dep)} |"]
    if dep:
        out += ["", "B-dependent alternatives:", "", "| trace | alternative | profile |", "|---|---|---|"]
        out += [f"| {r['trace']} | {r['what']} | {r['profile']} |" for r in dep[:20]]
    return "\n".join(out) + "\n"


def e6():
    rs = rows(os.path.join(RES, "e6_checker_vs_explorer.csv"))
    if not rs:
        return "_no results_\n"
    c = collections.Counter(r["kind"] for r in rs)
    tc = sum(float(r["t_checker_ms"]) for r in rs)
    te = sum(float(r["t_explorer_ms"]) for r in rs)
    return (f"| pairs (alternative, B) | agree | unsound (checker feasible, explorer unreachable) | incomplete | skipped | checker time | explorer time |\n|---|---|---|---|---|---|---|\n"
            f"| {len(rs)} | {c['agree']} | {c['UNSOUND']} | {c['incomplete']} | {c['skip']} | {tc:.0f} ms | {te:.0f} ms |\n")


def e4():
    rs = rows(os.path.join(RES, "e4", "e4.csv"))
    if not rs:
        return "_no results_\n"
    out = ["Run and traced times were measured on the development host (4-rank rows on 8 Sep,",
           "16-rank rows on 15 Sep; the 16-rank lu.A row was not measured before the lane was",
           "stopped). Analysis times were re-measured with the final analysis code on the second",
           "machine, which is shared and gives indicative times only.", "",
           "| benchmark | class | np | plain (s) | traced (s) | slowdown | trace (MB) | events | wildcards | analysis (s) | findings |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rs:
        out.append(f"| {r['benchmark']} | {r['class']} | {r['np']} | {float(r['plain_s']):.2f} | {float(r['traced_s']):.2f} | {r['slowdown']} | {r['trace_MB']} | {r['events']} | {r['wildcards']} | {float(r['analysis_s']):.1f} | {r['findings']} {r['classes']} |")
    return "\n".join(out) + "\n"


def e5():
    p = os.path.join(RES, "amg", "report8.txt")
    if not os.path.exists(p):
        return "_no results_\n"
    lines = open(p).read().splitlines()
    head = lines[0] if lines else ""
    cls = collections.Counter(l.split("]")[0].strip("[ ") for l in lines if l.startswith("["))
    tm = ""
    e = os.path.join(RES, "amg", "analyze8.err")
    if os.path.exists(e):
        tm = " ".join(l for l in open(e).read().splitlines() if "analysis" in l)
    note = os.path.join(RES, "amg", "analyze8.note")
    if os.path.exists(note):
        tm = " ".join(open(note).read().split())
    conf = os.path.join(RES, "amg", "confirm8.txt")
    c = open(conf).read() if os.path.exists(conf) else ""
    return f"AMG (LLNL), 8 ranks, `-P 2 2 2 -n 16 16 16 -problem 1`, traced with Open MPI.\n\n{head}\n\nFindings by class: {dict(cls)}. {tm}\n\n" + (f"Replay confirmation:\n\n```\n{c}\n```\n" if c else "")


def e3():
    f = os.path.join(RES, "e3.md")
    if os.path.exists(f):
        return open(f).read() + "\n"
    try:
        out = subprocess.run([sys.executable, os.path.join(ROOT, "experiments", "e3_levels.py")], capture_output=True, text=True, timeout=600).stdout
    except Exception as ex:  # noqa: BLE001
        out = f"_failed: {ex}_"
    return out + "\n"


def main():
    doc = ["# Results", "", "Generated by `experiments/tables.py`. Two machines with the same userland (Ubuntu 24.04,",
           "Open MPI 4.1.6, MPICH 4.2.0, gcc 13, Python 3.12): the development host (112 threads) for the",
           "MBI corpus, AMG tracing and NPB overhead, and a second, shared machine (80 threads, the",
           "container in `experiments/container/`, 24 processors) for MPI-BugBench, E2, E6 and every",
           "analysis time. MUST is 1.9.2 with MPICH in MPI-BugBench's own container on both.",
           "", "## E1: predictions vs benchmark labels", "",
           e1("MPI Bugs Initiative (MBI): message-race, tag-matching, probe and buffering programs", os.path.join(RES, "mbi", "mbi.csv")),
           e1("MPI-BugBench level 2: point-to-point and collective call-ordering, missing-call, local-concurrency and correct programs", os.path.join(RES, "mbb", "mbb.csv")),
           "## E2: buffering ablation", "", e2(),
           "## E3: level ablation", "", e3(),
           "## E4: tracer overhead and analysis cost (NPB 3.4.3, MPICH build)", "", e4(),
           "## E5: real application (AMG)", "", e5(),
           "## E6: polynomial checker vs exhaustive explorer", "", e6()]
    path = os.path.join(ROOT, "docs", "results.md")
    open(path, "w").write("\n".join(doc))
    print("wrote", path)


if __name__ == "__main__":
    main()
