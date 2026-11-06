#!/usr/bin/env bash
# MUST on the 513 programs of MPI-CorrBench a05644b outside its OpenMP directory
# (experiments/corrbench/programs.csv), REPS runs each on 2 processes, inside a MUST image
# (experiments/must/Dockerfile: MUST 1.11.2, MPICH, clang 14), each run by scripts/must_once.sh,
# the run and verdict of scripts/must_run.sh, with the program's directory and CorrBench's
# correct/include on the include path:
#   experiments/corrbench/must.sh CORRBENCH_0_LEVEL_DIR OUT.csv WORK
# Settings: REPS (3), TIMEOUT (60 s per run), JOBS (parallel runs, 4), ONLY (a regular expression
# on the path), STRETTO_MUST_DIR (MUST's installation, /MBB-builds/MUST1112), STRETTO_COMMIT
# (the commit column, else COMMIT at the repository root).
# Row: program,label,run,must,seconds,commit (must: the verdict without its MUST: prefix).
set -u
R=$(cd "$(dirname "$0")/../.." && pwd)
if [ "${1:-}" = --one ]; then   # --one C W TIMEOUT REPS PROGRAM: every run of one program, its rows on stdout
  C=$2 W=$3 TO=$4 REPS=$5 f=$6
  name=$(echo "${f%.c}" | sed 's|/|__|g')
  case $f in correct/*) label=correct;; *) label=erroneous;; esac
  for k in $(seq 1 "$REPS"); do
    d=$W/$name/$k; rm -rf "$d"; mkdir -p "$d/src"; cp "$C/$f" "$d/src/prog.c"
    t0=$SECONDS
    v=$(cd "$d/src" && STRETTO_MUST_CFLAGS="-I$C/$(dirname "$f") -I$C/correct/include" "$R/scripts/must_once.sh" 2 "$TO" | tail -1)
    echo "$f,$label,$k,\"${v//\"/\'}\",$((SECONDS - t0)),$COMMIT"
    # MUST's output stays only for runs it did not judge OK, without the binary and MUST's
    # temporary files, and only the last 64 KiB of the run's output
    if [ "$v" = OK ]; then rm -rf "$d"
    else rm -rf "$d/src/prog" "$d/src/must_temp"
         for o in "$d"/src/run.out "$d"/src/run.err; do [ -f "$o" ] && tail -c 65536 "$o" > "$o.t" && mv "$o.t" "$o"; done
    fi
  done
  exit 0
fi
C=${1:?usage: must.sh CORRBENCH_0_LEVEL_DIR OUT.csv WORK}; OUT=${2:?}; W=${3:?}
export STRETTO_MUST_DIR=${STRETTO_MUST_DIR:-/MBB-builds/MUST1112}
COMMIT=${STRETTO_COMMIT:-$(cat "$R/COMMIT" 2>/dev/null || echo unknown)}; export COMMIT
mkdir -p "$W"
[ -e "$OUT" ] || echo "program,label,run,must,seconds,commit" > "$OUT"
tail -n +2 "$R/experiments/corrbench/programs.csv" | cut -d, -f1 | grep -E "${ONLY:-.}" \
  | xargs -P "${JOBS:-4}" -I{} "$0" --one "$C" "$W" "${TIMEOUT:-60}" "${REPS:-3}" {} >> "$OUT"
