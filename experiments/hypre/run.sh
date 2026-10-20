#!/usr/bin/env bash
# A real bug predicted from one clean run: the ParaSails hang of HYPRE 2.10.1.
#
# ParaSails' two row exchanges, ExchangePrunedRows and ExchangeStoredRows
# (distributed_ls/ParaSails/ParaSails.c), both send their row requests with tag 222 and
# receive them with MPI_Probe(MPI_ANY_SOURCE, 222) followed by MPI_Recv from the probed
# source. A rank that has finished the pruned exchange can send its stored-rows request while
# a neighbour is still probing for pruned-rows requests, and the probe takes it: the run
# hangs. The bug cost a production code months of debugging ("Noise Injection Techniques to
# Expose Subtle and Unintended Message Races", PPoPP 2017, which reports 291 diagnosis jobs and
# 10,392 compute-node hours) and was fixed in 2.11.0 by giving the two exchanges different tags
# (upstream commit bc3158ee). With ParaSails logging on (the ij default), a statistics
# Allreduce between the exchanges closes the window, so the driver runs with -pout 0.
#
# Steps, inside the experiment container (experiments/container/run.sh), from the repository
# root, MPICH build (HYPRE 2.10.1 calls MPI-1 functions Open MPI 4 removed):
#   fetch    download both versions, check the archives' sha256
#   build    configure and make each version with the MPICH wrappers, -j8
#   trace    one run of test/ij -solver 8 -pout 0 at 4 ranks per version, traced
#   analyse  stretto analyze on both traces
#   replay   the alternatives at the pruned exchange's second probe (rank 1 and rank 2), each
#            with its control (the probe pinned to the sender it saw in the run) and the benign
#            alternative at the first probe; 60 s each
# Writes experiments/results/hypre/{report4.txt,report4-211.txt,replays.csv,...}.
# usage: experiments/hypre/run.sh [fetch|build|trace|analyse|replay|all]
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
H="${HYPRE_DIR:-$R/third_party/apps/hypre}"
OUT=experiments/results/hypre; mkdir -p "$OUT" "$H"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || git -C "$R" rev-parse --short HEAD 2>/dev/null || echo unknown)}"
NP=4; ARGS=(-solver 8 -pout 0); TIMEOUT=60
TRACER="$R/tracer/bin/mpich/libstretto-trace.so"; PINLIB="$R/replay/bin/mpich/libstretto-pin.so"
declare -A SHA=([2.10.1]=69c50d785fccaebab9c220839b56d4ffef0666248988a668747e60c9250e7bdf
                [2.11.0]=d9d6999c60e6ac394e9751923e25af2f1d8dd1e84b000063be212737b4d1aa31)
src() { [ -f "$H/hypre-$1/configure" ] && echo "$H/hypre-$1" || echo "$H/hypre-$1/src"; }
tag() { [ "$1" = 2.10.1 ] && echo "" || echo "-211"; }

step_fetch() {
    for v in 2.10.1 2.11.0; do
        f="$H/hypre-$v.tar.gz"
        [ -f "$f" ] || curl -sSL -o "$f" "https://github.com/hypre-space/hypre/archive/refs/tags/v$v.tar.gz"
        echo "${SHA[$v]}  $f" | sha256sum -c - || return 1
        [ -d "$H/hypre-$v" ] || tar xzf "$f" -C "$H"
    done
}
step_build() {
    for v in 2.10.1 2.11.0; do
        (cd "$(src $v)" && ./configure CC=mpicc.mpich CXX=mpicxx.mpich F77=mpif77.mpich CFLAGS="-O2 -fcommon -g" \
             CXXFLAGS="-O2 -g" --without-fei --with-MPI > "$R/$OUT/configure$(tag $v).log" 2>&1 \
         && make -j8 > "$R/$OUT/make$(tag $v).log" 2>&1 && cd test && make ij > "$R/$OUT/ij$(tag $v).log" 2>&1) \
            || { echo "build of $v failed" >&2; return 1; }
    done
}
step_trace() {
    for v in 2.10.1 2.11.0; do
        d="$R/$OUT/trace$NP$(tag $v)"; rm -rf "$d"; mkdir -p "$d"
        (cd "$(src $v)/test" && /usr/bin/timeout 300 mpirun.openmpi --oversubscribe -np $NP -x LD_PRELOAD="$TRACER" \
             -x STRETTO_TRACE_DIR="$d" ./ij "${ARGS[@]}" > "$R/$OUT/run$NP$(tag $v).log" 2>&1)
        echo "$v traced: rc=$?, $(cat "$d"/rank-*.jsonl | wc -l) records"
    done
}
step_analyse() {
    for v in 2.10.1 2.11.0; do
        PYTHONPATH="$R/analysis" python3 -m stretto analyze -v "$OUT/trace$NP$(tag $v)" > "$OUT/report$NP$(tag $v).txt" 2> "$OUT/analyze$NP$(tag $v).err"
        echo "$v: $(grep -c probe-alt "$OUT/report$NP$(tag $v).txt") probe alternatives"
    done
}
step_replay() {
    # every probe alternative of the request exchange (tag 222) in report4.txt, the pins taken
    # from that report since probe ordinals belong to the run that was traced: an alternative
    # with a different size is a request of the other exchange ("alternative"), one of the same
    # shape a reordering inside the exchange ("benign"); each comes with its control, the same
    # probe pinned to the sender it saw in the run
    echo "pin,role,result,seconds,probe_found,residual,commit" > "$OUT/replays.csv"
    PYTHONPATH="$R/analysis" python3 - "$OUT/report$NP.txt" > "$OUT/replay-pins.txt" <<'PY'
import re, sys
text = open(sys.argv[1]).read()
for block in re.split(r"\n(?=\[)", text):
    m = re.match(r"\[\s*probe-alt\]\s+L1\s+probe\((\d+), \d+\) src=ANY tag=222 could observe send\((\d+), \d+\).* instead of send\((\d+), \d+\)", block)
    pin = re.search(r"STRETTO_PIN_PROBE=(\d+):(\d+):(\d+)", block)
    if not m or not pin:
        continue
    rank, pidx, alt = pin.groups()
    role = "alternative" if "different size" in block else "benign"
    print(f"{rank}:{pidx}:{alt},{role}")
    print(f"{rank}:{pidx}:{m.group(3)},control")
PY
    while IFS=, read -r pin role; do
        log="$OUT/replay-${pin//:/-}.log"; t0=$SECONDS
        (cd "$(src 2.10.1)/test" && /usr/bin/timeout -k 5 $TIMEOUT mpirun.openmpi --oversubscribe -np $NP \
             -x LD_PRELOAD="$PINLIB" -x STRETTO_PIN_PROBE=$pin ./ij "${ARGS[@]}" < /dev/null > "$R/$log" 2>&1)
        rc=$?; res=$([ $rc = 0 ] && echo FINISHED || { [ $rc = 124 ] && echo TIMEOUT || echo "rc=$rc"; })
        pidx=$(echo "$pin" | cut -d: -f2); rank=${pin%%:*}
        found=$(grep -c "rank $rank: applied probe pidx=$pidx\b" "$log")
        resid=$(grep -o "Final Relative Residual Norm = .*" "$log" | sed 's/.*= //')
        echo "$pin,$role,$res,$((SECONDS - t0)),$found,$resid,$STRETTO_COMMIT" | tee -a "$OUT/replays.csv"
    done < "$OUT/replay-pins.txt"
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; trace) step_trace;; analyse) step_analyse;; replay) step_replay;;
        all) step_fetch && step_build && step_trace && step_analyse && step_replay;;
        *) echo "usage: $0 [fetch|build|trace|analyse|replay|all]" >&2; exit 2;;
    esac
done
