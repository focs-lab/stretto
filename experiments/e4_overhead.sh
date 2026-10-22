#!/usr/bin/env bash
# E4: tracer overhead and analysis cost on NPB 3.4.3 (MPI). Binaries built with the MPICH
# wrappers (so the Fortran kernels are interposed) and launched with mpirun.openmpi (PMIx).
# usage: experiments/e4_overhead.sh [np ...]     default: 4 16
#   BENCHMARKS, CLASSES  select rows (default: is ep cg mg lu, S A)
#   REPS                 repetitions of the whole set (default 1); a repetition runs every row
#                        once, so drift during the leg spreads over all rows
#   CSV                  table to write (default: experiments/results/e4/e4.csv), so that
#                        single rows can be measured without overwriting the table
set -uo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$R/third_party/apps/NPB-mpich/bin"
TR="$R/tracer/bin/mpich/libstretto-trace.so"
OUT="$R/experiments/results/e4"; mkdir -p "$OUT"
export OMPI_MCA_rmaps_base_oversubscribe=1 PYTHONPATH="$R/analysis"
nps="${*:-4 16}"
CSV="${CSV:-$OUT/e4.csv}"
COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
# wall times include the launcher's start-up; NPB's own timer ("Time in seconds") covers the
# timed section of the benchmark only, where the tracer's cost per call shows
npb_time() { sed -n 's/^ *Time in seconds *= *//p' "$1" | head -1; }
echo "benchmark,class,np,rep,plain_s,traced_s,slowdown,plain_npb_s,traced_npb_s,npb_slowdown,trace_MB,events,wildcards,analysis_s,findings,classes,commit" > "$CSV"
# The launcher is called directly with fixed timeouts (600 s plain, 1200 s traced) rather than
# through mpi_run, so TIMEOUT does not apply here: a timed leg must not cut a benchmark short.
for rep in $(seq 1 "${REPS:-1}"); do
for np in $nps; do for b in ${BENCHMARKS:-is ep cg mg lu}; do for cls in ${CLASSES:-S A}; do
  exe="$BIN/$b.$cls.x"; [ -x "$exe" ] || continue
  t0=$(date +%s.%N); timeout -k 5 600 /usr/bin/mpirun.openmpi -np $np "$exe" > "$OUT/$b.$cls.$np.plain.log" 2>&1; t1=$(date +%s.%N)
  plain=$(echo "$t1 - $t0" | bc)
  td="$OUT/trace.$b.$cls.$np"; rm -rf "$td"
  t0=$(date +%s.%N); timeout -k 5 1200 /usr/bin/mpirun.openmpi -np $np -x STRETTO_TRACE_DIR="$td" -x LD_PRELOAD="$TR" "$exe" > "$OUT/$b.$cls.$np.traced.log" 2>&1; t1=$(date +%s.%N)
  traced=$(echo "$t1 - $t0" | bc)
  mb=$(du -sm "$td" | cut -f1); ev=$(cat "$td"/rank-*.jsonl | wc -l)
  t0=$(date +%s.%N); python3 -m stretto analyze --no-sp "$td" > "$OUT/$b.$cls.$np.report.txt" 2> "$OUT/$b.$cls.$np.analyze.err"; t1=$(date +%s.%N)
  an=$(echo "$t1 - $t0" | bc)
  wc=$(head -1 "$OUT/$b.$cls.$np.report.txt" | grep -oE '[0-9]+ wildcard' | cut -d' ' -f1)
  nf=$(grep -cE '^\[' "$OUT/$b.$cls.$np.report.txt"); cl=$(grep -oE '^\[ *[a-z0-9-]+\]' "$OUT/$b.$cls.$np.report.txt" | tr -d '[] ' | sort -u | tr '\n' ' ')
  sd=$(echo "scale=2; $traced / $plain" | bc)
  pn=$(npb_time "$OUT/$b.$cls.$np.plain.log"); tn=$(npb_time "$OUT/$b.$cls.$np.traced.log")
  nsd=$([ -n "$pn" ] && [ -n "$tn" ] && echo "scale=3; $tn / $pn" | bc 2>/dev/null)
  echo "$b,$cls,$np,$rep,$plain,$traced,$sd,$pn,$tn,$nsd,$mb,$ev,${wc:-0},$an,$nf,$cl,$COMMIT" | tee -a "$CSV"
done; done; done
done
