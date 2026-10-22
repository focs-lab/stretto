#!/usr/bin/env bash
# Run a command inside the MPI-PRV artifact image (Docker Hub mpiprv/mpiprv, pinned below by
# digest), with the repository mounted at /w, in the foreground. The image keeps its tool
# paths in /root/.bashrc, which the command sources first, and expects to run as root; the
# files the command creates under /w are handed back to the calling user at the end.
#   CPUS     processors the container may use (default: every processor; the paper's runs used four)
#   MEMORY   memory limit (default 16g)
#   NAME     container name (default stretto-prv)
#   PRV_IMAGE  the image (default: the pinned digest)
#   DOCKER_ARGS  more docker run options (default "--cap-add SYS_PTRACE": the artifact is meant
#            to run --privileged, and without the capability the default seccomp profile refuses
#            process_vm_readv/writev, so Open MPI's one-sided calls fail and its RQ1 program for
#            property 6 hangs)
# usage: experiments/mpiprv/prv.sh <shell command>      (run from the repository root)
set -euo pipefail
PRV_IMAGE="${PRV_IMAGE:-mpiprv/mpiprv@sha256:af980da29375ffca673d1e5a16aa8c1d06a9d69211e79b6f948a137ba95c43cf}"
CPUS="${CPUS:-0-$(($(nproc) - 1))}"
MEMORY="${MEMORY:-16g}"
NAME="${NAME:-stretto-prv}"
read -r -a extra <<< "${DOCKER_ARGS---cap-add SYS_PTRACE}"
exec docker run ${extra[@]+"${extra[@]}"} --rm --name "$NAME" --hostname "${MACHINE:-$(hostname)}" --ipc host \
  --cpuset-cpus "$CPUS" --memory "$MEMORY" --memory-swap "$MEMORY" \
  -e STRETTO_COMMIT -e REPS -e TWIN_REPS -e JOBS -e TIMEOUT -e MAXNP -e DELAY -v "$PWD:/w" -w /w "$PRV_IMAGE" \
  bash -c "source /root/.bashrc; export OMPI_MCA_rmaps_base_oversubscribe=1; rc=0; ( $* ) || rc=\$?; chown -R $(id -u):$(id -g) /w/experiments/results/mpiprv /w/experiments/mpiprv/work 2>/dev/null; exit \$rc"
