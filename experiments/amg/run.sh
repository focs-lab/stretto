#!/usr/bin/env bash
# E5: trace AMG (LLNL, study/apps/AMG/test/amg, built by third_party/fetch.sh apps) at 8 ranks,
# analyse the trace, and replay the first probe alternative with its pin to check that it is
# realisable. Writes experiments/results/amg/{trace8,report8.txt,analyze8.err,summary8.txt,run8.log,confirm8.txt};
# summary8.txt and confirm8.txt are committed, the rest is not.
# usage: experiments/amg/run.sh [trace|analyse|summary|confirm|all]      (OMP_NUM_THREADS=1: one thread per rank)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
source scripts/env.sh
D=experiments/results/amg; mkdir -p "$D"
EXE=study/apps/AMG/test/amg
ARGS=(-P 2 2 2 -n 16 16 16 -problem 1)
export OMP_NUM_THREADS=1 OMPI_MCA_hwloc_base_binding_policy=none
step_trace() {
    [ -x "$EXE" ] || { echo "$EXE missing: third_party/fetch.sh apps" >&2; return 1; }
    need_lib "$TRACER" tracer
    rm -rf "$D/trace8"; mkdir -p "$D/trace8"
    TIMEOUT=600 mpi_run 8 "REMATCH_TRACE_DIR=$R/$D/trace8" "LD_PRELOAD=$TRACER" "OMP_NUM_THREADS=1" -- "$EXE" "${ARGS[@]}" > "$D/run8.log" 2>&1
    echo "traced run: $(classify_rc $? "$D/run8.log"), $(cat "$D"/trace8/rank-*.jsonl | wc -l) records"
}
step_analyse() {
    /usr/bin/time -f 'analysis %e s, %M KB' python3 -m rematch analyze --no-sp "$D/trace8" > "$D/report8.txt" 2> "$D/analyze8.err"
    step_summary
}
step_summary() {
    # summary8.txt is what docs/results.md reads and what is committed (the report is 1 MB)
    { head -1 "$D/report8.txt"
      echo "findings by class: $(grep -o '^\[ *[0-9a-z-]* *\]' "$D/report8.txt" | tr -d '[] ' | sort | uniq -c | awk '{printf "%s %s, ", $2, $1}' | sed 's/, $//')"
      echo "host: $(hostname), one process; $(tail -1 "$D/analyze8.err")"
    } > "$D/summary8.txt"
    cat "$D/summary8.txt"
}
step_confirm() {
    # the first probe alternative's pin; the replay must report it applied and finish
    need_lib "$PINLIB" replay
    pin="$(grep -m1 -o 'REMATCH_PIN_PROBE=[0-9:,]*' "$D/report8.txt")"
    [ -n "$pin" ] || { echo "no probe alternative to replay" | tee "$D/confirm8.txt"; return 0; }
    TIMEOUT=600 mpi_run 8 "$pin" "LD_PRELOAD=$PINLIB" "OMP_NUM_THREADS=1" -- "$EXE" "${ARGS[@]}" > "$D/run8pin.log" 2> "$D/run8pin.err"
    rc=$?
    { echo "pin: $pin"; grep '^\[rematch-pin\]' "$D/run8pin.err"; echo "result: $(classify_rc $rc "$D/run8pin.err")"; } | tee "$D/confirm8.txt"
}
for s in "${@:-all}"; do
    case "$s" in
        trace) step_trace;; analyse) step_analyse;; summary) step_summary;; confirm) step_confirm;;
        all) step_trace && step_analyse && step_confirm;;
        *) echo "usage: $0 [trace|analyse|summary|confirm|all]" >&2; exit 2;;
    esac
done
