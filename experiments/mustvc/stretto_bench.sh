#!/usr/bin/env bash
# Stretto on the small programs MUST-vc is compared on: the artifact's 50 test programs, the
# 103 OpenMP programs of MPI-CorrBench a05644b, and Stretto's own idioms with threads. Inside the experiment image
# (experiments/container/Dockerfile: Open MPI 4.1.6, gcc 13 with libgomp, whose entry points
# the tracer follows) with this repository at REPO (the tracer built for Open MPI):
#   experiments/mustvc/stretto_bench.sh SUITE DIR OUT.csv WORK
# SUITE is artifact (DIR: the artifact's tests/, programs under MPI-Corrbench/ and
# own_checks/), corrbench (DIR: micro-benches/0-level/openmp) or idioms (DIR: idioms/, the
# idioms 15-47 that have a flags file). Each program is built with -g -O0 -fopenmp (an idiom
# with its flags), DIR (and the program's directory) on the include path, and run on 2
# processes, as under MUST-vc (an idiom on its idiom_np, scripts/env.sh, with its env file),
# with the OpenMP threads of its RUN line (artifact) or 2, REPS traced runs, each analysed by `stretto analyze --json`; one untraced run
# for the time. Settings: REPS (3), TIMEOUT (60 s per run), ATIMEOUT (600 s per analysis),
# ONLY (a regular expression on the path), STRETTO_COMMIT (the commit column).
# Row: suite,group,label,program,threads,run,result,seconds,status,errors,warnings,classes,
# analysis_seconds,commit. run 0 is the untraced run. errors counts findings of an error class
# (stretto.report.ERROR_CLASSES), warnings the others; classes lists class@level*count.
set -u
SUITE=${1:?usage: stretto_bench.sh artifact|corrbench|idioms DIR OUT.csv WORK}; D=${2:?}; OUT=${3:?}; W=${4:?}
REPS=${REPS:-3}; TO=${TIMEOUT:-60}; ATO=${ATIMEOUT:-600}
R=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || echo unknown)}
LIB=$R/tracer/bin/openmpi/libstretto-trace.so
[ -f "$LIB" ] || { echo "no tracer at $LIB" >&2; exit 1; }
export PYTHONPATH=$R/analysis OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
mkdir -p "$W/bin"
[ -e "$OUT" ] || echo "suite,group,label,program,threads,run,result,seconds,status,errors,warnings,classes,analysis_seconds,commit" > "$OUT"
case $SUITE in
  artifact) list=$(cd "$D" && find MPI-Corrbench own_checks -name '*.c' -o -name '*.cpp' | sort);;
  corrbench) list=$(cd "$D" && find . -name '*.c' | sed 's|^\./||' | sort);;
  idioms) list=$(cd "$D" && for f in [1-4][0-9]_*/flags; do k=${f%%_*}; [ "$k" -ge 15 ] && echo "${f%/flags}/main.c"; done)
          source <(grep '^idiom_np()' "$R/scripts/env.sh");;
  *) echo "unknown suite $SUITE" >&2; exit 2;;
esac
summary() {   # report.json -> status,errors,warnings,classes
  python3 -I - "$1" <<'PY'
import collections, json, sys
sys.path.insert(0, __import__("os").environ["PYTHONPATH"])
from stretto.report import ERROR_CLASSES
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    print("no-report,,,"); raise SystemExit
f = d.get("findings", [])
c = collections.Counter(f"{x['cls']}@{x['level']}" for x in f)
e = sum(x["cls"] in ERROR_CLASSES for x in f)
print(f"{str(d.get('status', '?')).replace(',', '+')},{e},{len(f) - e},{';'.join(f'{k}*{v}' for k, v in sorted(c.items()))}")
PY
}
for f in $(grep -E "${ONLY:-.}" <<< "$list"); do
  if [ "$SUITE" = artifact ]; then
    case $f in MPI-Corrbench/*) suite=artifact-COBE;; *) suite=artifact-Own;; esac
    group=$(echo "$f" | cut -d/ -f2)
    th=$(sed -n 's/.*OMP_NUM_THREADS=\([0-9]*\).*/\1/p' "$D/$f" | head -1)
  else
    suite=$SUITE; group=$(echo "$f" | cut -d/ -f1); th=2
  fi
  th=${th:-2}; np=2; flags=-fopenmp; envs=()
  if [ "$SUITE" = idioms ]; then
    np=$(idiom_np "${group%%_*}"); flags=$(cat "$D/$group/flags")
    [ -f "$D/$group/env" ] && while read -r e; do envs+=(-x "$e"); done < "$D/$group/env"
  fi
  case $f in */correct/*) label=correct;; *) label=erroneous;; esac
  name=$(echo "${f%.*}" | sed 's|/|__|g')
  case $f in *.cpp) cc=/usr/bin/mpicxx.openmpi;; *) cc=/usr/bin/mpicc.openmpi;; esac
  # shellcheck disable=SC2086
  if ! $cc -g -O0 $flags -I"$D" -I"$(dirname "$D/$f")" "$D/$f" -o "$W/bin/$name" -lm > "$W/bin/$name.build.log" 2>&1; then
    echo "$suite,$group,$label,$f,$th,0,BUILD-FAILED,,,,,,,$COMMIT" >> "$OUT"; continue
  fi
  for k in $(seq 0 "$REPS"); do
    o=$W/out/$name/$k; rm -rf "$o"; mkdir -p "$o/run" "$o/trace"
    pre=""; [ "$k" -gt 0 ] && pre="-x LD_PRELOAD=$LIB -x STRETTO_TRACE_DIR=$o/trace"
    t0=$(date +%s.%N)
    # shellcheck disable=SC2086
    (cd "$o/run" && timeout -k 5 "$TO" mpirun -np "$np" -x OMP_NUM_THREADS="$th" ${envs[@]+"${envs[@]}"} $pre "$W/bin/$name" > ../stdout.log 2> ../stderr.log < /dev/null)
    rc=$?; t1=$(date +%s.%N)
    res=FINISHED; [ $rc = 124 ] || [ $rc = 137 ] && res=TIMEOUT; [ $rc != 0 ] && [ $res = FINISHED ] && res="rc=$rc"
    secs=$(echo "$t1 - $t0" | bc | awk '{printf "%.2f", $1}')
    if [ "$k" = 0 ]; then echo "$suite,$group,$label,$f,$th,0,$res,$secs,,,,,,$COMMIT" >> "$OUT"; continue; fi
    a0=$(date +%s.%N)
    timeout "$ATO" python3 -m stretto analyze --json "$o/trace" > "$o/report.json" 2> "$o/report.err"
    a1=$(date +%s.%N)
    echo "$suite,$group,$label,$f,$th,$k,$res,$secs,$(summary "$o/report.json"),$(echo "$a1 - $a0" | bc | awk '{printf "%.1f", $1}'),$COMMIT" >> "$OUT"
  done
done
