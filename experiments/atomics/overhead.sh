#!/usr/bin/env bash
# Tracer overhead with and without the atomics recorded (docs/threads.md section 17): wall time
# of mpirun, REPS (5) repetitions per mode, the modes interleaved, on two applications built by
# their scripts with TSAN=1 (and HDF5 also without).
#   experiments/atomics/overhead.sh dlaf BIN OUT.csv     BIN holds copy_check (experiments/dlaf/build.sh)
#   experiments/atomics/overhead.sh hdf5 WORK OUT.csv    WORK as for experiments/subfiling/run.sh
# Modes: untraced (the stub runtime), traced with STRETTO_ATOMICS=0, traced with the atomics
# recorded; for HDF5 also its gcc build untraced and traced. DLA-Future runs the 2x1 cell of
# traced.sh (2 ranks, 4 pika threads, DLAF_SIZE (512) square, DLAF_COPIES (1) copies, jitter
# DLAF_JITTER (200) us); HDF5 t_subfiling_vfd on 4 ranks
# in SCRATCH (a tmpfs). Run inside a container of the image built from Dockerfile here, with
# nothing else on its processors.
set -u
APP=${1:?usage: overhead.sh dlaf|hdf5 DIR OUT.csv}; D=${2:?}; OUT=${3:?}
REPS=${REPS:-5}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
T=$REPO/tracer/bin/openmpi/libstretto-trace.so
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none OPENBLAS_NUM_THREADS=1
tmp=$(mktemp -d)
echo "app,mode,rep,seconds,rc" > "$OUT"
run() {    # mode exe env... -- args...
  local mode=$1 exe=$2; shift 2
  local env=() s e rc
  while [ "$1" != -- ]; do env+=(-x "$1"); shift; done; shift
  rm -rf "$tmp/trace"; mkdir -p "$tmp/trace"
  s=$(date +%s.%N)
  (cd "${SCRATCH:-$tmp}" && timeout 600 mpirun --bind-to none "${NPARG[@]}" ${env[@]+"${env[@]}"} "$exe" "$@" > "$tmp/out.log" 2>&1)
  rc=$?; e=$(date +%s.%N)
  [ -n "${SCRATCH:-}" ] && find "$SCRATCH" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  echo "$APP,$mode,$i,$(echo "$e - $s" | bc),$rc" >> "$OUT"
}
for i in $(seq 1 "$REPS"); do
  case $APP in
  dlaf)
    n=${DLAF_SIZE:-512}
    NPARG=(-np 2); a=("$n" "$n" 64 64 64 64 2 1 0 0 1 0 "${DLAF_COPIES:-1}" "${DLAF_JITTER:-200}" --pika:threads=4 --pika:bind=none)
    run untraced "$D/copy_check" -- "${a[@]}"
    run traced-atomics-off "$D/copy_check" STRETTO_ATOMICS=0 STRETTO_TRACE_DIR="$tmp/trace" LD_PRELOAD="$T" -- "${a[@]}"
    run traced-atomics-on "$D/copy_check" STRETTO_TRACE_DIR="$tmp/trace" LD_PRELOAD="$T" -- "${a[@]}" ;;
  hdf5)
    NPARG=(-np 4)
    run gcc-untraced "$D/build-pre/bin/t_subfiling_vfd" --
    run gcc-traced "$D/build-pre/bin/t_subfiling_vfd" STRETTO_TRACE_DIR="$tmp/trace" LD_PRELOAD="$T" --
    run untraced "$D/build-pre-tsan/bin/t_subfiling_vfd" --
    run traced-atomics-off "$D/build-pre-tsan/bin/t_subfiling_vfd" STRETTO_ATOMICS=0 STRETTO_TRACE_DIR="$tmp/trace" LD_PRELOAD="$T" --
    run traced-atomics-on "$D/build-pre-tsan/bin/t_subfiling_vfd" STRETTO_TRACE_DIR="$tmp/trace" LD_PRELOAD="$T" -- ;;
  esac
done
rm -rf "$tmp"
awk -F, 'NR > 1 { n[$2]++; s[$2] += $4; if (!($2 in lo) || $4 < lo[$2]) lo[$2] = $4; if ($4 > hi[$2]) hi[$2] = $4 }
         END { for (m in n) printf "%s: mean %.2f s, min %.2f, max %.2f (%d runs)\n", m, s[m] / n[m], lo[m], hi[m], n[m] }' "$OUT"
