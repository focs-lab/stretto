#!/usr/bin/env python3
"""Check the paper's claims against the numbers experiments/tables.py wrote.

The paper takes every number of its evaluation from paper/generated/numbers.tex. This script
reads that file, checks each claim the text makes about those numbers (that no accepted
alternative is unsound, that every replay matched its pins, that no stress run hit the HYPRE
hang, ...), and with --against compares every number of the chosen research questions with an
earlier copy of the file, such as the one shipped with the paper.

Only the numbers the inputs fix (the corpora's sizes and labels, how many runs, samples and
repetitions the scripts make, the buffering thresholds of the MPI library) have to equal the
paper's. Every count taken from recorded runs depends on their schedule: which wildcard
receive matched which send, which programs hang under the harness, how many stress runs a
race needs, how many HYPRE traces record an exposing order. Those and every time vary from
run to run; the claims are what has to hold.

usage: artifact/claims.py [--rq 1 2 ...] [--against OLD_NUMBERS_TEX] [NUMBERS_TEX]
exit status: 0 if every checked claim holds, 1 otherwise.
"""
from __future__ import annotations
import argparse
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NUMBERS = os.path.join(ROOT, "paper", "generated", "numbers.tex")

# which research question a macro belongs to, by name; the first match wins
RQ_OF = [
    (1, r"^nEsix|^nMBIReplay|^nAMGsample|^nNeksample|^nPin"),
    (2, r"^(MBI|MBB)fone|^n(MBI|MBB)(tp|fn|fp|progs|notBuilt|correct|errors|race|Stress|Clean|OnlyStretto|LC)|^n?Guard|^n?Reps"),
    (3, r"^nEtwo|^B[a-z]+OMPI$"),
    (4, r"^n(MBI|MBB)(one|sp|obs)[A-Z]"),
    (5, r"^TraceOverhead|^LuAnalysis|^LuEvents|^AMGtime|^Nek(GB|Minutes|PerAlt|Records)|^nEfour"),
    (6, r"^nHypre|^BHypre|^nAMG|^nNek|^nDeal"),
]
# numbers the inputs fix: a difference there is unexpected; every other number is a time or a
# count taken from recorded runs, which depends on their schedule
FIXED = re.compile(r"^n(MBI|MBB)(progs|notBuilt|correct|errors|race)$|^B[a-z]+OMPI$|^n(AMG|Nek)sample$|^nEfourReps$"
                   r"|^nMBIStressRuns$|^nHypre(Fixed)?(Traced|(Four|Eight)(Local)?Runs)$|^nHypreStressRuns$"
                   r"|^nMBBLC$|^nGuardLC(OMPI|MPICH)built$|^nGuardCases$|^nGuardAppRuns$"
                   r"|^nReps(Runs|Progs|Errors)$|^nDeal(Fixed)?Traced$|^nDealRanks$|^nDealStress(Plain|Delay)?Runs$")


def load(path: str) -> dict:
    out = {}
    for name, value in re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{(.*?)\\xspace\}", open(path).read()):
        out[name] = value
    return out


def value(text: str):
    """A macro's text as a number when it is one ('3\\,263' -> 3263, '0.97' -> 0.97)."""
    t = text.replace("\\,", "")
    try:
        return int(t)
    except ValueError:
        try:
            return float(t)
        except ValueError:
            return text


def claims(n: dict) -> list:
    """(research question, claim, holds) for every claim whose numbers are present."""
    v = {k: value(t) for k, t in n.items()}
    out = []

    def claim(rq, text, names, test):
        if all(k in v for k in names):
            out.append((rq, text, bool(test(*[v[k] for k in names]))))
        else:
            out.append((rq, text + " (numbers missing)", None))

    claim(1, "no accepted alternative is unsound against the exhaustive explorer",
          ["nEsixUnsound"], lambda u: u == 0)
    claim(1, "the explorer confirms every contained deadlock the check reports",
          ["nEsixLdeadUnsound"], lambda u: u == 0)
    claim(1, "no MBI replay contradicts its finding",
          ["nMBIReplayNotConfirmed"], lambda u: u == 0)
    claim(1, "every inconclusive MBI replay belongs to a racing loop labelled erroneous",
          ["nMBIReplayInconclusive", "nMBIReplayInconclusiveLoop"], lambda i, l: i == l)
    for app in ("AMG", "Nek"):
        claim(1, f"{app} sample: every replay matched its pins and finished, every control finished",
              [f"n{app}sample", f"n{app}sampleMatched", f"n{app}sampleFinish", f"n{app}sampleControls"],
              lambda s, m, f, c: s > 0 and s == m == f == c)
    claim(1, "every application replay or control that hung or finished matched its pins",
          ["nPinRuns", "nPinUnmatchedEnded"], lambda r, e: r > 0 and e == 0)
    claim(2, "no false positive on MBI", ["nMBIfp"], lambda x: x == 0)
    claim(2, "every MBI error Stretto finds was shown by the run, or predicted by a consequence or a threshold",
          ["nMBItpunion", "nMBItpobserved", "nMBItpPredSP", "nMBItpPredBuf"], lambda u, o, s, b: u == o + s + b)
    claim(2, "Stretto's F1 on MBI is above MUST's, under both scorings, and above stress testing",
          ["MBIfoneunion", "MBIfoneMUST", "MBIfoneMUSTsym", "MBIfonestress"],
          lambda s, m, ms, st: s > max(m, ms, st))
    claim(2, "every MBI error stress testing never exposes is predicted by Stretto from one run",
          ["nMBIStressErr", "nMBIStressFound", "nMBIStressPredMissed"], lambda e, f, p: e - f == p)
    claim(2, "on MPI-BugBench, Stretto's F1 is at least MUST's",
          ["MBBfoneunion", "MBBfoneMUST"], lambda s, m: s >= m)
    claim(2, "with the buffer guard, every MPI-BugBench local-concurrency program gets 3-local and nothing else changes",
          ["nMBBLC", "nMBBLCguard", "nMBBfp", "nMBBfpguard", "MBIfoneunion", "MBIfoneguard"],
          lambda lc, g, fp, fpg, s, sg: lc > 0 and lc == g and fp == fpg and s == sg)
    claim(2, "under MPICH the guard finds every local-concurrency program that builds",
          ["nGuardLCMPICHbuilt", "nGuardLCMPICHfound"], lambda b, f: b > 0 and b == f)
    claim(2, "the guard records nothing on NPB LU, HYPRE and AMG, and every run finishes",
          ["nGuardAppRuns", "nGuardAppBad"], lambda r, b: r > 0 and b == 0)
    claim(2, "repeated MBI runs: no false positive in any run", ["nRepsRuns", "nRepsFPever"], lambda n, f: n > 1 and f == 0)
    claim(2, "repeated MBI runs: no labelled error the run shows more often than Stretto reports it",
          ["nRepsUnionBelowObs"], lambda b: b == 0)
    claim(2, "repeated MBI runs: every clean run Stretto predicts nothing from is of a value-check program (L2)",
          ["nRepsCleanMissedNotLtwo"], lambda x: x == 0)
    claim(3, "every recorded run infeasible at B=0 needs no threshold, finished, or did not finish",
          ["nEtwoObsBuf", "nEtwoObsBufNever", "nEtwoObsBufFinished", "nEtwoObsBufStopped"], lambda a, n, f, s: a == n + f + s)
    claim(3, "the runs that finished and need a threshold are the ones replayed",
          ["nEtwoObsBufFinished", "nEtwoReplayPrograms"], lambda f, p: f == p)
    claim(3, "no buffering replay goes against the prediction", ["nEtwoReplayBad"], lambda b: b == 0)
    claim(3, "with the default limits every replayed program finishes again",
          ["nEtwoReplayPrograms", "nEtwoReplayDefaultOK"], lambda p, ok: p > 0 and p == ok)
    claim(3, "below the predicted bound every testable program hangs",
          ["nEtwoReplayBelowTestable", "nEtwoReplayBelowOK"], lambda t, ok: t == ok)
    claim(3, "at the predicted bound every program finishes",
          ["nEtwoReplayAt", "nEtwoReplayAtOK"], lambda a, ok: a == ok)
    claim(6, "HYPRE 2.10.1: every cross alternative hangs when replayed",
          ["nHypreCrossAlts", "nHypreCrossHang"], lambda a, h: a > 0 and a == h)
    for p in ("Four", "Eight"):
        claim(6, f"HYPRE 2.10.1, {p.lower()} processes: every cross replay matched its pins, every control finished",
              [f"nHypre{p}Alts", f"nHypre{p}Matched", f"nHypre{p}Controls"], lambda a, m, c: a == m == c)
        claim(6, f"HYPRE 2.10.1, {p.lower()} processes: no same alternative hangs",
              [f"nHypre{p}LocalHang"], lambda h: h == 0)
    claim(6, "HYPRE 2.11.0, the fix: no trace has a cross alternative", ["nHypreFixedCrossAlts"], lambda a: a == 0)
    claim(6, "stress testing never hits the HYPRE hang", ["nHypreStressRuns", "nHypreStressHang"],
          lambda r, h: r > 0 and h == 0)
    claim(6, "deal.II 9.3.0-rc1: every cross alternative is replayed, finishes and prints wrong values",
          ["nDealAlts", "nDealReplayed", "nDealFinished", "nDealWrong"], lambda a, r, f, w: a > 0 and a == r == f == w)
    claim(6, "deal.II 9.3.0, the fix: no trace has a cross alternative", ["nDealFixedTraced", "nDealFixedAlts"],
          lambda t, a: t > 0 and a == 0)
    return out


def rq_of(name: str):
    return next((rq for rq, pat in RQ_OF if re.search(pat, name)), None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("numbers", nargs="?", default=NUMBERS)
    ap.add_argument("--rq", type=int, nargs="*", default=[1, 2, 3, 4, 5, 6])
    ap.add_argument("--against", help="an earlier numbers.tex to compare with")
    a = ap.parse_args()
    n = load(a.numbers)
    failed = 0
    print(f"claims, from {os.path.relpath(a.numbers, ROOT)}:")
    for rq, text, holds in claims(n):
        if rq in a.rq:
            print(f"  RQ{rq} {'holds ' if holds else 'FAILS ' if holds is False else 'n/a   '} {text}")
            failed += holds is False
    if a.against:
        old = load(a.against)
        print(f"\nnumbers against {a.against} (~ depends on the runs, ! should not differ):")
        for k in sorted(set(n) | set(old), key=lambda k: (rq_of(k) or 9, k)):
            if (rq_of(k) or 0) not in a.rq:
                continue
            o, m = old.get(k, "-"), n.get(k, "-")
            mark = "=" if o == m else ("!" if FIXED.search(k) else "~")
            print(f"  RQ{rq_of(k)} {mark} {k:34} {o:>14} -> {m}")
    print(f"\n{'every checked claim holds' if not failed else f'{failed} claim(s) fail'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
