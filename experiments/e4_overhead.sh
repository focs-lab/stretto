#!/usr/bin/env bash
# E4: tracer overhead and analysis cost on NPB 3.4.3 (MPI). Binaries built with the MPICH
# wrappers (so the Fortran kernels are interposed) and launched with mpirun.openmpi (PMIx).
# usage: experiments/e4_overhead.sh [np ...]     default: 4 16
#   BENCHMARKS, CLASSES  select rows (default: is ep cg mg lu, S A)
#   CSV                  table to write (default: experiments/results/e4/e4.csv), so that
#                        single rows can be measured without overwriting the table
set -uo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$R/third_party/apps/NPB-mpich/bin"
TR="$R/tracer/bin/mpich/librematch-trace.so"
OUT="$R/experiments/results/e4"; mkdir -p "$OUT"
export OMPI_MCA_rmaps_base_oversubscribe=1 PYTHONPATH="$R/analysis"
nps="${*:-4 16}"
CSV="${CSV:-$OUT/e4.csv}"
echo "benchmark,class,np,plain_s,traced_s,slowdown,trace_MB,events,wildcards,analysis_s,findings,classes" > "$CSV"
# The launcher is called directly with fixed timeouts (600 s plain, 1200 s traced) rather than
# through mpi_run, so TIMEOUT does not apply here: a timed leg must not cut a benchmark short.
for np in $nps; do for b in ${BENCHMARKS:-is ep cg mg lu}; do for cls in ${CLASSES:-S A}; do
  exe="$BIN/$b.$cls.x"; [ -x "$exe" ] || continue
  t0=$(date +%s.%N); timeout -k 5 600 /usr/bin/mpirun.openmpi -np $np "$exe" > "$OUT/$b.$cls.$np.plain.log" 2>&1; t1=$(date +%s.%N)
  plain=$(echo "$t1 - $t0" | bc)
  td="$OUT/trace.$b.$cls.$np"; rm -rf "$td"
  t0=$(date +%s.%N); timeout -k 5 1200 /usr/bin/mpirun.openmpi -np $np -x REMATCH_TRACE_DIR="$td" -x LD_PRELOAD="$TR" "$exe" > "$OUT/$b.$cls.$np.traced.log" 2>&1; t1=$(date +%s.%N)
  traced=$(echo "$t1 - $t0" | bc)
  mb=$(du -sm "$td" | cut -f1); ev=$(cat "$td"/rank-*.jsonl | wc -l)
  t0=$(date +%s.%N); python3 -m rematch analyze --no-sp "$td" > "$OUT/$b.$cls.$np.report.txt" 2> "$OUT/$b.$cls.$np.analyze.err"; t1=$(date +%s.%N)
  an=$(echo "$t1 - $t0" | bc)
  wc=$(head -1 "$OUT/$b.$cls.$np.report.txt" | grep -oE '[0-9]+ wildcard' | cut -d' ' -f1)
  nf=$(grep -cE '^\[' "$OUT/$b.$cls.$np.report.txt"); cl=$(grep -oE '^\[ *[a-z0-9-]+\]' "$OUT/$b.$cls.$np.report.txt" | tr -d '[] ' | sort -u | tr '\n' ' ')
  sd=$(echo "scale=2; $traced / $plain" | bc)
  echo "$b,$cls,$np,$plain,$traced,$sd,$mb,$ev,${wc:-0},$an,$nf,$cl" | tee -a "$CSV"
done; done; done
