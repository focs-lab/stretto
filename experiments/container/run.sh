#!/usr/bin/env bash
# Run a command from the repository inside the experiment container, detached.
#   IMAGE    container image with Ubuntu 24.04, both MPI implementations and Python 3
#            (default rematch-exp:24.04, built from experiments/container/Dockerfile;
#            any image with the same packages works)
#   CPUS     processors the container may use (default 0-11,40-51)
#   MEMORY   memory limit (default 24g)
#   NAME     container name (default rematch-exp)
#   MACHINE  the host name the scripts print in their summaries (default: hostname)
# usage: experiments/container/run.sh <command ...>      (run from the repository root)
set -euo pipefail
IMAGE="${IMAGE:-rematch-exp:24.04}"
CPUS="${CPUS:-0-11,40-51}"
MEMORY="${MEMORY:-24g}"
NAME="${NAME:-rematch-exp}"
exec docker run -d --rm --name "$NAME" --hostname "${MACHINE:-$(hostname)}" \
  --ipc host --cpuset-cpus "$CPUS" --memory "$MEMORY" --memory-swap "$MEMORY" \
  --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD:/w" -w /w "$IMAGE" nice -n 10 "$@"
