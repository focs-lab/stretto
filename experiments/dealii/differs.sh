#!/usr/bin/env bash
# The validity check of the deal.II campaign (experiments/dealii/run.sh, step stress) on one run's
# working directory: the test's file `output`, normalised with deal.II's normalize.pl as its test
# harness does, against the expected output. Prints the number of lines that differ, position by
# position (0: the expected output), or "no-output" when the run wrote none.
# usage: experiments/dealii/differs.sh <normalize.pl> <expected output>   (in the run's directory)
set -uo pipefail
[ -f output ] || { echo no-output; exit 0; }
perl -p "$1" output > normalized || { echo normalize-failed; exit 0; }
awk 'NR == FNR { a[FNR] = $0; n = FNR; next } { if (!(FNR in a) || a[FNR] != $0) d++; m = FNR }
     END { if (n > m) d += n - m; print d + 0 }' normalized "$2"
