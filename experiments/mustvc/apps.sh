#!/usr/bin/env bash
# MUST-vc on the four thread bugs Stretto found, in the cells of their own series, plain and
# fixed: each run once without MUST (mpiexec, the time and the outcome) and once under mustrun
# with the clock layout, alternating. Inside the mustvc image, with the builds of
# build-apps.sh (SLATE and Uintah under /mvc, PaRSEC and DLA-Future at /h, mounted as they were
# built):
#   experiments/mustvc/apps.sh APP OUT.csv WORK
# APP is slate, parsec, dlaf or uintah. Settings: RUNS (5), TIMEOUT (seconds per MUST run, 900),
# NTIMEOUT (per run without MUST, 300), CELLS (names, default all of APP's), STRETTO_COMMIT.
# Row: app,cell,variant,np,threads,run,tool,result,outcome,seconds,errors,warnings,early,
# culprit,messages,commit (early: once.sh). tool is native or mustvc; result as once.sh says; outcome is the driver's
# verdict (ok, wrong, truncate, crash, hang); culprit counts MUST reports that name the pair of
# the bug (SLATE: concurrent receives with a tag in the collision range [2 A_mt, A_mt + A_nt - 1]
# = [20, 29]; PaRSEC: concurrent receives on a tag of the reshapes, which use the self
# communicator; DLA-Future: concurrent receives at all (only colliding tags give two pending
# receives of one source and tag); Uintah: concurrent collectives on one communicator).
set -u
APP=${1:?usage: apps.sh slate|parsec|dlaf|uintah OUT.csv WORK}; OUT=${2:?}; W=${3:?}
RUNS=${RUNS:-5}; TO=${TIMEOUT:-900}; NTO=${NTIMEOUT:-300}
R=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || echo unknown)}
export OPENBLAS_NUM_THREADS=1 MUST_TIMEOUT=0   # no MUST time limit (once.sh); TIMEOUT bounds a run
mkdir -p "$W"
[ -e "$OUT" ] || echo "app,cell,variant,np,threads,run,tool,result,outcome,seconds,errors,warnings,early,culprit,messages,commit" > "$OUT"
case $APP in
  slate) ALL="wide-2x2 wide-2x2-compat wide-4x1-compat";;
  parsec) ALL="vp2-mt vp2-mt-4ranks";;
  dlaf) ALL="2x1-offset-jitter 4x1-offset-jitter";;
  uintah) ALL="np4-t4 np4-t2";;
  *) echo "unknown application $APP" >&2; exit 2;;
esac
if [ "$APP" = uintah ]; then
  sed 's|<patches>   \[1,1,1\]       </patches>|<patches>   [2,2,2]       </patches>|; s|<maxTime>0.1</maxTime>|<maxTime>0.05</maxTime>|' \
    /mvc/src/uintah/src/StandAlone/inputs/Examples/solvertest1.ups > "$W/solvertest-8p.ups"
fi
setup() {   # cell variant -> NP TH CMD (array) and the environment
  local c=$1 v=$2
  unset HWLOC_SYNTHETIC REPS PARSEC_MCA_runtime_vpmap
  case $APP/$c in
    slate/wide-*)
      # -compat: SLATE with slate-trace-init.patch (build-apps.sh slate-compat)
      NP=4 TH=4; export OMP_NUM_THREADS=4; k=; case $c in *-compat) k=-compat;; esac
      export LD_LIBRARY_PATH=/mvc/slate/lib$k/$v
      case $c in wide-2x2*) g="2 2";; *) g="4 1";; esac
      CMD=(/mvc/slate/wide_lu$k 1000 2000 100 $g C 1 1);;
    parsec/vp2-mt)
      # the vpmap by PaRSEC's environment variable: mustrun takes " -- " for its own separator
      NP=1 TH=6; export HWLOC_SYNTHETIC="package:2 core:3 pu:1" REPS=5 PARSEC_MCA_runtime_vpmap=hwloc
      CMD=(/h/build/parsec-$v/tests/collections/reshape/reshape_check -N 120 -t 9 -c 6 -m 1);;
    parsec/vp2-mt-4ranks)
      NP=4 TH=2; export HWLOC_SYNTHETIC="package:2 core:1 pu:1" REPS=5 PARSEC_MCA_runtime_vpmap=hwloc
      CMD=(/h/build/parsec-$v/tests/collections/reshape/reshape_check -N 120 -t 9 -c 2 -m 1);;
    dlaf/2x1-offset-jitter)
      NP=2 TH=4; e=/h/build/copy_check; [ "$v" = fixed ] && e=/h/build/copy_check_fixed
      CMD=("$e" 512 512 64 64 64 64 2 1 0 0 1 0 5 200 --pika:threads=4 --pika:bind=none);;
    dlaf/4x1-offset-jitter)
      NP=4 TH=2; e=/h/build/copy_check; [ "$v" = fixed ] && e=/h/build/copy_check_fixed
      CMD=("$e" 1024 1024 64 64 64 64 4 1 0 0 1 0 5 200 --pika:threads=2 --pika:bind=none);;
    uintah/np4-t4|uintah/np4-t2)
      NP=4 TH=${c#np4-t}; b=$v; [ "$v" = fixed ] && b=fix
      CMD=(/mvc/uintah/$b/StandAlone/sus -nthreads "$TH" "$W/solvertest-8p.ups");;
  esac
}
outcome() {   # run.out run.err -> the driver's verdict
  local t; t=$(cat "$1" "$2" 2>/dev/null)
  case $APP in
    slate) r=$(sed -n 's/^rep 0 .* \(ok\|WRONG\)$/\1/p' <<< "$t" | head -1); [ "$r" = WRONG ] && r=wrong;;
    parsec|dlaf) r=$(sed -n 's/^summary reps [0-9]* wrong \([0-9]*\)$/\1/p' <<< "$t" | head -1)
                 [ -n "$r" ] && { [ "$r" -gt 0 ] && r=wrong || r=ok; };;
    uintah) grep -q "Sus: going down successfully" <<< "$t" && r=ok || r=;;
  esac
  [ -z "$r" ] && grep -q -i -E 'MPI_ERR_TRUNCATE|message truncated' <<< "$t" && r=truncate
  echo "${r:-crash}"
}
culprit() {   # run.out -> MUST reports that name the bug's pair
  local f=$1
  case $APP in
    slate) grep -oE 'Found concurrent MPI receives that use the same communciator, tag [0-9]+' "$f" \
             | awk '{t = $NF + 0; if (t >= 20 && t <= 29) n++} END {print n + 0}';;
    parsec|dlaf) grep -cE '\[MUST-REPORT\] Warning.*Found concurrent MPI receives' "$f";;
    uintah) grep -cE '\[MUST-REPORT\] Error.*Found concurrent MPI collectives that use the same communicator' "$f";;
  esac
}
# one unrecorded MUST run per cell first, so that no recorded run includes the build of MUST's
# infrastructure for the cell's layout (kept in /home/user/.cache/must)
for c in ${CELLS:-$ALL}; do
  [ "${WARMUP:-1}" = 1 ] || break   # WARMUP=0: none (Uintah, whose MUST runs all time out)
  setup "$c" plain
  (cd "$W" && "$R/experiments/mustvc/once.sh" "$NP" "$TO" "$W/warmup-$c" "${CMD[@]}" > /dev/null)
done
for i in $(seq 1 "$RUNS"); do
  for c in ${CELLS:-$ALL}; do
    for v in plain fixed; do
      setup "$c" "$v"
      for tool in native mustvc; do
        o=$W/$c-$v-$i-$tool; rm -rf "$o"; mkdir -p "$o"
        if [ $tool = native ]; then row=$(cd "$W" && NATIVE=1 "$R/experiments/mustvc/once.sh" "$NP" "$NTO" "$o" "${CMD[@]}")
        else row=$(cd "$W" && "$R/experiments/mustvc/once.sh" "$NP" "$TO" "$o" "${CMD[@]}"); fi
        IFS=, read -r res rc secs ne nw early msgs <<< "$row"
        cu=; [ $tool = mustvc ] && cu=$(culprit "$o/run.out")
        [ $tool = native ] && ne= nw= early=
        echo "$APP,$c,$v,$NP,$TH,$i,$tool,$res,$(outcome "$o/run.out" "$o/run.err"),$secs,$ne,$nw,$early,$cu,$msgs,$COMMIT" >> "$OUT"
        rm -rf "$W"/solvertest.uda* 2>/dev/null
      done
    done
  done
done
