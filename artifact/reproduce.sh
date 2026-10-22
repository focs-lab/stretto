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
#   MEMORY   memory limit of a container (default 24g; Nekbone and the scale step get 48g)
#   JOBS     programs run at a time in the corpus steps (default 6), replays in HYPRE's (4)
#   NEKBONE  0: leave Nekbone out of rq1 and rq5 (it needs 48 GB and about an hour)
#   SCALE    0: leave the runs from 16 to 64 processes out of rq5 (38 GB and about 7 hours)
#   MPIPRV   0: leave MPI-PRV out of rq2 (its image takes 24 GB of disk; the runs about 6 GB
#            and 3.5 hours on four processors)
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
# HYPRE, AMG and NPB LU from 16 to 64 processes; HYPRE is built here unless rq6 built it, and
# scale/run.sh appends to its table, so the table starts afresh
scale() {
    [ "${SCALE:-1}" = 0 ] || MEMORY=48g once scale box scale bash -c \
        "{ [ -x third_party/apps/hypre/hypre-2.10.1/src/test/ij ] || { experiments/hypre/run.sh fetch \
        && experiments/hypre/run.sh build; }; } && rm -f experiments/results/scale/scale.csv \
        && experiments/scale/run.sh all"
}

# MPI-PRV (section E9): its artifact image pulled by digest, its own RQ1, RQ2 and motivating
# example rerun, the probes, the Adept check, its marker arms on the corpora and Stretto on its
# applications (experiments/mpiprv/README.md); the corpus step has built the tracer and the
# replay library, which run inside MPI-PRV's image too
mpiprv() {
    [ "${MPIPRV:-1}" = 0 ] && return
    once mpiprv bash -c 'set -e; P=experiments/mpiprv
        docker pull "$(grep -o "mpiprv/mpiprv@sha256:[0-9a-f]*" $P/prv.sh)"
        $P/prv.sh $P/rq1.sh
        $P/prv.sh $P/probes.sh
        $P/prv.sh "python3 $P/run.py --rq1-check && python3 $P/run.py"
        $P/prv.sh $P/adept.sh
        $P/prv.sh python3 $P/apps.py' 2>&1 | tee -a "$OUT/mpiprv.log"
    box mpiprv-stretto python3 experiments/mpiprv/stretto_corpus.py
}

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
    # stress testing at the default eager limits, then with the limit forced down
    box rq2 experiments/run_remaining.sh stress stresseager
    # MUST runs in its own image, started from the host, with the corpus run's timeout
    for c in mbi mbb; do
        MUST_CPUS="$CPUS" python3 experiments/mbi/run_must.py "experiments/results/$c/$c.csv" --jobs 3 --timeout 60 2>&1 | tee -a "$OUT/rq2.log"
    done
    # MUST 1.11.2, plain and with ThreadSanitizer, in its own image (experiments/RUNBOOK.md)
    CPUS="$CPUS" experiments/must/build.sh 2>&1 | tee -a "$OUT/rq2.log"
    for c in mbi mbb; do
        for tsan in 0 1; do
            MUST_TSAN=$tsan MUST_IMAGE=stretto-must:1.11.2 MUST_PREFIX=/MBB-builds/MUST1112 MUST_CACHE="$R/$OUT/must-cache" \
                MUST_CPUS="$CPUS" python3 experiments/mbi/run_must.py "experiments/results/$c/$c.csv" --jobs 3 --timeout 60 \
                --column "must1112$([ $tsan = 1 ] && echo tsan)" 2>&1 | tee -a "$OUT/rq2.log"
        done
    done
    box guard experiments/run_remaining.sh guard
    box reps experiments/run_remaining.sh reps
    box ninja bash -c "experiments/ninja/build.sh && RAND_DELAYS=50000 experiments/ninja/campaign.sh mbi"
    mpiprv
    claims 2
}
t_rq3() { corpora; box rq3 experiments/run_remaining.sh e2; claims 3; }
t_rq4() { corpora; box rq4 experiments/run_remaining.sh e3; claims 4; }
t_rq5() {
    # times are claims only on an otherwise idle machine (experiments/RUNBOOK.md, timed leg)
    box rq5 experiments/timing_leg.sh
    nekbone
    scale
    claims 5
}
t_rq6() {
    box rq6 bash -c "experiments/hypre/run.sh fetch && experiments/hypre/run.sh build \
        && JOBS=${JOBS:-4} experiments/hypre/campaign.sh all \
        && HYPRE_VERSION=2.11.0 JOBS=${JOBS:-4} experiments/hypre/campaign.sh predict"
    box npbmg experiments/npbmg/run.sh all
    box dealii bash -c "JOBS=${JOBS:-4} experiments/dealii/run.sh all"
    box ninja bash -c "experiments/ninja/build.sh && RUNS=10 experiments/ninja/campaign.sh calibrate \
        && experiments/ninja/campaign.sh hypre && NPS=16 RAND_DELAYS=50000 experiments/ninja/campaign.sh mg"
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

[ $# -gt 0 ] || { sed -n '2,29p' "$0"; exit 2; }
for t in "$@"; do
    declare -F "t_$t" > /dev/null || { echo "unknown target: $t" >&2; exit 2; }
    log "start $t"
    "t_$t"
    log "end $t"
done
