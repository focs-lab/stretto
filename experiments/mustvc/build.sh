#!/usr/bin/env bash
# Build the MUST-vc image (experiments/mustvc/Dockerfile) on a host with docker.
#   ARTIFACT  a clone of https://github.com/RWTH-HPC/must-omp-clock-checks (default
#             third_party/must-omp-clock-checks; cloned when missing), checked out at e1b187e;
#             its MUST tarball is checked against its sha256
#   MPI       mpich (default: the artifact's, Debian's MPICH 4.0.2) or openmpi (Debian's Open MPI
#             4.1.4 in its place: the Dockerfile with libopenmpi-dev for libmpich-dev, for
#             DLA-Future, which crashes under MPICH 4.0.2; README)
#   IMAGE     image name (default mustvc:1.9-omp, with MPI=openmpi mustvc:1.9-omp-openmpi)
#   JOBS      make jobs inside the build (default 8)
#   CPUS, MEMORY  the build's cpuset and memory limit (default: none; legacy builder)
#   DOCKER_BUILD_ARGS  more docker build options (e.g. --cgroup-parent=docker.slice)
# usage: experiments/mustvc/build.sh
set -euo pipefail
R="$(cd "$(dirname "$0")/../.." && pwd)"
A="${ARTIFACT:-$R/third_party/must-omp-clock-checks}"
COMMIT=e1b187e36be4fa48eff37cd73a0503773581777f
SHA=37328e624e47fdfdd5c6f7d35ad1798895ab08b76c5a28d793d219760746c1c2
MPI=${MPI:-mpich}
case $MPI in mpich) IMAGE="${IMAGE:-mustvc:1.9-omp}";; openmpi) IMAGE="${IMAGE:-mustvc:1.9-omp-openmpi}";;
  *) echo "unknown MPI $MPI" >&2; exit 2;; esac
[ -d "$A/.git" ] || git clone -q https://github.com/RWTH-HPC/must-omp-clock-checks.git "$A"
git -C "$A" checkout -q "$COMMIT"
echo "$SHA  $A/MUST-v1.9-omp-checks.tar.gz" | sha256sum -c -
ctx="$(mktemp -d)"; trap 'rm -rf "$ctx"' EXIT
cp -r "$R/experiments/mustvc/Dockerfile" "$A/MUST-v1.9-omp-checks.tar.gz" "$A/requirements.txt" "$A/tests" "$ctx/"
[ "$MPI" = openmpi ] && sed -i 's/ libmpich-dev / libopenmpi-dev /' "$ctx/Dockerfile"
read -r -a extra <<< "${DOCKER_BUILD_ARGS:-}"
DOCKER_BUILDKIT=0 docker build -t "$IMAGE" --build-arg JOBS="${JOBS:-8}" \
    ${CPUS:+--cpuset-cpus "$CPUS"} ${MEMORY:+--memory "$MEMORY" --memory-swap "$MEMORY"} \
    ${extra[@]+"${extra[@]}"} "$ctx"
docker run --rm "$IMAGE" bash -c 'ls /home/user/must/bin/mustrun && { mpichversion 2>/dev/null || ompi_info --version; } | head -1 && clang --version | head -1 && lit --version'
