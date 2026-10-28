#!/usr/bin/env bash
# Reproduce the paper's evaluation, one research question at a time (artifact/README.md).
#
#   image    build the experiment image from experiments/container/Dockerfile (pinned base and
#            packages) and compare its packages with experiments/container/versions.txt, and,
#            unless READS=0, the image of Dockerfile.firstread (the same plus clang 18)
#   inputs   fetch MBI, MPI-BugBench, AMG, MACSio, Nekbone and NPB at the pinned commits and
#            build them inside the image; build the MUST image on the host
#   kick     build the tracer, the replay library and the idioms, trace the idioms and run the
#            unit tests (a few minutes)
#   tables   regenerate docs/results.md and the paper's numbers from the result tables in
#            experiments/results/ and check the paper's claims against them (seconds)
#   rq1..rq6 rerun the experiments behind one research question of the evaluation, then tables
#   all      rq1, rq2, rq3, rq4, rq6 and rq5, each shared step once
#   paper    build the paper from a copy of its sources named by PAPER_DIR, with the numbers
#            of experiments/generated/, in a TeX image pinned by digest
#
# Every target but image, inputs, paper and the MUST runs runs inside the experiment image
# (experiments/container/run.sh, in the foreground). The first call keeps the numbers the paper
# was built with in artifact/out/numbers.paper.tex; the claims check compares with that copy.
# Steps several questions share (the corpora, AMG, Nekbone) run once: a marker in artifact/out/
# records each that succeeded, and removing it reruns the step. A step that fails stops the
# target, leaves no marker and makes the script exit with a non-zero status.
#
# usage: artifact/reproduce.sh <target> ...         (from any directory)
#   CPUS     processors the containers may use (default: all of them)
#   PAPER_DIR  a copy of the paper's sources, for the paper target
#   MEMORY   memory limit of a container (default 24g; Nekbone and the scale step get 48g)
#   PREFIX   the containers are named PREFIX-<step> (default stretto), so that two copies can run apart
#   JOBS     programs run at a time in the corpus steps (default 6), replays in HYPRE's (4) and in
#            the HYPRE 3.2.0 and PETSc campaigns (2)
#   NEKBONE  0: leave Nekbone out of rq1 and rq5 (it needs 48 GB and about an hour)
#   SCALE    0: leave the runs from 16 to 64 processes out of rq5 (24 GB and about 80 minutes on 6 cores)
#   MPIPRV   0: leave MPI-PRV out of rq2 (its image takes 24 GB of disk; the runs about 6 GB
#            and 3.5 hours on four processors)
#   NEWBUG   0: leave the HYPRE 3.2.0 and PETSc campaigns, the new bug, out of rq6 (their builds
#            take 1.3 GB of disk, the runs about 10 hours)
#   HISTBUGS 0: leave E5g, the five documented bugs of Cabana, stempy, MFEM 3.3.2, deal.II's NBX
#            and LAMMPS, out of rq6 (their sources and builds take 3.3 GB of disk, the runs about
#            2.5 hours)
#   NEWCASES 0: leave E5h, the two new bugs of SuperLU_DIST 9.3.1 and Quantum ESPRESSO, out of rq6
#            (their sources and builds take 2.4 GB of disk, the runs about 5.5 hours)
#   READS    0: leave E3b, the read bounds, out of rq1 and rq4: the maps (in the image of
#            Dockerfile.firstread), the re-analysis and its replays (experiments/reads/run.sh),
#            and E6 with read bounds (about 20 minutes on 8 processors)
#   HB       0: leave E8b, happens-before selection against the check (experiments/e8_hb.py), out
#            of rq6: the comparison takes about 45 minutes (NPB MG at 32 processes about 20, the
#            synthetic programs about 25), then up to 30 replays per program and class
#   VALID    0: leave E8c out of rq6: every replay behind a confirmation claim run again under the
#            tracer (experiments/valid/run.sh), about 8 hours on 12 processors, Nekbone's in 48 GB
#   KEEP_TRACES  a directory in which every driver keeps every trace it records, traced runs,
#            replays and controls alike, one .tar.xz per trace with a line in MANIFEST.tsv
#            (analysis/stretto/keep.py); off by default. A relative directory is the copy's and
#            must be one git ignores, such as artifact/out/traces; an absolute one is mounted
#            into the containers. A full rerun keeps about 5 GB (about 220 GB of traces before
#            compression) and spends about an hour of one processor compressing them; the
#            tables come out the same with it and without it
set -eEuo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"; cd "$R"
export IMAGE="${IMAGE:-stretto-exp:24.04}" CPUS="${CPUS:-0-$(($(nproc) - 1))}" DETACH=0
OUT=artifact/out; mkdir -p "$OUT"
TEX_IMAGE="${TEX_IMAGE:-latexml/ar5ivist@sha256:cb395e7a60491ca75deab279b7b0fcdcd93f7d13c366bdf22a6391446839774a}"
log() { echo "== $(date '+%F %T') $*"; }

# KEEP_TRACES reaches the containers through experiments/container/run.sh and
# experiments/mpiprv/prv.sh. A directory inside the copy that git does not ignore would make
# every row of the rerun record a "-dirty" commit (experiments/provenance.py), so it is refused.
if [ -n "${KEEP_TRACES:-}" ]; then
    export KEEP_TRACES
    k="$(realpath -m "$( [ "${KEEP_TRACES#/}" != "$KEEP_TRACES" ] && echo "$KEEP_TRACES" || echo "$R/$KEEP_TRACES")")"
    r="$(realpath "$R")"
    if [ "${k#"$r"/}" != "$k" ] && git rev-parse HEAD > /dev/null 2>&1 && ! git check-ignore -q "${k#"$r"/}/MANIFEST.tsv"; then
        log "KEEP_TRACES=$KEEP_TRACES is inside the copy and not ignored by git (every row would record a -dirty commit): use artifact/out/traces or a directory outside"
        exit 2
    fi
    log "keeping every trace in $k (MANIFEST.tsv there)"
fi

# the commit every result row records (experiments/provenance.py): before each step, when the copy
# has its history, COMMIT and COMMIT.files are written from git again, so that a step after a pull
# or an edit records the code it ran ("<commit>-dirty" when the code differs from that commit);
# a copy without .git keeps the COMMIT and COMMIT.files it came with
stamp() {
    if git rev-parse HEAD > /dev/null 2>&1; then
        python3 experiments/provenance.py --write > /dev/null || { log "cannot write COMMIT from git"; return 1; }
    fi
}
[ -f "$OUT/numbers.paper.tex" ] || cp experiments/generated/numbers.tex "$OUT/numbers.paper.tex"

box() {   # box <name> <command ...>: run in the experiment image, in the foreground, logged;
          # the status is the command's, not tee's
    local name=$1; shift
    local rc=0
    stamp || return 1
    NAME="${PREFIX:-stretto}-$name" experiments/container/run.sh "$@" 2>&1 | tee -a "$OUT/$name.log" || rc=${PIPESTATUS[0]}
    [ "$rc" = 0 ] || log "$name failed with status $rc (log: $OUT/$name.log)"
    return "$rc"
}
once() {   # once <step> <command ...>: run unless artifact/out/<step>.done says it ran; the
           # marker is written only when the command succeeded
    local step=$1; shift
    if [ -f "$OUT/$step.done" ]; then log "$step: kept from $(cat "$OUT/$step.done") (remove $OUT/$step.done to rerun)"; return 0; fi
    local rc=0
    "$@" || rc=$?
    if [ "$rc" != 0 ]; then log "$step failed with status $rc: no marker written"; return "$rc"; fi
    date '+%F %T' > "$OUT/$step.done"
}
host() {   # host <name> <command ...>: run on the host (docker, MUST), logged; the command's status
    local name=$1; shift
    local rc=0
    stamp || return 1
    "$@" 2>&1 | tee -a "$OUT/$name.log" || rc=${PIPESTATUS[0]}
    [ "$rc" = 0 ] || log "$name: $1 failed with status $rc (log: $OUT/$name.log)"
    return "$rc"
}
claims() {   # claims <rq ...>: regenerate the numbers, check the claims, compare with the paper's
    box tables python3 experiments/tables.py
    box claims python3 artifact/claims.py --against "$OUT/numbers.paper.tex" --rq "$@"
}
corpora() { once corpora box corpora experiments/run_remaining.sh build tests mbi mbb; }
amg() { once amg box amg experiments/amg/run.sh all; }
nekbone() { [ "${NEKBONE:-1}" = 0 ] || MEMORY=48g once nekbone box nekbone experiments/nekbone/run.sh all; }
# HYPRE 2.10.1 and 2.11.0, built once: rq6's campaigns run them, and so does rq2's guard step
# (HYPRE's ij among its applications)
hypre() { once hypre box hypre bash -c "experiments/hypre/run.sh fetch && experiments/hypre/run.sh build"; }
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
    [ "${MPIPRV:-1}" = 0 ] && return 0
    once mpiprv host mpiprv bash -c 'set -e; P=experiments/mpiprv
        docker pull "$(grep -o "mpiprv/mpiprv@sha256:[0-9a-f]*" $P/prv.sh)"
        $P/prv.sh $P/rq1.sh
        $P/prv.sh $P/probes.sh
        $P/prv.sh "python3 $P/run.py --rq1-check && python3 $P/run.py"
        $P/prv.sh $P/adept.sh
        $P/prv.sh python3 $P/apps.py'
    box mpiprv-stretto python3 experiments/mpiprv/stretto_corpus.py
}

# the new bug of RQ6, HYPRE 3.2.0's ParaSails across pattern levels: hypre's ex5 with its level
# count set, the same with even levels' requests under a tag of their own, and PETSc 3.26.0's KSP
# tutorial ex2; traced runs and replays, untraced runs, NINJA and the scan, with the settings of
# the committed tables (experiments/RUNBOOK.md, "Scouting current releases")
newbug() {
    [ "${NEWBUG:-1}" = 0 ] && return
    local s="RUNS=10 STRESS_RUNS=40 TIMEOUT=30 JOBS=${JOBS:-2}"
    once newbug box hypre32 bash -c "experiments/ninja/build.sh \
        && experiments/hypre3/run.sh fetch build && $s experiments/hypre3/run.sh predict stress ninja \
        && VARIANT=fix experiments/hypre3/run.sh fetch build && VARIANT=fix $s experiments/hypre3/run.sh predict stress ninja \
        && experiments/petsc/run.sh fetch build && $s experiments/petsc/run.sh parasails stress ninja scan"
}

# E5g of RQ6, five bugs from projects' histories: each case fetches and builds its version with
# the bug and its fix, predicts from traced runs with replays and controls, and runs the
# untraced baseline on both (experiments/RUNBOOK.md, "Documented bugs from projects' histories").
# deal.II's NBX case runs its replays one traced run at a time with a 5 s timeout, the settings
# of its committed tables: a replay that hangs in its polling loops writes a large trace
histbugs() {
    [ "${HISTBUGS:-1}" = 0 ] && return
    once histbugs box histbugs bash -c "for c in lammps stempy mfem332 cabana; do JOBS=${JOBS:-3} experiments/\$c/run.sh all || exit 1; done \
        && TIMEOUT=5 JOBS=1 experiments/dealii8929/run.sh all"
}
# E5h of RQ6, two new bugs: SuperLU_DIST 9.3.1's 3D solve and Quantum ESPRESSO's elph_prt, each
# fetched and built with the bug and with the proposed fix, predicted from traced runs with
# replays and controls, and tested by untraced runs (experiments/RUNBOOK.md, "Two new bugs in
# current code")
newcases() {
    [ "${NEWCASES:-1}" = 0 ] && return
    once newcases box newcases bash -c "for c in qeelph superlu3d; do JOBS=${JOBS:-3} experiments/\$c/run.sh all || exit 1; done"
}

# E3b, the read bounds of RQ1 and RQ4 (experiments/reads/run.sh): the first-read maps of the
# corpus run's MBI programs, in the image of Dockerfile.firstread (clang 18), then the corpus run
# analysed again with them and the programs whose levels change replayed with their witnesses
FIRSTREAD_IMAGE="${FIRSTREAD_IMAGE:-stretto-firstread:24.04}"
reads() {
    [ "${READS:-1}" = 0 ] && return 0
    corpora
    docker image inspect "$FIRSTREAD_IMAGE" > /dev/null 2>&1 \
        || host reads docker build -t "$FIRSTREAD_IMAGE" -f experiments/container/Dockerfile.firstread experiments/container
    IMAGE="$FIRSTREAD_IMAGE" once reads-maps box reads experiments/reads/run.sh maps
    once reads box reads experiments/reads/run.sh analyse replays
}
# E8b (appendix): happens-before selection against the check on the corpus traces, the idioms and
# AMG, HYPRE 2.10.1 and NPB MG traced again, and 100 synthetic programs, with the settings of the
# committed tables (experiments/RUNBOOK.md); after the corpus run and rq6's builds
hb() {
    [ "${HB:-1}" = 0 ] && return 0
    corpora
    amg
    once hb box hb python3 experiments/e8_hb.py all --synthetic 100 --max-states 100000 --jobs 2
}
# E8c (RQ1, RQ6): every replay behind a confirmation claim run again under the tracer, judged as a
# run of the program without its pins; after the runs it replays (corpora, E3b, rq6's campaigns,
# AMG, Nekbone). Nekbone's replays need 48 GB.
valid() {
    [ "${VALID:-1}" = 0 ] && return 0
    corpora
    reads
    amg
    nekbone
    local steps="mbi npbmg dealii hypre amg"
    [ "${READS:-1}" = 0 ] || steps="$steps reads"
    once valid box valid experiments/valid/run.sh "$steps"
    [ "${NEKBONE:-1}" = 0 ] || MEMORY=48g once valid-nekbone box valid experiments/valid/run.sh nekbone
}

t_image() {
    docker build -t "$IMAGE" experiments/container
    experiments/container/check.sh "$IMAGE"
    [ "${READS:-1}" = 0 ] || docker build -t "$FIRSTREAD_IMAGE" -f experiments/container/Dockerfile.firstread experiments/container
}
t_inputs() {
    box inputs bash -c "third_party/fetch.sh corpora && third_party/fetch.sh apps && third_party/fetch.sh npb"
    third_party/fetch.sh must
}
t_kick() { box kick experiments/run_remaining.sh build tests; }
t_tables() { claims 1 2 3 4 5 6; }
t_rq1() {
    corpora
    box rq1 experiments/run_remaining.sh e6 $([ "${READS:-1}" = 0 ] || echo e6reads)
    reads
    amg
    nekbone
    claims 1
}
t_rq2() {
    corpora
    # stress testing on both corpora at the default eager limits, then with the limit forced down
    box rq2 experiments/run_remaining.sh stress stresseager
    # MUST runs in its own image, started from the host, with the corpus run's timeout
    for c in mbi mbb; do
        MUST_CPUS="$CPUS" host rq2 python3 experiments/mbi/run_must.py "experiments/results/$c/$c.csv" --jobs 3 --timeout 60
    done
    # MUST 1.11.2, plain and with ThreadSanitizer, in its own image (experiments/RUNBOOK.md)
    CPUS="$CPUS" host rq2 experiments/must/build.sh
    for c in mbi mbb; do
        for tsan in 0 1; do
            MUST_TSAN=$tsan MUST_IMAGE=stretto-must:1.11.2 MUST_PREFIX=/MBB-builds/MUST1112 MUST_CACHE="$R/$OUT/must-cache" \
                MUST_CPUS="$CPUS" host rq2 python3 experiments/mbi/run_must.py "experiments/results/$c/$c.csv" --jobs 3 --timeout 60 \
                --column "must1112$([ $tsan = 1 ] && echo tsan)"
        done
    done
    # the buffer guard: the corpora traced again with it and its own checks, which run HYPRE's ij
    # too; its checks run in both modes of the MPI libraries' single-copy transports, which the
    # container's seccomp profile refuses unless it has CAP_SYS_PTRACE (experiments/guard/run.sh)
    hypre
    box guard env SINGLE_COPY=off experiments/run_remaining.sh guard
    DOCKER_ARGS="--cap-add SYS_PTRACE" box guard-cma env SINGLE_COPY=on REPS=3 experiments/guard/run.sh all
    box reps experiments/run_remaining.sh reps
    # the stress baseline on MBI ten more times, each pass with delays from its own seed
    box stressreps experiments/run_remaining.sh stressreps
    box ninja bash -c "experiments/ninja/build.sh && RAND_DELAYS=50000 experiments/ninja/campaign.sh mbi"
    mpiprv
    claims 2
}
t_rq3() { corpora; box rq3 experiments/run_remaining.sh e2; claims 3; }
t_rq4() { corpora; box rq4 experiments/run_remaining.sh e3; reads; claims 4; }
t_rq5() {
    # times are claims only on an otherwise idle machine (experiments/RUNBOOK.md, timed leg)
    box rq5 experiments/timing_leg.sh
    nekbone
    scale
    claims 5
}
t_rq6() {
    hypre
    box rq6 bash -c "JOBS=${JOBS:-4} experiments/hypre/campaign.sh all \
        && HYPRE_VERSION=2.11.0 JOBS=${JOBS:-4} experiments/hypre/campaign.sh predict"
    # the settings of the paper's tables (experiments/RUNBOOK.md): NPB MG built for 16 and 32
    # processes (E8b traces both), 3 traced runs at 16 and 20 stress runs at each count;
    # deal.II 20 traced runs and 50 stress runs
    box npbmg bash -c "experiments/npbmg/run.sh fetch build && RUNS=3 NPS=16 experiments/npbmg/run.sh predict \
        && RUNS=20 experiments/npbmg/run.sh stress"
    box dealii bash -c "experiments/dealii/run.sh fetch build && RUNS=20 JOBS=${JOBS:-4} experiments/dealii/run.sh predict \
        && RUNS=50 experiments/dealii/run.sh stress changes"
    box ninja bash -c "experiments/ninja/build.sh && RUNS=10 experiments/ninja/campaign.sh calibrate \
        && experiments/ninja/campaign.sh hypre && NPS=16 RAND_DELAYS=50000 experiments/ninja/campaign.sh mg"
    newbug
    histbugs
    newcases
    amg
    nekbone
    hb
    valid
    # E8c's claims are RQ1's and RQ6's
    claims 1 6
}
# every question; the claims of all once more at the end, since rq6's E8c reruns replays of RQ1
t_all() { t_rq1; t_rq2; t_rq3; t_rq4; t_rq6; t_rq5; claims 1 2 3 4 5 6; }
t_paper() {   # the paper's sources are kept apart from this repository; PAPER_DIR names a copy
    [ -n "${PAPER_DIR:-}" ] && [ -f "$PAPER_DIR/main.tex" ] \
        || { log "paper: set PAPER_DIR to a copy of the paper's sources (main.tex)"; return 2; }
    mkdir -p "$PAPER_DIR/generated" && cp experiments/generated/numbers.tex experiments/generated/rq2.tex "$PAPER_DIR/generated/"
    docker run --rm --cpuset-cpus "$CPUS" -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$(cd "$PAPER_DIR" && pwd):/p" -w /p \
        --entrypoint bash "$TEX_IMAGE" -c 'mkdir -p build && P="pdflatex -interaction=nonstopmode -halt-on-error -output-directory=build main.tex" \
        && $P > /dev/null && BIBINPUTS=. bibtex build/main > /dev/null && $P > /dev/null && $P > build/pdflatex.log && ls -l build/main.pdf'
}

[ $# -gt 0 ] || { sed -n '2,/^[^#]/{/^#/p}' "$0"; exit 2; }
# errexit stops the script at the first failing step; say where before it does
trap 'rc=$?; log "stopped: a step of ${t:-the setup} failed with status $rc"' ERR
for t in "$@"; do
    declare -F "t_$t" > /dev/null || { echo "unknown target: $t" >&2; exit 2; }
    log "start $t"
    "t_$t"
    log "end $t"
done
