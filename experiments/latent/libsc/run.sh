#!/usr/bin/env bash
# Latent races in libsc's sc_notify (develop 6928417, the head since 22 May 2026), reached only
# through a driver over the public API (docs/findings.md, latent bugs).
#
# sc_notify_payload_nbx (src/sc_notify.c:1932 Issend, :1960 Iprobe(MPI_ANY_SOURCE, 220), :1990
# Ibarrier, :1996 Test) uses one fixed tag, and nothing orders one call after the other: a process
# whose Ibarrier completed starts the next call and Issends, while another, still in its loop,
# probes and takes that message for the old call. sc_notify_recursive_nary reuses its per-level
# tags (260 + level) in the next call the same way. sc_notify_payloadv_nbx Issends tag NBXV (221)
# but probes and receives NBX (220), and hangs every time (not a race; found by reading). NBX and
# NARY are opt-in (sc_notify_set_type or the global default; the default type PEX is safe), and
# the shipped callers either use PEX or put a collective between calls, so no shipped program
# reaches these. fix.diff, the proposed fix: an MPI_Barrier after the NBX loop and after the NARY
# recursion, and the NBXV tag in payloadv's probe and receive.
#
# The driver, notify2.c, makes two sc_notify calls in a row on MPI_COMM_WORLD (an MPI_Barrier
# before the first only) and checks every call's senders and payloads; rank 0 prints
# "notify2: OK" or the first "notify2: FAIL rank R call C" (judge.py).
#
# Steps, inside the experiment container, from the repository root, Open MPI:
#   fetch    CMake 3.28.3 (the image has none, sha256 checked) and libsc at 6928417 (git checks
#            the commit), and a copy with fix.diff applied
#   build    libsc with MPI (RelWithDebInfo, no tests or examples, system zlib) at both, -j$MAKE_JOBS,
#            and notify2 against each
#   predict  RUNS traced runs per setting, analysed at L1, every cross alternative
#            (classify.py: a probe of one call that would see a message of another, a flag flip
#            included) replayed with its horizon pins and with its control, TIMEOUT seconds each,
#            JOBS traced runs at a time (experiments/history_predict.py). Settings: NBX at 4, 6
#            and 8 processes, NARY with widths 3 2 2 at 6 and 8, NARY 2 2 2 (the default) at 6
#   fixed    predict on the fixed copy
#   stress   STRESS_RUNS untraced runs of both copies per setting (NBX at 6 and 8, NARY 3 2 2 and
#            2 2 2 at 6), plain and with random send and receive delays from the replay library,
#            and NBXV_RUNS plain runs of payloadv (nbxv, sorted = 0) at 4 and 6
#            (experiments/history_stress.py)
#   summary  the compact tables experiments/results/latent/libsc-{predict,stress}.csv
#   all      fetch build predict fixed stress summary
# The per-run tables go to experiments/results/latent/libsc/ (not committed).
# usage: experiments/latent/libsc/run.sh [fetch|build|predict|fixed|stress|summary|all]
#        RUNS (10), STRESS_RUNS (40), NBXV_RUNS (10), TIMEOUT (20), JOBS (1), MAKE_JOBS (8),
#        LIBSC_DIR (third_party/apps/latent-libsc)
set -uo pipefail
R="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$R"
H="$R/experiments/latent/libsc"
D="${LIBSC_DIR:-$R/third_party/apps/latent-libsc}"
OUT="$R/experiments/results/latent/libsc"; mkdir -p "$OUT"
RUNS="${RUNS:-10}"; STRESS_RUNS="${STRESS_RUNS:-40}"; NBXV_RUNS="${NBXV_RUNS:-10}"; TIMEOUT="${TIMEOUT:-20}"; JOBS="${JOBS:-1}"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
# the container refuses process_vm_readv (experiments/mfem332/run.sh); concurrent runs must not
# all bind to the first processors; more ranks than processors are allowed
export OMPI_MCA_btl_vader_single_copy_mechanism=none OMPI_MCA_hwloc_base_binding_policy=none OMPI_MCA_rmaps_base_oversubscribe=1
SC=692841759d9628f15c8eb9b65036aa6a837f1d26
CMAKE_TGZ=cmake-3.28.3-linux-x86_64.tar.gz
CMAKE_SHA=804d231460ab3c8b556a42d2660af4ac7a0e21c98a7f8ee3318a74b4a9a187a6
CMAKE="$D/cmake-3.28.3-linux-x86_64/bin/cmake"
exe() { echo "$D/notify2-$1"; }          # exe <bug|fix>

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    fetch_checked "$CMAKE_TGZ" "https://github.com/Kitware/CMake/releases/download/v3.28.3/$CMAKE_TGZ" "$CMAKE_SHA" || return 1
    [ -d cmake-3.28.3-linux-x86_64 ] || tar xzf "$CMAKE_TGZ" || return 1
    if [ ! -d libsc-bug ]; then
        rm -rf libsc-bug.part && git init -q libsc-bug.part \
            && git -C libsc-bug.part fetch -q --depth 1 https://github.com/cburstedde/libsc.git "$SC" \
            && git -C libsc-bug.part checkout -q FETCH_HEAD && [ "$(git -C libsc-bug.part rev-parse HEAD)" = "$SC" ] \
            && mv libsc-bug.part libsc-bug || { harness_error "cannot fetch libsc $SC"; return 1; }
    fi
    if [ ! -d libsc-fix ]; then
        cp -a libsc-bug libsc-fix.part && patch -s -p1 -d libsc-fix.part < "$H/fix.diff" \
            && mv libsc-fix.part libsc-fix || { rm -rf libsc-fix.part; return 1; }
    fi
    cd "$R"
}
step_build() {
    local v b i
    need_file "$CMAKE" || return 1
    for v in bug fix; do
        b="$D/build-$v"; i="$D/inst-$v"
        if [ ! -f "$i/lib/libsc.a" ]; then
            ("$CMAKE" -S "$D/libsc-$v" -B "$b" -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_INSTALL_PREFIX="$i" \
                 -DSC_ENABLE_MPI=ON -DCMAKE_C_COMPILER=/usr/bin/mpicc.openmpi -DMPI_C_COMPILER=/usr/bin/mpicc.openmpi \
                 -DSC_BUILD_TESTING=OFF -DSC_BUILD_EXAMPLES=OFF -DSC_USE_INTERNAL_ZLIB=OFF -DSC_USE_INTERNAL_JSON=OFF > "$b.log" 2>&1 \
             && "$CMAKE" --build "$b" -j "${MAKE_JOBS:-8}" >> "$b.log" 2>&1 && "$CMAKE" --install "$b" >> "$b.log" 2>&1) \
                || { echo "build of libsc ($v) failed ($b.log)" >&2; return 1; }
        fi
        /usr/bin/mpicc.openmpi -O2 -g -I"$i/include" "$H/notify2.c" -o "$(exe $v)" -L"$i/lib" -lsc -lm -lz \
            $( [ -f "$i/lib/libjansson.a" ] && echo -ljansson ) || return 1
    done
}
p1() {        # p1 <bug|fix> <label> <np> <notify2 args...>
    local v=$1 l=$2 np=$3; shift 3
    local ver=$l; [ "$v" = fix ] && ver=fix-$l
    rm -f "$OUT/predict-$ver-np$np.csv"
    python3 experiments/history_predict.py --version "$ver" --mpi openmpi --np "$np" --runs "$RUNS" --no-sp \
        --classify "$H/classify.py" --replay cross --sample 0 --timeout "$TIMEOUT" --trace-timeout 60 --jobs "$JOBS" \
        --judge "$H/judge.py" --out "$OUT/predict-$ver-np$np.csv" -- "$(exe "$v")" "$@"
}
predict() {   # predict <bug|fix>
    need_file "$(exe "$1")" || return 1
    local np
    for np in 4 6 8; do p1 "$1" nbx $np nbx 2 || return 1; done
    for np in 6 8; do p1 "$1" nary322 $np nary 2 3 2 2 || return 1; done
    p1 "$1" nary222 6 nary 2 2 2 2
}
step_stress() {
    local v s l np args
    for v in bug fix; do
        need_file "$(exe $v)" || return 1
        rm -f "$OUT/stress-$v.csv"
        for s in "nbx:6:nbx 2" "nbx:8:nbx 2" "nary322:6:nary 2 3 2 2" "nary222:6:nary 2 2 2 2"; do
            IFS=: read -r l np args <<< "$s"
            # shellcheck disable=SC2086
            python3 experiments/history_stress.py --version $v --label $l --mpi openmpi --np $np --runs "$STRESS_RUNS" \
                --timeout 30 --jobs "$JOBS" --judge "$H/judge.py" --out "$OUT/stress-$v.csv" -- "$(exe $v)" $args || return 1
        done
        python3 experiments/history_stress.py --version $v --label nbxv --mpi openmpi --np 4 6 --runs "$NBXV_RUNS" \
            --settings plain --timeout "$TIMEOUT" --jobs "$JOBS" --judge "$H/judge.py" --out "$OUT/stress-$v.csv" \
            -- "$(exe $v)" nbxv 1 || return 1
    done
}
step_summary() {
    local s="$R/experiments/results/latent"
    rm -f "$s/libsc-predict.csv" "$s/libsc-stress.csv"
    python3 -I experiments/latent/summarize.py predict --case libsc --source 6928417 --out "$s/libsc-predict.csv" \
        "$OUT"/predict-n*.csv || return 1
    python3 -I experiments/latent/summarize.py predict --case libsc --source 6928417+fix.diff --out "$s/libsc-predict.csv" \
        "$OUT"/predict-fix-*.csv || return 1
    python3 -I experiments/latent/summarize.py stress --case libsc --source 6928417 --out "$s/libsc-stress.csv" "$OUT/stress-bug.csv" \
        && python3 -I experiments/latent/summarize.py stress --case libsc --source 6928417+fix.diff --out "$s/libsc-stress.csv" "$OUT/stress-fix.csv"
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) predict bug;; fixed) predict fix;; stress) step_stress;;
        summary) step_summary;;
        all) step_fetch && step_build && predict bug && predict fix && step_stress && step_summary;;
        *) echo "usage: $0 [fetch|build|predict|fixed|stress|summary|all]" >&2; exit 2;;
    esac || exit 1
done
