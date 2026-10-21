#!/usr/bin/env bash
# Run MUST on one MPI program via the MPI-BugBench container (MUST 1.9.2 + MPICH inside),
# because MUST 1.11.2 built against this host's Open MPI hangs in MPI_Init under PnMPI.
# usage: scripts/must_run.sh <np> <source.c> [timeout-seconds]
# MUST_IMAGE (default mbb-must:latest) and MUST_CPUS (optional cpuset) select image and processors;
# MUST_PREFIX is MUST's installation in the image (default /MBB-builds/MUST192; the MUST 1.11.2
# image of experiments/must/ has /MBB-builds/MUST1112). MUST_TSAN=1 adds MUST's ThreadSanitizer
# analysis: the program is compiled with must-tsan-cc --wrap-target mpicc.mpich and run with
# --must:tsan, and a data race counts as an error, whether MUST's report has it
# (MUST_WARNING_DATARACE, which MPI-BugBench's harness maps to no error class) or only
# ThreadSanitizer's own report does. The latter is the case here: as MUST's own test suite does
# for clang 14 and later, the run sets MUST_DISABLE_TSAN_ONREPORT=1, since with clang 14 MUST's
# hook into ThreadSanitizer's reports misreads them and the run hangs after the first race.
# MUST_CACHE is a host directory for MUST's user cache, where MUST builds the tool layouts it
# has no prebuilt one for (the TSan ones), so that each is built once.
# prints one line: MUST:<verdict> where verdict is OK | ERROR:<summary> | TIMEOUT | CE:<msg> | RE:<msg>
set -uo pipefail
np="$1"; src="$(readlink -f "$2")"; to="${3:-120}"
img="${MUST_IMAGE:-mbb-must:latest}"
work="$(mktemp -d)"
mkdir -p "$work/src"; cp "$(dirname "$src")"/*.h "$(dirname "$src")"/../*.h "$work/" 2>/dev/null; cp "$src" "$work/src/prog.c"
cat > "$work/parse.py" <<'EOF'
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
EOF
tsan_env=(); [ "${MUST_TSAN:-0}" = 1 ] && tsan_env=(-e MUST_DISABLE_TSAN_ONREPORT=1)
docker run --rm --memory 4g ${MUST_CPUS:+--cpuset-cpus "$MUST_CPUS"} --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$work:/w" -w /w/src \
  -e STRETTO_MUST_DIR="${MUST_PREFIX:-/MBB-builds/MUST192}" -e STRETTO_MUST_TSAN="${MUST_TSAN:-0}" \
  ${tsan_env[@]+"${tsan_env[@]}"} \
  ${MUST_CACHE:+-v "$MUST_CACHE:/cache" -e XDG_CACHE_HOME=/cache} "$img" bash -c '
  export PATH=$STRETTO_MUST_DIR/bin:$PATH
  cc=(mpicc.mpich); tsan=()
  [ "$STRETTO_MUST_TSAN" = 1 ] && { cc=(must-tsan-cc --wrap-target mpicc.mpich); tsan=(--must:tsan); }
  "${cc[@]}" -O0 -g -I.. -o prog prog.c -lm 2> build.err || { echo "CE:$(head -c 160 build.err | tr "\n" " ")"; exit 0; }
  /usr/bin/timeout -k 5 '"$to"' mustrun -np '"$np"' --must:mpiexec mpirun.mpich --must:distributed "${tsan[@]}" --must:output json ./prog > run.out 2> run.err
  rc=$?
  json=""; [ -s MUST_Output.json ] && json="$(python3 ../parse.py)"
  if grep -qi "detected a deadlock" run.out run.err 2>/dev/null; then echo "ERROR:deadlock (MUST detected a deadlock)"
  elif [ "${json%%:*}" = ERROR ]; then echo "$json"
  elif grep -qa "ThreadSanitizer: data race" run.out run.err 2>/dev/null; then echo "ERROR:data race (ThreadSanitizer report)"
  elif [ -n "$json" ]; then echo "$json"
  elif [ $rc -eq 124 ] || [ $rc -eq 137 ]; then echo TIMEOUT
  else echo "RE:$(tail -c 200 run.err | tr "\n" " ")"; fi
' 2>/dev/null | tail -1 | sed 's/^/MUST:/'
[ -n "${MUST_KEEP:-}" ] && echo "kept $work" || rm -rf "$work"
