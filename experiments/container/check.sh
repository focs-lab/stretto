#!/usr/bin/env bash
# Compare the packages of an experiment image with versions.txt, the package list of the image
# built from this Dockerfile on 21 Oct 2026, whose versions equal those of the image the results
# ran in. ca-certificates, which the build takes from the current archive, is not compared.
# Prints the differences and exits 1 if there are any.
# usage: experiments/container/check.sh [image]        (default stretto-exp:24.04)
set -euo pipefail
IMAGE="${1:-stretto-exp:24.04}"
D="$(cd "$(dirname "$0")" && pwd)"
got="$(mktemp)"; trap 'rm -f "$got"' EXIT
docker run --rm --entrypoint "" "$IMAGE" dpkg-query -W -f '${Package} ${Version}\n' | grep -v '^ca-certificates ' | sort > "$got"
docker run --rm --entrypoint "" "$IMAGE" sh -c \
  'mpirun.openmpi --version | head -1; mpichversion | head -1; gcc --version | head -1; gfortran --version | head -1; python3 --version'
if diff <(grep -v '^#' "$D/versions.txt") "$got"; then
    echo "$IMAGE: $(wc -l < "$got") packages, every one as in versions.txt"
else
    echo "$IMAGE differs from versions.txt (< expected, > found)" >&2
    exit 1
fi
