#!/usr/bin/env bash
# Untraced runs of the tests whose traces have an observed 2-buffering or unreceived sends (the
# scan's O1 and O2) with Open MPI's eager limit forced down to 0 payload bytes (TCP, as
# experiments/ga/eager.sh) and at the defaults, on 4 ranks of NCPU workers, as shipped ("ser")
# and with MPI_THREAD_MULTIPLE and the workers' own sends ("1", the shim mtinit.c). A run that
# does not end within TIMEOUT seconds is a hang (exit 124, or 137 when mpirun itself had to be
# killed 10 s later). Run inside a container of the image built from
# Dockerfile here:
#   experiments/starpu/eager.sh OUT.csv
# Settings: TESTS, MODES (ser 1), EAGER (default 0), RUNS (5), TIMEOUT (60), TREE
# (spu-openmpi-plain), NCPU (2), VARIANT (a name for the tree in the table, plain).
set -u
OUT=${1:?usage: eager.sh OUT.csv}
TESTS=${TESTS:-large_set insert_task insert_task_seq early_request load_balancer mpi_scatter_gather coop_many}
MODES=${MODES:-ser 1}; EAGER=${EAGER:-default 0}; RUNS=${RUNS:-5}; TIMEOUT=${TIMEOUT:-60}
TREE=${TREE:-spu-openmpi-plain}; NCPU=${NCPU:-2}; VARIANT=${VARIANT:-plain}
REPO=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=$(python3 "$REPO/experiments/provenance.py")
B=/h/build/$TREE L=$B/src/.libs:$B/mpi/src/.libs M=/h/build/libmtinit-openmpi.so
[ -f "$M" ] || /usr/bin/mpicc.openmpi -O2 -Wall -shared -fPIC -o "$M" "$REPO/experiments/starpu/mtinit.c" -ldl
export OMPI_MCA_rmaps_base_oversubscribe=1 OMPI_MCA_btl_vader_single_copy_mechanism=none
[ -f "$OUT" ] || echo "test,variant,mode,eager,run,result,rc,seconds,commit" > "$OUT"
log=$(mktemp); cwd=$(mktemp -d)
for i in $(seq 1 "$RUNS"); do
  for t in $TESTS; do
    exe=$B/mpi/tests/.libs/$t; [ -x "$exe" ] || exe=$B/mpi/tests/$t
    for ms in $MODES; do
      for e in $EAGER; do
        mca=()
        [ "$e" != default ] && mca=(--mca btl self,tcp --mca btl_tcp_if_include lo
                                    --mca btl_tcp_eager_limit $((e + 56)) --mca btl_self_eager_limit $((e + 56)))
        mode=(); [ "$ms" != ser ] && mode=(-x LD_PRELOAD="$M" -x STARPU_MPI_THREAD_MULTIPLE_SEND="$ms")
        t0=$(date +%s.%N)
        (cd "$cwd" && timeout -k 10 "$TIMEOUT" mpirun --bind-to none -np 4 "${mca[@]}" -x LD_LIBRARY_PATH="$L" \
           -x STARPU_NCPU="$NCPU" -x STARPU_MPI_NOBIND=1 -x STARPU_WORKERS_NOBIND=1 -x STARPU_SILENT=1 \
           -x STARPU_HOME="$cwd" "${mode[@]}" "$exe") > "$log" 2>&1; rc=$?
        s=$(echo "$(date +%s.%N) - $t0" | bc)
        res=fail; [ $rc = 0 ] && res=pass; [ $rc = 124 ] && res=hang; [ $rc = 77 ] && res=skip
        # mpirun can be stuck after its ranks were stopped: timeout then kills it (137)
        [ $rc = 137 ] && [ "$(echo "$s >= $TIMEOUT" | bc)" = 1 ] && res=hang
        echo "$t,$VARIANT,$ms,$e,$i,$res,$rc,$(printf %.1f "$s"),$COMMIT" >> "$OUT"
      done
    done
  done
done
rm -rf "$log" "$cwd"
