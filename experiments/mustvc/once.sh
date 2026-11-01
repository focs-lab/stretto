#!/usr/bin/env bash
# One run of a program under MUST-vc, in a fresh directory, inside the mustvc image:
#   experiments/mustvc/once.sh NP TIMEOUT OUTDIR PROGRAM [ARGS...]
# The layout is the artifact's (/home/user/tests/layout_<ANALYSIS>.xml, ANALYSIS clock by
# default) with its MPI level (thread_place) sized to NP, since mustrun takes the number of
# processes from the layout; the options are the artifact's RUN lines' (--must:openmp,
# --must:instant-logging warning) and its lit environment (MUST_OUTPUT=stdout; MUST_TIMEOUT=5,
# which ends the application after 5 s, unless MUST_TIMEOUT is set: experiments/mustvc/apps.sh
# sets 0, no limit).
# NATIVE=1 runs the program without MUST (mpiexec -n NP) for the time and the outcome.
# Prints result,rc,seconds,errors,warnings,early,messages: result FINISHED (mustrun's closing
# line after the program ended; natively exit status 0), TIMEOUT, CRASHED (MPICH's launcher saw
# a process end badly), or rc=N; errors and warnings count [MUST-REPORT] lines; early is 1 when
# MUST warned that the OpenMP runtime started before MPI_Init (its threaded checks are then not
# supported); messages names the reports' texts with counts. OUTDIR keeps run.out and run.err.
np=$1 to=$2 out=$3; shift 3
mkdir -p "$out"; w=$(mktemp -d); cd "$w" || exit 1
export MUST_TIMEOUT=${MUST_TIMEOUT-5} MUST_OUTPUT=stdout
t0=$(date +%s.%N)
if [ "${NATIVE:-0}" = 1 ]; then
  /usr/bin/timeout -k 5 "$to" mpiexec -n "$np" "$@" < /dev/null > run.out 2> run.err
  rc=$?
else
  sed "/place-name=\"thread_place\"/s/size=\"[0-9]*\"/size=\"$np\"/" "/home/user/tests/layout_${ANALYSIS:-clock}.xml" > layout.xml
  /usr/bin/timeout -k 5 "$to" mustrun --must:instant-logging warning --must:openmp --must:layout "$w/layout.xml" "$@" \
    < /dev/null > run.out 2> run.err
  rc=$?
fi
t1=$(date +%s.%N)
secs=$(echo "$t1 - $t0" | bc | awk '{printf "%.2f", $1}')
if [ $rc = 124 ] || [ $rc = 137 ]; then result=TIMEOUT
elif grep -q "BAD TERMINATION" run.out run.err 2>/dev/null; then result=CRASHED
elif [ "${NATIVE:-0}" = 1 ]; then [ $rc = 0 ] && result=FINISHED || result="rc=$rc"
elif grep -q "\[MUST\] Execution finished" run.out run.err 2>/dev/null; then result=FINISHED
else result="rc=$rc"; fi
ne=$(cat run.out run.err | grep -cE '\[MUST-REPORT\] Error'); nw=$(cat run.out run.err | grep -cE '\[MUST-REPORT\] Warning')
msgs=$(cat run.out run.err | grep -oE '\[MUST-REPORT\] (Error|Warning).*' | sed -E 's/^\[MUST-REPORT\] (Error|Warning)[^:]*: (from: call [^:]*: )?//; s/(Found [^.]*|[^.]*)\..*/\1/' \
       | cut -c1-90 | sort | uniq -c | awk '{n=$1; $1=""; sub(/^ /, ""); gsub(/[,;]/, " "); printf "%s%s*%d", s, $0, n; s=";"}')
early=0; grep -q "OpenMP runtime has been initialized before the call to MPI_Init" run.out run.err 2>/dev/null && early=1
cp -f run.out run.err "$out/" 2>/dev/null
cd / && rm -rf "$w"
echo "$result,$rc,$secs,$ne,$nw,$early,$msgs"
