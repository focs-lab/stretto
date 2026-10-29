#!/usr/bin/env bash
# A latent race in Swift/T's ADLB server loop (swift-t 4b3882d, the head on 29 Oct 2026), reached
# only through a driver over ADLB's C API (docs/findings.md, latent bugs).
#
# A server-to-server sync whose subscript is longer than the inline limit (32 bytes) is an Isend
# of SYNC_REQUEST (lb/code/src/sync.c:356) followed by a send of SYNC_SUB (sync.c:531). The
# receiving server takes SYNC_REQUEST into a pre-posted Irecv(MPI_ANY_SOURCE) (sync.c:152, 713),
# which xlb_check_sync_msgs tests, and every other message with xlb_poll's
# Iprobe(MPI_ANY_SOURCE, MPI_ANY_TAG) (server.c:376) and a dispatch on the tag. When both messages
# arrive between the Test that fails and the Iprobe, the Iprobe returns SYNC_SUB, which has no
# handler: "handle(): invalid tag: 40", and the server's loop returns ADLB_ERROR. STC-compiled
# Swift uses short subscripts (by reading), so only programs over the C API or hand-written
# Turbine Tcl rules on long container keys, with two or more servers, reach it. fix.diff, the
# proposed fix: xlb_poll reports nothing for a SYNC_SUB message, which the sync handler receives
# from its source on the loop's next turn.
#
# The driver, adlbsub.c: two servers and two or four workers; NTASKS tasks put with ADLB_Dput,
# each waiting on a 48-byte subscript of one of two containers (one per server), then stored;
# worker 0 prints "adlbsub: OK n", a failed server "adlbsub: SERVER ERROR rc" (judge.py).
# ADLB cancels its pre-posted sync receives at shutdown without completing them; the tracer tests
# them at MPI_Finalize and records that the cancels succeeded, and the analysis models such a
# receive as posted until its cancel (docs/model.md, cancellation; since 29 Oct 2026). The tables
# of 29 Oct 2026 came from traces of an earlier tracer, read through a filter that dropped their
# cancel records (strip_cancel.py, last in commit ec56253).
#
# Steps, inside the experiment container, from the repository root, Open MPI:
#   fetch    swift-t at 4b3882d (git checks the commit), and a copy with fix.diff applied
#   build    ExM c-utils and ADLB (lb) of both, static, -O2 -g, -j$MAKE_JOBS, and adlbsub against each
#   predict  RUNS traced runs at 4 and 6 processes, analysed at L1, a seeded sample of SAMPLE of the
#            syncsub alternatives per process count (classify.py: a wildcard-tag probe that could
#            see SYNC_SUB) replayed with its horizon pins and with its control; and a sample of
#            OTHER_SAMPLE of the other probe alternatives at 4 in OTHER_RUNS traced runs, which
#            should be harmless (experiments/history_predict.py)
#   fixed    OTHER_RUNS traced runs of the fixed copy at 4, a sample of SAMPLE/2 syncsub
#            alternatives replayed (they remain: the probe can still see SYNC_SUB)
#   stress   untraced runs of both copies: STRESS_RUNS per process count (4 and 6), plain and with
#            random send and receive delays; BIG_RUNS plain runs with 2000 tasks at 4; ONE_RUNS
#            plain runs with 400 tasks and the four ranks on the one processor ONE_CPU
#            (experiments/history_stress.py)
#   summary  the compact tables experiments/results/latent/swiftt-{predict,stress}.csv
#   all      fetch build predict fixed stress summary
# The per-run tables go to experiments/results/latent/swiftt/ (not committed).
# usage: experiments/latent/swiftt/run.sh [fetch|build|predict|fixed|stress|summary|all]
#        RUNS (10), SAMPLE (20), OTHER_RUNS (5), OTHER_SAMPLE (10), STRESS_RUNS (40), BIG_RUNS (100),
#        ONE_RUNS (30), ONE_CPU (the container's first processor), TIMEOUT (60), JOBS (1),
#        MAKE_JOBS (4), SWIFTT_DIR (third_party/apps/latent-swiftt)
set -uo pipefail
R="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$R"
H="$R/experiments/latent/swiftt"
D="${SWIFTT_DIR:-$R/third_party/apps/latent-swiftt}"
OUT="$R/experiments/results/latent/swiftt"; mkdir -p "$OUT"
RUNS="${RUNS:-10}"; SAMPLE="${SAMPLE:-20}"; OTHER_RUNS="${OTHER_RUNS:-5}"; OTHER_SAMPLE="${OTHER_SAMPLE:-10}"
STRESS_RUNS="${STRESS_RUNS:-40}"; BIG_RUNS="${BIG_RUNS:-100}"; ONE_RUNS="${ONE_RUNS:-30}"
ONE_CPU="${ONE_CPU:-$(taskset -pc $$ | sed 's/.*: //; s/[,-].*//')}"
TIMEOUT="${TIMEOUT:-60}"; JOBS="${JOBS:-1}"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
export OMPI_MCA_btl_vader_single_copy_mechanism=none OMPI_MCA_hwloc_base_binding_policy=none OMPI_MCA_rmaps_base_oversubscribe=1
SWT=4b3882df1c7ff3c9e46f3eca8c1187764eeceb88
exe() { echo "$D/$1/adlbsub"; }          # exe <bug|fix>

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    if [ ! -d swift-t-bug ]; then
        rm -rf swift-t-bug.part && git init -q swift-t-bug.part \
            && git -C swift-t-bug.part fetch -q --depth 1 https://github.com/swift-lang/swift-t.git "$SWT" \
            && git -C swift-t-bug.part checkout -q FETCH_HEAD && [ "$(git -C swift-t-bug.part rev-parse HEAD)" = "$SWT" ] \
            && mv swift-t-bug.part swift-t-bug || { harness_error "cannot fetch swift-t $SWT"; return 1; }
    fi
    if [ ! -d swift-t-fix ]; then
        cp -a swift-t-bug swift-t-fix.part && patch -s -p1 -d swift-t-fix.part < "$H/fix.diff" \
            && mv swift-t-fix.part swift-t-fix || { rm -rf swift-t-fix.part; return 1; }
    fi
    cd "$R"
}
step_build() {
    local v W j="${MAKE_JOBS:-4}" inc
    inc="$(/usr/bin/mpicc.openmpi -show | grep -o -- "-I[^ ]*" | tr "\n" " ")"
    for v in bug fix; do
        W="$D/$v"
        if [ ! -f "$W/inst/lb/lib/libadlb.a" ]; then
            rm -rf "$W" && mkdir -p "$W" && cp -a "$D/swift-t-$v/c-utils/code" "$W/c-utils" && cp -a "$D/swift-t-$v/lb/code" "$W/lb" || return 1
            (cd "$W/c-utils" && ./bootstrap > ../cu.log 2>&1 && ./configure --prefix="$W/inst/c-utils" CC=gcc --disable-shared >> ../cu.log 2>&1 \
                 && make -j"$j" >> ../cu.log 2>&1 && make install >> ../cu.log 2>&1) || { echo "build of c-utils ($v) failed ($W/cu.log)" >&2; return 1; }
            (cd "$W/lb" && ./bootstrap > ../lb.log 2>&1 && CPPFLAGS="$inc" CFLAGS="-O2 -g" ./configure --prefix="$W/inst/lb" \
                 CC=/usr/bin/mpicc.openmpi --with-c-utils="$W/inst/c-utils" --disable-shared >> ../lb.log 2>&1 \
                 && make -j"$j" >> ../lb.log 2>&1 && make install >> ../lb.log 2>&1) || { echo "build of ADLB ($v) failed ($W/lb.log)" >&2; return 1; }
        fi
        /usr/bin/mpicc.openmpi -O2 -g -std=gnu99 -I"$W/inst/lb/include" -I"$W/inst/c-utils/include" "$H/adlbsub.c" \
            -o "$(exe $v)" -L"$W/inst/lb/lib" -L"$W/inst/c-utils/lib" -ladlb -lexmcutils -lm || return 1
    done
}
p1() {        # p1 <bug|fix> <version> <np> <runs> <kind> <sample>
    local v=$1 ver=$2 np=$3 runs=$4 kind=$5 sample=$6
    rm -f "$OUT/predict-$ver-np$np.csv"
    python3 experiments/history_predict.py --version "$ver" --mpi openmpi --np "$np" --runs "$runs" --no-sp \
        --classify "$H/classify.py" --replay "$kind" --sample "$sample" --timeout "$TIMEOUT" --trace-timeout 120 \
        --jobs "$JOBS" --judge "$H/judge.py" --out "$OUT/predict-$ver-np$np.csv" -- "$(exe "$v")" 40 48
}
step_predict() {
    need_file "$(exe bug)" || return 1
    p1 bug adlb 4 "$RUNS" syncsub "$SAMPLE" && p1 bug adlb 6 "$RUNS" syncsub "$SAMPLE" \
        && p1 bug adlb-other 4 "$OTHER_RUNS" other "$OTHER_SAMPLE"
}
step_fixed() {
    need_file "$(exe fix)" || return 1
    p1 fix fix-adlb 4 "$OTHER_RUNS" syncsub $((SAMPLE / 2))
}
step_stress() {
    local v
    for v in bug fix; do
        need_file "$(exe $v)" || return 1
        rm -f "$OUT/stress-$v.csv"
        python3 experiments/history_stress.py --version $v --label adlb --mpi openmpi --np 4 6 --runs "$STRESS_RUNS" \
            --timeout "$TIMEOUT" --jobs "$JOBS" --judge "$H/judge.py" --out "$OUT/stress-$v.csv" -- "$(exe $v)" 40 48 \
        && python3 experiments/history_stress.py --version $v --label adlb2000 --mpi openmpi --np 4 --runs "$BIG_RUNS" \
            --settings plain --timeout 300 --jobs "$JOBS" --judge "$H/judge.py" --out "$OUT/stress-$v.csv" -- "$(exe $v)" 2000 48 \
        && python3 experiments/history_stress.py --version $v --label "adlb400 one processor" --mpi openmpi --np 4 --runs "$ONE_RUNS" \
            --settings plain --timeout 300 --jobs 1 --judge "$H/judge.py" --out "$OUT/stress-$v.csv" \
            -- /usr/bin/taskset -c "$ONE_CPU" "$(exe $v)" 400 48 || return 1
    done
}
step_summary() {
    local s="$R/experiments/results/latent"
    rm -f "$s/swiftt-predict.csv" "$s/swiftt-stress.csv"
    python3 -I experiments/latent/summarize.py predict --case swiftt --source 4b3882d --kind syncsub --out "$s/swiftt-predict.csv" \
        "$OUT"/predict-adlb-np4.csv "$OUT"/predict-adlb-np6.csv \
    && python3 -I experiments/latent/summarize.py predict --case swiftt --source 4b3882d --kind other --out "$s/swiftt-predict.csv" \
        "$OUT"/predict-adlb-other-np4.csv \
    && python3 -I experiments/latent/summarize.py predict --case swiftt --source 4b3882d+fix.diff --kind syncsub \
        --out "$s/swiftt-predict.csv" "$OUT"/predict-fix-adlb-np4.csv \
    && python3 -I experiments/latent/summarize.py stress --case swiftt --source 4b3882d --out "$s/swiftt-stress.csv" "$OUT/stress-bug.csv" \
    && python3 -I experiments/latent/summarize.py stress --case swiftt --source 4b3882d+fix.diff --out "$s/swiftt-stress.csv" "$OUT/stress-fix.csv"
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) step_predict;; fixed) step_fixed;; stress) step_stress;;
        summary) step_summary;;
        all) step_fetch && step_build && step_predict && step_fixed && step_stress && step_summary;;
        *) echo "usage: $0 [fetch|build|predict|fixed|stress|summary|all]" >&2; exit 2;;
    esac || exit 1
done
