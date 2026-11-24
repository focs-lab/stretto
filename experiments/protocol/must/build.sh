#!/usr/bin/env bash
# Build stretto-must-u:24.04: MUST 1.11.2 in the experiment image against both of its MPI
# libraries (experiments/protocol/must/Dockerfile), on a host with docker and stretto-exp:24.04.
#   MUST_TARBALL  the release archive (default third_party/must/MUST-v1.11.2.tar.gz); downloaded
#                 from MUST's site when missing, and checked against its sha256 either way
#   IMAGE         image name (default stretto-must-u:24.04)
#   JOBS          make jobs inside the build (default 8)
#   CPUS, MEMORY  the build's cpuset and memory limit (default: none; they need the legacy
#                 builder, which this script selects)
#   DOCKER_BUILD_ARGS  more docker build options
# usage: experiments/protocol/must/build.sh
set -euo pipefail
R="$(cd "$(dirname "$0")/../../.." && pwd)"
TARBALL="${MUST_TARBALL:-$R/third_party/must/MUST-v1.11.2.tar.gz}"
SHA=934d41dcf379df65c68853646344736a85d58ecc93e8fc4fe9c4077b2eca9ccb
IMAGE="${IMAGE:-stretto-must-u:24.04}"
if [ ! -f "$TARBALL" ]; then
    mkdir -p "$(dirname "$TARBALL")"
    curl -fsSL -o "$TARBALL" https://hpc.rwth-aachen.de/must/files/MUST-v1.11.2.tar.gz
fi
echo "$SHA  $TARBALL" | sha256sum -c -
ctx="$(mktemp -d)"; trap 'rm -rf "$ctx"' EXIT
cp "$R/experiments/protocol/must/Dockerfile" "$R/experiments/protocol/must/configure-must.sh" "$TARBALL" "$ctx/"
read -r -a extra <<< "${DOCKER_BUILD_ARGS:-}"
DOCKER_BUILDKIT=0 docker build -t "$IMAGE" --build-arg JOBS="${JOBS:-8}" \
    ${CPUS:+--cpuset-cpus "$CPUS"} ${MEMORY:+--memory "$MEMORY" --memory-swap "$MEMORY"} \
    ${extra[@]+"${extra[@]}"} "$ctx"
docker run --rm --ulimit core=0 "$IMAGE" bash -c \
    'ls /opt/must/mpich/bin/mustrun /opt/must/openmpi/bin/mustrun; mpichversion | head -1; mpirun.openmpi --version | head -1; clang-18 --version | head -1'
