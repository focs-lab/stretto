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
    (1, r"^nEsix|^nMBIReplay|^nAMGsample|^nNeksample|^nPin|^nReadsEsix|^nReadsReplay|^nValid"),
    (4, r"^nReads"),
    (6, r"^n?Ninja(Hypre|Replica|MG)"),
    (2, r"^n?NinjaMBI|^nMBIMUST|^nTsanLC|^(MBI|MBB)fone|^n(MBI|MBB)(tp|fn|fp|progs|notBuilt|correct|errors|race|Stress|Clean|OnlyStretto|LC)|^n?Guard|^n?Reps"
        r"|^n?PRV"),
    (3, r"^nEtwo|^B[a-z]+OMPI$"),
    (4, r"^n(MBI|MBB)(one|sp|obs)[A-Z]"),
    (5, r"^TraceOverhead|^LuAnalysis|^LuEvents|^AMGtime|^Nek(GB|Minutes|PerAlt|Records)|^nEfour|^n?Scale"),
    (6, r"^nHypre|^BHypre|^nAMG|^nNek|^nDeal"),
]
# numbers the inputs fix: a difference there is unexpected; every other number is a time or a
# count taken from recorded runs, which depends on their schedule
FIXED = re.compile(r"^n(MBI|MBB)(progs|notBuilt|correct|errors|race)$|^B[a-z]+OMPI$|^n(AMG|Nek)sample$|^nEfourReps$"
                   r"|^n(MBI|MBB)Stress(Runs|Progs)$|^n(MBI|MBB)StressEager(Runs|Bytes|Err)$|^nHypre(Fixed)?(Traced|(Four|Eight)(Local)?Runs)$|^nHypreStressRuns$"
                   r"|^nMBBLC$|^nGuardLC(OMPI|MPICH)built$|^nGuardCases$|^nGuardAppRuns$"
                   r"|^nReps(Runs|Progs|Errors)$|^nDeal(Fixed)?Traced$|^nDealRanks$|^nDealStress(Plain|Delay)?Runs$"
                   r"|^nNinja(Hypre|Replica|MG)[A-Za-z]*Runs$|^nNinjaMBIerrors$|^nTsanLC$"
                   r"|^nScale(Traces|Sample|NpMin|NpMax)$"
                   r"|^nPRV(rqOne(Rounds|Props)|rqTwo(Configs|Absent|Matched)|adeptTraces|adept(Orig|Fixed)Runs"
                   r"|adept[A-Za-z]*(Pairs|Delayed)|probe[A-Za-z]*Runs|corpus|inScope|outScope|twins|apps|appConfigs)$"
                   r"|^nValid(MBI|Reads|MG|Deal|Hypre|AMG|Nek)Replays$|^nValid(MBI|Reads)Committed$|^nValid(MG|Deal|Hypre)Traced$")


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
    claim(1, "every replay behind a confirmation claim, run again under the tracer, is a run of the program without its pins",
          ["nValidReplays", "nValidRuns"], lambda r, v: r > 0 and r == v)
    claim(1, "every control of those replays is a run of the program without its pins",
          ["nValidControls", "nValidControlRuns"], lambda c, v: c > 0 and c == v)
    claim(1, "no confirmation of those replays rests on the pins alone: the old and the strict rule confirm the same",
          ["nValidConfOld", "nValidConfNew"], lambda o, n: o > 0 and o == n)
    claim(1, "every MBI replay run again for E1 and E3b has the finding and pins of a committed replay",
          ["nValidMBIReplays", "nValidMBISamePins", "nValidReadsReplays", "nValidReadsSamePins"],
          lambda a, b, c, d: a > 0 and a == b and c > 0 and c == d)
    for app in ("AMG", "Nek"):
        claim(1, f"{app} sample, run again: every replay matched its pins and finished in a run of the program, so did every control",
              [f"nValid{app}Replays", f"nValid{app}ConfNew"], lambda r, c: r > 0 and r == c)
    claim(1, "with read bounds, no accepted alternative is unsound against the exhaustive explorer",
          ["nReadsEsixPairs", "nReadsEsixUnsound"], lambda p, u: p > 0 and u == 0)
    claim(1, "with read bounds, the explorer confirms every contained deadlock the check reports",
          ["nReadsEsixLdeadUnsound"], lambda u: u == 0)
    claim(1, "no replay of an MBI consequence the read bounds certify at L1 contradicts it",
          ["nReadsReplayFindings", "nReadsReplayNotConfirmed"], lambda f, n: f > 0 and n == 0)
    claim(4, "read bounds add no false positive on MBI and change no program's verdict",
          ["nReadsNewFP", "nReadsUnionChanged"], lambda f, c: f == 0 and c == 0)
    claim(4, "read bounds certify a consequence at L1 only on programs labelled erroneous",
          ["nReadsMoved", "nReadsMovedErr"], lambda m, e: m > 0 and m == e)
    claim(4, "the value-check programs keep no L1 consequence with read bounds (they read the value)",
          ["nReadsValue", "nReadsValueMoved"], lambda v, m: v > 0 and m == 0)
    claim(2, "no false positive on MBI", ["nMBIfp"], lambda x: x == 0)
    claim(2, "every MBI error Stretto finds was shown by the run, or predicted by a consequence or a threshold",
          ["nMBItpunion", "nMBItpobserved", "nMBItpPredSP", "nMBItpPredBuf"], lambda u, o, s, b: u == o + s + b)
    claim(2, "Stretto's F1 on MBI is above MUST's, under both scorings, and above stress testing",
          ["MBIfoneunion", "MBIfoneMUST", "MBIfoneMUSTsym", "MBIfonestress"],
          lambda s, m, ms, st: s > max(m, ms, st))
    claim(2, "Stretto's F1 on MBI is above MUST 1.11.2's, under both scorings, and above NINJA's in each mode",
          ["MBIfoneunion", "MBIfoneMUSTeleven", "MBIfoneMUSTelevensym", "NinjaMBIRandomfone", "NinjaMBISystemfone",
           "NinjaMBIAppfone"], lambda s, *other: s > max(other))
    claim(2, "no NINJA mode makes an MBI error fail that stress testing never exposes",
          ["nNinjaMBIRandomStressMissedFound", "nNinjaMBISystemStressMissedFound", "nNinjaMBIAppStressMissedFound"],
          lambda *found: max(found) == 0)
    claim(2, "MUST 1.11.2 with ThreadSanitizer finds no local-concurrency program the buffer guard misses",
          ["nTsanLC", "nTsanLCtsanOnly"], lambda n, t: n > 0 and t == 0)
    claim(2, "every MBI error stress testing never exposes is predicted by Stretto from one run",
          ["nMBIStressErr", "nMBIStressFound", "nMBIStressPredMissed"], lambda e, f, p: e - f == p)
    claim(2, "with the eager limit forced down, stress testing exposes every MBI error the default limits leave unexposed "
          "but those that send to their own rank",
          ["nMBIStressPredMissed", "nMBIStressEagerBufFound", "nMBIStressEagerPredMissed", "nMBIStressEagerPredMissedSelf"],
          lambda m, f, s, ss: f > 0 and m == f + s and s == ss)
    claim(2, "with the eager limit forced down, stress testing fails no correct MBI program",
          ["nMBIStressEagerErr", "nMBIStressEagerFP"], lambda e, fp: e > 0 and fp == 0)
    claim(2, "on MPI-BugBench, Stretto's F1 is at least MUST's",
          ["MBBfoneunion", "MBBfoneMUST"], lambda s, m: s >= m)
    claim(2, "on the MPI-BugBench programs stress testing runs, Stretto's F1 is above stress testing's at the default "
          "limits and at least its F1 with the eager limit forced down",
          ["MBBfoneunionstress", "MBBfonestress", "MBBfonestresseager"], lambda s, d, e: s > d and s >= e)
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
    # MPI-PRV (experiments/mpiprv/, section E9, the appendix comparing with it)
    claim(2, "MPI-PRV's RQ1 reproduces: every one of its properties is reported in every round",
          ["nPRVrqOneRounds", "nPRVrqOneFull", "nPRVrqOneProps"], lambda r, f, p: r > 0 and p > 0 and f == r)
    claim(2, "MPI-PRV reports its pattern in none of the application configurations its artifact ships traces for",
          ["nPRVrqTwoConfigs", "nPRVrqTwoMatched"], lambda c, m: c > 0 and m == 0)
    claim(2, "MPI-PRV probes: a wildcard receive gets the impossible order in every run, a named sender in none",
          ["nPRVprobeWildRecvAny", "nPRVprobeWildRecvAnyRuns", "nPRVprobeWildIrecvAny", "nPRVprobeWildIrecvAnyRuns",
           "nPRVprobeWildRecvSrc", "nPRVprobeWildIrecvSrc"], lambda a, ar, b, br, s, t: ar > 0 and br > 0 and a == ar and b == br and s == t == 0)
    claim(2, "MPI-PRV probes: the order across a barrier is reported in every run, across a send and receive in none, "
             "and the observed order in every run of both",
          ["nPRVprobeOrderBarrierBa", "nPRVprobeOrderBarrierBaRuns", "nPRVprobeOrderSendrecvBa",
           "nPRVprobeOrderBarrierAb", "nPRVprobeOrderBarrierAbRuns", "nPRVprobeOrderSendrecvAb", "nPRVprobeOrderSendrecvAbRuns"],
          lambda b, br, s, ob, obr, os_, osr: br > 0 and b == br and s == 0 and ob == obr and os_ == osr)
    claim(2, "MPI-PRV probes: the correct program with four ranks is reported from unrelated events in every run",
          ["nPRVprobeUnrelated", "nPRVprobeUnrelatedRuns"], lambda u, r: r > 0 and u == r)
    claim(2, "MPI-PRV with script-placed markers reports fewer local-concurrency errors than the buffer guard, whose "
             "false reports there are no more than its MPI-BugBench false positives",
          ["nPRVtpBLC", "nPRVtpStrettoLC", "nPRVfpStrettoLC", "nMBBfpguard"], lambda b, s, fs, fg: b < s and fs <= fg)
    claim(2, "MPI-PRV's properties on MPI calls alone report correct programs", ["nPRVfpA"], lambda f: f > 0)
    claim(2, "Adept kernel with the missing waits: every run, delayed or not, gives the blocking variant's result, and "
             "every send of the delayed rank comes after its neighbour's interior loop",
          ["nPRVadeptFixedRuns", "nPRVadeptFixedSame", "nPRVadeptDelayOneDelayed", "nPRVadeptDelayOneDelayedLate",
           "nPRVadeptDelayTwoDelayed", "nPRVadeptDelayTwoDelayedLate"],
          lambda r, s, d1, l1, d2, l2: r > 0 and s == r and d1 > 0 and l1 == d1 and d2 > 0 and l2 == d2)
    claim(2, "Adept kernel: the mutant that updates the halo planes before the waits changes the result, under the delay too",
          ["nPRVadeptMutantSame", "nPRVadeptMutantDelayDelayed", "nPRVadeptMutantDelayDelayedLate"],
          lambda s, d, l: s == 0 and d > 0 and l == d)
    claim(2, "Adept kernel as shipped: no run gives the blocking variant's result, the guard reports writes to its "
             "send buffers, and none once the waits are added",
          ["nPRVadeptOrigRuns", "nPRVadeptOrigSame", "nPRVadeptGuard", "nPRVadeptGuardFixed"],
          lambda r, s, g, gf: r > 0 and s == 0 and g > 0 and gf == 0)
    claim(2, "Heat: Stretto predicts a deadlock; every prediction is confirmed, and every replay of its alternatives "
             "hangs with its pins matched while its control finishes",
          ["nPRVheatDeadlock", "nPRVheatDeadlockConfirmed", "nPRVheatAlts", "nPRVheatReplayHang"],
          lambda d, c, a, h: d > 0 and c == d and a > 0 and h == a)
    claim(2, "MPI-PRV's applications: every replay under rendezvous hangs as predicted, among them DTG's",
          ["nPRVappsRendezvous", "nPRVappsRendezvousHang", "nPRVdtgRendezvousHang"], lambda r, h, d: r > 0 and h == r and d > 0)
    claim(2, "MPI-PRV's applications: every sampled replay finished with its pins matched and its control finished, "
             "but Heat's, which hang",
          ["nPRVappsReplays", "nPRVappsReplayFinished", "nPRVappsReplayHang", "nPRVheatReplayHang"],
          lambda n, f, h, hh: n > 0 and f + h == n and h == hh)
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
    claim(5, "from 16 to 64 processes every traced run and every analysis finished, and every sampled replay "
             "matched its pins",
          ["nScaleTraces", "nScaleTracedFinished", "nScaleAnalysed", "nScaleSample", "nScaleSampleMatched"],
          lambda t, tf, a, s, m: t > 0 and t == tf == a and s > 0 and s == m)
    claim(5, "AMG's analysis is within 10% of a time linear in its records plus its alternatives, and each "
             "alternative costs less at 64 processes than at 16",
          ["ScaleAMGFitDev", "ScaleAMGSixteenPerAlt", "ScaleAMGSixtyFourPerAlt"], lambda d, lo, hi: d < 10 and hi < lo)
    claim(5, "every sampled replay that did not finish is a request-probe alternative of HYPRE 2.10.1, the ParaSails race",
          ["nScaleSample", "nScaleSampleFinish", "nScaleSampleUnfinished", "nScaleSampleUnfinishedRequest"],
          lambda s, f, u, r: s - f == u == r)
    claim(5, "every sampled control that did not finish exited with status 15, and every other matched its pins",
          ["nScaleSample", "nScaleSampleControls", "nScaleSampleControlsFailed", "nScaleSampleControlsStatusFifteen",
           "nScaleSampleControlsMatched"],
          lambda s, c, fl, ff, cm: s - c == fl == ff and cm == c)
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
    claim(6, "NINJA hangs HYPRE 2.10.1 in the ParaSails setup in some runs, the fix in none",
          ["nNinjaHypreHang", "nNinjaHypreFixedRuns", "nNinjaHypreFixedHang"], lambda h, r, f: h > 0 and r > 0 and f == 0)
    claim(6, "deal.II 9.3.0, the fix: no trace has a cross alternative", ["nDealFixedTraced", "nDealFixedAlts"],
          lambda t, a: t > 0 and a == 0)
    claim(6, "HYPRE 2.10.1, a sample run again: every cross alternative hangs in a run of the program, every control finishes",
          ["nValidHypreReplays", "nValidHypreConfNew", "nValidHypreHungRuns"], lambda r, c, h: r > 0 and r == c == h)
    claim(6, "NPB MG 2.4.1, a sample run again: every replay finishes in a run of the program",
          ["nValidMGReplays", "nValidMGRuns"], lambda r, v: r > 0 and r == v)
    claim(6, "deal.II 9.3.0-rc1, a sample run again: every cross alternative prints wrong values in a run of the program",
          ["nValidDealReplays", "nValidDealConfNew"], lambda r, c: r > 0 and r == c)
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
