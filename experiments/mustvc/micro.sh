#!/usr/bin/env bash
# MUST-vc on its own artifact's 50 test programs (30 of its own suite, 20 adapted from
# MPI-CorrBench), each run by lit with the artifact's RUN lines and FileCheck patterns, for the
# counter-based and the clock-based layout, REPS times. Runs inside the mustvc image (Dockerfile
# here), as the image's user:
#   experiments/mustvc/micro.sh OUT.csv WORK
# WORK gets one copy of /home/user/tests per analysis and repetition (lit writes each program's
# binary and MUST log next to its source). Settings: REPS (5), ANALYSES ("counter clock"),
# STRETTO_COMMIT (the commit column; else the COMMIT file or git, experiments/provenance.py).
# Row: suite,group,label,test,analysis,rep,lit,verdict,errors,warnings,messages,seconds,commit
# lit is PASS or FAIL as the artifact's run_tests.sh counts it (a correct program passes when
# MUST reports no error or warning, an erroneous one when the report the RUN lines expect is
# there); verdict TP/FN on erroneous programs, TN/FP on correct ones; errors and warnings count
# the [MUST-REPORT] lines of the run's log, messages names their texts.
set -u
OUT=${1:?usage: micro.sh OUT.csv WORK}; W=${2:?}
REPS=${REPS:-5}; ANALYSES=${ANALYSES:-"counter clock"}
R=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || git -C "$R" rev-parse --short HEAD 2>/dev/null || echo unknown)}
mkdir -p "$W"
[ -e "$OUT" ] || echo "suite,group,label,test,analysis,rep,lit,verdict,errors,warnings,messages,seconds,commit" > "$OUT"
for rep in $(seq 1 "$REPS"); do
  for a in $ANALYSES; do
    T=$W/$a-$rep; rm -rf "$T"; cp -r /home/user/tests "$T"
    (cd "$T" && find MPI-Corrbench own_checks -name '*.c' -o -name '*.cpp' | sort) | while read -r f; do
      case $f in MPI-Corrbench/*) suite=COBE;; *) suite=Own;; esac
      case $f in */correct/*) label=correct;; *) label=erroneous;; esac
      group=$(echo "$f" | cut -d/ -f2); test=$(basename "${f%.*}")
      t0=$(date +%s.%N)
      out=$(cd "$T" && LIT_MUST_VERSION=$a /usr/bin/timeout -k 5 300 lit -j1 -v "$f" 2>&1)
      t1=$(date +%s.%N)
      if grep -q "^PASS: " <<< "$out"; then lit=PASS; elif grep -q "^FAIL: " <<< "$out"; then lit=FAIL; else lit=ERROR; fi
      case $label/$lit in correct/PASS) v=TN;; correct/FAIL) v=FP;; erroneous/PASS) v=TP;; erroneous/FAIL) v=FN;; *) v=ERROR;; esac
      log=$T/$f.$a.log; [ -f "$log" ] || log=$T/$f.log   # some RUN lines write %s.log
      ne=$(grep -cE '\[MUST-REPORT\] Error' "$log" 2>/dev/null); nw=$(grep -cE '\[MUST-REPORT\] Warning' "$log" 2>/dev/null)
      msgs=$(grep -oE '\[MUST-REPORT\] (Error|Warning).*' "$log" 2>/dev/null | sed -E 's/.*: (Found [^.]*|[A-Z][^.:]*)\..*/\1/' | sort | uniq -c \
             | awk '{n=$1; $1=""; sub(/^ /, ""); gsub(/[,;]/, " "); printf "%s%s*%d", s, $0, n; s=";"}')
      echo "$suite,$group,$label,$test,$a,$rep,$lit,$v,${ne:-0},${nw:-0},$msgs,$(echo "$t1 - $t0" | bc | awk '{printf "%.1f", $1}'),$COMMIT" >> "$OUT"
    done
  done
done
