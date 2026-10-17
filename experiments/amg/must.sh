#!/usr/bin/env bash
# MUST 1.9.2 on AMG at 8 ranks, in the MPI-BugBench container (MUST + MPICH): AMG is built
# there with the container's mpicc and run under mustrun with the same arguments as the
# traced run. Prints MUST:<verdict> like scripts/must_run.sh and keeps MUST's output in
# experiments/results/amg/must8-<layout>/ (verdict.txt is committed). MUST_ARGS selects the MUST
# layout (default --must:distributed; an empty value runs the centralised layout).
# usage: experiments/amg/must.sh [timeout-seconds]
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
to="${1:-1800}"; img="${MUST_IMAGE:-mbb-must:latest}"; margs="${MUST_ARGS---must:distributed}"
layout="$([ -z "$margs" ] && echo central || echo distributed)"
out="$R/experiments/results/amg/must8-$layout"; rm -rf "$out"; mkdir -p "$out"
work="$(mktemp -d)"; cp -r study/apps/AMG "$work/AMG"; rm -f "$work"/AMG/*/*.o "$work"/AMG/test/amg
docker run --rm --memory 16g ${MUST_CPUS:+--cpuset-cpus "$MUST_CPUS"} --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$work:/w" -v "$out:/out" -w /w/AMG "$img" bash -c '
  export PATH=/MBB-builds/MUST192/bin:$PATH OMP_NUM_THREADS=1
  sed -i "s|^CC = .*|CC = mpicc.mpich|; s|^INCLUDE_CFLAGS = .*|INCLUDE_CFLAGS = -O2 -DTIMER_USE_MPI -DHYPRE_HOPSCOTCH -DHYPRE_USING_PERSISTENT_COMM -DHYPRE_BIGINT|; s|^INCLUDE_LFLAGS = .*|INCLUDE_LFLAGS = -lm|" Makefile.include
  make -j8 > /out/build.log 2>&1 || { echo "CE:$(tail -c 200 /out/build.log | tr "\n" " ")"; exit 0; }
  cd test
  /usr/bin/timeout -k 5 '"$to"' mustrun -np 8 --must:mpiexec mpirun.mpich '"$margs"' --must:output json ./amg -P 2 2 2 -n 16 16 16 -problem 1 > /out/run.out 2> /out/run.err
  rc=$?
  cp -f MUST_Output.json /out/ 2>/dev/null
  if grep -qi "detected a deadlock" /out/run.out /out/run.err 2>/dev/null; then echo "ERROR:deadlock (MUST detected a deadlock)"
  elif [ -s MUST_Output.json ]; then python3 -c "
import json
d = json.load(open(\"MUST_Output.json\")); msgs = d.get(\"messages\", d if isinstance(d, list) else [])
errs = [m for m in msgs if str(m.get(\"type\", m.get(\"severity\", \"\"))).lower().startswith(\"error\")]
print(\"ERROR:\" + \"; \".join(sorted({str(m.get(\"text\", m.get(\"message\", \"\")))[:80] for m in errs})[:3]) if errs else \"OK\")"
  elif [ $rc -eq 124 ] || [ $rc -eq 137 ]; then echo TIMEOUT
  else echo "RE:$(tail -c 200 /out/run.err | tr "\n" " ")"; fi
' 2>/dev/null | tail -1 | sed 's/^/MUST:/' | tee "$out/verdict.txt"
rm -rf "$work"
