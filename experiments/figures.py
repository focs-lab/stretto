"""The README's results: its charts (docs/figures/*.svg) and the text between the markers
<!-- results:begin --> and <!-- results:end -->, written by experiments/tables.py from the same
numbers as the paper, so the README changes whenever a result does.

The charts are plain SVG on a light card, so they read the same in GitHub's light and dark
themes, and they are deterministic: the same numbers give the same files.
"""
from __future__ import annotations
import os
import re
from html import escape

FONT = "font-family=\"-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif\""
NAVY, BLUE, ORANGE, RUST, GREY, LIGHT, CARD, EDGE = ("#14213D", "#1F5FA8", "#E8873A", "#B4561A", "#9AA3B2",
                                                     "#E9ECF2", "#FFFDF8", "#E2DDD2")


def text(num: dict, k: str, default: str = "?") -> str:
    """A macro's value as display text: TeX thin spaces and en dashes made plain."""
    v = num.get(k)
    if v is None:
        return default
    return v.replace("\\,", " ").replace("--", "–")


def value(num: dict, k: str):
    v = num.get(k)
    if v is None:
        return None
    t = v.replace("\\,", "")
    try:
        return int(t)
    except ValueError:
        try:
            return float(t)
        except ValueError:
            return None


def card(w: int, h: int, title: str, body: list) -> str:
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{escape(title)}">',
        f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="14" fill="{CARD}" stroke="{EDGE}" stroke-width="2"/>',
        f'<text x="28" y="40" {FONT} font-size="19" font-weight="600" fill="{NAVY}">{escape(title)}</text>',
        *body, "</svg>", ""])


def bars(path: str, title: str, groups: list) -> None:
    """Horizontal bars on a 0..1 scale. groups: [(heading, [(label, value or None, colour)])]."""
    x0, width, row, w = 290, 470, 34, 860
    body, y = [], 70
    for heading, items in groups:
        body.append(f'<text x="28" y="{y + 6}" {FONT} font-size="15" font-weight="600" fill="{NAVY}">{escape(heading)}</text>')
        y += 18
        for label, v, colour in items:
            body.append(f'<text x="{x0 - 14}" y="{y + 18}" {FONT} font-size="14" fill="{NAVY}" text-anchor="end">{escape(label)}</text>')
            body.append(f'<rect x="{x0}" y="{y + 4}" width="{width}" height="20" rx="4" fill="{LIGHT}"/>')
            if v is not None:
                body.append(f'<rect x="{x0}" y="{y + 4}" width="{round(width * v, 1)}" height="20" rx="4" fill="{colour}"/>')
                body.append(f'<text x="{x0 + width + 12}" y="{y + 19}" {FONT} font-size="14" font-weight="600" fill="{NAVY}">{v:.2f}</text>')
            else:
                body.append(f'<text x="{x0 + width + 12}" y="{y + 19}" {FONT} font-size="14" fill="{GREY}">not run</text>')
            y += row
        y += 16
    for f in (0.5, 1.0):
        body.append(f'<line x1="{x0 + width * f}" y1="78" x2="{x0 + width * f}" y2="{y - 20}" stroke="{GREY}" stroke-width="1" stroke-dasharray="3 4"/>')
        body.append(f'<text x="{x0 + width * f}" y="{y - 4}" {FONT} font-size="12" fill="{GREY}" text-anchor="middle">{f:.1f}</text>')
    write(path, card(w, y + 10, title, body))


def fractions(path: str, title: str, items: list) -> None:
    """One bar per item, filled to k of n. items: [(label, note, k, n, colour)]."""
    x0, bx, bw, w = 28, 330, 400, 860
    body, y = [], 64
    for label, note, k, n, colour in items:
        body.append(f'<text x="{x0}" y="{y + 16}" {FONT} font-size="15" font-weight="600" fill="{NAVY}">{escape(label)}</text>')
        body.append(f'<text x="{x0}" y="{y + 36}" {FONT} font-size="13" fill="#3F4A5C">{escape(note)}</text>')
        body.append(f'<rect x="{bx}" y="{y + 8}" width="{bw}" height="22" rx="5" fill="{LIGHT}"/>')
        if k is not None and n:
            body.append(f'<rect x="{bx}" y="{y + 8}" width="{round(bw * k / n, 1)}" height="22" rx="5" fill="{colour}"/>')
        body.append(f'<text x="{w - 28}" y="{y + 25}" {FONT} font-size="15" font-weight="600" fill="{NAVY}" text-anchor="end">'
                    f'{"?" if k is None else k} / {"?" if n is None else n}</text>')
        y += 58
    write(path, card(w, y + 12, title, body))


def write(path: str, svg: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(svg)


def readme(root: str, num: dict) -> None:
    """Draw the charts and rewrite the README's results block from the paper's numbers."""
    figs = os.path.join(root, "docs", "figures")
    v = lambda k: value(num, k)    # noqa: E731
    t = lambda k: text(num, k)     # noqa: E731
    bars(os.path.join(figs, "f1.svg"), "F1 score on labelled MPI benchmarks (higher is better)", [
        (f"MBI: message races, probes, buffering ({t('nMBIprogs')} programs)", [
            ("Stretto, one run", v("MBIfoneunion"), BLUE),
            ("MUST 1.9.2", v("MBIfoneMUST"), GREY),
            ("MUST, a timeout or crash as an error", v("MBIfoneMUSTsym"), GREY),
            (f"stress testing, up to {t('nMBIStressRuns')} runs", v("MBIfonestress"), ORANGE),
            ("stress, eager limit forced down", v("MBIfonestresseager"), ORANGE)]),
        (f"MPI-BugBench level 2 ({t('nMBBprogs')} programs)", [
            ("Stretto, one run", v("MBBfoneunion"), BLUE),
            ("Stretto with the buffer guard", v("MBBfoneguard"), BLUE),
            ("MUST 1.9.2", v("MBBfoneMUST"), GREY),
            ("MUST, a timeout or crash as an error", v("MBBfoneMUSTsym"), GREY),
            (f"Stretto, on the {t('nMBBStressProgs')} stress-tested",v("MBBfoneunionstress"), BLUE),
            (f"stress testing, up to {t('nMBBStressRuns')} runs", v("MBBfonestress"), ORANGE),
            ("stress, eager limit forced down", v("MBBfonestresseager"), ORANGE)]),
    ])
    fractions(os.path.join(figs, "hypre.svg"), "HYPRE 2.10.1, ParaSails: a documented hang, predicted from one run", [
        ("Traced runs that predict it", "one clean run each, 4 and 8 processes", v("nHypreExposed"), v("nHypreTraced"), BLUE),
        ("Predicted alternatives that hang", "replayed with their pins; every control finished", v("nHypreCrossHang"), v("nHypreCrossAlts"), BLUE),
        ("Testing runs that hang", "plain runs and runs with random delays", v("nHypreStressHang"), v("nHypreStressRuns"), ORANGE),
        ("Traces of the fix that predict it", "HYPRE 2.11.0", v("nHypreFixedExposed"), v("nHypreFixedTraced"), GREY),
    ])
    ninja = ""
    if v("nNinjaHypreEightAppRuns"):
        ninja = (f" NINJA, the noise injector of that paper, hangs it in {t('nNinjaHypreEightAppHangSetup')} of "
                 f"{t('nNinjaHypreEightAppRuns')} runs at 8 processes (its application-centric mode) and in "
                 f"{t('nNinjaHypreEightRandomShortHangSetup')} of {t('nNinjaHypreEightRandomShortRuns')} with random noise, "
                 f"and never on the fix.")
    mg = ""
    if v("nMGReplayed"):
        fractions(os.path.join(figs, "npbmg.svg"), "NPB MG 2.4.1: a message of another exchange, predicted from one run", [
            ("Traced runs that predict it", f"one clean run each, {t('nMGRanks')} processes", v("nMGExposed"), v("nMGTraced"), BLUE),
            ("Replays that take the predicted message", "pins of the whole horizon", v("nMGMatched"), v("nMGReplayed"), BLUE),
            ("Replays whose result changes", f"{t('nMGControlsSame')} of {t('nMGReplayed')} controls keep the traced result", v("nMGChanged"), v("nMGReplayed"), RUST),
            ("Testing runs whose result changes", "plain runs and runs with random delays", v("nMGStressChanged"), v("nMGStressRuns"), ORANGE),
            ("Traces of the fix that predict it", "NPB 3.3.1", v("nMGFixedExposed"), v("nMGFixedTraced"), GREY),
        ])
        mg = f"""
**A second documented bug.** NPB MG 2.4.1 receives every halo exchange from `MPI_ANY_SOURCE`
with a tag that does not depend on the grid level; its changelog says this "could cause MPI_Wait
return a message meant for the wrong k", fixed in 3.2.1. From one clean run Stretto predicts
receives that take a message of another exchange. Forced, {t('nMGChanged')} of
{t('nMGReplayed')} replays compute another residual; {t('nMGFailAbs')} of them fail 2.4.1's own
check, an absolute error of 1e-8, and {t('nMGFailRel')} fail the relative check of later
versions. Testing changes the residual in {t('nMGStressChanged')} of {t('nMGStressRuns')} runs, and
nothing in those runs reports it.

<p align="center"><img src="docs/figures/npbmg.svg" width="760" alt="NPB MG results"></p>
"""
    dealii = ""
    if v("nDealReplayed"):
        fractions(os.path.join(figs, "dealii.svg"), "deal.II 9.3.0-rc1: a probe takes a message of the next call, predicted from one run", [
            ("Traced runs that predict it", f"one clean run each, {t('nDealRanks')} processes", v("nDealExposed"), v("nDealTraced"), BLUE),
            ("Replays that take the predicted message", "pins of the whole horizon", v("nDealMatched"), v("nDealReplayed"), BLUE),
            ("Replays that print wrong values", f"{t('nDealControlsSame')} of {t('nDealControls')} controls print the expected ones", v("nDealWrong"), v("nDealReplayed"), RUST),
            ("Testing runs that print wrong values", "plain runs and runs with random delays", v("nDealStressChanged"), v("nDealStressRuns"), ORANGE),
            ("Traces of the fix that predict it", "deal.II 9.3.0", v("nDealFixedExposed"), v("nDealFixedTraced"), GREY),
        ])
        dealii = f"""
**A third documented bug.** deal.II 9.3.0-rc1's `RemotePointEvaluation` takes each call's
messages with a wildcard probe under one tag, and nothing separates two calls on one cached
object, so a probe can take a message of the next call; upstream fixed it before 9.3.0 ("fix
race condition if called multiple times"). Every one of {t('nDealTraced')} clean runs of
deal.II's own test predicts it. Forced, all {t('nDealReplayed')} replays finish with status 0
and print wrong values, in the two calls the code predicts; the fix has no such alternative.
Here testing finds the race too: {t('nDealStressDelayChanged')} of {t('nDealStressDelayRuns')} runs
with random delays print wrong values, {t('nDealStressPlainChanged')} of {t('nDealStressPlainRuns')}
plain runs.

<p align="center"><img src="docs/figures/dealii.svg" width="760" alt="deal.II results"></p>
"""
    newbug = ""
    if v("nHypreNewAllCrossAlts"):
        fractions(os.path.join(figs, "hypre32.svg"), "HYPRE 3.2.0, ParaSails: a new hang across pattern levels, from clean runs", [
            ("Traces at two levels that predict it", "hypre's ex5 and PETSc's ex2, 4 and 8 processes",
             v("nHypreNewAllExposed"), v("nHypreNewAllTraced"), BLUE),
            ("Predicted alternatives that hang", "replayed with their pins; every control finished", v("nHypreNewAllCrossHang"),
             v("nHypreNewAllCrossAlts"), BLUE),
            ("Testing runs that hang", "plain runs and runs with random delays, two levels", v("nHypreNewAllStressHang"),
             v("nHypreNewAllStressRuns"), ORANGE),
            ("NINJA runs that hang", "ex5, its three modes, 4 and 8 processes", v("nNinjaHypreNewHang"), v("nNinjaHypreNewRuns"), ORANGE),
            ("Traces of a two-tag fix that predict it", "even levels' requests under a tag of their own", v("nHypreNewFixExposed"),
             v("nHypreNewFixTraced"), GREY),
        ])
        newbug = f"""
**A new bug in a current release.** HYPRE 3.2.0's ParaSails, like its master branch, sends the
row requests of every level of its sparsity pattern with one tag and takes them with a wildcard
probe. Nothing collective separates one level's probes from the next level's requests, so with
two or more levels a probe can take the next level's request and the setup hangs: the race above,
which the 2.11.0 fix left in place across levels. Every one of {t('nHypreNewAllTraced')} clean two-level runs of
hypre's `ex5` and of PETSc 3.26.0's unmodified KSP tutorial `ex2` (`-pc_type hypre -pc_hypre_type
parasails -pc_hypre_parasails_nlevels 2 -pc_hypre_parasails_thresh 0.01`) predicts it, and none at
one level. Forced, all {t('nHypreNewAllCrossAlts')} such alternatives hang with every pin matched, each a run of
the program, and every control finishes. The race needs only the neighbour's {t('BPetscCross')}-byte reply
buffered ({t('BHypreNewReply')} bytes in `ex5`), below Open MPI's default eager limit of {t('BimplOMPI')} bytes. None of
{t('nHypreNewAllStressRuns')} plain and delayed two-level runs hang: a delay before a call cannot hold back a
request already sent. NINJA, which delays sends in transit, hangs `ex5` in {t('nNinjaHypreNewHang')} of
{t('nNinjaHypreNewRuns')} runs. Giving even levels' requests a tag of their own removes every cross
alternative. Along the way: PETSc passes `-pc_hypre_parasails_nlevels` to HYPRE only when
`-pc_hypre_parasails_thresh` is given too.

<p align="center"><img src="docs/figures/hypre32.svg" width="760" alt="HYPRE 3.2.0 ParaSails results"></p>
"""
    history = ""
    if v("nCabanaClean") and v("nStempyClean") and v("nMfemGroupClean") and v("nLammpsClean"):
        history = f"""
**Four documented bugs from projects' histories.** Each was fixed and described by its project;
each case builds the version with the bug and its fix and runs a driver that reaches the code
(the project's own unit test or input where there is one). Stress testing is untraced runs,
plain and with random delays.

| bug | clean traced runs that predict it | forced, the predicted execution | controls | stress testing | the fix |
|---|---:|---|---:|---:|---|
| Cabana `createFromImports`, a round-1 wildcard receive takes a round-2 id (PR #835) | {t('nCabanaExposed')} of {t('nCabanaClean')} (deadlock, L1+SP) | {t('nCabanaReplayHang')} of {t('nCabanaReplayed')} replays hang | {t('nCabanaControlsFinish')} of {t('nCabanaReplayed')} finish | {t('nCabanaStressHang')} of {t('nCabanaStressRuns')} runs hang ({t('nCabanaStressPlainHang')} of {t('nCabanaStressPlainRuns')} plain) | no finding in {t('nCabanaFixClean')} runs |
| stempy 3.3.5 `gatherEvents`, the map receive takes another worker's size (issue #292) | {t('nStempyExposed')} of {t('nStempyClean')} (L1) | {t('nStempyCrossFail')} of {t('nStempyCrossReplayed')} replays fail, cereal throws | {t('nStempyCrossControlsOk')} of {t('nStempyCrossReplayed')} correct | {t('nStempyStressFail')} of {t('nStempyStressRuns')} runs fail ({t('nStempyStressPlainFail')} of {t('nStempyStressPlainRuns')} plain) | no alternative in {t('nStempyFixClean')} runs |
| MFEM 3.3.2 `GroupTopology::Create`, a receive takes the next mesh's row (c52c6243b) | {t('nMfemGroupExposed')} of {t('nMfemGroupClean')} (L1) | {t('nMfemGroupCrossFail')} of {t('nMfemGroupCrossReplayed')} replays fail ({t('nMfemGroupFourCrossFail')} of {t('nMfemGroupFourCrossReplayed')} at 4 ranks) | {t('nMfemGroupCrossControlsOk')} of {t('nMfemGroupCrossReplayed')} correct ({t('nMfemGroupFourCrossControlsOk')} of {t('nMfemGroupFourCrossReplayed')}) | {t('nMfemGroupStressFail')} of {t('nMfemGroupStressRuns')} runs fail ({t('nMfemGroupStressPlainFail')} of {t('nMfemGroupStressPlainRuns')} plain) | no cross alternative in {t('nMfemGroupFixClean')} runs |
| LAMMPS `Irregular::create_atom`, blocking count sends before the receives (411c069b) | {t('nLammpsExposed')} of {t('nLammpsClean')} (deadlock below {t('BLammps')} bytes) | {t('nLammpsBelowHang')} of {t('nLammpsBelowRuns')} runs hang just below the bound | {t('nLammpsAtFinish')} of {t('nLammpsAtRuns')} finish at it | {t('nLammpsStressPlainHang')} of {t('nLammpsStressPlainRuns')} plain and {t('nLammpsStressDelayHang')} of {t('nLammpsStressDelayRuns')} delayed runs hang; {t('nLammpsStressEagerHang')} of {t('nLammpsStressEagerRuns')} with the eager limit forced down | no finding in {t('nLammpsFixClean')} runs |
"""
    reads = ""
    if v("nReadsProgs"):
        reads = f"""
**Reads.** A program reads the result of a receive later, if at all. A static analysis of the
C sources (clang, [`docs/firstread.md`](docs/firstread.md)) bounds where; a deadlock or lost
message the single-path explorer finds then occurs in an execution of every program consistent
with the trace, if its witness execution issues nothing past those bounds. On MBI, {t('nReadsMoved')}
erroneous programs ({t('nReadsMovedClean')} with a clean run) get such a consequence; F1 counting
only what needs no single-path assumption rises from {t('nReadsFoneLOneBase')} to
{t('nReadsFoneLOne')}, with {t('nReadsNewFP')} new false positives. The {t('nReadsValue')}
value checks still need L2. Against the explorer with read bounds, {t('nReadsEsixUnsound')} of
{t('nReadsEsixPairs')} judged alternatives are unsound.
"""
    scale = ""
    if v("nScaleTraces"):
        unfinished, request = v("nScaleSampleUnfinished") or 0, v("nScaleSampleUnfinishedRequest") or 0
        failed, fifteen = v("nScaleSampleControlsFailed") or 0, v("nScaleSampleControlsStatusFifteen") or 0
        n_word = lambda n: {1: "one", 2: "two", 3: "three"}.get(n, str(n))   # noqa: E731
        replays = ("finished" if not unfinished else
                   f"all but {n_word(unfinished)} finished: " + (
                       ("the one left takes a request probe of HYPRE's ParaSails, the race above" if unfinished == 1 else
                        "each of those left takes a request probe of HYPRE's ParaSails, the race above")
                       if request == unfinished else f"{n_word(request)} of those left take a request probe of HYPRE's ParaSails"))
        controls = ("every control finished" if not failed else
                    f"{n_word(failed)} control{'s' if failed > 1 else ''} exited with status 15, the unexplained exit of "
                    "some of HYPRE's replays and controls in E5c" if fifteen == failed else f"{n_word(failed)} controls did not finish")
        linear = (v("ScaleAMGFitDev") is not None and v("ScaleAMGFitDev") < 10)
        fit = (f" ({t('ScaleAMGFitRecord')} µs per record plus {t('ScaleAMGFitAlt')} ms per alternative fits each trace within "
               f"{t('ScaleAMGFitDev')}%)" if linear else "")
        matched = (f"All {t('nScaleSample')} sampled replays matched their pins" if v("nScaleSampleMatched") == v("nScaleSample")
                   else f"{t('nScaleSampleMatched')} of {t('nScaleSample')} sampled replays matched their pins")
        scale = f"""
**Scale.** From {t('nScaleNpMin')} to {t('nScaleNpMax')} processes, in a container of 12 hardware threads that the
larger counts oversubscribe (so these runs measure the analysis, not the programs), the analysis
of AMG grows from {t('ScaleAMGSixteenTime')} s and {t('ScaleAMGSixteenGB')} GB to {t('ScaleAMGSixtyFourTime')} s and {t('ScaleAMGSixtyFourGB')} GB{', about linear in' if linear else ', with'} its records
({t('ScaleAMGSixteenRecords')} M to {t('ScaleAMGSixtyFourRecords')} M) {'plus' if linear else 'and'} its probe alternatives ({t('nScaleAMGSixteenProbeAlts')} to {t('nScaleAMGSixtyFourProbeAlts')}): each alternative
takes {t('ScaleAMGSixteenPerAlt')} ms at {t('nScaleNpMin')} processes and {t('ScaleAMGSixtyFourPerAlt')} ms at {t('nScaleNpMax')}{fit}. HYPRE's
analysis grows from {t('ScaleHypreSixteenTime')} to {t('ScaleHypreSixtyFourTime')} s; LU's, with no wildcards, {t('ScaleLuTimeRatio')}-fold with its
{t('ScaleLuRecordsRatio')}-fold trace. {matched} and {replays}; {controls}.
"""
    valid = ""
    if v("nValidReplays"):
        valid = f"""
A pin makes a wildcard receive a receive from one rank, which is another program, so the
replays behind these results (every MBI replay, samples of the application campaigns) were run
again under the tracer and each one's own trace judged as a run of the program without the
pins: {t('nValidRuns')} of {t('nValidReplays')} are, and {t('nValidConfNew')} of the {t('nValidConfOld')} confirmations among them
stand when a confirmation needs that too."""
    block = f"""<!-- results:begin -->
<!-- Written by experiments/tables.py from experiments/results/; do not edit by hand. -->

**Labelled benchmarks.** One traced run per program, against MUST (a runtime checker that
judges the run it sees) and stress testing with random delays.

<p align="center"><img src="docs/figures/f1.svg" width="760" alt="F1 on MBI and MPI-BugBench"></p>

| corpus | programs | Stretto, one run | false positives | MUST 1.9.2 | stress testing |
|---|---:|---:|---:|---:|---:|
| MBI (message races, probes, buffering) | {t('nMBIprogs')} | **{t('MBIfoneunion')}** | {t('nMBIfp')} | {t('MBIfoneMUST')}–{t('MBIfoneMUSTsym')} | {t('MBIfonestress')}, {t('MBIfonestresseager')} with the eager limit forced down |
| MPI-BugBench level 2 | {t('nMBBprogs')} | **{t('MBBfoneunion')}**, {t('MBBfoneguard')} with the buffer guard | {t('nMBBfp')} | {t('MBBfoneMUST')}–{t('MBBfoneMUSTsym')} | {t('MBBfonestress')}, {t('MBBfonestresseager')} forced down, on its {t('nMBBStressProgs')} call-ordering and correct programs (Stretto {t('MBBfoneunionstress')} on them) |

Every MBI error that stress testing never exposes ({t('nMBIStressPredMissed')} programs, the
buffering-dependent deadlocks) is predicted from a run that finished. With Open MPI's eager limit
forced down so that it buffers no payload byte, the same stress runs hang
{t('nMBIStressEagerBufFound')} of them, those that send to another rank, and fail
{'no correct program' if t('nMBIStressEagerFP') == '0' else t('nMBIStressEagerFP') + ' correct programs'}
(F1 {t('MBIfonestresseager')}); the other
{t('nMBIStressEagerPredMissedSelf')} send to their own rank, which Open MPI buffers at any limit.
Testing finds them once one suspects that buffering matters; Stretto finds them from one run at
the default setting and reports the threshold. The MPI-BugBench misses
are programs that touch a buffer while a nonblocking call still uses it, which no MPI call
records. The buffer guard (`STRETTO_GUARD=1`) watches those buffers in the run itself and finds
{t('nMBBLCguard')} of the {t('nMBBLC')} under Open MPI and {t('nGuardLCMPICHfound')} of
{t('nGuardLCMPICHbuilt')} under MPICH, with no new false positive.

**One run is one schedule.** Run {t('nRepsRuns')} times, MBI's racing programs fail in some runs
and finish cleanly in others: the run itself shows {t('nRepsObsAlways')} of the
{t('nRepsErrors')} labelled errors every time, {t('nRepsObsSometimes')} only sometimes and
{t('nRepsObsNever')} never. Stretto reports {t('nRepsUnionAlways')} of them in every run (F1
{t('RepsUnionFoneMin')}–{t('RepsUnionFoneMax')}, no false positive in any run): of the
{t('nRepsCleanRuns')} runs of an erroneous program that showed nothing, it predicts the error
from {t('nRepsCleanPredicted')}. The other {t('nRepsCleanMissed')} are runs of
{t('nRepsCleanMissedProgs')} programs whose error is a check of the received value, which the
value level (L2) decides.
{reads}
**A real bug.** HYPRE 2.10.1's ParaSails preconditioner reuses one tag for two exchanges behind
a wildcard probe; a probe can take a request of the next exchange, and the solver hangs. A
message race in HYPRE 2.10.1 hung a production code intermittently for 18 months, and
diagnosing it took 291 jobs and 10,392 compute-node hours (the NINJA noise-injection paper,
PPoPP 2017); HYPRE's changelog for 2.11.0 places the fix in ParaSails.{ninja}

<p align="center"><img src="docs/figures/hypre.svg" width="760" alt="HYPRE ParaSails results"></p>
{newbug}{mg}{dealii}{history}
**Soundness in practice.** Against an exhaustive explorer, {t('nEsixUnsound')} of
{t('nEsixPairs')} judged alternatives are unsound. Replays on MBI: {t('nMBIReplayConfirmed')}
confirmed, {t('nMBIReplayInconclusive')} inconclusive (the control itself hung),
{t('nMBIReplayNotConfirmed')} contradicted. Random samples of {t('nAMGsample')} alternatives on AMG
and on Nekbone: every replay matched its pins and finished, and so did every control.{valid}

**Cost.** Tracing slows NPB by {t('TraceOverhead')}× (its own timers). Analysis: lu.A on 16
processes ({t('LuEvents')} M events) in {t('LuAnalysis')} s, AMG in {t('AMGtime')} s, Nekbone
({t('NekRecords')} M records) in {t('NekMinutes')} min and {t('NekGB')} GB.
{scale}
Full tables: [`docs/results.md`](docs/results.md).
<!-- results:end -->"""
    path = os.path.join(root, "README.md")
    s = open(path).read()
    new, n = re.subn(r"<!-- results:begin -->.*?<!-- results:end -->", lambda m: block, s, flags=re.S)
    if n != 1:
        print("README.md has no results block; left as it is")
        return
    if new != s:
        open(path, "w").write(new)
    print("wrote", path, "and", figs)
