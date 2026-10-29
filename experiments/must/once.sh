#!/usr/bin/env bash
# experiments/must/once.sh <np> <timeout> <check> <out dir> <program> [args]: one run under
# mustrun, in a fresh directory, inside a MUST image (experiments/must/apps.sh); prints
# result,mustrun_rc,seconds,errors,warnings,deadlock,value,wrong_result
# (STRETTO_MUST_SETUP: a directory whose contents are copied into the run's directory first)
np=$1 to=$2 check=$3 out=$4; shift 4
export PATH=$STRETTO_MUST_DIR/bin:$PATH OMP_NUM_THREADS=1
mkdir -p "$out"; w=$(mktemp -d); cd "$w"
[ -n "${STRETTO_MUST_SETUP:-}" ] && cp -a "$STRETTO_MUST_SETUP/." "$w/"
t0=$SECONDS
/usr/bin/timeout -k 5 "$to" mustrun -np "$np" --must:mpiexec mpirun.mpich $STRETTO_MUST_ARGS --must:output json "$@" \
    < /dev/null > run.out 2> run.err
rc=$?; took=$((SECONDS - t0))
# mustrun exits with the status MUST chose for its findings (its return code file), not with the
# program's: a run counts as finished when mustrun printed its closing line after the program ended,
# and as crashed when MPICH's launcher reported a process that ended badly (MUST's own tool processes
# included; mustrun prints its closing line then too)
if [ $rc = 124 ] || { [ $rc = 137 ] && [ $took -ge "$to" ]; }; then result=TIMEOUT
elif [ $rc = 137 ]; then result=KILLED
elif grep -q "BAD TERMINATION OF ONE OF YOUR APPLICATION PROCESSES" run.out run.err 2>/dev/null; then result=CRASHED
elif grep -q "^\[MUST\] Execution finished" run.err run.out 2>/dev/null; then result=FINISHED
else result="rc=$rc"; fi
cp -f run.out run.err "$out/"; cp -f MUST_Output.json "$out/" 2>/dev/null; [ -f output ] && cp -f output "$out/"
classes=$(python3 - <<'PY'
import collections, json
try:
    d = json.load(open("MUST_Output.json"))
except Exception:
    print("no-report|no-report"); raise SystemExit
msgs = d.get("messages", d if isinstance(d, list) else [])
c = {"error": collections.Counter(), "warning": collections.Counter()}
for m in msgs:
    kind = str(m.get("type", "")).lower()
    k = "error" if kind.startswith("error") else "warning" if kind.startswith("warning") else None
    if k:
        c[k][f"{m.get('error_id', '?')}({(m.get('from') or {}).get('call', '?')})"] += 1
print("|".join(";".join(f"{n}*{v}" for n, v in sorted(c[k].items())) for k in ("error", "warning")))
PY
)
dead=False
grep -qi "detected a deadlock" run.out run.err 2>/dev/null && dead=True
case "$classes" in *DEADLOCK*) dead=True;; esac
txt=$(cat run.out run.err)
value= wrong=
case "$check" in
    residual) value=$(sed -n 's/.*Final Relative Residual Norm = *\([^ ]*\).*/\1/p' <<< "$txt" | head -1);;
    iterations) value=$(sed -n 's/.*[Ii]terations *\([0-9]*\).*/\1/p' <<< "$txt" | head -1);;
    mgnorm)
        # MG 2.4.1's class S reference norm, relative 1e-8 (experiments/tables.py, MG_REFERENCE)
        value=$(sed -n 's/.*L2 Norm is *\([^ ]*\).*/\1/p' <<< "$txt" | head -1)
        [ -n "$value" ] && wrong=$(python3 -c "print(abs(float('$value'.replace('D','E')) - 0.530770700573e-4) / 0.530770700573e-4 > 1e-8)");;
    dealii)
        # the test's output, normalised as deal.II's harness does, against the expected output
        if [ -f output ]; then
            perl -p "$STRETTO_DEALII_NORMALIZE" output > normalized
            value=$(awk 'NR == FNR { a[FNR] = $0; n = FNR; next } { if (!(FNR in a) || a[FNR] != $0) d++; m = FNR }
                         END { if (n > m) d += n - m; print d + 0 }' normalized "$STRETTO_DEALII_EXPECTED")
            # judged only on a run that finished: a killed run leaves its file incomplete or empty
            [ "$result" = FINISHED ] && { [ "$value" = 0 ] && wrong=False || wrong=True; }
        fi;;
    match)
        # the campaign's verdict on its output (STRETTO_MUST_OK, a Python regex): value, how many
        # times the output has it (once per rank for Cabana's test, once for the others); judged
        # on a finished run only
        value=$(STRETTO_MUST_TEXT="$txt" python3 -c "import os, re; print(len(re.findall(os.environ['STRETTO_MUST_OK'], os.environ['STRETTO_MUST_TEXT'])))")
        [ "$result" = FINISHED ] && { [ "$value" -gt 0 ] && wrong=False || wrong=True; };;
    xerr)
        # pddrive3d's ||X - Xtrue|| / ||X||; above 1e-4, or missing from a finished run, is wrong
        value=$(sed -n 's/.*||X - Xtrue|| \/ ||X|| = *\([^ \t]*\).*/\1/p' <<< "$txt" | head -1)
        [ "$result" = FINISHED ] && wrong=$(python3 -c "print('$value' == '' or float('$value') > 1e-4)");;
    qe)
        # the rows of ph.x's elph_prt table that differ from the reference's (STRETTO_MUST_QE_REF,
        # one process; experiments/elph_rows.py)
        if grep -q "JOB DONE" run.out 2>/dev/null; then
            value=$(python3 /w/experiments/elph_rows.py run.out "$STRETTO_MUST_QE_REF")
            # judged once ph.x printed JOB DONE, whether or not mustrun ended after it
            { [ "$value" = 0 ] && wrong=False || wrong=True; }
        fi;;
    nbx)
        # the NBX driver's own check (experiments/dealii8929/nbx_calls.cc): value, the wrong answers
        # rank 0 counts after the last call; a wrong answer that a rank printed is wrong when rank 0
        # printed no count. Judged once the program printed either, whether or not mustrun ended
        value=$(sed -n 's/.*nbx_calls: [0-9]* calls on [0-9]* ranks, \([0-9]*\) wrong answers.*/\1/p' <<< "$txt" | tail -1)
        if [ -n "$value" ]; then { [ "$value" = 0 ] && wrong=False || wrong=True; }
        elif grep -q "^call [0-9]* rank [0-9]*: the answer of rank" <<< "$txt"; then wrong=True; fi;;
esac
# residual and iterations: the bug is a hang; a printed value is the run's own (no reference)
echo "$result,$rc,$took,${classes%%|*},${classes#*|},$dead,$value,$wrong"
