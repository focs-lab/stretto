#!/usr/bin/env bash
# Build the MUST 1.11.2 image (experiments/must/Dockerfile) on a host with docker.
#   MUST_TARBALL  the release archive (default third_party/must/MUST-v1.11.2.tar.gz); downloaded
#                 from MUST's site when missing, and checked against its sha256 either way
#   IMAGE         image name (default stretto-must:1.11.2)
#   JOBS          make jobs inside the build (default 8)
#   CPUS, MEMORY  the build's cpuset and memory limit (default: none); they need the legacy
#                 builder, which this script selects
#   DOCKER_BUILD_ARGS  more docker build options (e.g. --cgroup-parent=docker.slice)
# usage: experiments/must/build.sh
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
TARBALL="${MUST_TARBALL:-$R/third_party/must/MUST-v1.11.2.tar.gz}"
SHA=934d41dcf379df65c68853646344736a85d58ecc93e8fc4fe9c4077b2eca9ccb
IMAGE="${IMAGE:-stretto-must:1.11.2}"
if [ ! -f "$TARBALL" ]; then
    mkdir -p "$(dirname "$TARBALL")"
    curl -fsSL -o "$TARBALL" https://hpc.rwth-aachen.de/must/files/MUST-v1.11.2.tar.gz
fi
echo "$SHA  $TARBALL" | sha256sum -c -
ctx="$(mktemp -d)"; trap 'rm -rf "$ctx"' EXIT
cp "$R/experiments/must/Dockerfile" "$TARBALL" "$ctx/"
read -r -a extra <<< "${DOCKER_BUILD_ARGS:-}"
DOCKER_BUILDKIT=0 docker build -t "$IMAGE" --build-arg JOBS="${JOBS:-8}" \
    ${CPUS:+--cpuset-cpus "$CPUS"} ${MEMORY:+--memory "$MEMORY" --memory-swap "$MEMORY"} \
    ${extra[@]+"${extra[@]}"} "$ctx"
docker run --rm "$IMAGE" bash -c 'ls /MBB-builds/MUST1112/bin/mustrun /MBB-builds/MUST1112/bin/must-tsan-cc && mpichversion | head -1 && clang --version | head -1'
