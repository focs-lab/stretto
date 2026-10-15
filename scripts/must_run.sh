#!/usr/bin/env bash
# Run MUST on one MPI program via the MPI-BugBench container (MUST 1.9.2 + MPICH inside),
# because MUST 1.11.2 built against this host's Open MPI hangs in MPI_Init under PnMPI.
# usage: scripts/must_run.sh <np> <source.c> [timeout-seconds]
# MUST_IMAGE (default mbb-must:latest) and MUST_CPUS (optional cpuset) select image and processors.
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
errs = [m for m in msgs if str(m.get('type', m.get('severity', ''))).lower().startswith('error')]
if errs:
    print('ERROR:' + '; '.join(sorted({str(m.get('text', m.get('message', '')))[:80] for m in errs})[:3]))
else:
    print('OK')
EOF
docker run --rm --memory 4g ${MUST_CPUS:+--cpuset-cpus "$MUST_CPUS"} --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$work:/w" -w /w/src "$img" bash -c '
  export PATH=/MBB-builds/MUST192/bin:$PATH
  mpicc.mpich -O0 -g -I.. -o prog prog.c -lm 2> build.err || { echo "CE:$(head -c 160 build.err | tr "\n" " ")"; exit 0; }
  /usr/bin/timeout -k 5 '"$to"' mustrun -np '"$np"' --must:mpiexec mpirun.mpich --must:distributed --must:output json ./prog > run.out 2> run.err
  rc=$?
  if grep -qi "detected a deadlock" run.out run.err 2>/dev/null; then echo "ERROR:deadlock (MUST detected a deadlock)"
  elif [ -s MUST_Output.json ]; then python3 ../parse.py
  elif [ $rc -eq 124 ] || [ $rc -eq 137 ]; then echo TIMEOUT
  else echo "RE:$(tail -c 200 run.err | tr "\n" " ")"; fi
' 2>/dev/null | tail -1 | sed 's/^/MUST:/'
[ -n "${MUST_KEEP:-}" ] && echo "kept $work" || rm -rf "$work"
