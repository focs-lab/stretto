#!/usr/bin/env bash
# Untraced series of the envelope-injection hang (H1): plain and patched builds (build.sh,
# VARIANT=gil with envelope-gil.patch) interleaved, RUNS runs per cell, NP ranks of OMP
# threads, each run that ends checked by Smilei's validation. Run inside a container of the
# image of Dockerfile here:
#   docker run ... smi-exp:24.04 /h/repo/experiments/smilei/series.sh OUT.csv
# Settings: MPIS (openmpi mpich), BENCHES (the two hung benchmarks), VARIANTS (plain gil), RUNS
# (20), NP (4), OMP (2), TIMEOUT (seconds, 300). One CSV row per run: bench, mpi, variant, omp,
# run, result (pass|invalid|hang|fail), rc, seconds, commit.
set -u
OUT=${1:?usage: series.sh OUT.csv}
MPIS=${MPIS:-openmpi mpich}; VARIANTS=${VARIANTS:-plain gil}; RUNS=${RUNS:-20}; NP=${NP:-4}; OMP=${OMP:-2}
TIMEOUT=${TIMEOUT:-300}
BENCHES=${BENCHES:-tst3d_24_envelope_Laguerre_Gauss_from_xmin.py tst3d_25_envelope_Hermite_Gauss_from_xmin_reduced_dispersion.py}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=$(python3 "$REPO/experiments/provenance.py")
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
W=$(mktemp -d)
[ -f "$OUT" ] || echo "bench,mpi,variant,omp,run,result,rc,seconds,commit" > "$OUT"
for i in $(seq 1 "$RUNS"); do
  for m in $MPIS; do for b in $BENCHES; do for v in $VARIANTS; do
    B=/h/build/smilei-$m-$v; d=$W/sim; rm -rf "$d"; mkdir -p "$d/restart000"
    s=$(date +%s)
    (cd "$d/restart000" && timeout "$TIMEOUT" mpirun --bind-to none -np "$NP" -x OMP_NUM_THREADS="$OMP" \
       -x OMP_SCHEDULE=dynamic "$B/smilei" "$B/benchmarks/$b" > log 2>&1); rc=$?
    e=$(( $(date +%s) - s ))
    res=pass; [ $rc = 124 ] && res=hang; [ $rc != 0 ] && [ $rc != 124 ] && res=fail
    if [ $res = pass ]; then
      (cd $B/validation && PYTHONPATH=$B timeout 600 python3 "$REPO/experiments/smilei/validate.py" "$b" "$d") > /dev/null 2>&1 || res=invalid
    fi
    echo "${b%.py},$m,$v,$OMP,$i,$res,$rc,$e,$COMMIT" >> "$OUT"
  done; done; done
done
rm -rf "$W"
