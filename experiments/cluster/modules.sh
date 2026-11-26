#!/usr/bin/env bash
# The fallback for a cluster with neither Docker nor Apptainer: build the tracer, the replay
# library, the re-execution shim and the idioms against the cluster's own MPI (its modules),
# and check that the analysis runs on its Python. Load the modules first, for example
#   module load gcc/13 openmpi/4.1 python/3.12        (names depend on the cluster)
# The libraries go to tracer/bin/<MPI>/, replay/bin/<MPI>/, rerun/bin/<MPI>/, where the harness
# looks for them; MPI names the build (openmpi or mpich: an Intel MPI or Cray MPICH counts as
# mpich, the ABI family the wrappers belong to).
#   MPI      openmpi or mpich (default: what mpicc says it is)
#   MPICC    the MPI's C compiler wrapper (default: mpicc on PATH)
#   CC       the C compiler of the tracer's stub runtime (default: gcc on PATH)
#   JOBS     make jobs (default 8)
# Then the job's launcher (job.sh RUNTIME=native): LAUNCHER=mpirun, the module's mpirun inside the
# allocation (it starts its daemons with srun), or LAUNCHER=srun (srun-mpirun). job.py passes it
# to stretto.confirm as STRETTO_MPIRUN; an MPICH mpiexec (hydra, -genv) is read from its --version.
# What differs from the image (docs/cluster.md): the MPI's version and transport, hence its eager
# limits; the compiler; the call sites' offsets (a build of its own). The results are rows of
# another configuration, not the image's.
# usage: experiments/cluster/modules.sh   (from the repository root, inside a job: job.sh's build
#        step runs it; builds and MPI runs do not belong on the login nodes)
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
MPICC="${MPICC:-$(command -v mpicc || true)}"
[ -n "$MPICC" ] || { echo "modules.sh: no mpicc on PATH (module load an MPI first)" >&2; exit 1; }
CC="${CC:-$(command -v gcc)}"
JOBS="${JOBS:-8}"
if [ -z "${MPI:-}" ]; then
    show="$("$MPICC" -show 2>/dev/null || "$MPICC" --showme 2>/dev/null || true)"
    if "$MPICC" --showme:version > /dev/null 2>&1 || echo "$show" | grep -qi 'open-\?mpi\|libmpi\.so' && ! echo "$show" | grep -qi mpich; then
        MPI=openmpi
    else
        MPI=mpich
    fi
fi
echo "MPI family $MPI, wrapper $MPICC ($("$MPICC" --version | head -1)), C compiler $CC"
mpirun_of() { command -v mpirun || command -v mpiexec || true; }
echo "launcher: $(mpirun_of) ($("$(mpirun_of)" --version 2>&1 | head -1))"
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    sys.exit("modules.sh: the analysis needs Python 3.10 or later, this is %s (module load python)" % sys.version.split()[0])
print("python", sys.version.split()[0])
PY
make -s -j"$JOBS" -C tracer MPI="$MPI" MPICC="$MPICC" CC="$CC"
make -s -j"$JOBS" -C replay MPI="$MPI" MPICC="$MPICC"
make -s -j"$JOBS" -C rerun MPI="$MPI" MPICC="$MPICC" || echo "modules.sh: the re-execution shim did not build (L2 by re-execution only)" >&2
make -s -j"$JOBS" -C idioms MPI="$MPI" MPICC="$MPICC"
ls -l "tracer/bin/$MPI/libstretto-trace.so" "replay/bin/$MPI/libstretto-pin.so"
# the analysis on a trace of this build: idiom 04 on one node
d="$(mktemp -d)"
"$(mpirun_of)" -np 3 env LD_PRELOAD="$R/tracer/bin/$MPI/libstretto-trace.so" STRETTO_TRACE_DIR="$d" \
    "$R/idioms/04_orphan_send/bin/$MPI/main" > /dev/null 2>&1 || true
PYTHONPATH="$R/analysis" python3 -m stretto analyze "$d" | tail -3 || true
rm -rf "$d"
