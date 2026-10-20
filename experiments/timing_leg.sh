#!/usr/bin/env bash
# Timed leg: the E4 plain/traced/analysis times (experiments/e4_overhead.sh) and the AMG
# analysis (experiments/amg/run.sh analyse) on an idle host, one job at a time, nothing else
# running on the machine between the announced start and end (experiments/RUNBOOK.md). Writes
# experiments/results/e4/e4.csv, experiments/results/amg/summary8.txt and the committed
# experiments/results/timing-leg.log (host, load before and after, and two more passes of the
# AMG analysis, printed for repeatability and not written to the tables). REPS (default 5):
# repetitions of the E4 set.
# usage: experiments/timing_leg.sh      (inside the experiment container, from the repository root)
set -uo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"; cd "$R"
L=experiments/results/timing-leg.log
S=experiments/results/amg/summary8.txt
{
    echo "timing leg: $(date -u +%FT%TZ), host $(hostname), $(nproc) processors available, load $(cut -d' ' -f1-3 /proc/loadavg)"
    echo "== E4: plain, traced and analysis times, ${REPS:-5} repetitions (experiments/e4_overhead.sh)"
    REPS="${REPS:-5}" experiments/e4_overhead.sh
    echo "== E5: AMG analysis, three passes (experiments/amg/run.sh analyse); the table keeps the first"
    [ -d experiments/results/amg/trace8 ] || experiments/amg/run.sh trace
    experiments/amg/run.sh analyse
    cp "$S" /tmp/summary8-pass1.txt
    for pass in 2 3; do experiments/amg/run.sh analyse > /dev/null && tail -n 1 experiments/results/amg/analyze8.err; done
    mv /tmp/summary8-pass1.txt "$S"
    echo "timed leg: $(date -u +%FT%TZ)" >> "$S"
    echo "end: $(date -u +%FT%TZ), load $(cut -d' ' -f1-3 /proc/loadavg)"
} 2>&1 | tee "$L"
