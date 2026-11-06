#!/usr/bin/env bash
# Trace sweep cells of SLATE's tester again and analyse the traces (LEADS.md, leads F, C, K).
# Run inside hunt-slate:24.04 with the tester at /b/build/test/tester, the repository at REPO and
# the tracer to use at TRACER (default: REPO's, built with a larger chain table, -DMAXCH=4096):
#   experiments/slate-leads/retrace.sh SWEEP_RUNS WORK OUT.csv CELL_DIR...
# Each CELL_DIR names a cell directory of the sweep (its args file gives the tester's arguments;
# the name ends in _<grid>_t<threads>). OUT gets one row per cell: the run's exit status and time,
# whether the chain table overflowed, the number of chains, and the count of each finding class.
set -u
S=${1:?usage: retrace.sh SWEEP_RUNS WORK OUT.csv CELL_DIR...}; W=${2:?}; OUT=${3:?}; shift 3
REPO=${REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
TRACER=${TRACER:-$REPO/tracer/bin/openmpi/libstretto-trace.so}
BIN=${BIN:-/b/build/test/tester}
MODE=${MODE:-traced}           # traced, or plain (no tracer: the run's status and time only)
TIMEOUT=${TIMEOUT:-300}
export PYTHONPATH=$REPO/analysis OPENBLAS_NUM_THREADS=1
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
ulimit -c 0
commit=$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || cat "$REPO/COMMIT")
[ -e "$OUT" ] || echo "cell,mode,rep,run_rc,run_seconds,status,overflow,chains,classes,commit" > "$OUT"
for c in "$@"; do
  name=$(basename "$c")
  threads=${name##*_t}; grid=${name%_t*}; grid=${grid##*_}
  np=$(( ${grid%x*} * ${grid#*x} ))
  eval "a=($(cat "$S/$name/args"))"; a=("${a[@]:1}")
  rep=${REP:-0}
  d=$W/$name.$MODE.$rep; rm -rf "$d"; mkdir -p "$d/trace"
  xs=(-x OMP_NUM_THREADS="$threads")
  [ "$MODE" = traced ] && xs+=(-x STRETTO_TRACE_DIR="$d/trace" -x LD_PRELOAD="$TRACER")
  t0=$(date +%s)
  (cd "$d" && timeout "$TIMEOUT" mpirun --bind-to none -np "$np" "${xs[@]}" "$BIN" "${a[@]}" < /dev/null) > "$d/log" 2>&1
  rc=$?; secs=$(( $(date +%s) - t0 ))
  status=; ovf=; nch=; classes=
  if [ "$MODE" = traced ]; then
    timeout 3600 python3 -m stretto analyze --json "$d/trace" > "$d/report.json" 2> "$d/report.err"
    read -r status ovf nch classes < <(python3 -I - "$d/report.json" <<'EOF'
import collections, json, re, sys
try:
    d = json.load(open(sys.argv[1]))
except ValueError:
    print("none", "", "", ""); sys.exit()
notes = d.get("notes", [])
ovf = int(any("chain table overflowed" in n for n in notes))
ch = [n for n in notes if n.startswith("chains")]
nch = len(re.findall(r"\d+\.\d+ \(", ch[0])) if ch else 0
c = collections.Counter(f"{f['cls']}@{f['level']}" for f in d.get("findings", []))
print(d.get("status", ""), ovf, nch, "+".join(f"{k}:{v}" for k, v in sorted(c.items())) or "-")
EOF
)
    tar -C "$d" -cJf "$d/trace.tar.xz" trace && rm -rf "$d/trace"
  else
    rm -rf "$d/trace"
  fi
  echo "$name,$MODE,$rep,$rc,$secs,$status,$ovf,$nch,$classes,$commit" >> "$OUT"
done
