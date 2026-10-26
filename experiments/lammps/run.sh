#!/usr/bin/env bash
# A documented deadlock in LAMMPS, from the project's history (commit 411c069b).
#
# Irregular::create_atom and create_data (src/irregular.cpp) tell every destination how much it
# will get with a blocking MPI_Send(&length_send[i], 1, MPI_INT, proc_send[i], 0, world) to each
# in turn, and only then receive the counts with MPI_Recv(&length_recv[i], 1, MPI_INT,
# MPI_ANY_SOURCE, 0, world). Two ranks that send to each other wait in their sends forever
# unless the MPI buffers the 4-byte messages: an unsafe program, which every common MPI runs
# because its eager protocol buffers small messages. Commit 411c069b (17 Mar 2017, "BUGFIX:
# Prevent possible deadlock in Irregular::create_atom and create_data"; first stable release
# with it 31Mar2017) sends the counts with MPI_Isend and MPI_Request_free; its parent 3a2da51a
# has the bug. The order in which the wildcard receives take the counts only changes the order
# of the migrated atoms, which LAMMPS documents as irreproducible; this is a buffering bug, not
# a matching race. The driver, experiments/lammps/in.displace, is LAMMPS's examples/melt input
# (an fcc Lennard-Jones box of 4000 atoms) without its integrator and output, with one
# displace_atoms of every atom by up to 3 lattice units in each direction, which migrates atoms
# between all subdomains both ways through Irregular::migrate_atoms, and 10 steps.
#
# Steps, inside the experiment container, from the repository root, Open MPI build:
#   fetch    download LAMMPS at both commits, check the archives' sha256
#   build    each with no packages, `make mpi` with the Open MPI wrapper (-g -O2), -j$MAKE_JOBS
#   predict  RUNS traced runs of 3a2da51a per process count of NPS, each analysed at L1 (the
#            2-buffering finding, its bound and the call sites of its cycle), then REPLAYS
#            untraced runs each with the default settings and on the TCP transport with the
#            eager threshold just below and at the bound (experiments/lammps/predict.py)
#   stress   STRESS_RUNS untraced runs of 3a2da51a per process count, plain, with random send
#            and receive delays from the replay library, and with Open MPI's eager threshold
#            forced down to 0 bytes as stress testing on the corpora does, TIMEOUT seconds each
#            (experiments/history_stress.py)
#   fixed    predict and stress on 411c069b
#   all      fetch build predict stress fixed
# Writes experiments/results/lammps/{predict,stress}-{3a2da51a,411c069b}.csv.
# usage: experiments/lammps/run.sh [fetch|build|predict|stress|fixed|all]
#        NPS ("4 8"), RUNS (20), REPLAYS (10), STRESS_RUNS (40), TIMEOUT (30), JOBS (3),
#        MAKE_JOBS (8), LAMMPS_DIR (third_party/apps/lammps)
set -uo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$R"
D="${LAMMPS_DIR:-$R/third_party/apps/lammps}"
OUT="$R/experiments/results/lammps"; mkdir -p "$OUT"
NPS="${NPS:-4 8}"; RUNS="${RUNS:-20}"; REPLAYS="${REPLAYS:-10}"; STRESS_RUNS="${STRESS_RUNS:-40}"; TIMEOUT="${TIMEOUT:-30}"
source "$R/experiments/harness.sh"
export STRETTO_COMMIT="${STRETTO_COMMIT:-$(python3 "$R/experiments/provenance.py")}"
# the container refuses process_vm_readv, the single-copy mechanism of Open MPI's shared-memory
# transport, which then prints a line per refused transfer; without it the transport copies
# through its shared buffers (mpirun passes OMPI_MCA_* settings on to the ranks)
export OMPI_MCA_btl_vader_single_copy_mechanism=none
BUG=3a2da51a8210970a2382185f003fd512c12bada9
FIX=411c069ba66fb10b0e1121d0499b7e888cac9c80
declare -A SHA=([$BUG]=c5d122a858e7fc8339c13346c62c4e2b8b1defa341c6516aec6c275eac673b1f
                [$FIX]=1f238fd482da352de5f37891d8d6aaea33baf942d54c49243b0a32deacfb78ad)
INPUT=(-in "$R/experiments/lammps/in.displace" -log none)
OK='Total wall time'
exe() { echo "$D/lammps-$1/src/lmp_mpi"; }
full() { case "$1" in "${BUG:0:8}") echo "$BUG";; "${FIX:0:8}") echo "$FIX";; esac; }

step_fetch() {
    mkdir -p "$D" && cd "$D" || return 1
    local c
    for c in "$BUG" "$FIX"; do
        fetch_checked "lammps-$c.tar.gz" "https://github.com/lammps/lammps/archive/$c.tar.gz" "${SHA[$c]}" || return 1
        [ -d "lammps-$c" ] || tar xzf "lammps-$c.tar.gz" || return 1
    done
    cd "$R"
}
step_build() {
    local c
    for c in "$BUG" "$FIX"; do
        [ -x "$(exe "$c")" ] && continue
        (cd "$D/lammps-$c/src" && make -j"${MAKE_JOBS:-8}" mpi CC=/usr/bin/mpicxx.openmpi LINK=/usr/bin/mpicxx.openmpi \
             CCFLAGS="-g -O2 -std=c++11" > "$D/make-${c:0:8}.log" 2>&1) || { echo "build of LAMMPS ${c:0:8} failed ($D/make-${c:0:8}.log)" >&2; return 1; }
    done
}
predict() {   # predict <short commit>
    need_file "$(exe "$(full "$1")")" || return 1
    python3 experiments/lammps/predict.py --version "$1" --np $NPS --runs "$RUNS" --replays "$REPLAYS" --timeout "$TIMEOUT" \
        --out "$OUT/predict-$1.csv" -- "$(exe "$(full "$1")")" "${INPUT[@]}"
}
stress() {    # stress <short commit>
    need_file "$(exe "$(full "$1")")" || return 1
    rm -f "$OUT/stress-$1.csv"
    python3 experiments/history_stress.py --version "$1" --label in.displace --np $NPS --runs "$STRESS_RUNS" --timeout "$TIMEOUT" \
        --jobs "${JOBS:-3}" --settings plain delays eager --eager 0 --ok "$OK" --out "$OUT/stress-$1.csv" \
        -- "$(exe "$(full "$1")")" "${INPUT[@]}"
}
for step in "${@:-all}"; do
    case "$step" in
        fetch) step_fetch;; build) step_build;; predict) predict "${BUG:0:8}";; stress) stress "${BUG:0:8}";;
        fixed) predict "${FIX:0:8}" && stress "${FIX:0:8}";;
        all) step_fetch && step_build && predict "${BUG:0:8}" && stress "${BUG:0:8}" && predict "${FIX:0:8}" && stress "${FIX:0:8}";;
        *) echo "usage: $0 [fetch|build|predict|stress|fixed|all]" >&2; exit 2;;
    esac || exit 1
done
