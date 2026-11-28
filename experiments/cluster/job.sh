#!/usr/bin/env bash
# The steps of one multi-node job, on the first node of a Slurm allocation (slurm.sbatch). Every
# step is experiments/cluster/job.py; ranks start through Slurm only (the compute nodes take no
# ssh). The unknowns of the cluster are parameters:
#   LAUNCHER srun (default): srun --mpi=$PMI for every rank (experiments/cluster/srun-mpirun);
#            mpirun: the MPI's own mpirun inside the allocation, which starts its daemons with
#            srun (RUNTIME=native only: an mpirun inside the image has no srun)
#   PMI      pmix (default) or pmi2: srun's --mpi (srun --mpi=list shows the cluster's). The
#            image's MPICH speaks PMIx only, so with pmi2 its steps are left out; its Open MPI
#            did not start under pmi2 either in the emulation (docs/cluster.md)
#   MPIS     the libraries of the bimpl and idioms steps (default "openmpi mpich")
#   RUNTIME  apptainer (default): every rank runs inside SIF (apptainer exec; Singularity's
#            singularity exec takes the same options: APPTAINER=singularity); native: the system's
#            Open MPI and the image's MPICH unpacked by experiments/cluster/mpich.sh (MPICH_PREFIX),
#            built by experiments/cluster/modules.sh
#   MPICH_PREFIX  native: mpich.sh's PREFIX (default ~/opt/mpich-4.2.0-ubuntu)
#   SIF      the image (default build/sif/stretto-exp_24.04.sif)
#   BINDS    what the image needs bound besides $HOME and the working directory, comma-separated
#            (default: the repository, OUT and LOCAL)
#   PYTHON   the harness's Python, outside the image: 3.10 or later (default python3; a module)
#   OUT      the job's output directory on the shared file system (default build/cluster/<job id>);
#            the shared home has no backup: copy OUT out after the job
#   LOCAL    a node-local directory for the traces (default: TMPDIR as the job started, else /tmp)
#   NET      the network interface or subnet of MPI's traffic between the nodes, if any (ib0, eth0,
#            192.168.48.0/21: slurm.sbatch's default on the SoC cluster)
#   MACHINE  the cluster's name, written into every row (rows.py host)
#   STEPS    the steps (default "setup bimpl idioms"): build (tracer, replay library, idioms, both
#            libraries, inside the image or with the modules), buildsubjects (the subjects of
#            SUBJECTS, experiments/protocol/build_subjects.sh), setup, bimpl, idioms (three idioms,
#            both libraries: plain, traced, analysed, replayed), subjects (the campaign's subjects of
#            SUBJECTS through experiments/protocol/run.py and replays.py; their builds must exist)
#   SUBJECTS "subject:config ..." for the subjects step (default "npbmg:np16 hypre:np8")
#   RUNS     runs per tool and configuration (default 5); REPLAYS replays per configuration (default 10)
# usage: experiments/cluster/job.sh        (from the repository root, inside the allocation)
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
LAUNCHER="${LAUNCHER:-srun}"; PMI="${PMI:-pmix}"; RUNTIME="${RUNTIME:-apptainer}"
APPTAINER="${APPTAINER:-apptainer}"
JOBID="${SLURM_JOB_ID:-$$}"
OUT="${OUT:-$R/build/cluster/$JOBID}"
LOCAL="${LOCAL:-${TMPDIR:-/tmp}}"
SIF="${SIF:-$R/build/sif/stretto-exp_24.04.sif}"
BINDS="${BINDS:-$R,$(dirname "$OUT"),$LOCAL}"
PYTHON="${PYTHON:-python3}"
export MPICH_PREFIX="${MPICH_PREFIX:-$HOME/opt/mpich-4.2.0-ubuntu}"
RUNS="${RUNS:-5}"; REPLAYS="${REPLAYS:-10}"
SUBJECTS="${SUBJECTS:-npbmg:np16 hypre:np8}"
export MACHINE="${MACHINE:-cluster}" PYTHONPATH="$R/analysis"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$("$PYTHON" experiments/provenance.py 2> /dev/null || git rev-parse --short=12 HEAD)}"
mkdir -p "$OUT"
IN=()
if [ "$RUNTIME" = apptainer ]; then
    command -v "$APPTAINER" > /dev/null || { echo "job.sh: no $APPTAINER on this node (module load apptainer?)" >&2; exit 1; }
    IN=("$APPTAINER" exec --bind "$BINDS" "$SIF")
fi
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
    || { echo "job.sh: $PYTHON is older than 3.10 (module load python?)" >&2; exit 1; }
J=("$PYTHON" "$R/experiments/cluster/job.py")
MPIS="${MPIS:-openmpi mpich}"; [ "$LAUNCHER" = srun ] && [ "$PMI" = pmi2 ] && MPIS=openmpi

step_build() {
    if [ "$RUNTIME" = apptainer ]; then
        "${IN[@]}" bash -c 'for m in openmpi mpich; do make -s -j8 -C tracer MPI=$m && make -s -j8 -C replay MPI=$m && make -s -j8 -C idioms MPI=$m || exit 1; done'
    else
        # the system's Open MPI, and the image's MPICH from experiments/cluster/mpich.sh
        MPI=openmpi MPICC=/usr/bin/mpicc.openmpi experiments/cluster/modules.sh
        MPI=mpich MPICC="$MPICH_PREFIX/bin/mpicc.mpich" experiments/cluster/modules.sh
    fi
}
step_buildsubjects() {
    # the subjects of SUBJECTS by the campaign's own fetch and build steps, into third_party/apps/
    local names
    names="$(for sc in $SUBJECTS; do echo "${sc%%:*}"; done | sort -u | tr '\n' ' ')"
    # (build_subjects.sh reads SUBJECTS and STEPS, which here hold this job's pairs and steps)
    if [ "$RUNTIME" = apptainer ]; then
        "${IN[@]}" env SUBJECTS="$names" STEPS="fetch build" experiments/protocol/build_subjects.sh
    else
        PATH="$MPICH_PREFIX/bin:$PATH" MPICH_BIN="$MPICH_PREFIX/bin" MAKE_JOBS="${MAKE_JOBS:-$(nproc)}" \
            SUBJECTS="$names" STEPS="fetch build" experiments/protocol/build_subjects.sh
    fi
}
step_setup() {
    "${J[@]}" setup --out "$OUT" --launcher "$LAUNCHER" --pmi "$PMI" --runtime "$RUNTIME" --local "$LOCAL" \
        --mpich-prefix "$MPICH_PREFIX" \
        ${NET:+--net "$NET"} $( [ "$RUNTIME" = apptainer ] && echo --sif "$SIF" --binds "$BINDS")
}
step_bimpl() {
    local m
    for m in $MPIS; do "${J[@]}" bimpl --out "$OUT" --mpi "$m"; done
}
step_idioms() {
    local m n d exe name
    for m in $MPIS; do
        for n in 01 04 05; do
            d="$(ls -d "$R"/idioms/"$n"_*)"; exe="$d/bin/$m/main"; name="idiom$n-$m"
            "${J[@]}" run --out "$OUT" --name "$name" --tool plain --np 3 --mpi "$m" --runs "$RUNS" --timeout 60 -- "$exe"
            "${J[@]}" run --out "$OUT" --name "$name" --tool traced --np 3 --mpi "$m" --runs "$RUNS" --timeout 60 -- "$exe"
            # with the single-path explorer, as scripts/run_idiom.sh: idiom 04's deadlock is an L1+SP finding
            "${J[@]}" analyse --out "$OUT" --name "$name" --sp
            "${J[@]}" replay --out "$OUT" --name "$name" --mpi "$m" --timeout 30 --all --sp -- "$exe"
        done
    done
}
step_subjects() {
    local sc s c v i
    for sc in $SUBJECTS; do
        s="${sc%%:*}"; c="${sc#*:}"
        for v in bug fix; do
            for i in $(seq 1 "$RUNS"); do
                "${J[@]}" protocol --out "$OUT" -- --subject "$s" --config "$c" --version $v --tool plain --setting - --run "$i" --timeout 300
                "${J[@]}" protocol --out "$OUT" -- --subject "$s" --config "$c" --version $v --tool delays --setting D50 --run "$i" --timeout 300
                "${J[@]}" protocol --out "$OUT" -- --subject "$s" --config "$c" --version $v --tool stretto --setting traced --run "$i" --timeout 600
            done
        done
        "${J[@]}" protocol-replays --out "$OUT" --subject "$s" --config "$c" --timeout 300 --max "$REPLAYS"
    done
}
for s in ${STEPS:-setup bimpl idioms}; do
    echo "== $s ($(date -u +%FT%TZ))"
    "step_$s"
done
