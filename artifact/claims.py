#!/usr/bin/env python3
"""Check the paper's claims against the numbers experiments/tables.py wrote.

The paper takes every number of its evaluation from experiments/generated/numbers.tex. This script
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

A claim fails when a number it needs is missing, and when there was nothing to check: each
claim names the counts of what it is about (traces, replays, programs), and a zero there makes
it fail rather than hold vacuously, unless the claim is declared to need none (an existence
claim, whose test is that count). With --against, a number the inputs fix that differs fails too.

usage: artifact/claims.py [--rq 1 2 ...] [--against OLD_NUMBERS_TEX] [NUMBERS_TEX]
exit status: 0 if every checked claim holds (and, with --against, every number the inputs fix
is equal), 1 otherwise.
"""
from __future__ import annotations
import argparse
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NUMBERS = os.path.join(ROOT, "experiments", "generated", "numbers.tex")

# which research question a macro belongs to, by name; the first match wins
RQ_OF = [
    (1, r"^nEsix|^nMBIReplay|^nAMGsample|^nNeksample|^nPin|^nReadsEsix|^nReadsReplay|^nValid"),
    (4, r"^nReads"),
    (6, r"^n?Ninja(Hypre|Replica|MG|Petsc|Cabana|Stempy|MfemGroup|Lammps|Superlu|Qe)|^nMust|^Prv(Cabana|Stempy|MfemGroup|Lammps|Superlu|Qe)"),
    (2, r"^n?NinjaMBI|^nMBIMUST|^nTsanLC|^(MBI|MBB)fone|^n(MBI|MBB)(tp|fn|fp|progs|notBuilt|correct|errors|race|Stress|Clean|OnlyStretto|LC)|^n?Guard|^n?Reps"
        r"|^n?PRV|^n?(MBI|MBB)(Union|Sign)"),
    (3, r"^nEtwo|^B[a-z]+OMPI$"),
    (4, r"^n(MBI|MBB)(one|sp|obs)[A-Z]"),
    (5, r"^TraceOverhead|^LuAnalysis|^LuEvents|^AMGtime|^Nek(GB|Minutes|PerAlt|Records)|^nEfour|^n?Scale"),
    (6, r"^nHypre|^BHypre|^nAMG|^nNek|^nDeal|^nPetsc|^BPetsc|^n(Cabana|Stempy|MfemGroup|Lammps)|^BLammps"),
]
# numbers the inputs fix: a difference there is unexpected; every other number is a time or a
# count taken from recorded runs, which depends on their schedule
FIXED = re.compile(r"^n(MBI|MBB)(progs|notBuilt|correct|errors|race)$|^B[a-z]+OMPI$|^n(AMG|Nek)sample$|^nEfourReps$"
                   r"|^n(MBI|MBB)Stress(Runs|Progs)$|^n(MBI|MBB)StressEager(Runs|Bytes|Err)$|^nHypre(Fixed)?(Traced|(Four|Eight)(Local)?Runs)$|^nHypreStressRuns$"
                   r"|^nMBBLC$|^nGuardLC(OMPI|MPICH)built$|^nGuardCases$|^nGuardAppRuns$"
                   r"|^nReps(Runs|Progs|Errors)$|^nDeal(Fixed)?Traced$|^nDealRanks$|^nDealStress(Plain|Delay)?Runs$"
                   r"|^nNinja(Hypre|Replica|MG|Petsc|Cabana|Stempy|MfemGroup|Lammps|Superlu|Qe)(Fix)?[A-Za-z]*Runs$|^nNinjaMBIerrors$|^nTsanLC$"
                   r"|^nMust[A-Za-z]*Runs$"
                   r"|^n(HypreNew(Fix|All)?|Petsc)(Two|One)?Traced$|^n(HypreNew|Petsc)(Four|Eight)Runs$"
                   r"|^nHypreNew(Fix|All)?Stress(Two(Delay)?)?Runs$|^nPetscStressTwo(Delay)?Runs$"
                   r"|^B(HypreNewReply|PetscReply|PetscCross)$"
                   r"|^nScale(Traces|Sample|NpMin|NpMax)$"
                   r"|^nPRV(rqOne(Rounds|Props)|rqTwo(Configs|Absent|Matched)|adeptTraces|adept(Orig|Fixed)Runs"
                   r"|adept[A-Za-z]*(Pairs|Delayed)|probe[A-Za-z]*Runs|corpus|inScope|outScope|twins|apps|appConfigs)$"
                   r"|^nValid(MBI|Reads|MG|Deal|Hypre|AMG|Nek)Replays$|^nValid(MBI|Reads)Committed$|^nValid(MG|Deal|Hypre)Traced$"
                   r"|^n(Cabana|Stempy|MfemGroup|Lammps)(Fix)?(Three|Four|Eight)?Traced$|^n(Cabana|Stempy|MfemGroup|Lammps)Ranks$"
                   r"|^n(Cabana|Stempy|MfemGroup|Lammps)(Fix)?Stress(Plain|Delay|Eager)?Runs$"
                   r"|^nLammps(Fix)?(Three|Four|Eight)?(Default|Below|At)Runs$|^BLammps$")


def load(path: str) -> dict:
    out = {}
    with open(path) as fh:
        text = fh.read()
    for name, value in re.findall(r"\\newcommand\{\\([A-Za-z]+)\}\{(.*?)\\xspace\}", text):
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
    """(research question, claim, holds) for every claim. A claim fails when one of its numbers is
    missing (the experiment behind it did not run, or tables.py did not find its table), and
    when it is vacuous: every claim names in `over` the counts of what it is about (traces,
    replays, programs), and if one of them is zero there was nothing to check. A claim that may
    hold with nothing to count says so with vacuous=True."""
    v = {k: value(t) for k, t in n.items()}
    out = []

    def claim(rq, text, names, test, over=(), vacuous=False):
        if not over and not vacuous:
            raise ValueError(f"claim {text!r} names no count of what it is about (over=) and is not declared vacuous")
        missing = [k for k in dict.fromkeys([*names, *over]) if k not in v]
        if missing:
            out.append((rq, f"{text} (numbers missing: {', '.join(missing)})", False))
            return
        empty = [k for k in over if not (isinstance(v[k], (int, float)) and v[k] > 0)]
        if empty:
            out.append((rq, f"{text} (nothing to check: {', '.join(f'{k} = {n[k]}' for k in empty)})", False))
            return
        out.append((rq, text, bool(test(*[v[k] for k in names]))))

    claim(1, "no accepted alternative is unsound against the exhaustive explorer",
          ["nEsixUnsound"], lambda u: u == 0, over=["nEsixPairs"])
    claim(1, "the explorer confirms every contained deadlock the check reports",
          ["nEsixLdeadUnsound"], lambda u: u == 0, over=["nEsixLdead"])
    claim(1, "no MBI replay contradicts its finding",
          ["nMBIReplayNotConfirmed"], lambda u: u == 0, over=["nMBIReplayFindings"])
    claim(1, "every inconclusive MBI replay belongs to a racing loop labelled erroneous",
          ["nMBIReplayInconclusive", "nMBIReplayInconclusiveLoop"], lambda i, l: i == l, over=["nMBIReplayFindings"])
    for app in ("AMG", "Nek"):
        claim(1, f"{app} sample: every replay matched its pins and finished, every control finished",
              [f"n{app}sample", f"n{app}sampleMatched", f"n{app}sampleFinish", f"n{app}sampleControls"],
              lambda s, m, f, c: s > 0 and s == m == f == c, over=[f"n{app}sample"])
    claim(1, "every application replay or control that hung or finished matched its pins",
          ["nPinRuns", "nPinUnmatchedEnded"], lambda r, e: r > 0 and e == 0, over=["nPinRuns"])
    claim(1, "every replay behind a confirmation claim, run again under the tracer, is a run of the program without its pins",
          ["nValidReplays", "nValidRuns"], lambda r, v: r > 0 and r == v, over=["nValidReplays"])
    claim(1, "every control of those replays is a run of the program without its pins",
          ["nValidControls", "nValidControlRuns"], lambda c, v: c > 0 and c == v, over=["nValidControls"])
    claim(1, "no confirmation of those replays rests on the pins alone: the old and the strict rule confirm the same",
          ["nValidConfOld", "nValidConfNew"], lambda o, n: o > 0 and o == n, over=["nValidConfOld"])
    claim(1, "every MBI replay run again for E1 and E3b has the finding and pins of a committed replay",
          ["nValidMBIReplays", "nValidMBISamePins", "nValidReadsReplays", "nValidReadsSamePins"],
          lambda a, b, c, d: a > 0 and a == b and c > 0 and c == d, over=["nValidMBIReplays", "nValidReadsReplays"])
    for app in ("AMG", "Nek"):
        claim(1, f"{app} sample, run again: every replay matched its pins and finished in a run of the program, so did every control",
              [f"nValid{app}Replays", f"nValid{app}ConfNew"], lambda r, c: r > 0 and r == c, over=[f"nValid{app}Replays"])
    claim(1, "with read bounds, no accepted alternative is unsound against the exhaustive explorer",
          ["nReadsEsixPairs", "nReadsEsixUnsound"], lambda p, u: p > 0 and u == 0, over=["nReadsEsixPairs"])
    claim(1, "with read bounds, the explorer confirms every contained deadlock the check reports",
          ["nReadsEsixLdeadUnsound"], lambda u: u == 0, over=["nReadsEsixLdead"])
    claim(1, "no replay of an MBI consequence the read bounds certify at L1 contradicts it",
          ["nReadsReplayFindings", "nReadsReplayNotConfirmed"], lambda f, n: f > 0 and n == 0, over=["nReadsReplayFindings"])
    claim(4, "read bounds add no false positive on MBI and change no program's verdict",
          ["nReadsNewFP", "nReadsUnionChanged"], lambda f, c: f == 0 and c == 0, over=["nReadsMaps", "nReadsProgs"])
    claim(4, "read bounds certify a consequence at L1 only on programs labelled erroneous",
          ["nReadsMoved", "nReadsMovedErr"], lambda m, e: m > 0 and m == e, over=["nReadsMoved"])
    claim(4, "the value-check programs keep no L1 consequence with read bounds (they read the value)",
          ["nReadsValue", "nReadsValueMoved"], lambda v, m: v > 0 and m == 0, over=["nReadsValue"])
    claim(2, "no false positive on MBI", ["nMBIfp"], lambda x: x == 0, over=["nMBIcorrect"])
    claim(2, "every MBI error Stretto finds was shown by the run, or predicted by a consequence or a threshold",
          ["nMBItpunion", "nMBItpobserved", "nMBItpPredSP", "nMBItpPredBuf"], lambda u, o, s, b: u == o + s + b,
          over=["nMBIerrors"])
    claim(2, "Stretto's F1 on MBI is above MUST's, under both scorings, and above stress testing",
          ["MBIfoneunion", "MBIfoneMUST", "MBIfoneMUSTsym", "MBIfonestress"],
          lambda s, m, ms, st: s > max(m, ms, st), over=["nMBIprogs"])
    claim(2, "Stretto's F1 on MBI is above MUST 1.11.2's, under both scorings, and above NINJA's in each mode",
          ["MBIfoneunion", "MBIfoneMUSTeleven", "MBIfoneMUSTelevensym", "NinjaMBIRandomfone", "NinjaMBISystemfone",
           "NinjaMBIAppfone"], lambda s, *other: s > max(other), over=["nMBIprogs", "nNinjaMBIerrors"])
    claim(2, "no NINJA mode makes an MBI error fail that stress testing never exposes",
          ["nNinjaMBIRandomStressMissedFound", "nNinjaMBISystemStressMissedFound", "nNinjaMBIAppStressMissedFound"],
          lambda *found: max(found) == 0, over=["nNinjaMBIerrors", "nMBIStressPredMissed"])
    claim(2, "MUST 1.11.2 with ThreadSanitizer finds no local-concurrency program the buffer guard misses",
          ["nTsanLC", "nTsanLCtsanOnly"], lambda n, t: n > 0 and t == 0, over=["nTsanLC"])
    claim(2, "every MBI error stress testing never exposes is predicted by Stretto from one run",
          ["nMBIStressErr", "nMBIStressFound", "nMBIStressPredMissed"], lambda e, f, p: e - f == p, over=["nMBIStressErr"])
    claim(2, "with the eager limit forced down, stress testing exposes every MBI error the default limits leave unexposed "
          "but those that send to their own rank",
          ["nMBIStressPredMissed", "nMBIStressEagerBufFound", "nMBIStressEagerPredMissed", "nMBIStressEagerPredMissedSelf"],
          lambda m, f, s, ss: f > 0 and m == f + s and s == ss, over=["nMBIStressPredMissed"])
    claim(2, "with the eager limit forced down, stress testing fails no correct MBI program",
          ["nMBIStressEagerErr", "nMBIStressEagerFP"], lambda e, fp: e > 0 and fp == 0, over=["nMBIStressEagerErr"])
    claim(2, "on MPI-BugBench, Stretto's F1 is at least MUST's",
          ["MBBfoneunion", "MBBfoneMUST"], lambda s, m: s >= m, over=["nMBBprogs"])
    claim(2, "on the MPI-BugBench programs stress testing runs, Stretto's F1 is above stress testing's at the default "
          "limits and at least its F1 with the eager limit forced down",
          ["MBBfoneunionstress", "MBBfonestress", "MBBfonestresseager"], lambda s, d, e: s > d and s >= e,
          over=["nMBBStressProgs"])
    claim(2, "with the buffer guard, every MPI-BugBench local-concurrency program gets 3-local and nothing else changes",
          ["nMBBLC", "nMBBLCguard", "nMBBfp", "nMBBfpguard", "MBIfoneunion", "MBIfoneguard"],
          lambda lc, g, fp, fpg, s, sg: lc > 0 and lc == g and fp == fpg and s == sg, over=["nMBBLC"])
    claim(2, "under MPICH the guard finds every local-concurrency program that builds",
          ["nGuardLCMPICHbuilt", "nGuardLCMPICHfound"], lambda b, f: b > 0 and b == f, over=["nGuardLCMPICHbuilt"])
    claim(2, "the guard records nothing on NPB LU, HYPRE and AMG, and every run finishes",
          ["nGuardAppRuns", "nGuardAppBad"], lambda r, b: r > 0 and b == 0, over=["nGuardAppRuns"])
    claim(2, "repeated MBI runs: no false positive in any run", ["nRepsRuns", "nRepsFPever"], lambda n, f: n > 1 and f == 0,
          over=["nRepsRuns"])
    claim(2, "repeated MBI runs: no labelled error the run shows more often than Stretto reports it",
          ["nRepsUnionBelowObs"], lambda b: b == 0, over=["nRepsRuns"])
    claim(2, "repeated MBI runs: every clean run Stretto predicts nothing from is of a value-check program (L2)",
          ["nRepsCleanMissedNotLtwo"], lambda x: x == 0, over=["nRepsRuns"])
    # MPI-PRV (experiments/mpiprv/, section E9, the appendix comparing with it)
    claim(2, "MPI-PRV's RQ1 reproduces: every one of its properties is reported in every round",
          ["nPRVrqOneRounds", "nPRVrqOneFull", "nPRVrqOneProps"], lambda r, f, p: r > 0 and p > 0 and f == r,
          over=["nPRVrqOneRounds", "nPRVrqOneProps"])
    claim(2, "MPI-PRV reports its pattern in none of the application configurations its artifact ships traces for",
          ["nPRVrqTwoConfigs", "nPRVrqTwoMatched"], lambda c, m: c > 0 and m == 0, over=["nPRVrqTwoConfigs"])
    claim(2, "MPI-PRV probes: a wildcard receive gets the impossible order in every run, a named sender in none",
          ["nPRVprobeWildRecvAny", "nPRVprobeWildRecvAnyRuns", "nPRVprobeWildIrecvAny", "nPRVprobeWildIrecvAnyRuns",
           "nPRVprobeWildRecvSrc", "nPRVprobeWildIrecvSrc"], lambda a, ar, b, br, s, t: ar > 0 and br > 0 and a == ar and b == br and s == t == 0,
          over=["nPRVprobeWildRecvAnyRuns", "nPRVprobeWildIrecvAnyRuns"])
    claim(2, "MPI-PRV probes: the order across a barrier is reported in every run, across a send and receive in none, "
             "and the observed order in every run of both",
          ["nPRVprobeOrderBarrierBa", "nPRVprobeOrderBarrierBaRuns", "nPRVprobeOrderSendrecvBa",
           "nPRVprobeOrderBarrierAb", "nPRVprobeOrderBarrierAbRuns", "nPRVprobeOrderSendrecvAb", "nPRVprobeOrderSendrecvAbRuns"],
          lambda b, br, s, ob, obr, os_, osr: br > 0 and b == br and s == 0 and ob == obr and os_ == osr,
          over=["nPRVprobeOrderBarrierBaRuns", "nPRVprobeOrderBarrierAbRuns", "nPRVprobeOrderSendrecvAbRuns"])
    claim(2, "MPI-PRV probes: the correct program with four ranks is reported from unrelated events in every run",
          ["nPRVprobeUnrelated", "nPRVprobeUnrelatedRuns"], lambda u, r: r > 0 and u == r, over=["nPRVprobeUnrelatedRuns"])
    claim(2, "MPI-PRV with script-placed markers reports fewer local-concurrency errors than the buffer guard, whose "
             "false reports there are no more than its MPI-BugBench false positives",
          ["nPRVtpBLC", "nPRVtpStrettoLC", "nPRVfpStrettoLC", "nMBBfpguard"], lambda b, s, fs, fg: b < s and fs <= fg,
          over=["nPRVtpStrettoLC"])
    # an existence claim: its test is the count itself
    claim(2, "MPI-PRV's properties on MPI calls alone report correct programs", ["nPRVfpA"], lambda f: f > 0, vacuous=True)
    claim(2, "Adept kernel with the missing waits: every run, delayed or not, gives the blocking variant's result, and "
             "every send of the delayed rank comes after its neighbour's interior loop",
          ["nPRVadeptFixedRuns", "nPRVadeptFixedSame", "nPRVadeptDelayOneDelayed", "nPRVadeptDelayOneDelayedLate",
           "nPRVadeptDelayTwoDelayed", "nPRVadeptDelayTwoDelayedLate"],
          lambda r, s, d1, l1, d2, l2: r > 0 and s == r and d1 > 0 and l1 == d1 and d2 > 0 and l2 == d2,
          over=["nPRVadeptFixedRuns", "nPRVadeptDelayOneDelayed", "nPRVadeptDelayTwoDelayed"])
    claim(2, "Adept kernel: the mutant that updates the halo planes before the waits changes the result, under the delay too",
          ["nPRVadeptMutantSame", "nPRVadeptMutantDelayDelayed", "nPRVadeptMutantDelayDelayedLate"],
          lambda s, d, l: s == 0 and d > 0 and l == d, over=["nPRVadeptMutantDelayDelayed"])
    claim(2, "Adept kernel as shipped: no run gives the blocking variant's result, the guard reports writes to its "
             "send buffers, and none once the waits are added",
          ["nPRVadeptOrigRuns", "nPRVadeptOrigSame", "nPRVadeptGuard", "nPRVadeptGuardFixed"],
          lambda r, s, g, gf: r > 0 and s == 0 and g > 0 and gf == 0, over=["nPRVadeptOrigRuns"])
    claim(2, "Heat: Stretto predicts a deadlock; every prediction is confirmed, and every replay of its alternatives "
             "hangs with its pins matched while its control finishes",
          ["nPRVheatDeadlock", "nPRVheatDeadlockConfirmed", "nPRVheatAlts", "nPRVheatReplayHang"],
          lambda d, c, a, h: d > 0 and c == d and a > 0 and h == a, over=["nPRVheatDeadlock", "nPRVheatAlts"])
    claim(2, "MPI-PRV's applications: every replay under rendezvous hangs as predicted, among them DTG's",
          ["nPRVappsRendezvous", "nPRVappsRendezvousHang", "nPRVdtgRendezvousHang"], lambda r, h, d: r > 0 and h == r and d > 0,
          over=["nPRVappsRendezvous"])
    claim(2, "MPI-PRV's applications: every sampled replay finished with its pins matched and its control finished, "
             "but Heat's, which hang",
          ["nPRVappsReplays", "nPRVappsReplayFinished", "nPRVappsReplayHang", "nPRVheatReplayHang"],
          lambda n, f, h, hh: n > 0 and f + h == n and h == hh, over=["nPRVappsReplays"])
    claim(3, "every recorded run infeasible at B=0 needs no threshold, finished, or did not finish",
          ["nEtwoObsBuf", "nEtwoObsBufNever", "nEtwoObsBufFinished", "nEtwoObsBufStopped"], lambda a, n, f, s: a == n + f + s,
          over=["nEtwoTraces", "nEtwoObsBuf"])
    claim(3, "the runs that finished and need a threshold are the ones replayed",
          ["nEtwoObsBufFinished", "nEtwoReplayPrograms"], lambda f, p: f == p, over=["nEtwoObsBufFinished"])
    claim(3, "no buffering replay goes against the prediction", ["nEtwoReplayBad"], lambda b: b == 0, over=["nEtwoReplayPrograms"])
    claim(3, "with the default limits every replayed program finishes again",
          ["nEtwoReplayPrograms", "nEtwoReplayDefaultOK"], lambda p, ok: p > 0 and p == ok, over=["nEtwoReplayPrograms"])
    claim(3, "below the predicted bound every testable program hangs",
          ["nEtwoReplayBelowTestable", "nEtwoReplayBelowOK"], lambda t, ok: t == ok, over=["nEtwoReplayBelowTestable"])
    claim(3, "at the predicted bound every program finishes",
          ["nEtwoReplayAt", "nEtwoReplayAtOK"], lambda a, ok: a == ok, over=["nEtwoReplayAt"])
    claim(5, "from 16 to 64 processes every traced run and every analysis finished, and every sampled replay "
             "matched its pins",
          ["nScaleTraces", "nScaleTracedFinished", "nScaleAnalysed", "nScaleSample", "nScaleSampleMatched"],
          lambda t, tf, a, s, m: t > 0 and t == tf == a and s > 0 and s == m, over=["nScaleTraces", "nScaleSample"])
    claim(5, "AMG's analysis is within 10% of a time linear in its records plus its alternatives, and each "
             "alternative costs less at 64 processes than at 16",
          ["ScaleAMGFitDev", "ScaleAMGSixteenPerAlt", "ScaleAMGSixtyFourPerAlt"], lambda d, lo, hi: d < 10 and hi < lo,
          over=["nScaleTraces", "ScaleAMGSixteenPerAlt", "ScaleAMGSixtyFourPerAlt"])
    claim(5, "every sampled replay that did not finish is a request-probe alternative of HYPRE 2.10.1, the ParaSails race",
          ["nScaleSample", "nScaleSampleFinish", "nScaleSampleUnfinished", "nScaleSampleUnfinishedRequest"],
          lambda s, f, u, r: s - f == u == r, over=["nScaleSample"])
    claim(5, "every sampled control that did not finish exited with status 15, and every other matched its pins",
          ["nScaleSample", "nScaleSampleControls", "nScaleSampleControlsFailed", "nScaleSampleControlsStatusFifteen",
           "nScaleSampleControlsMatched"],
          lambda s, c, fl, ff, cm: s - c == fl == ff and cm == c, over=["nScaleSample"])
    claim(6, "HYPRE 2.10.1: every cross alternative hangs when replayed",
          ["nHypreCrossAlts", "nHypreCrossHang"], lambda a, h: a > 0 and a == h, over=["nHypreCrossAlts"])
    for p in ("Four", "Eight"):
        claim(6, f"HYPRE 2.10.1, {p.lower()} processes: every cross replay matched its pins, every control finished",
              [f"nHypre{p}Alts", f"nHypre{p}Matched", f"nHypre{p}Controls"], lambda a, m, c: a == m == c,
              over=[f"nHypre{p}Alts"])
        claim(6, f"HYPRE 2.10.1, {p.lower()} processes: no same alternative hangs",
              [f"nHypre{p}LocalHang"], lambda h: h == 0, over=[f"nHypre{p}LocalAlts"])
    claim(6, "HYPRE 2.11.0, the fix: no trace has a cross alternative", ["nHypreFixedCrossAlts"], lambda a: a == 0,
          over=["nHypreFixedTraced"])
    claim(6, "stress testing never hits the HYPRE hang", ["nHypreStressRuns", "nHypreStressHang"],
          lambda r, h: r > 0 and h == 0, over=["nHypreStressRuns"])
    claim(6, "deal.II 9.3.0-rc1: every clean traced run predicts the race, and every cross alternative of a clean run "
             "is replayed, finishes and prints wrong values",
          ["nDealClean", "nDealCleanExposed", "nDealCleanReplayed", "nDealCleanFinished", "nDealCleanWrong"],
          lambda c, e, r, f, w: c > 0 and c == e and r > 0 and r == f == w, over=["nDealClean"])
    claim(6, "deal.II 9.3.0-rc1: every cross alternative is replayed and finishes, and all those of clean runs print "
             "wrong values",
          ["nDealAlts", "nDealReplayed", "nDealFinished", "nDealWrong", "nDealCleanReplayed"],
          lambda a, r, f, w, cr: a > 0 and a == r == f and w >= cr, over=["nDealAlts"])
    claim(6, "NINJA hangs HYPRE 2.10.1 in the ParaSails setup in some runs, the fix in none",
          ["nNinjaHypreHang", "nNinjaHypreFixedRuns", "nNinjaHypreFixedHang"], lambda h, r, f: h > 0 and r > 0 and f == 0,
          over=["nNinjaHypreRuns", "nNinjaHypreFixedRuns"])
    claim(6, "deal.II 9.3.0, the fix: no trace has a cross alternative", ["nDealFixedTraced", "nDealFixedAlts"],
          lambda t, a: t > 0 and a == 0, over=["nDealFixedTraced"])
    # the new bug: HYPRE 3.2.0's ParaSails across pattern levels (experiments/hypre3/, experiments/petsc/)
    for p, drv in (("HypreNew", "hypre's ex5"), ("Petsc", "PETSc's ex2")):
        claim(6, f"HYPRE 3.2.0, {drv}: every traced run at two levels predicts a cross-level probe alternative, "
                 "and none at one level",
              [f"n{p}TwoTraced", f"n{p}TwoExposed", f"n{p}OneTraced", f"n{p}OneExposed"],
              lambda t, e, o, oe: t > 0 and t == e and o > 0 and oe == 0, over=[f"n{p}TwoTraced", f"n{p}OneTraced"])
        claim(6, f"HYPRE 3.2.0, {drv}: every cross-level replay hangs with every pin matched and is a run of the program",
              [f"n{p}CrossAlts", f"n{p}CrossHang", f"n{p}CrossMatched", f"n{p}CrossValid"],
              lambda a, h, m, v: a > 0 and a == h == m == v, over=[f"n{p}CrossAlts"])
        claim(6, f"HYPRE 3.2.0, {drv}: every control of a cross-level replay finishes and is a run of the program",
              [f"n{p}CrossAlts", f"n{p}CrossControls", f"n{p}CrossControlsValid"], lambda a, c, v: a > 0 and a == c == v,
              over=[f"n{p}CrossAlts"])
        claim(6, f"HYPRE 3.2.0, {drv}: no plain or delayed run hangs", [f"n{p}StressTwoRuns", f"n{p}StressHang"],
              lambda r, h: r > 0 and h == 0, over=[f"n{p}StressTwoRuns"])
    claim(6, "HYPRE 3.2.0, the scan: every trace with two or three levels has a cross-level alternative, none with one",
          ["nPetscScanMultiTraced", "nPetscScanMultiExposed", "nPetscScanOneTraced", "nPetscScanOneExposed"],
          lambda t, e, o, oe: t > 0 and t == e and o > 0 and oe == 0, over=["nPetscScanMultiTraced", "nPetscScanOneTraced"])
    claim(6, "HYPRE 3.2.0: the cross-level alternative needs a reply buffered below Open MPI's default eager limit, "
             "and on ex2 the check's bound is that reply's size",
          ["BPetscCross", "BPetscReply", "BHypreNewReply", "BimplOMPI"],
          lambda c, r, h, lim: c == r and max(c, h) < lim, over=["BPetscReply", "BHypreNewReply", "BimplOMPI"])
    claim(6, "HYPRE 3.2.0: the cross-level alternative needs more buffering than stress testing's forced-down "
             "eager limit allows, and less than MPICH's default",
          ["BPetscCross", "BHypreNewReply", "nMBIStressEagerBytes", "BimplMPICH"],
          lambda c, h, e, m: min(c, h) > e and max(c, h) < m, over=["BPetscCross", "BHypreNewReply", "BimplMPICH"])
    claim(6, "NPB MG and deal.II: random delays expose the documented race in some untraced runs",
          ["nMGStressRuns", "nMGStressChanged", "nDealStressDelayRuns", "nDealStressDelayChanged"],
          lambda mr, mc, dr, dc: 0 < mc <= mr and 0 < dc <= dr, over=["nMGStressRuns", "nDealStressDelayRuns"])
    claim(6, "HYPRE 3.2.0 with a tag for even levels (the fix): no trace of ex5 has a cross-level alternative",
          ["nHypreNewFixTraced", "nHypreNewFixExposed", "nHypreNewFixCrossAlts"], lambda t, e, a: t > 0 and e == 0 and a == 0,
          over=["nHypreNewFixTraced"])
    # four documented bugs from projects' histories (experiments/cabana/, stempy/, mfem332/, lammps/)
    claim(6, "Cabana fbbcc37f: every clean traced run predicts the hang (a deadlock finding)",
          ["nCabanaClean", "nCabanaExposed"], lambda c, e: c > 0 and c == e, over=["nCabanaClean"])
    claim(6, "Cabana fbbcc37f: every replay of an error finding hangs with its pins matched, and every control finishes",
          ["nCabanaReplayed", "nCabanaReplayHang", "nCabanaMatched", "nCabanaControlsFinish"],
          lambda r, h, m, c: r == h == m == c, over=["nCabanaReplayed"])
    claim(6, "Cabana 38170631, the fix: no clean traced run has an error finding, and no untraced run fails",
          ["nCabanaFixClean", "nCabanaFixErrorRuns", "nCabanaFixStressRuns", "nCabanaFixStressFail"],
          lambda c, e, r, f: e == 0 and f == 0, over=["nCabanaFixClean", "nCabanaFixStressRuns"])
    claim(6, "stempy 3.3.5: every clean traced run has an alternative in which a receive takes a message of the other kind",
          ["nStempyClean", "nStempyExposed"], lambda c, e: c > 0 and c == e, over=["nStempyClean"])
    claim(6, "stempy 3.3.5, 4 ranks: every such replay fails with its pins matched, and every control gathers correctly",
          ["nStempyFourCrossReplayed", "nStempyFourCrossFail", "nStempyFourCrossMatched", "nStempyFourCrossControlsOk"],
          lambda r, f, m, c: r == f == m == c, over=["nStempyFourCrossReplayed"])
    claim(6, "stempy 3.3.6, the fix: no clean traced run has a receive alternative, and no untraced run fails",
          ["nStempyFixClean", "nStempyFixCrossAlts", "nStempyFixSameAlts", "nStempyFixStressRuns", "nStempyFixStressFail"],
          lambda c, x, s, r, f: x == 0 and s == 0 and f == 0, over=["nStempyFixClean", "nStempyFixStressRuns"])
    claim(6, "MFEM 3.3.2, 4 ranks: every replay of a receive taking the other mesh's row fails with its pins matched, "
             "and every control finishes with both areas right",
          ["nMfemGroupFourCrossReplayed", "nMfemGroupFourCrossFail", "nMfemGroupFourCrossMatched", "nMfemGroupFourCrossControlsOk"],
          lambda r, f, m, c: r == f == m == c, over=["nMfemGroupFourCrossReplayed"])
    claim(6, "MFEM 3.4, the fix: no clean traced run has a cross-call alternative, and no untraced run fails",
          ["nMfemGroupFixClean", "nMfemGroupFixCrossAlts", "nMfemGroupFixStressRuns", "nMfemGroupFixStressFail"],
          lambda c, x, r, f: x == 0 and f == 0, over=["nMfemGroupFixClean", "nMfemGroupFixStressRuns"])
    claim(6, "LAMMPS 3a2da51a: every traced run that finished predicts the deadlock below a 4-byte eager threshold",
          ["nLammpsClean", "nLammpsExposed", "BLammps"], lambda c, e, b: c > 0 and c == e and b == 4, over=["nLammpsClean"])
    claim(6, "LAMMPS 3a2da51a: every rerun just below the bound hangs, every rerun at it and with the defaults finishes",
          ["nLammpsBelowRuns", "nLammpsBelowHang", "nLammpsAtRuns", "nLammpsAtFinish", "nLammpsDefaultRuns", "nLammpsDefaultFinish"],
          lambda br, bh, ar, af, dr, df: br == bh and ar == af and dr == df, over=["nLammpsBelowRuns", "nLammpsAtRuns", "nLammpsDefaultRuns"])
    claim(6, "LAMMPS 411c069b, the fix: no traced run predicts a buffering deadlock, every rerun below the bound finishes, "
             "and no untraced run fails",
          ["nLammpsFixClean", "nLammpsFixExposed", "nLammpsFixBelowRuns", "nLammpsFixBelowFinish", "nLammpsFixStressRuns",
           "nLammpsFixStressFail"],
          lambda c, e, r, f, sr, sf: e == 0 and r == f and sf == 0, over=["nLammpsFixClean", "nLammpsFixBelowRuns", "nLammpsFixStressRuns"])
    claim(6, "LAMMPS 3a2da51a: no plain or delayed untraced run hangs",
          ["nLammpsStressPlainHang", "nLammpsStressDelayHang"], lambda p, d: p == 0 and d == 0,
          over=["nLammpsStressPlainRuns", "nLammpsStressDelayRuns"])
    # E5h: the other tools on those bugs and the two new ones. A NINJA run counts by the bug's own
    # symptom, which the fixes must never show; MUST's controls on the fixes report no race
    for p, what in (("Cabana", "Cabana 38170631"), ("Stempy", "stempy 3.3.6"), ("MfemGroup", "MFEM 3.4"), ("Lammps", "LAMMPS 411c069b"),
                    ("Superlu", "SuperLU_DIST 9.3.1 with the barrier"), ("Qe", "QE with a tag per spin")):
        claim(6, f"{what}, the fix: no NINJA run shows the bug's symptom",
              [f"nNinja{p}FixRuns", f"nNinja{p}FixRevealed"], lambda r, s: s == 0, over=[f"nNinja{p}FixRuns"])
    for p, what in (("Cabana", "Cabana 38170631"), ("MfemGroup", "MFEM 3.4")):
        claim(6, f"{what}, the fix: MUST's report names no race in any run",
              [f"nMust{p}FixRuns", f"nMust{p}FixRace"], lambda r, s: s == 0, over=[f"nMust{p}FixRuns"])
    # existence claims: their tests are the counts themselves
    claim(6, "HYPRE 3.2.0: NINJA hangs ex5 and ex2 in some runs",
          ["nNinjaHypreNewHang", "nNinjaPetscRandomHang"], lambda a, b: a > 0 and b > 0, vacuous=True)
    claim(6, "PETSc 3.26.0: with the level count and no threshold no trace has a cross-level alternative",
          ["nPetscNoThreshTwoTraced", "nPetscNoThreshTwoExposed"], lambda t, e: t > 0 and e == 0, over=["nPetscNoThreshTwoTraced"])
    claim(6, "HYPRE 2.10.1, a sample run again: every cross alternative hangs in a run of the program, every control finishes",
          ["nValidHypreReplays", "nValidHypreConfNew", "nValidHypreHungRuns"], lambda r, c, h: r > 0 and r == c == h,
          over=["nValidHypreReplays"])
    claim(6, "NPB MG 2.4.1, a sample run again: every replay finishes in a run of the program",
          ["nValidMGReplays", "nValidMGRuns"], lambda r, v: r > 0 and r == v, over=["nValidMGReplays"])
    claim(6, "deal.II 9.3.0-rc1, a sample run again: every cross alternative prints wrong values in a run of the program",
          ["nValidDealReplays", "nValidDealConfNew"], lambda r, c: r > 0 and r == c, over=["nValidDealReplays"])
    # statistics of the evaluation (experiments/tables.py, section "Statistics")
    claim(6, "stress testing's runs with delays never hang HYPRE 2.10.1, and their 95% upper bound on the hang rate is above 5%",
          ["nHypreStressDelayHang", "nHypreStressDelayHangCIHi"], lambda h, hi: h == 0 and hi > 0.05, over=["nHypreStressDelayRuns"])
    claim(6, "NINJA's application-centric mode hangs HYPRE 2.10.1 at 8 processes more often than stress testing's runs with "
             "delays, at 95% confidence (the intervals are disjoint)",
          ["nNinjaHypreEightAppHangSetupCILo", "nHypreStressDelayHangCIHi"], lambda lo, hi: lo > hi,
          over=["nNinjaHypreEightAppRuns", "nHypreStressDelayRuns"])
    claim(6, "NPB MG 2.4.1: the traced runs have the same alternatives, so the replays are each distinct alternative once per "
             "traced run",
          ["nMGDistinctTraces", "nMGDistinctPins", "nMGReplayed", "nMGTraced"], lambda d, p, r, t: d == 1 and r == p * t,
          over=["nMGTraced", "nMGDistinctPins"])
    claim(2, "on MBI, every labelled error Stretto finds is found by MUST 1.9.2 or by stress testing at the default limits",
          ["nMBIUnionStrettoNeither"], lambda n: n == 0, over=["nMBIUnionErrors", "nMBIUnionStretto"])
    claim(2, "on MBI, Stretto's lead over stress testing at the default limits is significant at the 5% level against "
             "every repeated stress pass (exact two-sided sign test) and pooled over the repetitions (paired "
             "permutation test)",
          ["nRepsStressPasses", "nRepsStressPassSig", "RepsStressRateP"], lambda n, s, p: n > 0 and s == n and p < 0.05,
          over=["nMBIUnionErrors"])
    claim(2, "on MBI, Stretto finds significantly more labelled errors than MUST 1.9.2 under both scorings "
             "(exact two-sided sign test, 5%)",
          ["nMBISignMUSTStretto", "nMBISignMUSTOther", "MBISignMUSTP", "nMBISignMUSTsymStretto", "nMBISignMUSTsymOther",
           "MBISignMUSTsymP"], lambda s, o, p, ss, so, sp: s > o and p < 0.05 and ss > so and sp < 0.05, over=["nMBIUnionErrors"])
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
            print(f"  RQ{rq} {'holds ' if holds else 'FAILS '} {text}")
            failed += not holds
    differ = 0
    if a.against:
        old = load(a.against)
        print(f"\nnumbers against {a.against} (~ depends on the runs, ! should not differ):")
        for k in sorted(set(n) | set(old), key=lambda k: (rq_of(k) or 9, k)):
            if (rq_of(k) or 0) not in a.rq:
                continue
            o, m = old.get(k, "-"), n.get(k, "-")
            mark = "=" if o == m else ("!" if FIXED.search(k) else "~")
            differ += mark == "!"
            print(f"  RQ{rq_of(k)} {mark} {k:34} {o:>14} -> {m}")
    why = ([f"{failed} claim(s) fail"] if failed else []) + \
          ([f"{differ} number(s) the inputs fix differ from {a.against}"] if differ else [])
    print(f"\n{'every checked claim holds' if not why else '; '.join(why)}")
    return 1 if why else 0


if __name__ == "__main__":
    sys.exit(main())
