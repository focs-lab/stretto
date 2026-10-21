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
            ("MUST, a timeout as an error", v("MBIfoneMUSTsym"), GREY),
            (f"stress testing, up to {t('nMBIStressRuns')} runs", v("MBIfonestress"), ORANGE)]),
        (f"MPI-BugBench level 2 ({t('nMBBprogs')} programs)", [
            ("Stretto, one run", v("MBBfoneunion"), BLUE),
            ("MUST 1.9.2", v("MBBfoneMUST"), GREY),
            ("MUST, a timeout as an error", v("MBBfoneMUSTsym"), GREY)]),
    ])
    fractions(os.path.join(figs, "hypre.svg"), "HYPRE 2.10.1, ParaSails: a documented hang, predicted from one run", [
        ("Traced runs that predict it", "one clean run each, 4 and 8 processes", v("nHypreExposed"), v("nHypreTraced"), BLUE),
        ("Predicted alternatives that hang", "replayed with their pins; every control finished", v("nHypreCrossHang"), v("nHypreCrossAlts"), BLUE),
        ("Testing runs that hang", "plain runs and runs with random delays", v("nHypreStressHang"), v("nHypreStressRuns"), ORANGE),
        ("Traces of the fix that predict it", "HYPRE 2.11.0", v("nHypreFixedExposed"), v("nHypreFixedTraced"), GREY),
    ])
    block = f"""<!-- results:begin -->
<!-- Written by experiments/tables.py from experiments/results/; do not edit by hand. -->

**Labelled benchmarks.** One traced run per program, against MUST (a runtime checker that
judges the run it sees) and stress testing with random delays.

<p align="center"><img src="docs/figures/f1.svg" width="760" alt="F1 on MBI and MPI-BugBench"></p>

| corpus | programs | Stretto, one run | false positives | MUST 1.9.2 | stress testing |
|---|---:|---:|---:|---:|---:|
| MBI (message races, probes, buffering) | {t('nMBIprogs')} | **{t('MBIfoneunion')}** | {t('nMBIfp')} | {t('MBIfoneMUST')}–{t('MBIfoneMUSTsym')} | {t('MBIfonestress')} |
| MPI-BugBench level 2 | {t('nMBBprogs')} | **{t('MBBfoneunion')}** | {t('nMBBfp')} | {t('MBBfoneMUST')}–{t('MBBfoneMUSTsym')} | – |

Every MBI error that stress testing never exposes ({t('nMBIStressPredMissed')} programs, the
buffering-dependent deadlocks) is predicted from a run that finished. The MPI-BugBench misses
are programs that touch a buffer while a nonblocking call still uses it, which no MPI call
records.

**A real bug.** HYPRE 2.10.1's ParaSails preconditioner reuses one tag for two exchanges behind
a wildcard probe; a probe can take a request of the next exchange, and the solver hangs. It
hung intermittently for 18 months, and diagnosing it took 291 jobs and 10,392 compute-node
hours (reported in the NINJA noise-injection paper, PPoPP 2017).

<p align="center"><img src="docs/figures/hypre.svg" width="760" alt="HYPRE ParaSails results"></p>

**Soundness in practice.** Against an exhaustive explorer, {t('nEsixUnsound')} of
{t('nEsixPairs')} judged alternatives are unsound. Replays on MBI: {t('nMBIReplayConfirmed')}
confirmed, {t('nMBIReplayInconclusive')} inconclusive (the control itself hung),
{t('nMBIReplayNotConfirmed')} contradicted. Random samples of {t('nAMGsample')} alternatives on AMG
and on Nekbone: every replay matched its pins and finished, and so did every control.

**Cost.** Tracing slows NPB by {t('TraceOverhead')}× (its own timers). Analysis: lu.A on 16
processes ({t('LuEvents')} M events) in {t('LuAnalysis')} s, AMG in {t('AMGtime')} s, Nekbone
({t('NekRecords')} M records) in {t('NekMinutes')} min and {t('NekGB')} GB.

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
