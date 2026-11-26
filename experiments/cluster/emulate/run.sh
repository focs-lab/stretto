#!/usr/bin/env bash
# A two-node Slurm cluster on one machine: two containers of stretto-slurm:24.04 (the experiment
# image with Slurm 23.11) on a Docker network of their own. Each has its own host name, network
# stack, IPC namespace, /tmp and node-local /scratch, so MPI between them goes over TCP as between
# two nodes; they share only the repository, mounted at /w in both (the cluster's shared home).
# No ssh: jobs go in with sbatch, ranks start with srun, as on the cluster. slurmd needs a cgroup
# tree it can write (node.sh): each container gets its own cgroup namespace, and the capability
# and AppArmor profile that let node.sh remount it writable; the host's tree is not visible.
#   CPUS1, CPUS2   the nodes' cpusets (default 28-35 and 36-43), 8 CPUs each
#   MEMORY         each node's memory limit (default 8g)
#   NET            the Docker network (default stretto-2node)
#   STATE          the munge key and the jobs' output (default build/cluster-emulate, under the repository)
# usage: experiments/cluster/emulate/run.sh up | test | shell | down | all
#   test   experiments/cluster/emulate/test.sh on node1 (it submits the jobs and checks them)
set -euo pipefail
R="$(cd "$(dirname "$0")/../../.." && pwd)"
IMAGE="${IMAGE:-stretto-slurm:24.04}"
NET="${NET:-stretto-2node}"
STATE="${STATE:-$R/build/cluster-emulate}"
CPUS1="${CPUS1:-28-35}"; CPUS2="${CPUS2:-36-43}"; MEMORY="${MEMORY:-8g}"
as_user=(docker exec -u "$(id -u)" -w /w -e HOME=/home/mpi -e "STRETTO_COMMIT=${STRETTO_COMMIT:-}"
         ${RUNS:+-e RUNS} ${REPLAYS:+-e REPLAYS} ${BIMPL_MPIS:+-e BIMPL_MPIS} node1)

up() {
    docker image inspect "$IMAGE" > /dev/null 2>&1 \
        || DOCKER_BUILDKIT=0 docker build --cpuset-cpus "$CPUS1" -t "$IMAGE" "$R/experiments/cluster/emulate"
    mkdir -p "$STATE/keys"
    [ -f "$STATE/keys/munge.key" ] || head -c 1024 /dev/urandom > "$STATE/keys/munge.key"
    docker network inspect "$NET" > /dev/null 2>&1 || docker network create "$NET" > /dev/null
    local n cpus
    for n in 1 2; do
        cpus="CPUS$n"
        docker rm -f "node$n" > /dev/null 2>&1 || true
        docker run -d --name "node$n" --hostname "node$n" --network "$NET" \
            --cpuset-cpus "${!cpus}" --memory "$MEMORY" --memory-swap "$MEMORY" --ulimit core=0 --shm-size 1g \
            --tmpfs /scratch:rw,exec,size=2g,mode=1777 \
            --cgroupns private --cap-add SYS_ADMIN --security-opt apparmor=unconfined \
            -e NODE_UID="$(id -u)" -e NODE_GID="$(id -g)" -e NODES="node1 node2" -e CPUS=8 \
            -v "$STATE/keys:/keys:ro" -v "$R:/w" "$IMAGE" > /dev/null
    done
    for _ in $(seq 1 60); do
        [ "$(docker exec node1 sinfo -h -o '%T %D' 2> /dev/null)" = "idle 2" ] && break
        sleep 1
    done
    docker exec node1 sinfo
}

down() {
    docker rm -f node1 node2 > /dev/null 2>&1 || true
    docker network rm "$NET" > /dev/null 2>&1 || true
}

case "${1:-all}" in
    up) up;;
    down) down;;
    shell) exec docker exec -it -u "$(id -u)" -w /w -e HOME=/home/mpi node1 bash;;
    test) shift; "${as_user[@]}" experiments/cluster/emulate/test.sh "$@";;
    all) up; rc=0; "${as_user[@]}" experiments/cluster/emulate/test.sh || rc=$?
         down; exit $rc;;
    *) echo "usage: $0 up | test [steps...] | shell | down | all" >&2; exit 2;;
esac
