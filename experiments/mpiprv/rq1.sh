#!/usr/bin/env bash
# MPI-PRV's own evaluation, unchanged, inside its image (experiments/mpiprv/prv.sh):
#   rq1    its RQ1 script (/home/ubuntu/RQ1/TestallProp.sh: the ten properties, each on its
#          own buggy program: instrument with wac, run, analyse), REPS times on a copy of RQ1;
#          the artifact's README asks for five rounds and expects every property found in each
#   adept  its motivating example (/home/ubuntu/Example/adept-kernel-mpi): MPI-PRV.sh, which
#          analyses the trace shipped with the example (output-4.txt), then REPS fresh runs of
#          the instrumented kernel shipped with it (4 ranks, the command line of its MUST.sh),
#          each analysed with the example's pattern (Irecv Use Isend)
#   rq2    its RQ2/RQ3 measurement of MPI-PRV (RQ2-3/MPI-PRV_result.sh): the analyser on the
#          trace shipped for each application and process count, with that application's
#          pattern, and whether the pattern was reported, next to the artifact's own table
# Writes experiments/results/mpiprv/{rq1,adept-prv,rq2}.{csv,log}.
# usage (repository root, on the host): experiments/mpiprv/prv.sh experiments/mpiprv/rq1.sh [rq1|adept|rq2] ...
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
OUT=experiments/results/mpiprv; mkdir -p "$OUT"
REPS="${REPS:-5}"
COMMIT="${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || echo unknown)}"
W=/tmp/prv-rq1

step_rq1() {
    echo "rep,prop,program,result,matched,commit" > "$OUT/rq1.csv"
    : > "$OUT/rq1.log"
    for rep in $(seq 1 "$REPS"); do
        rm -rf "$W"; cp -r /home/ubuntu/RQ1 "$W"
        (cd "$W" && /usr/bin/timeout 1800 bash TestallProp.sh) > "$W/all.log" 2>&1
        sed 's/\x1b\[[0-9;]*m//g' "$W/all.log" > "$W/plain.log"
        { echo "### round $rep"; cat "$W/plain.log"; } >> "$OUT/rq1.log"
        for p in $(seq 1 10); do
            # the section of one property: from its header to the next one
            sect=$(awk -v h="[Executing] Prop$p " 'index($0, h) {on = 1; next} /\[Executing\] Prop/ {on = 0} on' "$W/plain.log")
            prog=$(sed -n 's/^wac -mop [^ ]* \([^ ]*\.c\).*/\1/p' "$W/Prop$p.sh" | head -1)
            if echo "$sect" | grep -q "Pattern Matched!"; then res=PASS; else res=FAIL; fi
            seq_=$(echo "$sect" | grep -A1 "Pattern Matched!" | tail -1 | sed 's/ *$//')
            echo "$rep,Prop$p,$prog,$res,\"$seq_\",$COMMIT" >> "$OUT/rq1.csv"
        done
    done
}

step_adept() {
    local E=/home/ubuntu/Example/adept-kernel-mpi
    echo "run,trace,events,result,matched,analysis_s,commit" > "$OUT/adept-prv.csv"
    : > "$OUT/adept-prv.log"
    rm -rf "$W"; cp -r "$E" "$W"
    (cd "$W" && bash MPI-PRV.sh) > "$W/shipped.log" 2>&1
    { echo "### MPI-PRV.sh (the trace shipped with the example)"; cat "$W/shipped.log"; } >> "$OUT/adept-prv.log"
    row() {  # row <run> <trace> <log>
        local res=FAIL; grep -q "Pattern Matched!" "$3" && res=PASS
        local m t
        m=$(grep -A1 "Pattern Matched!" "$3" | tail -1 | sed 's/ *$//')
        t=$(grep -oE "(Program )?[Ee]xecution time: [0-9.]+" "$3" | grep -oE "[0-9.]+$" | tail -1)
        echo "$1,$2,$(grep -c '^event' "$W/$2"),$res,\"$m\",$t,$COMMIT" >> "$OUT/adept-prv.csv"
    }
    row shipped output-4.txt "$W/shipped.log"
    for rep in $(seq 1 "$REPS"); do
        rm -f "$W/output.txt"
        (cd "$W" && /usr/bin/timeout 1800 mpirun -np 4 ./kernel -b stencil -o 27 -s 512 -d double -a overlapped) > "$W/run$rep.log" 2>&1
        cp "$W/output.txt" "$W/fresh$rep.txt"
        (cd "$W" && MPI-PRV -trace "fresh$rep.txt" -pattern ./MPIaspect/Pattern.txt -np 4) > "$W/prv$rep.log" 2>&1
        { echo "### fresh run $rep"; grep -v "^usea0$" "$W/run$rep.log"; cat "$W/prv$rep.log"; } >> "$OUT/adept-prv.log"
        row "fresh$rep" "fresh$rep.txt" "$W/prv$rep.log"
    done
}

step_rq2() {
    # what MPI-PRV_result.sh measures (each application directory's testMPIPRV-all.sh: MPI-PRV on
    # the trace shipped for each process count, with the directory's pattern, under
    # /usr/bin/time), plus what those scripts drop: whether the pattern was reported. Their
    # table writes the result column as a constant.
    local B=/home/ubuntu/RQ2-3
    echo "app,np,pattern,events,result,matched,analysis_s,peak_kb,artifact_events,artifact_s,artifact_result,commit" > "$OUT/rq2.csv"
    : > "$OUT/rq2.log"
    for d in DTG integrate diffusion2d Gauss_elim image-manip Congrad poisson_mpi Heat adept-kernel-mpi kf-ray/src \
             clustalW ParMETIS/build/Linux-x86_64/programs MiniAMR/ref; do
        local dir="$B/MPIBenchmark/$d" app=${d%%/*}
        local pat; pat=$(sed -n 's/^Pattern : {\(.*\)}/\1/p' "$dir/MPIaspect/Pattern.txt" | tr ',' ' ')
        for np in $(sed -n 's/^PROCESS_LIST=(\([^)]*\)).*/\1/p' "$dir/testMPIPRV-all.sh"); do
            [ -f "$dir/output-$np.txt" ] || { echo "$app,$np,$pat,,missing trace,,,,,,,$COMMIT" >> "$OUT/rq2.csv"; continue; }
            (cd "$dir" && /usr/bin/time -f "TIME %e %M" MPI-PRV -trace "output-$np.txt" -pattern ./MPIaspect/Pattern.txt -np "$np") > "$W.rq2" 2>&1
            { echo "### $app $np"; grep -v '^$' "$W.rq2" | tail -6; } >> "$OUT/rq2.log"
            local res=none m="" t k ev
            if grep -q "Pattern Matched!" "$W.rq2"; then res=matched; m=$(grep -A1 "Pattern Matched!" "$W.rq2" | tail -1 | sed 's/ *$//'); fi
            grep -q "Pattern Not Matched!" "$W.rq2" || [ $res = matched ] || res=error
            read -r t k < <(sed -n 's/^TIME //p' "$W.rq2")
            ev=$(grep -c '^event' "$dir/output-$np.txt")
            local art; art=$(grep "^$app,$np," "$B/MPIPRV_result.csv" | head -1 | awk -F, '{print $6 "," $3 "," $7}')
            echo "$app,$np,$pat,$ev,$res,\"$m\",$t,$k,${art:-,,},$COMMIT" >> "$OUT/rq2.csv"
        done
    done
}

steps=("$@"); [ ${#steps[@]} -eq 0 ] && steps=(rq1 adept rq2)
for s in "${steps[@]}"; do "step_$s"; done
