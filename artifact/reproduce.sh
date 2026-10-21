#!/usr/bin/env bash
# Reproduce the paper's evaluation, one research question at a time (artifact/README.md).
#
#   image    build the experiment image from experiments/container/Dockerfile (pinned base and
#            packages) and compare its packages with experiments/container/versions.txt
#   inputs   fetch MBI, MPI-BugBench, AMG, MACSio, Nekbone and NPB at the pinned commits and
#            build them inside the image; build the MUST image on the host
#   kick     build the tracer, the replay library and the idioms, trace the idioms and run the
#            unit tests (a few minutes)
#   tables   regenerate docs/results.md and the paper's numbers from the result tables in
#            experiments/results/ and check the paper's claims against them (seconds)
#   rq1..rq6 rerun the experiments behind one research question of the evaluation, then tables
#   all      rq1, rq2, rq3, rq4, rq6 and rq5, each shared step once
#   paper    build paper/build/main.pdf in a TeX image pinned by digest
#
# Every target but image, inputs, paper and the MUST runs runs inside the experiment image
# (experiments/container/run.sh, in the foreground). The first call keeps the numbers the paper
# was built with in artifact/out/numbers.paper.tex; the claims check compares with that copy.
# Steps several questions share (the corpora, AMG, Nekbone) run once: a marker in artifact/out/
# records each, and removing it reruns the step.
#
# usage: artifact/reproduce.sh <target> ...         (from any directory)
#   CPUS     processors the containers may use (default: all of them)
#   MEMORY   memory limit of a container (default 24g; Nekbone gets 48g)
#   JOBS     programs run at a time in the corpus steps (default 6), replays in HYPRE's (4)
#   NEKBONE  0: leave Nekbone out of rq1 and rq5 (it needs 48 GB and about an hour)
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"; cd "$R"
export IMAGE="${IMAGE:-stretto-exp:24.04}" CPUS="${CPUS:-0-$(($(nproc) - 1))}" DETACH=0
OUT=artifact/out; mkdir -p "$OUT"
TEX_IMAGE="${TEX_IMAGE:-latexml/ar5ivist@sha256:cb395e7a60491ca75deab279b7b0fcdcd93f7d13c366bdf22a6391446839774a}"
log() { echo "== $(date '+%F %T') $*"; }

# the commit every result row records: the COMMIT file, or git when the copy has its history
[ -f COMMIT ] || { git rev-parse --short HEAD > COMMIT 2>/dev/null || echo unknown > COMMIT; }
[ -f "$OUT/numbers.paper.tex" ] || cp paper/generated/numbers.tex "$OUT/numbers.paper.tex"

box() {   # box <name> <command ...>: run in the experiment image, in the foreground, logged
    local name=$1; shift
    NAME="stretto-$name" experiments/container/run.sh "$@" 2>&1 | tee -a "$OUT/$name.log"
}
once() {   # once <step> <command ...>: run unless artifact/out/<step>.done says it ran
    local step=$1; shift
    if [ -f "$OUT/$step.done" ]; then log "$step: kept from $(cat "$OUT/$step.done") (remove $OUT/$step.done to rerun)"; return; fi
    "$@"
    date '+%F %T' > "$OUT/$step.done"
}
claims() {   # claims <rq ...>: regenerate the numbers, check the claims, compare with the paper's
    box tables python3 experiments/tables.py
    box claims python3 artifact/claims.py --against "$OUT/numbers.paper.tex" --rq "$@"
}
corpora() { once corpora box corpora experiments/run_remaining.sh build tests mbi mbb; }
amg() { once amg box amg experiments/amg/run.sh all; }
nekbone() { [ "${NEKBONE:-1}" = 0 ] || MEMORY=48g once nekbone box nekbone experiments/nekbone/run.sh all; }

t_image() {
    docker build -t "$IMAGE" experiments/container
    experiments/container/check.sh "$IMAGE"
}
t_inputs() {
    box inputs bash -c "third_party/fetch.sh corpora && third_party/fetch.sh apps && third_party/fetch.sh npb"
    third_party/fetch.sh must
}
t_kick() { box kick experiments/run_remaining.sh build tests; }
t_tables() { claims 1 2 3 4 5 6; }
t_rq1() { corpora; box rq1 experiments/run_remaining.sh e6; amg; nekbone; claims 1; }
t_rq2() {
    corpora
    box rq2 experiments/run_remaining.sh stress
    # MUST runs in its own image, started from the host, with the corpus run's timeout
    for c in mbi mbb; do
        MUST_CPUS="$CPUS" python3 experiments/mbi/run_must.py "experiments/results/$c/$c.csv" --jobs 3 --timeout 60 2>&1 | tee -a "$OUT/rq2.log"
    done
    box guard experiments/run_remaining.sh guard
    claims 2
}
t_rq3() { corpora; box rq3 experiments/run_remaining.sh e2; claims 3; }
t_rq4() { corpora; box rq4 experiments/run_remaining.sh e3; claims 4; }
t_rq5() {
    # times are claims only on an otherwise idle machine (experiments/RUNBOOK.md, timed leg)
    box rq5 experiments/timing_leg.sh
    nekbone
    claims 5
}
t_rq6() {
    box rq6 bash -c "experiments/hypre/run.sh fetch && experiments/hypre/run.sh build \
        && JOBS=${JOBS:-4} experiments/hypre/campaign.sh all \
        && HYPRE_VERSION=2.11.0 JOBS=${JOBS:-4} experiments/hypre/campaign.sh predict"
    amg
    nekbone
    claims 6
}
t_all() { t_rq1; t_rq2; t_rq3; t_rq4; t_rq6; t_rq5; }
t_paper() {
    docker run --rm --cpuset-cpus "$CPUS" -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$R/paper:/p" -w /p \
        --entrypoint bash "$TEX_IMAGE" -c 'mkdir -p build && P="pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main.tex" \
        && $P > /dev/null && BIBINPUTS=. bibtex build/main > /dev/null && $P > /dev/null && $P > build/pdflatex.log && ls -l build/main.pdf'
}

[ $# -gt 0 ] || { sed -n '2,30p' "$0"; exit 2; }
for t in "$@"; do
    declare -F "t_$t" > /dev/null || { echo "unknown target: $t" >&2; exit 2; }
    log "start $t"
    "t_$t"
    log "end $t"
done
