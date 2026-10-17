#!/usr/bin/env bash
# Timed leg: the E4 plain/traced/analysis times (experiments/e4_overhead.sh) and the AMG
# analysis (experiments/amg/run.sh analyse) on an idle host, one job at a time, nothing else
# running on the machine between the announced start and end (experiments/RUNBOOK.md). Writes
# experiments/results/e4/e4.csv, experiments/results/amg/summary8.txt and the committed
# experiments/results/timing-leg.log (host, load before and after, and a second pass of the
# analyses, printed for repeatability and not written to the tables).
# usage: experiments/timing_leg.sh      (inside the experiment container, from the repository root)
set -uo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"; cd "$R"
L=experiments/results/timing-leg.log
{
    echo "timing leg: $(date -u +%FT%TZ), host $(hostname), $(nproc) processors available, load $(cut -d' ' -f1-3 /proc/loadavg)"
    echo "== E4: plain, traced and analysis times (experiments/e4_overhead.sh)"
    experiments/e4_overhead.sh
    echo "== E5: AMG analysis (experiments/amg/run.sh analyse)"
    experiments/amg/run.sh analyse
    echo "== second pass of the analyses (repeatability; the tables keep the first)"
    cp experiments/results/e4/e4.csv /tmp/e4-pass1.csv; cp experiments/results/amg/summary8.txt /tmp/summary8-pass1.txt
    python3 experiments/e4_reanalyze.py
    experiments/amg/run.sh analyse > /dev/null && tail -n 1 experiments/results/amg/analyze8.err
    mv /tmp/e4-pass1.csv experiments/results/e4/e4.csv; mv /tmp/summary8-pass1.txt experiments/results/amg/summary8.txt
    echo "end: $(date -u +%FT%TZ), load $(cut -d' ' -f1-3 /proc/loadavg)"
} 2>&1 | tee "$L"
