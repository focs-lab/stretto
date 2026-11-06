#!/usr/bin/env bash
# One MUST run inside a MUST image (scripts/must_run.sh starts it in a container; a loop already
# inside the image, as experiments/corrbench/must.sh, calls it directly): builds prog.c in the
# current directory with MPICH's mpicc (-I.. and STRETTO_MUST_CFLAGS on the include path), runs
# it under mustrun and prints one line, the verdict of scripts/must_run.sh without its MUST:
# prefix: OK | ERROR:<summary> | TIMEOUT | CE:<msg> | RE:<msg> | HARNESS-ERROR:<msg>.
# usage: scripts/must_once.sh <np> <timeout-seconds>
# Environment: STRETTO_MUST_DIR (MUST's installation), STRETTO_MUST_TSAN (1: MUST's
# ThreadSanitizer analysis, scripts/must_run.sh), STRETTO_MUST_CFLAGS (extra compiler flags).
np="$1"; to="$2"
export PATH=$STRETTO_MUST_DIR/bin:$PATH
cc=(mpicc.mpich); tsan=()
[ "${STRETTO_MUST_TSAN:-0}" = 1 ] && { cc=(must-tsan-cc --wrap-target mpicc.mpich); tsan=(--must:tsan); }
# shellcheck disable=SC2086
"${cc[@]}" -O0 -g -I.. ${STRETTO_MUST_CFLAGS:-} -o prog prog.c -lm 2> build.err || { echo "CE:$(head -c 160 build.err | tr "\n" " ")"; exit 0; }
t0=$SECONDS
/usr/bin/timeout -k 5 "$to" mustrun -np "$np" --must:mpiexec mpirun.mpich --must:distributed "${tsan[@]}" --must:output json ./prog > run.out 2> run.err
rc=$?; took=$((SECONDS - t0))
json=""
[ -s MUST_Output.json ] && json="$(python3 - <<'PY'
import json, sys
try:
    d = json.load(open('MUST_Output.json'))
except Exception as e:
    print('RE:bad-json'); sys.exit(0)
msgs = d.get('messages', d if isinstance(d, list) else [])
errs = [m for m in msgs if str(m.get('type', m.get('severity', ''))).lower().startswith('error')
        or m.get('error_id') == 'MUST_WARNING_DATARACE']
if errs:
    print('ERROR:' + '; '.join(sorted({str(m.get('text', m.get('message', '')))[:80] for m in errs})[:3]))
else:
    print('OK')
PY
)"
# an error MUST reported stands; otherwise a run the timeout killed is a TIMEOUT, whatever JSON
# it left (MUST writes its JSON as it goes, so a killed run can leave one without the error it
# had not reached); a kill before the timeout is a memory cap, no verdict
if grep -qi "detected a deadlock" run.out run.err 2>/dev/null; then echo "ERROR:deadlock (MUST detected a deadlock)"
elif [ "${json%%:*}" = ERROR ]; then echo "$json"
elif grep -qa "ThreadSanitizer: data race" run.out run.err 2>/dev/null; then echo "ERROR:data race (ThreadSanitizer report)"
elif ! grep -qF "[MUST] Executing application" run.out 2>/dev/null; then
    echo "HARNESS-ERROR:MUST stopped before it started the program (status $rc): $(tail -c 160 run.err | tr "\n" " ")"
elif { [ $rc -eq 124 ] || { [ $rc -eq 137 ] && [ $took -ge "$to" ]; }; } && [ -s gti_layer_1.profile ]; then
    echo "HARNESS-ERROR:hung after MUST had finished (gti_layer_1.profile written), killed by the timeout"
elif { [ $rc -eq 124 ] || { [ $rc -eq 137 ] && [ $took -ge "$to" ]; }; } && grep -q "8shutdownENS_14GTI_FLUSH_TYPE" run.err 2>/dev/null; then
    echo "HARNESS-ERROR:hung in the shutdown of the MUST tool layer, after the program had finished, killed by the timeout"
elif [ $rc -eq 124 ] || { [ $rc -eq 137 ] && [ $took -ge "$to" ]; }; then echo TIMEOUT
elif [ $rc -eq 137 ]; then echo "HARNESS-ERROR:killed after ${took} s, before the timeout (memory cap?)"
elif [ -n "$json" ]; then echo "$json"
else echo "RE:$(tail -c 200 run.err | tr "\n" " ")"; fi
