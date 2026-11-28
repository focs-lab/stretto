#!/usr/bin/env bash
# Which launches start a two-node MPI program on the cluster, natively: the system's Open MPI
# 4.1.6 and the image's MPICH 4.2.0 (experiments/cluster/mpich.sh, MPICH_PREFIX), each under
# srun --mpi=pmix and pmi2, Open MPI's mpirun inside the allocation (its daemons start with
# srun), and, for MPICH, hydra with Slurm's launcher. Every rank prints its node; a launch works
# when 4 ranks on 2 nodes reduce to the right sum. NET: the subnet of Open MPI's TCP traffic.
# Submit from the repository root:
#   sbatch -p normal -C xcni experiments/cluster/launch-matrix.sbatch
# Output: build/cluster/launch-matrix-<job>.out (the table) and .log (every launch's output).
#SBATCH --job-name=stretto-launch
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=4
#SBATCH --cpus-per-task=1
#SBATCH --mem-per-cpu=1G
#SBATCH --time=00:25:00
#SBATCH --output=build/cluster/launch-matrix-%j.out
set -uo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
MPICH_PREFIX="${MPICH_PREFIX:-$HOME/opt/mpich-4.2.0-ubuntu}"
D="build/cluster/launch-matrix-$SLURM_JOB_ID"; mkdir -p "$D"; LOG="$D.log"
cat > "$D/hello.c" <<'EOF'
#include <mpi.h>
#include <stdio.h>
int main(int c, char **v) {
    int r, n, x, len;
    char host[MPI_MAX_PROCESSOR_NAME];
    MPI_Init(&c, &v);
    MPI_Comm_rank(MPI_COMM_WORLD, &r); MPI_Comm_size(MPI_COMM_WORLD, &n);
    MPI_Get_processor_name(host, &len);
    x = r; MPI_Allreduce(MPI_IN_PLACE, &x, 1, MPI_INT, MPI_SUM, MPI_COMM_WORLD);
    printf("rank %d of %d on %s, sum %d\n", r, n, host, x);
    MPI_Finalize();
    return 0;
}
EOF
/usr/bin/mpicc.openmpi -o "$D/hello-openmpi" "$D/hello.c" || exit 1
"$MPICH_PREFIX/bin/mpicc.mpich" -o "$D/hello-mpich" "$D/hello.c" || exit 1
echo "job $SLURM_JOB_ID on $SLURM_JOB_NODELIST; $(srun --version); Open MPI $(/usr/bin/ompi_info --version 2>/dev/null | head -1); $("$MPICH_PREFIX/bin/mpichversion" | head -1)"
echo "library | launch | result"
try() {   # try <library> <label> <command...>
    local m="$1" label="$2"; shift 2
    local out rc
    out="$(timeout -k 5 60 "$@" "$D/hello-$m" 2>&1)"; rc=$?
    printf '== %s | %s | rc %d\n%s\n' "$m" "$label" "$rc" "$out" >> "$LOG"
    local ok nodes
    ok=$(grep -c "of 4 on .*, sum 6" <<< "$out")
    nodes=$(grep -o "of 4 on [^,]*" <<< "$out" | sort -u | wc -l)
    if [ "$rc" -eq 0 ] && [ "$ok" -eq 4 ] && [ "$nodes" -eq 2 ]; then
        echo "$m | $label | works (4 ranks, 2 nodes)"
    else
        echo "$m | $label | fails: rc $rc, $ok of 4 ranks right, on $nodes nodes; $(grep -v '^rank ' <<< "$out" | grep -v '^\s*$' | head -2 2> /dev/null | tr '\n' ' ' | cut -c1-200)"
    fi
}
NET="${NET:-192.168.48.0/21}"   # one of the nodes' two subnets (docs/cluster.md)
IFACE="$(ip -o -4 addr show | awk -v n="${NET%.*/*}." 'index($4, n) == 1 {print $2; exit}')"
echo "NET $NET, interface $IFACE on $(hostname)"
TCP=(--mca oob_tcp_if_include "$NET" --mca btl_tcp_if_include "$NET")
UCX=(-x UCX_NET_DEVICES="$IFACE")
PMIXDIR="$PWD/$D/pmix"; mkdir -p "$PMIXDIR"   # on the shared home: the same path in and out of the job's /tmp
S=(srun --mpi=pmix -N 2 -n 4 --distribution=cyclic)
for m in openmpi mpich; do
    try $m "srun --mpi=pmix" "${S[@]}"
    # PMIx 5's shared-memory store fails to attach under Slurm's server: the hash store instead
    try $m "srun --mpi=pmix, PMIX_MCA_gds=hash" env PMIX_MCA_gds=hash OMPI_MCA_orte_tmpdir_base=/tmp "${S[@]}"
    # the job's private /tmp (job_container/tmpfs) hides the server's files: its client directory on the home
    try $m "srun --mpi=pmix, SLURM_PMIX_TMPDIR on the home" env SLURM_PMIX_TMPDIR="$PMIXDIR" "${S[@]}"
    try $m "srun --mpi=pmix, gds=hash, SLURM_PMIX_TMPDIR, no direct connections" env PMIX_MCA_gds=hash \
        SLURM_PMIX_TMPDIR="$PMIXDIR" SLURM_PMIX_DIRECT_CONN=false OMPI_MCA_orte_tmpdir_base=/tmp "${S[@]}"
    try $m "srun --mpi=pmi2" srun --mpi=pmi2 -N 2 -n 4 --distribution=cyclic
    try $m "Open MPI mpirun" /usr/bin/mpirun -np 4 --map-by node
    try $m "Open MPI mpirun, TCP on $NET" /usr/bin/mpirun -np 4 --map-by node "${TCP[@]}"
done
try mpich "Open MPI mpirun, one node" /usr/bin/mpirun -np 4 --map-by slot --host "$(hostname):4"
try mpich "Open MPI mpirun, TCP on $NET, UCX on $IFACE" /usr/bin/mpirun -np 4 --map-by node "${TCP[@]}" "${UCX[@]}"
try mpich "Open MPI mpirun, TCP on $NET, UCX tcp on $IFACE" /usr/bin/mpirun -np 4 --map-by node "${TCP[@]}" "${UCX[@]}" \
    -x UCX_TLS=tcp,self,sm
# the package's MPICH has a PMIx client only, so hydra's PMI server leaves every rank a singleton
try mpich "hydra -launcher slurm" "$MPICH_PREFIX/bin/mpiexec.hydra" -launcher slurm -n 4 -ppn 2
